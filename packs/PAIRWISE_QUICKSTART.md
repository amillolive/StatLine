# Fastest RELATE / SFE native pairwise workflow · v4 PARALLAX

Use the **single-adapter YAML** path during development: only split YAML into
StatPack sections for release. A new user can get a working model by copying
`examples/native_pairwise_v1/pairwise-elo.yaml` and its `inputs/` directory.

1. **Create the workspace** at `packs/<game>/<org>/workspace/<impl>/`. Put
   `adapter.yaml` there and `inputs/next-match.statpack` and
   `inputs/previous-match.statpack` (or CSV/JSON). You can copy the example:
   `cp -R examples/native_pairwise_v1 <your-workspace>/elo` (or PowerShell
   `Copy-Item ... -Recurse`). Rename `pairwise-elo.yaml` to `adapter.yaml`.
2. **Identify stable participants.** `pairwise.join.a_key` and `.b_key` must
   reference unique, stable IDs; all A participants must exist in B.
   A is today's **new** performance; B is immutable **old** history.
   Check duplicates, missing history, non-starters, scoring conventions, and
   whether each input is a raw CSV or a nested scoring StatPack.
3. **Declare the calculation**, once, in `adapter.yaml`. `pairwise.version: 1`,
   `inputs.a/b.{kind,path,dataset}`, one-to-one join, `participants: a`,
   `opponents: a`, `exclude_self: true`, `preserve_order: true`,
   `expression: pair.delta`, `aggregate: sum`, `normalize: false`,
   `outputs: result`. Implement pure, sandboxed `expressions.pair` and
   `expressions.result` using SFE. Optional `score_profiles.ELO` points to
   `newElo`. No external Python `eval`, no custom scoring runner, no network.
4. **Validate and create the distribution archive** from the YAML (the manifest
   operation compiles it, checks expressions and requires every declared input):

   ```powershell
   python -m statline --mode local statpack manifest packs/<game>/<org>/workspace/<impl>/adapter.yaml --out packs/<game>/<org>/workspace/<impl>/<impl>.statpack --force
   python -m statline --mode local statpack inspect packs/<game>/<org>/workspace/<impl>/<impl>.statpack
   ```
5. **Run it against the portable snapshots**, without a Gateway or device login:

   ```powershell
   python -m statline --mode local statpack run packs/<game>/<org>/workspace/<impl>/<impl>.statpack --format csv --no-timing
   ```

   Compare against known reference outputs and edge cases before deployment.
   RELATE joins by ID regardless of B row order, excludes self matches,
   rejects duplicate/missing participants, and enforces a pairwise work limit.
   For N participants the current implementation is O(N²).
6. **Deploy the validated implementation** into the organization's canonical
   directories. This also makes its flattened model discoverable by adapter ID:

   ```powershell
   python -m statline --mode local statpack deploy packs/<game>/<org>/workspace/<impl>/<impl>.statpack --game <game> --org <org> --impl <impl> --force
   python -m statline --mode local adapter list
   ```

7. **Share** `packs/<game>/<org>/<impl>.statpack`; recipients can inspect, run,
   or deploy it. Do not share workspace files or locally stored signing keys.
   Importing/installation never executes a pack's `runner.py`; explicitly
   running a received pack executes Python and requires a trusted source.

**Performance path:** SFE compiles expression graphs and caches reusable
plans; keep one model and process the whole cohort in one run. Avoid repeated
per-player manifest/compile cycles. The pair loop still performs pair
comparisons, so the throughput bottleneck at larger N is O(N²), not YAML
parsing. Measure real datasets rather than assuming unlimited speedups.
