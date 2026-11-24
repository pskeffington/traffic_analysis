#!/usr/bin/env python3
"""Create a tiny synthetic TRANS-compatible SQLite database for the README demo."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import sqlite3


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def create_database(path: Path) -> None:
    path = path.expanduser().absolute()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    os.close(descriptor)
    try:
        db = sqlite3.connect(path)
        try:
            db.executescript("""
              CREATE TABLE audio_sources (
                source_id TEXT PRIMARY KEY,
                call_id INTEGER NOT NULL,
                started_at TEXT
              );
              CREATE TABLE audio_transcripts (
                source_id TEXT NOT NULL,
                profile_name TEXT NOT NULL,
                profile_version TEXT NOT NULL,
                transcript_sha256 TEXT NOT NULL,
                provenance_sha256 TEXT NOT NULL,
                review_status TEXT NOT NULL,
                PRIMARY KEY(source_id,profile_name,profile_version)
              );
              CREATE TABLE audio_transcript_segments (
                source_id TEXT NOT NULL,
                profile_name TEXT NOT NULL,
                profile_version TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                start_ms INTEGER NOT NULL,
                end_ms INTEGER NOT NULL,
                text TEXT NOT NULL,
                text_sha256 TEXT NOT NULL,
                content_kind TEXT NOT NULL,
                confidence REAL,
                confidence_method TEXT,
                PRIMARY KEY(source_id,profile_name,profile_version,sequence)
              );
            """)
            source_id = "synthetic-call-001"
            profile = ("synthetic-demo", "1")
            timestamp = "2026-01-01T12:00:00+00:00"
            segments = (
                (0, 0, 2200, "UNIT 12 to DISPATCH, request traffic control.", "speech"),
                (1, 2300, 4200, "Engine 2 reports a structure fire; Battalion 3 responding.", "speech"),
                (2, 4300, 5900, "Police unit 14 en route to a traffic stop.", "speech"),
                (3, 6000, 7600, "Medic 4 transporting a patient to hospital.", "speech"),
                (4, 7700, 9300, "School bus 15 stopped near campus; students are safe.", "speech"),
                (5, 9400, 11100, "Downed power line across roadway; utility crew requested.", "speech"),
                (6, 11300, 13000, "[crowd cheering]", "audible_event"),
                (7, 13200, 14100, "[unintelligible radio traffic]", "uncertain"),
            )
            transcript_text = "\n".join(row[3] for row in segments)
            db.execute(
                "INSERT INTO audio_sources VALUES(?,?,?)",
                (source_id, 1, timestamp),
            )
            db.execute(
                "INSERT INTO audio_transcripts VALUES(?,?,?,?,?,?)",
                (source_id, *profile, digest(transcript_text),
                 digest("synthetic TRANS-compatible provenance v1"), "accepted"),
            )
            db.executemany(
                """INSERT INTO audio_transcript_segments
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (source_id, *profile, sequence, start_ms, end_ms, text,
                     digest(text), content_kind, 0.9, "synthetic-example-only")
                    for sequence, start_ms, end_ms, text, content_kind in segments
                ],
            )
            db.commit()
        finally:
            db.close()
    except Exception:
        path.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path,
                        help="new SQLite path; existing files are never overwritten")
    args = parser.parse_args()
    create_database(args.output)
    print(f"Created synthetic TRANS-compatible database: {args.output.expanduser()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
