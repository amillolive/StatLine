# statline/core/adapters/compile.py
from __future__ import annotations

import ast
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Callable, Optional, SupportsFloat, SupportsIndex, TypeAlias, cast

from .hooks import get as get_hooks
from .types import (
    AdapterSpec,
    BucketSpec,
    DimensionSpec,
    EffSpec,
    FilterSpec,
    MetricSpec,
    ScoreProfileSpec,
    SniffSpec,
    SourceSpec,
    TransformSpec,
)

_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod)
_ALLOWED_UNARY = (ast.UAdd, ast.USub)

# What float(x) accepts (typing-wise)
_ConvertibleToFloat: TypeAlias = SupportsFloat | SupportsIndex | str | bytes | bytearray


def _finite(x: float, default: float = 0.0) -> float:
    """
    Consistent with loader policy:
      - non-numeric or non-finite -> default (0.0)
    """
    try:
        xf = float(x)
    except Exception:
        return default
    return xf if math.isfinite(xf) else default


def _num(v: object) -> float:
    try:
        if v is None:
            return 0.0
        if isinstance(v, (int, float)):
            return _finite(float(v))
        if isinstance(v, str):
            s = v.strip().replace(",", ".")
            return _finite(float(s)) if s else 0.0

        # attempt float conversion for float-convertible objects
        return _finite(float(cast(_ConvertibleToFloat, v)))
    except Exception:
        return 0.0


def _eval_expr(expr: str, ctx: Mapping[str, object]) -> float:
    try:
        tree = ast.parse(expr, mode="eval")
    except Exception:
        return 0.0

    def _ev(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _ev(node.body)

        if isinstance(node, ast.Constant):
            return _num(node.value)

        if isinstance(node, ast.Name):
            return _num(ctx.get(node.id, 0.0))

        if isinstance(node, ast.UnaryOp) and isinstance(node.op, _ALLOWED_UNARY):
            v = _ev(node.operand)
            return +v if isinstance(node.op, ast.UAdd) else -v

        if isinstance(node, ast.BinOp) and isinstance(node.op, _ALLOWED_BINOPS):
            a, b = _ev(node.left), _ev(node.right)
            if isinstance(node.op, ast.Add):
                return a + b
            if isinstance(node.op, ast.Sub):
                return a - b
            if isinstance(node.op, ast.Mult):
                return a * b
            if isinstance(node.op, ast.Div):
                return a / b if abs(b) > 1e-12 else 0.0
            if isinstance(node.op, ast.FloorDiv):
                return a // b if abs(b) > 1e-12 else 0.0
            return a % b if abs(b) > 1e-12 else 0.0

        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            fn = node.func.id
            if fn in ("min", "max"):
                vals = [_ev(arg) for arg in node.args]
                return (min if fn == "min" else max)(vals) if vals else 0.0

        return 0.0

    return float(_ev(tree))


def _sanitize_row(raw: Mapping[str, object]) -> dict[str, object]:
    out: dict[str, object] = {}
    for k, v in raw.items():
        key = str(k)
        if isinstance(v, str):
            s = v.strip()
            out[key] = _num(s) if s else 0.0
        else:
            out[key] = v
    return out


def _compute_source(row: Mapping[str, object], src: SourceSpec) -> float:
    if src.kind == "field":
        return _num(row.get(src.field or "", 0.0))
    if src.kind == "const":
        return _num(src.const)
    if src.kind == "expr":
        return _eval_expr(src.expr or "", row)
    raise ValueError(f"Unsupported source kind: {src.kind}")


def _apply_transform(x: float, spec: Optional[TransformSpec], ctx: Mapping[str, object]) -> float:
    if spec is None:
        return x

    # params are already flattened by loader into dict[str, MetaValue]
    p: dict[str, object] = dict(spec.params)

    if spec.kind == "expr":
        expr = str(p.get("expr", "")).strip()
        if not expr:
            return x
        ctx2: dict[str, object] = dict(ctx)
        ctx2["x"] = x
        return _eval_expr(expr, ctx2)

    if spec.kind == "affine":
        scale = _num(p.get("scale", p.get("a", 1.0)))
        offset = _num(p.get("offset", p.get("b", 0.0)))
        return x * scale + offset

    if spec.kind == "scale":
        return x * _num(p.get("scale", 1.0))

    if spec.kind == "clip":
        lo = _num(p.get("lo", x))
        hi = _num(p.get("hi", x))
        return min(max(x, lo), hi)

    if spec.kind == "round":
        nd = int(_num(p.get("ndigits", 0)))
        try:
            return float(round(x, nd))
        except Exception:
            return x

    if spec.kind == "custom":
        name = str(p.get("name", "")).lower()

        if name == "linear":
            return x * _num(p.get("scale", 1.0)) + _num(p.get("offset", 0.0))
        if name == "capped_linear":
            cap = _num(p.get("cap", x))
            return x if x <= cap else cap
        if name == "minmax":
            lo = _num(p.get("lo", x))
            hi = _num(p.get("hi", x))
            return min(max(x, lo), hi)
        if name == "pct01":
            by = _num(p.get("by", 100.0)) or 100.0
            return x / by
        if name == "softcap":
            cap = _num(p.get("cap", x))
            slope = _num(p.get("slope", 1.0))
            return x if x <= cap else cap + (x - cap) * slope
        if name == "log1p":
            return math.log1p(max(x, 0.0)) * _num(p.get("scale", 1.0))

        raise ValueError(f"Unknown custom transform '{name}'")

    raise ValueError(f"Unknown transform kind '{spec.kind}'")


@dataclass(frozen=True)
class CompiledAdapter:
    key: str
    version: str
    aliases: tuple[str, ...]
    title: str

    dimensions: dict[str, DimensionSpec]
    sniff: SniffSpec
    filters: dict[str, FilterSpec]
    score_profiles: dict[str, ScoreProfileSpec]

    metrics: list[MetricSpec]
    buckets: dict[str, BucketSpec]
    weights: dict[str, dict[str, float]]
    penalties: dict[str, dict[str, float]]
    efficiency: list[EffSpec]

    def map_raw(self, raw: Mapping[str, object]) -> dict[str, float]:
        hooks_obj: object = get_hooks(self.key)
        raw_d: dict[str, object] = dict(raw)

        pre = getattr(hooks_obj, "pre_map", None)
        if callable(pre):
            row = cast(Callable[[dict[str, object]], Mapping[str, object]], pre)(raw_d)
        else:
            row = raw_d

        ctx = _sanitize_row(row)
        out: dict[str, float] = {}

        for m in self.metrics:
            # Loader is fail-fast, but keep a hard guard for programmatic specs.
            if m.source is None:
                raise ValueError(f"Metric '{m.key}' missing source (invalid AdapterSpec).")
            x = _compute_source(ctx, m.source)
            x = _apply_transform(x, m.transform, ctx)
            out[m.key] = _finite(float(x))
            ctx[m.key] = out[m.key]

        for e in self.efficiency:
            mk = _eval_expr(e.make, ctx)
            at = _eval_expr(e.attempt, ctx)

            # Keep your safeguard semantics (denom never < max(1, min_den)).
            min_den = float(e.min_den or 1.0)
            den = at if at >= max(1e-12, min_den) else max(1.0, min_den)

            val = (mk / den) if den > 0 else 0.0
            val = _apply_transform(val, e.transform, ctx)
            out[e.key] = _finite(float(val))
            ctx[e.key] = out[e.key]

        post = getattr(hooks_obj, "post_map", None)
        if callable(post):
            return cast(Callable[[dict[str, float]], dict[str, float]], post)(out)
        return out

    def map_raw_to_metrics(self, raw: Mapping[str, object]) -> Mapping[str, object]:
        return self.map_raw(dict(raw))


def compile_adapter(spec: AdapterSpec) -> CompiledAdapter:
    if getattr(spec, "mapping", None):
        raise ValueError("Legacy mapping is unsupported; use typed source/transform.")

    return CompiledAdapter(
        key=spec.key,
        version=spec.version,
        aliases=spec.aliases,
        title=(spec.title or spec.key),
        dimensions=dict(spec.dimensions),
        sniff=spec.sniff,
        filters=dict(spec.filters),
        score_profiles=dict(spec.score_profiles),
        metrics=list(spec.metrics),
        buckets=dict(spec.buckets),
        weights=dict(spec.weights),
        penalties=dict(spec.penalties),
        efficiency=list(spec.efficiency),
    )


__all__ = ["CompiledAdapter", "compile_adapter"]
