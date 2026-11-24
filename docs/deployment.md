# Local deployment

Traffic Analysis uses a user-level hourly oneshot service. It reads the TRANS
recovery checkpoint, imports it read-only, runs a resumable local Qwen batch,
then publishes one closed SQLite snapshot to the Research share. The active
SQLite file stays in private local storage because direct SQLite writes to the
mounted CIFS share fail during lock acquisition. Arch Notebook can open the
closed snapshot read-only.

## Paths

- TRANS input: `~/Research.Databases/audio_ingest/transcription.sqlite.gz`
- Local active database: `~/.local/share/traffic-analysis/traffic-analysis.sqlite`
- Published snapshot: `~/Research.Databases/traffic_analysis/traffic-analysis.sqlite`
- Default model: installed Qwen3.5-9B Q4 GGUF in the llama.cpp model cache
- Inference runtime: installed `llama-server`, started for each batch on a
  private Unix socket and stopped when the batch ends

The service holds a local single-writer lock, verifies SQLite integrity,
bounds checkpoint decompression, imports transcript text only, and atomically
replaces the closed Research snapshot after a completed cycle. It logs
aggregate counts and a snapshot digest, not transcript text. The snapshot and
local working directory use owner-only permissions. Model and source files are
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
paths are already allowed. Configure these with `systemctl --user edit
traffic-analysis.service`, then run `systemctl --user daemon-reload`. For custom
writable paths, clear and restate `ReadWritePaths` in that drop-in so both the
local data directory and snapshot directory are allowed. Keep model weights out
of the Git repository.

Every neural result remains pending review. TRANS review is also pending in the
current corpus, so Arch Notebook views do not publish these classifications or
contacts until both the source text and analysis have been reviewed and accepted.
