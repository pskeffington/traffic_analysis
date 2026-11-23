"""Command line interface for TRANS text import and radio connection review."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from .store import (
    analyze_pending,
    connection_counts,
    import_trans,
    list_candidates,
    list_events,
    open_store,
    review_candidate,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True,
                        help="private local traffic-analysis SQLite database")
    sub = parser.add_subparsers(dest="action", required=True)
    ingest = sub.add_parser("import-trans", help="read a TRANS SQL database without modifying it")
    ingest.add_argument("--trans-database", type=Path, required=True)
    review = sub.add_parser("review", help="accept or reject one explicit contact candidate")
    review.add_argument("candidate_id")
    review.add_argument("--state", choices=("accepted", "rejected"), required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--note", default="")
    candidates = sub.add_parser("candidates", help="show candidate evidence for analyst review")
    candidates.add_argument("--state", choices=("pending", "accepted", "rejected"),
                            default="pending")
    candidates.add_argument("--limit", type=int, default=50)
    events = sub.add_parser("events", help="show TRANS-labeled audible-event records")
    events.add_argument("--limit", type=int, default=50)
    sub.add_parser("report", help="show aggregate counts without transcript text")
    args = parser.parse_args(argv)
    try:
        with open_store(args.database) as db:
            if args.action == "import-trans":
                result = import_trans(db, args.trans_database)
                result["candidates"] = analyze_pending(db)["created"]
            elif args.action == "review":
                review_candidate(db, args.candidate_id, args.state, args.reviewer, args.note)
                result = {"reviewed": args.candidate_id, "state": args.state}
            elif args.action == "candidates":
                if args.limit < 1 or args.limit > 1000:
                    raise ValueError("limit_must_be_between_1_and_1000")
                result = list_candidates(db, args.state, args.limit)
            elif args.action == "events":
                if args.limit < 1 or args.limit > 1000:
                    raise ValueError("limit_must_be_between_1_and_1000")
                result = list_events(db, args.limit)
            else:
                result = {"connections": connection_counts(db),
                          "speech_segments": db.execute(
                              "SELECT count(*) FROM transcript_segments WHERE content_kind='speech'"
                          ).fetchone()[0],
                          "audible_events": db.execute(
                              "SELECT count(*) FROM audible_events"
                          ).fetchone()[0],
                          "arch_notebook_ready": db.execute(
                              "SELECT count(*) FROM arch_notebook_connections"
                          ).fetchone()[0]}
            print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"traffic-analysis: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
