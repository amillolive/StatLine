# statline/core/introspect.py
from __future__ import annotations

import re
from collections.abc import Mapping as AbcMapping
from collections.abc import Sequence as AbcSequence
from typing import Any, Dict, List, Mapping, Optional, Union, cast

from statline.core.adapters import load as _load_adapter


# ── small narrowers (avoid partially-unknown types)
def _as_mapping(v: object) -> Optional[Mapping[str, Any]]:
    return cast(Optional[Mapping[str, Any]], v) if isinstance(v, AbcMapping) else None

def _as_seq(v: object) -> Optional[AbcSequence[Any]]:
    if isinstance(v, (str, bytes)):
        return None
    return cast(Optional[AbcSequence[Any]], v) if isinstance(v, AbcSequence) else None

def _as_str(v: object) -> Optional[str]:
    return v if isinstance(v, str) else None

def _as_numlike(v: object) -> Optional[Union[int, float, str]]:
    return v if isinstance(v, (int, float, str)) else None

# ── tokenize simple identifiers used in efficiency formulas
_ID_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_FUN_BLACKLIST: set[str] = {"min", "max", "let", "abs", "round"}

def _tokenize(expr: str) -> List[str]:
    return [t for t in _ID_RE.findall(expr or "") if t not in _FUN_BLACKLIST]

# ── read a minimal adapter “config view” (metrics/efficiency) no matter how it’s exposed
def _adapter_cfg(adp: object) -> Dict[str, List[Dict[str, Any]]]:
    def _metric_list(val: object) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seq = _as_seq(val) or []
        for it in seq:
            m = _as_mapping(it)
            if not m:
                continue
            d: Dict[str, Any] = {}
            k = _as_str(m.get("key"))
            if k:
                d["key"] = k

            src_map = _as_mapping(m.get("source"))
            if src_map:
                for fld_key in ("field", "id", "name", "stat", "col", "column", "key"):
                    fld = _as_numlike(src_map.get(fld_key))
                    if fld is not None:
                        d["source"] = {"field": fld}
                        break
            out.append(d)
        return out

    def _eff_list(val: object) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seq = _as_seq(val) or []
        for it in seq:
            m = _as_mapping(it)
            if not m:
                continue
            d: Dict[str, Any] = {}
            for k in ("key", "make", "attempt"):
                s = _as_str(m.get(k))
                if s:
                    d[k] = s
            out.append(d)
        return out

    cfg: Dict[str, List[Dict[str, Any]]] = {"metrics": [], "efficiency": []}
    cfg["metrics"] = _metric_list(getattr(adp, "metrics", None))
    cfg["efficiency"] = _eff_list(getattr(adp, "efficiency", None))

    # Some adapters expose a container (config/spec) — prefer those if present.
    for holder in ("config", "spec"):
        cont = _as_mapping(getattr(adp, holder, None))
        if not cont:
            continue
        m2 = _metric_list(cont.get("metrics"))
        e2 = _eff_list(cont.get("efficiency"))
        if m2:
            cfg["metrics"] = m2
        if e2:
            cfg["efficiency"] = e2
    return cfg

# ── public helpers

def declared_metric_keys(adapter_key: str) -> List[str]:
    try:
        adp: object = _load_adapter(adapter_key)
    except Exception:
        return []
    cfg = _adapter_cfg(adp)
    out: List[str] = []
    seen: set[str] = set()
    for m in cfg.get("metrics", []):
        k = _as_str(m.get("key"))
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out

def declared_efficiency_keys(adapter_key: str) -> set[str]:
    try:
        adp: object = _load_adapter(adapter_key)
    except Exception:
        return set()
    cfg = _adapter_cfg(adp)
    eff: set[str] = set()
    for e in cfg.get("efficiency", []):
        k = _as_str(e.get("key"))
        if k:
            eff.add(k)
    return eff

def mapper_metric_like_keys(adapter_key: str) -> List[str]:
    """
    Probe the adapter mapper with {} and return keys, filtering out any keys
    that the adapter declares as efficiency outputs.
    """
    try:
        adp: object = _load_adapter(adapter_key)
    except Exception:
        return []
    mapper = getattr(adp, "map_raw_to_metrics", None) or getattr(adp, "map_raw", None)
    if not callable(mapper):
        return []
    try:
        out_any = mapper({})
    except Exception:
        return []
    if not isinstance(out_any, AbcMapping):
        return []
    eff = declared_efficiency_keys(adapter_key)
    keys: List[str] = []
    seen: set[str] = set()
    for k in out_any.keys(): # pyright: ignore[reportUnknownVariableType]
        s = str(k) # pyright: ignore[reportUnknownArgumentType]
        if s and s not in eff and s not in seen:
            seen.add(s)
            keys.append(s)
    return keys

def mapper_keys(adapter_key: str) -> List[str]:
    """Return keys produced by the mapper by probing with {} (no filtering)."""
    try:
        adp: object = _load_adapter(adapter_key)
    except Exception:
        return []
    mapper = getattr(adp, "map_raw_to_metrics", None) or getattr(adp, "map_raw", None)
    if not callable(mapper):
        return []
    try:
        out_any = mapper({})
    except Exception:
        return []
    if not isinstance(out_any, AbcMapping):
        return []
    seen: set[str] = set()
    keys: List[str] = []
    for k in out_any.keys(): # pyright: ignore[reportUnknownVariableType]
        s = str(k) # pyright: ignore[reportUnknownArgumentType]
        if s and s not in seen:
            seen.add(s)
            keys.append(s)
    return keys

def infer_input_keys(adapter_key: str) -> List[str]:
    """
    Build a best-effort list of input keys the adapter likely needs:
      1) metric source fields
      2) identifiers referenced by efficiency expressions (that aren’t produced keys)
      3) adapter-provided hints (input_keys/inputs/fields/features/expected_stats)
    """
    try:
        adp: object = _load_adapter(adapter_key)
    except Exception:
        return []

    cfg = _adapter_cfg(adp)
    metrics = cfg["metrics"]
    efficiency = cfg["efficiency"]

    inputs: List[str] = []

    # 1) explicit pulls from metrics[*].source.field
    for m in metrics:
        src = _as_mapping(m.get("source"))
        if not src:
            continue
        fld = src.get("field")
        if fld is None:
            continue
        s = str(fld).strip()
        if s:
            inputs.append(s)

    # 2) identifiers referenced in efficiency formulas but not produced keys
    produced: set[str] = set()
    for m in metrics:
        k = _as_str(m.get("key"))
        if k:
            produced.add(k)
    for e in efficiency:
        k = _as_str(e.get("key"))
        if k:
            produced.add(k)

    refs: List[str] = []
    for e in efficiency:
        mk = _as_str(e.get("make"))
        if mk:
            refs += _tokenize(mk)
        at = _as_str(e.get("attempt"))
        if at:
            refs += _tokenize(at)

    for t in refs:
        if t and t not in produced:
            inputs.append(t)

    # 3) adapter-exposed fallback
    if not inputs:
        for cand in ("input_keys", "inputs", "fields", "features", "expected_stats"):
            raw = getattr(adp, cand, None)
            seq = _as_seq(raw)
            if not seq:
                continue
            picked: List[str] = []
            for x in seq:
                if isinstance(x, (str, int, float)):
                    picked.append(str(x))
            if picked:
                inputs = picked
                break

    # dedupe/preserve order
    seen: set[str] = set()
    out_keys: List[str] = []
    for k in inputs:
        if k and k not in seen:
            seen.add(k)
            out_keys.append(k)
    return out_keys

__all__ = [
    "declared_metric_keys",
    "declared_efficiency_keys",
    "mapper_metric_like_keys",
    "mapper_keys",
    "infer_input_keys",
]
