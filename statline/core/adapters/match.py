"""Match adapters and datasets for automatic scoring."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from statline.core.adapters.registry import list_adapters, load_adapter
from statline.core.datasets import dataset_root
from statline.core.types.adapters import CompiledAdapter

PathLike = str | Path


def _normalize_dataset(value: object) -> str:
    """Return a case-insensitive portable dataset name."""
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return ""
    return PurePosixPath(raw).as_posix().removeprefix("./").casefold()


def _inside_root(path: Path, root: Path) -> Path:
    """Resolve a path and reject anything outside the dataset root."""
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError(f"Dataset must be inside the StatLine dataset root: {root}") from error
    return resolved


def resolve_dataset_path(value: PathLike) -> Path:
    """Resolve an explicit or dataset-root-relative CSV path."""
    root = dataset_root().resolve()
    supplied = Path(value).expanduser()

    candidate = supplied if supplied.is_absolute() else root / supplied
    resolved = _inside_root(candidate, root)

    if not resolved.is_file():
        raise FileNotFoundError(f"Dataset does not exist: {resolved}")
    if resolved.suffix.casefold() != ".csv":
        raise ValueError(f"Dataset must be a CSV file: {resolved}")

    return resolved


def dataset_name(value: PathLike) -> str:
    """Return the portable path of a CSV relative to the dataset root."""
    root = dataset_root().resolve()
    return resolve_dataset_path(value).relative_to(root).as_posix()


def dataset_for_adapter(adapter: str | CompiledAdapter) -> Path:
    """Resolve the dataset declared by an adapter's metadata."""
    compiled = load_adapter(adapter) if isinstance(adapter, str) else adapter
    declared = compiled.metadata.dataset

    if not declared:
        raise ValueError(f"Adapter '{compiled.metadata.id}' does not declare metadata.dataset")

    return resolve_dataset_path(declared)


def adapter_for_dataset(dataset: PathLike) -> CompiledAdapter:
    """Return the single adapter declaring the supplied dataset."""
    relative = dataset_name(dataset)
    target = _normalize_dataset(relative)
    matches: list[CompiledAdapter] = []

    for name in list_adapters():
        adapter = load_adapter(name)
        declared = adapter.metadata.dataset
        if declared and _normalize_dataset(declared) == target:
            matches.append(adapter)

    if not matches:
        raise ValueError(
            f"No adapter declares dataset '{relative}'. "
            "Provide --adapter or add metadata.dataset to an adapter."
        )

    if len(matches) > 1:
        names = ", ".join(sorted(adapter.metadata.id for adapter in matches))
        raise ValueError(f"Dataset '{relative}' is declared by multiple adapters: {names}")

    return matches[0]


def resolve_scoring_target(
    *,
    adapter: str | None = None,
    dataset: PathLike | None = None,
) -> tuple[CompiledAdapter, Path]:
    """Resolve the adapter and CSV for every supported score invocation.

    Supported forms:

        statline score --adapter eba.players
        statline score path/to/file.csv
        statline score --adapter eba.players path/to/file.csv

    An explicit adapter is authoritative. An explicit dataset is authoritative.
    Missing values are inferred from adapter metadata in either direction.
    """
    adapter_name = str(adapter or "").strip()
    dataset_missing = dataset is None or not str(dataset).strip()

    if not adapter_name and dataset_missing:
        raise ValueError("Scoring requires an adapter, a dataset, or both.")

    if adapter_name:
        compiled = load_adapter(adapter_name)

        if dataset is None or not str(dataset).strip():
            path = dataset_for_adapter(compiled)
        else:
            path = resolve_dataset_path(dataset)

        return compiled, path

    if dataset is None or not str(dataset).strip():
        raise ValueError("A dataset is required when no adapter is provided.")

    path = resolve_dataset_path(dataset)
    return adapter_for_dataset(path), path


__all__ = [
    "adapter_for_dataset",
    "dataset_for_adapter",
    "dataset_name",
    "resolve_dataset_path",
    "resolve_scoring_target",
]
