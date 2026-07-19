"""Adapter validation functions."""
from __future__ import annotations

from typing import cast

from statline.core.types.adapters import (
    AdapterSpec,
    AdapterValidationError,
    ValidationIssue,
)


def validate_adapter(spec: AdapterSpec) -> None:
    issues: list[ValidationIssue] = []
    if not spec.key.strip():
        issues.append(ValidationIssue("key", "Missing or empty key."))
    if not spec.version.strip():
        issues.append(ValidationIssue("version", "Missing or empty version."))
    bucket_keys = set(spec.buckets)
    seen_metrics: set[str] = set()
    for index, metric in enumerate(spec.metrics):
        path = f"metrics[{index}]"
        if metric.key in seen_metrics:
            issues.append(ValidationIssue(f"{path}.key", f"Duplicate metric key '{metric.key}'."))
        seen_metrics.add(metric.key)
        if metric.bucket is not None and metric.bucket not in bucket_keys:
            issues.append(ValidationIssue(f"{path}.bucket", f"Unknown bucket '{metric.bucket}'.", "Define it under buckets: or fix the metric.bucket."))
        if metric.clamp is not None and not metric.clamp[0] < metric.clamp[1]:
            issues.append(ValidationIssue(f"{path}.clamp", "Clamp must be (lo, hi) with lo < hi."))
    seen_efficiency: set[str] = set()
    for index, efficiency in enumerate(spec.efficiency):
        path = f"efficiency[{index}]"
        if efficiency.key in seen_efficiency:
            issues.append(ValidationIssue(f"{path}.key", f"Duplicate efficiency key '{efficiency.key}'."))
        seen_efficiency.add(efficiency.key)
        if efficiency.bucket not in bucket_keys:
            issues.append(ValidationIssue(f"{path}.bucket", f"Unknown bucket '{efficiency.bucket}'."))
        if efficiency.clamp is not None and not efficiency.clamp[0] < efficiency.clamp[1]:
            issues.append(ValidationIssue(f"{path}.clamp", "Clamp must be (lo, hi) with lo < hi."))
        if efficiency.min_den < 0:
            issues.append(ValidationIssue(f"{path}.min_den", "min_den must be >= 0."))
    for name, profile in spec.score_profiles.items():
        path = f"score_profiles.{name}"
        if profile.weights_profile not in spec.weights:
            issues.append(ValidationIssue(f"{path}.weights_profile", f"Unknown weights profile '{profile.weights_profile}'.", "Add it under weights: or correct the reference."))
        if profile.kind == "affine":
            if profile.lo is None or profile.hi is None:
                issues.append(ValidationIssue(path, "Affine profile requires lo and hi."))
            elif not profile.lo < profile.hi:
                issues.append(ValidationIssue(path, "Affine requires lo < hi."))
        elif profile.kind == "window":
            required = ("out_lo", "out_hi", "pct_lo", "pct_hi")
            missing = [key for key in required if getattr(profile, key) is None]
            if missing:
                issues.append(ValidationIssue(path, f"Window profile missing {missing}."))
            else:
                if not cast(float, profile.out_lo) < cast(float, profile.out_hi):
                    issues.append(ValidationIssue(path, "Window requires out_lo < out_hi."))
                if not cast(float, profile.pct_lo) < cast(float, profile.pct_hi):
                    issues.append(ValidationIssue(path, "Window requires pct_lo < pct_hi."))
    if issues:
        raise AdapterValidationError(spec.key or "<unknown>", issues)


__all__ = ["validate_adapter"]
