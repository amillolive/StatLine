# StatLine

**StatLine™ — weighted player scoring, efficiency modeling, and tooling.**

“StatLine” is a trademark of StatLine LLC (in formation), registration pending.
Source code is licensed under the GNU Affero General Public License v3 (see LICENSE).
Brand, name, and logo are not covered by the AGPL.

---

## What is StatLine?

StatLine is an adapter-driven analytics framework that:

- normalizes raw game stats,
- computes per-metric scores (with clamps, inversion, and ratios),
- aggregates into buckets and applies weight presets (e.g., pri),
- exposes a clean CLI and library API,
- optionally ingests Google Sheets and caches mapped rows.

Supported Python:

- **3.10 – 3.13** (tested in CI across Linux, macOS, Windows)

---

## Install

Base install:

```bash
# pip
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

StatLine installs a console script `statline`. You can also call the module directly.

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

- `adapters` — show available adapter keys & aliases
- `interactive` — guided prompts + timing table
- `export-csv` — export cached mapped metrics (when using the cache/Sheets flow)
- `score` — batch-score a CSV/YAML through an adapter

When the CLI runs, you’ll see a banner (noted so you can confirm proper install):

```diff
=== StatLine — Adapter-Driven Scoring ===
```

Enable per-stage timing via env (eg: 14ms):

```bash
STATLINE_TIMING=1 statline score --adapter rbw5 stats.csv
```

---

## Input formats

StatLine reads **CSV** or **YAML**. The columns/keys must match what the adapter expects.

### CSV

- First row is the header.
- Each subsequent row is an entity (player).
- Provide whatever **raw** fields your adapter maps (e.g., `ppg, apg, fgm, fga, tov`).

```cs
display_name,team,ppg,apg,orpg,drpg,spg,bpg,fgm,fga,tov
JordanRed,27.3,4.8,1.2,3.6,1.9,0.7,10.2,22.1,2.1
```

### Example adapter (yaml)

Adapters define the schema for raw inputs, buckets, weights, and any derived metrics.
Below is the `example.yaml` you can ship in `statline/core/adapters/defs/`:

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

Reference `HOWTO.md` should you have any questions regarding adapters and yaml formatting.
