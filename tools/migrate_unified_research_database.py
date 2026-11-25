#!/usr/bin/env python3
"""Build and publish the combined Research DB while retaining legacy inputs."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "src"))

from traffic_analysis.cli import main  # noqa: E402
from traffic_analysis.trans_sync import TRANS_TABLES, sync_trans_tables  # noqa: E402
from run_corpus_cycle import (  # noqa: E402
    ANALYSIS_HOME,
    DEFAULT_ANALYSIS_SNAPSHOT,
    DEFAULT_TRANS_CHECKPOINT,
    LEGACY_ANALYSIS_SNAPSHOT,
    configured_path,
    inflate_trans_checkpoint,
    publish_snapshot,
    require_local_checkpoint,
    restore_snapshot,
    run_cli,
    verify_research_mount,
)

_PROTECTED_TABLES = {
    "analysis_records": "record_id",
    "neural_analyses": "analysis_id",
    "neural_failures": (
        "record_id,model_id,model_manifest_sha256,engine_version,prompt_version"
    ),
    "connection_candidates": "candidate_id",
}


def table_digest(db: sqlite3.Connection, table: str, order: str) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    for row in db.execute(f'SELECT * FROM "{table}" ORDER BY {order}'):
        digest.update(json.dumps(tuple(row), ensure_ascii=False,
                                 separators=(",", ":")).encode())
        digest.update(b"\n")
        count += 1
    return count, digest.hexdigest()


def database_integrity(path: Path) -> None:
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("combined_database_integrity_check_failed")
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("combined_database_foreign_key_check_failed")


def main_migration() -> int:
    os.umask(0o077)
    data_home = configured_path("TRAFFIC_ANALYSIS_DATA_HOME", ANALYSIS_HOME)
    data_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    if data_home.stat().st_mode & 0o077:
        raise RuntimeError("local_analysis_directory_is_not_private")
    lock_fd = os.open(data_home / "production-cycle.lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(lock_fd, "r+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("traffic_analysis_cycle_already_running") from exc

        checkpoint = configured_path(
            "TRAFFIC_ANALYSIS_TRANS_CHECKPOINT", DEFAULT_TRANS_CHECKPOINT
        )
        source_snapshot = configured_path(
            "TRAFFIC_ANALYSIS_LEGACY_SNAPSHOT", LEGACY_ANALYSIS_SNAPSHOT
        )
        destination_snapshot = configured_path(
            "TRAFFIC_ANALYSIS_SNAPSHOT", DEFAULT_ANALYSIS_SNAPSHOT
        )
        if destination_snapshot.exists():
            raise RuntimeError("combined_research_database_already_exists")
        verify_research_mount(destination_snapshot)
        verify_research_mount(source_snapshot)
        require_local_checkpoint(checkpoint)

        with tempfile.TemporaryDirectory(prefix="traffic-unified-migration-") as temp:
            work = Path(temp)
            database = work / "research.sqlite"
            if not restore_snapshot(source_snapshot, database):
                raise RuntimeError("legacy_traffic_analysis_snapshot_missing")
            run_cli(database, "report")
            before = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
            try:
                protected_before = {
                    table: table_digest(before, table, order)
                    for table, order in _PROTECTED_TABLES.items()
                }
            finally:
                before.close()

            trans_database = inflate_trans_checkpoint(checkpoint, work)
            destination = sqlite3.connect(database)
            try:
                trans_sync = sync_trans_tables(trans_database, destination)
            finally:
                destination.close()
            imported = run_cli(
                database, "import-trans", "--trans-database", str(trans_database)
            )
            run_cli(database, "report")
            database_integrity(database)
            after = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
            try:
                protected_after = {
                    table: table_digest(after, table, order)
                    for table, order in _PROTECTED_TABLES.items()
                }
                trans_counts = {
                    table: after.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                    for table in TRANS_TABLES
                }
                projections = {
                    "arch_notebook_classifications": after.execute(
                        "SELECT count(*) FROM arch_notebook_classifications"
                    ).fetchone()[0],
                    "arch_notebook_connections": after.execute(
                        "SELECT count(*) FROM arch_notebook_connections"
                    ).fetchone()[0],
                }
            finally:
                after.close()
            if protected_before != protected_after:
                raise RuntimeError("existing_analysis_or_review_rows_changed")
            if trans_counts["audio_transcript_segments"] == 0:
                raise RuntimeError("combined_database_has_no_transcript_segments")

            snapshot_digest = publish_snapshot(database, destination_snapshot)
            print(json.dumps({
                "status": "PASS",
                "source_snapshot": str(source_snapshot),
                "combined_snapshot": str(destination_snapshot),
                "snapshot_sha256": snapshot_digest,
                "trans_table_sync": trans_sync,
                "trans_table_counts": trans_counts,
                "existing_analysis_review_rows_unchanged": True,
                "arch_notebook_projection_rows": projections,
                "import_summary": json.loads(imported),
                "legacy_files_retained": True,
            }, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main_migration())
    except (OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
        print(f"traffic-analysis migration: {exc}", file=sys.stderr)
        raise SystemExit(2)
