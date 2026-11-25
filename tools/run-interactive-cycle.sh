#!/usr/bin/env bash
set -euo pipefail

readonly repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly config_dir="${HOME}/.config/traffic-analysis"
readonly manual_environment_file="${config_dir}/manual-environment"
readonly default_data_home="${HOME}/.local/share/traffic-analysis"
umask 0077
if [[ -e "${manual_environment_file}" || -L "${manual_environment_file}" ]]; then
  parsed_config="$(mktemp "${config_dir}/.manual-values.XXXXXX")"
  chmod 0600 "${parsed_config}"
  if ! python3 - "${config_dir}" "${manual_environment_file}" "${parsed_config}" <<'PY'
import os
from pathlib import Path
import shlex
import stat
import sys

config_dir, config_path, output_path = map(Path, sys.argv[1:])
allowed = {
    "TRAFFIC_ANALYSIS_RESEARCH_SOURCE",
    "TRAFFIC_ANALYSIS_TRANS_CHECKPOINT",
    "TRAFFIC_ANALYSIS_SNAPSHOT",
    "TRAFFIC_ANALYSIS_DATA_HOME",
    "TRAFFIC_ANALYSIS_MODEL",
    "TRAFFIC_ANALYSIS_LLAMA_SERVER",
}
directory_info = config_dir.stat(follow_symlinks=False)
file_info = config_path.stat(follow_symlinks=False)
if not stat.S_ISDIR(directory_info.st_mode) or directory_info.st_mode & 0o077 or directory_info.st_uid != os.getuid():
    raise SystemExit("manual configuration directory must be private and user-owned")
if not stat.S_ISREG(file_info.st_mode) or file_info.st_mode & 0o077 or file_info.st_uid != os.getuid():
    raise SystemExit("manual configuration must be private, regular, and user-owned")
seen = set()
for number, raw_line in enumerate(config_path.read_text(encoding="utf-8").splitlines(), 1):
    fields = shlex.split(raw_line, comments=False, posix=True)
    if len(fields) != 2 or fields[0] not in allowed or fields[0] in seen:
        raise SystemExit(f"invalid manual configuration entry on line {number}")
    if any(character in fields[1] for character in ("\x00", "\n", "\r")):
        raise SystemExit(f"invalid value on line {number}")
    seen.add(fields[0])
    with output_path.open("ab") as output:
        output.write(fields[0].encode() + b"=" + fields[1].encode() + b"\0")
PY
  then
    rm -f -- "${parsed_config}"
    exit 1
  fi
  while IFS= read -r -d '' assignment; do
    export "${assignment%%=*}=${assignment#*=}"
  done < "${parsed_config}"
  rm -f -- "${parsed_config}"
fi
readonly data_home="${TRAFFIC_ANALYSIS_DATA_HOME:-${default_data_home}}"
readonly run_log="${data_home}/interactive-cycle.log"
mkdir -p -m 0700 "${data_home}"
chmod 0700 "${data_home}"
touch "${run_log}"
chmod 0600 "${run_log}"
exec > >(tee -a "${run_log}") 2>&1
printf '\nTraffic Analysis manual cycle started at %s\n' "$(date --iso-8601=seconds)"
exec /usr/bin/python3 "${repo}/tools/run_corpus_cycle.py"
