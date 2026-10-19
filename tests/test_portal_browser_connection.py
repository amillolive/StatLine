"""Regression tests for stale frontend builds and browser-target API checks."""

from __future__ import annotations

import importlib.util
import json
import os
import threading
from collections.abc import Generator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/serve.py"
spec = importlib.util.spec_from_file_location("statline_portal_local", SCRIPT)
assert spec is not None and spec.loader is not None
local = importlib.util.module_from_spec(spec)
spec.loader.exec_module(local)


def test_build_age_detects_missing_or_stale_static_files(tmp_path: Path) -> None:
    portal = tmp_path / "portal"
    (portal / "app").mkdir(parents=True)
    src = portal / "app/page.tsx"
    src.write_text("export default function Home() { return null; }")
    assert "missing" in local.static_build_outdated(portal)
    (portal / "dist/client/docs").mkdir(parents=True)
    index = portal / "dist/client/index.html"
    docs = portal / "dist/client/docs/index.html"
    index.write_text("<html></html>")
    docs.write_text("<html></html>")
    os.utime(src, ns=(1_000_000_000, 1_000_000_000))
    os.utime(index, ns=(2_000_000_000, 2_000_000_000))
    assert local.static_build_outdated(portal) is None
    os.utime(src, ns=(3_000_000_000, 3_000_000_000))
    assert "app/page.tsx" in local.static_build_outdated(portal)


@contextmanager
def fake_gateway(cors: bool = True) -> Generator[str, None, None]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/v4/health":
                payload = {"ok": True, "version": "4.0.0rc6"}
            elif self.path == "/openapi.json":
                payload = {
                    "openapi": "3.1.0",
                    "info": {"title": "StatLine", "version": "4.0.0rc6"},
                    "paths": {},
                }
            else:
                self.send_error(404)
                return
            self.send_response(200)
            if cors:
                self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))

        def do_OPTIONS(self) -> None:
            self.send_response(200)
            if cors:
                self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        worker.join(timeout=3)
        server.server_close()


def test_configured_browser_api_must_allow_actual_origin() -> None:
    with fake_gateway(cors=True) as address:
        local.verify_browser_api(address, "http://localhost:3000")
    with (
        fake_gateway(cors=False) as address,
        pytest.raises(ValueError, match="does not allow the browser origin"),
    ):
        local.verify_browser_api(address, "http://localhost:3000")
