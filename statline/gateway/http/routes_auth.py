"""Authentication, moderation, and administration routes for API v4."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from statline.core.adapters import adapter_cache_info, refresh_adapters
from statline.gateway.auth.service import (
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
    revoke_apikey_for_device,
)
from statline.gateway.http.dependencies import (
    SCOPE_ADMIN,
    SCOPE_MODERATION,
    SCOPE_USERBASE,
    AuthDep,
    DeviceRowDep,
    require_scope,
)
from statline.gateway.http.models import (
    ApiKeyRequestDecisionIn,
    ApiKeyRequestIn,
    EnrollIn,
)

auth_router = APIRouter(prefix="/v4/auth", tags=["authentication"])
mod_router = APIRouter(
    prefix="/v4/moderation",
    tags=["moderation"],
    dependencies=[Depends(require_scope(SCOPE_MODERATION))],
)
admin_router = APIRouter(
    prefix="/v4/admin",
    tags=["administration"],
    dependencies=[Depends(require_scope(SCOPE_ADMIN))],
)


def _safe_devkey_fingerprint() -> Optional[str]:
    try:
        return devkey_fingerprint()
    except Exception:
        return None


@auth_router.post("/enroll", summary="Request device enrollment")
def enroll(body: EnrollIn) -> Dict[str, Any]:
    return create_enrollment_request(
        reg_token=body.reg_token,
        user=body.user,
        email=body.email,
        device_pub_b64=body.device_pub_b64,
        meta=body.meta,
    )


@auth_router.get("/device", summary="Inspect verified device")
def device_info(device: DeviceRowDep) -> Dict[str, Any]:
    return {"device": device}


@auth_router.post("/api-key-requests", summary="Request an API key")
def api_key_request(body: ApiKeyRequestIn, device: DeviceRowDep) -> Dict[str, Any]:
    owner = body.owner or str(device.get("user") or "unknown")
    return create_apikey_request(
        device_id=str(device["device_id"]),
        owner=owner,
        scopes=list(body.scopes) if body.scopes is not None else None,
        ttl_days=body.ttl_days if body.ttl_days is not None else 30,
    )


@auth_router.get("/api-key-requests", summary="List this device's API-key requests")
def api_key_requests(device: DeviceRowDep) -> Dict[str, Any]:
    return {"requests": list_apikey_requests_for_device(str(device["device_id"]))}


@auth_router.post(
    "/api-key-requests/{request_id}/claim",
    summary="Claim an approved API key",
)
def api_key_claim(request_id: str, device: DeviceRowDep) -> Dict[str, Any]:
    token, record = claim_apikey_request(
        request_id=request_id,
        device_id=str(device["device_id"]),
    )
    return {"token": token, "record": record}


@auth_router.get("/api-keys", summary="List this device's API keys")
def api_keys(device: DeviceRowDep) -> Dict[str, Any]:
    return {"keys": list_apikeys_for_device(str(device["device_id"]))}


@auth_router.delete("/api-keys/{prefix}", summary="Revoke one of this device's API keys")
def api_key_revoke(prefix: str, device: DeviceRowDep) -> Dict[str, bool]:
    return {"ok": revoke_apikey_for_device(str(device["device_id"]), prefix)}


@auth_router.get("/whoami", summary="Inspect the authenticated principal")
def whoami(auth: AuthDep) -> Dict[str, Any]:
    return {
        "org": auth.org,
        "subject": auth.subject,
        "device_id": auth.device_id,
        "api_prefix": auth.api_prefix,
        "scopes": sorted(auth.scopes),
        "auth_mode": auth.auth_mode,
        "device_verified": auth.device_verified,
    }


@mod_router.post("/devices/{device_id}/revoke", summary="Revoke a device")
def revoke_device(device_id: str, note: Optional[str] = None) -> Dict[str, bool]:
    return {"ok": admin_revoke_device(device_id=device_id, note=note)}


@mod_router.get("/api-keys", summary="List API keys")
def list_api_keys(org: Optional[str] = None) -> Dict[str, Any]:
    return {"keys": admin_list_apikeys(org=org)}


@mod_router.patch("/api-keys/{prefix}/access", summary="Enable or disable API-key access")
def set_api_key_access(prefix: str, value: bool) -> Dict[str, bool]:
    return {"ok": admin_set_apikey_access(prefix8=prefix, value=value)}


@mod_router.delete("/api-keys/{prefix}", summary="Revoke an API key")
def revoke_api_key(prefix: str) -> Dict[str, bool]:
    return {"ok": admin_revoke_apikey(prefix8=prefix)}


@mod_router.get("/audit", summary="Read authentication audit events")
def audit(
    limit: int = 200,
    event: Optional[str] = None,
    org: Optional[str] = None,
) -> Dict[str, Any]:
    return {"audit": admin_list_audit(limit=limit, event=event, org=org)}


@admin_router.post("/developer-key", summary="Initialize the developer signing key")
def initialize_developer_key(overwrite: bool = False) -> Dict[str, Any]:
    return admin_generate_devkey_files(overwrite=overwrite)


@admin_router.get("/developer-key", summary="Inspect the developer signing key")
def developer_key() -> Dict[str, Any]:
    return {"fingerprint": _safe_devkey_fingerprint()}


@admin_router.post("/registration-tokens", summary="Mint a registration token")
def mint_registration_token(
    org: str,
    scopes: Optional[List[str]] = None,
    ttl_days: Optional[int] = 14,
) -> Dict[str, Any]:
    effective_scopes = scopes or [SCOPE_USERBASE]
    token = admin_mint_regtoken(org=org, scopes=effective_scopes, ttl_days=ttl_days)
    return {
        "token": token,
        "org": org,
        "scopes": effective_scopes,
        "kid": _safe_devkey_fingerprint(),
    }


@admin_router.post("/registration-tokens/inspect", summary="Inspect a registration token")
def inspect_registration_token(token: str) -> Dict[str, Any]:
    return {"payload": inspect_regtoken(token)}


@admin_router.get("/enrollments", summary="List enrollment requests")
def enrollments(status: str = "PENDING") -> Dict[str, Any]:
    return {"enrollments": admin_list_enrollments(status=status)}


@admin_router.get("/enrollments/{request_id}", summary="Read an enrollment request")
def enrollment(request_id: str) -> Dict[str, Any]:
    record = get_enrollment_request(request_id)
    if record is None:
        raise HTTPException(status_code=404, detail="not found")
    return record


@admin_router.post("/enrollments/{request_id}/approve", summary="Approve enrollment")
def approve_enrollment(
    request_id: str,
    decided_by: str = "dev",
    note: Optional[str] = None,
) -> Dict[str, bool]:
    return {
        "ok": admin_approve_enrollment(
            request_id=request_id,
            decided_by=decided_by,
            decision_note=note,
        )
    }


@admin_router.post("/enrollments/{request_id}/deny", summary="Deny enrollment")
def deny_enrollment(
    request_id: str,
    decided_by: str = "dev",
    note: Optional[str] = None,
) -> Dict[str, bool]:
    return {
        "ok": admin_deny_enrollment(
            request_id=request_id,
            decided_by=decided_by,
            decision_note=note,
        )
    }


@admin_router.get("/api-key-requests", summary="List API-key requests")
def admin_api_key_requests(
    status: str = "PENDING",
    org: Optional[str] = None,
) -> Dict[str, Any]:
    return {"requests": admin_list_apikey_requests(status=status, org=org)}


@admin_router.post(
    "/api-key-requests/{request_id}/approve",
    summary="Approve an API-key request",
)
def approve_api_key_request(
    request_id: str,
    body: ApiKeyRequestDecisionIn,
) -> Dict[str, bool]:
    return {
        "ok": admin_approve_apikey_request(
            request_id=request_id,
            decided_by=body.decided_by,
            decision_note=body.note,
            scopes=list(body.scopes) if body.scopes is not None else None,
        )
    }


@admin_router.post(
    "/api-key-requests/{request_id}/deny",
    summary="Deny an API-key request",
)
def deny_api_key_request(
    request_id: str,
    body: ApiKeyRequestDecisionIn,
) -> Dict[str, bool]:
    return {
        "ok": admin_deny_apikey_request(
            request_id=request_id,
            decided_by=body.decided_by,
            decision_note=body.note,
        )
    }


@admin_router.post("/adapters/refresh", summary="Reload the adapter registry")
def reload_adapters() -> Dict[str, Any]:
    refresh_adapters()
    return {"ok": True, "cache": adapter_cache_info()}


__all__ = ["admin_router", "auth_router", "mod_router"]
