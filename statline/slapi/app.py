# statline/slapi/app.py
from __future__ import annotations

from typing import Annotated, Any, Dict, List, Mapping, Optional

from fastapi import FastAPI, HTTPException
from fastapi.params import Depends
from fastapi.routing import APIRouter
from starlette.requests import Request

from statline.core.adapters import list_names as _list_names
from statline.core.adapters import load as _load_adapter

# Core bits we expose thinly
from statline.core.adapters.loader import load_spec as _load_spec
from statline.core.introspect import (
    declared_metric_keys as _declared_metric_keys,
)
from statline.core.introspect import (
    infer_input_keys as _infer_input_keys,
)
from statline.core.introspect import (
    mapper_keys as _mapper_keys,
)
from statline.core.introspect import (
    mapper_metric_like_keys as _mapper_metric_like_keys,
)
from statline.core.scoring import (
    calculate_pri as _calculate_pri_batch,
)
from statline.core.scoring import (
    calculate_pri_single as _calculate_pri_single,
)
from statline.slapi.adapters import list_discoverable_yaml as _list_yaml

# Auth/admin (combined module)
from statline.slapi.auth import (
    Principal,
    admin_generate_key,
    admin_list_keys,
    admin_revoke,
    admin_set_access,
    host_fp,
    need,
)

# Request models (you said these live in schemas now)
from statline.slapi.schemas import (
    MapBatchIn,
    MapRowIn,
    PriBatchIn,
    PriSingleIn,
    ScoreBatchIn,
    ScoreRowIn,
)
from statline.slapi.scoring import (
    ScoreBatchRequest,
    ScoreRowRequest,
)

# Scoring façade (thin call/response)
from statline.slapi.scoring import (
    adapters_available as _adapters_available,
)
from statline.slapi.scoring import (
    score_batch as _score_batch,
)
from statline.slapi.scoring import (
    score_row as _score_row,
)

from .dep import require_score

app: FastAPI = FastAPI(
    title="StatLine API",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

AuthDep = Annotated[Principal, Depends(require_score)]

# ──────────────────────────────────────────────────────────────────────────────
# Admin (local only) — use a router so stubs know .delete exists
# ──────────────────────────────────────────────────────────────────────────────

admin_router = APIRouter(prefix="/v2/admin", tags=["admin"])

def _require_local_admin(req: Request) -> None:
    client = req.client.host if req.client else ""
    if client not in {"127.0.0.1", "::1"}:
        raise HTTPException(403, "local admin only")
    if (req.headers.get("X-Host-FP") or "") != host_fp():
        raise HTTPException(403, "bad host fingerprint")

@admin_router.post("/generate-key") # pyright: ignore[reportUnknownMemberType]
def generate_key(
    req: Request,
    owner: str,
    scopes: Optional[List[str]] = None,
    ttl_days: Optional[int] = None,
) -> Dict[str, Any]:
    _require_local_admin(req)
    token, rec = admin_generate_key(owner, scopes, ttl_days)
    return {"token": token, "prefix": rec.prefix, "owner": rec.owner, "scopes": rec.scopes}

@admin_router.get("/keys") # pyright: ignore[reportUnknownMemberType]
def keys(req: Request) -> Dict[str, Any]:
    _require_local_admin(req)
    return {"keys": admin_list_keys()}

@admin_router.post("/keys/{prefix}/access") # pyright: ignore[reportUnknownMemberType]
def set_access(req: Request, prefix: str, value: bool) -> Dict[str, bool]:
    _require_local_admin(req)
    return {"ok": admin_set_access(prefix, value)}

@admin_router.delete("/keys/{prefix}") # pyright: ignore[reportUnknownMemberType]
def revoke(req: Request, prefix: str) -> Dict[str, bool]:
    _require_local_admin(req)
    return {"ok": admin_revoke(prefix)}

app.include_router(admin_router)


# ──────────────────────────────────────────────────────────────────────────────
# Adapters / metadata (thin)
# ──────────────────────────────────────────────────────────────────────────────

# --- DEBUG: list packaged core adapter YAML stems (no compile) ---
@app.get("/debug/core-adapters")
def debug_core_adapters() -> Dict[str, Any]:
    from pathlib import Path
    base = Path(__file__).resolve().parents[1] / "core" / "adapters" / "defs"
    names: List[str] = []
    try:
        for p in sorted(base.glob("*.y*ml")):
            names.append(p.stem)
        return {"defs_dir": str(base), "adapters": names}
    except Exception as e:
        return {"defs_dir": str(base), "error": str(e)}

# --- DEBUG: call registry list_names() directly to see if it hangs/errs ---
@app.get("/debug/registry-list")
def debug_registry_list() -> Dict[str, Any]:
    from statline.core.adapters import list_names
    try:
        return {"adapters": list_names()}
    except Exception as e:
        return {"error": str(e)}

@app.get("/debug/auth-check")
def auth_check(auth: Annotated[object, Depends(require_score)]) -> Dict[str, bool]:
    return {"ok": True}

@app.get("/debug/adapters-noguard")
def adapters_noguard() -> Dict[str, Any]:
    from statline.core.adapters import list_names as _list_names
    try:
        return {"adapters": list(_list_names())}
    except Exception as e:
        return {"error": str(e)}

@app.get("/v2/adapters")
def list_adapters(auth: AuthDep, fast: bool = False) -> Dict[str, List[str]]:
    need("score", auth)

    # Fast path: just list adapter YAML stems; no compile, no hooks.
    if fast:
        return {"adapters": _list_yaml()}

    # Normal path: try compiled names; if it raises or stalls, fall back.
    try:
        names = _adapters_available() or list(_list_names())
        if names:
            return {"adapters": names}
    except Exception:
        pass

    # Fallback: filenames only (avoids compilation)
    return {"adapters": _list_yaml()}

@app.get("/v2/adapter/{adapter}/weights")
def adapter_weights(adapter: str, auth: AuthDep) -> Dict[str, Dict[str, Dict[str, float]]]:
    need("score", auth)
    spec = _load_spec(adapter)
    out: Dict[str, Dict[str, float]] = {}
    for preset, bw in spec.weights.items():
        inner: Dict[str, float] = {str(k): float(v) for k, v in bw.items()}
        out[str(preset)] = inner
    return {"weights": out}

@app.get("/v2/adapter/{adapter}/metric-keys")
def adapter_metric_keys(adapter: str, auth: AuthDep) -> Dict[str, List[str]]:
    need("score", auth)
    spec = _load_spec(adapter)
    seen: set[str] = set()
    keys: List[str] = []
    for m in spec.metrics:
        if m.key and m.key not in seen:
            seen.add(m.key)
            keys.append(m.key)
    return {"keys": keys}

@app.get("/v2/adapter/{adapter}/metric-keys/probe")
def adapter_metric_keys_probe(adapter: str, auth: AuthDep) -> Dict[str, List[str]]:
    """
    Probe the adapter's mapper with {} and return keys it emits.
    """
    need("score", auth)
    return {"keys": _mapper_keys(adapter)}

@app.get("/v2/adapter/{adapter}/inputs")
def adapter_inputs(adapter: str, auth: AuthDep) -> Dict[str, List[str]]:
    """
    Best-effort list of raw input keys the adapter likely requires.
    """
    need("score", auth)
    return {"inputs": _infer_input_keys(adapter)}

@app.get("/v2/adapter/{adapter}/prompt-keys")
def adapter_prompt_keys(adapter: str, auth: AuthDep) -> Dict[str, List[str]]:
    """
    Keys suitable for prompting a user (prefers declared metrics; falls back
    to mapper outputs (excluding efficiency) and finally inferred inputs).
    """
    need("score", auth)
    keys = (
        _declared_metric_keys(adapter)
        or _mapper_metric_like_keys(adapter)
        or _infer_input_keys(adapter)
    )
    return {"keys": keys}

# ──────────────────────────────────────────────────────────────────────────────
# Mapping (thin wrappers)
# ──────────────────────────────────────────────────────────────────────────────

def _map_row(adapter_key: str, row: Mapping[str, Any]) -> Dict[str, float]:
    adp = _load_adapter(adapter_key)
    fn = getattr(adp, "map_raw_to_metrics", None) or getattr(adp, "map_raw", None)
    if not callable(fn):
        raise HTTPException(400, "adapter lacks map_raw/map_raw_to_metrics")

    out_any = fn(row)
    if not isinstance(out_any, Mapping):
        raise HTTPException(400, "mapper must return Mapping[str, Any]")

    safe: Dict[str, float] = {}
    for k_raw, v_raw in out_any.items(): # pyright: ignore[reportUnknownVariableType]
        ks = str(k_raw) # pyright: ignore[reportUnknownArgumentType]
        try:
            safe[ks] = float(v_raw) if v_raw is not None else 0.0 # pyright: ignore[reportUnknownArgumentType]
        except Exception:
            safe[ks] = 0.0

    sanity = getattr(adp, "sanity", None)
    if callable(sanity):
        try:
            sanity(safe)
        except Exception:
            pass
    return safe

@app.post("/v2/map/row")
def map_row(body: MapRowIn, auth: AuthDep) -> Dict[str, float]:
    need("score", auth)
    return _map_row(body.adapter, body.row)

@app.post("/v2/map/batch")
def map_batch(body: MapBatchIn, auth: AuthDep) -> List[Dict[str, float]]:
    need("score", auth)
    return [_map_row(body.adapter, r) for r in body.rows]


# ──────────────────────────────────────────────────────────────────────────────
# Scoring via façade (pure call/response)
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/v2/score/row")
def score_row(body: ScoreRowIn, auth: AuthDep) -> Dict[str, Any]:
    need("score", auth)
    wo = body.weights_override if isinstance(body.weights_override, dict) else None
    req = ScoreRowRequest(
        adapter=body.adapter,
        row=body.row,
        weights_override=wo,
        context=body.context,
        caps_override=body.caps_override,
    )
    return _score_row(req, timing=None)

@app.post("/v2/score/batch")
def score_batch(body: ScoreBatchIn, auth: AuthDep) -> List[Dict[str, Any]]:
    need("score", auth)
    wo = body.weights_override if isinstance(body.weights_override, dict) else None
    req = ScoreBatchRequest(
        adapter=body.adapter,
        rows=list(body.rows),   # Sequence -> List for strict typing
        weights_override=wo,
        context=body.context,
        caps_override=body.caps_override,
    )
    return _score_batch(req, timing=None)


# ──────────────────────────────────────────────────────────────────────────────
# Calc passthroughs (optional convenience)
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/v2/calc/pri")
def calc_pri_single(body: PriSingleIn, auth: AuthDep) -> Dict[str, Any]:
    need("score", auth)
    adp = _load_adapter(body.adapter)
    wo = body.weights_override if isinstance(body.weights_override, dict) else None
    return _calculate_pri_single(body.row, adp, weights_override=wo, context=None, caps_override=None)

@app.post("/v2/calc/pri/batch")
def calc_pri_batch(body: PriBatchIn, auth: AuthDep) -> List[Dict[str, Any]]:
    need("score", auth)
    adp = _load_adapter(body.adapter)
    wo = body.weights_override if isinstance(body.weights_override, dict) else None

    if (body.caps_mode or "batch").lower() == "clamps":
        return [
            _calculate_pri_single(r, adp, weights_override=wo, context=None, caps_override=None)
            for r in body.rows
        ]

    return _calculate_pri_batch(
        [dict(r) for r in body.rows],
        adapter=adp,
        weights_override=wo,
        context=body.context,
        caps_override=None,
    )
