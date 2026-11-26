# Production plan

This document describes release gates without recording source transcript content, corpus identifiers, or private machine paths. Live corpus observations remain in local operator records, not Git.

## Scope and ownership

- TRANS owns audio processing, canonical transcript text and hashes, event classification, and source review state.
- Traffic Analysis imports only TRANS SQLite text rows in read-only mode and derives conservative, review-pending radio-contact candidates.
- Arch Notebook reads an approved SQL projection and captures immutable snapshots for regression review.
- No stage infers real-world identity, treats co-occurrence as a connection, or promotes unreviewed text.

## Milestones

| Milestone | Acceptance criteria | Status |
|---|---|---|
| M0 — Local analysis package | Standard-library runtime; read-only TRANS import; provenance and timestamps retained; explicit pattern extraction; event and candidate review commands; Arch Notebook SQL view | Implemented prototype |
| M1 — TRANS review contract | Versioned speech/event/uncertain kinds; human review bound to source identity, transcript and all segment hashes/times/kinds; changed evidence invalidates stale decisions; resumable migration and rollback | In progress: append-only hash-bound accept/reject ledger and downstream digest validation are implemented in local worktrees; corrections are not yet supported, and the change is not merged or runtime-verified |
| M2 — Corpus operation | Repeatable private import; source integrity preserved; one persistent analysis database on the Research volume; aggregate status reporting; no corpus content in repository | PASS for the existing Traffic Analysis snapshot. TRANS was still stored separately at the last audit; unified storage is M8 and is not yet runtime-verified |
| M3 — Text call-type triage | Every speech segment gets overlapping deterministic labels; events/uncertain remain separate; aggregate counts available; labels do not imply graph edges | Keyword prototype implemented; unvalidated; see [literature matrix](literature_matrix.md) |
| M4 — Local neural triage | Optional local model proposes evidence-backed categories, station mentions, summary, and explicit radio contacts; outputs are versioned, pending, resumable, and review-gated | Runtime inference completed for prompt v4. Prompt v5 strengthens exact evidence copying after production records exposed unsupported-quote failures. V5 corpus inference remains to be verified; proposals remain pending and category quality and calibration are unvalidated. The current taxonomy is documented in the [annotation guide](call_type_annotation.md). Live counts remain in private operator records. |
| M5 — Arch Notebook regression | Approved classifications and connections only; capture baseline and later immutable snapshot; inspect additions/removals with evidence references | SQL views implemented/documented; live handoff remains gated by TRANS review and analyst acceptance |
| M6 — Automation | Operator-selected cadence, idempotent runs, bounded local history, failure and last-success reporting | PASS for the prior service configuration. One-database service paths are part of M8 and still require installation and runtime verification. |
| M7 — Public release and family integration | Privacy and dependency audit; approved family registration; independent malformed/stale/unauthorized review; reproducible package build; release owner approval | Not ready |
| M8 — One Research database | One active Research SQLite file contains TRANS source/transcript tables and Traffic Analysis outputs; TRANS stays the canonical text owner; one Research publisher prevents lost updates | Candidate sync/path changes are on non-main branches. A combined corpus snapshot is published and verified on this host; service reload/activation and legacy-file retirement remain pending |

## Required invariants

1. TRANS input is opened read-only. The importer never reads audio paths or source media.
2. The persistent analysis database and derived records remain on the operator-controlled Research volume. Per-run working copies are private and removed on completion. Git contains code and synthetic examples only.
3. Missing or unknown `content_kind` fails closed to `uncertain`. Audible events remain visible but never become speech-derived contacts.
4. Only explicit addressing patterns produce directed candidates. Every candidate starts pending review.
5. Arch Notebook output requires both an accepted candidate and source review state `accepted` or `corrected`.
6. Radio identifiers remain scoped to their TRANS call. No alias merging or person/organization identity resolution is performed.
7. The package has no network listeners, external network calls, telemetry, or model downloads. Optional neural inference uses a private temporary Unix socket to a local llama.cpp process.

## Release gates

- Confirm the TRANS schema version and exact source review semantics before depending on source approval.
- Review synthetic examples and approved representative radio examples for false positive and false negative behavior. Keep examples synthetic in Git.
- Before relying on call-type labels, define an annotation guide and evaluate per-label precision/recall/F1 on a source-grouped, adjudicated sample. Current keyword labels are unvalidated triage aids; consult the [literature matrix](literature_matrix.md).
- Before enabling automatic acceptance, downstream reliance, or public release of neural classifications, verify model shard completeness, memory/runtime fit, structured-output support, and per-label quality on authorized reviewed examples. Until those gates pass, keep proposals pending and advisory.
- Verify malformed schemas, changed hashes, unknown kinds, stale review, rejected text, and concurrent import behavior.
- Review storage permissions, database backup/retention, and Git-ignore rules before any corpus operation.
- Confirm the Arch Notebook SQL contract and operator approval before export or scheduled capture.
- Select a license and complete security, code, build, and release reviews before describing the package as a public release.
