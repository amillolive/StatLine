# StatLine Portal — rc6

This static Next/React portal presents **Core/SDK**, **App/local tools**, and
**Gateway/API**. It describes v4.0.0rc6 on `next`; v3 on `main` is the stable line.

## Build and run locally

From the extracted repository root, install the source Gateway dependencies:

```sh
python -m pip install -e ".[gateway]"
```

Run `./launch.sh` on Linux/macOS or `.\launch.ps1` on Windows. The portal is at
`http://localhost:3000` and the API listens at `http://127.0.0.1:8000`.
Ctrl+C stops both. An up-to-date prebuilt portal needs no Node tooling to serve,
but a source-only archive or changed frontend source requires Node 22+ and pnpm.
The launcher now installs/builds when needed rather than quietly serving stale pages.

The launcher configures exact CORS origins for localhost and 127.0.0.1 and verifies
health, OpenAPI and scoring preflights. `--check-only` / `-CheckOnly` verifies and exits.
Select ports with `--api-port` / `--portal-port`, or `-ApiPort` / `-PortalPort`.

For existing public hostname forwarding:

```sh
./launch.sh --portal-origin https://statline.dev --api-url https://api.statline.dev
```

```powershell
.\launch.ps1 -PortalOrigin https://statline.dev -ApiUrl https://api.statline.dev
```

The deployment folder includes separate portal/API hostname templates. Runtime
configuration contains public URLs only. The launcher temporarily sets the built
portal API URL and restores the previous setting when stopped.

## Browse and inspect

Public pages require no authentication. Saved EBA examples, docs and downloads
work without contacting the API. Choose **Check API** to contact the configured
service; live scoring needs an approved key under existing Gateway policy.
Browser integration registration has been removed. The previously reported
application-access prompt remains unverified on the deployed service.

The homepage, explorer and playground show engine-produced helped/limited factors
and an expandable input/normalization/weight/profile trace. Influences refer to
the raw sum relative to normalized midpoint 0.5; scores are model-based.
Live readers accept legacy and compact envelopes and show source identity.

Downloads contain one compact API response plus original inputs. Saved docs and
manifests record the source release, fingerprint and generation timestamp.

## Rebuild

Use Node 22.13+, pnpm 11.25.0 and Python 3.11+ with this checkout's dependencies.
Set `STATLINE_PYTHON` to the source virtual-environment interpreter if needed.

```sh
cd portal
pnpm install --frozen-lockfile
pnpm build
pnpm typecheck
pnpm check-generated
```

`pnpm generate` refreshes data, OpenAPI, downloadable results and StatPacks.
Generation compares Python/Gateway/StatPack results and expands compact responses
before checking equality. `check-generated` detects stale fingerprints,
mismatched downloadable contracts and incompatible samples.

Serve `portal/dist/client/` on the product host; point `api.statline.dev` to the
Gateway. Actual DNS, tunnel, access gates and TLS settings need verification in
your deployment. Use the public deployment template for informational pages;
the private Caddy example is explicitly for a private preview.

Read the root `RC6_CHANGES.md` and `RC6_VERIFICATION.md` for migration and checks.

## API connection troubleshooting (local vs deployed)

The portal checks the **browser-configured** API on load, every minute while the tab
is visible, and when you press **Check API**. `Not checked` and `Checking` are not
outages. A healthy local Uvicorn log is not proof that a remotely configured API
is reachable from the browser.

The joint launcher rebuilds the portal automatically when source changed (or the
static bundle is absent). This needs `pnpm` on PATH and Node 22+; it installs
locked dependencies if necessary. If pnpm is unavailable, the launcher reports
how to install and build rather than silently serving stale JavaScript.

On Windows, if PowerShell blocks the unsigned launch script, run it just for
that invocation without changing your system's permanent execution policy:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\\launch.ps1
```

For local development, open `http://localhost:3000` and ensure that the API
address shown on the website is `http://127.0.0.1:8000` (or your chosen port).
Running `--api-url`/`-ApiUrl` points the **browser** elsewhere; the launcher
checks that target separately so a healthy local API cannot mask a broken
remote target. A target serving HTTP without the expected CORS response fails
verification.

For deployed sites, set the allowed production origins **on the hosted Gateway
process before starting Uvicorn** (not just on your development PC):

```sh
export STATLINE_PORTAL_ORIGINS='https://statline.dev,https://www.statline.dev'
python -m uvicorn statline.gateway.http.app:app --host 127.0.0.1 --port 8000
```

Equivalent Windows PowerShell environment variable setup:

```powershell
$env:STATLINE_PORTAL_ORIGINS = 'https://statline.dev,https://www.statline.dev'
python -m uvicorn statline.gateway.http.app:app --host 127.0.0.1 --port 8000
```

The deployed portal's `/statline-config.js` must resolve to
`https://api.statline.dev`; this URL must be publicly reachable over HTTPS.
CORS must allow the exact requesting origin, including `https://www.statline.dev`
when the site is accessed via `www`. These settings allow browser transport;
they **do not** relax any StatLine authentication or device-proof requirement.
