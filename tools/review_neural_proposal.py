#!/usr/bin/env python3
"""Review model proposals against fresh TRANS status and publish safely."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from run_corpus_cycle import (  # noqa: E402
    ANALYSIS_HOME,
    DEFAULT_ANALYSIS_SNAPSHOT,
    DEFAULT_TRANS_CHECKPOINT,
    configured_path,
    inflate_trans_checkpoint,
    publish_snapshot,
    restore_snapshot,
    run_cli,
    verify_research_mount,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    listing = actions.add_parser("list", help="list pending proposals using current TRANS status")
    listing.add_argument("--limit", type=int, default=50)
    review = actions.add_parser("review", help="accept or reject a proposal and publish it")
    review.add_argument("analysis_id")
    review.add_argument("--state", choices=("accepted", "rejected"), required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--note", default="")
    args = parser.parse_args(argv)

    try:
        if args.action == "list" and not 1 <= args.limit <= 1000:
            raise ValueError("limit_must_be_between_1_and_1000")
        data_home = configured_path("TRAFFIC_ANALYSIS_DATA_HOME", ANALYSIS_HOME)
        data_home.mkdir(parents=True, exist_ok=True, mode=0o700)
        if data_home.stat().st_mode & 0o077:
            raise RuntimeError("local_analysis_directory_is_not_private")
        lock_path = data_home / "production-cycle.lock"
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(lock_fd, "r+") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("traffic_analysis_cycle_already_running") from exc
            checkpoint = configured_path("TRAFFIC_ANALYSIS_TRANS_CHECKPOINT",
                                          DEFAULT_TRANS_CHECKPOINT)
            snapshot = configured_path("TRAFFIC_ANALYSIS_SNAPSHOT",
                                       DEFAULT_ANALYSIS_SNAPSHOT)
            verify_research_mount(checkpoint, snapshot)
            with tempfile.TemporaryDirectory(prefix="traffic-analysis-review-") as temp:
                workspace = Path(temp)
                database = workspace / "traffic-analysis.sqlite"
                if not restore_snapshot(snapshot, database):
                    raise RuntimeError("analysis_snapshot_missing_run_production_cycle_first")
                trans_database = inflate_trans_checkpoint(checkpoint, workspace)
                run_cli(database, "import-trans", "--trans-database", str(trans_database))
                if args.action == "list":
                    run_cli(database, "neural-results", "--state", "pending",
                            "--limit", str(args.limit))
                else:
                    run_cli(database, "neural-review", args.analysis_id,
                            "--state", args.state, "--reviewer", args.reviewer,
                            "--note", args.note)
                    digest = publish_snapshot(database, snapshot)
                    print(json.dumps({"published": True, "analysis_id": args.analysis_id,
                                      "state": args.state, "snapshot_sha256": digest},
                                     sort_keys=True), flush=True)
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"traffic-analysis-review: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
