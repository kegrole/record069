"""Stdlib-only URL health checking package.

The package supports two input modes:

* replaying a fixture file of recorded probes (``--probes``);
* performing real HTTP probes against live URLs (``--urls``).

Both modes run the same state machine, alert de-duplication, reporting and
history persistence logic.
"""

from .alerts import Alert, AlertManager, AlertType
from .baseline import median_latency, latency_threshold
from .fingerprint import content_fingerprint
from .probe import HistoryStore, ProbeResult, load_fixture, probe_url
from .report import build_report
from .states import EngineConfig, MonitoringEngine, State, Transition

__all__ = [
    "Alert",
    "AlertManager",
    "AlertType",
    "EngineConfig",
    "HistoryStore",
    "MonitoringEngine",
    "ProbeResult",
    "State",
    "Transition",
    "build_report",
    "content_fingerprint",
    "latency_threshold",
    "load_fixture",
    "median_latency",
    "probe_url",
]

__version__ = "1.0.0"
