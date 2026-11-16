"""Probe records, live HTTP probing, fixture loading and history storage."""

from __future__ import annotations

import json
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .fingerprint import content_fingerprint


@dataclass(frozen=True)
class ProbeResult:
    url: str
    timestamp: str
    status_code: int | None
    latency_ms: float
    response_length: int
    content_sha256: str
    ok: bool
    error: str | None = None
    probe_index: int = 0

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "timestamp": self.timestamp,
            "status_code": self.status_code,
            "latency_ms": self.latency_ms,
            "response_length": self.response_length,
            "content_sha256": self.content_sha256,
            "ok": self.ok,
            "error": self.error,
            "probe_index": self.probe_index,
        }


def probe_url(url: str, timeout: float = 10.0, probe_index: int = 0) -> ProbeResult:
    """Perform one live HTTP probe using only the standard library."""
    started = time.perf_counter()
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read()
            status_code = response.getcode()
            error = None
    except urllib.error.HTTPError as exc:
        # An HTTP error response is still a completed probe with a body.
        body = exc.read() if exc.fp is not None else b""
        status_code = exc.code
        error = None
    except Exception as exc:  # network/timeout errors produce a failed record
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return ProbeResult(
            url=url,
            timestamp=timestamp,
            status_code=None,
            latency_ms=round(elapsed_ms, 3),
            response_length=0,
            content_sha256="",
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            probe_index=probe_index,
        )

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return ProbeResult(
        url=url,
        timestamp=timestamp,
        status_code=status_code,
        latency_ms=round(elapsed_ms, 3),
        response_length=len(body),
        content_sha256=content_fingerprint(body),
        ok=200 <= int(status_code) < 400,
        error=error,
        probe_index=probe_index,
    )


def load_fixture(path: str | Path) -> list[ProbeResult]:
    """Load recorded probes from a JSON fixture.

    Accepted top-level shapes are a bare list or an object with a ``probes``
    key. Each entry needs ``url``, ``timestamp``, ``latency_ms`` and either a
    successful ``status_code`` or an ``ok``/``error`` failure marker. When
    ``content`` is supplied its sha256 is derived automatically.
    """
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if isinstance(payload, dict):
        entries = payload.get("probes", [])
    else:
        entries = payload

    results = []
    per_url_index: dict[str, int] = {}
    for entry in entries:
        url = entry["url"]
        probe_index = per_url_index.get(url, 0)
        per_url_index[url] = probe_index + 1

        body = entry.get("content")
        fingerprint = entry.get("content_sha256") or entry.get("fingerprint")
        if fingerprint is None:
            fingerprint = content_fingerprint(body if body is not None else b"")

        status_code = entry.get("status_code")
        if "ok" in entry:
            ok = bool(entry["ok"])
        else:
            ok = status_code is not None and 200 <= int(status_code) < 400

        response_length = entry.get("response_length")
        if response_length is None:
            if body is None:
                response_length = int(entry.get("length", 0))
            elif isinstance(body, str):
                response_length = len(body.encode("utf-8"))
            else:
                response_length = len(body)

        results.append(
            ProbeResult(
                url=url,
                timestamp=entry["timestamp"],
                status_code=status_code,
                latency_ms=float(entry["latency_ms"]),
                response_length=int(response_length),
                content_sha256=fingerprint,
                ok=ok,
                error=entry.get("error"),
                probe_index=entry.get("probe_index", probe_index),
            )
        )
    return results


class HistoryStore:
    """SQLite-backed probe history (standard library ``sqlite3`` only)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path))
        self.connection.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS probe_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                status_code INTEGER,
                latency_ms REAL NOT NULL,
                response_length INTEGER NOT NULL,
                content_sha256 TEXT NOT NULL,
                ok INTEGER NOT NULL,
                error TEXT,
                probe_index INTEGER NOT NULL
            )
            """
        )
        self.connection.commit()

    def save(self, probe: ProbeResult) -> None:
        self.connection.execute(
            """
            INSERT INTO probe_history (
                url, timestamp, status_code, latency_ms, response_length,
                content_sha256, ok, error, probe_index
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                probe.url,
                probe.timestamp,
                probe.status_code,
                probe.latency_ms,
                probe.response_length,
                probe.content_sha256,
                1 if probe.ok else 0,
                probe.error,
                probe.probe_index,
            ),
        )
        self.connection.commit()

    def save_many(self, probes: list[ProbeResult]) -> None:
        for probe in probes:
            self.save(probe)

    def fetch_for_url(self, url: str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT url, timestamp, status_code, latency_ms, response_length,
                   content_sha256, ok, error, probe_index
              FROM probe_history
             WHERE url = ?
             ORDER BY id ASC
            """,
            (url,),
        ).fetchall()
        return [dict(row) for row in rows]

    def count(self) -> int:
        return self.connection.execute(
            "SELECT COUNT(*) FROM probe_history"
        ).fetchone()[0]

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "HistoryStore":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
