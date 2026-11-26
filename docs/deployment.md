# Local deployment

Traffic Analysis uses a user-level hourly oneshot service. It reads the TRANS
recovery checkpoint, imports it read-only, runs a resumable local Qwen batch,
then publishes the updated database to the Research share after each bounded
inference batch. SQLite changes are
made in a private, temporary local working directory because direct SQLite
writes to the mounted CIFS share fail during lock acquisition. The working copy
is removed after each run; the Research share holds the one persistent Traffic
Analysis database, which Arch Notebook can open read-only.

## Paths

- TRANS input: the read-only SQLite checkpoint configured by
  `TRAFFIC_ANALYSIS_TRANS_CHECKPOINT`
- Persistent database: the single analysis snapshot configured by
  `TRAFFIC_ANALYSIS_SNAPSHOT`
- Persistent service lock: the private local directory configured by
  `TRAFFIC_ANALYSIS_DATA_HOME`
- Private per-run working database and TRANS import: a temporary directory removed at cycle exit
- Model: an existing local GGUF configured by `TRAFFIC_ANALYSIS_MODEL`; model
  weights are not downloaded or bundled
- Inference runtime: installed `llama-server`, started for each batch on a
  private Unix socket and stopped when the batch ends

The service holds a local single-writer lock, verifies SQLite integrity,
bounds checkpoint decompression, imports transcript text only, and atomically
replaces the closed Research database after the import and each completed
inference invocation. Each run starts from that database, applies imports and
classifications in a private local working copy, publishes batches of up to 50
neural attempts (with a 1,000-attempt cycle cap), then removes its temporary
copy. A new model configuration begins with a one-segment probe. If a cycle
stops during a batch, the last published Research snapshot retains earlier
completed work and the next cycle resumes remaining records. If the canonical
Research database is missing and a split legacy Research snapshot exists, the
runner restores that snapshot as input and publishes the canonical database.
It does not retire the split legacy Research files. Use
`tools/retire_legacy_research_databases.py` as a separate guarded migration
after reviewing its rollback and service-state requirements; until then, keep
the legacy database as a rollback copy.
The service logs aggregate
counts and a database digest, not transcript text. The database, working
directory, and lock use owner-only permissions. Model and source files are
read-only inputs.

## Install and operate

For the initial installation, run `tools/install-user-service.sh` from a
terminal. The installer creates `~/Desktop/Deploy Traffic Analysis.desktop`
for later deployment updates and `~/Desktop/Run Traffic Analysis Now.desktop`
for manual cycles. It verifies the TRANS checkpoint's CIFS source and
readability, the Research output directory's
CIFS read-write mount, and the expected checksum for the default Qwen model before it reloads
systemd, enables the hourly timer, and starts the initial batch. It does not use
sudo. The service keeps the checkpoint read-only and writes only to the analysis
directory on the Research share.
The Traffic Analysis directory must be owner-only (mode `0700`) as reported by
the mounted filesystem; the installer checks this before starting the service.
The published SQLite file uses owner-only permissions (`0600`).

The initial batch starts asynchronously so installation does not wait for a
full corpus pass. A service state of `activating` with `SubState=start` means
that oneshot batch is still running; inspect the service journal and the
database's aggregate report for completion. Installer rollback covers failed
unit activation and deployment-file setup. Inference or corpus errors after
the service has started are reported by the normal cycle and do not undo the
installed unit.

The installer also creates a `Run Traffic Analysis Now` desktop action. Use it
for a manual cycle when the user systemd bus is unavailable. It invokes the same
guarded runner directly; the persistent single-writer lock prevents it from
overlapping an active timer cycle. The terminal remains open to show aggregate
status and safe error codes. A private mode-0600 log is also written to the
configured data home so the operator can inspect the outcome after the terminal
closes.

Inspect operation with:

```sh
systemctl --user status traffic-analysis.service traffic-analysis.timer
journalctl --user -u traffic-analysis.service
```

The first run imports the current TRANS checkpoint and makes up to 1,000 neural
attempts, checkpointing in batches of up to 50. Later hourly runs import new or changed
records and skip model analyses already completed for the same model, prompt,
and engine versions. The
model preflight uses Linux `MemAvailable`, honors a tighter active cgroup limit,
and conservatively respects NUMA memory-node restrictions. Recheck host memory
before moving the unit to another system.

By default, neural startup refuses when model shard bytes exceed the estimated
available RAM. On a host where an operator has reviewed accelerator visibility
and observed memory and swap behavior, opt in with a private local marker:

```sh
install -d -m 700 "$HOME/.local/share/traffic-analysis"
printf '%s\n' operator-reviewed-host-memory > "$HOME/.local/share/traffic-analysis/allow-memory-overcommit"
chmod 600 "$HOME/.local/share/traffic-analysis/allow-memory-overcommit"
```

The marker is local runtime configuration and is not stored in Git. Remove it
to restore the default preflight. The override can make inference slow or
exhaust host resources; review actual accelerator visibility and available
swap before creating it.

The installer accepts operator-specific paths as environment variables and
persists them for both the timer service and manual desktop action. Example:

```sh
export TRAFFIC_ANALYSIS_RESEARCH_SOURCE='//fileserver/research'
export TRAFFIC_ANALYSIS_TRANS_CHECKPOINT="$HOME/Research/audio_ingest/transcription.sqlite.gz"
export TRAFFIC_ANALYSIS_SNAPSHOT="$HOME/Research/traffic_analysis/traffic-analysis.sqlite"
export TRAFFIC_ANALYSIS_DATA_HOME="$HOME/.local/share/traffic-analysis"
export TRAFFIC_ANALYSIS_MODEL="/path/to/local-model.gguf"
export TRAFFIC_ANALYSIS_LLAMA_SERVER="/path/to/llama-server"
# Optional for a custom model: pin its trusted SHA-256 before installation.
# export TRAFFIC_ANALYSIS_MODEL_SHA256='0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef'
tools/install-user-service.sh
```

Use the same values when installing or updating the deployment. Paths may point
to any operator-selected local directories or Research mount locations, provided
the TRANS checkpoint is readable on the configured CIFS source and the snapshot
parent is writable on that source. `TRAFFIC_ANALYSIS_DATA_HOME` must be a
private local directory; it stores the lock, manual-cycle log, and other local
runtime state. Both the generated systemd `ReadWritePaths` and the manual action
use these configured locations.

The installer verifies that the checkpoint and snapshot are on the configured
CIFS source, checks the Research output mount is writable, and makes the
generated service unit's `ReadWritePaths` match the configured data home and
snapshot directory. It retains the known Qwen model SHA-256 pin when using the
default Qwen path. A custom local GGUF may supply `TRAFFIC_ANALYSIS_MODEL_SHA256`;
without a custom pin, the installer reports that it is trusting the operator's
existing local file. It never downloads weights or stores them in Git. The
generated unit keeps `ProtectSystem=strict`, `ProtectHome=read-only`, and
`RestrictAddressFamilies=AF_UNIX` regardless of configured paths.
Before those checks, the installer runs
`tools/ensure-research-mount-writable.py` against the configured Research path.
It verifies the exact mount source and current effective mount, asks `sudo` to
remount only when that mount is read-only, and confirms write access with a
temporary file that it removes. It never remounts an unexpected source or
changes file ownership or permissions. If the mount already reports read-write
but the probe fails, the installer stops without attempting a privileged
remount.
The `Run Traffic Analysis Now` desktop action runs the same preflight before
starting a manual cycle, so a read-only mount can be remounted from the
interactive terminal before the database publisher starts.
For manual runs, the installer also writes a separate mode-0600 allowlisted
configuration under the mode-0700 `~/.config/traffic-analysis` directory. The
manual runner parses this file as data, checks its permissions and ownership,
and never sources or evaluates it. Its private log is written under the same
configured data home as the timer's lock and runtime state.

Every neural result remains pending review. Arch Notebook views include a
classification only after both the source text and analysis have been reviewed
and accepted. Connection rows also require an accepted explicit contact
candidate.
