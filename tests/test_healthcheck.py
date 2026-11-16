"""Acceptance tests for the standard-library-only healthcheck package.

The seven numbered test classes map one-to-one to the seven judgment clauses
in the task specification, including the regenerated demo/probes.json fixture.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from healthcheck.alerts import AlertManager, AlertType
from healthcheck.baseline import baseline_latency, latency_threshold, median_latency
from healthcheck.cli import main as cli_main
from healthcheck.fingerprint import content_fingerprint
from healthcheck.probe import HistoryStore, ProbeResult, load_fixture, probe_url
from healthcheck.report import build_report
from healthcheck.states import EngineConfig, MonitoringEngine, State

FIXTURE_PATH = REPO_ROOT / "demo" / "probes.json"
URLS = {
    "A": "https://example.invalid/a",
    "B": "https://example.invalid/b",
    "C": "https://example.invalid/c",
    "D": "https://example.invalid/d",
    "E": "https://example.invalid/e",
}
CONFIG = EngineConfig(
    failure_threshold=3, latency_multiplier=3.0, baseline_size=3
)


def run_fixture(config: EngineConfig = CONFIG):
    probes = load_fixture(FIXTURE_PATH)
    engine = MonitoringEngine(config)
    engine.process(probes)
    report = build_report(engine, generated_at="2026-11-16T09:00:00Z")
    return probes, engine, report


def make_probe(
    url: str,
    index: int,
    status_code: int | None = 200,
    latency_ms: float = 50.0,
    body: str = "same-body",
    ok: bool | None = None,
    error: str | None = None,
    timestamp: str | None = None,
) -> ProbeResult:
    if ok is None:
        ok = status_code is not None and 200 <= status_code < 400
    raw = body.encode("utf-8") if body else b""
    return ProbeResult(
        url=url,
        timestamp=timestamp or f"2026-11-16T00:{index:02d}:00Z",
        status_code=status_code,
        latency_ms=float(latency_ms),
        response_length=len(raw),
        content_sha256=content_fingerprint(raw),
        ok=ok,
        error=error,
        probe_index=index,
    )


class Clause1FixtureContractTests(unittest.TestCase):
    def setUp(self):
        self.probes = load_fixture(FIXTURE_PATH)

    def test_five_urls_each_have_six_ordered_probes(self):
        by_url = {}
        for probe in self.probes:
            by_url.setdefault(probe.url, []).append(probe)

        self.assertEqual(set(by_url), set(URLS.values()))
        for probes in by_url.values():
            self.assertEqual(len(probes), 6)
            self.assertEqual(
                [probe.probe_index for probe in probes], list(range(6))
            )

    def test_status_sequences(self):
        by_url = self._by_url()
        self.assertEqual([p.status_code for p in by_url[URLS["A"]]], [200] * 6)
        self.assertEqual(
            [p.status_code for p in by_url[URLS["B"]]],
            [500, 500, 500, 500, 200, 200],
        )
        self.assertEqual(
            [p.status_code for p in by_url[URLS["C"]]],
            [500, 500, 200, 200, 200, 200],
        )
        self.assertEqual([p.status_code for p in by_url[URLS["D"]]], [200] * 6)
        self.assertEqual([p.status_code for p in by_url[URLS["E"]]], [200] * 6)

    def test_latency_sequences_include_every_probe(self):
        by_url = self._by_url()
        for key in ("A", "B", "C", "D"):
            self.assertEqual(
                [p.latency_ms for p in by_url[URLS[key]]], [50.0] * 6
            )
        self.assertEqual(
            [p.latency_ms for p in by_url[URLS["E"]]],
            [100.0, 100.0, 300.0, 400.0, 400.0, 400.0],
        )
        for probe in self.probes:
            self.assertIsInstance(probe.latency_ms, float)

    def test_fingerprint_and_body_contracts(self):
        raw_fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        raw_by_url = {}
        for entry in raw_fixture["probes"]:
            raw_by_url.setdefault(entry["url"], []).append(entry)

        b_entries = raw_by_url[URLS["B"]]
        self.assertEqual(len({e["content"] for e in b_entries[:4]}), 1)
        self.assertEqual(len({e["content_sha256"] for e in b_entries[:4]}), 1)

        c_entries = raw_by_url[URLS["C"]]
        self.assertNotEqual(c_entries[0]["content"], c_entries[2]["content"])
        self.assertNotEqual(
            c_entries[0]["content_sha256"], c_entries[2]["content_sha256"]
        )

        d_entries = raw_by_url[URLS["D"]]
        self.assertEqual(len({e["content_sha256"] for e in d_entries[:3]}), 1)
        self.assertEqual(len({e["content_sha256"] for e in d_entries[3:]}), 1)
        self.assertNotEqual(
            d_entries[0]["content_sha256"], d_entries[3]["content_sha256"]
        )

        e_entries = raw_by_url[URLS["E"]]
        self.assertEqual(len({e["content_sha256"] for e in e_entries}), 1)

        for probe in self.probes:
            self.assertEqual(len(probe.content_sha256), 64)
            self.assertGreaterEqual(probe.response_length, 0)

    def _by_url(self):
        grouped = {}
        for probe in self.probes:
            grouped.setdefault(probe.url, []).append(probe)
        return grouped


class Clause2StateOutcomeTests(unittest.TestCase):
    def test_exactly_three_urls_ever_became_fault_or_suspect(self):
        _, engine, _ = run_fixture()
        touched = set()
        for url, monitor in engine.monitors.items():
            reached = {transition.to_state for transition in monitor.transitions}
            if State.FAULT in reached or State.SUSPECT in reached:
                touched.add(url)
        self.assertEqual(touched, {URLS["B"], URLS["D"], URLS["E"]})

    def test_final_states(self):
        _, engine, _ = run_fixture()
        monitors = engine.monitors
        self.assertEqual(monitors[URLS["A"]].state, State.NORMAL)
        self.assertEqual(monitors[URLS["B"]].state, State.RECOVERY)
        self.assertEqual(monitors[URLS["C"]].state, State.NORMAL)
        self.assertEqual(monitors[URLS["D"]].state, State.SUSPECT)
        self.assertEqual(monitors[URLS["E"]].state, State.SUSPECT)

    def test_b_fault_then_recovery_and_d_e_remain_suspect(self):
        _, engine, _ = run_fixture()
        b_states = [t.to_state for t in engine.monitors[URLS["B"]].transitions]
        self.assertEqual(b_states, [State.FAULT, State.RECOVERY])
        self.assertEqual(
            [t.to_state for t in engine.monitors[URLS["D"]].transitions],
            [State.SUSPECT],
        )
        self.assertEqual(
            [t.to_state for t in engine.monitors[URLS["E"]].transitions],
            [State.SUSPECT],
        )


class Clause3FlapSuppressionAndBaselineTests(unittest.TestCase):
    def test_c_two_failures_do_not_enter_fault_or_suspect_and_reset_to_zero(self):
        probes = load_fixture(FIXTURE_PATH)
        c_probes = [probe for probe in probes if probe.url == URLS["C"]]
        engine = MonitoringEngine(CONFIG)
        monitor = engine.get_monitor(URLS["C"])

        monitor.process(c_probes[0])
        self.assertEqual(monitor.fail_count, 1)
        self.assertEqual(monitor.state, State.NORMAL)
        monitor.process(c_probes[1])
        self.assertEqual(monitor.fail_count, 2)
        self.assertEqual(monitor.state, State.NORMAL)

        monitor.process(c_probes[2])
        self.assertEqual(monitor.fail_count, 0)
        self.assertEqual(monitor.state, State.NORMAL)
        for probe in c_probes[3:]:
            monitor.process(probe)
        self.assertEqual(monitor.state, State.NORMAL)
        self.assertEqual(monitor.transitions, [])

    def test_c_fingerprint_baseline_is_first_success_not_failure_body(self):
        probes = load_fixture(FIXTURE_PATH)
        c_probes = [probe for probe in probes if probe.url == URLS["C"]]
        engine = MonitoringEngine(CONFIG)
        monitor = engine.get_monitor(URLS["C"])
        for probe in c_probes:
            monitor.process(probe)

        self.assertEqual(
            monitor.fingerprint_baseline, c_probes[2].content_sha256
        )
        self.assertNotEqual(
            monitor.fingerprint_baseline, c_probes[0].content_sha256
        )
        self.assertFalse(monitor.fingerprint_changed)

    def test_intermediate_success_resets_failure_counter_generally(self):
        engine = MonitoringEngine(EngineConfig(failure_threshold=3))
        monitor = engine.get_monitor("https://example.invalid/flap")
        for index in range(2):
            monitor.process(
                make_probe(monitor.url, index, status_code=500, body="err")
            )
        self.assertEqual(monitor.fail_count, 2)

        monitor.process(make_probe(monitor.url, 2, body="ok"))
        self.assertEqual(monitor.fail_count, 0)
        for index in range(2):
            monitor.process(
                make_probe(monitor.url, index + 3, status_code=500, body="err")
            )
        self.assertEqual(monitor.fail_count, 2)
        self.assertIs(monitor.state, State.NORMAL)


class Clause4AlertDeduplicationTests(unittest.TestCase):
    def test_exactly_two_alerts_one_fault_one_recovery_for_b(self):
        _, engine, report = run_fixture()
        all_alerts = [
            alert
            for monitor in engine.monitors.values()
            for alert in monitor.alerts
        ]
        self.assertEqual(len(all_alerts), 2)
        self.assertEqual(
            [alert.type for alert in all_alerts],
            [AlertType.FAULT, AlertType.RECOVERY],
        )
        b_alerts = engine.monitors[URLS["B"]].alerts
        self.assertEqual(len(b_alerts), 2)
        self.assertEqual(b_alerts[0].probe_index, 2)
        self.assertEqual(b_alerts[1].probe_index, 4)
        self.assertEqual(report["totals"]["alert_count"], 2)

    def test_fourth_consecutive_failure_does_not_emit_another_fault_alert(self):
        manager = AlertManager()
        first = manager.raise_alert(AlertType.FAULT, "u", "t1", 2)
        duplicate = manager.raise_alert(AlertType.FAULT, "u", "t2", 3)
        self.assertIsNotNone(first)
        self.assertIsNone(duplicate)
        self.assertEqual(len(manager.alerts), 1)

    def test_first_success_in_fault_recovers_without_second_success(self):
        engine = MonitoringEngine(EngineConfig(failure_threshold=3))
        monitor = engine.get_monitor("https://example.invalid/recover")
        for index in range(3):
            monitor.process(
                make_probe(monitor.url, index, status_code=500, body="err")
            )
        self.assertEqual(monitor.state, State.FAULT)
        self.assertEqual(len(monitor.alerts), 1)

        alerts = monitor.process(make_probe(monitor.url, 3, body="ok"))
        self.assertEqual(monitor.state, State.RECOVERY)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].type, AlertType.RECOVERY)
        self.assertEqual(len(monitor.alerts), 2)

    def test_recovery_is_sticky_until_new_signal(self):
        engine = MonitoringEngine(EngineConfig(failure_threshold=3))
        monitor = engine.get_monitor("https://example.invalid/sticky")
        for index in range(3):
            monitor.process(
                make_probe(monitor.url, index, status_code=500, body="err")
            )
        monitor.process(make_probe(monitor.url, 3, body="ok"))
        self.assertEqual(monitor.state, State.RECOVERY)
        monitor.process(make_probe(monitor.url, 4, body="ok"))
        monitor.process(make_probe(monitor.url, 5, body="ok"))
        self.assertEqual(monitor.state, State.RECOVERY)
        self.assertEqual(len(monitor.alerts), 2)

    def test_recovery_can_move_to_suspect_on_new_fingerprint_signal(self):
        engine = MonitoringEngine(EngineConfig(failure_threshold=3))
        monitor = engine.get_monitor("https://example.invalid/recovery-signal")
        for index in range(3):
            monitor.process(
                make_probe(monitor.url, index, status_code=500, body="err")
            )
        monitor.process(make_probe(monitor.url, 3, body="ok"))
        self.assertEqual(monitor.state, State.RECOVERY)

        monitor.process(make_probe(monitor.url, 4, body="changed"))
        self.assertEqual(monitor.state, State.SUSPECT)
        self.assertEqual(len(monitor.alerts), 2)
        self.assertEqual(
            [t.to_state for t in monitor.transitions],
            [State.FAULT, State.RECOVERY, State.SUSPECT],
        )

    def test_later_fault_episode_can_alert_again_after_recovery(self):
        engine = MonitoringEngine(EngineConfig(failure_threshold=3))
        monitor = engine.get_monitor("https://example.invalid/episode")
        for index in range(3):
            monitor.process(
                make_probe(monitor.url, index, status_code=500, body="err")
            )
        monitor.process(make_probe(monitor.url, 3, body="ok"))
        for index in range(4, 7):
            monitor.process(
                make_probe(monitor.url, index, status_code=500, body="err")
            )
        types = [alert.type for alert in monitor.alerts]
        self.assertEqual(
            types,
            [AlertType.FAULT, AlertType.RECOVERY, AlertType.FAULT],
        )


class Clause5MedianBaselineTests(unittest.TestCase):
    def test_median_baseline_for_e_is_100_and_limit_is_300(self):
        _, engine, _ = run_fixture()
        monitor = engine.monitors[URLS["E"]]
        self.assertEqual(monitor.latency_baseline_ms, 100.0)
        self.assertEqual(monitor.latency_limit_ms, 300.0)
        self.assertTrue(monitor.latency_high)
        self.assertEqual(monitor.state, State.SUSPECT)

    def test_median_not_mean_is_required(self):
        samples = [100.0, 100.0, 300.0]
        median = median_latency(samples)
        mean = sum(samples) / len(samples)
        self.assertEqual(median, 100.0)
        self.assertAlmostEqual(mean, 166.6666667)
        self.assertEqual(latency_threshold(median, 3.0), 300.0)
        self.assertEqual(latency_threshold(mean, 3.0), 500.0)
        self.assertGreater(400.0, latency_threshold(median, 3.0))
        self.assertLess(400.0, latency_threshold(mean, 3.0))

    def test_baseline_uses_first_three_failed_and_successful_latencies(self):
        url = "https://example.invalid/latency-failures"
        probes = [
            make_probe(url, 0, status_code=500, latency_ms=100, body="err"),
            make_probe(url, 1, status_code=500, latency_ms=100, body="err"),
            make_probe(url, 2, status_code=200, latency_ms=300, body="ok"),
        ]
        engine = MonitoringEngine(EngineConfig(baseline_size=3))
        monitor = engine.get_monitor(url)
        for probe in probes:
            monitor.process(probe)
        self.assertEqual(monitor.latency_baseline_ms, 100.0)
        self.assertEqual(monitor.latency_limit_ms, 300.0)

    def test_even_number_median_averages_middle_values(self):
        self.assertEqual(median_latency([10.0, 20.0, 30.0, 40.0]), 25.0)
        self.assertEqual(baseline_latency([1.0, 9.0, 2.0], 3), 2.0)


class Clause6ReportShapeTests(unittest.TestCase):
    def test_each_url_report_contains_final_state_transitions_and_alerts(self):
        _, _, report = run_fixture()
        self.assertEqual(len(report["urls"]), 5)
        for url_report in report["urls"]:
            self.assertIn("final_state", url_report)
            self.assertIn("transitions", url_report)
            self.assertIn("alerts", url_report)
            for transition in url_report["transitions"]:
                self.assertIn("timestamp", transition)
                self.assertIn("from_state", transition)
                self.assertIn("to_state", transition)
            for alert in url_report["alerts"]:
                self.assertIn(alert["type"], ("fault", "recovery"))

    def test_suspect_transitions_never_create_alerts(self):
        _, engine, _ = run_fixture()
        for key in ("D", "E"):
            monitor = engine.monitors[URLS[key]]
            self.assertTrue(
                any(t.to_state is State.SUSPECT for t in monitor.transitions)
            )
            self.assertEqual(monitor.alerts, [])

    def test_transition_records_previous_and_next_state_with_time(self):
        _, engine, _ = run_fixture()
        transitions = engine.monitors[URLS["B"]].transitions
        self.assertEqual(len(transitions), 2)
        self.assertEqual(transitions[0].from_state, State.NORMAL)
        self.assertEqual(transitions[0].to_state, State.FAULT)
        self.assertEqual(transitions[1].from_state, State.FAULT)
        self.assertEqual(transitions[1].to_state, State.RECOVERY)
        self.assertTrue(transitions[0].timestamp)
        self.assertTrue(transitions[1].timestamp)

    def test_fingerprint_change_with_200_is_suspect_and_holds_until_restored(self):
        url = "https://example.invalid/content-change"
        probes = [
            make_probe(url, 0, latency_ms=10, body="v1"),
            make_probe(url, 1, latency_ms=10, body="v1"),
            make_probe(url, 2, latency_ms=10, body="v1"),
            make_probe(url, 3, latency_ms=10, body="v2"),
            make_probe(url, 4, latency_ms=10, body="v2"),
            make_probe(url, 5, latency_ms=10, body="v1"),
        ]
        engine = MonitoringEngine(CONFIG)
        monitor = engine.get_monitor(url)
        for probe in probes:
            monitor.process(probe)
        states = [t.to_state for t in monitor.transitions]
        self.assertEqual(states, [State.SUSPECT, State.NORMAL])
        self.assertEqual(monitor.state, State.NORMAL)
        self.assertEqual(monitor.alerts, [])


class Clause7PersistenceAndSummaryTests(unittest.TestCase):
    def test_report_totals_and_alert_distribution(self):
        _, _, report = run_fixture()
        self.assertEqual(report["totals"]["url_count"], 5)
        self.assertEqual(report["totals"]["alert_count"], 2)
        self.assertEqual(
            report["alert_distribution"], {"fault": 1, "recovery": 1}
        )
        self.assertEqual(report["totals"]["fault_count"], 0)
        self.assertEqual(report["totals"]["suspect_count"], 2)
        self.assertEqual(report["totals"]["recovery_count"], 1)
        self.assertEqual(report["totals"]["normal_count"], 2)

    def test_every_probe_is_persisted_and_queryable_per_url(self):
        probes, _, _ = run_fixture()
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "history.db"
            with HistoryStore(db_path) as store:
                store.save_many(probes)
                self.assertEqual(store.count(), 30)
                for url in URLS.values():
                    rows = store.fetch_for_url(url)
                    self.assertEqual(len(rows), 6)
                    for row in rows:
                        self.assertEqual(row["url"], url)
                        self.assertIn("latency_ms", row)
                        self.assertIn("content_sha256", row)
                        self.assertIn("status_code", row)
                        self.assertIn("response_length", row)

            connection = sqlite3.connect(db_path)
            count = connection.execute(
                "SELECT COUNT(*) FROM probe_history WHERE latency_ms IS NOT NULL"
            ).fetchone()[0]
            connection.close()
            self.assertEqual(count, 30)

    def test_cli_writes_report_history_and_prints_one_summary_line(self):
        with tempfile.TemporaryDirectory() as directory:
            out_path = Path(directory) / "nested" / "report.json"
            db_path = Path(directory) / "history.db"
            exit_code = cli_main([
                "--probes", str(FIXTURE_PATH),
                "--out", str(out_path),
                "--db", str(db_path),
            ])
            self.assertEqual(exit_code, 0)
            report = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(report["totals"]["url_count"], 5)
            self.assertEqual(report["totals"]["alert_count"], 2)
            self.assertTrue(db_path.exists())

            with HistoryStore(db_path) as store:
                self.assertEqual(store.count(), 30)


class LiveProbeTests(unittest.TestCase):
    def test_probe_url_records_code_latency_length_and_sha256(self):
        body = b"live response body"

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                return

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/health"
            result = probe_url(url, timeout=2.0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.ok)
        self.assertEqual(result.response_length, len(body))
        self.assertEqual(result.content_sha256, content_fingerprint(body))
        self.assertGreaterEqual(result.latency_ms, 0.0)

    def test_failed_network_probe_still_records_latency(self):
        result = probe_url("http://127.0.0.1:1/never", timeout=0.1)
        self.assertFalse(result.ok)
        self.assertIsNone(result.status_code)
        self.assertGreaterEqual(result.latency_ms, 0.0)
        self.assertEqual(result.content_sha256, "")
        self.assertIsNotNone(result.error)


if __name__ == "__main__":
    unittest.main(verbosity=2)
