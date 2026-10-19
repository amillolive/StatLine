# Development entrypoints

All local hosting and snapshot refresh behavior belongs to `scripts/serve.py`.

```powershell
.\.venv\Scripts\python.exe .\scripts\serve.py
.\.venv\Scripts\python.exe .\scripts\serve.py --check-only
.\.venv\Scripts\python.exe .\scripts\serve.py --refresh-only
.\.venv\Scripts\python.exe .\scripts\serve.py --refresh
```

`launch.ps1`, `launch.sh`, and `portal/scripts/local.py` are lightweight
compatibility shims. The launcher uses `STATLINE_PORTAL_ORIGINS` and also
adds localhost:3000 / 127.0.0.1:3000; it verifies CORS, the OpenAPI contract,
occupied ports, and the browser-configured API URL. The generated site never
prompts for browser device access or automatically probes APIs.

Snapshots are generated from real source adapters and datasets; do not edit
`portal/data/eba-snapshot.json` by hand. After refresh commit the updated
source-controlled JSON and rebuilt public downloads as appropriate.
