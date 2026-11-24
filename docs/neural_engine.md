# Local neural engine contract

## Purpose and boundary

The optional engine sorts TRANS-labeled `speech` segments into overlapping operational call types, extracts station mentions, drafts a short summary, and proposes only explicitly evidenced radio contacts. It runs after TRANS; it never reads audio or changes transcript text. The implementation uses a locally installed `llama-server` and an operator-supplied GGUF file. It does not download weights, use a cloud model, add a Python runtime dependency, or open an IP listener.

The local server starts for one requested batch on a private Unix domain socket, emits no logs to a file or terminal, and is terminated after the batch. HTTP framing over the socket uses the Python standard library and the llama.cpp Chat Completions endpoint with JSON Schema constrained output. llama.cpp documents Unix socket paths for `--host` and schema-constrained output in its [server API documentation](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md#post-v1chatcompletions-openai-compatible-chat-completions-api).

## Classification record

For each speech segment, the model proposes:

- zero or more labels from `fire`, `police`, `ems`, `bus`, `school`, `traffic`, `electrical`, `public_works`, `hazmat`, and `weather`;
- a verbatim transcript evidence quote for every label;
- station mentions and their verbatim evidence;
- radio-contact proposals with origin, addressee, and verbatim evidence;
- a short transcript-grounded summary and qualitative uncertainty (`low`, `medium`, `high`).

There is intentionally no numeric model confidence. Self-reported probabilities are not calibrated. The transcript is JSON-encoded as untrusted data in the prompt, and the model is told not to follow embedded instructions. The application then validates response shape, allowed labels, bounds, exact evidence substrings, and contact direction. These checks constrain outputs; they do not prove that a label or summary is semantically correct.

## Review and export gates

1. Each result is written to `neural_analyses` with model identifier, model-shard manifest digest, llama.cpp version, prompt version, normalized JSON output, and output SHA-256.
2. Results begin `pending`. `neural-results` displays source text and proposed evidence for a local reviewer.
3. A proposal cannot be accepted until TRANS marks the corresponding source segment `accepted` or `corrected`.
4. Contact proposals create a pending `RADIO_CONTACT` candidate only if their exact quote independently matches the deterministic explicit-address parser and its parsed origin/addressee match the model fields. Candidate acceptance remains a separate review decision.
5. `arch_notebook_classifications` contains only analyst-accepted proposals whose TRANS source is accepted/corrected. `arch_notebook_connections` continues to require an accepted connection candidate and accepted/corrected TRANS source. When a segment has an accepted neural proposal, its type labels, summary, model identifier, uncertainty, and output digest are exposed as `connection_attribute:*` fields on that explicit contact row for Arch Notebook's existing SQL importer.

The neural classification view is a SQL read-only projection for downstream review, not automatic publication or identity resolution. Rejected, pending, uncertain-source, and event-only rows do not appear in it. A classification without a reviewed explicit radio contact remains available in `arch_notebook_classifications`, but is not converted into a graph edge.

## Operation

First import TRANS metadata into the private Traffic Analysis database. Then run an explicitly selected local model:

```sh
traffic-analysis --database /path/to/private/traffic-analysis.sqlite \
  neural-classify --model /path/to/model.gguf --limit 1000
traffic-analysis --database /path/to/private/traffic-analysis.sqlite \
  neural-results --state pending --limit 50
```

Successful segments are committed individually. Failures are counted by a bounded, content-free error code in a local table without storing the transcript or model response. Scheduled cycles skip prior failures; an operator can explicitly retry them with `--retry-failures`. The production runner performs one bounded retry after an initial all-failure batch to capture structured completion diagnostics, then leaves repeated failures for operator action. Re-running with the same model manifest, llama.cpp version, and prompt version skips completed rows. Changing the model-shard manifest, engine version, or prompt version creates a distinct analysis record. Model weights and transcript rows remain local.

Before starting the server, the engine compares model shard bytes with currently available system RAM (and the active cgroup memory limit when available) and refuses an obvious overcommit. `--allow-memory-overcommit` is available only for an operator who has checked GPU/accelerator offload and host memory behavior; it can still lead to a failed or extremely slow run.

The shard manifest digest covers shard filenames and byte sizes; it is an identity aid, not a cryptographic digest of model weights. For stronger provenance, operators should record the trusted model release/checksum separately before production deployment.

## Production gates

- Confirm every model shard is readable and the model fits the host's memory/compute budget before processing a corpus.
- Review the selected model's source, license, and redistribution/use terms; Traffic Analysis does not bundle model weights.
- Confirm the selected model supports the local server's chat template and constrained JSON output.
- Segments over the engine's 6,000-character input bound fail closed and remain available for manual review.
- Run first on synthetic data, inspect outputs and failure behavior, then evaluate a source-grouped, adjudicated private sample.
- Report per-label precision/recall/F1, support, common confusions, invalid-output rate, and review disagreement. Do not treat general benchmark results as corpus validation.
- Keep the neural path opt-in until these checks pass. Deterministic keyword labels remain a separate baseline and are not silently overwritten by neural proposals.
