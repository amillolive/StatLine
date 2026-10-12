"""Parse native pairwise v1 YAML without exposing Python or filesystem APIs."""

from __future__ import annotations

from collections.abc import Mapping

from statline.core.formula import FormulaError, StatLineFormulaEngine
from statline.core.types.adapters import NativePairwiseSpec


def _mapping(value: object, location: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{location} must be a YAML mapping")
    return {str(k): v for k, v in value.items()}


def _only(data: Mapping[str, object], allowed: set[str], location: str) -> None:
    extras = set(data) - allowed
    if extras:
        raise ValueError(f"{location}: unknown keys {sorted(extras)}")


def _formulas(value: object, location: str) -> dict[str, str]:
    raw = _mapping(value, location)
    if not raw:
        raise ValueError(f"{location} must not be empty")
    result = {key: str(formula).strip() for key, formula in raw.items()}
    if any(
        not key.isidentifier() or key.startswith("_") or not formula
        for key, formula in result.items()
    ):
        raise ValueError(f"{location}: invalid variable name or empty formula")
    return result


def parse_native_pairwise(pairwise: object, expressions: object) -> NativePairwiseSpec:
    data = _mapping(pairwise, "pairwise")
    _only(
        data,
        {
            "version",
            "inputs",
            "join",
            "participants",
            "opponents",
            "exclude_self",
            "preserve_order",
            "expression",
            "aggregate",
            "normalize",
            "outputs",
        },
        "pairwise",
    )
    if type(data.get("version")) is not int or data["version"] != 1:
        raise ValueError("pairwise.version must be 1")
    inputs = _mapping(data.get("inputs"), "pairwise.inputs")
    if set(inputs) != {"a", "b"}:
        raise ValueError("pairwise.inputs must contain exactly 'a' and 'b'")
    parsed_inputs: dict[str, dict[str, str]] = {}
    for side in ("a", "b"):
        source = _mapping(inputs[side], f"pairwise.inputs.{side}")
        _only(source, {"kind", "path", "dataset"}, f"pairwise.inputs.{side}")
        if source.get("kind") not in {"statpack", "csv", "json"}:
            raise ValueError(f"pairwise.inputs.{side}.kind must be statpack, csv, or json")
        path = source.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ValueError(f"pairwise.inputs.{side}.path is required")
        if source["kind"] == "statpack" and not isinstance(source.get("dataset"), str):
            raise ValueError(f"pairwise.inputs.{side}.dataset is required for StatPacks")
        parsed_inputs[side] = {str(k): str(v) for k, v in source.items()}
    join = _mapping(data.get("join"), "pairwise.join")
    _only(join, {"a_key", "b_key", "cardinality", "missing_b"}, "pairwise.join")
    if join.get("cardinality") != "one_to_one" or join.get("missing_b") != "error":
        raise ValueError("pairwise.join requires cardinality: one_to_one and missing_b: error")
    a_key, b_key = join.get("a_key"), join.get("b_key")
    if not isinstance(a_key, str) or not a_key or not isinstance(b_key, str) or not b_key:
        raise ValueError("pairwise.join requires a_key and b_key")
    if data.get("participants") != "a" or data.get("opponents") != "a":
        raise ValueError("pairwise only supports participants: a and opponents: a")
    for key, expected in (("exclude_self", True), ("preserve_order", True), ("normalize", False)):
        if data.get(key) is not expected:
            raise ValueError(f"pairwise.{key} must be {str(expected).lower()}")
    if data.get("aggregate") != "sum" or data.get("outputs") != "result":
        raise ValueError("pairwise requires aggregate: sum and outputs: result")
    ref = data.get("expression")
    if not isinstance(ref, str) or not ref.startswith("pair.") or not ref[5:].isidentifier():
        raise ValueError("pairwise.expression must reference pair.<name>")
    expr = _mapping(expressions, "expressions")
    _only(expr, {"pair", "result"}, "expressions")
    pair = _formulas(expr.get("pair"), "expressions.pair")
    result = _formulas(expr.get("result"), "expressions.result")
    reserved = {"self", "other", "count", "pairwise"}
    if reserved.intersection(pair) or reserved.intersection(result):
        raise ValueError("Pairwise formulas cannot redefine reserved context names")
    if ref[5:] not in pair:
        raise ValueError(f"pairwise.expression references unknown formula {ref!r}")
    engine = StatLineFormulaEngine()
    try:
        engine.compile_variables(pair)
        engine.compile_variables(result)
    except FormulaError as error:
        raise ValueError(f"Invalid native pairwise formula: {error}") from error
    return NativePairwiseSpec(parsed_inputs, a_key, b_key, pair, result, ref[5:])
