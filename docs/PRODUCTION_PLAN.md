# Radio traffic analysis production plan

**Task:** RAD-001 — TRANS text to reviewed radio network candidates to Arch Notebook  
**Owner:** Orchestrator/implementer: Codex, single session; no delegated execution claimed  
**Baseline:** `traffic_analysis` local candidate `5489e1e9c09aaf8972e9a9ba687398682bc7f3f5`; GitHub draft PR #1 head `e61dbc905aadb900b3c09d5b8241af7eee365aed`. TRANS working baseline `568dc9e4a487113cf30a385bb1183c5fda298c7f`; Arch Notebook working baseline `f4d005772f165352f400f4071fef747cdb635e09`.  
**Skills workflow:** `software-delivery-orchestrator` → `engineering-planning` → `architecture-design` → `code-implementation` → `build-integration` → documentation/review/release gates. Skill library baseline: `agent_skills` `d1edfb77d588c17432d448e26b3b8f3ffceec91a`.  
**Scope:** TRANS owns audio processing through timestamped text and event labels; Traffic Analysis consumes TRANS SQL text records and produces reviewed, explicit radio-contact candidates; Arch Notebook reads the accepted candidate view as immutable SQL snapshots. No audio processing, entity identity inference, unattended acceptance, or external delivery.

## Milestones

| Milestone | Result and acceptance criteria | Current state | Dependencies |
|---|---|---|---|
| M0 — Text analysis foundation | Read-only TRANS import; preserve text, time, content kind, source state, hashes, and provenance; deterministic radio patterns; candidate review and event listing commands; Arch Notebook SQL view | **PASS — prototype in draft PR #1.** Static syntax/CLI checks passed; no tests run. | None |
| M1 — TRANS speech/event and review contract | TRANS emits explicit `speech`/`audible_event`/`uncertain` segment kinds; each reviewed speech segment has a hash-bound decision; repeatable, checkpointed migration; event labels survive reprocessing | **BLOCKED.** Current live DB has 718 pending transcripts, 443 segments, no `content_kind` column; current schema restricts every transcript to `pending`. The family registry is still a proposal and does not identify Traffic Analysis as an approved module. | TRANS owner review workflow and schema/profile versioning; canonical family registry approval; retain checkpoint and rollback before migration |
| M2 — Populate Traffic Analysis | Import the TRANS text database idempotently into a private approved data path; report speech/event/uncertain and pending counts; human review candidates; accepted candidates require accepted/corrected TRANS text | **BLOCKED.** SMB is mounted read-only and the requested `audio_ingest` directory is not mounted at the checked paths. No Traffic Analysis corpus DB has been created. | Writable Research.Databases mount/path; M1 input labels and review states |
| M3 — Arch Notebook regression handoff | Query `arch_notebook_connections` read-only; capture one baseline and a later snapshot in one series; inspect additions/removals with evidence/source references; do not promote pending rows | **PARTIAL.** SQL view and query documented; no accepted source rows exist, and no live snapshot was captured. | M1 review state, M2 data path, Arch Notebook operator acceptance |
| M4 — Automated operation | Schedule idempotent TRANS import and Arch Notebook snapshot capture; report failures and last successful run; bounded local history | **NOT STARTED.** CLI is manual; no timer or scheduled job is installed. | M2 path, operator-selected cadence, verified local service configuration |
| M5 — Family integration and production readiness | Register the stage/profile with the canonical TRANS family contract; update synchronized contract copies; independent malformed/stale/unauthorized tests, security review, build evidence, code review, release dossier and owner approval | **BLOCKED.** `traffic_analysis` is not in the current module registry; PR #1 is draft; tests and independent reviews remain outstanding. | M1–M4, registry owner review, explicit release authorization |

## Interface and invariants

- TRANS is the sole owner of ASR, transcript text, text hashes, and sound-event classification. Traffic Analysis trusts only TRANS-provided `content_kind`; absent/unknown kinds fail closed to `uncertain`.
- TRANS input remains read-only. Traffic Analysis persists its own segment-analysis and candidate tables separately.
- Audible events remain queryable even though they do not create speech-derived connections.
- A graph edge means only an explicit radio contact. Callsigns remain radio identifiers scoped to `call_id`; acknowledgments, status, shared time, and co-occurrence do not create edges.
- Candidate review is separate from TRANS transcript review. Arch Notebook output requires both candidate `accepted` and TRANS segment state `accepted` or `corrected`.
- Arch Notebook remains read-only to the Traffic Analysis database and compares immutable captures. Confidence is intentionally low (`1`) pending corpus-specific evaluation.
- Do not copy sensitive transcript text to Git or the notebook; the local database and evidence reference remain the source for review.

## Next engineering task contract: RAD-002

**Role:** planner/architect handoff to the TRANS contract owner. **Baseline:** TRANS `568dc9e4a487113cf30a385bb1183c5fda298c7f`; current live DB schema and the compressed checkpoint were inspected read-only. **Scope:** design the review and compatibility contract; do not mutate the live database or change TRANS-family registry copies from this repository.

**Acceptance criteria:**

1. TRANS retains its existing canonical transcript and hash authority. A review decision binds to `source_id`, profile name/version, segment sequence, segment text SHA-256, and content kind, and records reviewer, time, and decision as append-only evidence.
2. Human acceptance applies only to the exact speech segment reviewed. Pending, rejected, stale-hash, unknown-kind, or missing review evidence fails closed. Audible events and uncertain segments remain visible in TRANS but cannot produce Traffic Analysis candidates or Arch Notebook rows.
3. Corrections create a new TRANS-authoritative text/hash state and invalidate decisions bound to the prior hash; Traffic Analysis cannot edit or approve TRANS text.
4. The migration has a TRANS-owned checkpoint, idempotent resume behavior, an explicit rollback artifact, and a report-only dry-run before touching the live queue.
5. The canonical registry owner approves the module/profile and the required bounded release/authorization handoff before any production cross-repository transfer. Existing planned `database_sql` status does not count as approval for Traffic Analysis.

**Dependencies/blockers:** the live DB currently exposes only transcript-level `pending`, not segment review; the archive is mounted read-only; Traffic Analysis is not an approved registry entry. No live migration or corpus import is authorized by this task contract. **Next owner:** TRANS contract/registry owners, then Traffic Analysis implementer after the contract and storage gates pass.

## Risks and rollback

- **ASR/event classification:** current stored segments predate `content_kind`. Reclassification must run through the TRANS-owned, checkpointed migration; do not guess in Traffic Analysis. Preserve the compressed checkpoint and source hashes before applying it.
- **Unreviewed text:** current transcript states are all pending. No notebook output should appear until TRANS supplies approved states; Traffic Analysis must not invent them.
- **Storage:** `/srv/archive` is CIFS `ro`; the archive `audio_ingest` directory was not found at `/srv/archive/audio_ingest` or `~/Research.Databases/audio_ingest`. Do not fall back to another permanent database path without recording that storage decision.
- **Rollback:** stop the TRANS worker, preserve the exact pre-migration checkpoint, restore that checkpoint to tmpfs if migration is rejected, and discard only the newly-created Traffic Analysis database after preserving its run receipt. Do not overwrite archived audio or transcript sources.

## Immediate next actions

1. Complete M1 in TRANS: versioned content-kind migration and human transcript-review workflow, with no automatic approval based on ASR confidence.
2. Restore writable access to the requested archive path, then run the migration and import under recorded DB/checkpoint digests.
3. Reconcile Traffic Analysis with the canonical family registry/release adapter, then capture an Arch Notebook baseline and subsequent regression snapshot.
4. Add the scheduled import only after the data path and failure behavior are verified.

No test suite was run in this task; the repository instruction forbids running tests unless requested. Production readiness remains **BLOCKED** at M1/M2/M5. Static `py_compile`, CLI help, and `git diff --check` on the Traffic Analysis prototype passed; these are not test or integration evidence.

## Handoff evidence

- **Code candidate checked:** draft PR [#1](https://github.com/pskeffington/traffic_analysis/pull/1), current code commit `543d1730d0897ae601f67f8f5763c29ab6fb0c09`.
- **Changed paths:** `README.md`, `src/traffic_analysis/store.py`, `docs/PRODUCTION_PLAN.md`.
- **Commands/results:** `git diff --check` — exit 0; `PYTHONPATH=src python3 -m py_compile src/traffic_analysis/*.py` — exit 0; `PYTHONPATH=src python3 -m traffic_analysis.cli --help` — exit 0.
- **Tests/reviews:** tests not run per `AGENTS.md`. Independent code review of the revised import/provenance safeguards **PASS** (bounded review of `store.py`, content SHA `c35213840b7fda9504440febef7b722344b44699`); no tests were run by the reviewer. Security and release reviews remain unperformed.
- **Stage result:** M0 prototype **PASS** for static checks and bounded independent code review. M1/M2/M5 remain **BLOCKED** as specified above. **Next owner:** TRANS contract and canonical registry owners for RAD-002; Traffic Analysis resumes once that interface and the requested shared data path are cleared.
