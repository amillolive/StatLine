# statline/core/scoring.py
from __future__ import annotations

import math
import re
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from .normalization import clamp01
from .weights import normalize_weights

# ──────────────────────────────────────────────────────────────────────────────
# Dataclasses
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ScoreResult:
    """Single-row kernel result (0..99 score, 0..1 components, unit-L1 weights)."""
    score: float
    components: Dict[str, float]
    weights: Dict[str, float]


@dataclass(frozen=True)
class PRIResult:
    """
    README-facing result object.

    - `.pri` is the normalized integer score in [55..99]
    - `.details` is the per-bucket/metric breakdown (and optional extras)
    """
    pri: int
    details: Dict[str, Any]


# ──────────────────────────────────────────────────────────────────────────────
# Small utilities
# ──────────────────────────────────────────────────────────────────────────────

def _to_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def _ctx_get(ctx: Mapping[str, Mapping[str, float]], k: str) -> Tuple[float, float]:
    info = ctx.get(k) or {}
    leader = _to_float(info.get("leader", 1.0), 1.0)
    floor = _to_float(info.get("floor", 0.0), 0.0)
    return leader, floor


def _norm01_from_ctx(v: Any, leader: float, floor: float, invert: bool) -> float:
    x = _to_float(v, 0.0)
    a = float(leader)
    b = float(floor)

    if invert:
        # best is leader (low), worst is floor (high)
        denom = max(1e-12, b - a)
        t = (b - x) / denom
    else:
        # best is leader (high), worst is floor (low)
        denom = max(1e-12, a - b)
        t = (x - b) / denom

    return clamp01(t)


def _context_from_clamps(adapter: Any, invert_map: Dict[str, bool]) -> Dict[str, Dict[str, float]]:
    """
    Build leader/floor context from adapter clamp ranges.
    - Non-invert: leader=hi, floor=lo
    - Invert:     leader=lo, floor=hi
    Includes BOTH primary metrics and efficiency specs (derived channels).
    """
    out: Dict[str, Dict[str, float]] = {}

    def _put(key: str, clamp: Any, inv: bool) -> None:
        if not clamp:
            out[key] = {"leader": 1.0, "floor": 0.0}
            return
        lo = _to_float(clamp[0], 0.0)
        hi = _to_float(clamp[1], 1.0)
        if inv:
            out[key] = {"leader": lo, "floor": hi}
        else:
            out[key] = {"leader": hi, "floor": lo}

    # metric clamps
    for m in getattr(adapter, "metrics", []) or []:
        _put(m.key, getattr(m, "clamp", None), invert_map.get(m.key, False))

    # efficiency clamps (critical for clamps-mode / single-row)
    for e in getattr(adapter, "efficiency", []) or []:
        _put(e.key, getattr(e, "clamp", None), invert_map.get(e.key, False))

    return out


def _safe_norm(value: float, cap: float) -> float: # pyright: ignore[reportUnusedFunction]
    cap = float(cap)
    v = float(value)
    if cap <= 1e-12:
        return 0.0
    return clamp01(v / cap)


def _slug_profile_key(name: str) -> str:
    # "PRI-AF" -> "pri_af", "PRI" -> "pri"
    return str(name).strip().lower().replace("-", "_").replace(" ", "_")


def _affine01(x01: float, lo: float, hi: float) -> float:
    x = clamp01(x01)
    return float(lo) + x * (float(hi) - float(lo))


def _score_from_profile(
    prof: Mapping[str, Any],
    *,
    raw01: float,
    pct01: float,
) -> float:
    kind = str(prof.get("kind", "affine")).strip().lower()

    if kind == "affine":
        lo = _to_float(prof.get("lo", 55), 55.0)
        hi = _to_float(prof.get("hi", 99), 99.0)
        return _affine01(raw01, lo, hi)

    if kind == "window":
        out_lo = _to_float(prof.get("out_lo", -50), -50.0)
        out_hi = _to_float(prof.get("out_hi", 50), 50.0)
        pct_lo = _to_float(prof.get("pct_lo", 0.25), 0.25)
        pct_hi = _to_float(prof.get("pct_hi", 0.75), 0.75)

        span = max(1e-12, float(pct_hi) - float(pct_lo))
        t = (float(pct01) - float(pct_lo)) / span
        t = clamp01(t)
        return float(out_lo) + t * (float(out_hi) - float(out_lo))

    # Unknown kind -> treat as PRI default
    return _affine01(raw01, 55.0, 99.0)


def _midrank_percentiles(values: List[float]) -> List[float]:
    """
    Midrank percentile in [0..100], stable with ties.
    For n==1 returns 50.0.
    """
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [50.0]

    pairs = sorted((v, i) for i, v in enumerate(values))
    out = [0.0] * n

    pos = 0
    while pos < n:
        v = pairs[pos][0]
        start = pos
        while pos < n and pairs[pos][0] == v:
            pos += 1
        end = pos
        less = start
        equal = end - start
        pct = 100.0 * (less + 0.5 * equal) / n
        for _, idx in pairs[start:end]:
            out[idx] = pct

    return out


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


def _inject_efficiency_metrics(rows: List[Dict[str, Any]], adapter: Any) -> List[Dict[str, Any]]:
    """Best-effort: compute adapter efficiency keys into mapped rows if missing.

    This keeps README-facing filtering consistent even if some adapters/servers
    only return primary metrics from mapping.
    """
    eff_list = list(getattr(adapter, "efficiency", []) or [])
    if not eff_list:
        return [dict(r) for r in rows]

    out: List[Dict[str, Any]] = []
    for raw in rows:
        r: Dict[str, Any] = dict(raw)
        for eff in eff_list:
            if eff.key in r:
                continue

            make = max(0.0, _resolve_expr(getattr(eff, "make", ""), r))
            att = max(
                float(getattr(eff, "min_den", 1.0)),
                _resolve_expr(getattr(eff, "attempt", ""), r),
            )
            val = (make / att) if att > 0 else 0.0
            val = _apply_transform_value(val, getattr(eff, "transform", None))
            val = _clamp_value(val, getattr(eff, "clamp", None))
            r[eff.key] = float(val)

        out.append(r)
    return out


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


def _resolve_bucket_weights(
    adapter: Any,
    *,
    weights: Optional[Union[str, Dict[str, float]]] = None,          # preset name OR bucket->weight
    weights_override: Optional[Dict[str, float]] = None,             # legacy override
) -> Tuple[Dict[str, float], Optional[str]]:
    """
    Resolve bucket weights + return (bucket_weights, preset_name_used_if_any).

    Precedence:
      1) `weights` dict
      2) `weights_override`
      3) `weights` preset name (string)
      4) adapter.weights["pri"]
    """
    if isinstance(weights, dict):
        return dict(weights), None
    if weights_override:
        return dict(weights_override), None

    preset = None
    if isinstance(weights, str) and weights.strip():
        preset = weights.strip()

    table = getattr(adapter, "weights", {}) or {}
    if preset and preset in table:
        return dict(table.get(preset) or {}), preset

    return dict(table.get("pri") or {}), "pri" if "pri" in table else preset


def _apply_penalties_to_bucket_weights(
    bucket_weights: Dict[str, float],
    adapter: Any,
    *,
    penalty_profile: Optional[str],
    penalties_override: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    """
    HOWTO semantics: extra downweight by bucket per preset.
    Implementation: weight[b] *= max(0, 1 - penalty[b])
    """
    penalties: Dict[str, float] = {}
    if penalties_override:
        penalties = dict(penalties_override)
    else:
        table = getattr(adapter, "penalties", {}) or {}
        if penalty_profile and penalty_profile in table:
            penalties = dict(table.get(penalty_profile) or {})

    if not penalties:
        return bucket_weights

    out = dict(bucket_weights)
    for b, p in penalties.items():
        try:
            pv = float(p)
        except Exception:
            continue
        if b not in out:
            continue
        out[b] = out[b] * max(0.0, 1.0 - pv)
    return out


def _apply_output_toggles(item: Dict[str, Any], output: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    If output is None: preserve legacy payload.
    If output is provided: apply v2.1 toggles.
    """
    if output is None:
        return item

    show_weights = bool(output.get("show_weights", False))
    hide_pri_raw = bool(output.get("hide_pri_raw", True))
    show_components = bool(output.get("show_components", True))
    show_buckets = bool(output.get("show_buckets", True))
    show_context_used = bool(output.get("show_context_used", False))

    out = dict(item)
    if not show_weights:
        out.pop("weights", None)
    if hide_pri_raw:
        out.pop("pri_raw", None)
    if not show_components:
        out.pop("components", None)
    if not show_buckets:
        out.pop("buckets", None)
    if not show_context_used:
        out.pop("context_used", None)

    return out


# ──────────────────────────────────────────────────────────────────────────────
# Schema-driven filters (adapter-derived)
# ──────────────────────────────────────────────────────────────────────────────

_NUM_RE = re.compile(r"^\s*[-+]?\d+(?:\.\d+)?\s*$")
_OP_NUM_RE = re.compile(r"^\s*(<=|>=|==|!=|=|<|>)\s*([-+]?\d+(?:\.\d+)?)\s*$")


def _ci_get(row: Mapping[str, Any], key: str) -> Any:
    """Case-insensitive key lookup (best-effort)."""
    if key in row:
        return row.get(key)
    lk = str(key).lower()
    for k in row.keys():
        try:
            if str(k).lower() == lk:
                return row.get(k)
        except Exception:
            continue
    return None


def _adapter_dimensions(adapter: Any) -> Dict[str, Dict[str, Any]]:
    dims = getattr(adapter, "dimensions", None) or {} # pyright: ignore[reportUnknownVariableType]
    if isinstance(dims, Mapping):
        out: Dict[str, Dict[str, Any]] = {}
        for k, v in dims.items(): # pyright: ignore[reportUnknownVariableType]
            ks = str(k).strip() # pyright: ignore[reportUnknownArgumentType]
            if not ks:
                continue
            if isinstance(v, Mapping):
                out[ks] = dict(v) # pyright: ignore[reportUnknownArgumentType]
            else:
                out[ks] = {}
        return out
    return {}


def _passes_dimension_filters(raw: Mapping[str, Any], filters: Optional[Dict[str, Any]], adapter: Any) -> bool:
    """
    If an adapter declares `dimensions` in its YAML schema, allow those dimension
    keys to be used as raw-row filters.

    Supported forms:
      - filters["map"] = "MapA" OR ["MapA", "MapB"]
      - filters["dimensions"] = {"map": "MapA", "side": ["Attack"]}

    Dimension matching is string-based and case-insensitive.
    """
    if not filters:
        return True

    dims = _adapter_dimensions(adapter)
    if not dims:
        return True

    dim_filters_any = filters.get("dimensions")
    dim_filters: Dict[str, Any] = dict(dim_filters_any) if isinstance(dim_filters_any, Mapping) else {} # pyright: ignore[reportUnknownArgumentType]

    # Also accept dimension keys at the top-level of filters.
    for dk in dims.keys():
        if dk in filters and dk not in dim_filters:
            dim_filters[dk] = filters.get(dk)

    for dk, want_any in dim_filters.items():
        if want_any is None:
            continue
        want: List[str] = []
        if isinstance(want_any, str):
            want = [want_any]
        elif isinstance(want_any, (list, tuple, set)):
            want = [str(x) for x in want_any if str(x).strip()] # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        else:
            # Unknown filter shape -> ignore.
            continue

        if not want:
            continue

        got = _ci_get(raw, dk)
        if got is None:
            return False
        got_s = str(got).strip().lower()
        want_s = {str(x).strip().lower() for x in want}
        if got_s not in want_s:
            return False

    return True


def _adapter_filters(adapter: Any) -> Dict[str, Mapping[str, Any]]:
    """Return adapter-declared filter definitions from schema."""
    table = getattr(adapter, "filters", None) or {} # pyright: ignore[reportUnknownVariableType]
    out: Dict[str, Mapping[str, Any]] = {}
    if isinstance(table, Mapping):
        for k, v in table.items(): # pyright: ignore[reportUnknownVariableType]
            ks = str(k).strip() # pyright: ignore[reportUnknownArgumentType]
            if not ks:
                continue
            if isinstance(v, Mapping):
                out[ks] = v
            else:
                out[ks] = {}
    return out


def _parse_predicate_any(pred_any: Any, *, default_metric: str) -> Optional[Dict[str, Any]]:
    """Parse a single predicate in flexible forms into {metric, op, value}."""
    if pred_any is None:
        return None

    # Mapping form: {op, value, metric?}
    if isinstance(pred_any, Mapping):
        metric = pred_any.get("metric", pred_any.get("stat", default_metric)) # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType, reportUnknownVariableType]
        op_any = pred_any.get("op") # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        value_any = pred_any.get("value", pred_any.get("val")) # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        op = str(op_any).strip() if op_any is not None else "" # pyright: ignore[reportUnknownArgumentType]
        if not op:
            op = ">="  # default
        if op == "=":
            op = "=="
        if metric is None:
            metric = default_metric
        return {"metric": str(metric).strip() or default_metric, "op": op, "value": value_any} # pyright: ignore[reportUnknownArgumentType]

    # String form: ">=10" / "< 3.5" / "10" (defaults to >=)
    if isinstance(pred_any, str):
        s = pred_any.strip()
        if not s:
            return None
        m = _OP_NUM_RE.match(s)
        if m:
            op, num = m.group(1), m.group(2)
            if op == "=":
                op = "=="
            return {"metric": default_metric, "op": op, "value": float(num)}
        if _NUM_RE.match(s):
            return {"metric": default_metric, "op": ">=", "value": float(s)}
        return None

    # Numeric form: 10 => >= 10
    if isinstance(pred_any, (int, float)):
        return {"metric": default_metric, "op": ">=", "value": float(pred_any)}

    return None


def _parse_filter_payload(payload: Any, *, default_metric: str) -> Tuple[List[Dict[str, Any]], str]:
    """
    Normalize a user filter payload into (predicates, mode).

    - mode: include-only (default) or exclude-only
    - predicates: list of {metric, op, value}
    """
    mode = "include-only"
    preds_any: Any = payload

    if isinstance(payload, Mapping):
        # Optional explicit mode
        m = payload.get("mode") # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if m is not None:
            mode = str(m).strip().lower() or "include-only" # pyright: ignore[reportUnknownArgumentType]

        # "predicates": [...] (preferred)
        if "predicates" in payload and isinstance(payload.get("predicates"), (list, tuple)): # pyright: ignore[reportUnknownMemberType]
            preds_any = payload.get("predicates") # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        # single predicate dict
        elif any(k in payload for k in ("op", "value", "val", "metric", "stat")):
            preds_any = [payload]

    preds: List[Dict[str, Any]] = []
    if isinstance(preds_any, (list, tuple)):
        for it in preds_any: # pyright: ignore[reportUnknownVariableType]
            p = _parse_predicate_any(it, default_metric=default_metric)
            if p is not None:
                preds.append(p)
    else:
        p = _parse_predicate_any(preds_any, default_metric=default_metric)
        if p is not None:
            preds.append(p)

    return preds, mode


def _passes_declared_adapter_filters(
    row: Mapping[str, Any],
    adapter: Any,
    filters: Optional[Dict[str, Any]],
    *,
    reserved_filter_keys: Optional[Sequence[str]] = None,
) -> bool:
    """
    Apply adapter-declared filters (schema `filters:`) against a mapped row.

    Adapter filter definitions are *declarative*; this function interprets
    whatever filter payload the caller provides for those keys.
    """
    if not filters:
        return True

    reserved = {str(x) for x in (reserved_filter_keys or ())}
    defs = _adapter_filters(adapter)
    if not defs:
        return True

    for fkey, spec in defs.items():
        if fkey in reserved:
            continue
        if fkey not in filters or filters.get(fkey) is None:
            continue

        default_metric = str(spec.get("metric", spec.get("stat", fkey))).strip() or fkey
        accepts_any = spec.get("accepts")
        accepts = None
        if isinstance(accepts_any, (list, tuple, set)):
            accepts = {str(x).strip() for x in accepts_any if str(x).strip()} # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
            accepts = {"==" if x == "=" else x for x in accepts}

        preds, mode = _parse_filter_payload(filters.get(fkey), default_metric=default_metric)
        if not preds:
            raise ValueError(f"Filter '{fkey}' was provided but could not be parsed.")

        # Validate ops if the schema declares accepted ops
        if accepts is not None:
            for p in preds:
                op = str(p.get("op", "")).strip()
                if op == "=":
                    op = "=="
                    p["op"] = op
                if op and op not in accepts:
                    raise ValueError(f"Filter '{fkey}' uses op '{op}' not in accepts={sorted(accepts)}")

        if not _passes_stat_where(row, preds, mode=mode):
            return False

    return True


# ──────────────────────────────────────────────────────────────────────────────
# Filters (v2.1)
# ──────────────────────────────────────────────────────────────────────────────

def _passes_raw_filters(
    raw: Mapping[str, Any],
    filters: Optional[Dict[str, Any]],
    *,
    adapter: Optional[Any] = None,
) -> bool:
    if not filters:
        return True

    # Adapter-defined enum-like dimensions (schema-driven)
    if adapter is not None:
        if not _passes_dimension_filters(raw, filters, adapter):
            return False

    # position: ["SG","SF"] etc
    if "position" in filters and filters["position"] is not None:
        allowed = filters["position"]
        if isinstance(allowed, (list, tuple, set)):
            pos = raw.get("position", raw.get("pos", None))
            if pos is None or str(pos) not in {str(x) for x in allowed}: # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
                return False

    # games_played_gte: 10
    if "games_played_gte" in filters and filters["games_played_gte"] is not None:
        want = _to_float(filters["games_played_gte"], 0.0)
        gp = raw.get("games_played", raw.get("gp", raw.get("games", None)))
        if _to_float(gp, 0.0) < want:
            return False

    return True


def _passes_stat_where(mapped: Mapping[str, Any], stat_where: Any, *, mode: str = "include-only") -> bool:
    """
    stat_where supports:
      - a single predicate dict
      - or a list/tuple of predicate dicts

    Each predicate supports:
      - stat: "gp" (alias: metric)
      - op: "<", "<=", ">", ">=", "==", "!=", "="
      - value: number

    mode:
      - "include-only": keep rows that pass all predicates (default)
      - "exclude-only": keep rows that fail at least one predicate (inverse)
    """
    if not stat_where:
        return True

    preds: List[Any]
    if isinstance(stat_where, Mapping):
        preds = [stat_where]
    elif isinstance(stat_where, (list, tuple)):
        preds = list(stat_where) # pyright: ignore[reportUnknownArgumentType]
    else:
        return True

    def _cmp(a: float, op: str, b: float) -> bool:
        # Accept "=" as "=="
        if op == "=":
            op = "=="

        if op == "<":
            return a < b
        if op == "<=":
            return a <= b
        if op == ">":
            return a > b
        if op == ">=":
            return a >= b
        if op == "==":
            return a == b
        if op == "!=":
            return a != b
        return False

    all_ok = True

    for pred in preds:
        if not isinstance(pred, Mapping):
            continue

        stat = pred.get("stat", pred.get("metric")) # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        op = str(pred.get("op", "")).strip() # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        val = pred.get("value") # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

        if not isinstance(stat, str) or not stat or not op:
            continue

        a = _to_float(mapped.get(stat, 0.0), 0.0)
        b = _to_float(val, 0.0)

        if not _cmp(a, op, b):
            all_ok = False
            break

    if str(mode or "include-only").strip().lower() == "exclude-only":
        return not all_ok
    return all_ok


# ──────────────────────────────────────────────────────────────────────────────
# MAPPED PRI (internal kernel API) — used by calculator & SLAPI
# ──────────────────────────────────────────────────────────────────────────────

def calculate_pri(
    mapped_rows: List[Dict[str, Any]],
    adapter: Any,
    *,
    # legacy:
    weights_override: Optional[Dict[str, float]] = None,
    # v2.1:
    weights: Optional[Union[str, Dict[str, float]]] = None,
    penalties_override: Optional[Dict[str, float]] = None,
    output: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Dict[str, float]]] = None,
    caps_override: Optional[Dict[str, float]] = None,
    _timing: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """
    Score already-mapped rows. (Internal API)
    Public README API is `calculate_pri_single/batch`, which accept raw rows.
    """
    T = _timing

    with (T.stage("spec") if T else nullcontext()):
        metrics_spec = getattr(adapter, "metrics", []) or []

        # Only bucketed metrics participate in weights + bucket aggregation.
        metric_keys: List[str] = []
        metric_to_bucket: Dict[str, str] = {}
        invert_map: Dict[str, bool] = {}

        for m in metrics_spec:
            invert_map[m.key] = bool(getattr(m, "invert", False))
            b = getattr(m, "bucket", None)
            if b is None:
                continue
            bs = str(b).strip()
            if not bs:
                continue
            metric_keys.append(m.key)
            metric_to_bucket[m.key] = bs

    with (T.stage("inject_eff") if T else nullcontext()):
        eff_list = list(getattr(adapter, "efficiency", []) or [])
        extended_rows: List[Dict[str, Any]] = []

        if not eff_list:
            extended_rows = [dict(r) for r in mapped_rows]
        else:
            for raw in mapped_rows:
                r: Dict[str, Any] = dict(raw)

                for eff in eff_list:
                    # Ensure derived eff metrics are known bucketed metrics.
                    eb = getattr(eff, "bucket", None)
                    if eb is not None:
                        metric_to_bucket[eff.key] = str(eb)
                    invert_map[eff.key] = bool(getattr(eff, "invert", False))
                    if eff.key not in metric_keys:
                        metric_keys.append(eff.key)

                    if eff.key not in r:
                        make = max(0.0, _resolve_expr(getattr(eff, "make", ""), r))
                        att = max(
                            float(getattr(eff, "min_den", 1.0)),
                            _resolve_expr(getattr(eff, "attempt", ""), r),
                        )
                        val = (make / att) if att > 0 else 0.0
                        val = _apply_transform_value(val, getattr(eff, "transform", None))
                        val = _clamp_value(val, getattr(eff, "clamp", None))

                        # IMPORTANT: do NOT clamp01 here — keep adapter clamps intact
                        r[eff.key] = float(val)

                extended_rows.append(r)

    with (T.stage("caps") if T else nullcontext()):
        # leader/floor context selection
        if caps_override:
            # Back-compat: interpret "cap" as leader with floor=0
            # and for invert metrics, best=0, worst=cap.
            ctx: Dict[str, Dict[str, float]] = {}
            for k, cap in caps_override.items():
                kk = str(k)
                c = max(1e-6, _to_float(cap, 1.0))
                if invert_map.get(kk, False):
                    ctx[kk] = {"leader": 0.0, "floor": c}
                else:
                    ctx[kk] = {"leader": c, "floor": 0.0}
            context_used = "caps_override"
        elif context is None and len(extended_rows) == 1:
            ctx = _context_from_clamps(adapter, invert_map)
            context_used = "clamps"
        else:
            ctx = context or _batch_context_from_rows(extended_rows, metric_keys, invert_map)
            context_used = "batch" if context is None else "external"

    with (T.stage("weights") if T else nullcontext()):
        bucket_weights, preset_name = _resolve_bucket_weights(
            adapter,
            weights=weights,
            weights_override=weights_override,
        )
        bucket_weights = _apply_penalties_to_bucket_weights(
            bucket_weights,
            adapter,
            penalty_profile=preset_name,
            penalties_override=penalties_override,
        )

        per_metric_weights = per_metric_weights_from_buckets(metric_to_bucket, bucket_weights)
        scored_metrics = {k for k, w in per_metric_weights.items() if abs(w) > 1e-12}

        # Normalize once (same for every row)
        unit_w = normalize_weights(per_metric_weights)

    with (T.stage("score_rows") if T else nullcontext()):
        tmp: List[Tuple[int, Dict[str, Any], float]] = []
        buckets_def = getattr(adapter, "buckets", {}) or {}
        bucket_keys = list(buckets_def.keys())

        for idx, r in enumerate(extended_rows):
            comps: Dict[str, float] = {}
            total = 0.0

            # If weights are empty, everything is 0 => PRI=55. That’s correct behavior.
            for k, w in unit_w.items():
                leader, floor = _ctx_get(ctx, k)
                norm = _norm01_from_ctx(r.get(k, 0.0), leader, floor, invert_map.get(k, False))
                comps[k] = norm
                total += norm * w

            raw01 = clamp01(total)
            RAW01_SCALE = 1.4  # start here (tune once)
            raw01 = clamp01(raw01 * RAW01_SCALE)

            # Bucket scores
            bucket_scores: Dict[str, float] = {b: 0.0 for b in bucket_keys}
            bucket_counts: Dict[str, int] = {b: 0 for b in bucket_keys}

            for mk, nv in comps.items():
                if mk not in scored_metrics:
                    continue
                b = metric_to_bucket.get(mk)
                if not b:
                    continue
                if b not in bucket_scores:
                    continue
                bucket_scores[b] += float(nv)
                bucket_counts[b] += 1

            # average per bucket; drop empty buckets
            for b in list(bucket_scores.keys()):
                c = bucket_counts.get(b, 0)
                if c > 0:
                    bucket_scores[b] /= c
                else:
                    bucket_scores.pop(b, None)

            payload = {
                "buckets": bucket_scores,
                "components": comps,
                "weights": dict(unit_w),
                "context_used": context_used,
                "pri_raw": raw01,
                "_i": idx,
            }
            tmp.append((idx, payload, raw01))

        # Percentiles (0..1) for window profiles
        raw_list = [raw for _, _, raw in tmp]
        pct_list_01 = [p / 100.0 for p in _midrank_percentiles(raw_list)]

        # Load configured profiles (or defaults)
        profiles_in = getattr(adapter, "score_profiles", None) or {} # pyright: ignore[reportUnknownVariableType]
        profiles: Dict[str, Mapping[str, Any]] = {}
        if isinstance(profiles_in, Mapping):
            for name, prof_any in profiles_in.items(): # pyright: ignore[reportUnknownVariableType]
                if isinstance(name, str) and isinstance(prof_any, Mapping):
                    profiles[name] = prof_any

        # Ensure we have a canonical "PRI" profile (case-insensitive)
        if "PRI" not in profiles:
            pri_key = None
            for k in list(profiles.keys()):
                if k.upper() == "PRI":
                    pri_key = k
                    break
            if pri_key is not None:
                profiles["PRI"] = profiles.pop(pri_key)
            else:
                profiles["PRI"] = {"kind": "affine", "lo": 55, "hi": 99}

        by_idx: Dict[int, Dict[str, Any]] = {}

        for (idx, payload, raw01), pct01 in zip(tmp, pct_list_01):
            item = dict(payload)

            # Compute all profiles
            scores: Dict[str, int] = {}
            for name, prof in profiles.items():
                val = _score_from_profile(prof, raw01=raw01, pct01=pct01)
                scores[name] = int(round(val))

            # Back-compat: keep primary PRI in "pri"
            item["pri"] = scores.get("PRI", int(round(_affine01(raw01, 55.0, 99.0))))

            # Full map + flattened fields
            item["scores"] = dict(scores)
            for name, sval in scores.items():
                slug = _slug_profile_key(name)
                if slug != "pri":
                    item[slug] = sval

            item.pop("_i", None)
            by_idx[idx] = item

        out_list = [by_idx[i] for i in range(len(tmp))]

    want_percentiles = bool(output.get("percentiles", False)) if output is not None else False
    if want_percentiles:
        pcts = _midrank_percentiles([_to_float(r.get("pri_raw", 0.0), 0.0) for r in out_list])
        for r, pct in zip(out_list, pcts):
            r["percentile"] = pct

    return [_apply_output_toggles(r, output) for r in out_list]


def calculate_pri_single_mapped(
    mapped_row: Mapping[str, Any],
    adapter: Any,
    *,
    weights_override: Optional[Dict[str, float]] = None,
    weights: Optional[Union[str, Dict[str, float]]] = None,
    penalties_override: Optional[Dict[str, float]] = None,
    output: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Dict[str, float]]] = None,
    caps_override: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Internal single-row mapped wrapper (dict)."""
    rows = calculate_pri(
        [dict(mapped_row)],
        adapter,
        weights_override=weights_override,
        weights=weights,
        penalties_override=penalties_override,
        output=output,
        context=context,
        caps_override=caps_override,
    )
    return rows[0]


# ──────────────────────────────────────────────────────────────────────────────
# README-FACING API — RAW rows in, PRIResult out
# ──────────────────────────────────────────────────────────────────────────────

def calculate_pri_batch(
    *,
    adapter: Any,
    rows: Iterable[Mapping[str, Any]],
    weights: Optional[Union[str, Dict[str, float]]] = None,
    output: Optional[Dict[str, Any]] = None,
    filters: Optional[Dict[str, Any]] = None,
    # still accepted for internal/legacy callers:
    weights_override: Optional[Dict[str, float]] = None,
    penalties_override: Optional[Dict[str, float]] = None,
    context: Optional[Dict[str, Dict[str, float]]] = None,
    caps_override: Optional[Dict[str, float]] = None,
    timing: Optional[Any] = None,
) -> List[PRIResult]:
    """
    README-facing batch API: raw rows -> mapped -> PRI.
    """
    # Lazy import avoids circulars and reuses existing mapping sanitization behavior
    from .calculator import safe_map_raw  # type: ignore

    raw_rows = [dict(r) for r in rows]
    raw_rows = [r for r in raw_rows if _passes_raw_filters(r, filters, adapter=adapter)]

    mapped_rows: List[Dict[str, Any]] = []
    if timing:
        with timing.stage("map_raw"):
            mapped_rows = [safe_map_raw(adapter, r) for r in raw_rows]
    else:
        mapped_rows = [safe_map_raw(adapter, r) for r in raw_rows]

    # Ensure efficiency/derived metrics exist for filtering (best-effort)
    mapped_rows = _inject_efficiency_metrics(mapped_rows, adapter)

    # Apply stat_where (mapped) predicates
    stat_where = (filters or {}).get("stat_where") if filters else None
    stat_where_mode = (
        str((filters or {}).get("stat_where_mode", "include-only")).strip().lower() if filters else "include-only"
    )
    if stat_where:
        mapped_rows = [m for m in mapped_rows if _passes_stat_where(m, stat_where, mode=stat_where_mode)]

    # Apply adapter-declared schema filters (derived keys like "gp")
    # Apply adapter-declared schema filters (derived keys like "gp")
    if filters:
        mapped_rows = [
            m for m in mapped_rows
            if _passes_declared_adapter_filters(
                m,
                adapter,
                filters,
                reserved_filter_keys=(
                    "position",
                    "games_played_gte",
                    "stat_where",
                    "stat_where_mode",
                    "dimensions",
                ),
            )
        ]

    if not mapped_rows:
        return []

    scored = calculate_pri(
        mapped_rows,
        adapter=adapter,
        weights_override=weights_override,
        weights=weights,
        penalties_override=penalties_override,
        output=output,
        context=context,
        caps_override=caps_override,
        _timing=timing,
    )

    out: List[PRIResult] = []
    for item in scored:
        pri = int(_to_float(item.get("pri", 0), 0.0))
        details = dict(item)
        details.pop("pri", None)
        out.append(PRIResult(pri=pri, details=details))
    return out


def calculate_pri_single(
    *,
    adapter: Any,
    row: Mapping[str, Any],
    weights: Optional[Union[str, Dict[str, float]]] = None,
    output: Optional[Dict[str, Any]] = None,
    # accepted for completeness / parity:
    filters: Optional[Dict[str, Any]] = None,
    weights_override: Optional[Dict[str, float]] = None,
    penalties_override: Optional[Dict[str, float]] = None,
    context: Optional[Dict[str, Dict[str, float]]] = None,
    caps_override: Optional[Dict[str, float]] = None,
    timing: Optional[Any] = None,
) -> PRIResult:
    """
    README-facing single-row API: raw row -> mapped -> PRI.
    """
    res = calculate_pri_batch(
        adapter=adapter,
        rows=[row],
        weights=weights,
        output=output,
        filters=filters,
        weights_override=weights_override,
        penalties_override=penalties_override,
        context=context,
        caps_override=caps_override,
        timing=timing,
    )
    if not res:
        raise ValueError("Row was filtered out or could not be scored.")
    return res[0]


__all__ = [
    "ScoreResult",
    "PRIResult",
    # mapped kernel (internal)
    "calculate_pri",
    "calculate_pri_single_mapped",
    # README-facing API
    "calculate_pri_single",
    "calculate_pri_batch",
]
