# TRANS SQL input contract

The importer opens the TRANS database with SQLite `mode=ro` and `PRAGMA query_only=ON`. It reads only `audio_sources`, `audio_transcripts`, and `audio_transcript_segments`; this stage does not read audio paths or source media.

Required TRANS fields:

- `audio_sources`: `source_id`, `call_id`, `started_at`
- `audio_transcripts`: `source_id`, `profile_name`, `profile_version`, `transcript_sha256`, `provenance_sha256`, `review_status`
- `audio_transcript_segments`: `source_id`, `profile_name`, `profile_version`, `sequence`, `start_ms`, `end_ms`, `text`, `text_sha256`

Optional segment fields are `content_kind`, `confidence`, and `confidence_method`. If `content_kind` is missing, imported text is set to `uncertain`; Traffic Analysis does not reclassify audio or infer event labels. Accepted kinds are `speech`, `audible_event`, and `uncertain`. TRANS's segment sequence is the stable segment key within a source/profile/version. A changed text hash under the same key fails closed; TRANS should issue a new profile version when transcript text changes.

TRANS review state is copied verbatim. When present, the TRANS `audio_transcript_reviews` ledger supersedes the processor's `audio_transcripts.review_status` only when its latest `review_sha256` matches a locally recomputed digest over source ID, call ID, start time, audio hash, transcript hash, and every segment's sequence, timing, text hash, and content kind. Stale or inconsistent decisions therefore fall back to the source status or fail import. Older TRANS databases without this ledger remain compatible and retain their exported review state. Connection candidates start `pending`; acknowledging or status phrases alone never produce graph edges. The `arch_notebook_connections` view requires both an accepted candidate and TRANS review state `accepted` or `corrected`. Radio identifier entity IDs are stable within a TRANS `call_id`, but are not merged across calls. Pending or rejected source text is never promoted here.
