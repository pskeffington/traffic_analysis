"""Command line interface for TRANS text import and radio connection review."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from .store import (
    analyze_pending,
    analyze_neural_batch,
    call_type_counts,
    connection_counts,
    import_trans,
    list_neural_analyses,
    list_candidates,
    list_events,
    open_store,
    review_candidate,
    review_neural_analysis,
)
from .neural import ENGINE_VERSION, PROMPT_VERSION, classify_segment, local_engine


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
    sub.add_parser("call-types", help="show aggregate speech call-type labels")
    neural = sub.add_parser("neural-classify", help="classify speech with a private local llama.cpp model")
    neural.add_argument("--model", type=Path, required=True,
                        help="local GGUF model file; no model is downloaded")
    neural.add_argument("--llama-server", help="path to llama-server; otherwise search PATH")
    neural.add_argument("--limit", type=int, default=1000,
                        help="maximum new speech segments per run (1..1000)")
    neural.add_argument("--retry-failures", action="store_true",
                        help="retry segments previously recorded as failed for this model and prompt")
    neural.add_argument("--startup-timeout", type=float, default=180.0)
    neural.add_argument("--allow-memory-overcommit", action="store_true",
                        help="start even when model shard bytes exceed currently available RAM")
    neural_results = sub.add_parser("neural-results", help="show local model proposals for review")
    neural_results.add_argument("--state", choices=("pending", "accepted", "rejected"),
                                default="pending")
    neural_results.add_argument("--limit", type=int, default=50)
    neural_review = sub.add_parser("neural-review", help="accept or reject a model proposal")
    neural_review.add_argument("analysis_id")
    neural_review.add_argument("--state", choices=("accepted", "rejected"), required=True)
    neural_review.add_argument("--reviewer", required=True)
    neural_review.add_argument("--note", default="")
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
            elif args.action == "call-types":
                result = call_type_counts(db)
            elif args.action == "neural-classify":
                if not 1 <= args.limit <= 1000:
                    raise ValueError("limit_must_be_between_1_and_1000")
                if not 10 <= args.startup_timeout <= 1800:
                    raise ValueError("startup_timeout_must_be_between_10_and_1800_seconds")
                with local_engine(args.llama_server, args.model,
                                  args.startup_timeout,
                                  args.allow_memory_overcommit) as (
                                      socket_path, model_id, engine_version,
                                      model_manifest_sha256
                                  ):
                    result = analyze_neural_batch(
                        db, socket_path, model_id, model_manifest_sha256,
                        classify_segment, engine_version or ENGINE_VERSION,
                        PROMPT_VERSION, args.limit, args.retry_failures,
                    )
            elif args.action == "neural-results":
                result = list_neural_analyses(db, args.state, args.limit)
            elif args.action == "neural-review":
                review_neural_analysis(db, args.analysis_id, args.state,
                                       args.reviewer, args.note)
                result = {"reviewed": args.analysis_id, "state": args.state}
            else:
                result = {"connections": connection_counts(db),
                          "call_types": call_type_counts(db),
                          "speech_segments": db.execute(
                              "SELECT count(*) FROM transcript_segments WHERE content_kind='speech'"
                          ).fetchone()[0],
                          "audible_events": db.execute(
                              "SELECT count(*) FROM audible_events"
                          ).fetchone()[0],
                          "arch_notebook_ready": db.execute(
                              "SELECT count(*) FROM arch_notebook_connections"
                          ).fetchone()[0],
                          "neural_analyses": {row[0]: row[1] for row in db.execute(
                              "SELECT review_state,count(*) FROM neural_analyses "
                              "GROUP BY review_state")},
                          "neural_failure_attempts": db.execute(
                              "SELECT coalesce(sum(attempt_count),0) FROM neural_failures"
                          ).fetchone()[0],
                          "arch_notebook_classifications_ready": db.execute(
                              "SELECT count(*) FROM arch_notebook_classifications"
                          ).fetchone()[0]}
            print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"traffic-analysis: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
