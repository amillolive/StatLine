# statline/slapi/app.py
from __future__ import annotations

import importlib
import re
from collections.abc import Sequence as AbcSequence
from pathlib import Path
from typing import Annotated, Any, Dict, List, Mapping, Optional, TypedDict, Union

from fastapi import FastAPI, HTTPException
from fastapi.params import Depends
from fastapi.routing import APIRouter
from pydantic import BaseModel
from starlette.requests import Request  # for typing + FastAPI

# Core (public)
from statline.core.adapters import list_names as _list_names
from statline.core.adapters import load as _load_adapter
from statline.core.adapters.loader import load_spec as _load_spec
from statline.core.scoring import calculate_pri as _calculate_pri_batch
from statline.core.scoring import calculate_pri_single as _calculate_pri_single

# admin + local-auth helpers
from statline.slapi.auth_local import (
    AuthZ,
    admin_generate_key,
    admin_list_keys,
    admin_revoke,
    admin_set_access,
    host_fp,  # ← add this
    need,
    require_regkey,
)

# SLAPI façade (public)
from statline.slapi.scoring import (
    ScoreBatchRequest,
    ScoreRowRequest,
)
from statline.slapi.scoring import (
    adapters_available as _adapters_available,
)
from statline.slapi.scoring import (
    score_batch as _score_batch,
)
from statline.slapi.scoring import (
    score_row as _score_row,
)

# ──────────────────────────────────────────────────────────────────────────────
# Types
# ──────────────────────────────────────────────────────────────────────────────

AuthDep = Annotated[AuthZ, Depends(require_regkey)]
Row = Mapping[str, Any]
Rows = List[Row]
Weights = Dict[str, float]
Context = Dict[str, Dict[str, float]]
Caps = Dict[str, float]


class ScoreRowIn(BaseModel):
    adapter: str
    row: Row
    weights_override: Optional[Union[Weights, str]] = None
    context: Optional[Context] = None
    caps_override: Optional[Caps] = None

class ScoreBatchIn(BaseModel):
    adapter: str
    rows: Rows
    weights_override: Optional[Union[Weights, str]] = None
    context: Optional[Context] = None
    caps_override: Optional[Caps] = None

class PriSingleIn(BaseModel):
    adapter: str
    row: Row
    weights_override: Optional[Union[Weights, str]] = None

class MapIn(BaseModel):
    adapter: str
    row: Row

class MapBatchIn(BaseModel):
    adapter: str
    rows: Rows

# Adapter spec fragments (lightweight typing)
class _SourceSpec(TypedDict, total=False):
    field: Union[str, int, float]

class MetricItem(TypedDict, total=False):
    key: str
    source: _SourceSpec
    bucket: str
    clamp: List[float]

class EfficiencyItem(TypedDict, total=False):
    key: str
    make: str
    attempt: str
    bucket: str
    clamp: List[float]

class _AdapterCfg(TypedDict):
    metrics: List[MetricItem]
    efficiency: List[EfficiencyItem]

# ──────────────────────────────────────────────────────────────────────────────
# Narrowers (return Any-friendly types to avoid “partially unknown”)
# ──────────────────────────────────────────────────────────────────────────────

def _as_mapping(v: object) -> Optional[Mapping[str, Any]]:
    return v if isinstance(v, Mapping) else None # pyright: ignore[reportUnknownVariableType]

def _as_seq(v: object) -> Optional[AbcSequence[Any]]:
    # Accept list/tuple (but not str/bytes)
    if isinstance(v, (str, bytes)):
        return None
    if isinstance(v, AbcSequence):
        return v # pyright: ignore[reportUnknownVariableType]
    return None

def _as_str(v: object) -> Optional[str]:
    return v if isinstance(v, str) else None

def _as_numlike(v: object) -> Optional[Union[int, float, str]]:
    return v if isinstance(v, (int, float, str)) else None

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _normalize_weights(adapter_key: str, w: object) -> Optional[Dict[str, float]]:
    if w is None:
        return None
    if isinstance(w, Mapping):
        # user provided explicit overrides
        return {str(k): float(v) for k, v in w.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType] # tolerate number-like
    if isinstance(w, str):
        try:
            spec = _load_spec(adapter_key)
        except Exception:
            return None
        preset = spec.weights.get(w.lower())
        if preset:
            # copy to avoid sharing the dataclass’ internal dict
            return {str(k): float(v) for k, v in preset.items()}
        return None
    return None

# ── Input key inference (for interactive prompting)

_ID_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_FUN_BLACKLIST: set[str] = {"min", "max", "let", "abs", "round"}

def _tokenize(expr: str) -> List[str]:
    return [t for t in _ID_RE.findall(expr) if t not in _FUN_BLACKLIST]

def _as_metric_list(val: object) -> List[MetricItem]:
    out: List[MetricItem] = []
    seq = _as_seq(val) or [] # pyright: ignore[reportUnknownVariableType]
    for it in seq: # pyright: ignore[reportUnknownVariableType]
        m = _as_mapping(it) # pyright: ignore[reportUnknownArgumentType]
        if m is None:
            continue
        d: MetricItem = {}
        k = _as_str(m.get("key"))
        if k is not None:
            d["key"] = k

        src_map = _as_mapping(m.get("source"))
        if src_map is not None:
            # Be flexible about how adapters name the field
            for fld_key in ("field", "id", "name", "stat", "col", "column", "key"):
                fld = _as_numlike(src_map.get(fld_key))
                if fld is not None:
                    d["source"] = {"field": fld}
                    break

        out.append(d)
    return out

def _as_eff_list(val: object) -> List[EfficiencyItem]:
    out: List[EfficiencyItem] = []
    seq = _as_seq(val) or [] # pyright: ignore[reportUnknownVariableType]
    for it in seq: # pyright: ignore[reportUnknownVariableType]
        m = _as_mapping(it) # pyright: ignore[reportUnknownArgumentType]
        if m is None:
            continue
        d: EfficiencyItem = {}
        k = _as_str(m.get("key"))
        if k is not None:
            d["key"] = k
        mk = _as_str(m.get("make"))
        if mk is not None:
            d["make"] = mk
        at = _as_str(m.get("attempt"))
        if at is not None:
            d["attempt"] = at
        out.append(d)
    return out

def _adapter_cfg(adp: object) -> _AdapterCfg:
    cfg: _AdapterCfg = {"metrics": [], "efficiency": []}
    cfg["metrics"] = _as_metric_list(getattr(adp, "metrics", None))
    cfg["efficiency"] = _as_eff_list(getattr(adp, "efficiency", None))

    for holder in ("config", "spec"):
        cont = _as_mapping(getattr(adp, holder, None))
        if cont is None:
            continue
        m2 = _as_metric_list(cont.get("metrics"))
        e2 = _as_eff_list(cont.get("efficiency"))
        if m2:
            cfg["metrics"] = m2
        if e2:
            cfg["efficiency"] = e2
    return cfg

def _infer_input_keys(adapter_key: str) -> List[str]:
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
        src = m.get("source")
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
        k = m.get("key")
        if k:
            produced.add(k)
    for e in efficiency:
        k = e.get("key")
        if k:
            produced.add(k)

    refs: List[str] = []
    for e in efficiency:
        mk = e.get("make")
        if mk:
            refs += _tokenize(mk)
        at = e.get("attempt")
        if at:
            refs += _tokenize(at)

    for t in refs:
        if t and t not in produced:
            inputs.append(t)

    # 3) adapter-exposed fallback
    if not inputs:
        for cand in ("input_keys", "inputs", "fields", "features", "expected_stats"):
            raw = getattr(adp, cand, None)
            seq = _as_seq(raw) # pyright: ignore[reportUnknownVariableType]
            if not seq:
                continue
            picked: List[str] = []
            for x in seq: # pyright: ignore[reportUnknownVariableType]
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

# Mapping helpers

def _coerce_float(v: Any) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return 0.0
    return 0.0

def _get_mapper(adp: object) -> Any:
    fn = getattr(adp, "map_raw_to_metrics", None)
    if callable(fn):
        return fn
    fn = getattr(adp, "map_raw", None)
    if callable(fn):
        return fn
    raise ValueError(f"Adapter '{getattr(adp, 'KEY', adp)}' lacks map_raw/map_raw_to_metrics")

def _map_row(adapter_key: str, row: Mapping[str, Any]) -> Dict[str, float]:
    adp = _load_adapter(adapter_key)
    mapper = _get_mapper(adp)
    out_any = _as_mapping(mapper(row)) or {}
    safe: Dict[str, float] = {}
    for k, v in out_any.items():
        safe[str(k)] = _coerce_float(v)
    sanity = getattr(adp, "sanity", None)
    if callable(sanity):
        try:
            sanity(safe)
        except Exception:
            pass
    return safe

# Cache helpers

def _cache_rows(guild_id: str) -> List[Dict[str, Any]]:
    try:
        mod = importlib.import_module("statline.core.cache")
        fn = getattr(mod, "get_mapped_rows_for_scoring", None)
        if not callable(fn):
            return []
        rows_obj: object = fn(guild_id)
        out: List[Dict[str, Any]] = []
        if isinstance(rows_obj, (list, tuple)):
            for r in rows_obj: # pyright: ignore[reportUnknownVariableType]
                m = _as_mapping(r) # pyright: ignore[reportUnknownArgumentType]
                if m:
                    out.append({str(k): m[k] for k in m})
        elif isinstance(rows_obj, Mapping):
            out.append({str(k): rows_obj[k] for k in rows_obj}) # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        return out
    except Exception:
        return []

def _cache_context(guild_id: str) -> Optional[Context]:
    try:
        mod = importlib.import_module("statline.core.cache")
        fn = getattr(mod, "get_metric_context_ap", None)
        if not callable(fn):
            return None
        ctx = _as_mapping(fn(guild_id))
        if ctx is None:
            return None

        safe: Context = {}
        for ok, inner_obj in ctx.items():
            inner_map = _as_mapping(inner_obj)
            if inner_map is None:
                continue
            inner: Dict[str, float] = {}
            for mk, mv in inner_map.items():
                try:
                    inner[str(mk)] = float(mv) if mv is not None else 0.0
                except Exception:
                    inner[str(mk)] = 0.0
            safe[str(ok)] = inner
        return safe
    except Exception:
        return None

def _force_refresh(guild_id: str) -> bool:
    try:
        mod = importlib.import_module("statline.core.refresh")
        fn = getattr(mod, "sync_guild_if_stale", None)
        if callable(fn):
            fn(guild_id, force=True)
            return True
    except Exception:
        pass
    return False

# Convenience: convert List[Mapping] → List[Dict] for core.calculate_pri
def _rows_to_dicts(rows: Rows) -> List[Dict[str, Any]]:
    return [dict(r) for r in rows]

def _mapper_keys(adapter_key: str) -> List[str]:
    """Return metric keys produced by the adapter's mapper by probing with {}."""
    try:
        adp = _load_adapter(adapter_key)
        mapper = _get_mapper(adp)  # you already have _get_mapper
    except Exception:
        return []
    try:
        out_any = mapper({})
    except Exception:
        return []
    if not isinstance(out_any, Mapping):
        return []
    keys: List[str] = []
    seen: set[str] = set()
    for k in out_any.keys(): # pyright: ignore[reportUnknownVariableType]
        s = str(k) # pyright: ignore[reportUnknownArgumentType]
        if s and s not in seen:
            seen.add(s)
            keys.append(s)
    return keys

# ──────────────────────────────────────────────────────────────────────────────
# App
# ──────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="StatLine API", version="2.0.0", docs_url="/docs", redoc_url="/redoc")

admin: APIRouter = APIRouter(prefix="/v2/admin", tags=["admin"])

def _require_local_admin(req: Request) -> None:
    client = req.client.host if req.client else ""
    if client not in {"127.0.0.1", "::1"}:
        raise HTTPException(403, "local admin only")
    want: str = req.headers.get("X-Host-FP", "") or ""
    have: str = host_fp()
    if want != have:
        raise HTTPException(403, "bad host fingerprint")

@admin.post("/generate-key") # pyright: ignore[reportUnknownMemberType]
def generate_key(
    req: Request,
    owner: str,
    scopes: Optional[List[str]] = None,
    ttl_days: Optional[int] = None,
) -> Dict[str, Any]:
    _require_local_admin(req)
    token, rec = admin_generate_key(owner, scopes, ttl_days)
    return {"token": token, "prefix": rec.prefix, "owner": rec.owner, "scopes": rec.scopes}

@admin.get("/keys") # pyright: ignore[reportUnknownMemberType]
def keys(req: Request) -> Dict[str, Any]:
    _require_local_admin(req)
    return {"keys": admin_list_keys()}

@admin.post("/keys/{prefix}/access") # pyright: ignore[reportUnknownMemberType]
def set_access(req: Request, prefix: str, value: bool) -> Dict[str, bool]:
    _require_local_admin(req)
    return {"ok": admin_set_access(prefix, value)}

@admin.delete("/keys/{prefix}") # pyright: ignore[reportUnknownMemberType]
def revoke(req: Request, prefix: str) -> Dict[str, bool]:
    _require_local_admin(req)
    return {"ok": admin_revoke(prefix)}

app.include_router(admin)

# Adapters

@app.get("/v2/adapters")
def list_adapters(auth: AuthDep) -> Dict[str, List[str]]:
    need("score", auth)
    names = _adapters_available() or list(_list_names())
    return {"adapters": names}

WeightMap = Dict[str, float]            # {bucket: weight}
WeightPresets = Dict[str, WeightMap]    # {preset: {bucket: weight}}

@app.get("/v2/adapter/{adapter}/weights")
def adapter_weights(adapter: str) -> Dict[str, WeightPresets]:
    spec = _load_spec(adapter)
    # spec.weights is already {preset: {bucket: weight}}
    # Copy if you want to avoid sharing:
    out: WeightPresets = {p: dict(bw) for p, bw in spec.weights.items()}
    return {"weights": out}

@app.get("/v2/adapter/{adapter}/inputs")
def adapter_inputs(adapter: str) -> Dict[str, List[str]]:
    return {"inputs": _infer_input_keys(adapter)}

@app.get("/v2/adapter/{adapter}/metric-keys")
def adapter_metric_keys(adapter: str) -> Dict[str, List[str]]:
    spec = _load_spec(adapter)
    keys = [m.key for m in spec.metrics]
    seen: set[str] = set()
    out: List[str] = []
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return {"keys": out}

@app.get("/v2/adapter/{adapter}/metric-keys/probe")
def adapter_metric_keys_probe(adapter: str) -> Dict[str, List[str]]:
    return {"keys": _mapper_keys(adapter)}

@app.get("/v2/adapter/{adapter}/weight-presets")
def adapter_weight_presets(adapter: str) -> Dict[str, List[str]]:
    spec = _load_spec(adapter)
    return {"presets": sorted(spec.weights.keys())}

@app.get("/v2/adapter/{adapter}/prompt-keys")
def adapter_prompt_keys(adapter: str) -> Dict[str, List[str]]:
    # 1) Prefer declared metric keys (strict)
    keys = _declared_metric_keys(adapter)
    # 2) Else probe the mapper and strip efficiency outputs
    if not keys:
        keys = _mapper_metric_like_keys(adapter)
    # 3) Else last resort: inferred inputs (may be empty)
    if not keys:
        keys = _infer_input_keys(adapter)
    return {"keys": keys}

def _declared_metric_keys(adapter_key: str) -> List[str]:
    try:
        adp: object = _load_adapter(adapter_key)
    except Exception:
        return []
    cfg = _adapter_cfg(adp)  # you already have this
    out: List[str] = []
    seen: set[str] = set()
    for m in cfg.get("metrics", []):
        k = m.get("key")
        if isinstance(k, str) and k and k not in seen:
            seen.add(k)
            out.append(k)
    return out

def _declared_efficiency_keys(adapter_key: str) -> set[str]:
    try:
        adp: object = _load_adapter(adapter_key)
    except Exception:
        return set()
    cfg = _adapter_cfg(adp)
    eff: set[str] = set()
    for e in cfg.get("efficiency", []):
        k = e.get("key")
        if isinstance(k, str) and k:
            eff.add(k)
    return eff

def _mapper_metric_like_keys(adapter_key: str) -> List[str]:
    """
    Probe the adapter mapper with {} and return keys,
    filtering out any keys that the adapter declares as efficiency.
    """
    try:
        adp: object = _load_adapter(adapter_key)
        mapper = _get_mapper(adp)
    except Exception:
        return []
    try:
        out_any = mapper({})
    except Exception:
        return []
    if not isinstance(out_any, Mapping):
        return []
    eff = _declared_efficiency_keys(adapter_key)
    seen: set[str] = set()
    keys: List[str] = []
    for k in out_any.keys(): # pyright: ignore[reportUnknownVariableType]
        s = str(k) # pyright: ignore[reportUnknownArgumentType]
        if not s or s in eff or s in seen:
            continue
        seen.add(s)
        keys.append(s)
    return keys

# Mapping only

@app.post("/v2/map/row")
def map_row(body: MapIn) -> Dict[str, float]:
    return _map_row(body.adapter, body.row)

@app.post("/v2/map/batch")
def map_batch(body: MapBatchIn) -> List[Dict[str, float]]:
    return [_map_row(body.adapter, r) for r in body.rows]

# Scoring (compat with earlier façade)

@app.post("/v2/score/row")
def score_row(body: ScoreRowIn, auth: AuthDep) -> Dict[str, Any]:
    need("score", auth)
    req = ScoreRowRequest(
        adapter=body.adapter,
        row=body.row,
        weights_override=_normalize_weights(body.adapter, body.weights_override),
        context=body.context,
        caps_override=body.caps_override,
    )
    return _score_row(req, timing=None)

@app.post("/v2/score/batch")
def score_batch(body: ScoreBatchIn) -> List[Dict[str, Any]]:
    req = ScoreBatchRequest(
        adapter=body.adapter,
        rows=body.rows,
        weights_override=_normalize_weights(body.adapter, body.weights_override),
        context=body.context,
        caps_override=body.caps_override,
    )
    return _score_batch(req, timing=None)

@app.post("/v2/calc/pri")
def calc_pri_single(body: PriSingleIn) -> Dict[str, Any]:
    adp: object = _load_adapter(body.adapter)
    return _calculate_pri_single(
        body.row,
        adp,
        weights_override=_normalize_weights(body.adapter, body.weights_override),
        context=None,
        caps_override=None,
    )

# Batch PRI (mimic CLI “batch|clamps”)

class PriBatchIn(BaseModel):
    adapter: str
    rows: Rows
    weights_override: Optional[Union[Weights, str]] = None
    context: Optional[Context] = None
    caps_mode: str = "batch"  # "batch" | "clamps"

@app.post("/v2/calc/pri/batch")
def calc_pri_batch(body: PriBatchIn) -> List[Dict[str, Any]]:
    adp = _load_adapter(body.adapter)
    w = _normalize_weights(body.adapter, body.weights_override)

    if (body.caps_mode or "batch").lower() == "clamps":
        out: List[Dict[str, Any]] = []
        for r in body.rows:
            out.append(
                _calculate_pri_single(
                    r, adp,
                    weights_override=w,
                    context=None,
                    caps_override=None,
                )
            )
        return out

    return _calculate_pri_batch(
        _rows_to_dicts(body.rows),
        adapter=adp,
        weights_override=w,
        context=body.context,
        caps_override=None,
    )

# Context/export/refresh

@app.get("/v2/context/{guild_id}")
def get_context(guild_id: str) -> Dict[str, Any]:
    ctx = _cache_context(guild_id)
    return {"guild_id": guild_id, "context": ctx or {}}

@app.get("/v2/export/{guild_id}")
def export_rows(guild_id: str) -> Dict[str, Any]:
    rows = _cache_rows(guild_id)
    return {"guild_id": guild_id, "rows": rows}

class RefreshIn(BaseModel):
    guild_id: str

@app.post("/v2/refresh")
def refresh(in_: RefreshIn) -> Dict[str, Any]:
    ok = _force_refresh(in_.guild_id)
    return {"guild_id": in_.guild_id, "refreshed": bool(ok)}

# ── Dataset listing (CSV under statline/data/stats) ───────────────────────────

def _datasets_dir() -> Path:
    # app.py lives at statline/slapi/app.py; go up to statline/, then data/stats
    return Path(__file__).resolve().parent.parent / "data" / "stats"

def _list_datasets() -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    d = _datasets_dir()
    try:
        if d.exists():
            for p in sorted(d.glob("*.csv")):
                out.append({
                    "name": p.name,
                    "path": str(p),
                })
    except Exception:
        pass
    return out

@app.get("/v2/datasets")
def list_datasets() -> Dict[str, List[Dict[str, str]]]:
    """
    Return CSV datasets available on the server under statline/data/stats.
    """
    return {"datasets": _list_datasets()}