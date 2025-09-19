# StatLine

**StatLine™ — weighted player scoring, efficiency modeling, and tooling.**

“StatLine” is a trademark of StatLine LLC (in formation), registration pending.
Source code is licensed under the GNU Affero General Public License v3 (see `LICENSE`).
Brand, name, and logo are **not** covered by the AGPL (see `TRADEMARKS.md`).

---

## What is StatLine?

StatLine is an **adapter‑driven analytics framework** with an optional **remote API (SLAPI)** and a **Discord app (SLcord)**. It:

* normalizes raw game stats,
* computes per‑metric scores (clamps, inversion, ratios),
* aggregates into buckets and applies weight presets (e.g., **pri**, **mvp**, role weights),
* exposes a clean **CLI** and **Python library API**,
* optionally ingests **Google Sheets** and caches mapped rows,
* integrates with **SLAPI** for secure, multi‑client deployments,
* and (new) stabilizes **PRI** to a **55–99** normalized range.

Supported Python: **3.10 – 3.13** (CI: Linux, macOS, Windows)

---

## What’s new (v2.1.0)

* **Keys & Auth:** REGKEY + API Access Tokens (tokens correspond to an active REGKEY).
* **PRI normalization:** output scale locked to **55–99** for clearer tiers and saner UX.
* **Adapters:** tightened demo adapter + docs; versioned schemas.
* **Roadmap (ships in v2.1.0):**

  * Batch processing **filters** (by position, games played, and adapter‑defined stat predicates)
  * **Output toggles** (e.g., show weights, hide `pri_raw`, include per‑metric deltas)
  * **Percentiles** (dataset or sample contextualization)
* **SLcord:** official Discord client (thin shim on SLAPI) with simple commands.

---

## Install

Base install:

```bash
pip install statline
```

With extras:

```bash
# Google Sheets ingestion
pip install "statline[sheets]"

# Developer tools (linters, types, tests)
pip install -e ".[dev]"
```

---

## CLI Basics

The console script is `statline` (also callable via `python -m statline.cli`).

```bash
statline --help
python -m statline.cli --help
```

### Common commands

```bash
# list bundled adapters
statline adapters

# interactive scoring REPL (banner + timing are enabled by default)
statline interactive

# score a file of rows (CSV or YAML understood by the adapter)
statline score --adapter example_game stats.csv

# write results to a CSV instead of stdout
statline score --adapter example_game stats.csv --out results.csv
```

Subcommands:

* `adapters` — show available adapter keys & aliases
* `interactive` — guided prompts + timing table
* `export-csv` — export cached mapped metrics (when using cache/Sheets flow)
* `score` — batch‑score a CSV/YAML through an adapter

When the CLI runs, you’ll see a banner (to confirm proper install):

```diff
=== StatLine — Adapter-Driven Scoring ===
```

Enable per‑stage timing via env (e.g., 14ms):

```bash
STATLINE_TIMING=1 statline score --adapter rbw5 stats.csv
```

---

## Python Library (Local Core)

```python
from statline.core.scoring import calculate_pri_single, calculate_pri_batch
from statline.core.adapters import load_adapter

adapter = load_adapter("example_game")
row = {"stat1_total": 24, "rounds_played": 15, "stat2_numer": 9, "stat2_denom": 20,
       "stat4_good": 18, "stat4_total": 22, "stat3_count": 7, "mistakes": 2}

result = calculate_pri_single(adapter=adapter, row=row, weights="pri",
                              output={"hide_pri_raw": False, "show_weights": True})
print(result.pri)     # normalized 55–99
print(result.details) # per-bucket breakdown
```

Batch:

```python
rows = [row, {...}, {...}]
res = calculate_pri_batch(adapter=adapter, rows=rows,
                          filters={"games_played_gte": 10},
                          output={"percentiles": True})  # v2.1.0 feature
```

---

## Remote API (SLAPI)

Use SLAPI for remote/scaled workflows. SLAPI validates **REGKEY** or **API Access Token** on every request. **Revocation** invalidates both.

### Auth headers (choose one)

```http
Authorization: Bearer <API_ACCESS_TOKEN>
# — or —
X-SLAPI-REGKEY: <REGKEY>
```

### Discover adapters

```bash
curl -s https://api.slapi.dev/v1/adapters \
  -H "Authorization: Bearer $SLAPI_TOKEN"
```

### Score a single row

```bash
curl -s https://api.slapi.dev/v1/score/row \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $SLAPI_TOKEN" \
  -d '{
    "adapter": "basketball.demo",
    "row": {
      "pts": 27, "reb": 8, "ast": 6, "tov": 2, "stl": 1, "blk": 1,
      "fgm": 10, "fga": 18, "ftm": 5, "fta": 6, "tpm": 2, "tpa": 6, "min": 33
    },
    "output": {"show_weights": true, "hide_pri_raw": false}
  }'
```

### Batch (v2.1.0 filters & percentiles)

```bash
curl -s https://api.slapi.dev/v1/score/batch \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $SLAPI_TOKEN" \
  -d '{
    "adapter": "basketball.demo",
    "rows": [...],
    "filters": {"position": ["SG","SF"], "games_played_gte": 10,
                 "stat_where": [{"stat":"tov","op":"<=","value":3}]},
    "output": {"percentiles": true, "hide_pri_raw": true}
  }'
```

**Error model (example):**

```json
{"error": {"code": "REGKEY_REVOKED", "message": "Key revoked", "trace_id": "b4c2a9"}}
```

Suggested codes: `UNAUTHORIZED`, `FORBIDDEN`, `REGKEY_MISSING`, `REGKEY_REVOKED`, `RATE_LIMITED`, `ADAPTER_NOT_FOUND`, `INVALID_ROW`, `TRANSFORM_UNSAFE`.

---

## SLcord (Discord App)

SLcord is a thin client over SLAPI—**no business logic duplication**.

**Example surface:**

* `/pri row` — 1 row → PRI + percentile (+ weights if opted)
* `/pri batch` — CSV upload → filtered + ranked → top N
* `/adapter info` — required fields, transforms, weights
* `/health` — latency + rate‑limit remaining

**Key handling:** bind a server‑scoped key; avoid DMs for credentials. Provide a simple setup command to store the key securely (e.g., guild‑scoped secret).

---

## Input Formats

StatLine reads **CSV** or **YAML**. Columns/keys must match adapter expectations.

### CSV

* First row is the header.
* Each subsequent row is an entity (player).
* Provide raw fields your adapter maps (e.g., `pts, ast, fgm, fga, tov`).

```csv
display_name,team,pts,ast,orpg,drpg,stl,blk,fgm,fga,tov
JordanRed,RED,27.3,4.8,1.2,3.6,1.9,0.7,10.2,22.1,2.1
```

### Example adapter (YAML)

Adapters define schema for raw inputs, buckets, weights, and derived metrics. Place in `statline/core/adapters/defs/`.

```yaml
key: example_game
version: 0.2.0
aliases: [ex, sample]
title: Example Game

dimensions:
  map:   { values: [MapA, MapB, MapC] }
  side:  { values: [Attack, Defense] }
  role:  { values: [Carry, Support, Flex] }
  mode:  { values: [Pro, Ranked, Scrim] }

buckets:
  scoring: {}
  impact: {}
  utility: {}
  survival: {}
  discipline: {}

metrics:
  - { key: stat3_count, bucket: utility,    clamp: [0, 50],  source: { field: stat3_count } }
  - { key: mistakes,    bucket: discipline, clamp: [0, 25], invert: true, source: { field: mistakes } }

efficiency:
  - key: stat1_per_round
    bucket: scoring
    clamp: [0.00, 2.00]
    min_den: 5
    make: "stat1_total"
    attempt: "rounds_played"

  - key: stat2_rate
    bucket: impact
    clamp: [0.00, 1.00]
    min_den: 10
    make: "stat2_numer"
    attempt: "stat2_denom"

  - key: stat4_quality
    bucket: survival
    clamp: [0.00, 1.00]
    min_den: 5
    make: "stat4_good"
    attempt: "stat4_total"

weights:
  pri:
    scoring:    0.30
    impact:     0.28
    utility:    0.16
    survival:   0.16
    discipline: 0.10
  mvp:
    scoring:    0.34
    impact:     0.30
    utility:    0.12
    survival:   0.14
    discipline: 0.10
  support:
    scoring:    0.16
    impact:     0.18
    utility:    0.40
    survival:   0.16
    discipline: 0.10

penalties:
  pri:     { discipline: 0.10 }
  mvp:     { discipline: 0.12 }
  support: { discipline: 0.08 }

sniff:
  require_any_headers:
    [stat1_total, rounds_played, stat2_numer, stat2_denom, stat4_good, stat4_total, stat3_count, mistakes]
```

See `HOWTO.md` for adapter authoring.

---

## Versioning & Stability

* **Adapters:** semantic (`MAJOR.MINOR.PATCH`). Breaking schema changes → new **MAJOR**.
* **PRI scale:** fixed to **55–99**; any change happens only via a new adapter version.
* **SLAPI:** deprecations target **≥60 days** notice unless security requires faster action.

---

## Security & Legal

* **Revocation:** We may revoke keys for AUP violations; revocation invalidates both REGKEY and any derived API tokens immediately.
* **ToS / Privacy / AUP:** see `/legal` links below.
* **Trademark:** "StatLine" and associated logos are trademarks; see `TRADEMARKS.md`.

**Links:**

* Terms of Service — `/legal/tos`
* Privacy Policy — `/legal/privacy`
* Acceptable Use Policy — `/legal/aup`
* Contributor License Agreement — `CLA.md`
* License — `LICENSE`
* Trademark Policy — `TRADEMARKS.md`

---

## Quick FAQ

**Is the CLI required?** No. Use the Python API directly or the remote SLAPI.

**Can I use my own game?** Yes—write an adapter; any game with a box score + valid datasheet can work.

**Do I need env vars?** No. You can pass `--token`/`--regkey` flags in clients. For ops, env vars are supported.

**How are percentiles computed?** Per dataset/sample window defined in the request or adapter context (v2.1.0 feature).

**Discord support?** SLcord surfaces row/batch scoring, adapter info, and health checks. Guild‑scoped key binding only.
