"""StatLineFormulaEngine strict evaluation and rc6 compatibility contract."""

from __future__ import annotations

import math

import pytest
from statline.core.adapters.compile import build_dataset_context, compile_expr
from statline.core.formula import FormulaError, StatLineFormulaEngine


def test_strict_math_functions_variables_and_conditionals() -> None:
    engine = StatLineFormulaEngine()
    variables = engine.evaluate_variables(
        {
            "k": "16 + 32 * exp(-0.15 * abs(self.old.roundsPlayed))",
            "actual": "1 if count < 2 else (count - self.new.finishPosition) / (count - 1)",
            "expected": "1 / (1 + pow(10, (other.old.eloScore - self.old.eloScore) / 400))",
            "teammate": "0.5 if self.new.teamId and other.new.teamId and self.new.teamId == other.new.teamId else 1.0",
        },
        {
            "count": 6,
            "self": {
                "old": {"roundsPlayed": 50, "eloScore": 1600},
                "new": {"finishPosition": 2, "teamId": "Red"},
            },
            "other": {"old": {"eloScore": 1400}, "new": {"teamId": "Red"}},
        },
    )
    k, actual, expected = variables["k"], variables["actual"], variables["expected"]
    assert isinstance(k, (int, float)) and not isinstance(k, bool)
    assert isinstance(actual, (int, float)) and not isinstance(actual, bool)
    assert isinstance(expected, (int, float)) and not isinstance(expected, bool)
    assert math.isclose(k, 16 + 32 * math.exp(-7.5), rel_tol=1e-6, abs_tol=1e-12)
    assert math.isclose(actual, 0.8, rel_tol=1e-6, abs_tol=1e-12)
    assert math.isclose(expected, 1 / (1 + 10 ** (-0.5)), rel_tol=1e-6, abs_tol=1e-12)
    assert variables["teammate"] == 0.5
    delta = engine.evaluate("k * (actual - expected) * teammate", variables)
    assert isinstance(delta, (int, float)) and not isinstance(delta, bool)
    assert math.isclose(delta, k * (actual - expected) * 0.5, rel_tol=1e-6, abs_tol=1e-12)
    assert engine.evaluate("clamp(-5, 0, 100)", {}) == 0
    assert engine.evaluate("mean(10, 20, 30)", {}) == 20
    assert engine.evaluate("sqrt(16) + floor(1.9)", {}) == 5


def test_strict_rounding_has_javascript_helpers() -> None:
    engine = StatLineFormulaEngine()
    assert engine.evaluate("js_round(1600.5)", {}) == 1601
    assert engine.evaluate("js_round(-1.5)", {}) == -1
    assert engine.evaluate("round(2.5)", {}) == 2  # Python semantics stay explicit
    assert engine.evaluate("js_fixed(2.675, 2)", {}) == 2.67
    assert engine.evaluate("js_fixed(23.234, 2)", {}) == 23.23


def test_strict_dataset_aggregates() -> None:
    ctx = build_dataset_context([{"pts": 2}, {"pts": 4}])
    engine = StatLineFormulaEngine()
    assert engine.evaluate('dataset_mean("PTS") * 2', ctx) == 6
    assert engine.evaluate('dataset_count("PTS")', ctx) == 2
    with pytest.raises(FormulaError, match="literal header"):
        engine.compile("dataset_mean(column)")


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('whoami')",
        "self.__class__",
        "self.old.eloScore.__class__",
        "items[0]",
        "[x for x in items]",
        "lambda: 1",
        "(1, 2)",
        "1 << 9",
        "(1 + 2; 3)",
        "sum(*items)",
        "f(1)",
    ],
)
def test_strict_rejects_unsafe_or_unsupported_syntax(expression: str) -> None:
    with pytest.raises(FormulaError):
        StatLineFormulaEngine().compile(expression)


@pytest.mark.parametrize(
    "expression, context, snippet",
    [
        ("missing + 5", {}, "Unknown variable"),
        ("self.old.nope", {"self": {"old": {}}}, "Missing field"),
        ("1 / 0", {}, "Division by zero"),
        ("2 ** 1000", {}, "Exponent"),
        ("sqrt(-1)", {}, "Math evaluation failed"),
        ("exp(9999)", {}, "Math evaluation failed"),
        ("1e999", {}, "Non-finite"),
        ("1 + 'hello'", {}, "Expected a number"),
    ],
)
def test_strict_invalid_operations_raise(
    expression: str, context: dict[str, object], snippet: str
) -> None:
    with pytest.raises(FormulaError, match=snippet):
        StatLineFormulaEngine().evaluate(expression, context)


def test_strict_resource_limits() -> None:
    with pytest.raises(FormulaError, match="characters"):
        StatLineFormulaEngine(max_chars=5).compile("1 + 12")
    with pytest.raises(FormulaError, match="AST nodes"):
        StatLineFormulaEngine(max_nodes=3).compile("1 + 2 + 3")
    with pytest.raises(FormulaError, match="depth"):
        StatLineFormulaEngine(max_depth=1).compile("2 + 3")


def test_legacy_zero_fallback_and_division_behavior_unchanged() -> None:
    engine = StatLineFormulaEngine(mode="legacy")
    assert engine.evaluate("1/0", {}) == 0
    assert engine.evaluate("x + unknown", {"x": 4}) == 4
    assert compile_expr("pow(2, 3)")({}, 0.0) == 0.0  # preexisting unsupported function
    assert compile_expr("1 / 0")({}, 0.0) == 0.0
    assert compile_expr("3 + 2 * x")({"x": 4.0}, 4.0) == 11


def test_six_driver_elo_exact_reference() -> None:
    """Replicate original JavaScript by authoring expressions, not Python math."""
    engine = StatLineFormulaEngine()
    drivers = [
        ("DriverA", 1600, 50, 1, "Red"),
        ("DriverB", 1600, 50, 2, "Red"),
        ("DriverC", 1500, 20, 3, "Blue"),
        ("DriverD", 1500, 20, 4, "Blue"),
        ("DriverE", 1400, 5, 5, "Green"),
        ("DriverF", 1400, 5, 6, "Green"),
    ]
    expressions = {
        "k": "16 + 32 * exp(-0.15 * abs(self.old.roundsPlayed))",
        "actual": "1 if count < 2 else (count - self.new.finishPosition) / (count - 1)",
        "expected": "1 / (1 + pow(10, (other.old.eloScore - self.old.eloScore) / 400))",
        "teammate": "0.5 if self.new.teamId and other.new.teamId and self.new.teamId == other.new.teamId else 1",
    }
    formula = engine.compile("k * (actual - expected) * teammate")
    variable_plan = engine.compile_variables(expressions)
    output = {}
    for i, player in enumerate(drivers):
        delta = 0.0
        for j, opponent in enumerate(drivers):
            if i == j:
                continue
            scope = {
                "self": {
                    "old": {"eloScore": player[1], "roundsPlayed": player[2]},
                    "new": {"finishPosition": player[3], "teamId": player[4]},
                },
                "other": {"old": {"eloScore": opponent[1]}, "new": {"teamId": opponent[4]}},
                "count": len(drivers),
            }
            pair_change = formula(variable_plan(scope))
            assert isinstance(pair_change, (int, float)) and not isinstance(pair_change, bool)
            delta += pair_change
        output[player[0]] = (
            engine.evaluate("js_fixed(delta, 2)", {"delta": delta}),
            engine.evaluate("js_round(old + delta)", {"old": player[1], "delta": delta}),
        )

    assert output == {
        "DriverA": (23.23, 1623),
        "DriverB": (8.82, 1609),
        "DriverC": (7.92, 1508),
        "DriverD": (-7.92, 1492),
        "DriverE": (-17.13, 1383),
        "DriverF": (-45.13, 1355),
    }


@pytest.mark.parametrize(
    "expression, context, expected",
    [
        ("1 < 2", {}, True),
        ("3 >= 7", {}, False),
        ("'Alpha' < 'Beta'", {}, True),
        ("'Red' == team", {"team": "Red"}, True),
    ],
)
def test_strict_scalar_comparison_regressions(
    expression: str, context: dict[str, object], expected: bool
) -> None:
    assert StatLineFormulaEngine().evaluate(expression, context) is expected


@pytest.mark.parametrize("expression", ["'2' < 3", "None > 1", "True < False"])
def test_strict_order_rejects_mixed_or_non_numeric_operands(expression: str) -> None:
    with pytest.raises(FormulaError, match="Expected a number"):
        StatLineFormulaEngine().evaluate(expression, {})


def test_formula_caches_are_bounded_and_release_unused_engines() -> None:
    """Cached compilation must not retain abandoned formula engine objects."""
    import gc
    import weakref

    def create_short_lived_engine() -> weakref.ReferenceType[StatLineFormulaEngine]:
        engine = StatLineFormulaEngine()
        first = engine.compile("1 + x")
        assert first is engine.compile("1 + x")
        assert first({"x": 2}) == 3
        plan = engine.compile_variables({"a": "1 + x"})
        assert plan is engine.compile_variables({"a": "1 + x"})
        assert plan({"x": 2}) == {"a": 3}
        assert "hits=" in str(engine.compile.cache_info())
        return weakref.ref(engine)

    previous_engine = create_short_lived_engine()
    gc.collect()
    assert previous_engine() is None
    with pytest.raises(FormulaError, match="characters"):
        StatLineFormulaEngine(max_chars=2).compile("1 + x")
