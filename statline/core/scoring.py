# statline/core/scoring.py
from __future__ import annotations

import math
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .normalization import clamp01
from .weights import normalize_weights

# ──────────────────────────────────────────────────────────────────────────────
# Dataclasses
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ScoreResult:
    """Single-row result (0..99 score, 0..1 components, unit-L1 weights)."""
    score: float
    components: Dict[str, float]
    weights: Dict[str, float]


# ──────────────────────────────────────────────────────────────────────────────
# Small utilities
# ──────────────────────────────────────────────────────────────────────────────

def _to_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def _safe_norm(value: float, cap: float) -> float:
    cap = float(cap)
    v = float(value)
    if cap <= 1e-12:
        return 0.0
    return clamp01(v / cap)


# ──────────────────────────────────────────────────────────────────────────────
# Spec-driven helpers (adapter/batch context)
# ──────────────────────────────────────────────────────────────────────────────

def caps_from_context(
    metrics_keys: List[str],
    context: Dict[str, Dict[str, float]],
    *,
    invert: Optional[Dict[str, bool]] = None,
) -> Dict[str, float]:
    """
    Build caps map from an external/batch context:
      - positive metrics: cap = leader
      - inverted metrics: cap = |floor - leader|
    Missing → 1.0 (benign).
    """
    caps: Dict[str, float] = {}
    inv = invert or {}
    for k in metrics_keys:
        info = context.get(k)
        if not info:
            caps[k] = 1.0
            continue
        leader = _to_float(info.get("leader", 1.0), 1.0)
        floor = _to_float(info.get("floor", 0.0), 0.0)
        if inv.get(k, False):
            caps[k] = max(1e-6, abs(floor - leader))
        else:
            caps[k] = max(1e-6, leader)
    return caps


def per_metric_weights_from_buckets(
    metric_to_bucket: Dict[str, str],
    bucket_weights: Dict[str, float],
) -> Dict[str, float]:
    """Spread each bucket's weight equally across its metrics."""
    counts: Dict[str, int] = {}
    for _, b in metric_to_bucket.items():
        counts[b] = counts.get(b, 0) + 1
    per_metric: Dict[str, float] = {}
    for m, b in metric_to_bucket.items():
        bw = float(bucket_weights.get(b, 0.0))
        n = max(1, counts.get(b, 1))
        per_metric[m] = bw / n
    return per_metric


def _resolve_expr(expr: Any, row: Mapping[str, Any]) -> float:
    """Minimal, safe resolver for efficiency strings."""
    try:
        s = str(expr or "").strip()
    except Exception:
        return 0.0
    if not s:
        return 0.0

    # $.metric → row['metric']
    if s.startswith("$."):
        return _to_float(row.get(s[2:], 0.0), 0.0)

    # raw["field"] / raw['field'] → row['field']
    if (s.startswith('raw["') and s.endswith('"]')) or (s.startswith("raw['") and s.endswith("']")):
        return _to_float(row.get(s[5:-2], 0.0), 0.0)

    # bare identifier (letters/digits/underscore)
    ident = s.replace("_", "")
    if ident.isalnum():
        return _to_float(row.get(s, 0.0), 0.0)

    # numeric literal fallback
    try:
        return float(s)
    except Exception:
        return 0.0


def _apply_transform_value(x: float, spec: Optional[Mapping[str, Any]]) -> float:
    if not spec:
        return x
    name = str(spec.get("name", "")).lower()
    p = dict(spec.get("params") or {})

    def _num(v: Any, d: float = 0.0) -> float:
        try:
            return float(v)
        except Exception:
            return d

    if name == "linear":
        return x * _num(p.get("scale", 1.0), 1.0) + _num(p.get("offset", 0.0), 0.0)
    if name == "capped_linear":
        cap = _num(p.get("cap", x))
        return x if x <= cap else cap
    if name == "minmax":
        lo = _num(p.get("lo", x))
        hi = _num(p.get("hi", x))
        return min(max(x, lo), hi)
    if name == "pct01":
        by = _num(p.get("by", 100.0), 100.0) or 100.0
        return x / by
    if name == "softcap":
        cap = _num(p.get("cap", x))
        slope = _num(p.get("slope", 1.0), 1.0)
        return x if x <= cap else cap + (x - cap) * slope
    if name == "log1p":
        return math.log1p(max(x, 0.0)) * _num(p.get("scale", 1.0), 1.0)
    return x


def _clamp_value(x: float, clamp: Optional[Tuple[float, float]]) -> float:
    if not clamp:
        return x
    lo, hi = float(clamp[0]), float(clamp[1])
    return min(max(x, lo), hi)


def _batch_context_from_rows(
    rows: List[Dict[str, Any]],
    metric_keys: List[str],
    invert: Dict[str, bool],
) -> Dict[str, Dict[str, float]]:
    """Fallback when no external context is provided: derive leader/floor from the batch."""
    vals: Dict[str, List[float]] = {k: [] for k in metric_keys}
    for r in rows:
        for k in metric_keys:
            v = r.get(k)
            if v is None:
                continue
            try:
                vals[k].append(float(v))
            except Exception:
                pass

    ctx: Dict[str, Dict[str, float]] = {}
    for k in metric_keys:
        xs = vals[k]
        if not xs:
            # benign defaults
            if invert.get(k, False):
                ctx[k] = {"leader": 0.0, "floor": 1.0}
            else:
                ctx[k] = {"leader": 1.0, "floor": 0.0}
            continue

        lo = min(xs)
        hi = max(xs)
        if invert.get(k, False):
            ctx[k] = {"leader": lo, "floor": hi}  # lower is better
        else:
            ctx[k] = {"leader": hi, "floor": lo}  # higher is better
    return ctx


def _caps_from_clamps(
    adapter: Any,
    invert_map: Dict[str, bool],
) -> Dict[str, float]:
    """
    Build per-metric caps from adapter metric clamp ranges.
    - Non-inverted: cap = upper bound (or 1.0 if missing)
    - Inverted:     cap = max(upper - lower, 1e-6) if clamp given, else 1.0
    """
    caps: Dict[str, float] = {}
    for m in getattr(adapter, "metrics", []):
        lower = upper = None
        clamp = getattr(m, "clamp", None)
        if clamp:
            try:
                lower = _to_float(clamp[0]) if clamp else None
                upper = _to_float(clamp[1]) if clamp else None
            except Exception:
                lower = upper = None

        if invert_map.get(m.key, False):
            caps[m.key] = max(1e-6, (upper - lower)) if (upper is not None and lower is not None) else 1.0
        else:
            caps[m.key] = float(upper) if (upper is not None) else 1.0

    # safety: never zero
    for k, v in list(caps.items()):
        caps[k] = max(1e-6, float(v))
    return caps


# ──────────────────────────────────────────────────────────────────────────────
# PRI kernel (single-row)
# ──────────────────────────────────────────────────────────────────────────────

def _pri_kernel_single(
    metrics: Mapping[str, float],
    caps: Mapping[str, float],
    weights: Mapping[str, float],
) -> ScoreResult:
    """
    Compute weighted, normalized score for one row.

    Mechanics:
      - normalize each metric by its cap (0..1)
      - apply signed, L1-normalized weights
      - sum (negative totals floor at 0)
      - map to 0..99
    """
    unit_w = normalize_weights(weights)  # preserve sign, L1-normalize
    if not unit_w:
        return ScoreResult(score=0.0, components={}, weights={})

    comps: Dict[str, float] = {}
    total = 0.0
    mget = metrics.get
    cget = caps.get
    for k, w in unit_w.items():
        norm = _safe_norm(_to_float(mget(k, 0.0)), _to_float(cget(k, 0.0), 1.0))
        comps[k] = norm
        total += norm * w

    base01 = clamp01(max(0.0, total))  # never below 0
    score = 99.0 * base01
    return ScoreResult(score=score, components=comps, weights=dict(unit_w))


# ──────────────────────────────────────────────────────────────────────────────
# Dynamic PRI (adapter-agnostic) — batch API
# ──────────────────────────────────────────────────────────────────────────────

def calculate_pri(
    mapped_rows: List[Dict[str, Any]],
    adapter: Any,
    *,
    weights_override: Optional[Dict[str, float]] = None,
    context: Optional[Dict[str, Dict[str, float]]] = None,
    caps_override: Optional[Dict[str, float]] = None,   # direct caps map {metric: cap}
    _timing: Optional[Any] = None,                      # StageTimes or None
) -> List[Dict[str, Any]]:
    """
    Fully adapter-constrained PRI (0–99).

    Caps precedence:
      1) caps_override (highest)
      2) adapter clamps (when single-row and no context)
      3) batch/external context
    """
    T = _timing

    # 1) Collect spec info from adapter
    with (T.stage("spec") if T else nullcontext()):
        metrics_spec = getattr(adapter, "metrics", [])
        metric_keys = [m.key for m in metrics_spec]
        metric_to_bucket: Dict[str, str] = {m.key: m.bucket for m in metrics_spec}
        invert_map: Dict[str, bool] = {m.key: bool(m.invert) for m in metrics_spec}

    # 2) Inject efficiency channels as derived metrics
    with (T.stage("inject_eff") if T else nullcontext()):
        eff_list = list(getattr(adapter, "efficiency", []) or [])
        extended_rows: List[Dict[str, Any]] = []

        if not eff_list:
            extended_rows = [dict(r) for r in mapped_rows]
        else:
            for raw in mapped_rows:
                r: Dict[str, Any] = dict(raw)
                for eff in eff_list:
                    # register metadata
                    if eff.key not in metric_to_bucket:
                        metric_to_bucket[eff.key] = eff.bucket
                    invert_map[eff.key] = bool(getattr(eff, "invert", False))
                    if eff.key not in metric_keys:
                        metric_keys.append(eff.key)

                    # only compute if adapter didn't already
                    if eff.key not in r:
                        make = max(0.0, _resolve_expr(getattr(eff, "make", ""), r))
                        att = max(
                            float(getattr(eff, "min_den", 1.0)),
                            _resolve_expr(getattr(eff, "attempt", ""), r),
                        )
                        val = (make / att) if att > 0 else 0.0
                        val = _apply_transform_value(val, getattr(eff, "transform", None))
                        val = _clamp_value(val, getattr(eff, "clamp", None))
                        r[eff.key] = clamp01(val)

                extended_rows.append(r)

    # 3) Resolve context (leaders/floors) & build caps
    with (T.stage("caps") if T else nullcontext()):
        if caps_override:
            caps = {str(k): max(1e-6, float(v)) for k, v in caps_override.items()}
            context_used = "caps_override"
        elif context is None and len(extended_rows) == 1:
            caps = _caps_from_clamps(adapter, invert_map)
            for eff in getattr(adapter, "efficiency", []) or []:
                caps.setdefault(eff.key, 1.0)
            context_used = "clamps"
        else:
            ctx = context or _batch_context_from_rows(extended_rows, metric_keys, invert_map)
            caps = caps_from_context(metric_keys, ctx, invert=invert_map)
            context_used = "batch" if context is None else "external"

    # 4) Bucket weights → per-metric weights; flip sign for inverted metrics
    with (T.stage("weights") if T else nullcontext()):
        bucket_weights = dict(
            weights_override or getattr(adapter, "weights", {}).get("pri", {}) or {}
        )
        per_metric_weights = per_metric_weights_from_buckets(metric_to_bucket, bucket_weights)
        for k, inv in invert_map.items():
            if inv and k in per_metric_weights:
                per_metric_weights[k] = -abs(per_metric_weights[k])
        scored_metrics = {k for k, w in per_metric_weights.items() if abs(w) > 1e-12}

    # 5) Score each row, keep pri_raw (pre-batch 0..1), then renormalize pri to [55..99]
    with (T.stage("score_rows") if T else nullcontext()):
        tmp: List[Tuple[int, Dict[str, Any], float]] = []
        buckets_def = getattr(adapter, "buckets", {}) or {}
        bucket_keys = list(buckets_def.keys())

        # First pass: compute raw components + pri_raw in [0..1]
        for idx, r in enumerate(extended_rows):
            res = _pri_kernel_single(
                metrics=r,
                caps=caps,
                weights=per_metric_weights,
            )

            # Per-bucket aggregation over scored metrics only
            bucket_scores: Dict[str, float] = {b: 0.0 for b in bucket_keys}
            bucket_counts: Dict[str, int] = {b: 0 for b in bucket_keys}
            for k, v in res.components.items():
                if k not in scored_metrics:
                    continue
                b = metric_to_bucket.get(k)
                if b is None:
                    continue
                bucket_scores[b] += v
                bucket_counts[b] += 1
            for b in list(bucket_scores.keys()):
                c = bucket_counts[b]
                if c:
                    bucket_scores[b] /= c
                else:
                    bucket_scores.pop(b, None)

            raw01 = clamp01(res.score / 99.0)  # ← pre-batch normalization (keep as pri_raw)
            payload = {
                "buckets": bucket_scores,
                "components": {k: v for k, v in res.components.items() if k in scored_metrics},
                "weights": res.weights,
                "context_used": context_used,
                "pri_raw": raw01,
                "_i": idx,  # original input order for potential consumers
            }
            tmp.append((idx, payload, raw01))

        # Second pass: affine-map raw01 across the batch → [55..99]
        LO, HI = 55.0, 99.0
        out_list: List[Dict[str, Any]]

        if len(tmp) == 1:
            # Single-row deterministic mapping within the band
            _, payload, _ = tmp[0]
            payload = dict(payload)
            raw01_single: float = clamp01(_to_float(payload.get("pri_raw", 0.0), 0.0))
            pri_f = LO + raw01_single * (HI - LO)
            payload["pri"] = int(round(pri_f))
            payload.pop("_i", None)
            out_list = [payload]
        else:
            raw_vals: List[float] = [raw for _, _, raw in tmp]
            min_raw: float = min(raw_vals)
            max_raw: float = max(raw_vals)
            span: float = max(1e-6, max_raw - min_raw)

            # Keep ORIGINAL INPUT ORDER by filling via index map.
            by_idx: Dict[int, Dict[str, Any]] = {}
            for idx, payload, raw01 in tmp:
                pri_f = LO + (raw01 - min_raw) * (HI - LO) / span
                pri = int(round(max(LO, min(HI, pri_f))))
                item = dict(payload)
                item["pri"] = pri
                item.pop("_i", None)
                by_idx[idx] = item

            out_list = [by_idx[i] for i in range(len(tmp))]

    return out_list


# ──────────────────────────────────────────────────────────────────────────────
# Single-row convenience
# ──────────────────────────────────────────────────────────────────────────────

def calculate_pri_single(
    mapped_row: Mapping[str, Any],
    adapter: Any,
    *,
    weights_override: Optional[Dict[str, float]] = None,
    context: Optional[Dict[str, Dict[str, float]]] = None,
    caps_override: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """
    Convenience wrapper; identical logic to calculate_pri but for one row.
    """
    rows = calculate_pri(
        [dict(mapped_row)],
        adapter,
        weights_override=weights_override,
        context=context,
        caps_override=caps_override,
    )
    return rows[0]


__all__ = [
    "ScoreResult",
    "calculate_pri",
    "calculate_pri_single",
]
