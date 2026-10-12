"""Native pairwise reducer with no user Python and no changes to historical PRI.

Inputs may be any Iterable of Mapping rows; filesystem and StatPack loading are
handled in sources.py. Every group is evaluated against one immutable pre-event
rating snapshot. Output formulas can access self.old/self.new, count, each
configured variable, and all reduction names such as delta.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, cast

from statline.core.formula import FormulaError, StatLineFormulaEngine
from statline.core.relate.errors import PairwiseError as PairwiseError
from statline.core.types.adapters import (
    AdapterSpec,
    CompiledAdapter,
    PairwiseSpec,
    ScoreProfileSpec,
)


def _number(value: object, *, context: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise PairwiseError(f"{context}: expected a finite number, got {value!r}")
    return float(value)


def _identity(row: Mapping[str, object], field: str, label: str) -> object:
    if field not in row or row[field] is None or row[field] == "":
        raise PairwiseError(f"{label}: missing identity field {field!r}")
    value = row[field]
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise PairwiseError(f"{label}: identity must be a string or number")
    return str(value)


def _coerce_input(rows: Iterable[Mapping[str, Any]], name: str) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for i, row in enumerate(rows):
        # Keep defensive runtime validation for callers bypassing type checking.
        if not isinstance(cast(object, row), Mapping):
            raise PairwiseError(f"{name}[{i}] must be a mapping")
        result.append(dict(row))
    return result


def _aggregate(operation: str, values: list[float], normalized: bool) -> float:
    if operation == "sum":
        result = math.fsum(values)
    elif operation == "mean":
        result = math.fsum(values) / len(values) if values else 0.0
    elif operation == "min":
        result = min(values, default=0.0)
    elif operation == "max":
        result = max(values, default=0.0)
    elif operation == "count":
        result = float(len(values))
    else:
        raise PairwiseError(f"Unsupported reduce operation: {operation}")
    # normalize:true means divide a sum by the number of opponents.
    if normalized and operation == "sum" and values:
        result /= len(values)
    return result


def score_pairwise(
    adapter: AdapterSpec | CompiledAdapter,
    *,
    old: Iterable[Mapping[str, Any]],
    new: Iterable[Mapping[str, Any]],
    model: str | None = None,
    selected_ids: Sequence[object] | None = None,
    include_pairs: bool = False,
    max_pairs: int = 100_000,
) -> list[dict[str, Any]]:
    """Execute a YAML pairwise model on two dataset snapshots.

    `old` is previous state, `new` is one or more complete event groups. The
    per-pair and per-entity mathematics come from YAML, not hardcoded Elo.
    `selected_ids` filters returned entities *after* all opponent deltas are
    calculated, to preserve complete-group mathematics.
    """
    models = adapter.pairwise
    if not models:
        raise PairwiseError("Adapter does not define pairwise models")
    name = model or next(iter(models))
    if name not in models:
        raise PairwiseError(f"Unknown pairwise model {name!r}")
    spec: PairwiseSpec = models[name]
    if max_pairs <= 0:
        raise PairwiseError("max_pairs must be positive")

    engine = StatLineFormulaEngine()
    try:
        plan_vars = engine.compile_variables(spec.variables)
        plan_pair = engine.compile_variables(spec.calculate)
        plan_out = engine.compile_variables(spec.output)
    except FormulaError as exc:
        raise PairwiseError(f"pairwise.{name}: {exc}") from exc

    old_rows = _coerce_input(old, "old")
    new_rows = _coerce_input(new, "new")
    old_by_id: dict[object, dict[str, object]] = {}
    for row in old_rows:
        entity_id = _identity(row, spec.on, "old")
        if entity_id in old_by_id:
            raise PairwiseError(f"Duplicate old identity {entity_id!r}")
        old_by_id[entity_id] = row

    groups: dict[object, list[tuple[object, dict[str, object], dict[str, object]]]] = defaultdict(
        list
    )
    for row in new_rows:
        entity_id = _identity(row, spec.on, "new")
        if entity_id not in old_by_id:
            raise PairwiseError(f"No previous-state row for {entity_id!r}")
        group_id: object = _identity(row, spec.group_by, "new") if spec.group_by else "__all__"
        groups[group_id].append((entity_id, old_by_id[entity_id], row))

    total_pairs = sum(len(rows) * (len(rows) - 1) for rows in groups.values())
    if total_pairs > max_pairs:
        raise PairwiseError(
            f"Pairwise request requires {total_pairs:,} comparisons; limit is {max_pairs:,}"
        )

    linked_profiles: dict[str, ScoreProfileSpec] = {
        key: value
        for key, value in adapter.score_profiles.items()
        if value.kind == "pairwise" and value.model == name
    }
    chosen = {str(value) for value in selected_ids} if selected_ids is not None else None
    output: list[dict[str, Any]] = []

    for group_id, members in groups.items():
        count = len(members)
        ids = [entity_id for entity_id, _, _ in members]
        if len(ids) != len(set(ids)):
            raise PairwiseError(f"Duplicate entity within event group {group_id!r}")
        for entity_id, prior, current in members:
            left = {"old": prior, "new": current}
            calculations: dict[str, list[float]] = {key: [] for key in spec.calculate}
            traces: list[dict[str, object]] = []
            for opponent_id, other_prior, other_current in members:
                if entity_id == opponent_id:
                    continue
                scope: dict[str, object] = {
                    "self": left,
                    "other": {"old": other_prior, "new": other_current},
                    "count": count,
                }
                try:
                    scope.update(plan_vars(scope))
                    pair_results = plan_pair(scope)
                except FormulaError as exc:
                    raise PairwiseError(
                        f"pairwise.{name}: group={group_id!r}, self={entity_id!r}, "
                        f"other={opponent_id!r}: {exc}"
                    ) from exc
                for key, value in pair_results.items():
                    calculations[key].append(_number(value, context=f"calculate.{key}"))
                if include_pairs:
                    traces.append({"opponent": opponent_id, **pair_results})
            reductions: dict[str, float] = {}
            for key, raw_rule in spec.reduce.items():
                rule = raw_rule
                reductions[key] = _aggregate(
                    str(rule["operation"]),
                    calculations[str(rule["field"])],
                    bool(rule["normalize"]),
                )
            scope = {"self": left, "count": count, **reductions}
            try:
                values = plan_out(scope)
            except FormulaError as exc:
                raise PairwiseError(f"pairwise.{name}: output for {entity_id!r}: {exc}") from exc
            scores = {
                profile_name: values[profile.value]
                for profile_name, profile in linked_profiles.items()
                if profile.value is not None
            }
            if chosen is not None and entity_id not in chosen:
                continue
            item: dict[str, Any] = {
                "id": entity_id,
                "model": name,
                "output": values,
                "scores": scores,
            }
            if "PRI" in scores:
                item["pri"] = scores["PRI"]
            if spec.group_by:
                item["group"] = group_id
            if include_pairs:
                item["pairs"] = traces
            output.append(item)
    return output
