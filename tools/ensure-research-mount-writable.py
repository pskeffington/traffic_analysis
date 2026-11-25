#!/usr/bin/env python3
"""Ensure the configured Research mount is writable before service deployment."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def unescape_mount_field(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), value)


def selected_mount(path: Path) -> dict[str, object]:
    target = path.resolve(strict=True)
    selected: tuple[int, int, Path, str, str, set[str], set[str]] | None = None
    for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
        fields = line.split()
        try:
            separator = fields.index("-")
            mount_id = int(fields[0])
            mountpoint = Path(unescape_mount_field(fields[4]))
            if mountpoint != target and mountpoint not in target.parents:
                continue
            filesystem = fields[separator + 1].casefold()
            source = unescape_mount_field(fields[separator + 2])
            mount_options = set(fields[5].split(","))
            super_options = set(fields[separator + 3].split(","))
        except (IndexError, ValueError):
            continue
        candidate = (len(mountpoint.parts), mount_id, mountpoint, source,
                     filesystem, mount_options, super_options)
        if selected is None or candidate[:2] > selected[:2]:
            selected = candidate
    if selected is None:
        raise RuntimeError("Research_mount_not_found")
    _, mount_id, mountpoint, source, filesystem, mount_options, super_options = selected
    return {
        "mount_id": mount_id,
        "mountpoint": mountpoint,
        "source": source,
        "filesystem": filesystem,
        "mount_options": mount_options,
        "super_options": super_options,
    }


def writable(mount: dict[str, object]) -> bool:
    mount_options = mount["mount_options"]
    super_options = mount["super_options"]
    assert isinstance(mount_options, set) and isinstance(super_options, set)
    return (
        "rw" in mount_options and "ro" not in mount_options and
        "rw" in super_options and "ro" not in super_options
    )


def write_probe(directory: Path) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".traffic-analysis-write-check-", dir=directory)
    try:
        os.write(descriptor, b"traffic-analysis writable mount check\n")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
        os.unlink(name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mount_path", type=Path, help="path inside the Research volume")
    parser.add_argument("expected_source", help="exact source configured for the Research mount")
    args = parser.parse_args()
    probe_directory = args.mount_path.resolve(strict=True)
    if not probe_directory.is_dir():
        raise RuntimeError("Research_write_probe_path_is_not_a_directory")
    expected_source = args.expected_source.casefold().rstrip("/")
    if not expected_source or "\n" in expected_source or "\r" in expected_source:
        raise RuntimeError("Research_mount_source_invalid")

    mount = selected_mount(args.mount_path)
    source = str(mount["source"]).casefold().rstrip("/")
    if source != expected_source:
        raise RuntimeError("Research_mount_source_does_not_match_configuration")
    if mount["filesystem"] != "cifs":
        raise RuntimeError("Research_mount_is_not_CIFS")

    if not writable(mount):
        target = str(mount["mountpoint"])
        print("Research mount is read-only; requesting sudo to remount it read-write.", flush=True)
        subprocess.run(
            ["sudo", "mount", "-o", "remount,rw", target],
            stdin=None, check=True,
        )
        mount = selected_mount(args.mount_path)
        if (str(mount["source"]).casefold().rstrip("/") != expected_source or
                mount["filesystem"] != "cifs" or not writable(mount)):
            raise RuntimeError("Research_mount_remains_read_only_after_remount")

    write_probe(probe_directory)
    print(json.dumps({
        "status": "PASS",
        "mount_id": mount["mount_id"],
        "mountpoint": str(mount["mountpoint"]),
        "filesystem": mount["filesystem"],
        "write_probe": "created_and_removed",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
        print(f"Research mount preflight: {exc}", file=sys.stderr)
        raise SystemExit(2)
