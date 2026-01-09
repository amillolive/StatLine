# statline/core/adapters/compile.py
from __future__ import annotations

# ───────────────────── Strict-path helpers (no legacy / no eval) ─────────────
import ast
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .hooks import get as get_hooks
from .types import AdapterSpec, EffSpec, MetricSpec

# Allowed operations for tiny arithmetic inside expressions (safe subset).
_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod)
_ALLOWED_UNARY = (ast.UAdd, ast.USub)


def _finite(x: float) -> float:
    """Coerce NaN/inf to safe values (0.0 for NaN/−inf, +inf→1e308 guard)."""
    try:
        xf = float(x)
    except Exception:
        return 0.0
    if not math.isfinite(xf):
        return 1e308 if xf > 0 else 0.0
    return xf


def _num(v: Any) -> float:
    """Best-effort numeric coercion with comma-as-dot support."""
    try:
        if v is None:
            return 0.0
        if isinstance(v, (int, float)):
            return _finite(float(v))
        if isinstance(v, str):
            s = v.strip().replace(",", ".")
            return _finite(float(s)) if s else 0.0
        return _finite(float(v))
    except Exception:
        return 0.0


def _eval_expr(expr: str, ctx: Mapping[str, Any]) -> float:
    """
    Extremely small safe-expression evaluator.
    Supports: numbers, identifiers, + - * / // %, unary +/-, min(), max(), and parentheses.
    Identifiers resolve from ctx by name.
    """
    try:
        tree = ast.parse(str(expr), mode="eval")
    except Exception:
        return 0.0

    def _ev(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _ev(node.body)

        if isinstance(node, ast.Constant):   # literals
            return _num(node.value)

        if isinstance(node, ast.Name):       # identifiers
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
            # Mod
            return a % b if abs(b) > 1e-12 else 0.0

        # allow min/max only (positional args, no keywords)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            fn = node.func.id
            if fn in ("min", "max"):
                vals = [_ev(arg) for arg in node.args]
                return (min if fn == "min" else max)(vals) if vals else 0.0

        return 0.0

    return float(_ev(tree))


def _sanitize_row(raw: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize input row to str->(float/str) with numeric-like strings coerced."""
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        key = str(k)
        if isinstance(v, str):
            s = v.strip()
            if s == "":
                out[key] = 0.0
                continue
            try:
                out[key] = _num(s.replace(",", "."))
                continue
            except Exception:
                pass
        out[key] = v
    return out


def _compute_source(row: Mapping[str, Any], src: Mapping[str, Any]) -> float:
    """Evaluate a strict 'source' block into a numeric value."""
    if "field" in src:
        return _num(row.get(str(src["field"]), 0))

    if "ratio" in src:
        r = src["ratio"]
        num = _num(row.get(str(r["num"]), 0))
        den = _num(row.get(str(r["den"]), 0))
        min_den = _num(r.get("min_den", 1))
        den = den if den >= max(min_den, 1e-12) else max(min_den, 1.0)
        return num / den

    if "sum" in src:
        keys: Sequence[Any] = src["sum"]
        return float(sum(_num(row.get(str(k), 0)) for k in keys))

    if "diff" in src:
        d = src["diff"]
        return _num(row.get(str(d["a"]), 0)) - _num(row.get(str(d["b"]), 0))

    if "const" in src:
        return _num(src["const"])

    if "expr" in src:
        return _eval_expr(str(src["expr"]), row)

    raise ValueError(f"Unsupported source: {src}")


def _apply_transform(x: float, spec: Optional[Mapping[str, Any]]) -> float:
    """Apply an optional transform to a numeric value."""
    if not spec:
        return x
    name = str(spec.get("name", "")).lower()
    p = dict(spec.get("params") or {})

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

    raise ValueError(f"Unknown transform '{name}'")


# ────────────────────────── Compiled adapter (strict only) ───────────────────

@dataclass(frozen=True)
class CompiledAdapter:
    """
    Fully strict compiled adapter: no legacy mapping, no eval(), no JSON configs.
    Provides a stable mapping API used by the calculator/scorer.
    """
    key: str
    version: str
    aliases: Tuple[str, ...]
    title: str
    dimensions: Dict[str, Any]
    sniff: Dict[str, Any]
    filters: Dict[str, Any]
    score_profiles: Dict[str, Any]
    metrics: List[MetricSpec]
    buckets: Dict[str, Any]
    weights: Dict[str, Dict[str, float]]
    penalties: Dict[str, Dict[str, float]]
    efficiency: List[EffSpec]

    # Public API expected by the rest of core ---------------------------------
    def map_raw(self, raw: Mapping[str, Any]) -> Dict[str, float]:
        """
        Map a raw input row (strings, numbers) into normalized metric values.
        - Runs adapter hooks (pre_map/post_map) if present.
        - Supports strict sources (field/ratio/sum/diff/const/expr).
        - Evaluates `efficiency` specs after primary metrics so they can reference them.
        """
        hooks = get_hooks(self.key)
        raw_d = dict(raw)  # <-- normalize Mapping -> Dict
        row = hooks.pre_map(raw_d) if hasattr(hooks, "pre_map") else raw_d
        ctx = _sanitize_row(row)
        out: Dict[str, float] = {}

        # 1) primary metrics
        for m in self.metrics:
            if m.source is None:
                raise KeyError(
                    f"Metric '{m.key}' missing strict 'source' block "
                    f"(legacy mapping is unsupported)."
                )
            x = _compute_source(ctx, m.source)
            x = _apply_transform(x, m.transform)
            xv = float(x)
            out[m.key] = _finite(xv)
            ctx[m.key] = out[m.key]  # allow later expressions to reference prior outputs

        # 2) efficiency channels (derived after metrics)
        for e in self.efficiency:
            mk = _eval_expr(e.make, ctx)
            at = _eval_expr(e.attempt, ctx)
            den = at if at >= max(1e-12, float(e.min_den or 1.0)) else float(e.min_den or 1.0)
            val = (mk / den) if den > 0 else 0.0
            val = _apply_transform(val, e.transform)
            out[e.key] = _finite(float(val))
            ctx[e.key] = out[e.key]

        return hooks.post_map(out) if hasattr(hooks, "post_map") else out

    # Back-compat shim: some call sites prefer map_raw_to_metrics()
    def map_raw_to_metrics(self, raw: Mapping[str, Any]) -> Mapping[str, Any]:
        return self.map_raw(dict(raw))


def compile_adapter(spec: AdapterSpec) -> CompiledAdapter:
    """
    Compile a strict AdapterSpec into a CompiledAdapter.
    Refuses legacy/loose mapping: only `source/transform/clamp` and `efficiency` are supported.
    """
    # Enforce strict mode: refuse legacy mapping if someone tries to sneak it in.
    if getattr(spec, "mapping", None):
        raise ValueError(
            "Legacy expression mapping is no longer supported. "
            "Convert adapter to strict 'source/transform/clamp' spec."
        )

    # Concrete copies for immutability / type clarity.
    metrics: List[MetricSpec] = list(spec.metrics)
    buckets: Dict[str, Any] = dict(spec.buckets or {})
    weights: Dict[str, Dict[str, float]] = dict(spec.weights or {})
    penalties: Dict[str, Dict[str, float]] = dict(spec.penalties or {})
    efficiency: List[EffSpec] = list(spec.efficiency or [])
    dimensions: Dict[str, Any] = dict(getattr(spec, "dimensions", {}) or {})
    sniff: Dict[str, Any] = dict(getattr(spec, "sniff", {}) or {})
    filters: Dict[str, Any] = dict(getattr(spec, "filters", {}) or {})
    score_profiles: Dict[str, Any] = dict(getattr(spec, "score_profiles", {}) or {})


    return CompiledAdapter(
        key=spec.key,
        version=spec.version,
        aliases=tuple(spec.aliases or ()),
        title=(spec.title or spec.key),
        dimensions=dimensions,
        sniff=sniff,
        filters=filters,
        score_profiles=score_profiles, 
        metrics=metrics,
        buckets=buckets,
        weights=weights,
        penalties=penalties,
        efficiency=efficiency,
    )

__all__ = ["CompiledAdapter", "compile_adapter"]
