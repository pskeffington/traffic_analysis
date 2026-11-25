#!/usr/bin/env python3
"""Retire split Research files only after exact local backups and DB checks."""
from __future__ import annotations

import fcntl
import ctypes
import errno
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import sqlite3
import stat
import subprocess
import sys
import tempfile
import uuid

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "src"))

from run_corpus_cycle import (  # noqa: E402
    ANALYSIS_HOME,
    DEFAULT_ANALYSIS_SNAPSHOT,
    DEFAULT_TRANS_CHECKPOINT,
    _mount_for,
    _require_research_mount,
    configured_path,
    require_local_checkpoint,
    verify_research_mount,
)


def file_sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def rename_noreplace(directory_fd: int, source_name: str, destination_name: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise RuntimeError("atomic_no_replace_rename_unavailable")
    renameat2.argtypes = (
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    result = renameat2(
        directory_fd, os.fsencode(source_name), directory_fd,
        os.fsencode(destination_name), 1,  # RENAME_NOREPLACE
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), destination_name)


def table_digest(db: sqlite3.Connection, table: str) -> tuple[tuple[str, ...], int, str]:
    info = list(db.execute(f'PRAGMA table_info("{table}")'))
    columns = tuple(row[1] for row in info)
    primary = tuple(row[1] for row in sorted((r for r in info if r[5]), key=lambda r: r[5]))
    if not columns or not primary:
        raise RuntimeError("legacy_table_has_no_columns_or_primary_key")
    digest = hashlib.sha256()
    count = 0
    select = ",".join(f'"{column}"' for column in columns)
    order = ",".join(f'"{column}"' for column in primary)
    for row in db.execute(f'SELECT {select} FROM "{table}" ORDER BY {order}'):
        digest.update(json.dumps(tuple(row), ensure_ascii=False,
                                 separators=(",", ":")).encode())
        digest.update(b"\n")
        count += 1
    return columns, count, digest.hexdigest()


def compare_table_data(legacy: sqlite3.Connection, combined: sqlite3.Connection,
                       table: str) -> int:
    legacy_info = list(legacy.execute(f'PRAGMA table_info("{table}")'))
    combined_info = list(combined.execute(f'PRAGMA table_info("{table}")'))
    legacy_cols = {r[1] for r in legacy_info}
    combined_cols = {r[1] for r in combined_info}
    if not legacy_cols.issubset(combined_cols):
        raise RuntimeError("combined_database_missing_legacy_table_columns")
    columns = sorted(legacy_cols & combined_cols)
    legacy_pk = [r[1] for r in sorted((r for r in legacy_info if r[5]), key=lambda r: r[5])]
    if not columns or not legacy_pk or not set(legacy_pk).issubset(columns):
        raise RuntimeError("legacy_analysis_schema_cannot_be_compared")
    projection = ",".join(f'"{column}"' for column in columns)
    order = ",".join(f'"{column}"' for column in legacy_pk)
    old_rows = legacy.execute(
        f'SELECT {projection} FROM "{table}" ORDER BY {order}'
    )
    new_rows = combined.execute(
        f'SELECT {projection} FROM "{table}" ORDER BY {order}'
    )
    old_count = 0
    for old, new in zip(old_rows, new_rows, strict=True):
        if tuple(old) != tuple(new):
            raise RuntimeError("legacy_analysis_rows_differ_from_combined_database")
        old_count += 1
    return old_count


def copy_share_file(source: Path, destination: Path) -> str:
    directory_fd = os.open(
        source.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
        getattr(os, "O_CLOEXEC", 0)
    )
    try:
        _require_research_mount(source.parent, directory_fd)
        descriptor = os.open(
            source.name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb") as raw:
            _require_research_mount(source, raw.fileno())
            with destination.open("xb") as backup:
                os.fchmod(backup.fileno(), 0o600)
                while block := raw.read(1024 * 1024):
                    digest.update(block)
                    backup.write(block)
                backup.flush()
                os.fsync(backup.fileno())
            _require_research_mount(source, raw.fileno())
        actual = file_sha256(destination)
        if actual != digest.hexdigest():
            raise RuntimeError("local_rollback_copy_digest_mismatch")
        backup_directory_fd = os.open(
            destination.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        )
        try:
            os.fsync(backup_directory_fd)
        finally:
            os.close(backup_directory_fd)
        return actual
    finally:
        os.close(directory_fd)


def unlink_verified_share_file(source: Path, expected_digest: str) -> None:
    directory_fd = os.open(
        source.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
        getattr(os, "O_CLOEXEC", 0)
    )
    try:
        _require_research_mount(source.parent, directory_fd)
        descriptor = os.open(
            source.name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        with os.fdopen(descriptor, "rb") as original:
            _require_research_mount(source, original.fileno())
            if not stat.S_ISREG(os.fstat(original.fileno()).st_mode):
                raise RuntimeError("legacy_research_file_changed_type")
            digest = hashlib.file_digest(original, "sha256").hexdigest()
            _require_research_mount(source, original.fileno())
        if digest != expected_digest:
            raise RuntimeError("legacy_research_file_changed_before_quarantine")
        quarantine_name = f".{source.name}.retire-{uuid.uuid4().hex}"
        rename_noreplace(directory_fd, source.name, quarantine_name)
        quarantine_fd = os.open(
            quarantine_name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        with os.fdopen(quarantine_fd, "rb") as quarantined:
            _require_research_mount(source.parent / quarantine_name, quarantined.fileno())
            quarantine_digest = hashlib.file_digest(quarantined, "sha256").hexdigest()
        if quarantine_digest != expected_digest:
            try:
                rename_noreplace(directory_fd, quarantine_name, source.name)
            except OSError as exc:
                if exc.errno != errno.EEXIST:
                    raise
                # Preserve the unexpected quarantined file for inspection; do
                # not overwrite a concurrent replacement at the original path.
            raise RuntimeError("quarantined_legacy_file_did_not_match_backup")
        os.unlink(quarantine_name, dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def verify_sqlite(path: Path) -> None:
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("legacy_sqlite_integrity_check_failed")
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("legacy_sqlite_foreign_key_check_failed")


def preserve_share_file(source: Path, destination: Path) -> str:
    if destination.exists():
        if destination.is_symlink() or not destination.is_file():
            raise RuntimeError("local_rollback_file_is_not_regular")
        if destination.stat().st_mode & 0o077:
            raise RuntimeError("local_rollback_file_is_not_private")
        digest = file_sha256(destination)
        if source.exists() and file_sha256(source) != digest:
            raise RuntimeError("existing_rollback_copy_differs_from_legacy_source")
        return digest
    if not source.exists():
        raise RuntimeError("legacy_source_missing_and_no_local_rollback_copy")
    return copy_share_file(source, destination)


def write_manifest(path: Path, value: dict[str, object]) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
            raise RuntimeError("rollback_manifest_is_not_private_regular_file")
        previous = json.loads(path.read_text(encoding="utf-8"))
        for key in ("combined_database", "legacy_analysis_sha256",
                    "legacy_trans_checkpoint_sha256", "trans_table_counts"):
            if previous.get(key) != value.get(key):
                raise RuntimeError("existing_rollback_manifest_conflicts")
    fd, staged_name = tempfile.mkstemp(prefix=".manifest-", dir=path.parent)
    staged = Path(staged_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(value, output, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(staged, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        staged.unlink(missing_ok=True)


def systemd_property(unit: str, property_name: str) -> str:
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show", unit,
             f"--property={property_name}", "--value"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, check=True, timeout=10, text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("user_systemd_manager_state_unavailable") from exc
    return result.stdout.strip()


def systemd_action(*arguments: str) -> None:
    try:
        subprocess.run(
            ["systemctl", "--user", *arguments], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=True, timeout=40,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("user_systemd_action_failed") from exc


def parse_environment_assignments(value: str) -> dict[str, str]:
    assignments: dict[str, str] = {}
    for item in shlex.split(value):
        if "=" in item:
            key, content = item.split("=", 1)
            assignments[key] = content
    return assignments


def verify_service_path_assignments(values: dict[str, str]) -> None:
    expected = {
        "TRAFFIC_ANALYSIS_SNAPSHOT": Path.home() / "Research.Databases/research.sqlite",
        "TRAFFIC_ANALYSIS_TRANS_CHECKPOINT":
            Path.home() / ".local/share/trans/audio-sql/trans-audio.sqlite.gz",
    }
    for name, expected_path in expected.items():
        if name not in values:
            continue
        value = values[name]
        resolved = Path(value.replace("%h", str(Path.home()))).expanduser()
        if resolved != expected_path:
            raise RuntimeError(f"Traffic_Analysis_{name}_does_not_use_unified_storage")


def configured_service_paths() -> None:
    expected_environment_file = Path.home() / ".config/traffic-analysis/environment"
    try:
        manager_environment = subprocess.run(
            ["systemctl", "--user", "show-environment"], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True,
            timeout=10, text=True,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("user_systemd_manager_state_unavailable") from exc
    # Each source is checked independently so systemd's precedence order
    # cannot mask a stale path. `EnvironmentFiles` and `Environment` are read
    # from the loaded unit and include drop-ins.
    verify_service_path_assignments(parse_environment_assignments(manager_environment))
    verify_service_path_assignments(parse_environment_assignments(
        systemd_property("traffic-analysis.service", "Environment")
    ))
    files_property = systemd_property("traffic-analysis.service", "EnvironmentFiles")
    file_entries = re.findall(
        r"(?:^|[\s,{])(-?)([^\s{},]+)\s+\(ignore_errors=(?:yes|no)\)(?=$|[\s,}])",
        files_property,
    )
    remainder = re.sub(
        r"(?:^|[\s,{])-?[^\s{},]+\s+\(ignore_errors=(?:yes|no)\)(?=$|[\s,}])",
        " ", files_property,
    ).strip(" \t\r\n,{}[]")
    if remainder or (files_property.strip() and not file_entries):
        raise RuntimeError("Traffic_Analysis_environment_file_list_is_unrecognized")
    for optional, raw_path in file_entries:
        declared_path = Path(raw_path.replace("%h", str(Path.home()))).expanduser()
        if declared_path != expected_environment_file:
            raise RuntimeError("Traffic_Analysis_uses_an_unrecognized_environment_file")
        path = declared_path
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("Traffic_Analysis_environment_file_is_missing_or_unsafe")
        if path.stat().st_mode & 0o077 or path.stat().st_uid != os.getuid():
            raise RuntimeError("Traffic_Analysis_environment_file_is_not_private")
        file_values = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = shlex.split(line, comments=True)
            if len(parts) == 1 and "=" in parts[0]:
                key, content = parts[0].split("=", 1)
                file_values[key] = content
        verify_service_path_assignments(file_values)


def wait_for_service_state(unit: str, expected: str, timeout_seconds: float = 40.0) -> None:
    import time

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if systemd_property(unit, "ActiveState") == expected:
            return
        time.sleep(0.25)
    raise RuntimeError("TRANS_worker_did_not_reach_expected_state")


def verify_loaded_service_configuration() -> tuple[Path, int]:
    if systemd_property("trans-audio-sql.service", "NeedDaemonReload") != "no":
        raise RuntimeError("TRANS_service_manager_has_not_loaded_current_unit")
    if systemd_property("traffic-analysis.service", "NeedDaemonReload") != "no":
        raise RuntimeError("Traffic_Analysis_manager_has_not_loaded_current_unit")
    if systemd_property("traffic-analysis.timer", "NeedDaemonReload") != "no":
        raise RuntimeError("Traffic_Analysis_timer_manager_has_not_loaded_current_unit")
    state = systemd_property("trans-audio-sql.service", "ActiveState")
    if state != "active":
        raise RuntimeError("TRANS_local_checkpoint_worker_is_not_active")
    main_pid_text = systemd_property("trans-audio-sql.service", "MainPID")
    if not main_pid_text.isdigit() or int(main_pid_text) < 1:
        raise RuntimeError("TRANS_worker_main_pid_unavailable")
    if systemd_property("traffic-analysis.service", "ActiveState") != "inactive":
        raise RuntimeError("Traffic_Analysis_cycle_must_be_inactive_for_retirement")

    trans_environment = shlex.split(
        systemd_property("trans-audio-sql.service", "Environment")
    )
    values = dict(item.split("=", 1) for item in trans_environment if "=" in item)
    checkpoint = values.get("TRANS_AUDIO_SQL_CHECKPOINT", "")
    expected_checkpoint = Path.home() / ".local/share/trans/audio-sql/trans-audio.sqlite.gz"
    if checkpoint not in {str(expected_checkpoint), "%h/.local/share/trans/audio-sql/trans-audio.sqlite.gz"}:
        raise RuntimeError("TRANS_service_is_not_using_the_private_local_checkpoint")

    write_paths = systemd_property("traffic-analysis.service", "ReadWritePaths")
    expected_write_path = str(Path.home() / "Research.Databases")
    if expected_write_path not in shlex.split(write_paths) and \
            "%h/Research.Databases" not in shlex.split(write_paths):
        raise RuntimeError("Traffic_Analysis_service_has_not_loaded_combined_database_path")

    configured_service_paths()

    database = values.get("TRANS_AUDIO_SQL_DATABASE", os.environ.get(
        "TRANS_AUDIO_SQL_DATABASE", "/tmp/trans-audio.sqlite"
    ))
    return Path(database).expanduser(), int(main_pid_text)


def assert_no_other_trans_processes(service_pid: int) -> None:
    for process in Path("/proc").glob("[0-9]*"):
        try:
            pid = int(process.name)
            if pid in {os.getpid(), service_pid}:
                continue
            command = (process / "cmdline").read_bytes().split(b"\0")
            executable = Path(os.fsdecode(command[0])).name if command and command[0] else ""
            if ("trans.audio_sql.pipeline" in os.fsdecode(b" ".join(command)) or
                    executable == "trans-audio-sql"):
                raise RuntimeError("another_TRANS_cli_or_worker_is_running")
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue


def verify_trans_checkpoint(path: Path, combined: Path, *, exact: bool) -> dict[str, int]:
    require_local_checkpoint(path)
    temporary_mount = _mount_for(Path("/dev/shm"))
    if temporary_mount is None or temporary_mount[1].casefold() != "tmpfs":
        raise RuntimeError("retirement_verification_requires_tmpfs_at_dev_shm")
    with tempfile.NamedTemporaryFile(prefix="retire-trans-", suffix=".sqlite",
                                     dir="/dev/shm") as raw:
        with gzip.open(path, "rb") as compressed:
            shutil.copyfileobj(compressed, raw, 1024 * 1024)
        raw.flush()
        source = sqlite3.connect(f"file:{raw.name}?mode=ro", uri=True)
        target = sqlite3.connect(f"file:{combined}?mode=ro", uri=True)
        try:
            if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("local_trans_checkpoint_integrity_check_failed")
            if source.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise RuntimeError("local_trans_checkpoint_foreign_key_check_failed")
            results = {}
            source_tables = {row[0] for row in source.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            core = ("audio_sources", "audio_transcripts", "audio_transcript_segments")
            if not set(core).issubset(source_tables):
                raise RuntimeError("TRANS_checkpoint_missing_core_tables")
            for table in core:
                count = compare_table_data(source, target, table)
                results[table] = count
            if "audio_transcript_reviews" in source_tables:
                results["audio_transcript_reviews"] = compare_table_data(
                    source, target, "audio_transcript_reviews"
                )
            elif exact or target.execute(
                "SELECT count(*) FROM audio_transcript_reviews"
            ).fetchone()[0] != 0:
                raise RuntimeError("TRANS_review_ledger_not_preserved")
            else:
                results["audio_transcript_reviews"] = 0
            if exact:
                for table in source_tables & {"audio_sources", "audio_transcripts",
                                              "audio_transcript_segments",
                                              "audio_transcript_reviews"}:
                    old_columns, old_count, old_digest = table_digest(source, table)
                    new_columns, new_count, new_digest = table_digest(target, table)
                    if (old_columns, old_count, old_digest) != (
                        new_columns, new_count, new_digest
                    ):
                        raise RuntimeError("combined_TRANS_rows_differ_from_local_checkpoint")
            return results
        finally:
            source.close()
            target.close()


def main() -> int:
    os.umask(0o077)
    combined = configured_path("TRAFFIC_ANALYSIS_SNAPSHOT", DEFAULT_ANALYSIS_SNAPSHOT)
    checkpoint = configured_path(
        "TRAFFIC_ANALYSIS_TRANS_CHECKPOINT", DEFAULT_TRANS_CHECKPOINT
    )
    legacy_analysis = Path.home() / "Research.Databases/traffic_analysis/traffic-analysis.sqlite"
    legacy_trans = Path.home() / "Research.Databases/audio_ingest/transcription.sqlite.gz"
    if not combined.is_file() or combined.is_symlink():
        raise RuntimeError("combined_research_database_missing_or_unsafe")
    verify_research_mount(combined)
    for path in (legacy_analysis, legacy_trans):
        if not path.exists():
            continue
        if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
            raise RuntimeError("legacy_research_file_is_not_regular")
        if any(Path(str(path) + suffix).exists() for suffix in ("-journal", "-wal", "-shm")):
            raise RuntimeError("legacy_sqlite_has_sidecars")

    service_unit = Path.home() / ".config/systemd/user/trans-audio-sql.service"
    traffic_unit = Path.home() / ".config/systemd/user/traffic-analysis.service"
    trans_text = service_unit.read_text(encoding="utf-8")
    traffic_text = traffic_unit.read_text(encoding="utf-8")
    if "TRANS_AUDIO_SQL_CHECKPOINT=%h/.local/share/trans/audio-sql/trans-audio.sqlite.gz" not in trans_text:
        raise RuntimeError("TRANS_service_unit_has_not_migrated_to_local_checkpoint")
    if "Research.Databases" not in traffic_text or "traffic_analysis" in traffic_text:
        raise RuntimeError("Traffic_Analysis_service_unit_has_not_migrated_to_combined_path")
    trans_database, trans_main_pid = verify_loaded_service_configuration()

    data_home = configured_path("TRAFFIC_ANALYSIS_DATA_HOME", ANALYSIS_HOME)
    data_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    if data_home.resolve(strict=True) != Path(os.path.abspath(data_home)):
        raise RuntimeError("local_analysis_directory_must_not_use_symlinks")
    data_metadata = data_home.lstat()
    data_mount = _mount_for(data_home)
    remote_filesystems = {
        "cifs", "smb3", "nfs", "nfs4", "sshfs", "fuse.sshfs", "9p",
        "afs", "ceph", "glusterfs", "davfs", "fuse.rclone", "fuse.s3fs",
    }
    if (data_metadata.st_mode & 0o077 or data_metadata.st_uid != os.getuid() or
            data_mount is None or data_mount[1].casefold() in remote_filesystems):
        raise RuntimeError("local_analysis_directory_is_not_private")
    lock_files = [
        data_home / "production-cycle.lock",
        trans_database.with_name(f".{trans_database.name}.lock"),
    ]
    locks = []
    trans_stopped = False
    try:
        # The worker owns the local checkpoint lock for its entire watch loop.
        # Stop it cleanly so its final checkpoint is stable while we verify and
        # retire the old share copy, then restart it in finally.
        trans_stopped = True
        systemd_action("stop", "trans-audio-sql.service")
        wait_for_service_state("trans-audio-sql.service", "inactive")
        for lock_path in lock_files:
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                os.close(fd)
                raise RuntimeError("a_TRANS_or_Traffic_Analysis_worker_is_running") from exc
            locks.append(fd)

        assert_no_other_trans_processes(-1)
        for legacy in (legacy_analysis, legacy_trans):
            if list(legacy.parent.glob(f".{legacy.name}.retire-*")):
                raise RuntimeError("legacy_retirement_quarantine_requires_operator_inspection")

        with sqlite3.connect(f"file:{combined.resolve()}?mode=ro", uri=True) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("combined_database_integrity_check_failed")
            if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise RuntimeError("combined_database_foreign_key_check_failed")
            analysis_tables = {
                row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")
            }
            required = {"analysis_records", "neural_analyses", "neural_failures",
                        "connection_candidates", "transcript_segments",
                        "audio_sources", "audio_transcripts",
                        "audio_transcript_segments", "audio_transcript_reviews"}
            if not required.issubset(analysis_tables):
                raise RuntimeError("combined_database_missing_required_tables")
            trans_counts = {
                table: db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                for table in ("audio_sources", "audio_transcripts",
                              "audio_transcript_segments", "audio_transcript_reviews")
            }

        backup_dir = Path.home() / ".local/share/traffic-analysis/unified-research-rollback/legacy-files"
        backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(backup_dir, 0o700)
        analysis_backup = backup_dir / "traffic-analysis.sqlite"
        trans_backup = backup_dir / "transcription.sqlite.gz"
        analysis_digest = preserve_share_file(legacy_analysis, analysis_backup)
        trans_digest = preserve_share_file(legacy_trans, trans_backup)
        verify_sqlite(analysis_backup)
        old_trans_rows = verify_trans_checkpoint(trans_backup, combined, exact=False)
        combined_trans_rows = verify_trans_checkpoint(checkpoint, combined, exact=True)
        if old_trans_rows != combined_trans_rows:
            raise RuntimeError("legacy_and_local_TRANS_checkpoints_differ")

        with sqlite3.connect(f"file:{analysis_backup.resolve()}?mode=ro", uri=True) as old_db, \
             sqlite3.connect(f"file:{combined.resolve()}?mode=ro", uri=True) as new_db:
            for table in ("transcript_segments", "analysis_records", "neural_analyses",
                          "neural_failures", "connection_candidates", "audible_events"):
                if old_db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone():
                    if not new_db.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                    ).fetchone():
                        raise RuntimeError("combined_database_missing_legacy_analysis_table")
                    compare_table_data(old_db, new_db, table)

        # Do not remove either legacy file until both exact local copies and all
        # source-to-combined checks above have succeeded. The fixed backup path
        # and manifest make an interrupted two-file retirement resumable.
        manifest_path = backup_dir / "manifest.json"
        write_manifest(manifest_path, {
            "combined_database": str(combined),
            "legacy_analysis_sha256": analysis_digest,
            "legacy_trans_checkpoint_sha256": trans_digest,
            "trans_table_counts": trans_counts,
            "status": "verified_pending_retirement",
        })
        if legacy_analysis.exists():
            unlink_verified_share_file(legacy_analysis, analysis_digest)
        if legacy_trans.exists():
            unlink_verified_share_file(legacy_trans, trans_digest)

        if legacy_analysis.exists() or legacy_trans.exists():
            raise RuntimeError("legacy_research_files_still_present_after_retirement")
        write_manifest(manifest_path, {
            "combined_database": str(combined),
            "legacy_analysis_sha256": analysis_digest,
            "legacy_trans_checkpoint_sha256": trans_digest,
            "trans_table_counts": trans_counts,
            "status": "retired",
        })
        print(json.dumps({
            "status": "PASS",
            "combined_database": str(combined),
            "trans_table_counts": trans_counts,
            "legacy_analysis_sha256": analysis_digest,
            "legacy_trans_checkpoint_sha256": trans_digest,
            "local_rollback_directory": str(backup_dir),
            "retired_files": [str(legacy_analysis), str(legacy_trans)],
        }, sort_keys=True))
    finally:
        for fd in reversed(locks):
            os.close(fd)
        if trans_stopped:
            systemd_action("start", "trans-audio-sql.service")
            wait_for_service_state("trans-audio-sql.service", "active")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
        print(f"traffic-analysis legacy retirement: {exc}", file=sys.stderr)
        raise SystemExit(2)
