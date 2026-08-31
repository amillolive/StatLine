from __future__ import annotations

import os
import sys


def main() -> None:
    # Import uvicorn only when launching, so "import statline" stays lightweight
    try:
        import uvicorn
    except ImportError:
        print(
            "Missing dependency: uvicorn. Install with: pip install '.[remote]'\n", file=sys.stderr
        )
        raise

    host = os.getenv("SLAPI_HOST", "127.0.0.1")
    port = int(os.getenv("SLAPI_PORT", "8000"))
    cpu_count = max(1, os.cpu_count() or 1)
    workers = max(1, int(os.getenv("SLAPI_WORKERS", str(min(4, cpu_count)))))
    keep_alive = max(5, int(os.getenv("SLAPI_KEEP_ALIVE", "60")))

    # Your FastAPI app is statline.gateway.http.app:app
    uvicorn.run(
        "statline.gateway.http.app:app",
        host=host,
        port=port,
        reload=False,  # keep False on servers
        workers=workers,
        timeout_keep_alive=keep_alive,
    )
