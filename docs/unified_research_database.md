# Unified Research database plan

## Decision

Maintain one active SQLite artifact on the Research volume:

`Research.Databases/research.sqlite`

It will contain TRANS-owned `audio_*` source, transcript, segment, and review
tables together with Traffic Analysis-owned import, neural proposal, failure,
candidate, and reporting tables. Raw audio remains in its existing audio
archive; the database retains source paths, hashes, transcript text, timing,
and provenance references.

TRANS remains the only owner of audio processing and canonical transcript
content. Its live SQLite queue remains on tmpfs and its compressed recovery
checkpoint moves to private local storage. TRANS does not write the Research
database. Traffic Analysis is the sole publisher of the combined Research
snapshot: each cycle reads an atomic TRANS checkpoint, restores the existing
combined snapshot to private local storage, synchronizes immutable TRANS
tables, imports text read-only into its derived tables, classifies speech, and
atomically replaces the Research snapshot. This single-writer arrangement
prevents stale TRANS checkpoints from overwriting newer analysis rows.

## Migration and rollback

The migration starts from the verified existing Traffic Analysis snapshot and
adds the current TRANS tables, data, indexes, and append-only review triggers.
It preserves all neural proposals, failure records, candidates, review states,
and existing transcript projections. Conflicting source keys or schema
versions fail closed. The old TRANS checkpoint and Traffic Analysis snapshot
remain available off the Research volume until the combined database passes
integrity, row-count, key, digest, permissions, and view checks. Only then may
the legacy files be moved into private local rollback storage.

No migration step may accept a transcript or neural proposal. New reviews
continue through the TRANS and analyst review ledgers. Existing source and
analysis decisions must match their bound content hashes after the migration.

After the user-session TRANS helper switches to the local checkpoint and
systemd confirms it loaded the new TRANS and Traffic Analysis units, run
`python3 tools/retire_legacy_research_databases.py` from this repository. It
stops TRANS for a stable checkpoint, acquires the TRANS and Traffic Analysis
locks, verifies that the combined DB still contains the source rows and
analysis review tables, writes owner-only exact rollback copies, retires the
two split Research files, and restarts TRANS. It refuses retirement if the
user systemd manager is inaccessible, units need reload, or either worker is
in an unsafe state.

## Acceptance criteria

1. Both source databases pass SQLite integrity checks before migration.
2. The combined database contains all TRANS audio tables and all Traffic
   Analysis tables, indexes, and required triggers.
3. Every TRANS primary key and row digest matches its source checkpoint.
4. Every analysis, failure, candidate, and review primary key and row digest
   matches the prior Traffic Analysis snapshot.
5. Transcript and proposal review states are unchanged; no approval rows are
   synthesized.
6. Arch Notebook views remain gated by current TRANS review and analyst
   acceptance, and suppress superseded neural proposals.
7. The combined database passes `PRAGMA integrity_check` and foreign-key
   checks, is owner-only, and is published atomically to the expected writable
   CIFS source.
8. After the service configuration is migrated, only
   `Research.Databases/research.sqlite` is an active project database on the
   Research volume. Legacy files are retained as verified local rollback
   copies until their owner-authorized retention period expires.

## Milestones

| Milestone | State | Evidence needed |
|---|---|---|
| RDB1 — Combined schema and checkpoint sync | Candidate implemented | TRANS-owned source, transcript, segment, and append-only review tables are synchronized by key; same-key content/schema conflicts fail closed. Still requires corpus verification |
| RDB2 — Service paths and writer ownership | Candidate implemented | TRANS service/helper target a private local checkpoint; Traffic Analysis defaults to the combined Research path. Service install/restart has not been verified |
| RDB3 — Corpus migration | Combined snapshot published; retirement pending | Corpus migration synchronized 1,010 sources, 1,035 transcripts, and 3,409 segments; existing neural proposals/review rows were unchanged; integrity and foreign-key checks passed. The guarded retirement command is ready but could not run because this session cannot access the user systemd bus |
| RDB4 — Human review handoff | Pending analyst action | TRANS source decisions and neural proposal decisions entered through their review ledgers; approved SQL views checked afterward |

