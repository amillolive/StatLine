# StatLine

**StatLine — Advanced weighted player scoring and analytics, with modular tools for awards, performance evaluation, and real-time integrations.**

“StatLine” is a trademark of StatLine LLC (*in formation*), registration pending.
Source code is licensed under the GNU Affero General Public License v3 (see `LICENSE`).
Brand, name, and logo are **not** covered by the AGPL (see `TRADEMARK_POLICY.md`).

---

## What is StatLine?

StatLine is an **adapter‑driven analytics framework** with an optional **remote API (SLAPI)**. StatLine..

* normalizes raw game stats,
* computes per‑metric scores,
* aggregates into buckets and applies weight presets (e.g., **pri**, **mvp**, role weights),
* exposes a clean **CLI** ~~and **Python library API**~~,
* ~~optionally ingests **Google Sheets** and caches mapped rows~~,
* integrates with **SLAPI** for secure, multi‑client deployments,
* and (new) stabilizes **PRI** to an adapter defined normalized set of ranges and percentiles (see below).

Supported Python: **3.10 – 3.14** (CI: Linux, macOS, Windows)

---

## What’s new (v3.0.0)

* **Keys & Auth:** Access is gated between local and SLAPI. CLI explains further on authorization procedure.
* **PRI normalization:** output scales are adapter defined for clearer tiers and saner UX.
* **Adapters:** tightened demo adapter + docs; versioned schemas.
* **Percentiles:** now available as defined in adapter schematics
* **Roadmap (ships in v3.1.0):**

  * Batch processing **filters** (by position, games played, and adapter‑defined stat predicates [BACKEND COMPLETE])
  * **Output toggles** (e.g., show weights, hide `pri_raw`, include per‑metric deltas [BACKEND COMPLETE])

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

The CLI `--help` command will guide you through authorization (contact the repository maintainer for a key).

---

## Remote API (SLAPI)

Use SLAPI for remote/scaled workflows. SLAPI validates **REGKEY** or **API Access Token** on every request. **Revocation** invalidates both. Requires validation from a project maintainer. (access not public *yet*)

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
# Key - name your adaper
# Version - SemVer; useful roadmapping
# Aliases - Alternative names/lookup
# Title - Non case sensitve naming option
key: example_game
version: 3.1.0
aliases: [eg, sample]
title: Example Game

dimensions:
  # Indirect levers for future implementation and/or filter I/O
  # eg: offense, defense, as values, side, map as unweighted definitions/buckets
  map:   { values: [MapA, MapB, MapC] }
  side:  { values: [Attack, Defense] }
  role:  { values: [Carry, Support, Flex] }
  mode:  { values: [Pro, Ranked, Scrim] }

buckets:
  # Weighting organization (metrics are unweighted, it's why you use buckets);
  # Great for grouping stats or interpolations together
  scoring: {}
  impact: {}
  utility: {}
  survival: {}
  discipline: {} # Weighted separate for punishable stats, like mistakes; see below
  telem: {} # Useful for metrics used in efficiency DSL but not weighted directly; see below

metrics:
  # Direct fields (ppg, apg, ast, kill, etc..) (clamped; discipline is inverted so fewer mistakes score higher) 
  # Clamps usually auto derived from leader and Foor in Batch mode
  - { key: stat3_count, bucket: utility,    clamp: [0, 50],               source: { field: stat3_count } }
  - { key: mistakes,    bucket: discipline, clamp: [0, 25], invert: true, source: { field: mistakes }    }
  # Still invert the bad metric; discipline is just a separate weight lever; the bucket isn't inverted
  - { key: gp,          bucket: telem,      clamp: [0, 12],               source: { field: gp }          }

# New ratio/derived DSL with field names, clamps, and minimum denominators
efficiency:
  # Per-round scoring output
  - key: stat1_per_round
    bucket: scoring
    clamp: [0.00, 2.00]
    min_den: 5
    make: "stat1_total"
    attempt: "rounds_played"

  # Impact success rate
  - key: stat2_rate
    bucket: impact
    clamp: [0.00, 1.00]
    min_den: 10
    make: "stat2_numer"
    attempt: "stat2_denom"

  # Survival quality (good vs total)
  - key: stat4_quality
    bucket: survival
    clamp: [0.00, 1.00]
    min_den: 5
    make: "stat4_good"
    attempt: "stat4_total"

weights:
  # Separate profiles of weight distribution
  # Doesn't need to sum to one; scorer evenly redistributes as necessary
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
  # our discipline bucket(s) is/are self weighted here; 
  # acts as a downweight after normalization (eg: pri says discipline matters 8% less under support)
  pri:     { discipline: 0.10 }
  mvp:     { discipline: 0.12 }
  support: { discipline: 0.08 }

filters:
# include-only = output whatever passes the filter (eg: >= outputs only >= because else failed)
# exclude-only = output whatever fails the filter (eg: <= outputs > because it passed <=)
  gp: { metric: gp, accepts: ["<", ">", "<=", ">=", "=", "!="], values: ["include-only", "exclude-only"] }

sniff:
# helps auto-select
  require_any_headers: [stat1_total, rounds_played, stat2_numer, stat2_denom, stat4_good, stat4_total, stat3_count, mistakes]
```

See `HOWTO.md` for adapter authoring.

---

## Versioning & Stability

* **Adapters:** semantic (`MAJOR.MINOR.PATCH`). Breaking schema changes → new **MAJOR** (suggested to match with repository release version).
* **PRI scale:** now adapter defined.
* **SLAPI:** deprecations target **≥60 days** notice unless security requires faster action. (Whoops! V2 is now deprecated, so much for 60 days!)

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

**Is the CLI required?** Currently, yes. You can score locally without API access.

**Can I use my own game?** Yes—write an adapter; any game with a box score + valid datasheet can work.

**Do I need env vars?** No. A maintainer should assist you should you require gated access.

**How are percentiles computed?** Per dataset/sample window defined in the request or adapter context (v3.0.0 feature).

**Discord support?** We aim to make a central Discord server soon for ongoing development of our Discord app. As of current, no.
