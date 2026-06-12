from __future__ import annotations

import os
import sys


def main() -> None:
    # Import uvicorn only when launching, so "import statline" stays lightweight
    try:
        import uvicorn
    except Exception:
        print("Missing dependency: uvicorn. Install with: pip install '.[api]'\n", file=sys.stderr)
        raise

    host = os.getenv("SLAPI_HOST", "127.0.0.1")
    port = int(os.getenv("SLAPI_PORT", "8080"))

    # Your FastAPI app is statline.slapi.app:app
    uvicorn.run(
        "statline.slapi.app:app",
        host=host,
        port=port,
        reload=False,  # keep False on servers
        workers=1,  # gunicorn handles workers in production; CLI should stay simple
    )


if __name__ == "__main__":
    main()
