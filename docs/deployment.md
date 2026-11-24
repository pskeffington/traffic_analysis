# Local deployment

Traffic Analysis uses a user-level hourly oneshot service. It reads the TRANS
recovery checkpoint, imports it read-only, runs a resumable local Qwen batch,
then publishes the updated database to the Research share. SQLite changes are
made in a private, temporary local working directory because direct SQLite
writes to the mounted CIFS share fail during lock acquisition. The working copy
is removed after each run; the Research share holds the one persistent Traffic
Analysis database, which Arch Notebook can open read-only.

## Paths

- TRANS input: `~/Research.Databases/audio_ingest/transcription.sqlite.gz`
- Persistent database: `~/Research.Databases/traffic_analysis/traffic-analysis.sqlite`
- Persistent service lock: `~/.local/share/traffic-analysis/production-cycle.lock`
- Private per-run working database and TRANS import: a temporary directory removed at cycle exit
- Default model: installed Qwen3.5-9B Q4 GGUF in the llama.cpp model cache
- Inference runtime: installed `llama-server`, started for each batch on a
  private Unix socket and stopped when the batch ends

The service holds a local single-writer lock, verifies SQLite integrity,
bounds checkpoint decompression, imports transcript text only, and atomically
replaces the closed Research database after a completed cycle. Each run starts
from that database, applies new imports and classifications locally, then
removes its temporary copy. If an older persistent local database exists, the
service removes it only after verifying it is byte-identical to the Research
database and successfully publishing the next Research version. It stops with
an error and preserves the local database if the Research copy is missing or
the two databases differ; resolve that migration manually before restarting.
The service logs aggregate
counts and a database digest, not transcript text. The database, working
directory, and lock use owner-only permissions. Model and source files are
read-only inputs.

## Install and operate

Click `~/Desktop/Deploy Traffic Analysis.desktop`, or run
`tools/install-user-service.sh` from a terminal. The installer verifies the
Research CIFS source and read-write mount, checkpoint readability, and the
pinned Qwen model checksum before it reloads systemd, enables the hourly timer,
and starts the initial batch. It does not use sudo.

Inspect operation with:

```sh
systemctl --user status traffic-analysis.service traffic-analysis.timer
journalctl --user -u traffic-analysis.service
```

The first run imports the current TRANS checkpoint and classifies up to 1,000
speech segments. Later hourly runs import new or changed records and skip model
analyses already completed for the same model, prompt, and engine versions. The
model preflight uses Linux `MemAvailable`, honors a tighter active cgroup limit,
and conservatively respects NUMA memory-node restrictions. Recheck host memory
before moving the unit to another system.

Set `TRAFFIC_ANALYSIS_TRANS_CHECKPOINT`, `TRAFFIC_ANALYSIS_SNAPSHOT`,
`TRAFFIC_ANALYSIS_DATA_HOME`, `TRAFFIC_ANALYSIS_MODEL`, or
`TRAFFIC_ANALYSIS_LLAMA_SERVER` in the user service environment to override
defaults on another installation. Set `TRAFFIC_ANALYSIS_RESEARCH_SOURCE` if
the private CIFS share has a different source name. Custom data-home and
snapshot paths must also be added to the unit's `ReadWritePaths`; the default
paths are already allowed. Configure these with
`systemctl --user edit traffic-analysis.service`, then run
`systemctl --user daemon-reload`. For custom
writable paths, clear and restate `ReadWritePaths` in that drop-in so both the
local data directory and snapshot directory are allowed. Keep model weights out
of the Git repository.

Every neural result remains pending review. TRANS review is also pending in the
current corpus, so Arch Notebook views do not publish these classifications or
contacts until both the source text and analysis have been reviewed and accepted.
