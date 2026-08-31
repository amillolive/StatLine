# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false

from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException
from statline.gateway.auth import service
from statline.gateway.auth.types import Principal


def test_base64_hash_and_canonical_json_helpers() -> None:
    raw = b"hello\x00world"
    encoded = service._b64u_encode(raw)
    assert "=" not in encoded
    assert service._b64u_decode(encoded) == raw
    assert service._sha256_hex("hello") == service._sha256_hex(b"hello")
    assert service._json_canon({"b": 2, "a": 1}) == b'{"a":1,"b":2}'


def test_generate_load_and_sign_device_keypair() -> None:
    private_pem, public_b64 = service.generate_device_keypair()
    private = service.load_device_private_key(private_pem)
    public_raw = service._b64u_decode(public_b64)
    assert len(public_raw) == 32

    signature = service.sign_envelope(
        device_private_pem=private_pem,
        method="post",
        target="/v4/score?x=1",
        timestamp=123,
        nonce="nonce",
        body_bytes=b"{}",
    )

    body_hash = service._sha256_hex(b"{}")
    envelope = f"POST\n/v4/score?x=1\n123\nnonce\n{body_hash}".encode()
    private.public_key().verify(service._b64u_decode(signature), envelope)


def test_load_device_private_key_rejects_non_ed25519() -> None:
    with pytest.raises((ValueError, TypeError)):
        service.load_device_private_key("not a pem")


def test_scope_guards() -> None:
    principal = Principal(
        org="org",
        subject="user",
        device_id="device",
        api_prefix="api_1234",
        scopes={"score", "adapter:read"},
    )

    service.need("score", principal)
    service.need("*", principal)
    service.need_any(["missing", "score"], principal)
    service.need_all(["score", "adapter:read"], principal)

    with pytest.raises(HTTPException) as one:
        service.need("admin", principal)
    assert one.value.status_code == 403

    with pytest.raises(HTTPException):
        service.need_any(["admin", "mod"], principal)

    with pytest.raises(HTTPException):
        service.need_all(["score", "admin"], principal)


def test_regtoken_mint_and_verify_with_ephemeral_devkey(monkeypatch: pytest.MonkeyPatch) -> None:
    private = Ed25519PrivateKey.generate()
    monkeypatch.setattr(service, "_load_dev_private", lambda: private)
    monkeypatch.setattr(service, "_load_dev_public", lambda: private.public_key())
    monkeypatch.setattr(service, "devkey_fingerprint", lambda: "test-kid")

    token = service.admin_mint_regtoken(org="test-org", scopes=["userbase"], ttl_days=1)
    assert token.startswith("reg_")

    payload = service.verify_regtoken(token)
    assert payload["v"] == 1
    assert payload["org"] == "test-org"
    assert payload["scopes"] == ["userbase"]
    assert payload["kid"] == "test-kid"
    assert isinstance(payload["rid"], str)


def test_regtoken_validation_rejects_bad_forms(monkeypatch: pytest.MonkeyPatch) -> None:
    private = Ed25519PrivateKey.generate()
    monkeypatch.setattr(service, "_load_dev_public", lambda: private.public_key())

    with pytest.raises(HTTPException) as wrong_type:
        service.verify_regtoken("api_not-a-reg-token")
    assert wrong_type.value.status_code == 401

    with pytest.raises(HTTPException) as malformed:
        service.verify_regtoken("reg_bad")
    assert malformed.value.status_code == 401

    payload = {
        "v": 1,
        "rid": "1234567890abcdef",
        "org": "org",
        "scopes": ["userbase"],
        "iat": 1,
        "exp": None,
        "kid": "kid",
    }
    raw = service._b64u_encode(service._json_canon(payload))
    bad_signature = service._b64u_encode(b"0" * 64)
    with pytest.raises(HTTPException) as invalid_signature:
        service.verify_regtoken(f"reg_{raw}.{bad_signature}")
    assert invalid_signature.value.status_code == 401


def test_regtoken_expiry_is_checked_before_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    private = Ed25519PrivateKey.generate()
    monkeypatch.setattr(service, "_load_dev_public", lambda: private.public_key())

    payload = {
        "v": 1,
        "rid": "1234567890abcdef",
        "org": "org",
        "scopes": ["userbase"],
        "iat": 1,
        "exp": 1,
        "kid": "kid",
    }
    raw = service._b64u_encode(json.dumps(payload).encode())
    signature = service._b64u_encode(b"0" * 64)
    with pytest.raises(HTTPException) as expired:
        service.verify_regtoken(f"reg_{raw}.{signature}")
    assert expired.value.status_code == 403


def test_b64_decode_accepts_unpadded_values() -> None:
    value = base64.urlsafe_b64encode(b"abcde").decode().rstrip("=")
    assert service._b64u_decode(value) == b"abcde"
