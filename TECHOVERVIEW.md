# StatLine v4.0.0rc6

## Formula Engine, Pairwise Scoring, API & Performance

> **Release status:** Technical overview of the `4.0.0rc6` development release. This document describes the capabilities represented in the supplied rc6 source and development materials; it is **not** a claim that this release has been published or deployed to production.
>
> **Scope:** The strict formula engine, YAML-based pairwise models, native StatPack/API support, backward compatibility, Gateway and portal changes, and measured v3-to-v4 performance. Where tests or measurements are reported, they refer to the supplied development records rather than a new independent production audit.

---

## Contents

1. [Overview](#1-overview)
2. [Formula engine](#2-formula-engine)
3. [Pairwise scoring models](#3-pairwise-scoring-models)
4. [Rating lifecycle: event results vs. league standings](#4-rating-lifecycle-event-results-vs-league-standings)
5. [Developer interfaces](#5-developer-interfaces)
6. [StatPacks](#6-statpacks)
7. [Gateway, result identity, and explainability](#7-gateway-result-identity-and-explainability)
8. [Portal and release integration](#8-portal-and-release-integration)
9. [Performance](#9-performance)
10. [Compatibility and limitations](#10-compatibility-and-limitations)
11. [Verification and development](#11-verification-and-development)

---

## 1. Overview

StatLine is a configurable data-scoring platform. Traditional StatLine scoring maps dataset fields into metrics, normalizes them, applies profile weights, and produces scores. In v4, **optional pairwise scoring** extends that model to calculations where a participant's result depends on their opponents and a prior-state snapshot.

The rc6 development line brings together four complementary capabilities:

| Component | Purpose |
| --- | --- |
| **Strict formula engine** | Evaluate adapter-authored mathematical expressions using a restricted AST interpreter. |
| **General YAML pairwise engine** | Compare members of one or more event groups, reduce per-opponent results, and produce named outputs. |
| **Native pairwise v1** | Run a portable, single-roster snapshot comparison through StatPacks, the Python SDK, or the authenticated `/v4/pairwise` API. |
| **Gateway and portal improvements** | Preserve input identity, provide optional explanations, offer compact responses, and integrate local development workflows. |

**Pairwise scoring is opt-in.** Existing metric/efficiency evaluation and ordinary weighted `affine`/`window` scoring remain separate from it.

### Core concepts

- **Adapter:** YAML configuration defining data interpretation or scoring rules.
- **Previous state (`old` / `b`):** The participant's state *before* the event, such as an established rating.
- **Current event (`new` / `a`):** The complete set of participants and match results being evaluated.
- **Directed comparison:** Each participant is compared with every other eligible participant in the event group; self-comparisons are excluded.
- **Reduction:** Pairwise contributions are combined into a per-participant value.
- **Output:** Adapter-defined fields, such as `eloChange` and `newElo`, optionally surfaced as named scoring profiles.

## 2. Formula engine

The public formula API is exposed through `statline.core.formula`:

```python
from statline.core.formula import FormulaError, StatLineFormulaEngine

engine = StatLineFormulaEngine()  # strict mode by default
expected = engine.compile("1 / (1 + pow(10, (other.old.eloScore - self.old.eloScore) / 400))")

value = expected(
    {
        "self": {"old": {"eloScore": 1600}},
        "other": {"old": {"eloScore": 1400}},
    }
)
```

### Supported expression features

- Arithmetic: `+`, `-`, `*`, `/`, `//`, `%`, and `**`.
- Comparisons, Boolean operators, and conditional expressions (`x if condition else y`).
- Dictionary-backed dotted field access, such as `self.old.eloScore`.
- Approved functions including `abs`, `min`, `max`, `sum`, `mean`, `exp`, `log`, `log10`, `log1p`, `sqrt`, `sin`, `cos`, `tan`, `floor`, `ceil`, `pow`, `round`, and `clamp`.
- `js_round` and `js_fixed`, for calculations that need JavaScript-compatible rounding behavior.
- Dataset aggregate functions through a context created with `build_dataset_context`.
- Reusable compiled expressions and declaration-ordered variable plans.

A formula can refer to previously declared variables in the same plan:

```python
variables = engine.compile_variables(
    {
        "k": "16 + 32 * exp(-0.15 * abs(self.old.roundsPlayed))",
        "expected": "1 / (1 + pow(10, (other.old.eloScore - self.old.eloScore) / 400))",
        "actual": "1 if count < 2 else (count - self.new.finishPosition) / (count - 1)",
        "teammate": "0.5 if self.new.teamId and other.new.teamId and self.new.teamId == other.new.teamId else 1",
        "delta": "k * (actual - expected) * teammate",
    }
)
```

### Execution and compatibility

The strict evaluator uses Python's standard-library `ast` parser and an explicit interpreter. **It does not use Python `eval`** for new authorable expressions. Invalid syntax, unknown variables, disallowed access, division by zero, and out-of-range operations raise `FormulaError`. Limits on expression size, AST complexity, numeric values, and exponents further constrain execution.

Legacy adapter expressions remain routed through `StatLineFormulaEngine.compile_legacy`, backed by `statline/core/formula/_legacy.py`. The existing `compile_expr(expr)(context, x)` shape and its historical numeric fallback behavior are retained. **Strict mode is not silently substituted for legacy `source.kind: expr` formulas.** The core formula system adds no NumPy or Pydantic dependency.

The strict grammar restricts expressions; it is not a substitute for isolation, request limits, and operational controls when exposing computation to untrusted users.

## 3. Pairwise scoring models

StatLine has **two related pairwise interfaces**, not two hardcoded Elo algorithms. Their mathematical behavior comes from the supplied formulas.

| Capability | General pairwise model | Native pairwise v1 |
| --- | --- | --- |
| Configuration | `pairwise.<model>` | `pairwise.version: 1` |
| Prior state | `old` | `b` |
| Event data | `new` | `a` |
| Event grouping | Optional `group_by` | One match roster per run |
| Pair reductions | `sum`, `mean`, `min`, `max`, `count` | Ordered `sum` |
| Optional sum normalization | Yes | No (`normalize: false`) |
| Detailed pair results | Optional with `include_pairs` | Not part of the native result envelope |
| SDK | `score_pairwise` | `score_native_pairwise` |
| CLI | `python -m statline.core.relate` | `statline statpack` commands |
| HTTP API | Not via the ordinary `/v4/score` endpoint | `POST /v4/pairwise` for registered models |

### 3.1 General YAML pairwise engine

A general pairwise model describes the complete calculation pipeline:

```yaml
metadata:
  id: example.race_ratings
  title: Race Ratings
  version: 1.0.0

pairwise:
  race_elo:
    join_on: id
    group_by: race_id
    compare: all_vs_all
    variables:
      k: "16 + 32 * exp(-0.15 * abs(self.old.roundsPlayed))"
      expected: "1 / (1 + pow(10, (other.old.eloScore - self.old.eloScore) / 400))"
      actual: "1 if count < 2 else (count - self.new.finishPosition) / (count - 1)"
    calculate:
      pair_delta: "k * (actual - expected)"
    reduce:
      delta: {operation: sum, field: pair_delta, normalize: false}
    output:
      oldElo: "self.old.eloScore"
      eloChange: "js_fixed(delta, 2)"
      newElo: "js_round(self.old.eloScore + delta)"

score_profiles:
  ELO: {kind: pairwise, model: race_elo, value: newElo}
```

*The example demonstrates the adapter schema; it is an illustrative rating model, not a prescribed competitive Elo formula.*

The general engine:

1. Joins each event participant to a unique previous-state row using `join_on`.
2. Separates the current dataset by `group_by`, when configured; otherwise treats it as one event group.
3. Evaluates declared variables and pair calculations for every directed opponent pair.
4. Aggregates per-opponent contributions using configured reductions.
5. Emits output fields and any linked named score profiles.

Duplicate IDs within a group, duplicate previous-state identities, and missing previous-state rows are errors. Passing `selected_ids` only filters the **returned results**; it does not change the opponents included in a calculation.

For PyYAML compatibility, the contract uses **`join_on`**, not an unquoted YAML key named `on`.

### 3.2 Native pairwise v1

Native v1 is designed for a reproducible, portable snapshot comparison. Its adapter uses:

```yaml
pairwise:
  version: 1
  inputs:
    a: {kind: statpack, path: inputs/current.statpack, dataset: match.csv}
    b: {kind: statpack, path: inputs/previous.statpack, dataset: match.csv}
  join:
    a_key: id
    b_key: id
    cardinality: one_to_one
    missing_b: error
  participants: a
  opponents: a
  exclude_self: true
  preserve_order: true
  expression: pair.delta
  aggregate: sum
  normalize: false
  outputs: result

expressions:
  pair:
    expected: "1 / (1 + pow(10, (other.old.eloScore - self.old.eloScore) / 400))"
    actual: "1 if count < 2 else (count - self.new.finishPosition) / (count - 1)"
    delta: "16 * (actual - expected)"
  result:
    playerId: "self.new.id"
    oldElo: "self.old.eloScore"
    newElo: "js_round(self.old.eloScore + pairwise.total)"
    eloChange: "js_fixed(pairwise.total, 2)"
```

The input files above are representative relative package paths. Native v1 uses in-order floating-point addition so that its summation behavior can reproduce reference JavaScript-style calculations. It validates stable participant IDs, rejects missing prior history, and enforces a limit of **100,000 ordered comparisons** by default.

**Safety distinction:** reading CSV/JSON/StatPack input data for pairwise calculations does not execute an embedded StatPack `runner.py`. More general StatPack execution can run trusted packaged code and should be treated accordingly.

## 4. Rating lifecycle: event results vs. league standings

Pairwise scoring consumes a **previous snapshot** and a **current event** and returns updated values:

```text
Previous ratings (old / b)    Event results (new / a)
             \                /
              \              /
               Pairwise model
                     |
             Rating changes
                     |
             Updated ratings
```

For an ongoing competition, an external application must save the output and use it as the next event's prior state:

```text
League snapshot → Event 1 → Updated snapshot
                                |
                                v
                         Event 2 → Updated snapshot
                                       |
                                       v
                                League leaderboard
```

**Neither pairwise engine is a built-in sequential league database.** General `group_by` evaluates several independent event groups against the *same incoming pre-event snapshot*. It does not automatically apply one group's result before scoring a later group. For chronological Elo updates, process events in order and persist each output before calculating the next one. Native v1 accepts one roster per run and likewise does not initialize absent prior ratings.

This separation lets an application maintain match history and standings independently of the rating formula. It also means that duplicate-event protection, persistence, and rollback/recalculation policies belong to the surrounding league application.

## 5. Developer interfaces

### 5.1 Python SDK

```python
from statline.sdk import load_adapter, load_pairwise_rows, score_pairwise

adapter = load_adapter("demo.race_elo")  # registered adapter
results = score_pairwise(
    adapter,
    old=load_pairwise_rows("ratings.json"),
    new=load_pairwise_rows("results.json"),
    model="race_elo",
    selected_ids=["DriverA", "DriverB"],
    include_pairs=True,
)
```

For an unregistered YAML adapter, compile the file using the adapter loader and compiler rather than calling `load_adapter` with an unknown registry ID.

### 5.2 General pairwise CLI

```powershell
python -m statline.core.relate --adapter examples/pairwise_race/race_elo.yaml

python -m statline.core.relate `
  --adapter examples/pairwise_race/race_elo.yaml `
  --select DriverA --select DriverB --explain-pairs

python -m statline.core.relate `
  --adapter examples/pairwise_race/race_elo.yaml `
  --old ratings.json --new results.json `
  --out updated_ratings.json
```

The loader accepts supported CSV, JSON, and StatPack inputs. The general model's `duo` paths may provide CLI defaults; they do **not** implicitly embed external input files in an adapter StatPack.

### 5.3 Authenticated native API

`POST /v4/pairwise` evaluates a native-v1 model already registered on the server:

```http
POST /v4/pairwise
Content-Type: application/json
Authorization: Bearer <API_KEY>
```

```json
{
  "adapter": "registered.pairwise_id",
  "a": [{"id": "DriverA", "finishPosition": 1}],
  "b": [{"id": "DriverA", "eloScore": 1500, "roundsPlayed": 10}]
}
```

**Request illustration only:** a real match roster must contain all intended participants, and each active participant needs a corresponding prior-state row. The API uses registered server-side formulas; callers cannot submit arbitrary formula source in the scoring request. Native pairwise output is returned as computed rows, not the legacy `ScoreOut` structure. The `/v4/score` route remains the ordinary metric-scoring API.

## 6. StatPacks

StatPacks make scoring definitions and associated inputs portable. Native-v1 manifests can bundle declared input files under `inputs/`, while `--a` and `--b` allow compatible local inputs to replace them at execution time.

```powershell
python -m statline statpack manifest examples/native_pairwise_v1/pairwise-elo.yaml --out native-elo.statpack
python -m statline statpack run native-elo.statpack --format csv --no-timing
python -m statline statpack run native-elo.statpack --format json --no-timing
python -m statline statpack render native-elo.statpack --out comparison.yaml
```

Native-pairwise mode is selected using `pairwise.version: 1`; historical StatPacks continue using their legacy dataset path. The package input loader requires portable paths to remain inside the package root.

## 7. Gateway, result identity, and explainability

### Compact scoring responses

The ordinary `/v4/score` endpoint supports an optional `response_format: "compact"` setting. Identical row-level context, weights, and explanation configuration are lifted into `shared`; differing values remain on individual rows. The conventional response format remains the default.

This reduces repetitive diagnostics without changing the underlying scores. A client reconstructs a full row by combining shared fields with that row's individual fields. Optional mapped data and lean output controls remain available.

### Stable result identity

Every Gateway scoring result includes `source_index`, the input's original position. Where configured and present, `source_id` identifies the original participant/entity using the adapter's declared identity field. This lets clients reconcile reordered or filtered results with source data, including when display names repeat.

### Optional explanations

With `output.show_explanation`, the scorer can expose the actual normalized values, effective weights, bucket aggregates, percentiles, and final transformations used by the calculation. Helped/limited indicators compare normalized values against a 0.5 reference. These are **calculation attributions**, not proof of real-world causation; bucket means are not independent additive point contributions.

Detailed explanations increase response size and processing cost. For large batches, request them selectively.

## 8. Portal and release integration

The accompanying portal supports public browsing of saved examples without initiating automatic authenticated API requests. Users can explicitly check API connectivity and use live scoring when a Gateway is available and credentials permit it.

Other rc6 integration changes include:

- Browser-friendly API-root navigation for HTML-capable clients while retaining machine-readable JSON behavior.
- Synchronized generated OpenAPI contracts, portal types, saved examples, and downloads.
- Source/version metadata attached to generated assets; `pnpm check-generated` detects drift.
- Local launch helpers that coordinate the Gateway and portal, including development-port selection and startup checks.
- Allowlist-based source packaging and distribution checks designed to exclude runtime credentials, local databases, and unnecessary build artifacts.

The portal's saved-data views are **not** a live league-state persistence system, and local launch tooling does not by itself configure public hosting or network infrastructure.

## 9. Performance

Two performance datasets were provided. They measure **different workloads**:

1. A v3.0.0-versus-v4.0.0rc6 comparison of EBA mapping/scoring and localhost HTTP requests.
2. An rc6-only comparison of conventional and compact response serialization.

Neither dataset benchmarks the pairwise Elo engine directly, so they should **not** be used to claim a pairwise speedup or to compare Python pairwise execution with a JavaScript Elo function.

### 9.1 v3 versus v4: 120-row scoring workloads

The following values are *median elapsed times in milliseconds*, reproduced from the supplied October 12, 2026 measurement report. Lower times are better.

| Fixture | Local complete: v3 → v4 | Local speedup | HTTP complete: v3 → v4 | HTTP speedup |
| --- | ---: | ---: | ---: | ---: |
| Players (120 rows) | 84.20 → 11.16 ms | **7.55×** | 101.85 → 44.58 ms | **2.28×** |
| Teams (120 rows) | 35.52 → 9.27 ms | **3.83×** | 49.94 → 27.32 ms | **1.83×** |

**Interpretation.** The biggest full-pipeline improvement came from raw-to-canonical adapter mapping. The already-mapped scoring stages changed much less, and some individual stages/regression cases became slower. Faster mapping does not imply a corresponding increase in the mathematical scorer's throughput.

The comparison used equivalent input records but **not identical adapter/scoring definitions**. On the 120-player example, 110 PRI values differed between v3 and v4; therefore these timings are comparative workload measurements, not proof of mathematically identical behavior or a pure engine-only speedup.

### 9.2 Compact response sizes (rc6 fixtures)

A separate rc6 fixture measured complete conventional JSON responses versus opt-in compact responses while retaining comparable diagnostics:

| Fixture | Conventional JSON | Compact JSON | Uncompressed reduction | Conventional gzip | Compact gzip |
| --- | ---: | ---: | ---: | ---: | ---: |
| 103 EBA players | 458,907 B | 173,040 B | **62.3%** | 44,728 B | 38,544 B |
| 24 EBA teams | 51,994 B | 23,398 B | **55.0%** | 4,833 B | 4,392 B |

These are **payload reductions**, not equivalent compute-time improvements. Compression narrows the difference, and the results depend on the diagnostic fields requested.

### 9.3 Benchmark methodology and cautions

- The v3/v4 study used warm, in-memory CSV rows and preloaded adapters after warm-up. HTTP tests used persistent connections to a local single-worker server.
- Each 120-row case was sampled repeatedly and summarized by median; the shared test host was not isolated from all other load.
- HTTP measurements excluded normal authentication processing and remote network/TLS effects. They are **not production latency or throughput guarantees**.
- Larger synthetic batches repeated rows rather than supplying distinct participants.
- Cold-start import and initial adapter loading were slower in v4 in the supplied report; warm full-pipeline scoring was generally faster in the highlighted examples.
- Explanations can substantially increase payload size; benchmarks with explanations are not comparable with lean responses.
- CSV ingestion, mapping, scoring, JSON encoding, and full HTTP round-trip measurements are overlapping scopes, not additive stages.

## 10. Compatibility and limitations

| Area | Status in supplied rc6 materials |
| --- | --- |
| Ordinary weighted scoring (`calculate_pri`) | Preserved; pairwise outputs do not silently enter ordinary default profile selection. |
| Existing `affine` and `window` profiles | Retained. |
| Legacy formula fallback | Retained under the legacy evaluator. |
| Strict formulas in legacy `source.kind: expr` | Not automatically enabled. |
| General YAML pairwise | Available through the dedicated SDK and CLI. |
| Native pairwise v1 | Available through the SDK, StatPack runtime, and `/v4/pairwise` for registered adapters. |
| Pairwise through ordinary `/v4/score` | Separate endpoint; not unified. |
| Automatic chronological league updating | Not provided; the caller must persist and feed updated state forward. |
| Missing participant history | An error, not automatic Elo initialization. |
| Arbitrary user-provided formulas in API requests | Not supported by the native endpoint. |
| Unlimited comparisons | Not supported; comparison caps are enforced. |

**Version-history clarification:** the strict formula foundation was introduced before pairwise orchestration, and the general pairwise integration preceded native-v1 StatPack/API support. Earlier patch notes saying that pairwise joining or HTTP integration was “not yet implemented” describe those **earlier steps**, not the combined rc6 development state described here.

## 11. Verification and development

The supplied development records report tests for formula evaluation, legacy-regression behavior, pairwise calculation, native StatPacks, API compatibility, result identity, response compaction, portal generation, packaging, and lint/type checks. Test counts in those records were captured at **different stages of development**, so they should not be combined into a single current release-certification total.

Common validation commands include:

```sh
python -m pip install -e ".[dev]"
python -m pytest -q
python -m pytest -q tests/test_pairwise.py tests/test_pairwise_native_statpacks.py
python -m mypy --config-file mypy-core.ini statline scripts portal/scripts
ruff check statline scripts tests portal/scripts
ruff format --check statline scripts tests portal/scripts
```

Portal checks (from the `portal/` directory):

```sh
pnpm install --frozen-lockfile
pnpm build
pnpm typecheck
pnpm check-generated
```

To start a local development instance, install the Gateway extras and use the platform launch helper:

```sh
python -m pip install -e ".[gateway]"
./launch.sh         # Linux/macOS
# or .\launch.ps1   # PowerShell on Windows
```

The conventional local portal address is `http://localhost:3000`, subject to launch settings. These commands describe the development workflow; their presence does not imply that every platform or deployment configuration was independently validated.

### Release-readiness boundaries

The source/build checks described in the supporting records are **not** a production security audit. The materials do not establish real-world authenticated API throughput, full network/TLS latency, sustained multiworker scaling, completed interactive browser/mobile QA, or independent verification of an arbitrary deployed environment.

---

*Prepared as a consolidated, public-facing technical overview from the supplied rc6 documentation and measured performance report. Implementation details can change before final release; use the checked-in source and generated API contract as the authoritative reference for the deployed version.*
