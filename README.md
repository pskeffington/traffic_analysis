# Radio Traffic Analysis

`traffic_analysis` is a local, review-first text analysis stage between TRANS and downstream network review:

```text
audio -> TRANS normalization / ASR / timestamped text and event labels
      -> traffic_analysis read-only import -> reviewed radio-contact candidates
      -> Arch Notebook read-only SQL snapshot and network regression review
```

TRANS remains the only audio processor. This package reads TRANS SQLite records in read-only mode and stores text, provenance, event labels, and candidate records in a separate private SQLite database. It does not read audio paths, run models, call network services, or send telemetry. Runtime code uses only the Python standard library.

## Install

Python 3.11 or later is required. From a clone of this repository:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
traffic-analysis --help
```

## Synthetic demo

The demo generator writes only fabricated text and hashes. It refuses to overwrite an existing output file.

```sh
python examples/create_synthetic_trans_db.py /tmp/traffic-analysis-demo/trans.sqlite
traffic-analysis --database /tmp/traffic-analysis-demo/analysis.sqlite \
  import-trans --trans-database /tmp/traffic-analysis-demo/trans.sqlite
traffic-analysis --database /tmp/traffic-analysis-demo/analysis.sqlite report
traffic-analysis --database /tmp/traffic-analysis-demo/analysis.sqlite \
  candidates --state pending
traffic-analysis --database /tmp/traffic-analysis-demo/analysis.sqlite events
```

Review is explicit. Use the candidate ID returned by the `candidates` command:

```sh
traffic-analysis --database /tmp/traffic-analysis-demo/analysis.sqlite \
  review CANDIDATE_ID --state accepted --reviewer demo-analyst \
  --note 'Verified against the synthetic source segment'
```

Arch Notebook can read the `arch_notebook_connections` SQL view. A row appears only after the candidate is accepted and the corresponding TRANS text review state is `accepted` or `corrected`.

## Import a TRANS database

The source TRANS database is opened with SQLite `mode=ro` and `PRAGMA query_only=ON`. Import does not modify it. Choose a private local destination for the analysis database and keep that file outside Git and shared/public folders.

```sh
traffic-analysis --database /path/to/private/traffic-analysis.sqlite \
  import-trans --trans-database /path/to/trans-database.sqlite
traffic-analysis --database /path/to/private/traffic-analysis.sqlite report
traffic-analysis --database /path/to/private/traffic-analysis.sqlite \
  candidates --state pending
traffic-analysis --database /path/to/private/traffic-analysis.sqlite events
```

The importer reads the documented TRANS fields only. If `content_kind` is absent, the segment is retained as `uncertain`; this package does not guess from text or audio. Audible events remain separate and queryable. See [the TRANS SQL input contract](docs/trans_sql_input.md).

## Analysis boundaries

The deterministic extractor creates directed `RADIO_CONTACT` candidates only for explicit addressing forms such as `UNIT 12 to DISPATCH`, `DISPATCH, this is UNIT 12`, and `DISPATCH from UNIT 12`. Acknowledgments, readbacks, corrections, wait instructions, status phrases, locations, timing, or co-occurrence do not create edges. See [radio patterns and references](docs/radio_patterns.md).

Every candidate begins pending review. Identifiers are scoped to a TRANS `call_id`; similar labels are never merged, and a radio identifier is not treated as a person or organization. The Arch Notebook view is a read-only projection. The current TRANS interface supplies transcript-level review state, so pending source text cannot be promoted here. Traffic Analysis never fabricates source approval or edits TRANS text.

## Data handling

- Keep source databases, imported transcript text, analysis databases, and review notes in private local storage.
- Never commit corpus records, exported transcripts, analysis databases, screenshots, logs containing text, or identifying source metadata.
- The repository demo contains synthetic text only. No runtime network access or telemetry is implemented.
- Use only data you are authorized to process and follow the TRANS source's access and retention rules.

See [the production plan](docs/PRODUCTION_PLAN.md) for current implementation status and remaining integration gates.

See the [text-classification literature matrix](docs/literature_matrix.md) for evidence, limits, and recommended validation practice for radio call-type labels.

## License

This project is distributed under the MIT License. See [LICENSE](LICENSE).
