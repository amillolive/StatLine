"""Generate portal artifacts by executing the existing StatLine product."""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import inspect
import io
import json
import os
import pathlib
import re
import shlex
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any, cast

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

import yaml
from source_contract import source_fingerprint

PORTAL = pathlib.Path(__file__).resolve().parents[1]


def write_json(path: pathlib.Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_documents(source: pathlib.Path) -> list[dict[str, str]]:
    """Hash present source documents; optional docs may not ship in all checkouts."""
    paths = ("README.md", "statline/gateway/V4_API.md", "ARCHITECTURE.md")
    return [
        {"path": name, "sha256": sha256(source / name)}
        for name in paths
        if (source / name).is_file()
    ]


def describe_functions(module: object, names: list[str]) -> list[dict[str, str]]:
    functions: list[dict[str, str]] = []
    for name in names:
        value = getattr(module, name, None)
        if inspect.isfunction(value):
            functions.append(
                {
                    "name": name,
                    "signature": str(inspect.signature(value)),
                    "description": inspect.getdoc(value) or "",
                }
            )
    return functions


def generate(source: pathlib.Path, output: pathlib.Path) -> None:
    if not (source / "statline" / "public.py").is_file():
        raise SystemExit("Set STATLINE_SOURCE to a complete StatLine source checkout.")
    sys.path.insert(0, str(source))
    fingerprint = source_fingerprint(source)
    generated_at = datetime.now(timezone.utc).isoformat()
    # OpenAPI generation must not touch a production authentication database.
    with tempfile.TemporaryDirectory(prefix="statline-portal-auth-") as auth_dir:
        os.environ["STATLINE_DATA_DIR"] = auth_dir
        os.environ["STATLINE_SECRETS_DIR"] = str(pathlib.Path(auth_dir) / "secrets")
        from statline import __version__, load_dataset, public, score
        from statline.core import statpacks
        from statline.core.adapters.paths import resolve_adapter_path
        from statline.core.datasets import resolve_dataset
        from statline.gateway.adapters.envelope import expand_results
        from statline.gateway.http.app import app
        from statline.gateway.http.models import ScoreIn
        from statline.gateway.http.resources import (
            adapter_catalog,
            adapter_document,
            dataset_catalog,
        )
        from statline.gateway.http.routes_api import score as gateway_score
        from statline.sdk import run_statpack

        project = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        spec = dict(app.openapi())
        spec["x-statline-snapshot"] = {
            "source_release": __version__,
            "generated_at": generated_at,
            "source_sha256": fingerprint,
            "response_format": "compact opt-in; legacy default",
        }
        downloads = output / "public" / "downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        write_json(output / "data" / "openapi.json", spec)
        write_json(downloads / "openapi.json", spec)
        operations = [
            {"path": path, "method": method, **operation}
            for path, methods in spec["paths"].items()
            for method, operation in methods.items()
            if method in ("get", "post", "put", "patch", "delete", "options", "head")
        ]
        paths = {op["operationId"]: op["path"] for op in operations if "operationId" in op}
        snapshots: list[dict[str, Any]] = []
        # This is the explicit editorial selection, not a second adapter registry.
        demos = [
            ("eba.players", "EBA_Elevate302/eba_players_gp3_plus.csv"),
            ("eba.teams", "EBA_Elevate302/eba_s1_teams.csv"),
        ]
        for adapter_id, dataset in demos:
            schema_path = resolve_adapter_path(adapter_id)
            schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
            document = adapter_document(adapter_id)
            dataset_path = resolve_dataset(dataset, allow_external=False)
            rows = load_dataset(dataset)
            results = score(adapter_id, rows, mode="batch", weights="pri")
            assert isinstance(results, list) and len(rows) == len(results)
            assert score(adapter_id, rows, mode="batch", weights="pri") == results
            diagnostic_output = {
                "show_weights": True,
                "hide_pri_raw": False,
                "show_components": True,
                "show_buckets": True,
                "show_context_used": True,
                "show_explanation": True,
            }
            explained_results = score(
                adapter_id, rows, mode="batch", weights="pri", output=diagnostic_output
            )
            assert isinstance(explained_results, list)
            assert [
                {
                    key: value
                    for key, value in result.items()
                    if key not in {"explanation", "explanation_context"}
                }
                for result in explained_results
            ] == results
            request = {
                "adapter": adapter_id,
                "dataset": dataset,
                "weights": "pri",
                "input_kind": "raw",
                "caps_mode": "batch",
                "include_mapped": True,
                "response_format": "compact",
                "output": diagnostic_output,
            }
            gateway_output = asyncio.run(gateway_score(ScoreIn.model_validate(request)))
            expanded = expand_results(gateway_output["shared"], gateway_output["results"])
            assert [
                {
                    key: value
                    for key, value in result.items()
                    if key not in {"source_index", "source_id"}
                }
                for result in expanded
            ] == explained_results, "Python and gateway scoring differ"
            required = schema["sniff"]["require_all_headers"]
            assert all(set(required).issubset(row) for row in rows)
            metric_buckets = {
                m["key"]: m["bucket"] for m in schema["metrics"] + schema.get("efficiency", [])
            }
            bucket_weights: dict[str, float] = {}
            for metric, weight in results[0]["weights"].items():
                bucket = metric_buckets[metric]
                bucket_weights[bucket] = bucket_weights.get(bucket, 0) + weight
            stem = adapter_id.replace(".", "-")
            filename = dataset_path.name
            result_file = stem + "-results.json"
            pack_file = adapter_id.replace(".", "_") + ".statpack"
            csv_file = stem + "-scored.csv"
            bundle = {
                "engine_version": __version__,
                "generated_at": generated_at,
                "source_sha256": fingerprint,
                "adapter": adapter_id,
                "adapter_version": document["version"],
                "dataset": dataset,
                "mode": "batch",
                "weights": "pri",
                "dataset_sha256": sha256(dataset_path),
                "schema_sha256": sha256(schema_path),
                "api_request": request,
                "api_response": gateway_output,
                "rows": [{"source_index": i, "input": row} for i, row in enumerate(rows)],
            }
            write_json(downloads / result_file, bundle)
            (downloads / filename).write_bytes(dataset_path.read_bytes())
            with tempfile.TemporaryDirectory(prefix="statline-portal-pack-") as temp:
                tree = pathlib.Path(temp) / "pack"
                (tree / "schema" / "sections").mkdir(parents=True)
                (tree / "metadata.yaml").write_text(
                    statpacks.dump_yaml({"metadata": dict(schema["metadata"], dataset=dataset)}),
                    encoding="utf-8",
                )
                (tree / "runner.py").write_text(statpacks.STANDARD_RUNNER, encoding="utf-8")
                for section in statpacks.SECTION_ORDER:
                    if section in schema:
                        (tree / "schema" / "sections" / (section + ".yaml")).write_text(
                            statpacks.dump_yaml({section: schema[section]}), encoding="utf-8"
                        )
                packed_data = tree / "dataset" / dataset
                packed_data.parent.mkdir(parents=True)
                packed_data.write_bytes(dataset_path.read_bytes())
                pack = statpacks.pack_statpack(tree, downloads / pack_file, overwrite=True)
                # Keep a canonical, runnable bundle next to the authorable YAML.
                # Temporary --output generation must not modify the source checkout.
                if output.resolve() == (source / "portal").resolve():
                    target = schema_path.with_suffix(".statpack")
                    target.write_bytes(pack.read_bytes())
                report = statpacks.inspect_statpack(pack)
                rendered = statpacks.render_statpack(pack, pathlib.Path(temp) / "rendered.yaml")
                assert score(str(rendered), rows, mode="batch", weights="pri") == results
                # Reuse StatLine's own CSV renderer; no portal export API or CSV pipeline.
                assert (
                    run_statpack(
                        tree,
                        [
                            "--format",
                            "csv",
                            "--weights-profile",
                            "pri",
                            "--output",
                            str(downloads / csv_file),
                            "--no-timing",
                        ],
                        stdout=io.StringIO(),
                        stderr=io.StringIO(),
                    )
                    == 0
                )
            # Add explicit reconciliation columns to the existing SDK CSV export.
            csv_path = downloads / csv_file
            with csv_path.open(encoding="utf-8", newline="") as stream:
                reader = csv.DictReader(stream)
                csv_fields = list(reader.fieldnames or [])
                exported = list(reader)
            assert len(exported) == len(gateway_output["results"])
            identity_field = document["source_id_field"]
            with csv_path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(
                    stream, fieldnames=["source_index", "source_id", *csv_fields]
                )
                writer.writeheader()
                for flat, result in zip(exported, gateway_output["results"], strict=True):
                    flat_identity = next(
                        (
                            value
                            for key, value in flat.items()
                            if key.casefold() == str(identity_field).casefold()
                        ),
                        None,
                    )
                    assert str(flat_identity) == str(result.get("source_id"))
                    writer.writerow(
                        {
                            "source_index": result["source_index"],
                            "source_id": result.get("source_id"),
                            **flat,
                        }
                    )
            report.pop("path", None)
            write_json(downloads / (stem + "-pack.json"), report)
            snapshot = {
                key: value for key, value in bundle.items() if key not in ("rows", "api_response")
            }
            snapshot.update(
                title=document["title"],
                profiles=schema["score_profiles"],
                inputs=required,
                aliases=document["aliases"],
                buckets=schema["buckets"],
                weights=schema["weights"],
                normalized_weights=bucket_weights,
                explanation_context=gateway_output["shared"].get("explanation_context"),
                filters=document["filters"],
                count=len(rows),
                result_file=result_file,
                dataset_file=filename,
                pack_file=pack_file,
                csv_file=csv_file,
                pack_contents=report,
                adapter_document=document,
                stat_fields=[
                    key
                    for key in required
                    if key != "GP" and isinstance(rows[0].get(key), (int, float))
                ][:4],
                rows=[
                    {
                        "id": str(gateway_output["results"][i]["source_index"]),
                        "source_index": gateway_output["results"][i]["source_index"],
                        "source_id": gateway_output["results"][i].get("source_id"),
                        "primary_profile": result["primary_profile"],
                        "explanation": gateway_output["results"][i].get("explanation"),
                        "name": str(row.get("PLAYER", row["TEAM"])),
                        "team": str(row["TEAM"]),
                        "gp": row["GP"],
                        "conference": str(row.get("CONFERENCE", "")),
                        "position": str(row.get("POSITION", "")),
                        "input": row,
                        "mapped": gateway_output["mapped"][i],
                        "pri": result["pri"],
                        "pri_raw": result["pri_raw"],
                        "scores": result["scores"],
                        "buckets": result["buckets"],
                    }
                    for i, (row, result) in enumerate(zip(rows, results))
                ],
            )
            snapshots.append(snapshot)
        first = snapshots[0]
        python_example = (
            "from statline import load_dataset, score\n\n"
            f"rows = load_dataset({first['dataset']!r})\n"
            f"results = score({first['adapter']!r}, rows, mode='batch', weights='pri')\n\n"
            "ranked = sorted(zip(rows, results),\n"
            "    key=lambda pair: (-pair[1]['pri'], -pair[1]['pri_raw']))\n"
            "for row, result in ranked[:3]:\n"
            "    print(row['PLAYER'], result['pri'])"
        )
        http_example = "\n".join(
            [
                'curl "$STATLINE_API_URL' + paths["score_v4"] + '" ' + chr(92),
                '  -H "Authorization: Bearer $STATLINE_API_KEY" ' + chr(92),
                '  -H "Content-Type: application/json" ' + chr(92),
                "  -d " + shlex.quote(json.dumps(first["api_request"], indent=2)),
            ]
        )
        readme = (source / "README.md").read_text(encoding="utf-8")
        highlights_heading = f"## v{__version__} highlights"
        highlights = (
            re.findall(
                r"^\* (.+)$",
                readme.split(highlights_heading, 1)[-1].split("\n---", 1)[0],
                re.MULTILINE,
            )
            if highlights_heading in readme
            else []
        )
        manifest: dict[str, Any] = {
            "engine_version": __version__,
            "generated_at": generated_at,
            "release_channels": {
                "stable": {
                    "line": "v3",
                    "branch": "main",
                    "href": "https://github.com/amillolive/StatLine/tree/main",
                },
                "preview": {
                    "version": __version__,
                    "branch": "next",
                    "href": "https://github.com/amillolive/StatLine/tree/next",
                },
            },
            "requires_python": project["requires-python"],
            "source_sha256": fingerprint,
            "release_highlights": highlights,
            "openapi_version": spec["openapi"],
            "operation_count": len(operations),
            "schema_count": len(spec.get("components", {}).get("schemas", {})),
            "paths": paths,
            "adapter_catalog": adapter_catalog(),
            "dataset_catalog": dataset_catalog(),
            "public_sdk": describe_functions(public, public.__all__),
            "statpack_sdk": describe_functions(statpacks, statpacks.__all__),
            "statpack_sections": list(statpacks.SECTION_ORDER),
            "statpack_runner": statpacks.STANDARD_RUNNER,
            "python_example": python_example,
            "http_example": http_example,
            "documentation": {
                "openapi": app.openapi_url,
                "swagger": app.docs_url,
                "redoc": app.redoc_url,
            },
            "scoring_output_fields": list(cast(dict[str, Any], first["rows"][0]["scores"])),
            "optional_dependencies": sorted(project.get("optional-dependencies", {})),
            "project_urls": {
                **project.get("urls", {}),
                "Repository": "https://github.com/amillolive/StatLine/tree/next",
            },
            "source_documents": source_documents(source),
        }
        write_json(output / "data" / "product.json", manifest)
        write_json(downloads / "product.json", manifest)
        docs_manifest = {
            "current": __version__,
            "generated_at": generated_at,
            "versions": [
                {
                    "version": __version__,
                    "channel": "prerelease"
                    if re.search(r"(?:a|b|rc|dev)\d", __version__)
                    else "stable",
                    "href": "/docs",
                    "source_sha256": fingerprint,
                }
            ],
        }
        write_json(output / "data" / "docs-manifest.json", docs_manifest)
        write_json(downloads / "docs-manifest.json", docs_manifest)
        write_json(
            output / "data" / "eba-snapshot.json",
            {
                "engine_version": __version__,
                "generated_at": generated_at,
                "source_sha256": fingerprint,
                "snapshots": snapshots,
            },
        )
        print(
            f"Generated {len(operations)} API operations, {len(snapshots)} EBA demos, "
            f"{sum(item['count'] for item in snapshots)} genuine engine results"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=pathlib.Path,
        default=pathlib.Path(os.getenv("STATLINE_SOURCE", str(PORTAL.parent))),
    )
    parser.add_argument("--output", type=pathlib.Path, default=PORTAL)
    args = parser.parse_args()
    generate(args.source.expanduser().resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
