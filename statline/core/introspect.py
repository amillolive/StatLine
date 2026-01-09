# statline/core/introspect.py
from __future__ import annotations

import ast
import re
from typing import Any, Iterable, List, Mapping, Optional, Set

from statline.core.adapters import load as _load_adapter

# Names that may appear in expressions but are not “raw input keys”
_RESERVED_EXPR_NAMES: Set[str] = {
    "min",
    "max",
    "abs",
    "sqrt",
    "log1p",
    # common boolean-ish / null-ish tokens people sometimes sneak into configs
    "true",
    "false",
    "none",
}

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _dedupe_preserve_order(items: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for s in items:
        s = (s or "").strip()
        if not s:
            continue
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _names_in_expr(expr: str) -> List[str]:
    """
    Extract identifier-like names from a strict arithmetic expression.

    We prefer AST parsing to avoid false positives, but fall back to regex
    if the expression isn't valid Python syntax.
    """
    expr = (expr or "").strip()
    if not expr:
        return []

    names: List[str] = []
    seen: Set[str] = set()

    try:
        tree = ast.parse(expr, mode="eval")
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                ident = node.id
                low = ident.lower()
                if low in _RESERVED_EXPR_NAMES:
                    continue
                if ident not in seen:
                    seen.add(ident)
                    names.append(ident)
        return names
    except Exception:
        # Fallback: regex extraction (less accurate, but better than nothing)
        for ident in _IDENT_RE.findall(expr):
            low = ident.lower()
            if low in _RESERVED_EXPR_NAMES:
                continue
            if ident not in seen:
                seen.add(ident)
                names.append(ident)
        return names


def _source_fields(src: Optional[Mapping[str, Any]]) -> List[str]:
    """
    Extract raw input field names referenced by a strict MetricSpec.source block.

    Supported source shapes (from compiler):
      - {field: "<raw_key>"}
      - {ratio: {num: "<raw_key>", den: "<raw_key>", min_den?: ...}}
      - {sum: ["<raw_key>", ...]}
      - {diff: {a: "<raw_key>", b: "<raw_key>"}}
      - {const: <number>}
      - {expr: "<expression using raw keys>"}
    """
    if not src:
        return []

    if "field" in src:
        return [str(src["field"])]

    if "ratio" in src:
        r = src.get("ratio") or {} # pyright: ignore[reportUnknownVariableType]
        if isinstance(r, Mapping):
            out: List[str] = []
            if "num" in r:
                out.append(str(r["num"])) # pyright: ignore[reportUnknownArgumentType]
            if "den" in r:
                out.append(str(r["den"])) # pyright: ignore[reportUnknownArgumentType]
            return _dedupe_preserve_order(out)
        return []

    if "sum" in src:
        keys = src.get("sum")
        if isinstance(keys, (list, tuple)):
            return _dedupe_preserve_order(str(k) for k in keys) # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        return []

    if "diff" in src:
        d = src.get("diff") or {} # pyright: ignore[reportUnknownVariableType]
        if isinstance(d, Mapping):
            out: List[str] = []
            if "a" in d:
                out.append(str(d["a"])) # pyright: ignore[reportUnknownArgumentType]
            if "b" in d:
                out.append(str(d["b"])) # pyright: ignore[reportUnknownArgumentType]
            return _dedupe_preserve_order(out)
        return []

    if "expr" in src:
        return _names_in_expr(str(src.get("expr") or ""))

    if "const" in src:
        return []

    # Unknown source shape (keep safe; don't guess and produce junk)
    return []


# ── public helpers ────────────────────────────────────────────────────────────

def declared_metric_keys(adapter_key: str) -> List[str]:
    """
    Keys the adapter *emits* as primary metrics (not efficiency channels).
    """
    try:
        adp = _load_adapter(adapter_key)
    except Exception:
        return []
    return _dedupe_preserve_order(getattr(m, "key", "") for m in getattr(adp, "metrics", []) or [])


def declared_efficiency_keys(adapter_key: str) -> Set[str]:
    """
    Keys the adapter *emits* as efficiency channels (derived after metrics).
    """
    try:
        adp = _load_adapter(adapter_key)
    except Exception:
        return set()
    keys = [getattr(e, "key", "") for e in getattr(adp, "efficiency", []) or []]
    return set(k for k in keys if isinstance(k, str) and k.strip())


def mapper_keys(adapter_key: str) -> List[str]:
    """
    Probe the adapter mapper with {} and return keys it emits (metrics + efficiency).
    """
    try:
        adp = _load_adapter(adapter_key)
    except Exception:
        return []
    mapper = getattr(adp, "map_raw_to_metrics", None) or getattr(adp, "map_raw", None)
    if not callable(mapper):
        return []
    try:
        out_any = mapper({})
    except Exception:
        return []
    if not isinstance(out_any, Mapping):
        return []
    return _dedupe_preserve_order(str(k) for k in out_any.keys()) # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]


def mapper_metric_like_keys(adapter_key: str) -> List[str]:
    """
    Probe the adapter mapper with {} and return output keys, excluding any keys
    declared as efficiency outputs (so callers can treat results as “base metrics”).
    """
    eff = declared_efficiency_keys(adapter_key)
    keys = mapper_keys(adapter_key)
    return [k for k in keys if k not in eff]


def infer_input_keys(adapter_key: str) -> List[str]:
    """
    Best-effort list of *raw input* keys the adapter likely needs.

    Priority order:
      1) raw fields referenced by MetricSpec.source blocks
      2) names referenced by efficiency expressions (excluding produced keys)
      3) adapter-provided hints (input_keys/inputs/fields/features/expected_stats)
    """
    try:
        adp = _load_adapter(adapter_key)
    except Exception:
        return []

    metrics = getattr(adp, "metrics", []) or []
    effs = getattr(adp, "efficiency", []) or []

    produced: Set[str] = set()
    for m in metrics:
        k = getattr(m, "key", None)
        if isinstance(k, str) and k.strip():
            produced.add(k)
    for e in effs:
        k = getattr(e, "key", None)
        if isinstance(k, str) and k.strip():
            produced.add(k)

    inputs: List[str] = []

    # 1) source fields
    for m in metrics:
        src = getattr(m, "source", None)
        if isinstance(src, Mapping):
            inputs.extend(_source_fields(src)) # pyright: ignore[reportUnknownArgumentType]

    # 2) efficiency expression identifiers (exclude produced keys)
    for e in effs:
        make = getattr(e, "make", "") or ""
        attempt = getattr(e, "attempt", "") or ""
        for name in _names_in_expr(str(make)) + _names_in_expr(str(attempt)):
            if name in produced:
                continue
            inputs.append(name)

    # 3) adapter hints (optional)
    for hint_attr in ("input_keys", "inputs", "fields", "features", "expected_stats", "required_headers"):
        v = getattr(adp, hint_attr, None)
        if isinstance(v, (list, tuple, set)):
            for item in v: # pyright: ignore[reportUnknownVariableType]
                if isinstance(item, str) and item.strip():
                    inputs.append(item)

    return _dedupe_preserve_order(inputs)


__all__ = [
    "declared_metric_keys",
    "declared_efficiency_keys",
    "mapper_metric_like_keys",
    "mapper_keys",
    "infer_input_keys",
]
