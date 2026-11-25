from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from traffic_analysis.store import (
    _current_trans_review_statuses,
    _read_trans_db,
    import_trans,
    open_store,
)


TRANSCRIPT = "Unit 12 to dispatch. Traffic stopped."
SEGMENTS = (
    (0, 0, 900, "Unit 12 to dispatch.", "speech"),
    (1, 1000, 1800, "Traffic stopped.", "speech"),
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _review_digest(source: sqlite3.Connection) -> str:
    transcript = source.execute("""SELECT s.source_id,s.call_id,s.started_at,s.sha256,
        t.transcript_sha256 FROM audio_sources s
        JOIN audio_transcripts t USING(source_id)
        WHERE s.source_id='source-1'""").fetchone()
    segments = source.execute("""SELECT sequence,start_ms,end_ms,text_sha256,content_kind
        FROM audio_transcript_segments WHERE source_id='source-1'
        ORDER BY sequence""").fetchall()
    payload = {
        "contract": "trans-audio-transcript-review/v1",
        "source_id": transcript[0],
        "call_id": transcript[1],
        "started_at": transcript[2],
        "audio_sha256": transcript[3],
        "transcript_sha256": transcript[4],
        "segments": [{"sequence": row[0], "start_ms": row[1], "end_ms": row[2],
                       "text_sha256": row[3], "content_kind": row[4]}
                      for row in segments],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _source_db(path: Path) -> str:
    path.unlink(missing_ok=True)
    source = sqlite3.connect(path)
    source.executescript("""
        CREATE TABLE audio_sources (
          source_id TEXT PRIMARY KEY, call_id INTEGER, started_at TEXT, sha256 TEXT
        );
        CREATE TABLE audio_transcripts (
          source_id TEXT, call_id INTEGER, profile_name TEXT, profile_version TEXT,
          text TEXT, transcript_sha256 TEXT, provenance_sha256 TEXT, review_status TEXT
        );
        CREATE TABLE audio_transcript_segments (
          source_id TEXT, profile_name TEXT, profile_version TEXT, sequence INTEGER,
          start_ms INTEGER, end_ms INTEGER, text TEXT, text_sha256 TEXT, content_kind TEXT
        );
        CREATE TABLE audio_transcript_reviews (
          source_id TEXT, profile_name TEXT, profile_version TEXT, revision INTEGER,
          transcript_sha256 TEXT, review_sha256 TEXT, review_status TEXT
        );
    """)
    source.execute("INSERT INTO audio_sources VALUES(?,?,?,?)",
                   ("source-1", 237, "2026-01-02T03:04:05+00:00", "a" * 64))
    source.execute("INSERT INTO audio_transcripts VALUES(?,?,?,?,?,?,?,?)",
                   ("source-1", 237, "fixture", "v1", TRANSCRIPT,
                    _sha(TRANSCRIPT), "p" * 64, "pending"))
    for sequence, start_ms, end_ms, text, kind in SEGMENTS:
        source.execute("INSERT INTO audio_transcript_segments VALUES(?,?,?,?,?,?,?,?,?)",
                       ("source-1", "fixture", "v1", sequence, start_ms, end_ms,
                        text, _sha(text), kind))
    digest = _review_digest(source)
    source.execute("INSERT INTO audio_transcript_reviews VALUES(?,?,?,?,?,?,?)",
                   ("source-1", "fixture", "v1", 1, _sha(TRANSCRIPT), digest, "accepted"))
    source.commit()
    source.close()
    os.chmod(path, 0o600)
    return digest


class TranscriptReviewImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="trans-review-import-test-")
        self.root = Path(self.temp_dir.name)
        self.source_path = self.root / "trans.sqlite"
        self.expected_review_sha = _source_db(self.source_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_import_uses_latest_hash_bound_review_and_keeps_source_read_only(self) -> None:
        before = hashlib.sha256(self.source_path.read_bytes()).hexdigest()
        source, _, segment_cols = _read_trans_db(self.source_path)
        try:
            self.assertEqual(source.execute("PRAGMA query_only").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                source.execute("UPDATE audio_sources SET call_id=238")
        finally:
            source.close()

        destination_path = self.root / "analysis.sqlite"
        destination_path.touch(mode=0o600)
        db = open_store(destination_path)
        try:
            result = import_trans(db, self.source_path)
            rows = db.execute("SELECT source_review_status FROM transcript_segments "
                              "ORDER BY segment_id").fetchall()
            statuses = [row[0] for row in rows]
        finally:
            db.close()
        after = hashlib.sha256(self.source_path.read_bytes()).hexdigest()
        self.assertEqual(result["imported"], 2)
        self.assertEqual(statuses, ["accepted", "accepted"])
        self.assertEqual(before, after)

    def test_changed_source_identity_does_not_inherit_review(self) -> None:
        source = sqlite3.connect(self.source_path)
        source.execute("UPDATE audio_sources SET started_at='2026-01-02T03:05:05+00:00'")
        source.commit()
        source.row_factory = sqlite3.Row
        self.assertEqual(_current_trans_review_statuses(source, {"content_kind"}), {})
        source.close()

    def test_changed_call_identity_does_not_inherit_review(self) -> None:
        source = sqlite3.connect(self.source_path)
        source.execute("UPDATE audio_sources SET call_id=238")
        source.execute("UPDATE audio_transcripts SET call_id=238")
        source.commit()
        source.row_factory = sqlite3.Row
        self.assertEqual(_current_trans_review_statuses(source, {"content_kind"}), {})
        source.close()

    def test_changed_audio_hash_does_not_inherit_review(self) -> None:
        source = sqlite3.connect(self.source_path)
        source.execute("UPDATE audio_sources SET sha256=?", ("b" * 64,))
        source.commit()
        source.row_factory = sqlite3.Row
        self.assertEqual(_current_trans_review_statuses(source, {"content_kind"}), {})
        source.close()

    def test_changed_transcript_text_and_hash_do_not_inherit_review(self) -> None:
        source = sqlite3.connect(self.source_path)
        changed_text = "Unit 12 to dispatch. Traffic is stopped."
        changed_segment = "Traffic is stopped."
        source.execute("UPDATE audio_transcripts SET text=?,transcript_sha256=?",
                       (changed_text, _sha(changed_text)))
        source.execute("UPDATE audio_transcript_segments SET text=?,text_sha256=? "
                       "WHERE sequence=1", (changed_segment, _sha(changed_segment)))
        source.commit()
        source.row_factory = sqlite3.Row
        self.assertEqual(_current_trans_review_statuses(source, {"content_kind"}), {})
        source.close()

    def test_changed_segment_kind_or_timing_does_not_inherit_review(self) -> None:
        for statement in (
            "UPDATE audio_transcript_segments SET end_ms=end_ms+1 WHERE sequence=1",
            "UPDATE audio_transcript_segments SET content_kind='audible_event' WHERE sequence=0",
        ):
            with self.subTest(statement=statement):
                source = sqlite3.connect(self.source_path)
                source.execute(statement)
                source.commit()
                source.row_factory = sqlite3.Row
                self.assertEqual(_current_trans_review_statuses(source, {"content_kind"}), {})
                source.close()
                _source_db(self.source_path)

    def test_newer_stale_revision_supersedes_older_matching_review(self) -> None:
        source = sqlite3.connect(self.source_path)
        source.execute("INSERT INTO audio_transcript_reviews VALUES(?,?,?,?,?,?,?)",
                       ("source-1", "fixture", "v1", 2, _sha(TRANSCRIPT),
                        "f" * 64, "rejected"))
        source.commit()
        source.row_factory = sqlite3.Row
        self.assertEqual(_current_trans_review_statuses(source, {"content_kind"}), {})
        source.close()

    def test_import_does_not_use_older_matching_revision_after_new_stale_revision(self) -> None:
        source = sqlite3.connect(self.source_path)
        source.execute("INSERT INTO audio_transcript_reviews VALUES(?,?,?,?,?,?,?)",
                       ("source-1", "fixture", "v1", 2, _sha(TRANSCRIPT),
                        "f" * 64, "rejected"))
        source.commit()
        source.close()
        destination_path = self.root / "analysis-stale.sqlite"
        destination_path.touch(mode=0o600)
        db = open_store(destination_path)
        try:
            import_trans(db, self.source_path)
            statuses = db.execute("SELECT DISTINCT source_review_status "
                                  "FROM transcript_segments").fetchall()
        finally:
            db.close()
        self.assertEqual([row[0] for row in statuses], ["pending"])


if __name__ == "__main__":
    unittest.main()
