"""Private SQLite store, TRANS read-only import, and Arch Notebook projection."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from typing import Any

from .patterns import classify_message, radio_contact

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS transcript_segments (
  source_id TEXT NOT NULL,
  call_id INTEGER NOT NULL,
  profile_name TEXT NOT NULL,
  profile_version TEXT NOT NULL,
  segment_id TEXT NOT NULL,
  start_ms INTEGER NOT NULL CHECK(start_ms >= 0),
  end_ms INTEGER NOT NULL CHECK(end_ms > start_ms),
  started_at TEXT,
  occurred_at TEXT,
  text TEXT NOT NULL,
  text_sha256 TEXT NOT NULL,
  transcript_sha256 TEXT NOT NULL,
  provenance_sha256 TEXT,
  content_kind TEXT NOT NULL CHECK(content_kind IN ('speech','audible_event','uncertain')),
  source_review_status TEXT NOT NULL,
  confidence REAL,
  confidence_method TEXT NOT NULL,
  imported_at TEXT NOT NULL,
  PRIMARY KEY(source_id,profile_name,profile_version,segment_id)
);
CREATE TABLE IF NOT EXISTS analysis_records (
  record_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  profile_name TEXT NOT NULL,
  profile_version TEXT NOT NULL,
  segment_id TEXT NOT NULL,
  content_kind TEXT NOT NULL,
  message_kind TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(source_id,profile_name,profile_version,segment_id)
    REFERENCES transcript_segments(source_id,profile_name,profile_version,segment_id)
);
CREATE TABLE IF NOT EXISTS connection_candidates (
  candidate_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  call_id INTEGER NOT NULL,
  profile_name TEXT NOT NULL,
  profile_version TEXT NOT NULL,
  segment_id TEXT NOT NULL,
  source_entity_id TEXT NOT NULL,
  target_entity_id TEXT NOT NULL,
  source_identifier TEXT NOT NULL,
  target_identifier TEXT NOT NULL,
  relationship TEXT NOT NULL CHECK(relationship='RADIO_CONTACT'),
  extraction_pattern TEXT NOT NULL,
  review_state TEXT NOT NULL DEFAULT 'pending'
    CHECK(review_state IN ('pending','accepted','rejected')),
  reviewer TEXT,
  analyst_note TEXT,
  created_at TEXT NOT NULL,
  reviewed_at TEXT,
  FOREIGN KEY(source_id,profile_name,profile_version,segment_id)
    REFERENCES transcript_segments(source_id,profile_name,profile_version,segment_id)
);
CREATE INDEX IF NOT EXISTS idx_candidates_review
  ON connection_candidates(review_state,source_id);
CREATE TABLE IF NOT EXISTS audible_events (
  source_id TEXT NOT NULL,
  profile_name TEXT NOT NULL,
  profile_version TEXT NOT NULL,
  segment_id TEXT NOT NULL,
  text TEXT NOT NULL,
  start_ms INTEGER NOT NULL,
  end_ms INTEGER NOT NULL,
  source_review_status TEXT NOT NULL,
  PRIMARY KEY(source_id,profile_name,profile_version,segment_id),
  FOREIGN KEY(source_id,profile_name,profile_version,segment_id)
    REFERENCES transcript_segments(source_id,profile_name,profile_version,segment_id)
);
CREATE VIEW IF NOT EXISTS arch_notebook_connections AS
SELECT c.candidate_id AS connection_id,
       c.source_identifier AS source,
       c.target_identifier AS target,
       c.source_entity_id,
       c.target_entity_id,
       'RADIO_CONTACT' AS relationship,
       COALESCE(s.occurred_at, '') AS date,
       1 AS confidence,
       1 AS directed,
       'TRANS:' || c.source_id || '/' || c.profile_name || '/' ||
         c.profile_version || '/' || c.segment_id AS source_reference,
       'REVIEWED RADIO CONTACT CANDIDATE; extraction=' || c.extraction_pattern ||
         '; TRANS review=' || s.source_review_status ||
         '; TRANS segment SHA-256=' || s.text_sha256 ||
         CASE WHEN c.analyst_note IS NULL OR c.analyst_note='' THEN ''
              ELSE '; analyst note=' || c.analyst_note END AS notes,
       'RADIO_IDENTIFIER' AS source_type,
       'RADIO_IDENTIFIER' AS target_type
FROM connection_candidates c
JOIN transcript_segments s USING(source_id,profile_name,profile_version,segment_id)
WHERE c.review_state='accepted'
  AND s.source_review_status IN ('accepted','corrected')
  AND s.content_kind='speech';
"""

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _occurred_at(started_at: str | None, start_ms: int) -> str | None:
    if not started_at:
        return None
    try:
        value = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        if value.tzinfo is None:
            return None
        return (value + timedelta(milliseconds=start_ms)).isoformat()
    except ValueError:
        return None


def open_store(path: Path) -> sqlite3.Connection:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError("analysis_database_must_be_private_regular_file")
    else:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.close(fd)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    db.commit()
    return db


def _digest_id(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _radio_entity_id(call_id: int, identifier: str) -> str:
    """Scope a radio label to its TRANS call; never merge across calls automatically."""
    return _digest_id("radio-identifier/v1", str(call_id), identifier)


def _read_trans_db(path: Path) -> tuple[sqlite3.Connection, set[str], set[str]]:
    resolved = path.expanduser().resolve(strict=True)
    source = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    source.execute("PRAGMA query_only=ON")
    tables = {row[0] for row in source.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    needed = {"audio_sources", "audio_transcripts", "audio_transcript_segments"}
    if not needed.issubset(tables):
        source.close()
        raise ValueError("input_is_not_a_trans_audio_sql_database")
    transcript_cols = {r[1] for r in source.execute("PRAGMA table_info(audio_transcripts)")}
    source_cols = {r[1] for r in source.execute("PRAGMA table_info(audio_sources)")}
    segment_cols = {r[1] for r in source.execute(
        "PRAGMA table_info(audio_transcript_segments)")}
    required_source = {"source_id", "call_id", "started_at"}
    required_t = {"source_id", "profile_name", "profile_version", "transcript_sha256",
                  "review_status", "provenance_sha256"}
    required_s = {"source_id", "profile_name", "profile_version", "sequence",
                  "start_ms", "end_ms", "text", "text_sha256"}
    if (not required_source.issubset(source_cols) or
        not required_t.issubset(transcript_cols) or
        not required_s.issubset(segment_cols)):
        source.close()
        raise ValueError("trans_database_schema_missing_required_fields")
    return source, transcript_cols, segment_cols


def import_trans(db: sqlite3.Connection, trans_path: Path) -> dict[str, int]:
    source, transcript_cols, segment_cols = _read_trans_db(trans_path)
    counts = {
        "imported": 0,
        "unchanged": 0,
        "speech": 0,
        "audible_events": 0,
        "uncertain": 0,
        "source_pending_segments": 0,
    }
    content_sql = "g.content_kind" if "content_kind" in segment_cols else "'uncertain'"
    confidence_sql = "g.confidence" if "confidence" in segment_cols else "NULL"
    confidence_method_sql = (
        "g.confidence_method" if "confidence_method" in segment_cols else "'unavailable'"
    )
    try:
        records = source.execute(f"""
          SELECT s.source_id,s.call_id,t.profile_name,t.profile_version,
                 g.sequence,g.start_ms,g.end_ms,s.started_at,g.text,g.text_sha256,
                 t.transcript_sha256,t.provenance_sha256,{content_sql} AS content_kind,
                 t.review_status,{confidence_sql} AS confidence,
                 {confidence_method_sql} AS confidence_method
          FROM audio_transcript_segments g
          JOIN audio_transcripts t USING(source_id,profile_name,profile_version)
          JOIN audio_sources s USING(source_id)
          ORDER BY s.source_id,t.profile_name,t.profile_version,g.sequence
        """)
        for row in records:
            text = row["text"]
            segment_sha = row["text_sha256"]
            if not isinstance(segment_sha, str) or not _SHA256.fullmatch(segment_sha):
                raise ValueError("trans_segment_hash_missing_or_invalid")
            kind = row["content_kind"]
            if kind not in {"speech", "audible_event", "uncertain"}:
                raise ValueError("trans_content_kind_unknown")
            source_review = row["review_status"]
            if source_review not in {"pending", "accepted", "corrected", "rejected"}:
                raise ValueError("trans_review_state_unknown")
            counts[kind] += 1
            counts["source_pending_segments"] += source_review == "pending"
            profile = (row["profile_name"], row["profile_version"])
            segment_id = str(row["sequence"])
            key = (row["source_id"], *profile, segment_id)
            existing = db.execute(
                "SELECT text_sha256,content_kind,source_review_status FROM transcript_segments "
                "WHERE source_id=? AND profile_name=? AND profile_version=? AND segment_id=?", key
            ).fetchone()
            if existing:
                if existing["text_sha256"] != segment_sha:
                    raise ValueError("trans_segment_changed_without_profile_version_change")
                if (existing["content_kind"] != kind or
                    existing["source_review_status"] != source_review):
                    db.execute("""UPDATE transcript_segments
                      SET content_kind=?,source_review_status=?
                      WHERE source_id=? AND profile_name=? AND profile_version=? AND segment_id=?""",
                      (kind, source_review, *key))
                    db.execute("""UPDATE analysis_records SET content_kind=?,message_kind=?
                      WHERE record_id=?""",
                      (kind, classify_message(text) if kind == "speech" else kind,
                       _digest_id(*key)))
                    if kind == "audible_event":
                        db.execute("""INSERT OR REPLACE INTO audible_events
                          (source_id,profile_name,profile_version,segment_id,text,start_ms,end_ms,
                           source_review_status) VALUES(?,?,?,?,?,?,?,?)""",
                          (*key[:1], *key[1:3], key[3], text, row["start_ms"],
                           row["end_ms"], source_review))
                    else:
                        db.execute("DELETE FROM audible_events WHERE source_id=? AND profile_name=? "
                                   "AND profile_version=? AND segment_id=?", key)
                counts["unchanged"] += 1
                continue
            now = _utc_now()
            db.execute("""INSERT INTO transcript_segments
              (source_id,call_id,profile_name,profile_version,segment_id,start_ms,end_ms,
               started_at,occurred_at,text,text_sha256,transcript_sha256,provenance_sha256,
               content_kind,source_review_status,confidence,confidence_method,imported_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (row["source_id"], row["call_id"], *profile, segment_id,
               row["start_ms"], row["end_ms"], row["started_at"],
               _occurred_at(row["started_at"], row["start_ms"]), text, segment_sha,
               row["transcript_sha256"], row["provenance_sha256"], kind, source_review,
               row["confidence"], row["confidence_method"], now))
            record_id = _digest_id(*key)
            db.execute("""INSERT INTO analysis_records
              (record_id,source_id,profile_name,profile_version,segment_id,content_kind,
               message_kind,created_at) VALUES(?,?,?,?,?,?,?,?)""",
              (record_id, *key[:1], *key[1:3], key[3], kind,
               classify_message(text) if kind == "speech" else kind, now))
            if kind == "audible_event":
                db.execute("""INSERT INTO audible_events
                  (source_id,profile_name,profile_version,segment_id,text,start_ms,end_ms,
                   source_review_status) VALUES(?,?,?,?,?,?,?,?)""",
                  (row["source_id"], *profile, segment_id, text, row["start_ms"],
                   row["end_ms"], source_review))
            counts["imported"] += 1
        db.commit()
        return counts
    except Exception:
        db.rollback()
        raise
    finally:
        source.close()


def analyze_pending(db: sqlite3.Connection) -> dict[str, int]:
    """Derive deterministic candidates only from TRANS-labeled speech rows."""
    created = 0
    records = db.execute("""SELECT s.* FROM transcript_segments s
       WHERE s.content_kind='speech' AND NOT EXISTS (
         SELECT 1 FROM connection_candidates c WHERE c.source_id=s.source_id
          AND c.profile_name=s.profile_name AND c.profile_version=s.profile_version
          AND c.segment_id=s.segment_id)
       ORDER BY s.imported_at""").fetchall()
    for row in records:
        contact = radio_contact(row["text"])
        if not contact:
            continue
        origin, addressee, pattern = contact
        key = (row["source_id"], row["profile_name"], row["profile_version"], row["segment_id"])
        candidate_id = _digest_id(*key, origin, addressee, pattern)
        db.execute("""INSERT OR IGNORE INTO connection_candidates
          (candidate_id,source_id,call_id,profile_name,profile_version,segment_id,
           source_entity_id,target_entity_id,source_identifier,target_identifier,
           relationship,extraction_pattern,review_state,created_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,'RADIO_CONTACT',?,'pending',?)""",
          (candidate_id, row["source_id"], row["call_id"], row["profile_name"],
           row["profile_version"], row["segment_id"],
           _radio_entity_id(row["call_id"], origin),
           _radio_entity_id(row["call_id"], addressee), origin, addressee,
           pattern, _utc_now()))
        created += 1
    db.commit()
    return {"created": created}


def review_candidate(db: sqlite3.Connection, candidate_id: str, state: str,
                     reviewer: str, note: str = "") -> None:
    if state not in {"accepted", "rejected"}:
        raise ValueError("review_state_must_be_accepted_or_rejected")
    if not reviewer.strip():
        raise ValueError("reviewer_required")
    candidate = db.execute("""SELECT s.source_review_status
       FROM connection_candidates c JOIN transcript_segments s
       USING(source_id,profile_name,profile_version,segment_id)
       WHERE c.candidate_id=?""", (candidate_id,)).fetchone()
    if candidate is None:
        raise ValueError("candidate_not_found")
    if state == "accepted" and candidate["source_review_status"] not in {"accepted", "corrected"}:
        raise ValueError("trans_segment_not_accepted_by_trans")
    db.execute("""UPDATE connection_candidates
       SET review_state=?,reviewer=?,analyst_note=?,reviewed_at=? WHERE candidate_id=?""",
       (state, reviewer.strip(), note.strip(), _utc_now(), candidate_id))
    db.commit()


def connection_counts(db: sqlite3.Connection) -> dict[str, int]:
    return {row[0]: row[1] for row in db.execute(
        "SELECT review_state,count(*) FROM connection_candidates GROUP BY review_state")}


def list_candidates(db: sqlite3.Connection, state: str, limit: int) -> list[dict[str, Any]]:
    rows = db.execute("""SELECT c.candidate_id,c.source_identifier,c.target_identifier,
      c.source_entity_id,c.target_entity_id,
      c.relationship,c.extraction_pattern,c.review_state,c.reviewer,c.analyst_note,
      s.source_id,s.segment_id,s.start_ms,s.occurred_at,s.text,s.source_review_status,
      s.text_sha256,s.confidence,s.confidence_method
      FROM connection_candidates c JOIN transcript_segments s
      USING(source_id,profile_name,profile_version,segment_id)
      WHERE c.review_state=? ORDER BY s.occurred_at,s.source_id,s.start_ms LIMIT ?""",
      (state, limit)).fetchall()
    return [dict(row) for row in rows]


def list_events(db: sqlite3.Connection, limit: int) -> list[dict[str, Any]]:
    rows = db.execute("""SELECT source_id,profile_name,profile_version,segment_id,
      start_ms,end_ms,text,source_review_status
      FROM audible_events ORDER BY source_id,profile_name,profile_version,start_ms LIMIT ?""",
      (limit,)).fetchall()
    return [dict(row) for row in rows]
