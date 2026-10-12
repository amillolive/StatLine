# StatLine Native Pairwise v1 Example

This adapter runs as a normal StatPack in the StatLine v4 implementation. It
embeds two match StatPacks and performs its entire Elo calculation from YAML;
no Python is needed from the adapter author.

From the repository root:

```powershell
python -m statline statpack manifest examples/native_pairwise_v1/pairwise-elo.yaml --out native-elo.statpack
python -m statline statpack run native-elo.statpack --format csv --no-timing
```

The expected output is in `expected.csv`. The `a` input contains finish
positions and current team affiliation. The `b` input supplies pre-race Elo and
round counts, paired by `id` rather than by row number. `a` defines the roster;
`b` may contain additional inactive drivers without affecting results.

For programmatic use, `statline.sdk.score_native_pairwise(spec, a, b)` accepts
already parsed rows; `read_inputs(spec, root)` reads safe CSV/JSON/StatPack
inputs without executing their embedded runner files.

The gateway also exposes `POST /v4/pairwise` to evaluate *registered* native
pairwise adapters with request fields `adapter`, `a`, and `b`. This endpoint
never accepts executable code from the request.

Optional score-profile naming:

```yaml
score_profiles:
  ELO: {kind: pairwise, model: native, value: newElo}
```

This creates an additional `ELO` result column; it does not alter weighted PRI.
If you explicitly name that profile `PRI`, its output is your Elo computation,
not legacy PRI normalization.
