#!/usr/bin/env bash
set -euo pipefail

readonly repo="${HOME}/github/traffic_analysis"
readonly expected_source="${TRAFFIC_ANALYSIS_RESEARCH_SOURCE:-//archive.local/research}"
readonly checkpoint="${HOME}/Research.Databases/audio_ingest/transcription.sqlite.gz"
readonly snapshot_dir="${HOME}/Research.Databases/traffic_analysis"
readonly model="${HOME}/.local/share/llama.cpp/cache/models--lmstudio-community--Qwen3.5-9B-GGUF/snapshots/1379f25c6b505a3fc737bd7818cb09389cf807c1/Qwen3.5-9B-Q4_K_M.gguf"
readonly expected_model_sha256="cd76ec205963b3b33350093e6904d9de16c4e666fd104e1f632d25c7f15f2a13"
readonly unit_dir="${HOME}/.config/systemd/user"
readonly data_home="${HOME}/.local/share/traffic-analysis"

die() {
  printf 'Traffic Analysis deployment: %s\n' "$1" >&2
  exit 1
}

verify_mount() {
  local path="$1"
  local access="$2"
  local mount_source mount_type mount_options
  mount_source="$(findmnt -n -o SOURCE -T "${path}")"
  mount_type="$(findmnt -n -o FSTYPE -T "${path}")"
  mount_options="$(findmnt -n -o OPTIONS -T "${path}")"
  [[ "${mount_source,,}" == "${expected_source,,}" ]] || die "Research storage source does not match the configured SMB share"
  [[ "${mount_type}" == "cifs" ]] || die "Research storage is not a CIFS mount"
  if [[ "${access}" == "write" ]]; then
    [[ ",${mount_options}," == *,rw,* && ",${mount_options}," != *,ro,* ]] || die "Research analysis storage is not mounted read-write"
  fi
}

verify_mount "${checkpoint}" "read"
verify_mount "${snapshot_dir}" "write"

[[ -r "${checkpoint}" ]] || die "TRANS checkpoint is not readable: ${checkpoint}"
[[ -r "${model}" ]] || die "installed Qwen GGUF is not readable: ${model}"
[[ -x "${HOME}/.local/bin/llama-server" ]] || die "llama-server is not executable"

mkdir -p -m 0700 "${snapshot_dir}" "${data_home}"
chmod 0700 "${data_home}"
[[ -w "${snapshot_dir}" ]] || die "Research snapshot directory is not writable"

actual_model_sha256="$(sha256sum "${model}" | cut -d ' ' -f 1)"
[[ "${actual_model_sha256}" == "${expected_model_sha256}" ]] || die "installed Qwen model checksum does not match the pinned artifact"

mkdir -p -m 0700 "${unit_dir}"
ln -sfn "${repo}/systemd/traffic-analysis.service" "${unit_dir}/traffic-analysis.service"
ln -sfn "${repo}/systemd/traffic-analysis.timer" "${unit_dir}/traffic-analysis.timer"
systemctl --user daemon-reload
systemctl --user enable --now traffic-analysis.timer
systemctl --user start --no-block traffic-analysis.service
systemctl --user --no-pager status traffic-analysis.service traffic-analysis.timer
