"""Public browsing, content negotiation and exact CORS policy checks."""

# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false

from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from statline.gateway.http.app import _sqlite_error_handler
from statline.gateway.http.portal import configure_portal_cors
from statline.gateway.http.routes_api import api_router, public_router


@pytest.fixture
def portal_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(
        "STATLINE_PORTAL_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,https://statline.dev",
    )
    instance = FastAPI(exception_handlers={sqlite3.Error: _sqlite_error_handler})
    instance.include_router(public_router)
    instance.include_router(api_router)

    @instance.get("/test-storage-error")
    def storage_error() -> None:
        raise sqlite3.OperationalError("synthetic")

    configure_portal_cors(instance)
    with TestClient(instance) as client:
        yield client


@pytest.mark.parametrize(
    "origin", ["http://localhost:3000", "http://127.0.0.1:3000", "https://statline.dev"]
)
def test_public_health_and_scoring_preflight(portal_client: TestClient, origin: str) -> None:
    response = portal_client.get("/v4/health", headers={"Origin": origin})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    preflight = portal_client.options(
        "/v4/score",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type,x-sl-device,x-sl-timestamp,x-sl-nonce,x-sl-signature",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == origin
    assert "access-control-allow-credentials" not in preflight.headers


def test_cors_rejects_unlisted_origins_and_headers(portal_client: TestClient) -> None:
    response = portal_client.options(
        "/v4/score",
        headers={"Origin": "https://untrusted.invalid", "Access-Control-Request-Method": "POST"},
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
    response = portal_client.options(
        "/v4/score",
        headers={
            "Origin": "https://statline.dev",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-unrelated-app",
        },
    )
    assert response.status_code == 400


@pytest.mark.parametrize("path", ["/v4/score", "/test-storage-error", "/missing"])
def test_cors_is_present_on_auth_and_handled_error_responses(
    portal_client: TestClient, path: str
) -> None:
    response = (
        portal_client.post(
            path,
            headers={"Origin": "https://statline.dev"},
            json={"adapter": "eba.players", "rows": []},
        )
        if path == "/v4/score"
        else portal_client.get(path, headers={"Origin": "https://statline.dev"})
    )
    assert response.status_code in {401, 403, 404, 503}
    assert response.headers["access-control-allow-origin"] == "https://statline.dev"


@pytest.mark.parametrize(
    "value",
    [
        "*",
        "https://statline.dev/docs",
        "https://user:pass@statline.dev",
        "https://statline.dev?query=1",
        "chrome-extension://example",
        "https://*.statline.dev",
    ],
)
def test_cors_invalid_origins_fail_fast(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("STATLINE_PORTAL_ORIGINS", value)
    with pytest.raises(ValueError, match="exact HTTP"):
        configure_portal_cors(FastAPI())


def test_api_root_is_html_for_browsers_and_json_for_clients(portal_client: TestClient) -> None:
    machine = portal_client.get("/")
    assert machine.status_code == 200 and machine.json()["name"] == "StatLine API"
    assert "Accept" in machine.headers["vary"].split(", ")
    browser = portal_client.get("/", headers={"Accept": "text/html,application/xhtml+xml"})
    assert browser.status_code == 200
    assert browser.headers["content-type"].startswith("text/html")
    assert "https://statline.dev" in browser.text
    assert "<script" not in browser.text
    explicit = portal_client.get("/", headers={"Accept": "application/json"})
    assert explicit.json() == machine.json()
