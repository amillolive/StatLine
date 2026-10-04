# StatLine HOWTO

This guide shows the practical workflows for **StatLine v4.0.0rc6**: installing the right variant, scoring locally, using SLAPI, writing adapters, and preparing a release.

---

## 1. Choose the right install variant

### Core

Use this for the Python scoring API, adapters, datasets, and StatPack runtime without App/Gateway dependencies.

```bash
pip install statline
```

### App

Use this for the canonical CLI/application surface and Textual StatLine OS.

```bash
pip install "statline[app]"
```

### Gateway

Use this for Core + authenticated SLAPI/API/server capabilities without requiring the App/CLI dependency set.

```bash
pip install "statline[gateway]"
```

### All runtime capabilities

Use this when App and Gateway should coexist in the same runtime.

```bash
pip install "statline[all]"
```

### Development

Use this from the canonical monorepo checkout. Dev includes Core + App + Gateway plus testing, typing, docs, packaging, and release tooling.

```bash
python -m venv .venv

# Linux/macOS
source .venv/bin/activate

# Windows PowerShell
# .\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

---

## 2. Sync component mirrors

Core, App, and Gateway are developed in the StatLine monorepo. After committing on `main`, `next`, or `dev`, publish the matching subtree mirrors with either helper:

```bash
./scripts/sync-components.sh
```

```powershell
.\scripts\sync-components.ps1
```

Feature branches are intentionally not mirrored. GitHub also performs the same synchronization after CI succeeds on `main`, `next`, or `dev` when `STATLINE_COMPONENT_SYNC_TOKEN` is configured.

---

## 3. Verify the install

Core-only install:

```bash
python -c "import statline; print(statline.__version__)"
```

App install:

```bash
statline --version
statline --mode local system status
statline --mode local adapter list
```

Use `--mode local` when you want zero SLAPI/network behavior. Use `--mode remote` when a Gateway/SLAPI server must be reachable and authenticated.

---

## 4. Inspect an adapter before scoring

Start with a current adapter such as `eba.players`. Deprecated schemas are hidden from discovery and must be addressed by explicit local YAML path.

```bash
statline --mode local adapter spec eba.players
statline --mode local adapter inputs eba.players
statline --mode local adapter metrics eba.players
statline --mode local adapter weights eba.players
statline --mode local adapter filters eba.players
```

Detect adapters from a file:

```bash
statline --mode local adapter sniff --file EBA_Elevate302/eba_s1_players.csv
```

Refresh the local adapter registry after changing YAML files:

```bash
statline --mode local adapter refresh
```

---

## 5. Score raw rows from CSV

From a source checkout:

```bash
statline --mode local score \
  --adapter statline/core/adapters/schemas/deprecated/demo.yaml \
  DEMO/demo.csv \
  --fmt table \
  --limit 10
```

Include all detected profile columns and client-side percentiles:

```bash
statline --mode local score \
  --adapter statline/core/adapters/schemas/deprecated/demo.yaml \
  DEMO/demo.csv \
  --fmt table \
  --profile all \
  --percentile
```

Write JSON:

```bash
statline --mode local score \
  --adapter statline/core/adapters/schemas/deprecated/demo.yaml \
  DEMO/demo.csv \
  --fmt json \
  --pretty \
  --out results.json
```

Write CSV:

```bash
statline --mode local score \
  --adapter statline/core/adapters/schemas/deprecated/demo.yaml \
  DEMO/demo.csv \
  --fmt csv \
  --out results.csv
```

Available output formats for `score`:

| Format  | Use                            |
| ------- | ------------------------------ |
| `table` | Human-readable terminal table. |
| `md`    | Markdown table.                |
| `csv`   | Spreadsheet-friendly output.   |
| `json`  | JSON array.                    |
| `jsonl` | One JSON object per line.      |

> On Windows PowerShell, either enter multiline examples on one line or use PowerShell's backtick continuation character instead of `\`.

---

## 6. Use custom weights

Use an adapter-defined preset:

```bash
statline --mode local score \
  --adapter statline/core/adapters/schemas/deprecated/demo.yaml \
  DEMO/demo.csv \
  --weights-preset pri
```

Use a YAML weight override file:

```yaml
# weights.yaml
aefg: 0.30
tov_eff: 0.15
two_way: 0.10
vers: 0.10
ppg: 0.05
rpg: 0.05
stocks: 0.05
helios: 0.05
handle: 0.05
effloor: 0.05
offball: 0.05
```

Then run:

```bash
statline --mode local score \
  --adapter statline/core/adapters/schemas/deprecated/demo.yaml \
  DEMO/demo.csv \
  --weights weights.yaml
```

Normalize arbitrary weights:

```bash
statline --mode local tools weights normalize aefg=3 tov_eff=2 two_way=1
```

Resolve weights against an adapter:

```bash
statline --mode local tools weights resolve --adapter statline/core/adapters/schemas/deprecated/demo.yaml --preset pri
statline --mode local tools weights resolve --adapter statline/core/adapters/schemas/deprecated/demo.yaml --preset pri --override aefg=0.35
```

---

## 7. Map raw data without scoring

Mapping is useful when you want to check whether an adapter is reading your columns correctly.

Map one row:

```bash
statline --mode local tools map row \
  --adapter statline/core/adapters/schemas/deprecated/demo.yaml \
  --set name="Example Player" \
  --set ppg=24.5 \
  --set apg=6.2 \
  --set orpg=1.0 \
  --set drpg=4.0 \
  --set spg=1.5 \
  --set bpg=0.7 \
  --set tov=2.1 \
  --set fgm=9.2 \
  --set fga=18.4 \
  --set win=12 \
  --set loss=8
```

Map a file:

```bash
statline --mode local tools map batch \
  --adapter statline/core/adapters/schemas/deprecated/demo.yaml \
  DEMO/demo.csv \
  --fmt json \
  --out mapped.json
```

---

## 8. Score already-mapped rows

Use `calc` when your input rows already contain adapter metric keys instead of raw source fields.

Score one mapped row:

```bash
statline --mode local tools calc row \
  --adapter statline/core/adapters/schemas/deprecated/demo.yaml \
  --set ppg=24.5 \
  --set apg=6.2 \
  --set spg=1.5 \
  --set bpg=0.7 \
  --set tov=2.1 \
  --set fgm=9.2 \
  --set fga=18.4 \
  --set orpg=1.0 \
  --set drpg=4.0 \
  --set win=12 \
  --set loss=8
```

Score mapped rows from a file:

```bash
statline --mode local tools calc batch mapped.json --adapter statline/core/adapters/schemas/deprecated/demo.yaml --fmt json
```

---

## 9. Use StatLine from Python

### List adapters and datasets

```python
from statline import list_adapters, list_datasets

print(list_adapters())
print(list_datasets())
```

### Load and score a bundled dataset

```python
from statline import load_dataset, score

rows = load_dataset("DEMO/demo", limit=10)
results = score(
    "statline/core/adapters/schemas/deprecated/demo.yaml", rows, mode="batch", weights="pri"
)

for result in results:
    print(result["pri"], result["pri_raw"])
```

### Score a single row

```python
from statline import score_row

row = {
    "name": "Example Player",
    "ppg": 24.5,
    "apg": 6.2,
    "orpg": 1.0,
    "drpg": 4.0,
    "spg": 1.5,
    "bpg": 0.7,
    "tov": 2.1,
    "fgm": 9.2,
    "fga": 18.4,
    "win": 12,
    "loss": 8,
}

result = score_row("statline/core/adapters/schemas/deprecated/demo.yaml", row, weights="pri")
print(result)
```

### Map before scoring

```python
from statline import map_row, score_row

raw = {
    "ppg": 24.5,
    "apg": 6.2,
    "orpg": 1.0,
    "drpg": 4.0,
    "spg": 1.5,
    "bpg": 0.7,
    "tov": 2.1,
    "fgm": 9.2,
    "fga": 18.4,
    "win": 12,
    "loss": 8,
}

mapped = map_row("statline/core/adapters/schemas/deprecated/demo.yaml", raw)
result = score_row("statline/core/adapters/schemas/deprecated/demo.yaml", raw)
```

---

## 10. Run SLAPI locally

Install the Gateway capability first:

```bash
pip install "statline[gateway]"
```

Start the API server through the dedicated Gateway entry point:

```bash
SLAPI_HOST=127.0.0.1 SLAPI_PORT=8000 SLAPI_WORKERS=2 slapi
```

`SLAPI_WORKERS` defaults to the available CPU count capped at 4. SLAPI keeps HTTP connections alive for 60 seconds by default; tune that with `SLAPI_KEEP_ALIVE` when persistent clients need a different idle window.

If you want the App CLI to manage the server, install both runtime capabilities:

```bash
pip install "statline[all]"
statline --mode local serve --host 127.0.0.1 --port 8000 --workers 2
```

Point the CLI at the server (requires App or All):

```bash
export SLAPI_URL="http://127.0.0.1:8000"
statline --mode remote system status
```

On Windows PowerShell:

```powershell
$env:SLAPI_URL = "http://127.0.0.1:8000"
statline --mode remote system status
```

Health check endpoint:

```bash
curl http://127.0.0.1:8000/v4/health
```

Interactive docs are available at:

```text
http://127.0.0.1:8000/docs
http://127.0.0.1:8000/redoc
```

---

## 11. Enroll a device and claim an API key

SLAPI protects private endpoints with device proof plus API key authentication. A typical flow is:

```bash
statline auth device-init
statline auth enroll --token reg_... --user your-handle --email you@example.com
```

After an admin approves the enrollment:

```bash
statline auth apikey-request --owner laptop
```

After an admin approves the API-key request:

```bash
statline auth apikey-requests
statline auth apikey-claim --request-id REQUEST_ID
statline auth whoami
```

Useful status commands:

```bash
statline auth status
statline auth device
statline auth apikeys
statline system status
```

Admin and moderator commands require the appropriate scopes.

---

## 12. Build an adapter

Adapters live in:

```text
statline/core/adapters/defs/<adapter>.yaml
```

A minimal adapter needs:

* `key`
* `version`
* `buckets`
* `metrics`
* `weights` or a default uniform PRI profile

Example:

```yaml
key: sample_game
version: 1.0.0
title: Sample Game
aliases: [sample]

buckets:
  scoring: {}
  creation: {}
  defense: {}
  discipline: {}

metrics:
  - key: points
    bucket: scoring
    clamp: [0, 40]
    source: { field: points }

  - key: assists
    bucket: creation
    clamp: [0, 15]
    source: { field: assists }

  - key: stocks
    bucket: defense
    clamp: [0, 6]
    source: { expr: "steals + blocks" }

  - key: turnovers
    bucket: discipline
    clamp: [0, 8]
    invert: true
    source: { field: turnovers }

weights:
  pri:
    scoring: 0.40
    creation: 0.25
    defense: 0.20
    discipline: 0.15

score_profiles:
  PRI:
    kind: affine
    weights_profile: pri
    lo: 55
    hi: 99

sniff:
  require_any_headers: [points, assists, steals, blocks, turnovers]
```

Then refresh and inspect:

```bash
statline --mode local adapter refresh
statline --mode local adapter spec sample_game --full
statline --mode local adapter inputs sample_game
```

### Adapter fields

| Field            | Required | Purpose                                             |
| ---------------- | -------: | --------------------------------------------------- |
| `key`            |      Yes | Unique adapter identifier.                          |
| `version`        |      Yes | Adapter SemVer. Results may change across versions. |
| `aliases`        |       No | Alternate adapter names.                            |
| `title`          |       No | Human-readable name.                                |
| `dimensions`     |       No | Enumerated fields for grouping/filtering.           |
| `sniff`          |       No | Header rules for adapter detection.                 |
| `filters`        |       No | Filterable fields and allowed operations.           |
| `buckets`        |      Yes | Weight categories.                                  |
| `metrics`        |      Yes | Raw-to-metric mappings.                             |
| `efficiency`     |       No | Derived ratio/per-X metrics.                        |
| `weights`        |       No | Bucket weight profiles.                             |
| `penalties`      |       No | Profile-specific penalty settings.                  |
| `score_profiles` |       No | Published scoring profiles such as PRI.             |

---

## 13. Metric source patterns

Direct field:

```yaml
source: { field: points }
```

Constant:

```yaml
source: { const: 1.0 }
```

Expression:

```yaml
source: { expr: "steals + blocks" }
```

Expressions are intentionally safe and small. Use arithmetic, parentheses, `min(...)`, `max(...)`, variable names, and dataset aggregate functions.

Metric order matters: expressions can reference values computed earlier in the adapter.

Dataset aggregate functions accept any raw input header as a quoted string and are case-insensitive:

```yaml
source: { expr: 'dataset_max("GP")' }
source: { expr: 'dataset_mean("PPG")' }
source: { expr: 'dataset_median("APG")' }
source: { expr: 'dataset_min("WIN")' }
source: { expr: 'dataset_sum("WIN")' }
source: { expr: 'dataset_count("PLAYER")' }
```

Aggregates are computed once from the submitted raw batch after raw filters and shared by every row during mapping.

`max`, `min`, `mean`, `median`, and `sum` ignore non-numeric values. `count` counts non-blank values. A single-row score treats that row as a one-row dataset.

---

## 14. Transforms and clamps

Clamp forms:

```yaml
clamp: [0, 40]
clamp: { lo: 0, hi: 40 }
clamp: "0..40"
```

Invert a bad metric so lower is better:

```yaml
- key: turnovers
  bucket: discipline
  clamp: [0, 8]
  invert: true
  source: { field: turnovers }
```

Common transform shape:

```yaml
transform:
  kind: affine
  params: { scale: 1.2, offset: 0.3 }
```

Supported custom transform names include:

```text
linear, capped_linear, minmax, pct01, softcap, log1p
```

---

## 15. Efficiency metrics

Efficiency metrics are derived after primary metrics.

```yaml
efficiency:
  - key: points_per_attempt
    bucket: scoring
    clamp: [0.5, 2.0]
    min_den: 5
    make: "points"
    attempt: "attempts"
```

`min_den` prevents tiny denominators from creating misleading rates.

---

## 16. Filters and dimensions

Dimensions describe enumerated context.

```yaml
dimensions:
  role:
    values: [Carry, Support, Flex]
```

Filters describe user-facing filter controls.

```yaml
filters:
  min_games:
    type: metric
    field: games_played
    accepts: [">=", ">"]
    modes: [include-only]
    description: Only include rows with enough games played.
```

CLI usage:

```bash
statline --mode local score \
  --adapter sample_game \
  stats.csv \
  --filter role=Carry \
  --filter min_games=10
```

---

## 17. Validate adapter behavior

A practical adapter test loop:

```bash
statline --mode local adapter refresh
statline --mode local adapter spec sample_game --full
statline --mode local adapter inputs sample_game
statline --mode local adapter sniff --file stats.csv
statline --mode local tools map batch --adapter sample_game stats.csv --fmt json --out mapped.json
statline --mode local score --adapter sample_game stats.csv --fmt table --profile all
```

Enable stricter loader behavior while developing adapters:

```bash
STATLINE_LOADER_STRICT=1 statline --mode local adapter spec sample_game --full
```

---

## 18. Development workflow

Install everything:

```bash
python -m pip install -e ".[dev]"
```

Run tests:

```bash
pytest
```

Run linting and typing:

```bash
ruff check statline tests
mypy statline
pyright
```

Build package artifacts:

```bash
python -m build
python -m twine check dist/*
```

Audit dependencies:

```bash
pip-audit
```

---

## 19. v4.0.0rc6 release checklist

1. Confirm `project.version` in `pyproject.toml` is `4.0.0rc6`.

2. Confirm `statline/RELEASE` matches the v4 generation and intended component release counters.

3. Confirm bundled adapter metadata and documentation match the intended release.

4. Confirm install variants:

   * core: `pip install statline`
   * app: `pip install "statline[app]"`
   * gateway: `pip install "statline[gateway]"`
   * all: `pip install "statline[all]"`
   * dev: `pip install -e ".[dev]"`
