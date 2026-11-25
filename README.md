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
traffic-analysis --database /tmp/traffic-analysis-demo/analysis.sqlite call-types
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

Arch Notebook can read the `arch_notebook_connections` SQL view. A row appears only after the candidate is accepted and the corresponding TRANS text review state is `accepted` or `corrected`. If a neural classification for the same segment is also analyst-accepted, its call types and summary appear as connection properties in that row; this adds context to an explicit radio contact without inventing a new edge.

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

## Call type labels

Every TRANS-labeled `speech` segment receives deterministic, overlapping text labels for `fire`, `police`, `ems`, `bus`, `school`, `traffic`, `electrical`, `public_works`, `hazmat`, and `weather`; unmatched speech is `unclassified`. `call-types` reports aggregate label counts without transcript text. A segment may count under more than one label (for example, school and bus). These labels are triage summaries of words in the transcript, not verified incident classifications and not network connections. `audible_event` and `uncertain` segments are kept separate and receive no call-type label.

## Optional local neural analysis

For model-assisted speech triage, install llama.cpp locally and provide an existing GGUF model file. No weights are downloaded. `neural-classify` starts llama.cpp on a private Unix socket for one resumable batch, stores model and prompt versions with each proposal, and stops the process afterward. Model labels and summaries begin pending review; the Arch Notebook classifications view requires both analyst acceptance and TRANS source status `accepted` or `corrected`.

```sh
traffic-analysis --database /path/to/private/traffic-analysis.sqlite \
  neural-classify --model /path/to/local/model.gguf --limit 1000
traffic-analysis --database /path/to/private/traffic-analysis.sqlite \
  neural-results --state pending
```

Review proposals only after the corresponding TRANS segment is accepted or corrected:

```sh
traffic-analysis --database /path/to/private/traffic-analysis.sqlite \
  neural-review ANALYSIS_ID --state accepted --reviewer analyst-id
```

This inference path uses a private temporary Unix socket and standard-library HTTP framing; it opens no IP listener and does not contact a remote model service. To expose an accepted classification with an explicit contact, query `arch_notebook_connections`; see [the neural engine contract](docs/neural_engine.md) for validation and downstream view semantics.

## Data handling

- Keep source databases, imported transcript text, the analysis database, and review notes in private operator-controlled storage. The production service persists one analysis database on the Research share and uses a private local working copy that is removed after each cycle.
- Never commit corpus records, exported transcripts, analysis databases, screenshots, logs containing text, or identifying source metadata.
- The repository demo contains synthetic text only. There is no external network access or telemetry; optional neural inference uses a temporary private Unix socket.
- Use only data you are authorized to process and follow the TRANS source's access and retention rules.

See [the production plan](docs/PRODUCTION_PLAN.md) for current implementation status and remaining integration gates.
See the [text-classification literature matrix](docs/literature_matrix.md) for evidence, limits, and recommended validation practice for radio call-type labels.
See [local deployment](docs/deployment.md) for the hourly import, Qwen batch, and Research database service.

## License

This project is distributed under the MIT License. See [LICENSE](LICENSE).
