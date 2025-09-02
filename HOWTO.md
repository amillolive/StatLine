# Creating a Compliant Adapter

> **Mission:** Adapters describe *your game's stats*; the PRI engine normalizes and scores them. Follow this document exactly.

---

## 0) Repository Placement

```plaintext
StatLine/
├── HOWTO.md        ← this file (top level)
└── statline/
    └── core/
        └── adapters/
            └── defs/
                └── example.yaml
```

---

## 1) Metadata (required)

**Purpose:** Identifies and labels the adapter so the PRI engine knows what it represents.

Keys:

* **`key`** (required): Unique ID (kebab or snake case).
* **`version`** (required): SemVer.
  * **Major**: structure overhauls (buckets/metrics/clamps).
  * **Minor**: add new metrics/buckets or meaningful clamp shifts.
  * **Patch**: small fixes/tuning.
* **`aliases`** (optional): Alternate names.
* **`title`** (optional): Human-friendly label.
**Example:**

```yaml
key: example_game
version: 0.2.0
aliases: [ex, sample]
title: Example Game
```

---

## 2) Dimensions (optional)

Used for filtering/rollups.

```yaml
dimensions:
  map:   { values: [MapA, MapB, MapC] }
  side:  { values: [Attack, Defense] }
  role:  { values: [Carry, Support, Flex] }
  mode:  { values: [Pro, Ranked, Scrim] }
```

---

## 3) Buckets (required)

Group metrics into weighted categories.

```yaml
buckets:
  scoring: {}
  impact: {}
  utility: {}
  survival: {}
  discipline: {}
```

---

## 4) Metrics (required)

Each metric:

* belongs to a bucket,
* uses realistic `clamp: [min, max]`,
* optional `invert: true` for penalty metrics if *lower is better*,
* pulls from `source.field`.

```yaml
metrics:
  - { key: stat3_count, bucket: utility,    clamp: [0, 50],  source: { field: stat3_count } }
  - { key: mistakes,    bucket: discipline, clamp: [0, 25], invert: true, source: { field: mistakes } }
```

---

## 5) Efficiency / Derived Ratios (updated)

**Purpose:** Define ratios and derived signals as **make/attempt** pairs.
**New DSL:** write **field names or expressions** directly (no `raw[...]`). You may also use **numeric constants** for `attempt`.

* `make`, `attempt` accept:
  * field names: `stat1_total` `rounds_played`
  * expressions: `min(3*hits, max(2*hits, score - penalties))`
  * numeric constants: `20`
* `min_den`: minimum denominator required to score (guards divide-by-low).
* `clamp`: post-compute clamp before normalization.
* Supported ops: `+ - * /`, parentheses, `min(a,b), max (a,b).

**Examples:**

```yaml
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

  # Survival quality (good / total)
  - key: stat4_quality
    bucket: survival
    clamp: [0.00, 1.00]
    min_den: 5
    make: "stat4_good"
    attempt: "stat4_total"

  # (Optional) Constant attempt — use to softly scale a raw signal
  - key: pressure_hint
    bucket: impact
    clamp: [0.00, 1.00]
    min_den: 1
    make: "entries"      # raw count
    attempt: "20"        # normalize by a fixed scale
```

---

## 6) Mapping (legacy)

Legacy mapping isn’t needed when you use `source.field` and the efficiency DSL. Prefer this newer approach.

---

## 7) Weights (optional)

Assign bucket importance for different presets.

Weights don’t need to sum to 1; the engine normalizes.

```yaml
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
```

---

## 8) Penalties (optional)

Extra downweight by bucket per preset (applies after normalization).

```yaml
penalties:
  pri:     { discipline: 0.10 }
  mvp:     { discipline: 0.12 }
  support: { discipline: 0.08 }
```

---

## 9) Sniff (optional)

Headers the engine can use to auto-select your adapter.
**Tip:** include all fields referenced by `metrics` and `efficiency`.

```yaml
sniff:
  require_any_headers:
    [stat1_total, rounds_played, stat2_numer, stat2_denom, stat4_good, stat4_total, stat3_count, mistakes]
```

---

## 10) Versioning Rules

* **Major** = structural change.
* **Minor** = added signals or material clamp shifts.
* **Patch** = tweaks/fixes.

---

## 11) Validation Checklist

* Metrics reference real fields; buckets exist.
* Buckets exist for all metrics.
* Clamps realistic; penalty metrics use `invert: true`.
* Efficiency pairs have valid fields/expressions; `min_den` set sensibly.
* Efficiency pairs have valid fields.
* Any constant denominators are quoted (e.g., `"20"`).
* Weights only reference existing buckets.
* Sniff headers cover all referenced raw fields.

---

## 12) Future Hooks (not yet supported)

* Metric transforms
* Per-dimension clamps.
* Per-metric multipliers.
* Team-factor modes.

---

## 13) Minimal Starter Template (Updated Example)

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
