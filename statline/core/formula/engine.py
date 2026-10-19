"""Strict, authorable mathematical expressions without running adapter Python.

The standard-library AST is only a syntax tree. Every supported node is
checked and interpreted explicitly. Attribute paths traverse dictionaries only.
Existing numeric adapter formulas retain their historical semantics within the same engine.
"""

from __future__ import annotations

import ast
import math
from collections.abc import Callable, Mapping
from decimal import ROUND_HALF_UP, Decimal, localcontext
from functools import cached_property, lru_cache
from typing import ClassVar, Literal, Protocol, cast

from statline.core.formula._compat import _dataset_aggregate, _num
from statline.core.formula._compat import compile_compat as _compile_compat
from statline.core.types.adapters import ExpressionEvaluator

FormulaValue = int | float | str | bool | None
FormulaCallable = Callable[[Mapping[str, object]], FormulaValue]


class _CachedExpressionCompiler(Protocol):
    """A bounded per-instance compiler retaining the public diagnostics API."""

    def __call__(self, expression: str) -> FormulaCallable: ...

    def cache_info(self) -> object: ...


class _CachedVariableCompiler(Protocol):
    """Cached variable plan callable held by one formula engine instance."""

    def __call__(
        self, variables: tuple[tuple[str, str], ...]
    ) -> Callable[[Mapping[str, object]], dict[str, FormulaValue]]: ...

    def cache_info(self) -> object: ...


class FormulaError(ValueError):
    """A formula cannot be validated or safely evaluated."""


class StatLineFormulaEngine:
    """Compile user-written expressions with a strict AST allowlist.

    ``legacy`` is deliberately separate: existing adapters keep their historical
    coercion, zero-defaults, and numeric behavior. New models use ``strict``.
    """

    _BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)
    _UNARY = (ast.UAdd, ast.USub, ast.Not)
    _CMPOPS = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)
    _FUNCTIONS = frozenset(
        {
            "abs",
            "min",
            "max",
            "sum",
            "mean",
            "exp",
            "log",
            "log10",
            "log1p",
            "sqrt",
            "sin",
            "cos",
            "tan",
            "floor",
            "ceil",
            "pow",
            "round",
            "js_round",
            "js_fixed",
            "clamp",
        }
    )
    _DATASET_FUNCTIONS: ClassVar[dict[str, str]] = {
        "dataset_max": "max",
        "dataset_min": "min",
        "dataset_mean": "mean",
        "dataset_median": "median",
        "dataset_sum": "sum",
        "dataset_count": "count",
    }

    def __init__(
        self,
        *,
        mode: Literal["strict", "legacy"] = "strict",
        max_chars: int = 2048,
        max_nodes: int = 128,
        max_depth: int = 24,
        max_abs_value: float = 1e100,
        max_exponent: float = 64.0,
    ) -> None:
        if mode not in ("strict", "legacy"):
            raise ValueError("mode must be 'strict' or 'legacy'")
        if min(max_chars, max_nodes, max_depth) < 1 or max_abs_value <= 0 or max_exponent <= 0:
            raise ValueError("Formula limits must be positive")
        self.mode = mode
        self.max_chars = max_chars
        self.max_nodes = max_nodes
        self.max_depth = max_depth
        self.max_abs_value = max_abs_value
        self.max_exponent = max_exponent

    @staticmethod
    def compile_compat(expression: str) -> ExpressionEvaluator:
        """Return a cached, safe compiled numeric adapter formula (rc5 compatible)."""
        return _compile_compat(expression)

    # Kept as a migration alias for older public imports. All internal callers use
    # compile_compat; the retired evaluator module is no longer imported.
    compile_legacy = compile_compat

    @staticmethod
    @lru_cache(maxsize=512)
    def _parse(expression: str) -> ast.Expression:
        try:
            return ast.parse(expression, mode="eval")
        except (SyntaxError, ValueError, RecursionError) as exc:
            raise FormulaError(f"Invalid formula syntax: {exc}") from exc

    @cached_property
    def compile(self) -> _CachedExpressionCompiler:
        """Keep the bounded expression cache on the owning engine instance.

        A cached global method retains each previous engine through the cache
        key. Keeping the wrapper on the instance lets unused engines be freed.
        """
        return cast(_CachedExpressionCompiler, lru_cache(maxsize=2048)(self._compile_uncached))

    def _compile_uncached(self, expression: str) -> FormulaCallable:
        """Validate once, then return a callable accepting a data context.

        Strict mode throws FormulaError for any unknown variable/function or
        forbidden syntax; it never silently substitutes zero.
        """
        if not isinstance(cast(object, expression), str) or not expression.strip():
            raise FormulaError("Expression must be a non-empty string")
        if len(expression) > self.max_chars:
            raise FormulaError(f"Expression exceeds {self.max_chars} characters")
        if self.mode == "legacy":
            old_evaluator = self.compile_compat(expression)
            return lambda context: old_evaluator(context, _num(context.get("x", 0.0)))

        parsed = self._parse(expression)
        if sum(1 for _ in ast.walk(parsed)) > self.max_nodes:
            raise FormulaError(f"Expression exceeds {self.max_nodes} AST nodes")
        self._validate(parsed, depth=0)
        return lambda context: self._checked(self._evaluate(parsed.body, context))

    def evaluate(self, expression: str, context: Mapping[str, object]) -> FormulaValue:
        return self.compile(expression)(context)

    def compile_variables(
        self, variables: Mapping[str, str]
    ) -> Callable[[Mapping[str, object]], dict[str, FormulaValue]]:
        """Compile a named formula chain once for repeated row or pair evaluation.

        The returned callable evaluates in YAML declaration order. Results from
        earlier variables are made available to later expressions.
        """
        return self._compile_variable_plan(tuple(variables.items()))

    @cached_property
    def _compile_variable_plan(self) -> _CachedVariableCompiler:
        """Cache formula chains only for the lifetime of their engine."""
        return cast(
            _CachedVariableCompiler,
            lru_cache(maxsize=512)(self._compile_variable_plan_uncached),
        )

    def _compile_variable_plan_uncached(
        self, variables: tuple[tuple[str, str], ...]
    ) -> Callable[[Mapping[str, object]], dict[str, FormulaValue]]:
        plan: list[tuple[str, FormulaCallable]] = []
        for name, expression in variables:
            if not name.isidentifier() or name.startswith("_") or name in self._FUNCTIONS:
                raise FormulaError(f"Invalid variable name: {name!r}")
            try:
                plan.append((name, self.compile(expression)))
            except FormulaError as exc:
                raise FormulaError(f"variables.{name}: {exc}") from exc

        def evaluate_plan(context: Mapping[str, object]) -> dict[str, FormulaValue]:
            scope: dict[str, object] = dict(context)
            results: dict[str, FormulaValue] = {}
            for name, formula in plan:
                try:
                    result = formula(scope)
                except FormulaError as exc:
                    raise FormulaError(f"variables.{name}: {exc}") from exc
                results[name] = result
                scope[name] = result
            return results

        return evaluate_plan

    def evaluate_variables(
        self, variables: Mapping[str, str], context: Mapping[str, object]
    ) -> dict[str, FormulaValue]:
        """One-off variable evaluation; compile_variables is faster in loops."""
        return self.compile_variables(variables)(context)

    def _validate(self, node: ast.AST, depth: int) -> None:
        if depth > self.max_depth:
            raise FormulaError(f"Expression exceeds depth {self.max_depth}")
        if isinstance(node, ast.Expression):
            self._validate(node.body, depth + 1)
        elif isinstance(node, ast.Constant):
            if not (node.value is None or isinstance(node.value, (bool, int, float, str))):
                raise FormulaError("Unsupported constant type")
            if isinstance(node.value, str) and len(node.value) > 256:
                raise FormulaError("String constants cannot exceed 256 characters")
            self._checked(node.value)
        elif isinstance(node, ast.Name):
            if node.id.startswith("_"):
                raise FormulaError(f"Private name is forbidden: {node.id}")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                raise FormulaError("Private field paths are forbidden")
            # Traversal is restricted to Mapping instances in _evaluate.
            self._validate(node.value, depth + 1)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, self._BINOPS):
            self._validate(node.left, depth + 1)
            self._validate(node.right, depth + 1)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, self._UNARY):
            self._validate(node.operand, depth + 1)
        elif isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            for item in node.values:
                self._validate(item, depth + 1)
        elif isinstance(node, ast.Compare) and all(isinstance(op, self._CMPOPS) for op in node.ops):
            self._validate(node.left, depth + 1)
            for item in node.comparators:
                self._validate(item, depth + 1)
        elif isinstance(node, ast.IfExp):
            self._validate(node.test, depth + 1)
            self._validate(node.body, depth + 1)
            self._validate(node.orelse, depth + 1)
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in (
                self._FUNCTIONS | self._DATASET_FUNCTIONS.keys()
            ):
                raise FormulaError("Function is not permitted")
            if node.keywords:
                raise FormulaError("Keyword arguments are not supported")
            if len(node.args) > 24:
                raise FormulaError("Too many function arguments")
            if node.func.id in self._DATASET_FUNCTIONS and (
                len(node.args) != 1
                or not isinstance(node.args[0], ast.Constant)
                or not isinstance(node.args[0].value, str)
                or not node.args[0].value.strip()
            ):
                raise FormulaError(f"{node.func.id} requires one literal header name")
            for item in node.args:
                self._validate(item, depth + 1)
        else:
            raise FormulaError(f"Unsupported syntax: {type(node).__name__}")

    def _checked(self, result: object) -> FormulaValue:
        if result is None or isinstance(result, (bool, str)):
            if isinstance(result, str) and len(result) > 4096:
                raise FormulaError("String result exceeds size limit")
            return result
        if isinstance(result, (int, float)):
            try:
                number = float(result)
            except (OverflowError, ValueError) as exc:
                raise FormulaError("Numeric result exceeds limits") from exc
            if not math.isfinite(number) or abs(number) > self.max_abs_value:
                raise FormulaError("Non-finite or oversized numeric result")
            return result
        raise FormulaError(f"Non-scalar formula value: {type(result).__name__}")

    def _number(self, value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise FormulaError(f"Expected a number; got {type(value).__name__}")
        self._checked(value)  # Validate magnitude before conversion.
        return float(value)

    def _ordered_compare(self, left: object, right: object, operator: ast.cmpop) -> bool:
        """Compare scalar operands only, without relying on arbitrary object methods."""
        if isinstance(left, str) and isinstance(right, str):
            a, b = left, right
            if isinstance(operator, ast.Lt):
                return a < b
            if isinstance(operator, ast.LtE):
                return a <= b
            if isinstance(operator, ast.Gt):
                return a > b
            if isinstance(operator, ast.GtE):
                return a >= b
        else:
            # Reject booleans, dictionaries and mismatched text/numeric types.
            x, y = self._number(left), self._number(right)
            if isinstance(operator, ast.Lt):
                return x < y
            if isinstance(operator, ast.LtE):
                return x <= y
            if isinstance(operator, ast.Gt):
                return x > y
            if isinstance(operator, ast.GtE):
                return x >= y
        raise FormulaError("Unknown ordered comparison")

    def _evaluate(self, node: ast.AST, context: Mapping[str, object]) -> object:
        try:
            result = self._do_evaluate(node, context)
            # Intermediate dictionary scopes may be traversed, but never returned
            # as a final formula value (compile() enforces scalar output).
            return (
                cast(Mapping[str, object], result)
                if isinstance(result, Mapping)
                else self._checked(result)
            )
        except FormulaError:
            raise
        except (ValueError, OverflowError, ZeroDivisionError, TypeError, ArithmeticError) as exc:
            raise FormulaError(f"Math evaluation failed: {exc}") from exc

    def _do_evaluate(self, node: ast.AST, context: Mapping[str, object]) -> object:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in context:
                raise FormulaError(f"Unknown variable '{node.id}'")
            return context[node.id]
        if isinstance(node, ast.Attribute):
            parent = self._evaluate(node.value, context)
            if not isinstance(parent, Mapping) or node.attr not in parent:
                raise FormulaError(f"Missing field '{node.attr}'")
            return cast(Mapping[str, object], parent)[node.attr]
        if isinstance(node, ast.UnaryOp):
            value = self._evaluate(node.operand, context)
            if isinstance(node.op, ast.Not):
                return not value
            num = self._number(value)
            return num if isinstance(node.op, ast.UAdd) else -num
        if isinstance(node, ast.BinOp):
            a = self._number(self._evaluate(node.left, context))
            b = self._number(self._evaluate(node.right, context))
            op = node.op
            if isinstance(op, ast.Add):
                return a + b
            if isinstance(op, ast.Sub):
                return a - b
            if isinstance(op, ast.Mult):
                return a * b
            if isinstance(op, (ast.Div, ast.FloorDiv, ast.Mod)):
                if b == 0:
                    raise FormulaError("Division by zero")
                if isinstance(op, ast.Div):
                    return a / b
                if isinstance(op, ast.FloorDiv):
                    return a // b
                return a % b
            if isinstance(op, ast.Pow):
                return self._pow(a, b)
        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                and_result: object = True
                for part in node.values:
                    and_result = self._evaluate(part, context)
                    if not and_result:
                        return and_result
                return and_result
            or_result: object = False
            for part in node.values:
                or_result = self._evaluate(part, context)
                if or_result:
                    return or_result
            return or_result
        if isinstance(node, ast.Compare):
            prev = self._evaluate(node.left, context)
            for comparison_op, item in zip(node.ops, node.comparators):
                next_val = self._evaluate(item, context)
                if isinstance(comparison_op, ast.Eq):
                    result = prev == next_val
                elif isinstance(comparison_op, ast.NotEq):
                    result = prev != next_val
                else:
                    result = self._ordered_compare(prev, next_val, comparison_op)
                if not result:
                    return False
                prev = next_val
            return True
        if isinstance(node, ast.IfExp):
            branch = node.body if self._evaluate(node.test, context) else node.orelse
            return self._evaluate(branch, context)
        if isinstance(node, ast.Call):
            assert isinstance(node.func, ast.Name)  # validated at compile time
            name = node.func.id
            if name in self._DATASET_FUNCTIONS:
                assert isinstance(node.args[0], ast.Constant)
                header = node.args[0].value
                if not isinstance(header, str):
                    raise FormulaError("Dataset aggregate requires a literal header name")
                return _dataset_aggregate(context, self._DATASET_FUNCTIONS[name], header)
            arguments = [self._evaluate(a, context) for a in node.args]
            return self._call(name, arguments)
        raise FormulaError(f"Unsupported expression node: {type(node).__name__}")

    def _pow(self, base: float, exponent: float) -> float:
        if abs(exponent) > self.max_exponent:
            raise FormulaError("Exponent exceeds the configured limit")
        if base == 0 and exponent < 0:
            raise FormulaError("Cannot raise zero to a negative power")
        if base < 0 and not float(exponent).is_integer():
            raise FormulaError("Complex results are not permitted")
        result = base**exponent
        self._checked(result)  # Validate magnitude before numeric conversion.
        return float(result)

    def _call(self, name: str, args: list[object]) -> FormulaValue:
        nums = [self._number(a) for a in args]
        if name in ("min", "max", "sum", "mean"):
            if not nums:
                raise FormulaError(f"{name} requires at least one argument")
            if name == "min":
                return min(nums)
            if name == "max":
                return max(nums)
            if name == "sum":
                return math.fsum(nums)
            return math.fsum(nums) / len(nums)
        if name == "clamp":
            if len(nums) != 3 or nums[1] > nums[2]:
                raise FormulaError("clamp(x, lo, hi) requires lo <= hi")
            return min(max(nums[0], nums[1]), nums[2])
        if name == "pow":
            if len(nums) != 2:
                raise FormulaError("pow(base, exponent) takes 2 arguments")
            return self._pow(nums[0], nums[1])
        if name in ("round", "js_fixed"):
            if len(nums) not in (1, 2):
                raise FormulaError(f"{name} takes 1 or 2 arguments")
            ndigits = nums[1] if len(nums) == 2 else 0.0
            if not float(ndigits).is_integer() or not -10 <= ndigits <= 10:
                raise FormulaError("Rounding precision must be an integer between -10 and 10")
            if name == "round":
                return float(round(nums[0], int(ndigits)))
            if ndigits < 0:
                raise FormulaError("js_fixed decimals must be nonnegative")
            # JS toFixed rounds the stored binary floating point number; Decimal
            # from_float reproduces this tie handling without binary repr loss.
            with localcontext() as ctx:
                ctx.prec = 150
                quantum = Decimal(1).scaleb(-int(ndigits))
                return float(Decimal.from_float(nums[0]).quantize(quantum, rounding=ROUND_HALF_UP))
        one_arg: dict[str, Callable[[float], float]] = {
            "abs": abs,
            "exp": math.exp,
            "log10": math.log10,
            "log1p": math.log1p,
            "sqrt": math.sqrt,
            "sin": math.sin,
            "cos": math.cos,
            "tan": math.tan,
            "floor": math.floor,
            "ceil": math.ceil,
            "js_round": lambda x: math.floor(x + 0.5),
        }
        if name == "log":
            if len(nums) == 1:
                return math.log(nums[0])
            if len(nums) == 2:
                return math.log(nums[0], nums[1])
            raise FormulaError("log takes 1 or 2 arguments")
        if len(nums) != 1 or name not in one_arg:
            raise FormulaError(f"{name} requires exactly one number")
        return self._checked(one_arg[name](nums[0]))


__all__ = ["FormulaCallable", "FormulaError", "FormulaValue", "StatLineFormulaEngine"]
