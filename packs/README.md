# StatLine v4 PARALLAX · canonical StatPacks

One ownership boundary per game/implementation family and organization:

```text
packs/
  <game>/
    <org>/
      workspace/                   # draft/test, excluded from discovery and wheel
        <impl>/
          adapter.yaml             # single authorable YAML (regular or RELATE)
          inputs/                 # CSV, JSON, nested StatPacks for pairwise
          ...                     # optional unpacked StatPack source instead
      data/
        <impl>/                    # canonical discoverable CSVs, one owner
          dataset.csv
      <impl>/                     # deployed unpacked StatPack
        metadata.yaml
        schema/sections/*.yaml
        dataset/                  # self-contained datasets carried by the pack
        inputs/                   # self-contained RELATE pairwise snapshots
        runner.py
        <impl>.yaml               # generated discoverable adapter; DO NOT EDIT
      <impl>.statpack             # portable shared, publishable archive
  retired/                        # explicit-path legacy archives only
```

Existing shipped sources follow the same ownership layout under
`basketball/eba/players`, `basketball/eba/teams`,
`basketball/eba/e302`, `rocket-league/urrl/players`, etc.
The former `statline/public/statpacks/packed` archive examples now live under
`packs/` too; `statline/public.py` remains the public Python SDK and
`portal/public/` remains the Next.js public static-assets directory.

`list_datasets()` returns canonical paths such as
`basketball/eba/data/players/EBA_Elevate302/eba_s1_players.csv`.
Legacy names such as `EBA_Elevate302/eba_s1_players.csv` continue to resolve
when unambiguous, preserving existing API calls, YAML metadata and external
StatPack dataset names. Ambiguous short names raise errors. Workspace files
are NEVER registered by name; load them by explicit YAML path.

## Fast iteration: edit → validate and manifest → deploy → share

```powershell
# Edit these in packs/<game>/<org>/workspace/<impl>:
#   adapter.yaml, dataset/your-data.csv and/or inputs/*.statpack

python -m statline --mode local statpack manifest packs/<game>/<org>/workspace/<impl>/adapter.yaml --out packs/<game>/<org>/workspace/<impl>/<impl>.statpack --force
python -m statline --mode local statpack inspect packs/<game>/<org>/workspace/<impl>/<impl>.statpack
python -m statline --mode local statpack run packs/<game>/<org>/workspace/<impl>/<impl>.statpack --format csv --no-timing
python -m statline --mode local statpack deploy packs/<game>/<org>/workspace/<impl>/<impl>.statpack --game <game> --org <org> --impl <impl> --force
```

Manifest/package operations now compile and validate full adapters before
publishing the archive. Deploy performs validation and safe extraction,
installs a runtime YAML, mirrors packaged datasets under `data/<impl>`, and
never executes `runner.py`. It replaces an old installed implementation only
when `--force` is passed, and preserves the editing workspace on every deploy.

For traditional score adapters, `metadata.dataset` is a portable dataset path
under `dataset/` in the StatPack. For native RELATE pairwise, use
`pairwise.version: 1`, put side A/B inputs under `inputs/`, reference them
with relative paths, and use `expressions.pair` / `expressions.result`. A/B
may be CSV, JSON, or nested StatPacks. Never refer to absolute host paths;
never ship credentials. See `PAIRWISE_QUICKSTART.md`.
