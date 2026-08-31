"""Compile adapter specifications into reusable execution plans."""

from __future__ import annotations

import ast
import math
import statistics
from collections.abc import Callable, Iterable, Mapping, Sequence
from functools import lru_cache
from types import MappingProxyType
from typing import SupportsFloat, SupportsIndex, TypeAlias, cast

from statline.core.adapters.hooks import get as get_hooks
from statline.core.types.adapters import (
    AdapterSpec,
    CompiledAdapter,
    CompiledEfficiency,
    CompiledMetric,
    EffSpec,
    ExpressionEvaluator,
    MetricEvaluator,
    MetricSpec,
    SourceSpec,
    TransformSpec,
)

_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod)
_ALLOWED_UNARY = (ast.UAdd, ast.USub)
_ConvertibleToFloat: TypeAlias = SupportsFloat | SupportsIndex | str | bytes | bytearray
_TransformEvaluator: TypeAlias = Callable[[float, Mapping[str, object]], float]


def _finite(x: float, default: float = 0.0) -> float:
    """Return a finite float or the supplied default."""
    try:
        value = float(x)
    except (TypeError, ValueError, OverflowError):
        return default
    return value if math.isfinite(value) else default


def _num(value: object) -> float:
    try:
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            return _finite(float(value))
        if isinstance(value, str):
            stripped = value.strip().replace(",", ".")
            return _finite(float(stripped)) if stripped else 0.0
        return _finite(float(cast(_ConvertibleToFloat, value)))
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _safe_div(left: float, right: float) -> float:
    return left / right if abs(right) > 1e-12 else 0.0


def _safe_floordiv(left: float, right: float) -> float:
    return left // right if abs(right) > 1e-12 else 0.0


def _safe_mod(left: float, right: float) -> float:
    return left % right if abs(right) > 1e-12 else 0.0


def _safe_min(values: tuple[float, ...]) -> float:
    return min(values, default=0.0)


def _safe_max(values: tuple[float, ...]) -> float:
    return max(values, default=0.0)


_DATASET_AGGREGATES_KEY = "__statline_dataset_aggregates__"
_DATASET_FUNCTIONS = {
    "dataset_max": "max",
    "dataset_min": "min",
    "dataset_mean": "mean",
    "dataset_median": "median",
    "dataset_sum": "sum",
    "dataset_count": "count",
}
_DATASET_OPERATIONS = frozenset(_DATASET_FUNCTIONS.values())
DatasetRequirement: TypeAlias = tuple[str, str]


def _numeric_or_none(value: object) -> float | None:
    """Return one finite numeric value without coercing arbitrary text to zero."""
    try:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, str):
            stripped = value.strip().replace(",", ".")
            if not stripped:
                return None
            number = float(stripped)
        else:
            number = float(cast(_ConvertibleToFloat, value))
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def build_dataset_context(
    rows: Iterable[Mapping[str, object]],
    requirements: Sequence[DatasetRequirement] | None = None,
) -> Mapping[str, object]:
    """Precompute case-insensitive per-header aggregates for one submitted dataset.

    ``requirements=None`` preserves the public/full aggregate behavior. Compiled
    adapters pass their exact aggregate requirements so batch mapping only computes
    the headers and operations referenced by adapter expressions.
    """
    if requirements is not None:
        normalized = {
            (str(operation).strip().casefold(), str(header).strip().casefold())
            for operation, header in requirements
            if str(operation).strip().casefold() in _DATASET_OPERATIONS and str(header).strip()
        }
        if not normalized:
            empty_aggregates: dict[str, Mapping[str, float]] = {}
            return MappingProxyType({_DATASET_AGGREGATES_KEY: MappingProxyType(empty_aggregates)})

        operations_by_header: dict[str, set[str]] = {}
        for operation, header in normalized:
            operations_by_header.setdefault(header, set()).add(operation)

        required_numeric: dict[str, list[float]] = {
            header: []
            for header, operations in operations_by_header.items()
            if operations - {"count"}
        }
        required_counts: dict[str, int] = {
            header: 0
            for header, operations in operations_by_header.items()
            if "count" in operations
        }

        for row in rows:
            for raw_key, raw_value in row.items():
                key = str(raw_key).strip().casefold()
                operations = operations_by_header.get(key)
                if not operations:
                    continue
                if (
                    "count" in operations
                    and raw_value is not None
                    and (not isinstance(raw_value, str) or raw_value.strip())
                ):
                    required_counts[key] += 1
                if operations - {"count"}:
                    number = _numeric_or_none(raw_value)
                    if number is not None:
                        required_numeric[key].append(number)

        required_aggregates: dict[str, Mapping[str, float]] = {}
        for header, operations in operations_by_header.items():
            values = required_numeric.get(header, [])
            aggregate: dict[str, float] = {}
            total: float | None = None
            for operation in operations:
                if operation == "count":
                    aggregate[operation] = float(required_counts.get(header, 0))
                elif operation == "max":
                    aggregate[operation] = max(values, default=0.0)
                elif operation == "min":
                    aggregate[operation] = min(values, default=0.0)
                elif operation in {"mean", "sum"}:
                    if total is None:
                        total = math.fsum(values) if values else 0.0
                    aggregate[operation] = (
                        total / len(values) if operation == "mean" and values else total
                    )
                elif operation == "median":
                    aggregate[operation] = statistics.median(values) if values else 0.0
            required_aggregates[header] = MappingProxyType(aggregate)

        return MappingProxyType({_DATASET_AGGREGATES_KEY: MappingProxyType(required_aggregates)})

    numeric: dict[str, list[float]] = {}
    counts: dict[str, int] = {}

    for row in rows:
        for raw_key, raw_value in row.items():
            key = str(raw_key).strip().casefold()
            if not key:
                continue
            if raw_value is not None and (not isinstance(raw_value, str) or raw_value.strip()):
                counts[key] = counts.get(key, 0) + 1
            number = _numeric_or_none(raw_value)
            if number is not None:
                numeric.setdefault(key, []).append(number)

    aggregates: dict[str, Mapping[str, float]] = {}
    for key in set(counts) | set(numeric):
        values = numeric.get(key, [])
        total = math.fsum(values) if values else 0.0
        aggregates[key] = MappingProxyType(
            {
                "max": max(values, default=0.0),
                "min": min(values, default=0.0),
                "mean": total / len(values) if values else 0.0,
                "median": statistics.median(values) if values else 0.0,
                "sum": total,
                "count": float(counts.get(key, 0)),
            }
        )

    return MappingProxyType({_DATASET_AGGREGATES_KEY: MappingProxyType(aggregates)})


def _dataset_aggregate(
    context: Mapping[str, object],
    operation: str,
    header: str,
) -> float:
    table_obj = context.get(_DATASET_AGGREGATES_KEY)

    if not isinstance(table_obj, Mapping):
        return 0.0

    table = cast(Mapping[str, object], table_obj)

    values_obj = table.get(str(header).strip().casefold())

    if not isinstance(values_obj, Mapping):
        return 0.0

    values = cast(Mapping[str, object], values_obj)

    return _finite(_num(values.get(operation, 0.0)))


def _lower_expr_node(node: ast.AST) -> ast.expr:
    """Lower the restricted expression DSL into a safe Python expression."""
    if isinstance(node, ast.Expression):
        return _lower_expr_node(node.body)
    if isinstance(node, ast.Constant):
        return ast.Constant(_num(node.value))
    if isinstance(node, ast.Name):
        if node.id == "x":
            return ast.Name("__x", ast.Load())
        return ast.Call(
            ast.Attribute(ast.Name("__ctx", ast.Load()), "get", ast.Load()),
            [ast.Constant(node.id), ast.Constant(0.0)],
            [],
        )
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, _ALLOWED_UNARY):
        return ast.UnaryOp(node.op, _lower_expr_node(node.operand))
    if isinstance(node, ast.BinOp) and isinstance(node.op, _ALLOWED_BINOPS):
        left = _lower_expr_node(node.left)
        right = _lower_expr_node(node.right)
        if isinstance(node.op, ast.Div):
            helper = "_safe_div"
        elif isinstance(node.op, ast.FloorDiv):
            helper = "_safe_floordiv"
        elif isinstance(node.op, ast.Mod):
            helper = "_safe_mod"
        else:
            return ast.BinOp(left, node.op, right)
        return ast.Call(ast.Name(helper, ast.Load()), [left, right], [])
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _DATASET_FUNCTIONS
        and not node.keywords
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ):
        return ast.Call(
            ast.Name("_dataset_aggregate", ast.Load()),
            [
                ast.Name("__ctx", ast.Load()),
                ast.Constant(_DATASET_FUNCTIONS[node.func.id]),
                ast.Constant(node.args[0].value),
            ],
            [],
        )
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in ("min", "max")
        and not node.keywords
    ):
        values = ast.Tuple([_lower_expr_node(arg) for arg in node.args], ast.Load())
        return ast.Call(ast.Name(f"_safe_{node.func.id}", ast.Load()), [values], [])
    return ast.Constant(0.0)


@lru_cache(maxsize=512)
def _compile_expr(expr: str) -> ExpressionEvaluator:
    """Compile and cache one restricted expression."""
    try:
        parsed = ast.parse(expr, mode="eval")
    except (SyntaxError, ValueError):
        return lambda _ctx, _x: 0.0

    function_ast = ast.Expression(
        ast.Lambda(
            ast.arguments(
                posonlyargs=[],
                args=[ast.arg("__ctx"), ast.arg("__x")],
                kwonlyargs=[],
                kw_defaults=[],
                defaults=[],
            ),
            _lower_expr_node(parsed),
        )
    )
    ast.fix_missing_locations(function_ast)
    namespace: dict[str, object] = {
        "__builtins__": {},
        "_safe_div": _safe_div,
        "_safe_floordiv": _safe_floordiv,
        "_safe_mod": _safe_mod,
        "_safe_min": _safe_min,
        "_safe_max": _safe_max,
        "_dataset_aggregate": _dataset_aggregate,
    }
    code = compile(function_ast, "<statline-adapter-expression>", "eval")
    return cast(ExpressionEvaluator, eval(code, namespace, {}))


def _eval_expr(  # pyright: ignore[reportUnusedFunction]
    expr: str, context: Mapping[str, object]
) -> float:
    return _finite(_compile_expr(expr)(context, _num(context.get("x", 0.0))))


def _sanitize_row(raw: Mapping[str, object]) -> dict[str, object]:
    """Create the trusted numeric context consumed by compiled evaluators."""
    return {str(key): _num(value) for key, value in raw.items()}


def _compile_source(source: SourceSpec) -> MetricEvaluator:
    if source.kind == "field":
        field = (source.field or "").strip()
        if not field:
            raise ValueError("Field source requires a non-empty field.")
        return lambda context: cast(float, context.get(field, 0.0))
    if source.kind == "const":
        if source.const is None:
            raise ValueError("Constant source requires const.")
        value = _num(source.const)
        return lambda _context: value
    if source.kind == "expr":
        expression = (source.expr or "").strip()
        if not expression:
            raise ValueError("Expression source requires a non-empty expr.")
        evaluator = _compile_expr(expression)
        return lambda context: _finite(evaluator(context, cast(float, context.get("x", 0.0))))
    raise ValueError(f"Unsupported source kind: {source.kind}")


def _compile_transform(spec: TransformSpec | None) -> _TransformEvaluator:
    if spec is None:
        return lambda value, _context: value

    params: dict[str, object] = dict(spec.params)
    if spec.kind == "expr":
        expression = str(params.get("expr", "")).strip()
        if not expression:
            raise ValueError("Expression transform requires a non-empty expr.")
        evaluator = _compile_expr(expression)
        return lambda value, context: _finite(evaluator(context, value))
    if spec.kind == "affine":
        scale = _num(params.get("scale", params.get("a", 1.0)))
        offset = _num(params.get("offset", params.get("b", 0.0)))
        return lambda value, _context: value * scale + offset
    if spec.kind == "scale":
        scale = _num(params["scale"])
        return lambda value, _context: value * scale
    if spec.kind == "clip":
        lower = _num(params["lo"])
        upper = _num(params["hi"])
        if lower > upper:
            raise ValueError("Clip transform requires lo <= hi.")
        return lambda value, _context: min(max(value, lower), upper)
    if spec.kind == "round":
        digits = int(_num(params["ndigits"]))
        return lambda value, _context: float(round(value, digits))
    if spec.kind != "custom":
        raise ValueError(f"Unknown transform kind '{spec.kind}'")

    name = str(params.get("name", "")).strip().casefold()
    if name == "linear":
        scale = _num(params.get("scale", 1.0))
        offset = _num(params.get("offset", 0.0))
        return lambda value, _context: value * scale + offset
    if name == "capped_linear":
        cap = _num(params["cap"])
        return lambda value, _context: min(value, cap)
    if name == "minmax":
        lower = _num(params["lo"])
        upper = _num(params["hi"])
        if lower > upper:
            raise ValueError("minmax transform requires lo <= hi.")
        return lambda value, _context: min(max(value, lower), upper)
    if name == "pct01":
        divisor = _num(params.get("by", 100.0)) or 100.0
        return lambda value, _context: value / divisor
    if name == "softcap":
        cap = _num(params["cap"])
        slope = _num(params.get("slope", 1.0))
        return lambda value, _context: value if value <= cap else cap + (value - cap) * slope
    if name == "log1p":
        scale = _num(params.get("scale", 1.0))
        return lambda value, _context: math.log1p(max(value, 0.0)) * scale
    raise ValueError(f"Unknown custom transform '{name}'")


def _compile_metric(metric: MetricSpec) -> CompiledMetric:
    if metric.source is None:
        raise ValueError(f"Metric '{metric.key}' is missing its source.")
    source = _compile_source(metric.source)
    transform = _compile_transform(metric.transform)

    def evaluate(context: Mapping[str, object]) -> float:
        return _finite(transform(source(context), context))

    return CompiledMetric(metric.key, evaluate)


def _compile_efficiency(efficiency: EffSpec) -> CompiledEfficiency:
    make = _compile_expr(efficiency.make)
    attempt = _compile_expr(efficiency.attempt)
    transform = _compile_transform(efficiency.transform)
    minimum = float(efficiency.min_den)
    if not math.isfinite(minimum) or minimum <= 0:
        raise ValueError(f"Efficiency '{efficiency.key}' requires min_den > 0.")

    def evaluate(context: Mapping[str, object]) -> float:
        x = cast(float, context.get("x", 0.0))
        numerator = make(context, x)
        attempted = attempt(context, x)
        denominator = attempted if attempted >= max(1e-12, minimum) else max(1.0, minimum)
        return _finite(transform(_safe_div(numerator, denominator), context))

    return CompiledEfficiency(efficiency.key, evaluate)


def _freeze_nested(table: Mapping[str, Mapping[str, float]]) -> Mapping[str, Mapping[str, float]]:
    return MappingProxyType(
        {name: MappingProxyType(dict(values)) for name, values in table.items()}
    )


def _dataset_requirements_from_expr(expr: str) -> set[DatasetRequirement]:
    """Return dataset aggregate operations referenced by one expression."""
    try:
        parsed = ast.parse(expr, mode="eval")
    except (SyntaxError, ValueError):
        return set()

    requirements: set[DatasetRequirement] = set()
    for node in ast.walk(parsed):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _DATASET_FUNCTIONS
            and not node.keywords
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            header = node.args[0].value.strip().casefold()
            if header:
                requirements.add((_DATASET_FUNCTIONS[node.func.id], header))
    return requirements


def _dataset_requirements(
    metrics: Sequence[MetricSpec],
    efficiency: Sequence[EffSpec],
) -> tuple[DatasetRequirement, ...]:
    requirements: set[DatasetRequirement] = set()
    for metric in metrics:
        source = metric.source
        if source is not None and source.kind == "expr" and source.expr:
            requirements.update(_dataset_requirements_from_expr(source.expr))
        transform = metric.transform
        if transform is not None and transform.kind == "expr":
            requirements.update(
                _dataset_requirements_from_expr(str(transform.params.get("expr", "")))
            )
    for item in efficiency:
        requirements.update(_dataset_requirements_from_expr(item.make))
        requirements.update(_dataset_requirements_from_expr(item.attempt))
        transform = item.transform
        if transform is not None and transform.kind == "expr":
            requirements.update(
                _dataset_requirements_from_expr(str(transform.params.get("expr", "")))
            )
    return tuple(sorted(requirements))


def map_raw(
    adapter: CompiledAdapter,
    raw: Mapping[str, object],
    *,
    dataset_context: Mapping[str, object] | None = None,
) -> dict[str, float]:
    """Map one raw row through an adapter's precompiled execution plan."""
    hooks = get_hooks(adapter.metadata.id)
    raw_row = dict(raw)
    pre = getattr(hooks, "pre_map", None)
    row = (
        cast(Callable[[dict[str, object]], Mapping[str, object]], pre)(raw_row)
        if callable(pre)
        else raw_row
    )
    context = _sanitize_row(row)
    if dataset_context:
        context.update(dataset_context)
    output: dict[str, float] = {}

    for metric in adapter.metric_plan:
        value = metric.evaluate(context)
        output[metric.key] = value
        context[metric.key] = value
    for efficiency in adapter.efficiency_plan:
        value = efficiency.evaluate(context)
        output[efficiency.key] = value
        context[efficiency.key] = value

    post = getattr(hooks, "post_map", None)
    if callable(post):
        return cast(Callable[[dict[str, float]], dict[str, float]], post)(output)
    return output


def compile_adapter(spec: AdapterSpec) -> CompiledAdapter:
    """Validate execution ownership and compile an immutable adapter plan."""
    if getattr(spec, "mapping", None):
        raise ValueError("Legacy mapping is unsupported; use typed source/transform.")

    keys = [metric.key for metric in spec.metrics] + [item.key for item in spec.efficiency]
    duplicate = next((key for key in keys if keys.count(key) > 1), None)
    if duplicate is not None:
        raise ValueError(f"Duplicate adapter output key '{duplicate}'.")

    metrics = tuple(spec.metrics)
    efficiency = tuple(spec.efficiency)
    return CompiledAdapter(
        metadata=spec.metadata,
        dimensions=MappingProxyType(dict(spec.dimensions)),
        sniff=spec.sniff,
        filters=MappingProxyType(dict(spec.filters)),
        score_profiles=MappingProxyType(dict(spec.score_profiles)),
        metrics=metrics,
        buckets=MappingProxyType(dict(spec.buckets)),
        weights=_freeze_nested(spec.weights),
        penalties=_freeze_nested(spec.penalties),
        efficiency=efficiency,
        metric_plan=tuple(_compile_metric(metric) for metric in metrics),
        efficiency_plan=tuple(_compile_efficiency(item) for item in efficiency),
        dataset_requirements=_dataset_requirements(metrics, efficiency),
    )


compile_expr = _compile_expr
__all__ = ["build_dataset_context", "compile_adapter", "compile_expr", "map_raw"]
