"""StatLine formula compilation and evaluation."""

from functools import lru_cache

from statline.core.formula.engine import FormulaError, StatLineFormulaEngine


@lru_cache(maxsize=1)
def get_formula_engine() -> StatLineFormulaEngine:
    """One process-wide strict compiler; cached plans do not cache row results."""
    return StatLineFormulaEngine()


__all__ = ["FormulaError", "StatLineFormulaEngine", "get_formula_engine"]
