# StatLine Gateway API v4 — rc6

This document describes the v4 preview on `next`. The stable v3 line remains on `main`.
The patched source stays at **4.0.0rc6**; install this checkout to use its changes.

## Hosts and public routes

The product portal belongs at `https://statline.dev`; the API belongs at
`https://api.statline.dev`. Browser navigation to the API root returns HTML links
when `Accept` contains `text/html`. Ordinary machine clients and explicit
`Accept: application/json` receive the existing JSON index. Responses vary on `Accept`.

Health, OpenAPI, Swagger (`/docs`), ReDoc (`/redoc`), and the API index require no credential.
Public portal browsing uses saved content; choosing Check API contacts public API resources.

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/v4/health` | Public health and source version |
| GET | `/v4/adapters` | Canonical adapter catalog |
| GET | `/v4/adapters/{adapter}` | Adapter, dimensions, identity field, weights, filters, profiles |
| POST | `/v4/adapters/sniff` | Match adapters from headers |
| GET | `/v4/datasets` | Packaged datasets |
| GET | `/v4/datasets/{dataset}` | Paginated input rows |
| POST | `/v4/score` | Map, filter, and score a row, batch, or dataset |

Catalog, dataset and scoring operations require approved `userbase` access under
existing Gateway authentication policy. Send `Authorization: Bearer api_...`.
Enrolled-device operations use signed `X-SL-*` proof headers; CORS does not grant access.

## A complete scoring request

```json
{
  "adapter": "eba.players",
  "dataset": "EBA_Elevate302/eba_players_gp3_plus.csv",
  "weights": "pri",
  "response_format": "compact",
  "profiles": ["PRI"],
  "output": {
    "show_weights": true,
    "show_context_used": true,
    "show_components": true,
    "hide_pri_raw": false
  },
  "include_mapped": false
}
```

Provide exactly one of `row`, `rows`, or `dataset`. Raw input maps before metric
filters and scoring. `input_kind: "mapped"` skips mapping. `caps_mode: "batch"`
uses one cohort; `"row"` evaluates rows independently. Dataset `source_index`
is relative to the loaded dataset prefix (if `dataset_limit` is specified).

## Compact response and RC migration

**Legacy remains the default.** Existing RC requests retain their row-level
metadata and original envelope. Results gain additive `source_index` and, when
available, `source_id`. Consumers that forbid unknown row fields must allow these fields.

Opt in with `response_format: "compact"`. The compact envelope adds
`response_format: "compact"` and `shared`. Only `context_used`, `weights` and
`explanation_context` that are present and exactly equal in **every** result
are moved into `shared`. Unequal or partially present values remain on rows.
An empty run has `shared: {}`; a one-row run can share its metadata once.
Profiles and independent-row context are compared as complete objects.

```python
results = [{**response.get("shared", {}), **row} for row in response["results"]]
```

```javascript
const results = response.results.map(row => ({ ...response.shared, ...row }));
```

Both readers work for legacy and compact responses. Row fields take precedence.
No component, bucket, score, diagnostic value, or rounding behavior changes when
compacting. Local SDK dictionaries keep their usual shape and meaning.

`output` controls stay effective. Disabled weights/context are not created just
because compact mode is selected. `output: {"show_components": false,
"show_buckets": false}` remains lean; explanations are opt-in.
`include_mapped` independently controls mapped rows.

## Identify and reconcile results

`source_index` is the zero-based position in the submitted input **before** raw
or mapped filtering. The Gateway preserves it even when names repeat. Results
remain in surviving input order; sorting consumers retain the index.

`source_id` is the value from `metadata.meta.source_id_field` when declared;
otherwise the first declared adapter dimension is used. The adapter document
exposes the resolved `source_id_field`. EBA player adapters choose `PLAYER`;
EBA team adapters choose `TEAM`. Identifiers are optional, can repeat, and are
not guaranteed globally unique. Missing identities are omitted. Mapped input
can include the identity field explicitly.

Filtered-out inputs have no result. `input_count` includes all submitted/loaded
rows; `mapped_count` counts mapped rows remaining after filters; `scored_count`
counts results. In the current pipeline the latter two match. Optional mapped
rows align with surviving results, whose `source_index` identifies original input.

## Why this score?

Set `output.show_explanation: true` in the API or SDK `output` mapping.
The kernel returns `explanation` per result and `explanation_context` for the run
(shared when compacting). It uses actual components, effective weights after
penalties/overrides, the exact pre-clipping weighted sum, raw aggregate, cohort
percentile when applicable, unrounded final value, and rounded score.

Metric `mapped_value` is the post-adapter value. Raw source fields and adapter
expressions/transforms are described in context; the original input is visible
in the portal and remains available to the submitting caller. Explicit normalization
limits and inversion describe how mapped values become normalized components.

Each contribution is `normalized_value × effective_weight`. Relative influence
is `(normalized_value − 0.5) × effective_weight`, relative to every metric at
its normalized midpoint. This reference is **not** a population average or an
objective ranking. Positive relative influence need not mean a positive weight.

Bucket summaries remain unweighted means of nonzero primary-weight metrics;
they are not additive score points. Contributions apply to the raw weighted sum.
Aggregate clipping, scale, unscoreable overrides, affine/window transforms,
cohort midrank percentiles and rounding determine the final score. A window score
cannot be attributed as fixed per-metric final-score points. Explanation output
is diagnostic and larger than a lean scoring response.

## CORS, source snapshots and release checks

Set `STATLINE_PORTAL_ORIGINS` to exact comma-separated HTTP(S) origins before
starting workers. The joint launcher adds both local origins and validates the
scoring preflight, health and OpenAPI. Extra public origins use `--portal-origin`.
Wildcards, unrelated origins and unrelated headers are not permitted.

Generate portal content with `pnpm generate`, validate `pnpm check-generated`,
then rebuild with `pnpm build`. OpenAPI/download snapshots carry the source
release, generation time and fingerprint. Saved docs remain available during
API outages; live data is checked only when requested.

Build distributions normally, then run `python scripts/check_release.py dist/*.whl dist/*.tar.gz`.
CI scans these artifacts before uploading or publishing. The sanitized source
bundle uses `python scripts/build_release.py /path/outside/repo.zip` (or `.tar`).

## v3 route migration

| Former route family | v4 replacement |
| --- | --- |
| `/v3/map/row`, `/v3/map/batch` | `/v4/score` with `include_mapped: true` |
| `/v3/calc/pri*` | `/v4/score` with `input_kind: "mapped"` |
| `/v3/pri*`, `/v3/score*` | `/v4/score` with raw inputs |
| Separate adapter detail endpoints | `/v4/adapters/{adapter}` |
| `/v3/datasets` | `/v4/datasets` |

The v4 server does not register v3 compatibility routes. CLI transport translates
its existing command vocabulary to v4 and accepts compact metadata. Adapter
compilation is cached per process; `/v4/admin/adapters/refresh` performs an atomic reload.
