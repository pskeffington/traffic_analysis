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
import unicodedata

from .patterns import (
    classify_call_types,
    classify_message,
    normalize_identifier,
    radio_contact,
)

SCHEMA = """
PRAGMA foreign_keys=ON;
DROP VIEW IF EXISTS arch_notebook_connections;
DROP VIEW IF EXISTS arch_notebook_classifications;
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
  call_type TEXT NOT NULL DEFAULT 'unclassified',
  created_at TEXT NOT NULL,
  FOREIGN KEY(source_id,profile_name,profile_version,segment_id)
    REFERENCES transcript_segments(source_id,profile_name,profile_version,segment_id)
);
CREATE TABLE IF NOT EXISTS neural_analyses (
  analysis_id TEXT PRIMARY KEY,
  record_id TEXT NOT NULL,
  model_id TEXT NOT NULL,
  model_manifest_sha256 TEXT NOT NULL DEFAULT 'unknown',
  engine_version TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  labels TEXT NOT NULL,
  summary TEXT NOT NULL,
  uncertainty TEXT NOT NULL CHECK(uncertainty IN ('low','medium','high')),
  output_json TEXT NOT NULL,
  output_sha256 TEXT NOT NULL,
  review_state TEXT NOT NULL DEFAULT 'pending'
    CHECK(review_state IN ('pending','accepted','rejected')),
  reviewer TEXT,
  analyst_note TEXT,
  created_at TEXT NOT NULL,
  reviewed_at TEXT,
  FOREIGN KEY(record_id) REFERENCES analysis_records(record_id)
);
CREATE INDEX IF NOT EXISTS idx_neural_review
  ON neural_analyses(review_state,model_id,created_at);
CREATE TABLE IF NOT EXISTS neural_failures (
  record_id TEXT NOT NULL,
  model_id TEXT NOT NULL,
  model_manifest_sha256 TEXT NOT NULL,
  engine_version TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  last_error_type TEXT NOT NULL,
  last_attempt_at TEXT NOT NULL,
  PRIMARY KEY(record_id,model_id,model_manifest_sha256,engine_version,prompt_version),
  FOREIGN KEY(record_id) REFERENCES analysis_records(record_id)
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
       n.labels AS "connection_attribute:call_types",
       n.summary AS "connection_attribute:triage_summary",
       n.model_id AS "connection_attribute:classification_model",
       n.uncertainty AS "connection_attribute:model_uncertainty",
       n.output_sha256 AS "connection_attribute:classification_sha256",
       'RADIO_IDENTIFIER' AS source_type,
       'RADIO_IDENTIFIER' AS target_type
FROM connection_candidates c
JOIN transcript_segments s USING(source_id,profile_name,profile_version,segment_id)
LEFT JOIN analysis_records a USING(source_id,profile_name,profile_version,segment_id)
LEFT JOIN neural_analyses n ON n.record_id=a.record_id
  AND n.analysis_id=(SELECT n2.analysis_id FROM neural_analyses n2
    WHERE n2.record_id=a.record_id
    ORDER BY n2.created_at DESC,n2.analysis_id DESC LIMIT 1)
  AND n.review_state='accepted'
  AND NOT EXISTS (SELECT 1 FROM neural_failures f
    WHERE f.record_id=n.record_id AND f.last_attempt_at>n.created_at)
WHERE c.review_state='accepted'
  AND s.source_review_status IN ('accepted','corrected')
  AND s.content_kind='speech';
CREATE VIEW IF NOT EXISTS arch_notebook_classifications AS
SELECT n.analysis_id AS classification_id,
       n.labels AS call_types,
       n.summary AS summary,
       n.uncertainty AS model_uncertainty,
       s.occurred_at AS date,
       s.text AS source_text,
       s.text_sha256 AS source_text_sha256,
       'TRANS:' || s.source_id || '/' || s.profile_name || '/' ||
         s.profile_version || '/' || s.segment_id AS source_reference,
       n.model_id,
       n.model_manifest_sha256,
       n.engine_version,
       n.prompt_version,
       n.output_sha256 AS analysis_sha256
FROM neural_analyses n
JOIN analysis_records a USING(record_id)
JOIN transcript_segments s USING(source_id,profile_name,profile_version,segment_id)
WHERE n.review_state='accepted'
  AND n.analysis_id=(SELECT n2.analysis_id FROM neural_analyses n2
    WHERE n2.record_id=n.record_id
    ORDER BY n2.created_at DESC,n2.analysis_id DESC LIMIT 1)
  AND NOT EXISTS (SELECT 1 FROM neural_failures f
    WHERE f.record_id=n.record_id AND f.last_attempt_at>n.created_at)
  AND s.content_kind='speech'
  AND s.source_review_status IN ('accepted','corrected');
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
    # Upgrade this legacy column before SCHEMA rebuilds views that reference it.
    existing_tables = {row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "neural_analyses" in existing_tables:
        neural_columns = {row[1] for row in db.execute(
            "PRAGMA table_info(neural_analyses)")}
        if "model_manifest_sha256" not in neural_columns:
            db.execute("ALTER TABLE neural_analyses ADD COLUMN model_manifest_sha256 TEXT "
                       "NOT NULL DEFAULT 'unknown'")
    db.executescript(SCHEMA)
    analysis_columns = {row[1] for row in db.execute("PRAGMA table_info(analysis_records)")}
    if "call_type" not in analysis_columns:
        db.execute(
            "ALTER TABLE analysis_records ADD COLUMN call_type TEXT NOT NULL DEFAULT 'unclassified'"
        )
    db.commit()
    return db


def _digest_id(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _radio_entity_id(call_id: int, identifier: str) -> str:
    """Scope a radio label to its TRANS call; never merge across calls automatically."""
    return _digest_id("radio-identifier/v1", str(call_id), identifier)


def _transcript_hash(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _current_trans_review_statuses(source: sqlite3.Connection,
                                   segment_cols: set[str]) -> dict[tuple[str, str, str], str]:
    tables = {row[0] for row in source.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "audio_transcript_reviews" not in tables:
        return {}
    review_cols = {row[1] for row in source.execute(
        "PRAGMA table_info(audio_transcript_reviews)")}
    required = {"source_id", "profile_name", "profile_version", "revision",
                "transcript_sha256", "review_sha256", "review_status"}
    if not required.issubset(review_cols) or "content_kind" not in segment_cols:
        raise ValueError("trans_review_ledger_schema_missing_required_fields")
    rows = source.execute("""SELECT t.source_id,t.profile_name,t.profile_version,
        t.text,t.transcript_sha256,t.call_id,s.call_id AS source_call_id,
        s.started_at,s.sha256 AS audio_sha256,r.review_sha256,r.review_status
        FROM audio_transcripts t JOIN audio_transcript_reviews r
          USING(source_id,profile_name,profile_version)
        JOIN audio_sources s USING(source_id)
        WHERE r.revision=(SELECT max(r2.revision) FROM audio_transcript_reviews r2
          WHERE r2.source_id=t.source_id AND r2.profile_name=t.profile_name
            AND r2.profile_version=t.profile_version)
        ORDER BY t.source_id,t.profile_name,t.profile_version""").fetchall()
    statuses: dict[tuple[str, str, str], str] = {}
    for row in rows:
        if row["review_status"] not in {"accepted", "rejected"}:
            raise ValueError("trans_review_state_unknown")
        if _transcript_hash(row["text"]) != row["transcript_sha256"]:
            raise ValueError("transcript_text_hash_mismatch")
        if row["call_id"] != row["source_call_id"]:
            raise ValueError("transcript_source_call_id_mismatch")
        segments = source.execute("""SELECT sequence,start_ms,end_ms,text,text_sha256,content_kind
            FROM audio_transcript_segments WHERE source_id=? AND profile_name=?
              AND profile_version=? ORDER BY sequence""",
            (row["source_id"], row["profile_name"], row["profile_version"])).fetchall()
        material = []
        for segment in segments:
            if _transcript_hash(segment["text"]) != segment["text_sha256"]:
                raise ValueError("trans_segment_hash_mismatch")
            if segment["content_kind"] not in {"speech", "audible_event", "uncertain"}:
                raise ValueError("trans_content_kind_unknown")
            material.append({"sequence": segment["sequence"],
                             "start_ms": segment["start_ms"],
                             "end_ms": segment["end_ms"],
                             "text_sha256": segment["text_sha256"],
                             "content_kind": segment["content_kind"]})
        payload = {"contract": "trans-audio-transcript-review/v1",
                   "source_id": row["source_id"],
                   "call_id": row["source_call_id"],
                   "started_at": row["started_at"],
                   "audio_sha256": row["audio_sha256"],
                   "transcript_sha256": row["transcript_sha256"],
                   "segments": material}
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")).encode("utf-8")
        review_sha = hashlib.sha256(canonical).hexdigest()
        if review_sha == row["review_sha256"]:
            statuses[(row["source_id"], row["profile_name"],
                      row["profile_version"])] = row["review_status"]
    return statuses


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
    review_statuses: dict[tuple[str, str, str], str] = {}
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
        review_statuses = _current_trans_review_statuses(source, segment_cols)
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
            source_review = review_statuses.get(
                (row["source_id"], row["profile_name"], row["profile_version"]),
                row["review_status"],
            )
            if source_review not in {"pending", "accepted", "corrected", "rejected"}:
                raise ValueError("trans_review_state_unknown")
            counts["audible_events" if kind == "audible_event" else kind] += 1
            counts["source_pending_segments"] += source_review == "pending"
            profile = (row["profile_name"], row["profile_version"])
            segment_id = str(row["sequence"])
            key = (row["source_id"], *profile, segment_id)
            message_kind = classify_message(text) if kind == "speech" else kind
            call_type = classify_call_types(text) if kind == "speech" else "not_applicable"
            existing = db.execute(
                "SELECT call_id,started_at,start_ms,end_ms,text_sha256,transcript_sha256,"
                "provenance_sha256,content_kind,source_review_status,confidence,"
                "confidence_method FROM transcript_segments "
                "WHERE source_id=? AND profile_name=? AND profile_version=? AND segment_id=?", key
            ).fetchone()
            if existing:
                if (existing["call_id"] != row["call_id"] or
                    existing["started_at"] != row["started_at"] or
                    existing["start_ms"] != row["start_ms"] or
                    existing["end_ms"] != row["end_ms"] or
                    existing["confidence"] != row["confidence"] or
                    existing["confidence_method"] != row["confidence_method"]):
                    raise ValueError("trans_source_metadata_changed_without_profile_version_change")
                if existing["text_sha256"] != segment_sha:
                    raise ValueError("trans_segment_changed_without_profile_version_change")
                if (existing["transcript_sha256"] != row["transcript_sha256"] or
                    existing["provenance_sha256"] != row["provenance_sha256"]):
                    raise ValueError("trans_provenance_changed_without_profile_version_change")
                if (existing["content_kind"] != kind or
                    existing["source_review_status"] != source_review):
                    db.execute("""UPDATE transcript_segments
                      SET content_kind=?,source_review_status=?
                      WHERE source_id=? AND profile_name=? AND profile_version=? AND segment_id=?""",
                      (kind, source_review, *key))
                    if kind == "audible_event":
                        db.execute("""INSERT OR REPLACE INTO audible_events
                          (source_id,profile_name,profile_version,segment_id,text,start_ms,end_ms,
                           source_review_status) VALUES(?,?,?,?,?,?,?,?)""",
                          (*key[:1], *key[1:3], key[3], text, row["start_ms"],
                           row["end_ms"], source_review))
                    else:
                        db.execute("DELETE FROM audible_events WHERE source_id=? AND profile_name=? "
                                   "AND profile_version=? AND segment_id=?", key)
                db.execute("""UPDATE analysis_records
                   SET content_kind=?,message_kind=?,call_type=? WHERE record_id=?""",
                   (kind, message_kind, call_type, _digest_id(*key)))
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
               message_kind,call_type,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
              (record_id, *key[:1], *key[1:3], key[3], kind,
               message_kind, call_type, now))
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


def analyze_neural_batch(db: sqlite3.Connection, socket_path: Path, model_id: str,
                         model_manifest_sha256: str, classify: Any,
                         engine_version: str, prompt_version: str,
                         limit: int, retry_failures: bool = False) -> dict[str, int]:
    """Store validated local model proposals and only evidence-verified radio contacts."""
    if not 1 <= limit <= 1000:
        raise ValueError("limit_must_be_between_1_and_1000")
    counts: dict[str, Any] = {
        "analyzed": 0, "failed": 0, "contact_candidates": 0,
        "failure_types": {}, "blocked_failures": 0,
        "blocked_failure_types": {},
    }

    def blocked_failure_summary() -> dict[str, int]:
        rows = db.execute("""SELECT f.last_error_type,count(*)
          FROM neural_failures f
          JOIN analysis_records a USING(record_id)
          JOIN transcript_segments s USING(source_id,profile_name,profile_version,segment_id)
          WHERE s.content_kind='speech' AND f.model_id=?
            AND f.model_manifest_sha256=? AND f.engine_version=?
            AND f.prompt_version=? AND f.attempt_count>=2
            AND NOT EXISTS (
              SELECT 1 FROM neural_analyses n WHERE n.record_id=f.record_id
                AND n.model_id=f.model_id
                AND n.model_manifest_sha256=f.model_manifest_sha256
                AND n.engine_version=f.engine_version
                AND n.prompt_version=f.prompt_version)
          GROUP BY f.last_error_type""",
          (model_id, model_manifest_sha256, engine_version, prompt_version)).fetchall()
        return {row[0]: row[1] for row in rows}

    if not retry_failures:
        prior_analyses = db.execute("""SELECT COUNT(*) FROM neural_analyses
          WHERE model_id=? AND model_manifest_sha256=? AND engine_version=?
            AND prompt_version=?""",
          (model_id, model_manifest_sha256, engine_version, prompt_version)).fetchone()[0]
        failure_count, max_attempt = db.execute("""SELECT COUNT(*),MAX(attempt_count)
          FROM neural_failures WHERE model_id=? AND model_manifest_sha256=?
            AND engine_version=? AND prompt_version=?""",
          (model_id, model_manifest_sha256, engine_version, prompt_version)).fetchone()
        if prior_analyses == 0:
            if failure_count and max_attempt >= 2:
                counts["blocked_failure_types"] = blocked_failure_summary()
                counts["blocked_failures"] = sum(
                    counts["blocked_failure_types"].values()
                )
                counts["paused"] = 1
                return counts
            if failure_count:
                # Retry only one failed segment after a failed initial probe.
                retry_failures = True
                limit = 1
                counts["diagnostic_retry"] = 1
            else:
                # Prove this model/configuration can emit a valid proposal
                # before allowing a scheduled production batch.
                limit = 1
    records = db.execute("""SELECT s.*,a.record_id,a.message_kind
      FROM transcript_segments s JOIN analysis_records a
      USING(source_id,profile_name,profile_version,segment_id)
      WHERE s.content_kind='speech' AND NOT EXISTS (
        SELECT 1 FROM neural_analyses n WHERE n.record_id=a.record_id
          AND n.model_id=? AND n.model_manifest_sha256=?
          AND n.engine_version=? AND n.prompt_version=?)
        AND (? OR NOT EXISTS (
          SELECT 1 FROM neural_failures f WHERE f.record_id=a.record_id
            AND f.model_id=? AND f.model_manifest_sha256=?
            AND f.engine_version=? AND f.prompt_version=?
            AND f.attempt_count>=2))
      ORDER BY s.occurred_at,s.source_id,s.start_ms LIMIT ?""",
      (model_id, model_manifest_sha256, engine_version, prompt_version,
       int(retry_failures), model_id, model_manifest_sha256, engine_version,
       prompt_version, limit)).fetchall()
    if not records:
        # Distinguish a fully processed queue from speech segments suppressed
        # after repeated malformed or unsupported model output. Keep the
        # diagnostic aggregate free of source text and do not retry implicitly.
        counts["blocked_failure_types"] = blocked_failure_summary()
        counts["blocked_failures"] = sum(counts["blocked_failure_types"].values())
    from .neural import validate_analysis

    try:
        for row in records:
            try:
                result = classify(socket_path, model_id, row["text"], row["message_kind"])
                result = validate_analysis(result, row["text"])
            except (OSError, ValueError, RuntimeError) as exc:
                counts["failed"] += 1
                exception_type = type(exc).__name__
                reason = str(exc)
                error_code = (f"{exception_type}:{reason}"
                              if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:=-]{0,125}", reason)
                              else exception_type)
                failure_types = counts["failure_types"]
                failure_types[error_code] = failure_types.get(error_code, 0) + 1
                db.execute("""INSERT INTO neural_failures
                  (record_id,model_id,model_manifest_sha256,engine_version,prompt_version,
                   attempt_count,last_error_type,last_attempt_at)
                  VALUES(?,?,?,?,?,1,?,?)
                  ON CONFLICT(record_id,model_id,model_manifest_sha256,engine_version,
                              prompt_version)
                  DO UPDATE SET attempt_count=attempt_count+1,
                    last_error_type=excluded.last_error_type,
                    last_attempt_at=excluded.last_attempt_at""",
                  (row["record_id"], model_id, model_manifest_sha256, engine_version,
                   prompt_version, error_code, _utc_now()))
                db.commit()
                continue
            key = (row["source_id"], row["profile_name"], row["profile_version"],
                   row["segment_id"])
            record_id = row["record_id"]
            analysis_id = _digest_id("neural-analysis/v1", record_id, model_id,
                                     model_manifest_sha256, engine_version, prompt_version)
            output_json = json.dumps(result, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"))
            output_sha = hashlib.sha256(output_json.encode("utf-8")).hexdigest()
            labels = ";".join(item["label"] for item in result["labels"]) or "unclassified"
            db.execute("""INSERT OR IGNORE INTO neural_analyses
              (analysis_id,record_id,model_id,model_manifest_sha256,engine_version,
               prompt_version,labels,
               summary,uncertainty,output_json,output_sha256,created_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
              (analysis_id, record_id, model_id, model_manifest_sha256, engine_version,
               prompt_version,
               labels, result["summary"], result["uncertainty"], output_json,
               output_sha, _utc_now()))
            db.execute("""DELETE FROM neural_failures WHERE record_id=? AND model_id=?
              AND model_manifest_sha256=? AND engine_version=? AND prompt_version=?""",
              (record_id, model_id, model_manifest_sha256, engine_version, prompt_version))
            for item in result["contacts"]:
                explicit = radio_contact(item["evidence"])
                if not explicit:
                    continue
                origin, addressee, _ = explicit
                if (normalize_identifier(item["origin"]) != origin or
                    normalize_identifier(item["addressee"]) != addressee):
                    continue
                candidate_id = _digest_id("neural-radio-contact/v1", *key, origin,
                                          addressee, item["evidence"])
                cursor = db.execute("""INSERT OR IGNORE INTO connection_candidates
                  (candidate_id,source_id,call_id,profile_name,profile_version,segment_id,
                   source_entity_id,target_entity_id,source_identifier,target_identifier,
                   relationship,extraction_pattern,review_state,created_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,'RADIO_CONTACT',?,'pending',?)""",
                  (candidate_id, row["source_id"], row["call_id"], row["profile_name"],
                   row["profile_version"], row["segment_id"],
                   _radio_entity_id(row["call_id"], origin),
                   _radio_entity_id(row["call_id"], addressee), origin, addressee,
                   "neural_explicit_contact", _utc_now()))
                counts["contact_candidates"] += cursor.rowcount
            counts["analyzed"] += 1
            db.commit()
    except Exception:
        db.rollback()
        raise
    return counts


def review_neural_analysis(db: sqlite3.Connection, analysis_id: str, state: str,
                           reviewer: str, note: str = "") -> None:
    if state not in {"accepted", "rejected"}:
        raise ValueError("review_state_must_be_accepted_or_rejected")
    if not reviewer.strip():
        raise ValueError("reviewer_required")
    row = db.execute("""SELECT s.source_review_status,s.content_kind,n.record_id,
      n.created_at FROM neural_analyses n
      JOIN analysis_records a USING(record_id)
      JOIN transcript_segments s USING(source_id,profile_name,profile_version,segment_id)
      WHERE n.analysis_id=?""", (analysis_id,)).fetchone()
    if row is None:
        raise ValueError("neural_analysis_not_found")
    if state == "accepted" and row["content_kind"] != "speech":
        raise ValueError("trans_segment_not_currently_classified_as_speech")
    if state == "accepted" and row["source_review_status"] not in {"accepted", "corrected"}:
        raise ValueError("trans_segment_not_accepted_by_trans")
    if state == "accepted":
        newer = db.execute("""SELECT 1 FROM neural_analyses
          WHERE record_id=? AND (created_at>? OR
            (created_at=? AND analysis_id>?)) LIMIT 1""",
          (row["record_id"], row["created_at"], row["created_at"], analysis_id)).fetchone()
        if newer:
            raise ValueError("neural_analysis_superseded")
        failed_after = db.execute("""SELECT 1 FROM neural_failures
          WHERE record_id=? AND last_attempt_at>? LIMIT 1""",
          (row["record_id"], row["created_at"])).fetchone()
        if failed_after:
            raise ValueError("newer_neural_attempt_failed")
    db.execute("""UPDATE neural_analyses SET review_state=?,reviewer=?,analyst_note=?,
      reviewed_at=? WHERE analysis_id=?""",
      (state, reviewer.strip(), note.strip(), _utc_now(), analysis_id))
    db.commit()


def list_neural_analyses(db: sqlite3.Connection, state: str,
                         limit: int) -> list[dict[str, Any]]:
    if state not in {"pending", "accepted", "rejected"}:
        raise ValueError("invalid_neural_review_state")
    if not 1 <= limit <= 1000:
        raise ValueError("limit_must_be_between_1_and_1000")
    rows = db.execute("""SELECT n.analysis_id,n.model_id,n.model_manifest_sha256,
      n.engine_version,
      n.prompt_version,n.labels,n.summary,n.uncertainty,n.output_json,n.review_state,
      s.source_id,s.segment_id,s.start_ms,s.occurred_at,s.text,s.text_sha256,
      s.source_review_status
      FROM neural_analyses n JOIN analysis_records a USING(record_id)
      JOIN transcript_segments s USING(source_id,profile_name,profile_version,segment_id)
      WHERE n.review_state=?
        AND n.analysis_id=(SELECT n2.analysis_id FROM neural_analyses n2
          WHERE n2.record_id=n.record_id
          ORDER BY n2.created_at DESC,n2.analysis_id DESC LIMIT 1)
        AND NOT EXISTS (SELECT 1 FROM neural_failures f
          WHERE f.record_id=n.record_id AND f.last_attempt_at>n.created_at)
      ORDER BY n.created_at DESC,s.source_id,s.start_ms LIMIT ?""",
      (state, limit)).fetchall()
    return [dict(row) for row in rows]


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


def call_type_counts(db: sqlite3.Connection) -> dict[str, int]:
    """Count speech call-type labels; a segment can contribute to several labels."""
    counts: dict[str, int] = {}
    for (value,) in db.execute(
        "SELECT call_type FROM analysis_records WHERE content_kind='speech'"
    ):
        for label in value.split(";"):
            counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items()))


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
