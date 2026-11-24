#!/usr/bin/env bash
set -euo pipefail

readonly repo="${HOME}/github/traffic_analysis"
exec /usr/bin/python3 "${repo}/tools/run_corpus_cycle.py"
