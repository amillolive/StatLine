"""Compatibility evaluator: preserve rc6 AST and numeric semantics exactly.

New authorable formulas use StatLineFormulaEngine's strict mode instead.
"""

from __future__ import annotations

import ast
import math
from collections.abc import Mapping
from functools import lru_cache
from typing import SupportsFloat, SupportsIndex, TypeAlias, cast

from statline.core.types.adapters import ExpressionEvaluator

_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod)
_ALLOWED_UNARY = (ast.UAdd, ast.USub)
_ConvertibleToFloat: TypeAlias = SupportsFloat | SupportsIndex | str | bytes | bytearray


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
def compile_legacy(expr: str) -> ExpressionEvaluator:
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
    return _finite(compile_legacy(expr)(context, _num(context.get("x", 0.0))))


__all__ = [
    "compile_legacy",
    "_DATASET_AGGREGATES_KEY",
    "_DATASET_FUNCTIONS",
    "_DATASET_OPERATIONS",
    "DatasetRequirement",
    "_finite",
    "_num",
    "_safe_div",
    "_dataset_aggregate",
]
