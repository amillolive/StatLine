"""Preview static portal files alongside StatLine's existing local FastAPI."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def available_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Port must be an integer.") from error
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("Port must be between 1 and 65535.")
    return port


def stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def interrupted(_signal: int, _frame: object) -> None:
    raise KeyboardInterrupt


def static_build_outdated(portal: Path) -> str | None:
    """Do not silently serve a stale React bundle after source changes."""
    static = portal / "dist/client"
    required = [static / "index.html", static / "docs/index.html"]
    if any(not path.is_file() for path in required):
        return "The compiled static portal is missing."
    built_at = (static / "index.html").stat().st_mtime_ns
    source_roots = ["app", "components", "lib", "data", "scripts", "public", "vendor"]
    extensions = {".ts", ".tsx", ".js", ".mjs", ".css", ".json", ".svg"}
    candidates = (
        path
        for folder in source_roots
        for path in (portal / folder).rglob("*")
        if path.is_file() and path.suffix in extensions
    )
    configuration = [
        portal / name
        for name in ("package.json", "pnpm-lock.yaml", "next.config.ts", "vite.config.ts")
    ]
    for path in (*candidates, *configuration):
        if path.is_file() and path.stat().st_mtime_ns > built_at:
            return (
                f"Portal source changed after the last build: {path.relative_to(portal).as_posix()}"
            )
    return None


def ensure_portal_build(
    portal: Path, python_path: str, env: dict[str, str], parser: argparse.ArgumentParser
) -> None:
    reason = static_build_outdated(portal)
    if not reason:
        return
    print(reason, flush=True)
    pnpm = shutil.which("pnpm") or shutil.which("pnpm.cmd")
    if not pnpm:
        parser.error(
            "The portal needs rebuilding, but pnpm is not on PATH. "
            "Install Node.js 22+ and pnpm, then in the portal folder run "
            "'pnpm install --frozen-lockfile' and 'pnpm build' before launching again."
        )
    if not (portal / "node_modules/vinext/dist/cli.js").is_file():
        print("Installing the locked portal dependencies…", flush=True)
        try:
            installed = subprocess.run(
                [pnpm, "install", "--frozen-lockfile"], cwd=portal, env=env, check=False
            )
        except OSError as error:
            parser.error(f"Cannot install portal dependencies: {error}")
        if installed.returncode != 0:
            parser.error("pnpm install failed. Fix the errors above before restarting.")
    print("Rebuilding the portal to include the latest API connection fixes…", flush=True)
    try:
        built = subprocess.run(
            [pnpm, "build"],
            cwd=portal,
            env={**env, "STATLINE_PYTHON": python_path},
            check=False,
        )
    except OSError as error:
        parser.error(f"Cannot rebuild the portal: {error}")
    if built.returncode != 0 or static_build_outdated(portal):
        parser.error(
            "The portal build failed or did not produce the required pages. Fix the build errors above."
        )


def verify_browser_api(url: str, portal_url: str) -> None:
    """Check the URL actually written into the browser config, not only localhost."""
    try:
        for path in ("/v4/health", "/openapi.json"):
            with urlopen(
                Request(url + path, headers={"Origin": portal_url}), timeout=6
            ) as response:
                payload = json.load(response)
                if response.headers.get("Access-Control-Allow-Origin") != portal_url:
                    raise ValueError(
                        f"{url + path} does not allow the browser origin {portal_url}."
                    )
                if path == "/v4/health" and (
                    payload.get("ok") is not True or not isinstance(payload.get("version"), str)
                ):
                    raise ValueError(
                        "The configured browser API did not return the expected health contract."
                    )
                if path == "/openapi.json" and not isinstance(payload.get("paths"), dict):
                    raise ValueError(
                        "The configured browser API did not return a usable OpenAPI contract."
                    )
        with urlopen(
            Request(
                url + "/v4/score",
                method="OPTIONS",
                headers={
                    "Origin": portal_url,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "authorization,content-type",
                },
            ),
            timeout=6,
        ) as response:
            if response.headers.get("Access-Control-Allow-Origin") != portal_url:
                raise ValueError(
                    "The configured browser API does not permit scoring preflight from "
                    + portal_url
                )
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise ValueError(f"Configured browser API {url} could not be verified: {error}") from error


def main() -> int:
    portal = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=os.getenv("STATLINE_SOURCE", str(portal.parent)))
    parser.add_argument("--python", dest="python_path", default=os.getenv("STATLINE_PYTHON"))
    parser.add_argument("--api-port", type=available_port, default=8000)
    parser.add_argument("--portal-port", type=available_port, default=3000)
    parser.add_argument(
        "--api-url", help="API URL the browser should use; defaults to the local API."
    )
    parser.add_argument(
        "--portal-origin",
        action="append",
        default=[],
        help="Additional exact origins, e.g. https://statline.dev.",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Launch, verify HTTP and CORS, then stop both services.",
    )
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, interrupted)
    source = Path(args.source).expanduser().resolve()
    if not (source / "statline/gateway/http/portal.py").is_file():
        parser.error("The source checkout needs the existing Gateway CORS patch.")
    static = portal / "dist/client"
    venv_python = source / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python_path = args.python_path or (
        str(venv_python) if venv_python.is_file() else sys.executable
    )
    api_url = f"http://127.0.0.1:{args.api_port}"
    portal_url = f"http://localhost:{args.portal_port}"
    browser_api_url = (args.api_url or api_url).rstrip("/")
    parsed_api = urlsplit(browser_api_url)
    if (
        parsed_api.scheme not in {"http", "https"}
        or not parsed_api.netloc
        or parsed_api.username
        or parsed_api.password
        or parsed_api.query
        or parsed_api.fragment
    ):
        parser.error(
            "--api-url must be a plain HTTP(S) service URL without credentials, query or fragment."
        )
    origins = [
        value.strip()
        for value in os.getenv("STATLINE_PORTAL_ORIGINS", "").split(",")
        if value.strip()
    ]
    origins.extend([portal_url, f"http://127.0.0.1:{args.portal_port}", *args.portal_origin])
    env = os.environ.copy()
    env["STATLINE_PORTAL_ORIGINS"] = ",".join(dict.fromkeys(origins))
    env["PYTHONPATH"] = str(source) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    ensure_portal_build(portal, python_path, env, parser)

    # Check the interpreter resolves this checkout before starting the real Gateway.
    try:
        probe = subprocess.run(
            [python_path, "-c", "import statline, fastapi, uvicorn; print(statline.__file__)"],
            cwd=source,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        parser.error(
            f"Cannot run {python_path}: {error}. Select the working interpreter with --python."
        )
    if probe.returncode:
        parser.error(
            f'{python_path} needs StatLine\'s Gateway dependencies. Install with: python -m pip install -e ".[gateway]".\n{probe.stderr.strip()}'
        )
    imported = Path(probe.stdout.strip()).resolve()
    if imported != (source / "statline/__init__.py").resolve():
        parser.error(f"The interpreter loaded a different StatLine checkout: {imported}")
    for port in [args.api_port, args.portal_port]:
        with socket.socket() as connection:
            connection.settimeout(0.2)
            if connection.connect_ex(("127.0.0.1", port)) == 0:
                parser.error(
                    f"Port {port} is already in use. Stop the old statline serve/preview command with Ctrl+C, then retry."
                )
    if args.api_port == args.portal_port:
        parser.error("The API and static preview need different ports.")

    config = static / "statline-config.js"
    previous = config.read_bytes() if config.exists() else None
    processes: list[subprocess.Popen[bytes]] = []
    try:
        config.write_text(
            "window.STATLINE_PUBLIC_CONFIG = " + json.dumps({"apiUrl": browser_api_url}) + ";\n",
            encoding="utf-8",
        )
        print(f"StatLine source: {source}", flush=True)
        print(f"Python: {python_path}", flush=True)
        # Python's generic static file server, bound to this computer only.
        preview = subprocess.Popen(
            [
                python_path,
                "-m",
                "http.server",
                str(args.portal_port),
                "--bind",
                "127.0.0.1",
                "--directory",
                str(static),
            ],
            cwd=portal,
        )
        processes.append(preview)
        print(
            f"StatLine portal: {portal_url}\nDocs: {portal_url}/docs/\nCtrl+C stops both local processes.",
            flush=True,
        )
        # Existing StatLine ASGI app; no portal endpoints or auth replacements.
        gateway = subprocess.Popen(
            [
                python_path,
                "-m",
                "uvicorn",
                "statline.gateway.http.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.api_port),
                "--workers",
                "1",
            ],
            cwd=source,
            env=env,
        )
        processes.append(gateway)
        deadline = time.monotonic() + 20
        verified = False
        while time.monotonic() < deadline and gateway.poll() is None:
            try:
                request = Request(api_url + "/v4/health", headers={"Origin": portal_url})
                with urlopen(request, timeout=1) as response:
                    health = json.load(response)
                    allowed_origin = response.headers.get("Access-Control-Allow-Origin")
                if health.get("ok") is not True or allowed_origin != portal_url:
                    print(
                        "The Gateway responded but its health/CORS contract did not match this portal.",
                        file=sys.stderr,
                    )
                    break
                with urlopen(
                    Request(api_url + "/openapi.json", headers={"Origin": portal_url}), timeout=3
                ) as response:
                    contract = json.load(response)
                    if response.headers.get(
                        "Access-Control-Allow-Origin"
                    ) != portal_url or not isinstance(contract.get("paths"), dict):
                        print(
                            "The live OpenAPI contract was not readable for this portal origin.",
                            file=sys.stderr,
                        )
                        break
                for origin in dict.fromkeys(origins):
                    preflight = Request(
                        api_url + "/v4/score",
                        method="OPTIONS",
                        headers={
                            "Origin": origin,
                            "Access-Control-Request-Method": "POST",
                            "Access-Control-Request-Headers": "authorization,content-type",
                        },
                    )
                    with urlopen(preflight, timeout=3) as response:
                        if response.headers.get(
                            "Access-Control-Allow-Origin"
                        ) != origin.strip().rstrip("/"):
                            raise ValueError("Scoring preflight did not allow " + origin)
                with urlopen(portal_url + "/", timeout=3) as response:
                    if "text/html" not in response.headers.get("Content-Type", ""):
                        raise ValueError("Portal root is not HTML")
                if browser_api_url != api_url:
                    # Without this, launch --api-url can report success for a
                    # local Gateway while the browser points to a broken host.
                    verify_browser_api(browser_api_url, portal_url)
                print(
                    f"Existing Gateway {health.get('version', 'unknown')}: {api_url} (health, OpenAPI and scoring preflight CORS verified)",
                    flush=True,
                )
                if browser_api_url != api_url:
                    print(f"Browser-configured API also verified: {browser_api_url}", flush=True)
                verified = True
                break
            except (URLError, TimeoutError):
                time.sleep(0.2)
            except (ValueError, OSError) as error:
                print(f"API verification failed: {error}", file=sys.stderr)
                break
        if not verified:
            print(
                "The API is unavailable; saved results, docs and downloads remain available in the local portal.",
                file=sys.stderr,
            )
        if args.check_only:
            return 0 if verified else 1
        reported_stop = not verified
        while preview.poll() is None:
            if not reported_stop and gateway.poll() is not None:
                print(
                    "The API stopped; the static portal remains available. Live actions will show Unavailable.",
                    flush=True,
                )
                reported_stop = True
            time.sleep(0.25)
        print("The static preview stopped; stopping the Gateway.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        for process in reversed(processes):
            stop(process)
        if previous is None:
            config.unlink(missing_ok=True)
        else:
            config.write_bytes(previous)


if __name__ == "__main__":
    raise SystemExit(main())
