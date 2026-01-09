# statline/slapi/app.py
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Callable, Dict, List, Mapping, Optional, Sequence, TypeAlias

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.params import Depends
    from fastapi.routing import APIRouter
    from pydantic import BaseModel, ConfigDict
except ModuleNotFoundError as e:
    raise RuntimeError(
        "StatLine SLAPI requires optional dependencies. Install with: "
        "pip install 'statline[api]'  (or)  pip install -e '.[api]'"
    ) from e

from statline.core.adapters import list_names as _list_names
from statline.core.adapters import load as _load_adapter
from statline.core.adapters.loader import load_spec as _load_spec
from statline.core.adapters.sniff import sniff_adapters as _sniff_adapters
from statline.core.calculator import safe_map_raw as _safe_map_raw
from statline.core.introspect import declared_metric_keys as _declared_metric_keys
from statline.core.introspect import infer_input_keys as _infer_input_keys
from statline.core.introspect import mapper_keys as _mapper_keys
from statline.core.introspect import mapper_metric_like_keys as _mapper_metric_like_keys

# IMPORTANT: calc endpoints below expect MAPPED metrics
from statline.core.scoring import calculate_pri as _calculate_pri_mapped_batch
from statline.core.scoring import calculate_pri_single_mapped as _calculate_pri_mapped_single
from statline.slapi.adapters import list_discoverable_yaml as _list_yaml

# Auth (device-proof + api key; plus enrollment/admin) — PATH 2 (request/approve/claim)
from statline.slapi.auth import (
    Principal,
    admin_approve_apikey_request,
    admin_approve_enrollment,
    admin_deny_apikey_request,
    admin_deny_enrollment,
    admin_generate_devkey_files,
    admin_list_apikey_requests,
    admin_list_apikeys,
    admin_list_audit,
    admin_list_enrollments,
    admin_mint_regtoken,
    admin_revoke_apikey,
    admin_revoke_device,
    admin_set_apikey_access,
    claim_apikey_request,
    create_apikey_request,
    create_enrollment_request,
    devkey_fingerprint,
    get_enrollment_request,
    inspect_regtoken,
    list_apikey_requests_for_device,
    list_apikeys_for_device,
    need,
    require_device,
    require_principal,
    revoke_apikey_for_device,
)

# Request models
from statline.slapi.schemas import (
    MapBatchIn,
    MapRowIn,
    PriBatchIn,
    PriSingleIn,
    ScoreBatchIn,
    ScoreRowIn,
    SniffIn,
)
from statline.slapi.scoring import ScoreBatchRequest, ScoreRowRequest
from statline.slapi.scoring import adapters_available as _adapters_available
from statline.slapi.scoring import score_batch as _score_batch
from statline.slapi.scoring import score_row as _score_row

# =============================================================================
# Versioning
# =============================================================================

API_VERSION = "3.0.0"

# Scope names (expanded/validated in auth.py; app.py only gates)
SCOPE_USERBASE = "userbase"
SCOPE_MODERATION = "moderation"
SCOPE_ADMIN = "admin"

app: FastAPI = FastAPI(
    title="StatLine API",
    version=API_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Auth dependency for protected endpoints (requires BOTH device proof + api key)
AuthDep = Annotated[Principal, Depends(require_principal)]

# Device-only dependency for issuance endpoints (device-proof only)
DeviceRow: TypeAlias = Dict[str, Any]
DeviceRowDep = Annotated[DeviceRow, Depends(require_device)]


# =============================================================================
# Scope gating helpers (server-side authoritative RBAC)
# =============================================================================

def require_scope(scope: str) -> Callable[[Principal], Principal]:
    def _dep(p: Annotated[Principal, Depends(require_principal)]) -> Principal:
        need(scope, p)
        return p
    return _dep


# =============================================================================
# Minimal auth schemas (until you move them into statline.slapi.schemas)
# =============================================================================

class ApiKeyRequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    owner: Optional[str] = None
    scopes: Optional[Sequence[str]] = None
    ttl_days: Optional[int] = 30


class ApiKeyRequestDecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decided_by: str = "dev"
    note: Optional[str] = None
    scopes: Optional[Sequence[str]] = None  # optional scope narrowing at approval


class EnrollIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reg_token: str
    user: str
    email: Optional[str] = None
    device_pub_b64: str
    meta: Optional[Dict[str, Any]] = None


# =============================================================================
# Routers (V3)
# =============================================================================

# Public-ish auth flows:
# - enroll is anonymous (reg token is the capability)
# - other /auth endpoints are device-proof only or full auth where needed
auth_router = APIRouter(prefix="/v3/auth", tags=["auth"])

# Userbase API (everything “normal”)
api_router = APIRouter(
    prefix="/v3",
    tags=["api"],
    dependencies=[Depends(require_scope(SCOPE_USERBASE))],
)

# Moderation (revoke keys/devices, view audit; NOT approvals/devkey)
mod_router = APIRouter(
    prefix="/v3/mod",
    tags=["moderation"],
    dependencies=[Depends(require_scope(SCOPE_MODERATION))],
)

# Admin/superadmin (root) capabilities
admin_router = APIRouter(
    prefix="/v3/admin",
    tags=["admin"],
    dependencies=[Depends(require_scope(SCOPE_ADMIN))],
)


# =============================================================================
# DEVKEY helpers
# =============================================================================

def _safe_devkey_fp() -> Optional[str]:
    try:
        return devkey_fingerprint()
    except Exception:
        return None


# =============================================================================
# Admin endpoints (ADMIN scope)
# =============================================================================

@admin_router.post("/devkey/init")  # pyright: ignore[reportUnknownMemberType]
def admin_devkey_init(overwrite: bool = False) -> Dict[str, Any]:
    """Generate DEVKEY + DEVKEY.pub files on this host (ADMIN)."""
    return admin_generate_devkey_files(overwrite=overwrite)


@admin_router.get("/devkey")  # pyright: ignore[reportUnknownMemberType]
def admin_devkey_info() -> Dict[str, Any]:
    """Return current DEVKEY fingerprint (ADMIN)."""
    return {"fingerprint": _safe_devkey_fp()}


@admin_router.post("/mint-regtoken")  # pyright: ignore[reportUnknownMemberType]
def admin_mint_reg(
    org: str,
    scopes: Optional[List[str]] = None,
    ttl_days: Optional[int] = 14,
) -> Dict[str, Any]:
    """Mint a one-time enrollment token (reg_...) signed by DEVKEY (ADMIN)."""
    eff_scopes = scopes or [SCOPE_USERBASE]
    tok = admin_mint_regtoken(org=org, scopes=eff_scopes, ttl_days=ttl_days)
    return {"token": tok, "org": org, "scopes": eff_scopes, "kid": _safe_devkey_fp()}


@admin_router.post("/regtoken/inspect")  # pyright: ignore[reportUnknownMemberType]
def admin_regtoken_inspect(token: str) -> Dict[str, Any]:
    return {"payload": inspect_regtoken(token)}


@admin_router.get("/enrollments")  # pyright: ignore[reportUnknownMemberType]
def admin_enrollments(status: str = "PENDING") -> Dict[str, Any]:
    return {"enrollments": admin_list_enrollments(status=status)}


@admin_router.get("/enrollments/{request_id}")  # pyright: ignore[reportUnknownMemberType]
def admin_enrollment_get(request_id: str) -> Dict[str, Any]:
    r = get_enrollment_request(request_id)
    if r is None:
        raise HTTPException(404, "not found")
    return r


@admin_router.post("/enrollments/{request_id}/approve")  # pyright: ignore[reportUnknownMemberType]
def admin_enroll_approve(
    request_id: str,
    decided_by: str = "dev",
    note: Optional[str] = None,
) -> Dict[str, bool]:
    return {"ok": admin_approve_enrollment(request_id=request_id, decided_by=decided_by, decision_note=note)}


@admin_router.post("/enrollments/{request_id}/deny")  # pyright: ignore[reportUnknownMemberType]
def admin_enroll_deny(
    request_id: str,
    decided_by: str = "dev",
    note: Optional[str] = None,
) -> Dict[str, bool]:
    return {"ok": admin_deny_enrollment(request_id=request_id, decided_by=decided_by, decision_note=note)}


@admin_router.get("/apikey-requests")  # pyright: ignore[reportUnknownMemberType]
def admin_apikey_requests(status: str = "PENDING", org: Optional[str] = None) -> Dict[str, Any]:
    return {"requests": admin_list_apikey_requests(status=status, org=org)}


@admin_router.post("/apikey-requests/{request_id}/approve")  # pyright: ignore[reportUnknownMemberType]
def admin_apikey_request_approve(request_id: str, body: ApiKeyRequestDecisionIn) -> Dict[str, bool]:
    ok = admin_approve_apikey_request(
        request_id=request_id,
        decided_by=body.decided_by,
        decision_note=body.note,
        scopes=list(body.scopes) if body.scopes is not None else None,
    )
    return {"ok": ok}


@admin_router.post("/apikey-requests/{request_id}/deny")  # pyright: ignore[reportUnknownMemberType]
def admin_apikey_request_deny(request_id: str, body: ApiKeyRequestDecisionIn) -> Dict[str, bool]:
    ok = admin_deny_apikey_request(
        request_id=request_id,
        decided_by=body.decided_by,
        decision_note=body.note,
    )
    return {"ok": ok}


# (Optional) Debug endpoints are ADMIN-only in V3
@admin_router.get("/debug/core-adapters")  # pyright: ignore[reportUnknownMemberType]
def debug_core_adapters() -> Dict[str, Any]:
    base = Path(__file__).resolve().parents[1] / "core" / "adapters" / "defs"
    names: List[str] = []
    try:
        for p in sorted(base.glob("*.y*ml")):
            names.append(p.stem)
        return {"defs_dir": str(base), "adapters": names}
    except Exception as e:
        return {"defs_dir": str(base), "error": str(e)}


@admin_router.get("/debug/registry-list")  # pyright: ignore[reportUnknownMemberType]
def debug_registry_list() -> Dict[str, Any]:
    from statline.core.adapters import list_names
    try:
        return {"adapters": list_names()}
    except Exception as e:
        return {"error": str(e)}


# =============================================================================
# Moderation endpoints (MODERATION scope)
# =============================================================================

@mod_router.post("/devices/{device_id}/revoke")  # pyright: ignore[reportUnknownMemberType]
def mod_device_revoke(device_id: str, note: Optional[str] = None) -> Dict[str, bool]:
    return {"ok": admin_revoke_device(device_id=device_id, note=note)}


@mod_router.get("/apikeys")  # pyright: ignore[reportUnknownMemberType]
def mod_apikeys(org: Optional[str] = None) -> Dict[str, Any]:
    return {"keys": admin_list_apikeys(org=org)}


@mod_router.post("/apikeys/{prefix}/access")  # pyright: ignore[reportUnknownMemberType]
def mod_apikey_access(prefix: str, value: bool) -> Dict[str, bool]:
    return {"ok": admin_set_apikey_access(prefix8=prefix, value=value)}


@mod_router.delete("/apikeys/{prefix}")  # pyright: ignore[reportUnknownMemberType]
def mod_apikey_revoke(prefix: str) -> Dict[str, bool]:
    return {"ok": admin_revoke_apikey(prefix8=prefix)}


@mod_router.get("/audit")  # pyright: ignore[reportUnknownMemberType]
def mod_audit(limit: int = 200, event: Optional[str] = None, org: Optional[str] = None) -> Dict[str, Any]:
    return {"audit": admin_list_audit(limit=limit, event=event, org=org)}


# =============================================================================
# Auth (user-facing): enroll + apikey requests (device-proof only)
# =============================================================================

@auth_router.post("/enroll")  # pyright: ignore[reportUnknownMemberType]
def enroll(body: EnrollIn) -> Dict[str, Any]:
    """
    Redeem a reg_ token ONCE, creating a PENDING enrollment request.
    ADMIN must approve before device becomes ACTIVE.
    """
    return create_enrollment_request(
        reg_token=body.reg_token,
        user=body.user,
        email=body.email,
        device_pub_b64=body.device_pub_b64,
        meta=body.meta,
    )


@auth_router.get("/device")  # pyright: ignore[reportUnknownMemberType]
def device_info(device: DeviceRowDep) -> Dict[str, Any]:
    """Device-proof only sanity check: returns the server-side device record."""
    return {"device": device}


@auth_router.post("/apikey-requests")  # pyright: ignore[reportUnknownMemberType]
def apikey_request(body: ApiKeyRequestIn, device: DeviceRowDep) -> Dict[str, Any]:
    """
    Create a PENDING api key request for this ACTIVE device (device-proof only).
    ADMIN must approve before the request can be claimed.
    """
    owner = body.owner or str(device.get("user") or "unknown")
    return create_apikey_request(
        device_id=str(device["device_id"]),
        owner=owner,
        scopes=list(body.scopes) if body.scopes is not None else None,
        ttl_days=body.ttl_days if body.ttl_days is not None else 30,
    )


@auth_router.get("/apikey-requests")  # pyright: ignore[reportUnknownMemberType]
def apikey_requests(device: DeviceRowDep) -> Dict[str, Any]:
    return {"requests": list_apikey_requests_for_device(str(device["device_id"]))}


@auth_router.post("/apikey-requests/{request_id}/claim")  # pyright: ignore[reportUnknownMemberType]
def apikey_claim(request_id: str, device: DeviceRowDep) -> Dict[str, Any]:
    """Claim an APPROVED request (mints api_ token exactly once)."""
    token, rec = claim_apikey_request(request_id=request_id, device_id=str(device["device_id"]))
    return {"token": token, "record": rec}


@auth_router.get("/apikeys")  # pyright: ignore[reportUnknownMemberType]
def apikeys(device: DeviceRowDep) -> Dict[str, Any]:
    return {"keys": list_apikeys_for_device(str(device["device_id"]))}


@auth_router.delete("/apikeys/{prefix}")  # pyright: ignore[reportUnknownMemberType]
def apikey_revoke(prefix: str, device: DeviceRowDep) -> Dict[str, bool]:
    return {"ok": revoke_apikey_for_device(str(device["device_id"]), prefix)}


@auth_router.get("/whoami")  # pyright: ignore[reportUnknownMemberType]
def whoami(auth: AuthDep) -> Dict[str, Any]:
    # This is full auth (device + api key), so CLI can introspect capabilities.
    return {
        "org": auth.org,
        "subject": auth.subject,
        "device_id": auth.device_id,
        "api_prefix": auth.api_prefix,
        "scopes": sorted(auth.scopes),
    }


# =============================================================================
# Health / info (no auth)
# =============================================================================

@app.get("/")  # pyright: ignore[reportUnknownMemberType]
def root() -> Dict[str, Any]:
    return {"name": "StatLine API", "version": API_VERSION}


@app.get("/v3/health")  # pyright: ignore[reportUnknownMemberType]
def health() -> Dict[str, Any]:
    # Intentionally do NOT expose devkey fingerprint in public health.
    return {"ok": True, "version": API_VERSION}


# =============================================================================
# Adapters / metadata (USERBASE)
# =============================================================================

@api_router.get("/datasets")  # pyright: ignore[reportUnknownMemberType]
def list_datasets() -> Dict[str, Any]:
    base = Path(__file__).resolve().parents[1] / "data" / "stats"  # statline/data/stats
    names: List[str] = []
    try:
        if base.exists():
            for p in sorted(base.glob("*.csv")):
                names.append(p.name)
    except Exception:
        pass
    return {"datasets": names}


@api_router.get("/adapters")  # pyright: ignore[reportUnknownMemberType]
def list_adapters(fast: bool = False) -> Dict[str, List[str]]:
    if fast:
        return {"adapters": _list_yaml()}

    try:
        names = _adapters_available() or list(_list_names())
        if names:
            return {"adapters": names}
    except Exception:
        pass

    return {"adapters": _list_yaml()}


@api_router.get("/adapter/{adapter}/weights")  # pyright: ignore[reportUnknownMemberType]
def adapter_weights(adapter: str) -> Dict[str, Dict[str, Dict[str, float]]]:
    spec = _load_spec(adapter)
    out: Dict[str, Dict[str, float]] = {}
    for preset, bw in spec.weights.items():
        inner: Dict[str, float] = {str(k): float(v) for k, v in bw.items()}
        out[str(preset)] = inner
    return {"weights": out}


@api_router.get("/adapter/{adapter}/metric-keys")  # pyright: ignore[reportUnknownMemberType]
def adapter_metric_keys(adapter: str) -> Dict[str, List[str]]:
    spec = _load_spec(adapter)
    seen: set[str] = set()
    keys: List[str] = []
    for m in spec.metrics:
        if m.key and m.key not in seen:
            seen.add(m.key)
            keys.append(m.key)
    return {"keys": keys}


@api_router.post("/adapters/sniff")  # pyright: ignore[reportUnknownMemberType]
def sniff_adapters(body: SniffIn) -> Dict[str, List[str]]:
    return {"adapters": _sniff_adapters(body.headers)}


@api_router.get("/adapter/{adapter}/metric-keys/probe")  # pyright: ignore[reportUnknownMemberType]
def adapter_metric_keys_probe(adapter: str) -> Dict[str, List[str]]:
    return {"keys": _mapper_keys(adapter)}


@api_router.get("/adapter/{adapter}/inputs")  # pyright: ignore[reportUnknownMemberType]
def adapter_inputs(adapter: str) -> Dict[str, List[str]]:
    return {"inputs": _infer_input_keys(adapter)}


@api_router.get("/adapter/{adapter}/prompt-keys")  # pyright: ignore[reportUnknownMemberType]
def adapter_prompt_keys(adapter: str) -> Dict[str, List[str]]:
    keys = _infer_input_keys(adapter) or _mapper_metric_like_keys(adapter) or _declared_metric_keys(adapter)
    return {"keys": keys}


# =============================================================================
# Mapping (USERBASE)
# =============================================================================

def _map_row_with_adapter(adapter_key: str, adp: Any, row: Mapping[str, Any]) -> Dict[str, float]:
    mapped = _safe_map_raw(adp, row)
    safe: Dict[str, float] = {}
    for k, v in mapped.items():
        try:
            safe[str(k)] = float(v) if v is not None else 0.0
        except Exception:
            safe[str(k)] = 0.0
    return safe


def _map_row(adapter_key: str, row: Mapping[str, Any]) -> Dict[str, float]:
    adp = _load_adapter(adapter_key)
    return _map_row_with_adapter(adapter_key, adp, row)


@api_router.post("/map/row")  # pyright: ignore[reportUnknownMemberType]
def map_row(body: MapRowIn) -> Dict[str, float]:
    return _map_row(body.adapter, body.row)


@api_router.post("/map/batch")  # pyright: ignore[reportUnknownMemberType]
def map_batch(body: MapBatchIn) -> List[Dict[str, float]]:
    adp = _load_adapter(body.adapter)
    return [_map_row_with_adapter(body.adapter, adp, r) for r in body.rows]


# =============================================================================
# Scoring via façade (USERBASE)
# =============================================================================

@api_router.post("/score/row")  # pyright: ignore[reportUnknownMemberType]
def score_row(body: ScoreRowIn) -> Dict[str, Any]:
    weights_in = body.weights if body.weights is not None else body.weights_override
    req = ScoreRowRequest(
        adapter=body.adapter,
        row=body.row,
        weights=weights_in if isinstance(weights_in, (str, dict)) else None,
        penalties_override=body.penalties_override,
        output=body.output,
        filters=body.filters,
        context=body.context,
        caps_override=body.caps_override,
    )
    return _score_row(req, timing=None)


@api_router.post("/score/batch")  # pyright: ignore[reportUnknownMemberType]
def score_batch(body: ScoreBatchIn) -> List[Dict[str, Any]]:
    weights_in = body.weights if body.weights is not None else body.weights_override
    req = ScoreBatchRequest(
        adapter=body.adapter,
        rows=list(body.rows),
        weights=weights_in if isinstance(weights_in, (str, dict)) else None,
        penalties_override=body.penalties_override,
        output=body.output,
        filters=body.filters,
        context=body.context,
        caps_override=body.caps_override,
    )
    return _score_batch(req, timing=None)


# =============================================================================
# Calc passthroughs (mapped-metrics convenience) (USERBASE)
# =============================================================================

@api_router.post("/calc/pri")  # pyright: ignore[reportUnknownMemberType]
def calc_pri_single(body: PriSingleIn) -> Dict[str, Any]:
    """
    MAPPED metrics in -> PRI details dict out.
    (This is NOT the README raw-row API.)
    """
    adp = _load_adapter(body.adapter)

    weights_in = body.weights if getattr(body, "weights", None) is not None else body.weights_override
    weights_arg = weights_in if isinstance(weights_in, (str, dict)) else None

    return _calculate_pri_mapped_single(
        dict(body.row),
        adp,
        weights=weights_arg,
        penalties_override=getattr(body, "penalties_override", None),
        output=getattr(body, "output", None),
        context=getattr(body, "context", None),
        caps_override=getattr(body, "caps_override", None),
    )


@api_router.post("/calc/pri/batch")  # pyright: ignore[reportUnknownMemberType]
def calc_pri_batch(body: PriBatchIn) -> List[Dict[str, Any]]:
    """
    MAPPED rows in -> PRI details list out.
    caps_mode="clamps" forces per-row clamps context (single-row behavior).
    """
    adp = _load_adapter(body.adapter)

    weights_in = body.weights if getattr(body, "weights", None) is not None else body.weights_override
    weights_arg = weights_in if isinstance(weights_in, (str, dict)) else None

    if (body.caps_mode or "batch").lower() == "clamps":
        return [
            _calculate_pri_mapped_single(
                dict(r),
                adp,
                weights=weights_arg,
                penalties_override=getattr(body, "penalties_override", None),
                output=getattr(body, "output", None),
                context=getattr(body, "context", None),
                caps_override=getattr(body, "caps_override", None),
            )
            for r in body.rows
        ]

    return _calculate_pri_mapped_batch(
        [dict(r) for r in body.rows],
        adapter=adp,
        weights=weights_arg,
        penalties_override=getattr(body, "penalties_override", None),
        output=getattr(body, "output", None),
        context=body.context,
        caps_override=body.caps_override,
    )


# =============================================================================
# Combined convenience endpoints (RAW -> MAPPED -> PRI) (USERBASE)
# =============================================================================

@api_router.post("/pri/row")  # pyright: ignore[reportUnknownMemberType]
def pri_row(body: ScoreRowIn) -> Dict[str, Any]:
    """
    RAW row in -> maps via adapter -> PRI details out.
    This combines /v3/map/row + /v3/calc/pri into one call.
    """
    adp = _load_adapter(body.adapter)

    weights_in = body.weights if getattr(body, "weights", None) is not None else getattr(body, "weights_override", None)
    weights_arg = weights_in if isinstance(weights_in, (str, dict)) else None  # pyright: ignore[reportUnknownVariableType]

    mapped = _map_row_with_adapter(body.adapter, adp, body.row)

    return _calculate_pri_mapped_single(
        mapped,
        adp,
        weights=weights_arg,  # pyright: ignore[reportUnknownArgumentType]
        penalties_override=getattr(body, "penalties_override", None),
        output=getattr(body, "output", None),
        context=getattr(body, "context", None),
        caps_override=getattr(body, "caps_override", None),
    )


@api_router.post("/pri/batch")  # pyright: ignore[reportUnknownMemberType]
def pri_batch(body: ScoreBatchIn, caps_mode: str = "batch") -> List[Dict[str, Any]]:
    """
    RAW rows in -> maps via adapter -> PRI details list out.
    caps_mode:
      - "batch"  (default): batch caps behavior
      - "clamps": per-row single behavior (like /v3/calc/pri/batch caps_mode="clamps")
    """
    adp = _load_adapter(body.adapter)

    weights_in = body.weights if getattr(body, "weights", None) is not None else getattr(body, "weights_override", None)
    weights_arg = weights_in if isinstance(weights_in, (str, dict)) else None  # pyright: ignore[reportUnknownVariableType]

    mapped_rows = [_map_row_with_adapter(body.adapter, adp, r) for r in body.rows]

    if (caps_mode or "batch").lower() == "clamps":
        return [
            _calculate_pri_mapped_single(
                r,
                adp,
                weights=weights_arg,  # pyright: ignore[reportUnknownArgumentType]
                penalties_override=getattr(body, "penalties_override", None),
                output=getattr(body, "output", None),
                context=getattr(body, "context", None),
                caps_override=getattr(body, "caps_override", None),
            )
            for r in mapped_rows
        ]

    return _calculate_pri_mapped_batch(
        mapped_rows,
        adapter=adp,
        weights=weights_arg,  # pyright: ignore[reportUnknownArgumentType]
        penalties_override=getattr(body, "penalties_override", None),
        output=getattr(body, "output", None),
        context=getattr(body, "context", None),
        caps_override=getattr(body, "caps_override", None),
    )


# =============================================================================
# Optional: adapter spec peek (USERBASE)
# =============================================================================

@api_router.get("/adapter/{adapter}/spec")  # pyright: ignore[reportUnknownMemberType]
def adapter_spec(adapter: str) -> Dict[str, Any]:
    """
    Thin, JSON-safe peek at an adapter spec.
    (Avoids returning raw objects that may not be serializable.)
    """
    spec = _load_spec(adapter)

    weights: Dict[str, Dict[str, float]] = {}
    for preset, bw in spec.weights.items():
        weights[str(preset)] = {str(k): float(v) for k, v in bw.items()}

    buckets: List[str] = []
    try:
        buckets = [str(b.key) for b in getattr(spec, "buckets", {}).values()]  # type: ignore[attr-defined]
    except Exception:
        buckets = []

    metrics: List[str] = []
    try:
        metrics = [str(m.key) for m in spec.metrics if getattr(m, "key", None)]
    except Exception:
        metrics = []

    return {
        "key": getattr(spec, "key", adapter),
        "version": getattr(spec, "version", None),
        "title": getattr(spec, "title", None),
        "aliases": list(getattr(spec, "aliases", []) or []),
        "buckets": buckets,
        "metrics": metrics,
        "weights": weights,
    }

app.include_router(auth_router)
app.include_router(api_router)
app.include_router(mod_router)
app.include_router(admin_router)