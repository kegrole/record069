"""Alert creation with per-URL de-duplication.

A fault episode emits exactly one ``fault`` alert when the failure threshold
is crossed. Subsequent failed probes while the URL remains faulty are
suppressed, and a single ``recovery`` alert is emitted on the first successful
probe after the fault. Suspect state changes never create alerts.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AlertType(str, Enum):
    FAULT = "fault"
    RECOVERY = "recovery"

    @property
    def label(self) -> str:
        return {
            AlertType.FAULT: "进入故障",
            AlertType.RECOVERY: "恢复通知",
        }[self]


@dataclass(frozen=True)
class Alert:
    url: str
    type: AlertType
    timestamp: str
    message: str
    probe_index: int

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "type": self.type.value,
            "timestamp": self.timestamp,
            "message": self.message,
            "probe_index": self.probe_index,
        }


class AlertManager:
    """Tracks the active alert type to prevent duplicate notifications."""

    def __init__(self) -> None:
        self._active: AlertType | None = None
        self.alerts: list[Alert] = []

    def raise_alert(
        self,
        alert_type: AlertType,
        url: str,
        timestamp: str,
        probe_index: int,
        detail: str = "",
    ) -> Alert | None:
        """Create an alert unless one of this type is already active.

        Recovery clears the active fault so a later episode can alert again.
        """
        if self._active == alert_type:
            return None
        if alert_type is AlertType.FAULT:
            message = f"URL {url} entered FAULT after consecutive failures"
        else:
            message = f"URL {url} recovered after a successful probe"
        if detail:
            message = f"{message}: {detail}"
        alert = Alert(
            url=url,
            type=alert_type,
            timestamp=timestamp,
            message=message,
            probe_index=probe_index,
        )
        self.alerts.append(alert)
        self._active = alert_type
        return alert

    @property
    def active_type(self) -> AlertType | None:
        return self._active
