"""Optional attribution from the scorer's actual components and run metadata."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from typing import Any


def explanation_context(
    adapter: Any,
    context: Mapping[str, Mapping[str, float]],
    profile_weights: Mapping[str, Mapping[str, float]],
    profiles: Mapping[str, Any],
    normalization_source: str,
    raw_scale: float,
) -> dict[str, Any]:
    """Describe a run once, including effective weights after penalties/overrides."""
    metrics = {}
    for spec in (*adapter.metrics, *adapter.efficiency):
        descriptor = asdict(spec)
        descriptor["label"] = spec.key.replace("_", " ").title()
        bucket = adapter.buckets.get(spec.bucket)
        descriptor["bucket_label"] = bucket.title if bucket else spec.bucket
        metrics[spec.key] = descriptor
    return {
        "schema_version": "1",
        "metrics": metrics,
        "context_used": dict(context),
        "normalization_source": normalization_source,
        "profile_weights": {name: dict(weights) for name, weights in profile_weights.items()},
        "profiles": {name: asdict(profile) for name, profile in profiles.items()},
        "baseline": {
            "kind": "normalized_midpoint",
            "normalized_value": 0.5,
            "description": "Each normalized metric at 0.5; a reference, not the cohort average.",
        },
        "raw_scale": raw_scale,
        "rounding": "Python round: nearest integer, ties to even",
        "bucket_aggregation": "Unweighted means of nonzero primary-weight metrics; not additive points.",
        "attribution": "Contributions explain the weighted sum before clipping and profile scaling.",
        "model_notice": "A model-based evaluation under this adapter, weights and cohort.",
    }


def explain_row(
    mapped: Mapping[str, Any],
    components: Mapping[str, float],
    profile_weights: Mapping[str, Mapping[str, float]],
    profiles: Mapping[str, Any],
    raw_aggregates: Mapping[str, float],
    percentiles: Mapping[str, float],
    weighted_sums: Mapping[str, float],
    unrounded_scores: Mapping[str, float],
    scores: Mapping[str, int],
    unscoreable: bool,
) -> dict[str, Any]:
    """Attribute the raw sum without claiming additive effects on final scores."""
    explained = {}
    for name, weights in profile_weights.items():
        contributions = {key: components.get(key, 0.0) * weight for key, weight in weights.items()}
        influence = {
            key: (components.get(key, 0.0) - 0.5) * weight for key, weight in weights.items()
        }
        total = weighted_sums[name]
        raw = raw_aggregates[name]
        helped = sorted(
            (key for key in influence if influence[key] > 1e-12), key=lambda key: -influence[key]
        )[:3]
        limited = sorted(
            (key for key in influence if influence[key] < -1e-12), key=lambda key: influence[key]
        )[:3]
        kind = profiles[name].kind
        explained[name] = {
            "contributions": contributions,
            "relative_influence": influence,
            "weighted_sum": total,
            "baseline_weighted_sum": sum(0.5 * weight for weight in weights.values()),
            "raw_aggregate": raw,
            "aggregate_clipped": total < 0.0 or total > 1.0,
            "unscoreable_override": unscoreable,
            "percentile": percentiles[name] if kind == "window" else None,
            "final_unrounded": unrounded_scores[name],
            "final_score": scores[name],
            "helped": helped,
            "limited": limited,
            "final_attribution": "Cohort percentile/window; raw contributions are not final-score points."
            if kind == "window"
            else "Affine scaling of the clipped raw aggregate, followed by rounding.",
        }
    return {
        "metrics": {
            key: {"mapped_value": mapped.get(key), "normalized_value": value}
            for key, value in components.items()
        },
        "profiles": explained,
    }
