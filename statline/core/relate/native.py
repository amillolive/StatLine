"""Native v1 snapshot comparator used by the normal StatPack runtime."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from statline.core.formula import FormulaError, StatLineFormulaEngine
from statline.core.relate.errors import PairwiseError
from statline.core.relate.sources import load_pairwise_rows
from statline.core.types.adapters import NativePairwiseSpec


def portable_input(root: Path, logical: str) -> Path:
    normalized = logical.replace("\\", "/")
    candidate = PurePosixPath(normalized)
    if (
        not normalized
        or candidate.is_absolute()
        or ".." in candidate.parts
        or ":" in candidate.parts[0]
    ):
        raise PairwiseError(f"Invalid portable pairwise input path: {logical!r}")
    result = (root / Path(*candidate.parts)).resolve()
    try:
        result.relative_to(root.resolve())
    except ValueError as error:
        raise PairwiseError(f"Pairwise input escapes package: {logical!r}") from error
    if not result.is_file() or result.is_symlink():
        raise PairwiseError(f"Pairwise input not found: {logical!r}")
    return result


def read_inputs(
    spec: NativePairwiseSpec, root: Path, *, a: Path | None = None, b: Path | None = None
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    result: dict[str, list[dict[str, object]]] = {}
    for side, override in (("a", a), ("b", b)):
        descriptor = spec.inputs[side]
        expected_kind = descriptor["kind"]
        source = (
            Path(override).expanduser().resolve()
            if override is not None
            else portable_input(root, descriptor["path"])
        )
        extension = ".statpack" if expected_kind == "statpack" else "." + expected_kind
        if source.suffix.casefold() != extension:
            raise PairwiseError(f"{side}: expected {expected_kind} input, got {source.name}")
        result[side] = load_pairwise_rows(source, dataset=descriptor.get("dataset"))
    return result["a"], result["b"]


def _id(row: Mapping[str, object], field: str, source: str, index: int) -> str:
    value = row.get(field)
    if (
        value is None
        or value == ""
        or isinstance(value, bool)
        or not isinstance(value, (str, int, float))
    ):
        raise PairwiseError(f"{source}[{index}] missing valid identity {field!r}")
    if isinstance(value, float) and not math.isfinite(value):
        raise PairwiseError(f"{source}[{index}].{field} must be finite")
    return str(value)


def score_native_pairwise(
    spec: NativePairwiseSpec,
    a: Sequence[Mapping[str, Any]],
    b: Sequence[Mapping[str, Any]],
    *,
    max_pairs: int = 100000,
    profiles: Mapping[str, str] | None = None,
) -> list[dict[str, object]]:
    """Evaluate all ordered matchups over A in input order against an immutable B snapshot."""
    if max_pairs < 1 or len(a) * max(0, len(a) - 1) > max_pairs:
        raise PairwiseError("Pairwise comparison limit exceeded")
    prev: dict[str, Mapping[str, Any]] = {}
    for i, row in enumerate(b):
        identity = _id(row, spec.b_key, "b", i)
        if identity in prev:
            raise PairwiseError(f"Duplicate identity {identity!r} in b")
        prev[identity] = row
    active: list[tuple[str, Mapping[str, Any], Mapping[str, Any]]] = []
    seen: set[str] = set()
    for i, row in enumerate(a):
        identity = _id(row, spec.a_key, "a", i)
        if identity in seen:
            raise PairwiseError(f"Duplicate identity {identity!r} in a")
        seen.add(identity)
        if identity not in prev:
            raise PairwiseError(f"a[{i}] participant {identity!r} missing from b")
        active.append((identity, row, prev[identity]))
    engine = StatLineFormulaEngine()
    try:
        pair_plan = engine.compile_variables(spec.pair)
        result_plan = engine.compile_variables(spec.result)
    except FormulaError as error:
        raise PairwiseError(f"Invalid formula: {error}") from error
    output: list[dict[str, object]] = []
    count = len(active)
    for self_id, self_new, self_old in active:
        total = 0.0
        for other_id, other_new, other_old in active:
            if self_id == other_id:
                continue
            scope: dict[str, object] = {
                "self": {"new": self_new, "old": self_old},
                "other": {"new": other_new, "old": other_old},
                "count": count,
            }
            try:
                partial = pair_plan(scope)
            except FormulaError as error:
                raise PairwiseError(
                    f"pair formula: self={self_id!r}, other={other_id!r}: {error}"
                ) from error
            delta = partial[spec.expression]
            if (
                isinstance(delta, bool)
                or not isinstance(delta, (float, int))
                or not math.isfinite(float(delta))
            ):
                raise PairwiseError(
                    f"pair.{spec.expression}: expected finite number for {self_id!r} vs {other_id!r}"
                )
            total += float(delta)  # match JS += traversal order, not math.fsum
            if not math.isfinite(total):
                raise PairwiseError("Pairwise sum became non-finite")
        scope = {
            "self": {"new": self_new, "old": self_old},
            "pairwise": {"total": total},
            "count": count,
        }
        try:
            calculated = result_plan(scope)
        except FormulaError as error:
            raise PairwiseError(f"result formula: participant={self_id!r}: {error}") from error
        row_out: dict[str, object] = dict(calculated)
        if profiles:
            for profile_name, field in profiles.items():
                if field not in calculated:
                    raise PairwiseError(
                        f"Profile {profile_name!r} references unknown output {field!r}"
                    )
                row_out[profile_name] = calculated[field]
        output.append(row_out)
    return output
