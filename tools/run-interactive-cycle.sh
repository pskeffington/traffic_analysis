#!/usr/bin/env bash
set -euo pipefail

readonly repo="${HOME}/github/traffic_analysis"
readonly data_home="${HOME}/.local/share/traffic-analysis"
readonly run_log="${data_home}/interactive-cycle.log"
umask 0077
mkdir -p -m 0700 "${data_home}"
chmod 0700 "${data_home}"
touch "${run_log}"
chmod 0600 "${run_log}"
exec > >(tee -a "${run_log}") 2>&1
printf '\\nTraffic Analysis manual cycle started at %s\\n' "$(date --iso-8601=seconds)"
exec /usr/bin/python3 "${repo}/tools/run_corpus_cycle.py"
