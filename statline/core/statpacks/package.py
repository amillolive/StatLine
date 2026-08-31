"""StatPack packaging, rendering, inspection, extraction, and execution functions."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, cast

import yaml

from statline.core.adapters.load import load_spec
from statline.core.adapters.paths import current_adapter_dir
from statline.core.datasets import dataset_root

SECTION_ORDER = (
    "dimensions",
    "sniff",
    "filters",
    "buckets",
    "metrics",
    "efficiency",
    "weights",
    "penalties",
    "score_profiles",
)
SECTION_TITLES = {
    "dimensions": "Dimensions",
    "sniff": "Sniff",
    "filters": "Filters",
    "buckets": "Buckets",
    "metrics": "Metrics",
    "efficiency": "Efficiency",
    "weights": "Weights",
    "penalties": "Penalties",
    "score_profiles": "Score Profiles",
}
STANDARD_RUNNER = """from pathlib import Path\n\nfrom statline.sdk import run_statpack\n\nraise SystemExit(run_statpack(Path(__file__).resolve().parent))\n"""
_REQUIRED_ROOT = ("metadata.yaml", "runner.py")


def _normalize_member(value: str) -> str:
    raw = str(value or "").replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts or ":" in path.parts[0]:
        raise ValueError(f"Unsafe StatPack path: {value!r}")
    return path.as_posix()


def _logical_dataset_path(value: object) -> PurePosixPath:
    raw = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts or ":" in path.parts[0]:
        raise ValueError("metadata.dataset must be a portable relative path")
    return path


def _yaml_mapping(path: Path, *, expected_key: str | None = None) -> dict[str, Any]:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ValueError(f"Invalid YAML in {path}: {error}") from error
    if not isinstance(loaded, Mapping):
        raise TypeError(f"{path} must contain a top-level mapping")
    data = {str(key): value for key, value in cast(Mapping[object, object], loaded).items()}
    if expected_key is not None and set(data) != {expected_key}:
        raise ValueError(f"{path} must contain exactly one top-level key: {expected_key}")
    return data


def dump_yaml(data: Mapping[str, Any]) -> str:
    dumped: str = yaml.safe_dump(
        dict(data),
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=1000,
    )
    return dumped.rstrip() + "\n"


def _write_yaml(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_yaml(data), encoding="utf-8")


def _ensure_source_tree(root: Path) -> None:
    metadata_path = root / "metadata.yaml"
    sections_dir = root / "schema" / "sections"
    if not metadata_path.is_file():
        raise FileNotFoundError(f"StatPack source is missing {metadata_path}")
    metadata = _yaml_mapping(metadata_path, expected_key="metadata")
    meta_value = metadata.get("metadata")
    if not isinstance(meta_value, Mapping):
        raise TypeError("metadata.yaml metadata value must be a mapping")
    if not str(cast(Mapping[object, object], meta_value).get("id") or "").strip():
        raise ValueError("metadata.yaml requires metadata.id")
    if not sections_dir.is_dir() or not any(sections_dir.glob("*.y*ml")):
        raise FileNotFoundError("StatPack source requires schema/sections/*.yaml")
    for path in sorted((*sections_dir.glob("*.yaml"), *sections_dir.glob("*.yml"))):
        _yaml_mapping(path, expected_key=path.stem)
    runner = root / "runner.py"
    if not runner.exists():
        runner.write_text(STANDARD_RUNNER, encoding="utf-8")
    if not runner.is_file():
        raise FileNotFoundError(f"StatPack source is missing {runner}")


def _iter_source_files(root: Path) -> Iterable[tuple[Path, str]]:
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.is_symlink():
            raise ValueError(f"StatPack source cannot include symlinks: {path}")
        relative = _normalize_member(path.relative_to(root).as_posix())
        if relative.startswith(("dataset/", "schema/sections/")) or relative in _REQUIRED_ROOT:
            yield path, relative


def _write_zip(output: Path, files: Iterable[tuple[Path, str]], *, overwrite: bool) -> Path:
    output = output.expanduser().resolve()
    if output.suffix.casefold() != ".statpack":
        output = output.with_suffix(".statpack")
    if output.exists() and not overwrite:
        raise FileExistsError(f"StatPack already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path, member in files:
                info = zipfile.ZipInfo(_normalize_member(member), date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                archive.writestr(info, path.read_bytes())
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def pack_statpack(
    source_directory: str | Path,
    output: str | Path | None = None,
    *,
    overwrite: bool = False,
) -> Path:
    """Package an unpacked StatPack source directory."""
    root = Path(source_directory).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"StatPack source directory not found: {root}")
    _ensure_source_tree(root)
    target = Path(output) if output is not None else root.parent / f"{root.name}.statpack"
    return _write_zip(target, _iter_source_files(root), overwrite=overwrite)


def _find_dataset_for_yaml(source: Path, logical: PurePosixPath) -> Path | None:
    relative = Path(*logical.parts)
    candidates = [
        source.parent / relative,
        source.parent / "dataset" / relative,
        dataset_root() / relative,
    ]
    for ancestor in (source.parent, *source.parents):
        candidates.extend(
            [
                ancestor / "statline" / "core" / "datasets" / relative,
                ancestor / "core" / "datasets" / relative,
            ]
        )
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file():
            return resolved
    return None


def manifest_yaml_statpack(
    adapter_yaml: str | Path,
    output: str | Path | None = None,
    *,
    overwrite: bool = False,
) -> Path:
    """Split one adapter YAML into a portable StatPack."""
    source = Path(adapter_yaml).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Adapter YAML not found: {source}")
    data = _yaml_mapping(source)
    if "metadata" not in data:
        raise KeyError(f"Adapter YAML is missing metadata: {source}")
    metadata = data["metadata"]
    if not isinstance(metadata, Mapping):
        raise TypeError("adapter metadata must be a mapping")

    target = Path(output) if output is not None else source.with_suffix(".statpack")
    with tempfile.TemporaryDirectory(prefix="statline-manifest-") as temporary:
        root = Path(temporary) / source.stem
        _write_yaml(
            root / "metadata.yaml", {"metadata": dict(cast(Mapping[object, Any], metadata))}
        )
        (root / "runner.py").write_text(STANDARD_RUNNER, encoding="utf-8")
        for section in SECTION_ORDER:
            if section in data:
                _write_yaml(
                    root / "schema" / "sections" / f"{section}.yaml", {section: data[section]}
                )
        extras = [key for key in data if key not in {"metadata", *SECTION_ORDER}]
        if extras:
            raise KeyError(f"Unsupported adapter top-level section(s): {', '.join(sorted(extras))}")

        dataset_value = cast(Mapping[object, object], metadata).get("dataset")
        if dataset_value:
            logical = _logical_dataset_path(dataset_value)
            dataset = _find_dataset_for_yaml(source, logical)
            if dataset is None:
                raise FileNotFoundError(
                    f"Dataset referenced by metadata.dataset was not found: {logical.as_posix()}"
                )
            destination = root / "dataset" / Path(*logical.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dataset, destination)
        return pack_statpack(root, target, overwrite=overwrite)


def manifest_statpack(
    source: str | Path,
    output: str | Path | None = None,
    *,
    overwrite: bool = False,
) -> Path:
    """Manifest a directory or adapter YAML into a .statpack package."""
    path = Path(source).expanduser().resolve()
    if path.is_dir():
        return pack_statpack(path, output, overwrite=overwrite)
    if path.is_file() and path.suffix.casefold() in {".yaml", ".yml"}:
        return manifest_yaml_statpack(path, output, overwrite=overwrite)
    raise ValueError("manifest source must be a StatPack directory or adapter YAML")


def _safe_archive_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members: list[zipfile.ZipInfo] = []
    for info in archive.infolist():
        member = _normalize_member(info.filename)
        if member != info.filename.replace("\\", "/").lstrip("./"):
            raise ValueError(f"Non-canonical StatPack entry: {info.filename!r}")
        mode = (info.external_attr >> 16) & 0o170000
        if mode == 0o120000:
            raise ValueError(f"StatPack cannot contain symlinks: {info.filename}")
        members.append(info)
    names = {info.filename for info in members if not info.is_dir()}
    missing = [name for name in _REQUIRED_ROOT if name not in names]
    if missing:
        raise FileNotFoundError(f"StatPack is missing required file(s): {', '.join(missing)}")
    if not any(
        name.startswith("schema/sections/") and name.endswith((".yaml", ".yml")) for name in names
    ):
        raise FileNotFoundError("StatPack is missing schema/sections YAML files")
    return members


def unpack_statpack(
    pack: str | Path,
    output_directory: str | Path | None = None,
    *,
    overwrite: bool = False,
) -> Path:
    """Safely extract a StatPack for editing."""
    source = Path(pack).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"StatPack not found: {source}")
    target = (
        Path(output_directory).expanduser().resolve()
        if output_directory is not None
        else source.with_suffix("")
    )
    if target.exists():
        if not overwrite:
            if target.is_dir() and not any(target.iterdir()):
                pass
            else:
                raise FileExistsError(f"Unpack destination already exists: {target}")
        elif target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    target.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(source, "r") as archive:
            members = _safe_archive_members(archive)
            for info in members:
                destination = (target / PurePosixPath(info.filename)).resolve()
                try:
                    destination.relative_to(target)
                except ValueError as error:
                    raise ValueError(f"Unsafe StatPack entry: {info.filename}") from error
                if info.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with (
                    archive.open(info, "r") as source_handle,
                    destination.open("wb") as target_handle,
                ):
                    shutil.copyfileobj(source_handle, target_handle)
        _ensure_source_tree(target)
        return target
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise


def load_statpack_tree(root: Path) -> dict[str, Any]:
    _ensure_source_tree(root)
    combined = _yaml_mapping(root / "metadata.yaml", expected_key="metadata")
    sections_dir = root / "schema" / "sections"
    discovered: dict[str, Any] = {}
    for path in sorted((*sections_dir.glob("*.yaml"), *sections_dir.glob("*.yml"))):
        section_data = _yaml_mapping(path, expected_key=path.stem)
        discovered[path.stem] = section_data[path.stem]
    ordered = [*SECTION_ORDER, *sorted(set(discovered).difference(SECTION_ORDER))]
    for section_name in ordered:
        if section_name in discovered:
            combined[section_name] = discovered[section_name]
    return combined


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _section_banner(section: str) -> str:
    title = SECTION_TITLES.get(section, section.replace("_", " ").title())
    line = "# " + "─" * 77
    return f"{line}\n# {title}\n{line}\n"


def _render_text(data: Mapping[str, Any], *, source: Path, output: Path) -> str:
    line = "# " + "─" * 77
    header = (
        f"{line}\n"
        "# StatLine Adapter Schema\n"
        f"{line}\n"
        "# AUTO-GENERATED RUNTIME ADAPTER — DO NOT EDIT DIRECTLY\n"
        f"# Source: {_display_path(source)}\n"
        f"# Output: {_display_path(output)}\n"
        f"# Version: {data['metadata']['version']}\n"
        "#\n"
        "# This file is compiler-generated and verified for StatLine.\n"
        "# Manual edits bypass validation and may cause scoring, parsing, or runtime failures.\n"
        "# To modify this adapter, edit the source StatPack listed above, then recompile.\n"
        f"{line}\n\n"
    )
    pieces = [header, dump_yaml({"metadata": data["metadata"]}).rstrip(), "\n\n"]
    for section in [*SECTION_ORDER, *sorted(set(data).difference({"metadata", *SECTION_ORDER}))]:
        if section not in data:
            continue
        pieces.extend(
            [_section_banner(section), "\n", dump_yaml({section: data[section]}).rstrip(), "\n\n"]
        )
    return "".join(pieces).rstrip() + "\n"


def render_statpack(
    pack: str | Path,
    output: str | Path | None = None,
    *,
    overwrite: bool = False,
) -> Path:
    """Render a StatPack into one validated runtime adapter YAML."""
    source = Path(pack).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"StatPack not found: {source}")
    target = (
        Path(output).expanduser().resolve()
        if output is not None
        else current_adapter_dir() / f"{source.stem}.yaml"
    )
    if target.exists() and not overwrite:
        raise FileExistsError(f"Runtime adapter already exists: {target}")

    with tempfile.TemporaryDirectory(prefix="statline-render-") as temporary:
        root = unpack_statpack(source, Path(temporary) / source.stem)
        data = load_statpack_tree(root)
        validation_path = Path(temporary) / "validation.yaml"
        validation_path.write_text(dump_yaml(data), encoding="utf-8")
        load_spec(validation_path)
        text = _render_text(data, source=source, output=target)

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = target.with_name(f".{target.name}.tmp")
    temporary_output.write_text(text, encoding="utf-8")
    os.replace(temporary_output, target)
    if target.parent.resolve() == current_adapter_dir().resolve():
        from statline.core.adapters import refresh_adapters

        refresh_adapters()
    return target


def inspect_statpack(pack: str | Path) -> dict[str, Any]:
    """Return package metadata and contents without executing runner.py."""
    source = Path(pack).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"StatPack not found: {source}")
    with zipfile.ZipFile(source, "r") as archive:
        members = _safe_archive_members(archive)
        metadata_raw = archive.read("metadata.yaml").decode("utf-8")
        metadata_loaded = yaml.safe_load(metadata_raw)
        metadata: dict[str, Any] = {}
        if isinstance(metadata_loaded, Mapping):
            loaded_root = cast(Mapping[object, object], metadata_loaded)
            metadata_value = loaded_root.get("metadata")
            if isinstance(metadata_value, Mapping):
                metadata = {
                    str(key): value
                    for key, value in cast(Mapping[object, object], metadata_value).items()
                }
        files = sorted(info.filename for info in members if not info.is_dir())
    return {
        "path": str(source),
        "metadata": metadata,
        "files": files,
        "sections": [
            PurePosixPath(name).stem
            for name in files
            if name.startswith("schema/sections/") and name.endswith((".yaml", ".yml"))
        ],
        "datasets": [
            name.removeprefix("dataset/") for name in files if name.startswith("dataset/")
        ],
        "runnable": "runner.py" in files,
    }


def execute_statpack(
    pack: str | Path,
    arguments: Sequence[str] = (),
    *,
    inherit_secrets: bool = False,
) -> int:
    """Extract a trusted StatPack and execute its root runner.py in a subprocess."""
    source = Path(pack).expanduser().resolve()
    with tempfile.TemporaryDirectory(prefix="statline-run-") as temporary:
        root = unpack_statpack(source, Path(temporary) / source.stem)
        runner = root / "runner.py"
        env = dict(os.environ)
        env["STATLINE_STATPACK"] = str(source)
        project_root = str(Path(__file__).resolve().parents[3])
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            project_root
            if not existing_pythonpath
            else project_root + os.pathsep + existing_pythonpath
        )
        if not inherit_secrets:
            for key in tuple(env):
                upper = key.upper()
                if upper in {"STATLINE_API_KEY", "SLAPI_API_KEY"} or upper.endswith(
                    ("_TOKEN", "_SECRET", "_PASSWORD", "_PRIVATE_KEY")
                ):
                    env.pop(key, None)
        completed = subprocess.run(
            [sys.executable, str(runner), *[str(value) for value in arguments]],
            cwd=root,
            env=env,
            check=False,
        )
        return int(completed.returncode)


def write_statpack_report(pack: str | Path, output: str | Path) -> Path:
    """Write inspection metadata as JSON for tooling."""
    target = Path(output).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(inspect_statpack(pack), indent=2) + "\n", encoding="utf-8")
    return target


__all__ = [
    "SECTION_ORDER",
    "STANDARD_RUNNER",
    "dump_yaml",
    "execute_statpack",
    "inspect_statpack",
    "load_statpack_tree",
    "manifest_statpack",
    "manifest_yaml_statpack",
    "pack_statpack",
    "render_statpack",
    "unpack_statpack",
    "write_statpack_report",
]
