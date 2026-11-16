"""Command-line entry point for fixture replay and live URL probing."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .probe import HistoryStore, load_fixture, probe_url
from .report import build_report, summarize
from .states import EngineConfig, MonitoringEngine


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m healthcheck.cli",
        description="Stdlib-only URL health checker.",
    )
    parser.add_argument("--probes", help="Path to a recorded-probe JSON fixture")
    parser.add_argument(
        "--urls",
        nargs="*",
        default=[],
        help="Live URLs to probe; repeatable as a space-separated list",
    )
    parser.add_argument("--out", required=True, help="Report JSON output path")
    parser.add_argument("--db", help="SQLite history path (defaults to <out>/history.db)")
    parser.add_argument(
        "--failure-threshold",
        type=int,
        default=3,
        help="Consecutive failures required to enter fault (default: 3)",
    )
    parser.add_argument(
        "--latency-multiplier",
        type=float,
        default=3.0,
        help="A successful probe above baseline*multiplier is suspect (default: 3)",
    )
    parser.add_argument(
        "--baseline-size",
        type=int,
        default=3,
        help="Number of first latency records used for the median baseline (default: 3)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Per-request timeout in seconds for live probes",
    )
    return parser


def _default_db_path(out_path: Path) -> Path:
    return out_path.parent / "history.db"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.probes and not args.urls:
        raise SystemExit("either --probes or --urls is required")

    out_path = Path(args.out)
    db_path = Path(args.db) if args.db else _default_db_path(out_path)
    config = EngineConfig(
        failure_threshold=args.failure_threshold,
        latency_multiplier=args.latency_multiplier,
        baseline_size=args.baseline_size,
    )

    if args.probes:
        probes = load_fixture(args.probes)
    else:
        probes = []
        per_url_index = {}
        for url in args.urls:
            index = per_url_index.get(url, 0)
            per_url_index[url] = index + 1
            probes.append(probe_url(url, timeout=args.timeout, probe_index=index))

    engine = MonitoringEngine(config)
    engine.process(probes)

    with HistoryStore(db_path) as store:
        store.save_many(probes)

    generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    report = build_report(engine, generated_at=generated_at)
    report["history_db"] = str(db_path)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    print(summarize(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
