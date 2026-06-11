# StatLine

**StatLine** is an adapter-driven player scoring and analytics toolkit for turning raw stat rows into weighted, explainable ratings.

It can run completely local for simple scoring workflows, or against **SLAPI**, the optional StatLine API layer for authenticated remote scoring, adapter inspection, and multi-client deployments.

> Release target: **v3.0.0**  
> Python: **3.10 through 3.14**  
> License: **AGPL-3.0-or-later**, with separate trademark restrictions for the StatLine name and branding.

---

## What StatLine does

StatLine takes raw rows like CSV box-score data, maps those rows through an adapter, scores the mapped metrics, then returns profile scores such as **PRI** and adapter-defined variants.

At a high level, StatLine provides:

- **Adapter-based scoring** for different games, leagues, datasets, or stat schemas.
- **Local scoring** through the Python package and `statline` CLI.
- **Remote/API scoring** through SLAPI when installed with the remote stack.
- **Weighted score profiles**, including PRI-style outputs and adapter-defined variants.
- **Batch scoring**, row scoring, mapping-only commands, and already-mapped calculation commands.
- **Adapter inspection tools** for inputs, metrics, dimensions, filters, weights, and sniffing.
- **Typed public Python API** for bots, dashboards, notebooks, and application code.

---

## Install

StatLine v3.0.0 has four intended install variants.

| Variant | Command | Use this when you want |
| --- | --- | --- |
| **base** | `pip install statline` | Functional local library and local CLI scoring. |
| **remote** | `pip install "statline[remote]"` | Base plus API client/auth and the SLAPI serving stack. |
| **extras** | `pip install "statline[extras]"` | Remote plus user conveniences such as richer terminal/UI and Google Sheets-related helpers. |
| **devpack** | `pip install -e ".[devpack]"` | Everything needed for development, testing, typing, docs, packaging, and release checks. |

For a source checkout:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -e ".[devpack]"
```

---

## Quick start: local CLI

Local mode avoids all network probing and uses the installed StatLine core directly.

```bash
statline --mode local adapter list
statline --mode local adapter inputs demo
statline --mode local adapter weights demo
```

Score the bundled demo CSV from a source checkout:

```bash
statline --mode local score \
  --adapter demo \
  statline/data/stats/DEMO/demo.csv \
  --fmt table \
  --profile all \
  --percentile \
  --limit 10
```

Write JSON instead:

```bash
statline --mode local score \
  --adapter demo \
  statline/data/stats/DEMO/demo.csv \
  --fmt json \
  --pretty \
  --out results.json
```

---

## Quick start: Python API

```python
from statline import list_adapters, load_dataset, score

print(list_adapters())

rows = load_dataset("DEMO/demo", limit=10)
results = score("demo", rows, mode="batch", weights="pri")

for row in results[:3]:
    print(row["pri"], row["pri_raw"], row.get("scores", {}))
```

For one row:

```python
from statline import score_row

player = {
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

result = score_row("demo", player, weights="pri")
print(result["pri"])
```

---

## CLI overview

The main command is:

```bash
statline --help
```

Useful global options:

| Option | Meaning |
| --- | --- |
| `--mode auto` | Probe SLAPI; use remote when reachable and authenticated, otherwise local where supported. |
| `--mode local` | Force offline local scoring and skip SLAPI entirely. |
| `--mode remote` | Require SLAPI to be reachable and authenticated. |
| `--url URL` | Set the SLAPI base URL. Also supported through `SLAPI_URL`. |
| `--timing / --no-timing` | Show or hide timing summaries. |
| `--version` | Print the CLI version. |

Primary user commands:

| Command | Purpose |
| --- | --- |
| `statline adapter list` | List adapters. |
| `statline adapter spec <adapter>` | Show adapter metadata/spec details. |
| `statline adapter inputs <adapter>` | Show raw input keys expected by an adapter. |
| `statline adapter metrics <adapter>` | Show mapped metric keys. |
| `statline adapter weights <adapter>` | Show available weight profiles. |
| `statline adapter filters <adapter>` | Show adapter-declared filters. |
| `statline adapter sniff --file stats.csv` | Detect matching adapters from headers. |
| `statline map row` / `map batch` | Map raw rows without scoring. |
| `statline calc row` / `calc batch` | Score already-mapped metric rows. |
| `statline score` | Map and score raw CSV/YAML/JSON rows. |
| `statline interactive` | Guided CLI scoring flow. |
| `statline serve` | Start SLAPI locally. Requires the remote stack. |
| `statline auth ...` | Device enrollment and API key workflows. |
| `statline sys status` | Runtime, auth, path, and logging status. |

---

## Scoring concepts

### Adapter

An adapter is a YAML contract that explains how to turn raw fields into StatLine metrics. It defines:

- metadata such as `key`, `version`, `aliases`, and `title`,
- raw-to-metric mappings,
- derived efficiency metrics,
- buckets,
- weight profiles,
- penalties,
- score profiles,
- optional dimensions and filters,
- optional sniffing rules for adapter detection.

### Metric

A metric is a numeric signal used by the scoring engine. Metrics can be direct fields, constants, or safe expressions over previous values.

### Bucket

A bucket groups metrics for weighting. A PRI profile does not usually weight every metric one by one; it weights the buckets.

### Weight profile

A weight profile defines how strongly each bucket contributes. The default profile is usually `pri`, but adapters can expose more.

### Score profile

A score profile controls how the normalized raw score becomes a published score. StatLine supports affine profiles and windowed profiles.

### PRI and `pri_raw`

`pri_raw` is the normalized raw score. `pri` is the adapter/profile-rendered score. Some adapters also expose additional profile scores such as `pri_af`, `pri_ar`, or `pri_ap`.

---

## Input formats

The CLI reads CSV, YAML, JSON-like YAML, and stdin CSV for commands that accept file input.

CSV example:

```csv
name,ppg,apg,orpg,drpg,spg,bpg,tov,fgm,fga,win,loss
Example Player,24.5,6.2,1.0,4.0,1.5,0.7,2.1,9.2,18.4,12,8
```

YAML example:

```yaml
- name: Example Player
  ppg: 24.5
  apg: 6.2
  orpg: 1.0
  drpg: 4.0
  spg: 1.5
  bpg: 0.7
  tov: 2.1
  fgm: 9.2
  fga: 18.4
  win: 12
  loss: 8
```

---

## Remote/API mode

Install the remote variant:

```bash
pip install "statline[remote]"
```

Start SLAPI locally:

```bash
statline --mode local serve --host 127.0.0.1 --port 8000
```

Or use the `slapi` console entry point:

```bash
SLAPI_HOST=127.0.0.1 SLAPI_PORT=8000 slapi
```

Then point clients at it:

```bash
export SLAPI_URL="http://127.0.0.1:8000"
statline --mode remote sys status
```

SLAPI supports protected auth flows. Normal remote use requires both device enrollment and an API key. Administrative and moderation commands require the matching scopes.

Common auth flow:

```bash
statline auth device-init
statline auth enroll --token reg_... --user your-handle --email you@example.com
statline auth apikey-request --owner your-name
statline auth apikey-claim --request-id REQUEST_ID
statline auth whoami
```

The exact approval steps depend on the SLAPI administrator.

---

## Development

Install the development pack:

```bash
python -m pip install -e ".[devpack]"
```

Run checks:

```bash
pytest
ruff check statline tests
mypy statline
pyright
```

---

## Legal

StatLine source code is licensed under the **GNU Affero General Public License v3 or later**. The StatLine name, marks, and logos are not granted by the source license.

See:

- `LICENSE`
- `TRADEMARK_POLICY.md`
- `CLA.md`
- `legal/tos.md`
- `legal/privacypolicy.md`
- `legal/aup.md`

---

## Repository

- Homepage: `https://statline.dev`
- Repository: `https://github.com/amillolive/StatLine`
- Issues: `https://github.com/amillolive/StatLine/issues`
