# StatLine HOWTO

This guide shows the practical workflows for StatLine v3.0.0: installing the right variant, scoring locally, using SLAPI, writing adapters, and preparing a release.

---

## 1. Choose the right install variant

### Local user install

Use this when you only need local scoring and the Python library.

```bash
pip install statline
```

### Remote/API user install

Use this when you need authenticated SLAPI access or want to run the local API server.

```bash
pip install "statline[remote]"
```

### Power-user install

Use this when you want the remote stack plus convenience tooling.

```bash
pip install "statline[extras]"
```

### Developer install

Use this from a cloned repository.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -e ".[devpack]"
```

---

## 2. Verify the install

```bash
statline --version
statline --mode local sys status
statline --mode local adapter list
```

Use `--mode local` when you want zero network behavior. Use `--mode remote` when a SLAPI server must be reachable and authenticated.

---

## 3. Inspect an adapter before scoring

Start with the adapter metadata. The bundled demo adapter is named `demo`.

```bash
statline --mode local adapter spec demo
statline --mode local adapter inputs demo
statline --mode local adapter metrics demo
statline --mode local adapter weights demo
statline --mode local adapter filters demo
```

Detect adapters from a file:

```bash
statline --mode local adapter sniff --file statline/data/stats/DEMO/demo.csv
```

Refresh the local adapter registry after changing YAML files:

```bash
statline --mode local adapter refresh
```

---

## 4. Score raw rows from CSV

From a source checkout:

```bash
statline --mode local score \
  --adapter demo \
  statline/data/stats/DEMO/demo.csv \
  --fmt table \
  --limit 10
```

Include all detected profile columns and client-side percentiles:

```bash
statline --mode local score \
  --adapter demo \
  statline/data/stats/DEMO/demo.csv \
  --fmt table \
  --profile all \
  --percentile
```

Write JSON:

```bash
statline --mode local score \
  --adapter demo \
  statline/data/stats/DEMO/demo.csv \
  --fmt json \
  --pretty \
  --out results.json
```

Write CSV:

```bash
statline --mode local score \
  --adapter demo \
  statline/data/stats/DEMO/demo.csv \
  --fmt csv \
  --out results.csv
```

Available output formats for `score`:

| Format | Use |
| --- | --- |
| `table` | Human-readable terminal table. |
| `md` | Markdown table. |
| `csv` | Spreadsheet-friendly output. |
| `json` | JSON array. |
| `jsonl` | One JSON object per line. |

---

## 5. Use custom weights

Use an adapter-defined preset:

```bash
statline --mode local score \
  --adapter demo \
  statline/data/stats/DEMO/demo.csv \
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
  --adapter demo \
  statline/data/stats/DEMO/demo.csv \
  --weights weights.yaml
```

Normalize arbitrary weights:

```bash
statline --mode local weights normalize aefg=3 tov_eff=2 two_way=1
```

Resolve weights against an adapter:

```bash
statline --mode local weights resolve --adapter demo --preset pri
statline --mode local weights resolve --adapter demo --preset pri --override aefg=0.35
```

---

## 6. Map raw data without scoring

Mapping is useful when you want to check whether an adapter is reading your columns correctly.

Map one row:

```bash
statline --mode local map row \
  --adapter demo \
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
statline --mode local map batch \
  --adapter demo \
  statline/data/stats/DEMO/demo.csv \
  --fmt json \
  --out mapped.json
```

---

## 7. Score already-mapped rows

Use `calc` when your input rows already contain adapter metric keys instead of raw source fields.

Score one mapped row:

```bash
statline --mode local calc row \
  --adapter demo \
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
statline --mode local calc batch mapped.json --adapter demo --fmt json
```

---

## 8. Use StatLine from Python

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
results = score("demo", rows, mode="batch", weights="pri")

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

result = score_row("demo", row, weights="pri")
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

mapped = map_row("demo", raw)
result = score_row("demo", raw)
```

---

## 9. Run SLAPI locally

Install the remote variant first:

```bash
pip install "statline[remote]"
```

Start the API server:

```bash
statline --mode local serve --host 127.0.0.1 --port 8000
```

Start in the background:

```bash
statline --mode local serve --host 127.0.0.1 --port 8000 --background
```

Point the CLI at the server:

```bash
export SLAPI_URL="http://127.0.0.1:8000"
statline --mode remote sys status
```

Health check endpoint:

```bash
curl http://127.0.0.1:8000/v3/health
```

Interactive docs are available at:

```text
http://127.0.0.1:8000/docs
http://127.0.0.1:8000/redoc
```

---

## 10. Enroll a device and claim an API key

SLAPI v3 protects private endpoints with device proof plus API key authentication. A typical flow is:

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
statline sys status
```

Admin and moderator commands require the appropriate scopes.

---

## 11. Build an adapter

Adapters live in:

```text
statline/core/adapters/defs/<adapter>.yaml
```

A minimal adapter needs:

- `key`
- `version`
- `buckets`
- `metrics`
- `weights` or a default uniform PRI profile

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

| Field | Required | Purpose |
| --- | ---: | --- |
| `key` | Yes | Unique adapter identifier. |
| `version` | Yes | Adapter SemVer. Results may change across versions. |
| `aliases` | No | Alternate adapter names. |
| `title` | No | Human-readable name. |
| `dimensions` | No | Enumerated fields for grouping/filtering. |
| `sniff` | No | Header rules for adapter detection. |
| `filters` | No | Filterable fields and allowed operations. |
| `buckets` | Yes | Weight categories. |
| `metrics` | Yes | Raw-to-metric mappings. |
| `efficiency` | No | Derived ratio/per-X metrics. |
| `weights` | No | Bucket weight profiles. |
| `penalties` | No | Profile-specific penalty settings. |
| `score_profiles` | No | Published scoring profiles such as PRI. |

---

## 12. Metric source patterns

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

Expressions are intentionally safe and small. Use arithmetic, parentheses, `min(...)`, `max(...)`, and variable names. Metric order matters: expressions can reference values computed earlier in the adapter.

---

## 13. Transforms and clamps

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

## 14. Efficiency metrics

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

## 15. Filters and dimensions

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

## 16. Validate adapter behavior

A practical adapter test loop:

```bash
statline --mode local adapter refresh
statline --mode local adapter spec sample_game --full
statline --mode local adapter inputs sample_game
statline --mode local adapter sniff --file stats.csv
statline --mode local map batch --adapter sample_game stats.csv --fmt json --out mapped.json
statline --mode local score --adapter sample_game stats.csv --fmt table --profile all
```

Enable stricter loader behavior while developing adapters:

```bash
STATLINE_LOADER_STRICT=1 statline --mode local adapter spec sample_game --full
```

---

## 17. Development workflow

Install everything:

```bash
python -m pip install -e ".[devpack]"
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

## 18. v3.0.0 release checklist

1. Update package metadata to `3.0.0` in `pyproject.toml`.
2. Update runtime versions in `statline/__init__.py`, `statline/cli.py`, and `statline/slapi/app.py`.
3. Update bundled adapter versions where they still say `3.0.0rc3`.
4. Confirm install variants:
   - base: `pip install statline`
   - remote: `pip install "statline[remote]"`
   - extras: `pip install "statline[extras]"`
   - devpack: `pip install -e ".[devpack]"`
5. Run tests, linting, and type checks.
6. Build artifacts with `python -m build`.
7. Confirm artifacts do not include local DBs, logs, secrets, `.git`, caches, or bytecode.
8. Run `twine check dist/*`.
9. Tag and publish only after version strings and docs agree.

---

## 19. Troubleshooting

### The CLI is trying to reach SLAPI when I only want local scoring

Use local mode:

```bash
statline --mode local score --adapter demo stats.csv
```

### `serve` says a dependency is missing

Install the remote stack:

```bash
pip install "statline[remote]"
```

Some older runtime error strings may still mention `[api]`; for v3.0.0, the intended extra name is `[remote]`.

### My adapter does not appear

Refresh the registry and inspect errors:

```bash
statline --mode local adapter refresh
STATLINE_LOADER_STRICT=1 statline --mode local adapter spec your_adapter --full
```

### Scores look compressed or inflated

Inspect clamps, score profiles, and the normalization context:

```bash
statline --mode local adapter spec your_adapter --full
statline --mode local score --adapter your_adapter stats.csv --caps batch
statline --mode local score --adapter your_adapter stats.csv --caps clamps
```

### CSV names are wrong in output

Pass preferred name columns:

```bash
statline --mode local score \
  --adapter demo \
  stats.csv \
  --name-col player_name \
  --name-col name
```
