#!/usr/bin/env python3
"""Run one private TRANS import/classification cycle and publish an SQLite snapshot."""
from __future__ import annotations

import fcntl
import gzip
import hashlib
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
import uuid


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "src"))

from traffic_analysis.cli import main  # noqa: E402


MAX_TRANSCRIPT_DB_BYTES = 8 * 1024**3
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


def _require_research_mount(path: Path, descriptor_fd: int | None = None) -> None:
    mount = _mount_for(path)
    if (mount is None or mount[1].casefold() != "cifs" or
            mount[2].rstrip("/") != RESEARCH_SOURCE or
            "rw" not in mount[3] or "ro" in mount[3]):
        raise RuntimeError("research_share_not_mounted_from_expected_rw_source")
    if descriptor_fd is not None and _descriptor_mount_id(descriptor_fd) != mount[0]:
        raise RuntimeError("research_descriptor_is_not_on_current_share_mount")


def verify_research_mount(checkpoint: Path, snapshot: Path) -> None:
    for path in (checkpoint, snapshot.parent):
        _require_research_mount(path)


def restore_snapshot(snapshot: Path, database: Path) -> None:
    if database.exists():
        if not database.is_file() or database.stat().st_size == 0:
            raise RuntimeError("local_analysis_database_is_not_a_valid_file")
        require_integrity(database)
        return
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        directory_fd = os.open(snapshot.parent, directory_flags)
    except OSError as exc:
        raise RuntimeError("analysis_snapshot_missing_and_no_local_database") from exc
    temporary = database.with_name(database.name + ".restore-partial")
    if temporary.exists():
        os.close(directory_fd)
        raise RuntimeError("stale_analysis_restore_file_requires_operator_review")
    try:
        _require_research_mount(snapshot.parent, directory_fd)
        source_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        source_fd = os.open(snapshot.name, source_flags, dir_fd=directory_fd)
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
            _require_research_mount(checkpoint, compressed.fileno())
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
            _require_research_mount(checkpoint, compressed.fileno())
        os.chmod(target, 0o600)
        require_integrity(target)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


def publish_snapshot(database: Path, snapshot: Path) -> str:
    require_integrity(database)
    snapshot.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    directory_fd = os.open(snapshot.parent, directory_flags)
    temporary_name = f".{snapshot.name}.{uuid.uuid4().hex}.partial"
    digest = hashlib.sha256()
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
        _require_research_mount(snapshot.parent, directory_fd)
        os.replace(temporary_name, snapshot.name,
                   src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
        published_fd = os.open(snapshot.name, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
                               dir_fd=directory_fd)
        with os.fdopen(published_fd, "rb") as published_file:
            _require_research_mount(snapshot, published_file.fileno())
            published_digest = hashlib.file_digest(published_file, "sha256").hexdigest()
            _require_research_mount(snapshot, published_file.fileno())
        if published_digest != digest.hexdigest():
            raise RuntimeError("analysis_snapshot_copy_digest_mismatch")
        return published_digest
    finally:
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)


def run_cli(database: Path, *args: str) -> None:
    code = main(["--database", str(database), *args])
    if code != 0:
        raise RuntimeError(f"traffic_analysis_command_failed:{args[0]}:{code}")


def main_cycle() -> int:
    os.umask(0o077)
    data_home = configured_path("TRAFFIC_ANALYSIS_DATA_HOME", ANALYSIS_HOME)
    data_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    if data_home.stat().st_mode & 0o077:
        raise RuntimeError("local_analysis_directory_is_not_private")
    database = data_home / "traffic-analysis.sqlite"
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
        restore_snapshot(snapshot, database)
        with tempfile.TemporaryDirectory(prefix="traffic-analysis-cycle-") as temp:
            trans_database = inflate_trans_checkpoint(checkpoint, Path(temp))
            run_cli(database, "import-trans", "--trans-database", str(trans_database))
        run_cli(database, "neural-classify", "--model", str(model),
                "--llama-server", str(server), "--limit", "1000")
        run_cli(database, "report")
        digest = publish_snapshot(database, snapshot)
        print(f"Research analysis snapshot updated; sha256={digest}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main_cycle())
    except Exception as exc:
        print(f"traffic-analysis-cycle: {exc}", file=sys.stderr)
        raise SystemExit(1)
