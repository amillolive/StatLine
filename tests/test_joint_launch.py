"""Real process verification of joint launch, preflight checks, and cleanup."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "portal/dist/client/index.html"
pytestmark = pytest.mark.skipif(
    not STATIC.exists(), reason="Joint launch needs the built static portal"
)


def ports() -> tuple[int, int]:
    with socket.socket() as first, socket.socket() as second:
        first.bind(("127.0.0.1", 0))
        second.bind(("127.0.0.1", 0))
        return first.getsockname()[1], second.getsockname()[1]


def launch_environment(tmp_path: Path) -> dict[str, str]:
    return {
        **os.environ,
        "STATLINE_PYTHON": sys.executable,
        "STATLINE_DATA_DIR": str(tmp_path / "db"),
        "STATLINE_SECRETS_DIR": str(tmp_path / "secrets"),
        "STATLINE_PORTAL_ORIGINS": "",
    }


def command(api_port: int, portal_port: int) -> list[str]:
    prefix = (
        ["bash", str(ROOT / "launch.sh")]
        if os.name == "posix"
        else [sys.executable, str(ROOT / "scripts/serve.py")]
    )
    return [*prefix, "--api-port", str(api_port), "--portal-port", str(portal_port)]


def test_joint_launch_verifies_custom_origins_and_restores_configuration(tmp_path: Path) -> None:
    api_port, portal_port = ports()
    config = ROOT / "portal/dist/client/statline-config.js"
    before = config.read_bytes()
    # Verify custom origins against the freshly launched local gateway.
    # Public API URLs are external dependencies and can legitimately return 403.
    # External browser-target verification is covered with a fake HTTP server.
    result = subprocess.run(
        [
            *command(api_port, portal_port),
            "--check-only",
            "--portal-origin",
            "https://statline.dev",
            "--api-url",
            f"http://127.0.0.1:{api_port}",
        ],
        cwd=tmp_path,
        env=launch_environment(tmp_path),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "scoring preflight CORS verified" in result.stdout
    assert config.read_bytes() == before
    for port in (api_port, portal_port):
        with socket.socket() as connection:
            assert connection.connect_ex(("127.0.0.1", port)) != 0


def test_joint_launch_rejects_occupied_ports_without_starting_processes(tmp_path: Path) -> None:
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        api_port = occupied.getsockname()[1]
        _, portal_port = ports()
        result = subprocess.run(
            [*command(api_port, portal_port), "--check-only"],
            cwd=tmp_path,
            env=launch_environment(tmp_path),
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 2
        assert "already in use" in result.stderr


@pytest.mark.skipif(
    os.name != "posix", reason="POSIX signal cleanup; Windows console execution is separate"
)
def test_joint_launch_sigterm_stops_both_children(tmp_path: Path) -> None:
    api_port, portal_port = ports()
    config = ROOT / "portal/dist/client/statline-config.js"
    before = config.read_bytes()
    process = subprocess.Popen(
        command(api_port, portal_port),
        cwd=tmp_path,
        env=launch_environment(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        ready = False
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and process.poll() is None:
            try:
                with urlopen(f"http://127.0.0.1:{api_port}/v4/health", timeout=0.5) as response:
                    ready = response.status == 200
                if ready:
                    break
            except (URLError, TimeoutError):
                time.sleep(0.05)
        assert ready
        process.send_signal(signal.SIGTERM)
        output, _ = process.communicate(timeout=15)
        assert process.returncode == 0, output
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=15)
    assert config.read_bytes() == before
    for port in (api_port, portal_port):
        with socket.socket() as connection:
            assert connection.connect_ex(("127.0.0.1", port)) != 0
