# Traffic Analysis

`traffic_analysis` is the text-analysis stage between TRANS and Arch Notebook:

```text
audio -> TRANS normalization / ASR / timestamped text and event labels
      -> traffic_analysis read-only import -> reviewed radio-contact candidates
      -> Arch Notebook read-only SQL snapshot and network regression review
```

TRANS remains the only audio processor. This project reads TRANS SQLite records in read-only mode and stores transcript text, provenance, event labels, and extracted candidates in a separate private SQLite database. It has no audio decoder, model downloads, telemetry, or network service.

## Current extraction scope

The first deterministic pass recognizes only explicit radio addressing forms: `UNIT 12 to DISPATCH`, `DISPATCH, this is UNIT 12`, and `DISPATCH from UNIT 12`. It creates a directed `RADIO_CONTACT` candidate. Common acknowledgments, readbacks, corrections, wait instructions, and status phrases are labeled but do not create a connection by themselves. TRANS-labeled audible events such as cheering or music remain separate, timestamped observations.

All candidates require human review. Arch Notebook's SQL view emits a row only when the candidate has been accepted and the corresponding TRANS segment is `accepted` or `corrected`. Radio-identifier entity IDs are stable within a TRANS `call_id`, but are not merged across calls. This project never equates a radio identifier with a person or organization, never merges similar identifiers, and never infers edges from co-occurrence or timing. See [radio patterns and references](docs/radio_patterns.md).

## Use

The TRANS database is opened read-only. Use a private local path for the analysis database:

```sh
traffic-analysis --database /path/to/private/traffic-analysis.sqlite \
  import-trans --trans-database /tmp/trans-audio.sqlite
traffic-analysis --database /path/to/private/traffic-analysis.sqlite report
traffic-analysis --database /path/to/private/traffic-analysis.sqlite candidates --state pending
traffic-analysis --database /path/to/private/traffic-analysis.sqlite events
```

Review a candidate explicitly:

```sh
traffic-analysis --database /path/to/private/traffic-analysis.sqlite \
  review CANDIDATE_SHA256 --state accepted --reviewer analyst-id \
  --note 'Addressing pattern verified against the source transcript'
```

The candidate listing includes the transcript segment and its source-review status. The events listing exposes TRANS-labeled cheering, music, and other audible-event rows for review; event rows are retained separately and never treated as spoken callsigns. See [the TRANS SQL input contract](docs/trans_sql_input.md) for fields and compatibility behavior.

Arch Notebook can read `arch_notebook_connections` from the analysis database in SQL Review. Query template:

```sql
SELECT connection_id, source, target, source_entity_id, target_entity_id,
       source_type, target_type,
       relationship, date, confidence, directed, source_reference, notes
FROM arch_notebook_connections
ORDER BY date, connection_id
```

Ingest repeated query captures into one Arch Notebook snapshot series to review additions, removals, and changes. The exported confidence is intentionally `1` (low); the source text remains available in this local database for review, linked by source reference and segment ID.

The current TRANS audio SQL schema still fixes transcript review state to `pending`. The projection therefore exports no connection rows until TRANS introduces an accepted/corrected text review state; Traffic Analysis does not manufacture that approval.

## Development

Python 3.11+; runtime uses only the standard library. Tests have not yet been added or run.
