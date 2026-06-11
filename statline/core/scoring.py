# statline/core/scoring.py
from __future__ import annotations

import re
from collections.abc import Mapping as ABCMapping
from contextlib import nullcontext
from typing import (
    Any,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
    cast,
    overload,
)

# Reuse the adapter compiler’s safe numeric semantics
from .adapters.compile import _finite as _finite_num  # type: ignore
from .adapters.types import FilterSpec, ScoreProfileSpec  # typed schema
from .normalization import clamp01
from .weights import normalize_weights

# ──────────────────────────────────────────────────────────────────────────────
# Public result surface
# ──────────────────────────────────────────────────────────────────────────────

# NOTE:
# - Keep the scorer typed internally, but the public surface is dict-like (JSON-friendly).
PRIResult = Dict[str, Any]

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

    for m in getattr(adapter, "metrics", []) or []:
        _put(m.key, getattr(m, "clamp", None), invert_map.get(m.key, False))

    for e in getattr(adapter, "efficiency", []) or []:
        _put(e.key, getattr(e, "clamp", None), invert_map.get(e.key, False))

    return out


def _slug_profile_key(name: str) -> str:
    # "PRI-AF" -> "pri_af", "PRI" -> "pri"
    return str(name).strip().lower().replace("-", "_").replace(" ", "_")


def _affine01(x01: float, lo: float, hi: float) -> float:
    x = clamp01(x01)
    return float(lo) + x * (float(hi) - float(lo))


def _profile_float(value: Any, default: float) -> float:
    """
    Preserve valid numeric zeroes.

    Do NOT use `value or default` for score-profile fields because:
      - 0.0 is a valid pct_lo
      - 0.0 may also be a valid score bound
    """
    if value is None:
        return float(default)
    return _to_float(value, default)


def _score_from_profile(
    prof_any: Any,
    *,
    raw01: float,
    pct01: float,
) -> float:
    """
    Typed-first: ScoreProfileSpec, but stays tolerant of Mapping[str, Any] for legacy.
    """
    if isinstance(prof_any, ScoreProfileSpec):
        kind = str(prof_any.kind or "affine").strip().lower()

        if kind == "affine":
            lo = _profile_float(prof_any.lo, 55.0)
            hi = _profile_float(prof_any.hi, 99.0)
            return _affine01(raw01, lo, hi)

        if kind == "window":
            out_lo = _profile_float(prof_any.out_lo, -50.0)
            out_hi = _profile_float(prof_any.out_hi, 50.0)
            pct_lo = _profile_float(prof_any.pct_lo, 0.25)
            pct_hi = _profile_float(prof_any.pct_hi, 0.75)

            span = max(1e-12, pct_hi - pct_lo)
            t = clamp01((float(pct01) - pct_lo) / span)
            return out_lo + t * (out_hi - out_lo)

        return _affine01(raw01, 55.0, 99.0)

    if isinstance(prof_any, Mapping):
        kind = str(prof_any.get("kind", "affine")).strip().lower()  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]

        if kind == "affine":
            lo = _to_float(prof_any.get("lo", 55), 55.0)  # pyright: ignore[reportUnknownMemberType]
            hi = _to_float(prof_any.get("hi", 99), 99.0)  # pyright: ignore[reportUnknownMemberType]
            return _affine01(raw01, lo, hi)

        if kind == "window":
            out_lo = _to_float(prof_any.get("out_lo", -50), -50.0)  # pyright: ignore[reportUnknownMemberType]
            out_hi = _to_float(prof_any.get("out_hi", 50), 50.0)  # pyright: ignore[reportUnknownMemberType]
            pct_lo = _to_float(prof_any.get("pct_lo", 0.25), 0.25)  # pyright: ignore[reportUnknownMemberType]
            pct_hi = _to_float(prof_any.get("pct_hi", 0.75), 0.75)  # pyright: ignore[reportUnknownMemberType]

            span = max(1e-12, pct_hi - pct_lo)
            t = clamp01((float(pct01) - pct_lo) / span)
            return out_lo + t * (out_hi - out_lo)

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
# Weights / buckets helpers
# ──────────────────────────────────────────────────────────────────────────────


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


def _resolve_bucket_weights(
    adapter: Any,
    *,
    weights: Optional[Union[str, Dict[str, float]]] = None,  # preset name OR bucket->weight
    weights_override: Optional[Dict[str, float]] = None,  # legacy override
    default_preset: str = "pri",
) -> Tuple[Dict[str, float], Optional[str]]:
    """
    Resolve bucket weights + return (bucket_weights, preset_name_used_if_any).

    Precedence:
      1) `weights` dict
      2) `weights_override`
      3) `weights` preset name (string)
      4) adapter.weights[default_preset] (fallback to adapter.weights["pri"])
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

    if default_preset in table:
        return dict(table.get(default_preset) or {}), default_preset

    return dict(table.get("pri") or {}), "pri" if "pri" in table else preset


def _apply_penalties_to_bucket_weights(
    bucket_weights: Dict[str, float],
    adapter: Any,
    *,
    penalty_profile: Optional[str],
    penalties_override: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    """
    weight[b] *= max(0, 1 - penalty[b])

    If penalties_override is provided, it is treated as global and wins.
    Otherwise, use adapter.penalties[penalty_profile] if present.
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
        pv = _finite_num(p, 0.0)
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
# Filtering (schema-driven; typed)
# ──────────────────────────────────────────────────────────────────────────────

_NUM_RE = re.compile(r"^\s*[-+]?\d+(?:\.\d+)?\s*$")
_OP_NUM_RE = re.compile(r"^\s*(<=|>=|==|!=|=|<|>)\s*([-+]?\d+(?:\.\d+)?)\s*$")


def _ci_get(row: Mapping[str, Any], key: str) -> Any:
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


def _adapter_filter_specs(adapter: Any) -> Dict[str, FilterSpec]:
    table = getattr(adapter, "filters", None) or {}  # pyright: ignore[reportUnknownVariableType]
    out: Dict[str, FilterSpec] = {}
    if isinstance(table, Mapping):
        for k, v in table.items():  # pyright: ignore[reportUnknownVariableType]
            ks = str(k).strip()  # pyright: ignore[reportUnknownArgumentType]
            if not ks:
                continue
            if isinstance(v, FilterSpec):
                out[ks] = v
    return out


def _parse_predicate_any(pred_any: Any, *, default_metric: str) -> Optional[Dict[str, Any]]:
    """
    Parse a single predicate in flexible forms into {metric, op, value}.
    Works for BOTH numeric (metric) and string (dimension) comparisons.
    """
    if pred_any is None:
        return None

    if isinstance(pred_any, Mapping):
        metric = pred_any.get("metric", pred_any.get("stat", default_metric))  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType, reportUnknownVariableType]
        op_any = pred_any.get("op")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        value_any = pred_any.get("value", pred_any.get("val"))  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        op = str(op_any).strip() if op_any is not None else ""  # pyright: ignore[reportUnknownArgumentType]
        if not op:
            op = "=="  # safest default (dimension-friendly)
        if op == "=":
            op = "=="
        return {"metric": str(metric).strip() or default_metric, "op": op, "value": value_any}  # pyright: ignore[reportUnknownArgumentType]

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
        # Non-numeric string => equality predicate (dimension filters)
        return {"metric": default_metric, "op": "==", "value": s}

    if isinstance(pred_any, (int, float)):
        return {"metric": default_metric, "op": ">=", "value": float(pred_any)}

    return None


def _parse_filter_payload(payload: Any, *, default_metric: str) -> Tuple[List[Dict[str, Any]], str]:
    mode = "include-only"
    preds_any: Any = payload

    if isinstance(payload, Mapping):
        m = payload.get("mode")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if m is not None:
            mode = str(m).strip().lower() or "include-only"  # pyright: ignore[reportUnknownArgumentType]

        if "predicates" in payload and isinstance(payload.get("predicates"), (list, tuple)):  # pyright: ignore[reportUnknownMemberType]
            preds_any = payload.get("predicates")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        elif any(k in payload for k in ("op", "value", "val", "metric", "stat")):
            preds_any = [payload]

    preds: List[Dict[str, Any]] = []
    if isinstance(preds_any, (list, tuple)):
        for it in preds_any:  # pyright: ignore[reportUnknownVariableType]
            p = _parse_predicate_any(it, default_metric=default_metric)
            if p is not None:
                preds.append(p)
    else:
        p = _parse_predicate_any(preds_any, default_metric=default_metric)
        if p is not None:
            preds.append(p)

    return preds, mode


def _passes_predicates(row: Mapping[str, Any], preds: Sequence[Mapping[str, Any]], *, mode: str) -> bool:
    """
    Generic predicate evaluator:
      - numeric ops compare floats when possible
      - ==/!= falls back to case-insensitive string compare if not numeric
    """

    def _cmp(a_any: Any, op: str, b_any: Any) -> bool:
        if op == "=":
            op = "=="

        # Try numeric if the operator is order-based, or if both look numeric
        wants_numeric = op in ("<", "<=", ">", ">=")
        if not wants_numeric:
            # heuristic: if either side is numeric-ish, still try numeric
            if isinstance(b_any, (int, float)) or (isinstance(b_any, str) and _NUM_RE.match(b_any.strip() or "")):
                wants_numeric = True

        if wants_numeric:
            a = _to_float(a_any, 0.0)
            b = _to_float(b_any, 0.0)
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

        # string-ish compare (== / != only)
        a_s = "" if a_any is None else str(a_any).strip().lower()
        b_s = "" if b_any is None else str(b_any).strip().lower()
        if op == "==":
            return a_s == b_s
        if op == "!=":
            return a_s != b_s
        return False

    all_ok = True
    for pred in preds:
        stat = pred.get("stat", pred.get("metric"))
        op = str(pred.get("op", "")).strip()
        val = pred.get("value")
        if not isinstance(stat, str) or not stat or not op:
            continue

        got = _ci_get(row, stat)
        if not _cmp(got, op, val):
            all_ok = False
            break

    if str(mode or "include-only").strip().lower() == "exclude-only":
        return not all_ok
    return all_ok


def _passes_declared_adapter_filters_typed(
    row: Mapping[str, Any],
    adapter: Any,
    filters: Optional[Dict[str, Any]],
    *,
    kind: str,  # "metric" or "dimension"
    reserved_filter_keys: Optional[Sequence[str]] = None,
) -> bool:
    """
    Apply adapter-declared typed filters (schema `filters:`).
    `kind` selects which FilterSpec.type to enforce on this pass.
    """
    if not filters:
        return True

    reserved = {str(x) for x in (reserved_filter_keys or ())}
    defs = _adapter_filter_specs(adapter)
    if not defs:
        return True

    for fkey, spec in defs.items():
        if fkey in reserved:
            continue
        if spec.type != kind:
            continue
        if fkey not in filters or filters.get(fkey) is None:
            continue

        default_field = spec.field.strip() or fkey

        preds, mode = _parse_filter_payload(filters.get(fkey), default_metric=default_field)
        if not preds:
            raise ValueError(f"Filter '{fkey}' was provided but could not be parsed.")

        # Validate mode if schema declares allowed modes
        allowed_modes = set(spec.modes or ())
        if allowed_modes and mode not in allowed_modes:
            raise ValueError(f"Filter '{fkey}' mode '{mode}' not in allowed modes={sorted(allowed_modes)}")

        # Validate ops if schema declares accepted ops
        accepts = set(spec.accepts or ())
        if accepts:
            for p in preds:
                op = str(p.get("op", "")).strip()
                if op == "=":
                    op = "=="
                    p["op"] = op
                if op and op not in accepts:
                    raise ValueError(f"Filter '{fkey}' uses op '{op}' not in accepts={sorted(accepts)}")

        if not _passes_predicates(row, preds, mode=mode):
            return False

    return True


# ──────────────────────────────────────────────────────────────────────────────
# Filters (raw pre-map)
# ──────────────────────────────────────────────────────────────────────────────


def _passes_dimension_filters(raw: Mapping[str, Any], filters: Optional[Dict[str, Any]], adapter: Any) -> bool:
    """
    Legacy convenience:
      - filters["dimensions"] = {"map": "MapA", ...}
      - also allow dimension keys at top-level of filters if adapter.dimensions defines them
    String match, case-insensitive.
    """
    if not filters:
        return True

    dims_any = getattr(adapter, "dimensions", None) or {}  # pyright: ignore[reportUnknownVariableType]
    if not isinstance(dims_any, Mapping) or not dims_any:
        return True

    dim_filters_any = filters.get("dimensions")
    dim_filters: Dict[str, Any] = dict(dim_filters_any) if isinstance(dim_filters_any, Mapping) else {}  # pyright: ignore[reportUnknownArgumentType]

    # also accept dimension keys at the top-level
    for dk in dims_any.keys():  # pyright: ignore[reportUnknownVariableType]
        k = str(dk).strip()  # pyright: ignore[reportUnknownArgumentType]
        if k and k in filters and k not in dim_filters:
            dim_filters[k] = filters.get(k)

    for dk, want_any in dim_filters.items():
        if want_any is None:
            continue

        want: List[str] = []
        if isinstance(want_any, str):
            want = [want_any]
        elif isinstance(want_any, (list, tuple, set)):
            want = [str(x) for x in want_any if str(x).strip()]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        else:
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


def passes_raw_filters(  # pyright: ignore[reportUnusedFunction]
    raw: Mapping[str, Any],
    filters: Optional[Dict[str, Any]],
    *,
    adapter: Optional[Any] = None,
) -> bool:
    if not filters:
        return True

    if adapter is not None:
        if not _passes_dimension_filters(raw, filters, adapter):
            return False

        # Typed schema filters of type "dimension" apply on raw rows
        if not _passes_declared_adapter_filters_typed(
            raw,
            adapter,
            filters,
            kind="dimension",
            reserved_filter_keys=("dimensions", "stat_where", "stat_where_mode", "position", "games_played_gte"),
        ):
            return False

    if "position" in filters and filters["position"] is not None:
        allowed = filters["position"]
        if isinstance(allowed, (list, tuple, set)):
            pos = raw.get("position", raw.get("pos", None))
            if pos is None or str(pos) not in {str(x) for x in allowed}:  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
                return False

    if "games_played_gte" in filters and filters["games_played_gte"] is not None:
        want = _to_float(filters["games_played_gte"], 0.0)
        gp = raw.get("games_played", raw.get("gp", raw.get("games", None)))
        if _to_float(gp, 0.0) < want:
            return False

    return True


# ──────────────────────────────────────────────────────────────────────────────
# Canonical scorer — MAPPED rows in, dicts out
# ──────────────────────────────────────────────────────────────────────────────


@overload
def calculate_pri(
    rows_or_row: Mapping[str, Any],
    adapter: Any,
    *,
    weights_override: Optional[Dict[str, float]] = None,
    weights: Optional[Union[str, Dict[str, float]]] = None,
    penalties_override: Optional[Dict[str, float]] = None,
    output: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Dict[str, float]]] = None,
    caps_override: Optional[Dict[str, float]] = None,
    timing: Optional[Any] = None,
) -> PRIResult: ...


@overload
def calculate_pri(
    rows_or_row: Iterable[Mapping[str, Any]],
    adapter: Any,
    *,
    weights_override: Optional[Dict[str, float]] = None,
    weights: Optional[Union[str, Dict[str, float]]] = None,
    penalties_override: Optional[Dict[str, float]] = None,
    output: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Dict[str, float]]] = None,
    caps_override: Optional[Dict[str, float]] = None,
    timing: Optional[Any] = None,
) -> List[PRIResult]: ...


def calculate_pri(
    rows_or_row: Union[Mapping[str, Any], Iterable[Mapping[str, Any]]],
    adapter: Any,
    *,
    weights_override: Optional[Dict[str, float]] = None,
    weights: Optional[Union[str, Dict[str, float]]] = None,
    penalties_override: Optional[Dict[str, float]] = None,
    output: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Dict[str, float]]] = None,
    caps_override: Optional[Dict[str, float]] = None,
    timing: Optional[Any] = None,
) -> Union[PRIResult, List[PRIResult]]:
    """
    Canonical PRI scorer.

    Contract:
    - Input rows are ALREADY mapped (adapter.map_raw/map_raw_to_metrics output).
    - Output is dict-like (JSON friendly).
    """
    # NOTE: `Mapping` is also `Iterable` (iterates keys), so use an actual ABC check + casts
    # to keep Pyright/Pylance narrowing correct.
    if isinstance(rows_or_row, ABCMapping):
        row = cast(Mapping[str, Any], rows_or_row)
        mapped_rows: List[Dict[str, Any]] = [dict(row)]
        is_single = True
    else:
        rows = cast(Iterable[Mapping[str, Any]], rows_or_row) # pyright: ignore[reportUnnecessaryCast]
        mapped_rows = [dict(r) for r in rows]
        is_single = False

    out = _calculate_pri_batch_mapped(
        mapped_rows,
        adapter,
        weights_override=weights_override,
        weights=weights,
        penalties_override=penalties_override,
        output=output,
        context=context,
        caps_override=caps_override,
        _timing=timing,
    )
    return out[0] if is_single else out


# ──────────────────────────────────────────────────────────────────────────────
# MAPPED PRI (internal kernel API) — used by calculator & SLAPI
# ──────────────────────────────────────────────────────────────────────────────


def _calculate_pri_batch_mapped(
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
    Score already-mapped rows. (Kernel API)
    """
    T = _timing

    with (T.stage("spec") if T else nullcontext()):
        metrics_spec = getattr(adapter, "metrics", []) or []

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

        # Include efficiency keys declared by the adapter.
        for eff in list(getattr(adapter, "efficiency", []) or []):
            eb = getattr(eff, "bucket", None)
            if eb is not None:
                metric_to_bucket[eff.key] = str(eb)
            invert_map[eff.key] = bool(getattr(eff, "invert", False))
            if eff.key not in metric_keys:
                metric_keys.append(eff.key)

        rows_used = mapped_rows

    with (T.stage("caps") if T else nullcontext()):
        if caps_override:
            ctx: Dict[str, Dict[str, float]] = {}
            for k, cap in caps_override.items():
                kk = str(k)
                c = max(1e-6, _to_float(cap, 1.0))
                if invert_map.get(kk, False):
                    ctx[kk] = {"leader": 0.0, "floor": c}
                else:
                    ctx[kk] = {"leader": c, "floor": 0.0}
        elif context is not None:
            ctx = {}
            for k, v in (context or {}).items():
                kk = str(k)
                ctx[kk] = {
                    "leader": _to_float(v.get("leader", 1.0), 1.0),
                    "floor": _to_float(v.get("floor", 0.0), 0.0),
                }
        elif len(rows_used) == 1:
            ctx = _context_from_clamps(adapter, invert_map)
        else:
            # batch-derived context fallback
            vals: Dict[str, List[float]] = {k: [] for k in metric_keys}
            for r in rows_used:
                for k in metric_keys:
                    v = r.get(k)
                    if v is None:
                        continue
                    try:
                        vals[k].append(float(v))
                    except Exception:
                        pass

            ctx = {}
            for k in metric_keys:
                xs = vals[k]
                if not xs:
                    ctx[k] = {"leader": 0.0, "floor": 1.0} if invert_map.get(k, False) else {"leader": 1.0, "floor": 0.0}
                    continue
                lo = min(xs)
                hi = max(xs)
                ctx[k] = {"leader": lo, "floor": hi} if invert_map.get(k, False) else {"leader": hi, "floor": lo}

    with (T.stage("ctx_used") if T else nullcontext()):
        context_used = {k: {"leader": _ctx_get(ctx, k)[0], "floor": _ctx_get(ctx, k)[1]} for k in metric_keys}

    # ──────────────────────────────────────────────────────────────────────────
    # Score profiles (typed) + weights alignment
    # ──────────────────────────────────────────────────────────────────────────

    with (T.stage("profiles") if T else nullcontext()):
        profiles_in = getattr(adapter, "score_profiles", None) or {}  # pyright: ignore[reportUnknownVariableType]
        profiles: Dict[str, ScoreProfileSpec] = {}
        profile_order: List[str] = []

        if isinstance(profiles_in, Mapping):
            for name, prof in profiles_in.items():  # pyright: ignore[reportUnknownVariableType]
                if isinstance(name, str) and isinstance(prof, ScoreProfileSpec):
                    profiles[name] = prof
                    profile_order.append(name)

        # Adapter-defined profiles ONLY
        if not profiles:
            raise ValueError("Adapter defines no score_profiles; cannot score.")

        # Choose primary profile:
        # 1) Explicit PRI if present
        # 2) Otherwise first declared (stable order)
        primary_name = "PRI" if "PRI" in profiles else (profile_order[0] if profile_order else next(iter(profiles)))

        primary_profile = profiles[primary_name]
        primary_default_preset = str(primary_profile.weights_profile or "pri").strip() or "pri"

    with (T.stage("weights") if T else nullcontext()):
        # Primary profile weights resolution
        pri_bucket_weights, pri_preset_used = _resolve_bucket_weights(
            adapter,
            weights=weights,
            weights_override=weights_override,
            default_preset=primary_default_preset,
        )

        pri_bucket_weights = _apply_penalties_to_bucket_weights(
            pri_bucket_weights,
            adapter,
            penalty_profile=pri_preset_used,
            penalties_override=penalties_override,
        )

        pri_per_metric = per_metric_weights_from_buckets(metric_to_bucket, pri_bucket_weights)
        pri_unit_w = normalize_weights(pri_per_metric)
        pri_scored_metrics = {k for k, w in pri_unit_w.items() if abs(w) > 1e-12}

        # Per-profile unit weights
        unit_w_by_profile: Dict[str, Dict[str, float]] = {primary_name: dict(pri_unit_w)}
        preset_used_by_profile: Dict[str, Optional[str]] = {primary_name: pri_preset_used}

        for name, prof in profiles.items():
            if name == primary_name:
                continue

            preset_name = str(prof.weights_profile or "").strip() or "pri"
            bw, used = _resolve_bucket_weights(
                adapter,
                weights=preset_name,
                weights_override=None,
                default_preset=preset_name,
            )
            bw = _apply_penalties_to_bucket_weights(
                bw,
                adapter,
                penalty_profile=used,
                penalties_override=penalties_override,
            )
            pm = per_metric_weights_from_buckets(metric_to_bucket, bw)
            unit_w_by_profile[name] = normalize_weights(pm)
            preset_used_by_profile[name] = used

    with (T.stage("score_rows") if T else nullcontext()):
        buckets_def = getattr(adapter, "buckets", {}) or {}
        bucket_keys = list(buckets_def.keys())

        # First pass: compute components once, then raw01 per profile
        tmp_rows: List[Dict[str, Any]] = []
        raw01_by_profile: Dict[str, List[float]] = {name: [] for name in unit_w_by_profile.keys()}

        RAW01_SCALE = 1  # tune once

        for idx, r in enumerate(rows_used):
            comps: Dict[str, float] = {}

            # Fail fast if the adapter did not materialize required metrics.
            missing = [k for k in metric_keys if k not in r]
            if missing:
                head = ", ".join(missing[:6])
                tail = "…" if len(missing) > 6 else ""
                raise KeyError(f"Mapped row missing required metrics: {head}{tail}")

            # Components are independent of which weights profile is used
            for k in metric_keys:
                leader, floor = _ctx_get(ctx, k)
                comps[k] = _norm01_from_ctx(r.get(k, 0.0), leader, floor, invert_map.get(k, False))

            # Bucket scores: preserve legacy behavior (based on PRIMARY-scored metrics only)
            bucket_scores: Dict[str, float] = {b: 0.0 for b in bucket_keys}
            bucket_counts: Dict[str, int] = {b: 0 for b in bucket_keys}

            for mk, nv in comps.items():
                if mk not in pri_scored_metrics:
                    continue
                b = metric_to_bucket.get(mk)
                if not b or b not in bucket_scores:
                    continue
                bucket_scores[b] += float(nv)
                bucket_counts[b] += 1

            for b in list(bucket_scores.keys()):
                c = bucket_counts.get(b, 0)
                if c > 0:
                    bucket_scores[b] /= c
                else:
                    bucket_scores.pop(b, None)

            # Compute raw01 per profile
            for pname, uw in unit_w_by_profile.items():
                total = 0.0
                for mk, w in uw.items():
                    total += comps.get(mk, 0.0) * float(w)
                x01 = clamp01(total)
                x01 = clamp01(x01 * RAW01_SCALE)
                raw01_by_profile[pname].append(x01)

            payload = {
                "buckets": bucket_scores,
                "components": comps,
                "weights": dict(pri_unit_w),  # legacy: primary profile weights only
                "context_used": context_used,
                "pri_raw": raw01_by_profile[primary_name][-1],  # legacy: pri_raw == PRIMARY
                "primary_profile": primary_name,
                "_i": idx,
            }

            tmp_rows.append(payload)

        # Percentiles per profile (only used by window profiles, but cheap to compute consistently)
        pct01_by_profile: Dict[str, List[float]] = {}
        for pname, xs in raw01_by_profile.items():
            pct01_by_profile[pname] = [p / 100.0 for p in _midrank_percentiles(list(xs))]

        # Final pass: build output items
        by_idx: Dict[int, Dict[str, Any]] = {}
        for payload in tmp_rows:
            idx = int(payload.get("_i", 0))
            item = dict(payload)
            item.pop("_i", None)

            scores: Dict[str, int] = {}
            for name, prof in profiles.items():
                raw01 = raw01_by_profile.get(name, [0.0])[idx]
                pct01 = pct01_by_profile.get(name, [0.5])[idx]
                sval = _score_from_profile(prof, raw01=raw01, pct01=pct01)
                scores[name] = int(round(sval))

            # Back-compat: keep primary PRI in "pri"
            item["pri"] = scores.get(
                primary_name,
                int(round(_affine01(item.get("pri_raw", 0.0), 55.0, 99.0))),
            )

            # Full map + flattened fields
            item["scores"] = dict(scores)
            for name, sval in scores.items():
                slug = _slug_profile_key(name)
                if slug != "pri":
                    item[slug] = sval

            by_idx[idx] = item

        out_list = [by_idx[i] for i in range(len(tmp_rows))]

    want_percentiles = bool(output.get("percentiles", False)) if output is not None else False
    if want_percentiles:
        pcts = _midrank_percentiles([_to_float(r.get("pri_raw", 0.0), 0.0) for r in out_list])
        for r, pct in zip(out_list, pcts):
            r["percentile"] = pct

    return [_apply_output_toggles(r, output) for r in out_list]


__all__ = ["PRIResult", "calculate_pri", "passes_raw_filters"]
