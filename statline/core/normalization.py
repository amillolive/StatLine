# statline/core/normalization.py
from __future__ import annotations

import math
from typing import SupportsFloat


def clamp01(x: SupportsFloat) -> float:
    """
    Clamp a numeric value to the [0.0, 1.0] range.
    - NaN -> 0.0
    - +inf -> 1.0
    - -inf -> 0.0
    """
    xf = float(x)
    if math.isnan(xf):
        return 0.0
    if math.isinf(xf):
        return 1.0 if xf > 0 else 0.0
    return 0.0 if xf < 0.0 else (1.0 if xf > 1.0 else xf)


def norm(value: SupportsFloat, max_value: SupportsFloat) -> float:
    """
    Normalize `value` by `max_value` into [0, 1].
    Returns 0.0 if max_value <= 0, or if either argument is NaN.
    """
    v = float(value)
    m = float(max_value)
    if m <= 0.0 or math.isnan(v) or math.isnan(m):
        return 0.0
    return clamp01(v / m)


__all__ = ["clamp01", "norm"]
