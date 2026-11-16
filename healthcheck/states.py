"""Per-URL health state machine and the replay engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .alerts import AlertManager, AlertType
from .baseline import baseline_latency, latency_threshold


class State(str, Enum):
    NORMAL = "normal"
    SUSPECT = "suspect"
    FAULT = "fault"
    RECOVERY = "recovery"


@dataclass(frozen=True)
class Transition:
    timestamp: str
    from_state: State
    to_state: State
    reason: str
    probe_index: int

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "from_state": self.from_state.value,
            "to_state": self.to_state.value,
            "reason": self.reason,
            "probe_index": self.probe_index,
        }


@dataclass(frozen=True)
class EngineConfig:
    failure_threshold: int = 3
    latency_multiplier: float = 3.0
    baseline_size: int = 3

    def __post_init__(self) -> None:
        if self.failure_threshold <= 0:
            raise ValueError("failure_threshold must be positive")
        if self.latency_multiplier <= 0:
            raise ValueError("latency_multiplier must be positive")
        if self.baseline_size <= 0:
            raise ValueError("baseline_size must be positive")


@dataclass
class UrlMonitor:
    """State machine for one URL.

    States:
        normal: healthy, or suspicious conditions have cleared.
        suspect: held while fingerprint differs from baseline or latency is
            above the median-derived threshold.
        fault: ``failure_threshold`` consecutive probes failed.
        recovery: sticky after the first success following a fault; it remains
            until a fresh fault or suspect signal appears.
    """

    url: str
    config: EngineConfig
    state: State = State.NORMAL
    fail_count: int = 0
    fingerprint_baseline: str | None = None
    fingerprint_changed: bool = False
    latency_high: bool = False
    latency_baseline_ms: float | None = None
    latency_limit_ms: float | None = None
    latencies: list[float] = field(default_factory=list)
    transitions: list[Transition] = field(default_factory=list)
    alert_manager: AlertManager = field(default_factory=AlertManager)

    def process(self, probe) -> list:
        """Process one probe and return any alerts created for it."""
        self.latencies.append(float(probe.latency_ms))
        self._update_latency_baseline()

        if not probe.ok:
            return self._handle_failure(probe)
        return self._handle_success(probe)

    def _update_latency_baseline(self) -> None:
        if len(self.latencies) >= self.config.baseline_size:
            self.latency_baseline_ms = baseline_latency(
                self.latencies, self.config.baseline_size
            )
            self.latency_limit_ms = latency_threshold(
                self.latency_baseline_ms, self.config.latency_multiplier
            )

    def _handle_failure(self, probe) -> list:
        # Failed probes never establish or compare a content fingerprint.
        self.fail_count += 1
        if self.fail_count >= self.config.failure_threshold:
            self._move(State.FAULT, probe, "consecutive_failure_threshold")
            alert = self.alert_manager.raise_alert(
                AlertType.FAULT,
                self.url,
                probe.timestamp,
                probe.probe_index,
                detail=f"fail_count={self.fail_count}",
            )
            return [alert] if alert is not None else []
        return []

    def _handle_success(self, probe) -> list:
        created_alerts = []
        self.fail_count = 0

        if self.fingerprint_baseline is None:
            self.fingerprint_baseline = probe.content_sha256

        self.fingerprint_changed = (
            probe.content_sha256 != self.fingerprint_baseline
        )
        if self.latency_limit_ms is None:
            self.latency_high = False
        else:
            self.latency_high = probe.latency_ms > self.latency_limit_ms

        if self.state is State.FAULT:
            # One successful probe is enough: do not require two successes.
            self._move(State.RECOVERY, probe, "first_success_after_fault")
            alert = self.alert_manager.raise_alert(
                AlertType.RECOVERY,
                self.url,
                probe.timestamp,
                probe.probe_index,
            )
            if alert is not None:
                created_alerts.append(alert)
        elif self.fingerprint_changed or self.latency_high:
            reason = self._suspect_reason(probe)
            self._move(State.SUSPECT, probe, reason)
        elif self.state is State.SUSPECT:
            # Both signals must clear before leaving the suspect state.
            self._move(State.NORMAL, probe, "baseline_and_latency_restored")
        # NORMAL stays normal, and RECOVERY is sticky until a new signal.

        return created_alerts

    def _suspect_reason(self, probe) -> str:
        reasons = []
        if self.fingerprint_changed:
            reasons.append("fingerprint_changed")
        if self.latency_high:
            reasons.append("latency_above_threshold")
        return "+".join(reasons)

    def _move(self, new_state: State, probe, reason: str) -> None:
        if self.state is new_state:
            return
        self.transitions.append(
            Transition(
                timestamp=probe.timestamp,
                from_state=self.state,
                to_state=new_state,
                reason=reason,
                probe_index=probe.probe_index,
            )
        )
        self.state = new_state

    @property
    def alerts(self) -> list:
        return self.alert_manager.alerts


class MonitoringEngine:
    """Processes ordered probes while keeping one monitor per URL."""

    def __init__(self, config: EngineConfig | None = None) -> None:
        self.config = config or EngineConfig()
        self.monitors: dict[str, UrlMonitor] = {}

    def get_monitor(self, url: str) -> UrlMonitor:
        monitor = self.monitors.get(url)
        if monitor is None:
            monitor = UrlMonitor(url=url, config=self.config)
            self.monitors[url] = monitor
        return monitor

    def process(self, probes: list) -> list:
        """Process probes in input order; return every created alert."""
        all_alerts = []
        for probe in probes:
            monitor = self.get_monitor(probe.url)
            all_alerts.extend(monitor.process(probe))
        return all_alerts

    def url_order(self) -> list[str]:
        return list(self.monitors)
