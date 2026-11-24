# Traffic Analysis project instructions

- This repository starts after TRANS ASR. Never read or process audio here; accept only transcript/event rows from a TRANS SQLite database in read-only mode.
- Preserve source, segment, time, transcript hash, provenance, content kind, and TRANS review state on every imported row.
- Keep spoken text, audible-event annotations, and uncertain content distinct. Do not drop non-speech events.
- Extract only explicit radio addressing patterns into directed `RADIO_CONTACT` candidates. Co-occurrence, timing, status phrases, locations, and callsign similarity do not establish a connection.
- Every extracted connection starts pending review. Arch Notebook export must require both an accepted candidate and an accepted/corrected TRANS text segment.
- Keep source text and derived records local. No telemetry, network service, cloud model, or added package dependency. The optional neural classifier may use a preinstalled llama.cpp executable connected only through a private per-run Unix socket; it must stop that process after each batch and never download model weights.
- Treat names/callsigns as radio identifiers, not real-world identities. Preserve ambiguity; do not merge aliases automatically.
- Do not run tests unless the user requests them. Report unrun checks accurately.
