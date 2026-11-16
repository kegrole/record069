"""Aggregate state-machine output into a JSON-serialisable report."""

from __future__ import annotations

from .alerts import AlertType
from .states import MonitoringEngine, State


def _url_report(monitor) -> dict:
    return {
        "url": monitor.url,
        "final_state": monitor.state.value,
        "fail_count": monitor.fail_count,
        "fingerprint_baseline": monitor.fingerprint_baseline,
        "fingerprint_changed": monitor.fingerprint_changed,
        "latency_baseline_ms": monitor.latency_baseline_ms,
        "latency_limit_ms": monitor.latency_limit_ms,
        "latency_records_ms": list(monitor.latencies),
        "transitions": [transition.to_dict() for transition in monitor.transitions],
        "alerts": [alert.to_dict() for alert in monitor.alerts],
    }


def build_report(engine: MonitoringEngine, generated_at: str | None = None) -> dict:
    """Build the full report, including totals and per-type alert counts."""
    urls = [_url_report(monitor) for monitor in engine.monitors.values()]
    all_alerts = [
        alert
        for monitor in engine.monitors.values()
        for alert in monitor.alerts
    ]

    alert_distribution = {
        AlertType.FAULT.value: 0,
        AlertType.RECOVERY.value: 0,
    }
    for alert in all_alerts:
        alert_distribution[alert.type.value] += 1

    state_counts = {state.value: 0 for state in State}
    for monitor in engine.monitors.values():
        state_counts[monitor.state.value] += 1

    return {
        "generated_at": generated_at,
        "config": {
            "failure_threshold": engine.config.failure_threshold,
            "latency_multiplier": engine.config.latency_multiplier,
            "baseline_size": engine.config.baseline_size,
        },
        "totals": {
            "url_count": len(engine.monitors),
            "fault_count": state_counts[State.FAULT.value],
            "suspect_count": state_counts[State.SUSPECT.value],
            "recovery_count": state_counts[State.RECOVERY.value],
            "normal_count": state_counts[State.NORMAL.value],
            "alert_count": len(all_alerts),
        },
        "alert_distribution": alert_distribution,
        "urls": urls,
    }


def summarize(report: dict) -> str:
    """Return the single stdout summary line required by the CLI."""
    totals = report["totals"]
    return (
        f"URLs: {totals['url_count']}, "
        f"faults: {totals['fault_count']}, "
        f"suspects: {totals['suspect_count']}, "
        f"alerts: {totals['alert_count']}"
    )
