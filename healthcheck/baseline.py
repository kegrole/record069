"""Latency baseline calculations.

The baseline is deliberately the **median** of the first N recorded probe
latencies. Every probe contributes a latency sample, including failed probes;
the median must not be silently replaced with an arithmetic mean.
"""

from __future__ import annotations


def median_latency(samples: list[float]) -> float:
    """Return the median of latency samples in milliseconds.

    For an even number of samples the two middle values are averaged, which
    matches :func:`statistics.median`.
    """
    if not samples:
        raise ValueError("at least one latency sample is required")
    ordered = sorted(samples)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def baseline_latency(latencies: list[float], baseline_size: int) -> float:
    """Return the median of the first ``baseline_size`` latency records."""
    if baseline_size <= 0:
        raise ValueError("baseline_size must be positive")
    window = latencies[:baseline_size]
    return median_latency(window)


def latency_threshold(baseline_ms: float, multiplier: float) -> float:
    """Return the latency that a probe must exceed to be suspicious."""
    if multiplier <= 0:
        raise ValueError("latency_multiplier must be positive")
    return baseline_ms * multiplier
