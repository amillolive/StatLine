# Creating a Compliant Adapter (v2.1.0)

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

* **`key`** (required): unique ID (kebab or snake case).
* **`version`** (required): SemVer.

  * **Major**: structure overhauls (buckets/metrics/clamps/DSL changes).
  * **Minor**: add metrics/buckets or meaningful clamp shifts.
  * **Patch**: small fixes/tuning.
* **`aliases`** (optional): alternate names.
* **`title`** (optional): human‑friendly label.

**Example:**

```yaml
key: example_game
version: 0.2.0
aliases: [ex, sample]
title: Example Game
```

---

## 2) Dimensions (optional)

Used for filtering and rollups (e.g., map/role/mode). Values are strict enums.

```yaml
dimensions:
  map:   { values: [MapA, MapB, MapC] }
  side:  { values: [Attack, Defense] }
  role:  { values: [Carry, Support, Flex] }
  mode:  { values: [Pro, Ranked, Scrim] }
```

---

## 3) Buckets (required)

Group metrics into weighted categories. Names are free‑form but must be consistent across `weights` and `penalties`.

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
* optional `invert: true` for penalty metrics (*lower is better*),
* pulls from `source.field`.

```yaml
metrics:
  - { key: stat3_count, bucket: utility,    clamp: [0, 50],  source: { field: stat3_count } }
  - { key: mistakes,    bucket: discipline, clamp: [0, 25], invert: true, source: { field: mistakes } }
```

> **Tip:** Keep clamps tight and data‑driven. Wildly wide clamps dilute normalization and downstream PRI.

---

## 5) Efficiency / Derived Ratios (updated DSL)

**Purpose:** Define ratios and derived signals as **make/attempt** pairs.

**New DSL (v2.1.0):** write **field names or expressions** directly (no `raw[...]`). You may also use **numeric constants** for `attempt`.

* `make`, `attempt` accept:

  * **field names:** `stat1_total`, `rounds_played`
  * **expressions:** `min(3*hits, max(2*hits, score - penalties))`
  * **numeric constants:** `20`
* `min_den`: minimum denominator required to score (guards divide‑by‑low).
* `clamp`: post‑compute clamp before normalization.
* Supported ops: `+ - * /`, parentheses, `min(a,b)`, `max(a,b)`.

**Examples:**

```yaml
efficiency:
  # Per‑round scoring output
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

  # (Optional) Constant attempt — softly scale a raw signal
  - key: pressure_hint
    bucket: impact
    clamp: [0.00, 1.00]
    min_den: 1
    make: "entries"
    attempt: "20"
```

> **Guardrails:** choose `min_den` high enough to avoid volatile low‑sample noise; clamp tight to expected domain.

---

## 6) Mapping (legacy)

Legacy mapping isn’t needed when you use `source.field` and the efficiency DSL. Prefer the newer approach.

---

## 7) Weights (optional)

Assign bucket importance for different presets. **They don’t need to sum to 1**; the engine normalizes.

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

Extra downweight by bucket per preset (applies **after normalization**).

```yaml
penalties:
  pri:     { discipline: 0.10 }
  mvp:     { discipline: 0.12 }
  support: { discipline: 0.08 }
```

---

## 9) Sniff (optional)

Headers the engine can use to auto‑select your adapter. **Include all fields referenced by `metrics` and `efficiency`.**

```yaml
sniff:
  require_any_headers:
    [stat1_total, rounds_played, stat2_numer, stat2_denom, stat4_good, stat4_total, stat3_count, mistakes]
```

---

## 10) v2.1.0 Features & Compatibility

* **PRI Scale:** fixed **55–99**. Do not assume 0–99.
* **Percentiles (batch/output):** adapters do not define these; they’re computed by the engine per request/dataset window.
* **Output toggles:** callers may request `show_weights`, `hide_pri_raw`, per‑metric deltas, etc. Adapters should not rely on these.
* **Batch filters:** callers may filter by `position`, `games_played`, and adapter‑defined predicates like `{stat, op, value}`.
* **Versioning:** any breaking schema change → bump **major**.

---

## 11) Validation Checklist

* [ ] Every `metric.bucket` exists in `buckets`.
* [ ] `clamp` ranges are realistic; penalty metrics use `invert: true`.
* [ ] `efficiency[*].make/attempt` reference real fields or valid expressions; `min_den` is set.
* [ ] Constant denominators are quoted strings (e.g., `"20"`).
* [ ] `weights` only reference existing buckets (engine normalizes totals).
* [ ] `sniff.require_any_headers` includes all raw fields used by `metrics`/`efficiency`.
* [ ] `version` follows SemVer and reflects material changes.

---

## 12) Future Hooks (not yet supported)

* Metric transforms
* Per‑dimension clamps
* Per‑metric multipliers
* Team‑factor modes

---

## 13) Minimal Starter Template (updated)

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

---

## 14) Gotchas & Best Practices

* Keep buckets few and meaningful; noisy bucket design destroys interpretability.
* Prefer **rates** to raw counts; clamp rates in realistic domains.
* Use **`min_den`** to fight small‑sample volatility.
* When in doubt, simulate on real match CSVs and check percentile stability.
* Document your adapter’s required columns in repo docs for users.
