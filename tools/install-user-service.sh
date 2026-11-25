#!/usr/bin/env bash
set -euo pipefail

readonly repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly default_research_source="//archive.local/research"
readonly default_checkpoint="${HOME}/.local/share/trans/audio-sql/trans-audio.sqlite.gz"
readonly default_snapshot="${HOME}/Research.Databases/research.sqlite"
readonly default_model="${HOME}/.local/share/llama.cpp/cache/models--lmstudio-community--Qwen3.5-9B-GGUF/snapshots/1379f25c6b505a3fc737bd7818cb09389cf807c1/Qwen3.5-9B-Q4_K_M.gguf"
readonly default_model_sha256="cd76ec205963b3b33350093e6904d9de16c4e666fd104e1f632d25c7f15f2a13"

expand_home() {
  case "$1" in
    '~') printf '%s' "${HOME}" ;;
    '~/'*) printf '%s/%s' "${HOME}" "${1#~/}" ;;
    *) printf '%s' "$1" ;;
  esac
}

absolute_path() {
  local name="$1"
  local value="$2"
  [[ "${value}" == /* ]] || die "${name} must be an absolute path"
  printf '%s' "${value%/}"
}

die() {
  printf 'Traffic Analysis deployment: %s\n' "$1" >&2
  exit 1
}

readonly expected_source="${TRAFFIC_ANALYSIS_RESEARCH_SOURCE:-${default_research_source}}"
readonly checkpoint="$(absolute_path TRAFFIC_ANALYSIS_TRANS_CHECKPOINT "$(expand_home "${TRAFFIC_ANALYSIS_TRANS_CHECKPOINT:-${default_checkpoint}}")")"
readonly snapshot="$(absolute_path TRAFFIC_ANALYSIS_SNAPSHOT "$(expand_home "${TRAFFIC_ANALYSIS_SNAPSHOT:-${default_snapshot}}")")"
readonly snapshot_dir="$(dirname -- "${snapshot}")"
readonly model="$(absolute_path TRAFFIC_ANALYSIS_MODEL "$(expand_home "${TRAFFIC_ANALYSIS_MODEL:-${default_model}}")")"
readonly server="$(absolute_path TRAFFIC_ANALYSIS_LLAMA_SERVER "$(expand_home "${TRAFFIC_ANALYSIS_LLAMA_SERVER:-${HOME}/.local/bin/llama-server}")")"
readonly data_home="$(absolute_path TRAFFIC_ANALYSIS_DATA_HOME "$(expand_home "${TRAFFIC_ANALYSIS_DATA_HOME:-${HOME}/.local/share/traffic-analysis}")")"
readonly model_sha256="${TRAFFIC_ANALYSIS_MODEL_SHA256:-}"

readonly unit_dir="${HOME}/.config/systemd/user"
readonly config_dir="${HOME}/.config/traffic-analysis"
readonly environment_file="${config_dir}/environment"
readonly manual_environment_file="${config_dir}/manual-environment"
readonly service_file="${unit_dir}/traffic-analysis.service"
readonly timer_file="${unit_dir}/traffic-analysis.timer"
readonly service_template="${repo}/systemd/traffic-analysis.service.in"
readonly timer_source="${repo}/systemd/traffic-analysis.timer"
readonly desktop_template="${repo}/desktop/traffic-analysis-now.desktop.in"
readonly deploy_desktop_template="${repo}/desktop/traffic-analysis-deploy.desktop.in"

verify_mount() {
  local path="$1"
  local access="$2"
  local mount_source mount_type mount_options
  mount_source="$(findmnt -n -o SOURCE -T "${path}" | tail -n 1)" || die "cannot determine mount source for ${path}"
  mount_type="$(findmnt -n -o FSTYPE -T "${path}" | tail -n 1)" || die "cannot determine filesystem type for ${path}"
  mount_options="$(findmnt -n -o OPTIONS -T "${path}" | tail -n 1)" || die "cannot determine mount options for ${path}"
  [[ "${mount_source,,}" == "${expected_source,,}" ]] || die "Research storage source does not match TRAFFIC_ANALYSIS_RESEARCH_SOURCE"
  [[ "${mount_type}" == "cifs" ]] || die "Research storage is not a CIFS mount"
  if [[ "${access}" == "write" ]]; then
    [[ ",${mount_options}," == *,rw,* && ",${mount_options}," != *,ro,* ]] || die "Research analysis storage is not mounted read-write"
  fi
}

require_absolute() {
  local name="$1"
  local value="$2"
  [[ "${value}" == /* ]] || die "${name} must be an absolute path"
  [[ "${value}" != *$'\n'* && "${value}" != *$'\r'* ]] || die "${name} must not contain a newline"
}

for item in \
  "TRAFFIC_ANALYSIS_TRANS_CHECKPOINT:${checkpoint}" \
  "TRAFFIC_ANALYSIS_SNAPSHOT:${snapshot}" \
  "TRAFFIC_ANALYSIS_DATA_HOME:${data_home}" \
  "TRAFFIC_ANALYSIS_MODEL:${model}" \
  "TRAFFIC_ANALYSIS_LLAMA_SERVER:${server}"; do
  require_absolute "${item%%:*}" "${item#*:}"
done
[[ -n "${expected_source}" && "${expected_source}" != *$'\n'* ]] || die "TRAFFIC_ANALYSIS_RESEARCH_SOURCE must be a non-empty single-line mount source"
[[ "${expected_source}" != *$'\r'* ]] || die "TRAFFIC_ANALYSIS_RESEARCH_SOURCE must be a non-empty single-line mount source"
[[ -f "${service_template}" ]] || die "service template is missing from the repository"
[[ -f "${timer_source}" ]] || die "timer unit is missing from the repository"
[[ -f "${desktop_template}" ]] || die "manual desktop template is missing from the repository"
[[ -f "${deploy_desktop_template}" ]] || die "deploy desktop template is missing from the repository"

mount_probe="${snapshot_dir}"
while [[ ! -e "${mount_probe}" && "${mount_probe}" != "/" ]]; do
  mount_probe="$(dirname -- "${mount_probe}")"
done
verify_mount "${mount_probe}" "write"

[[ -f "${checkpoint}" && ! -L "${checkpoint}" && -r "${checkpoint}" ]] || die "TRANS checkpoint must be a readable regular local file"
checkpoint_resolved="$(realpath -e -- "${checkpoint}")" || die "cannot resolve TRANS checkpoint path"
[[ "${checkpoint_resolved}" == "${checkpoint}" ]] || die "TRANS checkpoint path must not resolve through a symlink"
checkpoint_fstype="$(findmnt -n -o FSTYPE -T "${checkpoint}" | tail -n 1)" || die "cannot determine filesystem for TRANS checkpoint"
case "${checkpoint_fstype}" in
  cifs|smb3|nfs|nfs4|sshfs|fuse.sshfs) die "TRANS checkpoint must be on local storage" ;;
esac
checkpoint_mode="$(stat -c '%a' "${checkpoint}")" || die "cannot inspect TRANS checkpoint permissions"
(( (8#${checkpoint_mode} & 077) == 0 )) || die "TRANS checkpoint must be owner-only"
checkpoint_uid="$(stat -c '%u' "${checkpoint}")" || die "cannot inspect TRANS checkpoint owner"
[[ "${checkpoint_uid}" == "$(id -u)" ]] || die "TRANS checkpoint must be owned by the current user"

# Runtime state contains locks and operator logs; keep it on a local filesystem.
data_probe="${data_home}"
while [[ ! -e "${data_probe}" && "${data_probe}" != "/" ]]; do
  data_probe="$(dirname -- "${data_probe}")"
done
data_fstype="$(findmnt -n -o FSTYPE -T "${data_probe}")" || die "cannot determine runtime data filesystem for ${data_home}"
case "${data_fstype}" in
  cifs|smb3|nfs|nfs4|sshfs|fuse.sshfs) die "TRAFFIC_ANALYSIS_DATA_HOME must be on a local filesystem" ;;
esac
[[ "${data_home}" != "${snapshot}" && "${snapshot}" != "${data_home}"/* ]] || die "TRAFFIC_ANALYSIS_DATA_HOME must be separate from the Research database"

[[ -r "${checkpoint}" ]] || die "TRANS checkpoint is not readable"
[[ -r "${model}" ]] || die "configured local GGUF model is not readable"
[[ -x "${server}" ]] || die "configured llama-server is not executable"

expected_model_sha256="${model_sha256}"
if [[ -z "${expected_model_sha256}" && "${model}" == "${default_model}" ]]; then
  expected_model_sha256="${default_model_sha256}"
fi
if [[ -n "${expected_model_sha256}" ]]; then
  [[ "${expected_model_sha256}" =~ ^[[:xdigit:]]{64}$ ]] || die "TRAFFIC_ANALYSIS_MODEL_SHA256 must contain 64 hexadecimal characters"
  actual_model_sha256="$(sha256sum "${model}" | cut -d ' ' -f 1)"
  [[ "${actual_model_sha256,,}" == "${expected_model_sha256,,}" ]] || die "local model SHA-256 does not match TRAFFIC_ANALYSIS_MODEL_SHA256"
else
  printf 'Traffic Analysis deployment: no model SHA-256 pin supplied; using the existing local model as configured\n' >&2
fi

mkdir -p -m 0700 "${snapshot_dir}" "${data_home}" "${unit_dir}" "${config_dir}"
chmod 0700 "${data_home}" "${config_dir}"
verify_mount "${snapshot_dir}" "write"
[[ -w "${snapshot_dir}" ]] || die "Research snapshot directory is not writable"
snapshot_mode="$(stat -c '%a' "${snapshot_dir}")" || die "cannot inspect Research snapshot directory permissions"
[[ "${snapshot_mode}" =~ ^[0-7]{3,4}$ ]] || die "Research snapshot directory permissions are unavailable"
(( (8#${snapshot_mode} & 077) == 0 )) || die "Research snapshot directory must be private to the owner"

environment_tmp="$(mktemp "${config_dir}/.environment.XXXXXX")"
manual_environment_tmp="$(mktemp "${config_dir}/.manual-environment.XXXXXX")"
service_tmp="$(mktemp "${unit_dir}/.traffic-analysis.service.XXXXXX")"
timer_tmp="$(mktemp "${unit_dir}/.traffic-analysis.timer.XXXXXX")"
rm -f -- "${timer_tmp}"
environment_backup=""
manual_environment_backup=""
service_backup=""
timer_backup=""
had_environment=0
had_manual_environment=0
had_service=0
had_timer=0
commit_started=0
commit_complete=0
activation_started=0

restore_file() {
  local target="$1"
  local backup="$2"
  local had_old="$3"
  if [[ "${had_old}" == "1" ]]; then
    rm -f -- "${target}"
    mv -- "${backup}" "${target}"
  else
    rm -f -- "${target}"
  fi
}

cleanup() {
  local status=$?
  trap - EXIT
  rm -f -- "${environment_tmp}" "${service_tmp}" "${timer_tmp}"
  rm -f -- "${manual_environment_tmp}"
  if [[ "${commit_started}" == "1" && "${commit_complete}" == "0" ]]; then
    restore_file "${environment_file}" "${environment_backup}" "${had_environment}"
    restore_file "${manual_environment_file}" "${manual_environment_backup}" "${had_manual_environment}"
    restore_file "${service_file}" "${service_backup}" "${had_service}"
    restore_file "${timer_file}" "${timer_backup}" "${had_timer}"
    systemctl --user daemon-reload >/dev/null 2>&1 || true
  elif [[ "${activation_started}" == "1" && "${commit_complete}" == "1" && "${status}" != "0" ]]; then
    systemctl --user disable --now traffic-analysis.timer >/dev/null 2>&1 || true
    restore_file "${environment_file}" "${environment_backup}" "${had_environment}"
    restore_file "${manual_environment_file}" "${manual_environment_backup}" "${had_manual_environment}"
    restore_file "${service_file}" "${service_backup}" "${had_service}"
    restore_file "${timer_file}" "${timer_backup}" "${had_timer}"
    if [[ -n "${desktop_backup_dir:-}" && -d "${desktop_backup_dir}" ]]; then
      rm -f -- "${HOME}/Desktop/Deploy Traffic Analysis.desktop" "${HOME}/Desktop/Run Traffic Analysis Now.desktop"
      if [[ -f "${desktop_backup_dir}/manifest" ]]; then
        while IFS= read -r -d '' target && IFS= read -r -d '' backup; do
          mv -- "${backup}" "${target}"
        done < "${desktop_backup_dir}/manifest"
      fi
      rm -f -- "${desktop_backup_dir}/manifest" "${desktop_backup_dir}/Deploy Traffic Analysis.desktop" "${desktop_backup_dir}/Run Traffic Analysis Now.desktop"
      rmdir -- "${desktop_backup_dir}" 2>/dev/null || true
    fi
    systemctl --user daemon-reload >/dev/null 2>&1 || true
  else
    [[ -z "${environment_backup}" ]] || rm -f -- "${environment_backup}"
    [[ -z "${manual_environment_backup}" ]] || rm -f -- "${manual_environment_backup}"
    [[ -z "${service_backup}" ]] || rm -f -- "${service_backup}"
    [[ -z "${timer_backup}" ]] || rm -f -- "${timer_backup}"
  fi
  exit "${status}"
}
trap cleanup EXIT

quote_environment_value() {
  local value="$1"
  [[ "${value}" != *$'\n'* && "${value}" != *$'\r'* ]] || die "environment values must not contain newlines"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '"%s"' "${value}"
}

{
  printf 'TRAFFIC_ANALYSIS_RESEARCH_SOURCE=%s\n' "$(quote_environment_value "${expected_source}")"
  printf 'TRAFFIC_ANALYSIS_TRANS_CHECKPOINT=%s\n' "$(quote_environment_value "${checkpoint}")"
  printf 'TRAFFIC_ANALYSIS_SNAPSHOT=%s\n' "$(quote_environment_value "${snapshot}")"
  printf 'TRAFFIC_ANALYSIS_DATA_HOME=%s\n' "$(quote_environment_value "${data_home}")"
  printf 'TRAFFIC_ANALYSIS_MODEL=%s\n' "$(quote_environment_value "${model}")"
  printf 'TRAFFIC_ANALYSIS_LLAMA_SERVER=%s\n' "$(quote_environment_value "${server}")"
} > "${environment_tmp}"
chmod 0600 "${environment_tmp}"

TRAFFIC_MANUAL_CONFIG_OUTPUT="${manual_environment_tmp}" \
TRAFFIC_MANUAL_CONFIG_VALUES="${expected_source}"$'\n'"${checkpoint}"$'\n'"${snapshot}"$'\n'"${data_home}"$'\n'"${model}"$'\n'"${server}" \
python3 - <<'PY'
import os
from pathlib import Path
import shlex

names = (
    "TRAFFIC_ANALYSIS_RESEARCH_SOURCE",
    "TRAFFIC_ANALYSIS_TRANS_CHECKPOINT",
    "TRAFFIC_ANALYSIS_SNAPSHOT",
    "TRAFFIC_ANALYSIS_DATA_HOME",
    "TRAFFIC_ANALYSIS_MODEL",
    "TRAFFIC_ANALYSIS_LLAMA_SERVER",
)
values = os.environ["TRAFFIC_MANUAL_CONFIG_VALUES"].split("\n")
if len(values) != len(names) or any("\n" in value or "\r" in value for value in values):
    raise SystemExit("invalid manual configuration values")
Path(os.environ["TRAFFIC_MANUAL_CONFIG_OUTPUT"]).write_text(
    "".join(f"{name} {shlex.quote(value)}\n" for name, value in zip(names, values)),
    encoding="utf-8",
)
PY
chmod 0600 "${manual_environment_tmp}"

TRAFFIC_SERVICE_TEMPLATE="${service_template}" \
TRAFFIC_SERVICE_OUTPUT="${service_tmp}" \
TRAFFIC_SERVICE_REPOSITORY="${repo}" \
TRAFFIC_SERVICE_RUNNER="${repo}/tools/run_corpus_cycle.py" \
TRAFFIC_SERVICE_DATA_HOME="${data_home}" \
TRAFFIC_SERVICE_SNAPSHOT_DIR="${snapshot_dir}" \
python3 - <<'PY'
import os
from pathlib import Path

def unit_quote(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise SystemExit("unit paths must not contain newlines")
    value = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return f'"{value}"'

template = Path(os.environ["TRAFFIC_SERVICE_TEMPLATE"]).read_text(encoding="utf-8")
values = {
    "@WORKING_DIRECTORY@": unit_quote(os.environ["TRAFFIC_SERVICE_REPOSITORY"]),
    "@RUNNER@": unit_quote(os.environ["TRAFFIC_SERVICE_RUNNER"]),
    "@DATA_HOME@": unit_quote(os.environ["TRAFFIC_SERVICE_DATA_HOME"]),
    "@SNAPSHOT_DIR@": unit_quote(os.environ["TRAFFIC_SERVICE_SNAPSHOT_DIR"]),
}
for token, value in values.items():
    template = template.replace(token, value)
if any(token in template for token in values):
    raise SystemExit("unexpanded unit-template token remains")
Path(os.environ["TRAFFIC_SERVICE_OUTPUT"]).write_text(template, encoding="utf-8")
PY
chmod 0600 "${service_tmp}"

if [[ -e "${environment_file}" || -L "${environment_file}" ]]; then
  environment_backup="$(mktemp "${config_dir}/.environment.previous.XXXXXX")"
  rm -f -- "${environment_backup}"
  cp -a -- "${environment_file}" "${environment_backup}"
  had_environment=1
fi
if [[ -e "${manual_environment_file}" || -L "${manual_environment_file}" ]]; then
  manual_environment_backup="$(mktemp "${config_dir}/.manual-environment.previous.XXXXXX")"
  rm -f -- "${manual_environment_backup}"
  cp -a -- "${manual_environment_file}" "${manual_environment_backup}"
  had_manual_environment=1
fi
if [[ -e "${service_file}" || -L "${service_file}" ]]; then
  service_backup="$(mktemp "${unit_dir}/.service.previous.XXXXXX")"
  rm -f -- "${service_backup}"
  cp -a -- "${service_file}" "${service_backup}"
  had_service=1
fi
if [[ -e "${timer_file}" || -L "${timer_file}" ]]; then
  timer_backup="$(mktemp "${unit_dir}/.timer.previous.XXXXXX")"
  rm -f -- "${timer_backup}"
  cp -a -- "${timer_file}" "${timer_backup}"
  had_timer=1
fi

ln -s -- "${timer_source}" "${timer_tmp}"
commit_started=1
mv -f -- "${environment_tmp}" "${environment_file}"
mv -f -- "${manual_environment_tmp}" "${manual_environment_file}"
mv -f -- "${service_tmp}" "${service_file}"
mv -Tf -- "${timer_tmp}" "${timer_file}"
if ! systemctl --user daemon-reload; then
  die "systemd user manager rejected the generated unit; previous unit and config will be restored"
fi
commit_complete=1

activation_started=1
if [[ -d "${HOME}/Desktop" ]]; then
  desktop_backup_dir="$(mktemp -d "${HOME}/Desktop/.traffic-analysis-backup.XXXXXX")"
  desktop_deploy="${HOME}/Desktop/Deploy Traffic Analysis.desktop"
  desktop_manual="${HOME}/Desktop/Run Traffic Analysis Now.desktop"
  for target in "${desktop_deploy}" "${desktop_manual}"; do
    if [[ -e "${target}" || -L "${target}" ]]; then
      backup="${desktop_backup_dir}/$(basename -- "${target}")"
      cp -a -- "${target}" "${backup}"
      printf '%s\0%s\0' "${target}" "${backup}" >> "${desktop_backup_dir}/manifest"
    fi
  done
  python3 - "${repo}" "${HOME}/Desktop" <<'PY'
from pathlib import Path
import os
import sys

repository = Path(sys.argv[1])
desktop_dir = Path(sys.argv[2])
actions = (
    (repository / "desktop/traffic-analysis-deploy.desktop.in", desktop_dir / "Deploy Traffic Analysis.desktop"),
    (repository / "desktop/traffic-analysis-now.desktop.in", desktop_dir / "Run Traffic Analysis Now.desktop"),
)
escaped = str(repository).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
for template_path, output_path in actions:
    template = template_path.read_text(encoding="utf-8")
    template = template.replace("@REPOSITORY@", f'"{escaped}"')
    if "@REPOSITORY@" in template:
        raise SystemExit(f"unexpanded desktop-template token remains in {template_path.name}")
    temporary = output_path.with_name(f".{output_path.name}.tmp")
    temporary.write_text(template, encoding="utf-8")
    temporary.chmod(0o755)
    os.replace(temporary, output_path)
PY
  desktop_created=1
fi

if ! systemctl --user enable --now traffic-analysis.timer; then
  die "could not enable the hourly timer; deployment files will be rolled back"
fi
if ! systemctl --user start --no-block traffic-analysis.service; then
  die "could not start the initial cycle; deployment files will be rolled back"
fi
systemctl --user --no-pager status traffic-analysis.service traffic-analysis.timer
rm -f -- "${environment_backup}" "${manual_environment_backup}" "${service_backup}" "${timer_backup}"
if [[ -n "${desktop_backup_dir:-}" ]]; then
  rm -f -- "${desktop_backup_dir}/manifest" "${desktop_backup_dir}/Deploy Traffic Analysis.desktop" "${desktop_backup_dir}/Run Traffic Analysis Now.desktop"
  rmdir -- "${desktop_backup_dir}"
fi
