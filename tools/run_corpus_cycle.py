#!/usr/bin/env python3
"""Run one private TRANS import/classification cycle and publish an SQLite snapshot."""
from __future__ import annotations

import errno
import fcntl
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import uuid
from contextlib import redirect_stdout


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "src"))

from traffic_analysis.cli import main  # noqa: E402


MAX_TRANSCRIPT_DB_BYTES = 8 * 1024**3
NEURAL_PUBLISH_BATCH_SIZE = 50
NEURAL_CYCLE_ATTEMPT_LIMIT = 1000
RESEARCH_SOURCE = os.environ.get(
    "TRAFFIC_ANALYSIS_RESEARCH_SOURCE", "//archive.local/research"
).casefold().rstrip("/")
ANALYSIS_HOME = Path.home() / ".local/share/traffic-analysis"
DEFAULT_TRANS_CHECKPOINT = Path.home() / "Research.Databases/audio_ingest/transcription.sqlite.gz"
DEFAULT_ANALYSIS_SNAPSHOT = Path.home() / "Research.Databases/traffic_analysis/traffic-analysis.sqlite"
DEFAULT_MODEL = (
    Path.home() / ".local/share/llama.cpp/cache/models--lmstudio-community--"
    "Qwen3.5-9B-GGUF/snapshots/1379f25c6b505a3fc737bd7818cb09389cf807c1/"
    "Qwen3.5-9B-Q4_K_M.gguf"
)


def configured_path(name: str, default: Path) -> Path:
    return Path(os.environ.get(name, str(default))).expanduser()


def require_integrity(path: Path) -> None:
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as db:
        result = db.execute("PRAGMA integrity_check").fetchone()
    if not result or result[0] != "ok":
        raise RuntimeError("sqlite_integrity_check_failed")


def _mount_field(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), value)


def _mount_for(path: Path) -> tuple[int, str, str, set[str]] | None:
    resolved = path.resolve()
    selected: tuple[int, int, str, str, set[str]] | None = None
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        fields = line.split()
        try:
            separator = fields.index("-")
            mount_id = int(fields[0])
            mountpoint = Path(_mount_field(fields[4]))
            if mountpoint != resolved and mountpoint not in resolved.parents:
                continue
            filesystem = fields[separator + 1]
            source = _mount_field(fields[separator + 2]).casefold()
            options = set(fields[5].split(",")) | set(fields[separator + 3].split(","))
        except (IndexError, ValueError):
            continue
        candidate = (len(mountpoint.parts), mount_id, filesystem, source, options)
        if selected is None or candidate[:2] > selected[:2]:
            selected = candidate
    if selected is None:
        return None
    return selected[1], selected[2], selected[3], selected[4]


def _descriptor_mount_id(descriptor_fd: int) -> int:
    for line in Path(f"/proc/self/fdinfo/{descriptor_fd}").read_text().splitlines():
        if line.startswith("mnt_id:"):
            return int(line.split(":", 1)[1].strip())
    raise RuntimeError("descriptor_mount_id_unavailable")


def _require_research_mount(path: Path, descriptor_fd: int | None = None,
                            writable: bool = True) -> None:
    mount = _mount_for(path)
    if (mount is None or mount[1].casefold() != "cifs" or
            mount[2].rstrip("/") != RESEARCH_SOURCE):
        raise RuntimeError("research_share_not_mounted_from_expected_source")
    if writable and ("rw" not in mount[3] or "ro" in mount[3]):
        raise RuntimeError("research_analysis_directory_not_mounted_read_write")
    if descriptor_fd is not None and _descriptor_mount_id(descriptor_fd) != mount[0]:
        raise RuntimeError("research_descriptor_is_not_on_current_share_mount")


def verify_research_mount(checkpoint: Path, snapshot: Path) -> None:
    _require_research_mount(checkpoint, writable=False)
    _require_research_mount(snapshot.parent, writable=True)


def research_file_sha256(path: Path) -> str:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        directory_fd = os.open(path.parent, directory_flags)
    except OSError as exc:
        raise RuntimeError("research_database_directory_unavailable") from exc
    try:
        _require_research_mount(path.parent, directory_fd)
        source_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        source_fd = os.open(path.name, source_flags, dir_fd=directory_fd)
        with os.fdopen(source_fd, "rb") as source:
            _require_research_mount(path, source.fileno())
            digest = hashlib.file_digest(source, "sha256").hexdigest()
            _require_research_mount(path, source.fileno())
            return digest
    finally:
        os.close(directory_fd)


def restore_snapshot(snapshot: Path, database: Path) -> bool:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        directory_fd = os.open(snapshot.parent, directory_flags)
    except OSError as exc:
        raise RuntimeError("research_database_directory_unavailable") from exc
    try:
        _require_research_mount(snapshot.parent, directory_fd)
        source_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            source_fd = os.open(snapshot.name, source_flags, dir_fd=directory_fd)
        except FileNotFoundError:
            _require_research_mount(snapshot.parent, directory_fd)
            return False
        temporary = database.with_name(database.name + ".restore-partial")
        if temporary.exists():
            os.close(source_fd)
            raise RuntimeError("stale_analysis_restore_file_requires_operator_review")
        try:
            with os.fdopen(source_fd, "rb") as source, temporary.open("xb") as output:
                _require_research_mount(snapshot, source.fileno())
                shutil.copyfileobj(source, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
                _require_research_mount(snapshot, source.fileno())
            os.chmod(temporary, 0o600)
            require_integrity(temporary)
            os.replace(temporary, database)
        finally:
            if temporary.exists():
                temporary.unlink()
        return True
    finally:
        os.close(directory_fd)


def verify_legacy_duplicate(legacy: Path, snapshot: Path) -> str | None:
    if not legacy.exists():
        return None
    legacy_info = legacy.lstat()
    if not legacy.is_file() or legacy_info.st_mode & 0o077:
        raise RuntimeError("legacy_local_database_is_not_a_private_regular_file")
    if any(Path(str(legacy) + suffix).exists()
           for suffix in ("-journal", "-shm", "-wal")):
        raise RuntimeError("legacy_local_database_has_sqlite_sidecars")
    require_integrity(legacy)
    with legacy.open("rb") as source:
        legacy_digest = hashlib.file_digest(source, "sha256").hexdigest()
    if legacy_digest != research_file_sha256(snapshot):
        raise RuntimeError("legacy_local_database_differs_from_research_snapshot")
    return legacy_digest


def remove_verified_legacy_duplicate(legacy: Path, expected_digest: str) -> None:
    if any(Path(str(legacy) + suffix).exists()
           for suffix in ("-journal", "-shm", "-wal")):
        raise RuntimeError("legacy_local_database_has_sqlite_sidecars")
    with legacy.open("rb") as source:
        current_digest = hashlib.file_digest(source, "sha256").hexdigest()
    if current_digest != expected_digest:
        raise RuntimeError("legacy_local_database_changed_before_removal")
    legacy.unlink()
    directory_fd = os.open(legacy.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def inflate_trans_checkpoint(checkpoint: Path, directory: Path) -> Path:
    if not checkpoint.is_file():
        raise RuntimeError("trans_checkpoint_missing")
    target = directory / "transcription.sqlite"
    written = 0
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        source_fd = os.open(checkpoint, flags)
        with os.fdopen(source_fd, "rb") as compressed:
            _require_research_mount(checkpoint, compressed.fileno(), writable=False)
            with gzip.GzipFile(fileobj=compressed, mode="rb") as source, target.open("xb") as output:
                while True:
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    written += len(block)
                    if written > MAX_TRANSCRIPT_DB_BYTES:
                        raise RuntimeError("trans_checkpoint_exceeds_size_limit")
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            _require_research_mount(checkpoint, compressed.fileno(), writable=False)
        os.chmod(target, 0o600)
        require_integrity(target)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


def _fsync_directory(directory_fd: int) -> None:
    try:
        os.fsync(directory_fd)
    except OSError as exc:
        if exc.errno not in {errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}:
            raise


def publish_snapshot(database: Path, snapshot: Path) -> str:
    require_integrity(database)
    snapshot.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    directory_fd = os.open(snapshot.parent, directory_flags)
    temporary_name = f".{snapshot.name}.{uuid.uuid4().hex}.partial"
    backup_name = f".{snapshot.name}.{uuid.uuid4().hex}.previous"
    digest = hashlib.sha256()
    backup_created = False
    backup_file_created = False
    replaced = False
    try:
        _require_research_mount(snapshot.parent, directory_fd)
        if os.fstat(directory_fd).st_mode & 0o077:
            raise RuntimeError("analysis_snapshot_directory_is_not_private")
        file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        file_fd = os.open(temporary_name, file_flags, 0o600, dir_fd=directory_fd)
        with database.open("rb") as source, os.fdopen(file_fd, "wb") as output:
            while True:
                block = source.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        copied_stat = os.stat(temporary_name, dir_fd=directory_fd, follow_symlinks=False)
        if copied_stat.st_size != database.stat().st_size:
            raise RuntimeError("analysis_snapshot_copy_size_mismatch")

        staged_fd = os.open(temporary_name, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
                            dir_fd=directory_fd)
        with os.fdopen(staged_fd, "rb") as staged:
            _require_research_mount(snapshot.parent / temporary_name, staged.fileno())
            staged_digest = hashlib.file_digest(staged, "sha256").hexdigest()
            _require_research_mount(snapshot.parent / temporary_name, staged.fileno())
        if staged_digest != digest.hexdigest():
            raise RuntimeError("analysis_snapshot_staged_digest_mismatch")

        try:
            previous_fd = os.open(
                snapshot.name,
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
        except FileNotFoundError:
            previous_fd = None
        if previous_fd is not None:
            try:
                backup_fd = os.open(backup_name, file_flags, 0o600, dir_fd=directory_fd)
            except Exception:
                os.close(previous_fd)
                raise
            backup_file_created = True
            previous_digest = hashlib.sha256()
            with os.fdopen(previous_fd, "rb") as previous, os.fdopen(backup_fd, "wb") as backup:
                _require_research_mount(snapshot, previous.fileno())
                _require_research_mount(snapshot.parent / backup_name, backup.fileno())
                while True:
                    block = previous.read(1024 * 1024)
                    if not block:
                        break
                    previous_digest.update(block)
                    backup.write(block)
                backup.flush()
                os.fsync(backup.fileno())
                _require_research_mount(snapshot, previous.fileno())
            backup_check_fd = os.open(backup_name, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
                                      dir_fd=directory_fd)
            with os.fdopen(backup_check_fd, "rb") as backup_check:
                _require_research_mount(snapshot.parent / backup_name, backup_check.fileno())
                backup_digest = hashlib.file_digest(backup_check, "sha256").hexdigest()
                _require_research_mount(snapshot.parent / backup_name, backup_check.fileno())
            if backup_digest != previous_digest.hexdigest():
                os.unlink(backup_name, dir_fd=directory_fd)
                raise RuntimeError("analysis_snapshot_rollback_copy_mismatch")
            backup_created = True

        _require_research_mount(snapshot.parent, directory_fd)
        os.replace(temporary_name, snapshot.name,
                   src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        replaced = True
        _fsync_directory(directory_fd)
        published_fd = os.open(snapshot.name, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
                               dir_fd=directory_fd)
        with os.fdopen(published_fd, "rb") as published_file:
            _require_research_mount(snapshot, published_file.fileno())
            published_digest = hashlib.file_digest(published_file, "sha256").hexdigest()
            _require_research_mount(snapshot, published_file.fileno())
        if published_digest != digest.hexdigest():
            raise RuntimeError("analysis_snapshot_copy_digest_mismatch")
        if backup_created:
            os.unlink(backup_name, dir_fd=directory_fd)
            backup_created = False
            try:
                _fsync_directory(directory_fd)
            except OSError as exc:
                print(f"warning: published database is verified but backup cleanup sync failed ({exc.errno})",
                      file=sys.stderr)
        return published_digest
    except Exception:
        if replaced:
            try:
                if backup_created:
                    os.replace(backup_name, snapshot.name,
                               src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
                    backup_created = False
                else:
                    # Never leave an unverified first publication at the
                    # canonical path, where the next cycle could trust it.
                    os.unlink(snapshot.name, dir_fd=directory_fd)
                    print("warning: first Research snapshot publication failed verification; "
                          "removed the untrusted file so the next cycle can rebuild from TRANS",
                          file=sys.stderr)
                _fsync_directory(directory_fd)
            except OSError as rollback_exc:
                raise RuntimeError("analysis_snapshot_publish_and_rollback_failed") from rollback_exc
        raise
    finally:
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        if backup_created and not replaced:
            try:
                os.unlink(backup_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        elif backup_file_created and not backup_created:
            try:
                os.unlink(backup_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        os.close(directory_fd)


def run_cli(database: Path, *args: str) -> str:
    captured = io.StringIO()
    with redirect_stdout(captured):
        code = main(["--database", str(database), *args])
    output = captured.getvalue()
    if output:
        print(output, end="" if output.endswith("\n") else "\n", flush=True)
    if code != 0:
        raise RuntimeError(f"traffic_analysis_command_failed:{args[0]}:{code}")
    return output


def memory_overcommit_operator_opt_in(data_home: Path) -> bool:
    """Read a private local marker for a deliberately reviewed memory override."""
    marker = data_home / "allow-memory-overcommit"
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(marker, flags)
    except FileNotFoundError:
        return False
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise RuntimeError("memory_override_marker_must_be_private_regular_file")
        with os.fdopen(descriptor, "r", encoding="utf-8") as marker_file:
            descriptor = -1
            if marker_file.read(128).strip() != "operator-reviewed-host-memory":
                raise RuntimeError("memory_override_marker_content_invalid")
        return True
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def main_cycle() -> int:
    os.umask(0o077)
    data_home = configured_path("TRAFFIC_ANALYSIS_DATA_HOME", ANALYSIS_HOME)
    data_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    if data_home.stat().st_mode & 0o077:
        raise RuntimeError("local_analysis_directory_is_not_private")
    lock_path = data_home / "production-cycle.lock"
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(lock_fd, "r+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("traffic_analysis_cycle_already_running") from exc

        checkpoint = configured_path(
            "TRAFFIC_ANALYSIS_TRANS_CHECKPOINT", DEFAULT_TRANS_CHECKPOINT
        )
        snapshot = configured_path(
            "TRAFFIC_ANALYSIS_SNAPSHOT", DEFAULT_ANALYSIS_SNAPSHOT
        )
        model = configured_path("TRAFFIC_ANALYSIS_MODEL", DEFAULT_MODEL)
        if not model.is_file():
            raise RuntimeError("configured_local_model_missing")
        server = configured_path(
            "TRAFFIC_ANALYSIS_LLAMA_SERVER", Path.home() / ".local/bin/llama-server"
        )
        if not server.is_file():
            found = shutil.which("llama-server")
            if not found:
                raise RuntimeError("llama_server_not_found")
            server = Path(found)

        verify_research_mount(checkpoint, snapshot)
        legacy_database = data_home / "traffic-analysis.sqlite"
        legacy_duplicate_digest = verify_legacy_duplicate(legacy_database, snapshot)
        with tempfile.TemporaryDirectory(prefix="traffic-analysis-cycle-") as temp:
            working_directory = Path(temp)
            database = working_directory / "traffic-analysis.sqlite"
            restore_snapshot(snapshot, database)
            trans_database = inflate_trans_checkpoint(checkpoint, working_directory)
            run_cli(database, "import-trans", "--trans-database", str(trans_database))
            digest = publish_snapshot(database, snapshot)
            print(f"Research analysis snapshot updated after TRANS import; sha256={digest}",
                  flush=True)
            neural_args = ["neural-classify", "--model", str(model),
                           "--llama-server", str(server)]
            if memory_overcommit_operator_opt_in(data_home):
                neural_args.append("--allow-memory-overcommit")
                print("Using private operator-reviewed memory override", file=sys.stderr)
            batch_number = 0
            attempted_this_cycle = 0
            while True:
                batch_number += 1
                remaining = NEURAL_CYCLE_ATTEMPT_LIMIT - attempted_this_cycle
                if remaining <= 0:
                    break
                batch_args = [*neural_args, "--limit", str(min(
                    NEURAL_PUBLISH_BATCH_SIZE, remaining
                ))]
                try:
                    batch_result = json.loads(run_cli(database, *batch_args))
                except (json.JSONDecodeError, TypeError) as exc:
                    raise RuntimeError("neural_batch_result_is_not_valid_json") from exc
                if not isinstance(batch_result, dict) or any(
                    not isinstance(batch_result.get(key, 0), int) or
                    batch_result.get(key, 0) < 0
                    for key in ("analyzed", "failed", "paused")
                ):
                    raise RuntimeError("neural_batch_result_schema_invalid")
                digest = publish_snapshot(database, snapshot)
                attempted = batch_result.get("analyzed", 0) + batch_result.get("failed", 0)
                attempted_this_cycle += attempted
                print(json.dumps({
                    "checkpoint_batch": batch_number,
                    "attempted_this_cycle": attempted_this_cycle,
                    "analyzed": batch_result.get("analyzed", 0),
                    "failed": batch_result.get("failed", 0),
                    "paused": batch_result.get("paused", 0),
                    "snapshot_sha256": digest,
                }, sort_keys=True), flush=True)
                if batch_result.get("paused"):
                    break
                if not batch_result.get("analyzed", 0) and not batch_result.get("failed", 0):
                    break
            run_cli(database, "report")
            digest = research_file_sha256(snapshot)
        if legacy_duplicate_digest:
            remove_verified_legacy_duplicate(legacy_database, legacy_duplicate_digest)
        print(f"Research analysis snapshot updated; sha256={digest}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main_cycle())
    except Exception as exc:
        print(f"traffic-analysis-cycle: {exc}", file=sys.stderr)
        raise SystemExit(1)
