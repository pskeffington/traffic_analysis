"""Fail-closed synchronization of TRANS-owned audio evidence tables."""
from __future__ import annotations

import sqlite3
from pathlib import Path


TRANS_TABLES = (
    "audio_sources",
    "audio_transcripts",
    "audio_transcript_segments",
    "audio_transcript_reviews",
)
_MUTABLE_SOURCE_COLUMNS = {"duration_seconds", "status", "failure_code"}


def _table_sql(db: sqlite3.Connection, name: str) -> str:
    row = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    if not row or not row[0]:
        raise RuntimeError("trans_checkpoint_missing_required_table")
    return row[0]


def _columns(db: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in db.execute(f'PRAGMA table_info("{table}")')]


def _primary_key(db: sqlite3.Connection, table: str) -> tuple[str, ...]:
    rows = [row for row in db.execute(f'PRAGMA table_info("{table}")') if row[5]]
    return tuple(row[1] for row in sorted(rows, key=lambda row: row[5]))


def _dependent_sql(db: sqlite3.Connection, kind: str, table: str):
    return db.execute(
        "SELECT name,sql FROM sqlite_master WHERE type=? AND tbl_name=? "
        "AND sql IS NOT NULL ORDER BY name",
        (kind, table),
    ).fetchall()


def sync_trans_tables(source_path: Path, destination: sqlite3.Connection) -> dict[str, int]:
    """Append TRANS evidence to a private combined DB; reject key/schema drift.

    TRANS is the canonical writer. Existing source identity and transcript rows
    cannot be replaced. Only the operational state fields on audio_sources may
    advance as the source worker processes its queue. Review rows are append-only.
    """
    source = sqlite3.connect(f"file:{source_path.resolve()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    source.execute("PRAGMA query_only=ON")
    try:
        if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("trans_checkpoint_integrity_check_failed")
        if source.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("trans_checkpoint_foreign_key_check_failed")
        source_tables = {
            row[0] for row in source.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if not set(TRANS_TABLES).issubset(source_tables):
            raise RuntimeError("trans_checkpoint_missing_required_table")

        destination.row_factory = sqlite3.Row
        destination.execute("PRAGMA foreign_keys=ON")
        destination.execute("BEGIN IMMEDIATE")
        counts = {"inserted": 0, "updated_source_state": 0}
        try:
            for table in TRANS_TABLES:
                source_ddl = _table_sql(source, table)
                existing = destination.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
                if existing is None:
                    destination.execute(source_ddl)
                elif existing[0] != source_ddl:
                    raise RuntimeError("trans_table_schema_conflict")
                if _columns(source, table) != _columns(destination, table):
                    raise RuntimeError("trans_table_column_conflict")
                if _primary_key(source, table) != _primary_key(destination, table):
                    raise RuntimeError("trans_table_primary_key_conflict")

            for table in TRANS_TABLES:
                columns = _columns(source, table)
                keys = _primary_key(source, table)
                if not keys:
                    raise RuntimeError("trans_table_primary_key_missing")
                names = ",".join(f'"{column}"' for column in columns)
                placeholders = ",".join("?" for _ in columns)
                lookup = " AND ".join(f'"{key}"=?' for key in keys)
                for row in source.execute(f'SELECT * FROM "{table}"'):
                    values = tuple(row[column] for column in columns)
                    key_values = tuple(row[key] for key in keys)
                    prior = destination.execute(
                        f'SELECT * FROM "{table}" WHERE {lookup}', key_values
                    ).fetchone()
                    if prior is None:
                        destination.execute(
                            f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})', values
                        )
                        counts["inserted"] += 1
                        continue
                    differing = [column for column in columns if prior[column] != row[column]]
                    if table == "audio_sources" and set(differing).issubset(
                        _MUTABLE_SOURCE_COLUMNS
                    ):
                        if differing:
                            assignments = ",".join(f'"{column}"=?' for column in differing)
                            destination.execute(
                                f'UPDATE "audio_sources" SET {assignments} WHERE {lookup}',
                                tuple(row[column] for column in differing) + key_values,
                            )
                            counts["updated_source_state"] += 1
                    elif differing:
                        raise RuntimeError("trans_existing_row_conflict")

            for kind in ("index", "trigger"):
                for table in TRANS_TABLES:
                    for name, ddl in _dependent_sql(source, kind, table):
                        prior = destination.execute(
                            "SELECT sql FROM sqlite_master WHERE type=? AND name=?",
                            (kind, name),
                        ).fetchone()
                        if prior is None:
                            destination.execute(ddl)
                        elif prior[0] != ddl:
                            raise RuntimeError("trans_dependent_schema_conflict")
            if destination.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise RuntimeError("combined_database_foreign_key_check_failed")
            destination.commit()
        except Exception:
            destination.rollback()
            raise
        return counts
    finally:
        source.close()
