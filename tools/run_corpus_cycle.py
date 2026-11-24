#!/usr/bin/env python3
"""Run one private TRANS import/classification cycle and publish an SQLite snapshot."""
from __future__ import annotations

import fcntl
import gzip
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "src"))

from traffic_analysis.cli import main  # noqa: E402


MAX_TRANSCRIPT_DB_BYTES = 8 * 1024**3
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


def restore_snapshot(snapshot: Path, database: Path) -> None:
    if database.exists():
        if not database.is_file() or database.stat().st_size == 0:
            raise RuntimeError("local_analysis_database_is_not_a_valid_file")
        require_integrity(database)
        return
    if not snapshot.is_file():
        raise RuntimeError("analysis_snapshot_missing_and_no_local_database")
    temporary = database.with_name(database.name + ".restore-partial")
    if temporary.exists():
        raise RuntimeError("stale_analysis_restore_file_requires_operator_review")
    try:
        shutil.copyfile(snapshot, temporary)
        os.chmod(temporary, 0o600)
        require_integrity(temporary)
        os.replace(temporary, database)
    finally:
        if temporary.exists():
            temporary.unlink()


def inflate_trans_checkpoint(checkpoint: Path, directory: Path) -> Path:
    if not checkpoint.is_file():
        raise RuntimeError("trans_checkpoint_missing")
    target = directory / "transcription.sqlite"
    written = 0
    try:
        with gzip.open(checkpoint, "rb") as source, target.open("xb") as output:
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
        os.chmod(target, 0o600)
        require_integrity(target)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


def publish_snapshot(database: Path, snapshot: Path) -> str:
    require_integrity(database)
    snapshot.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if snapshot.parent.stat().st_mode & 0o077:
        raise RuntimeError("analysis_snapshot_directory_is_not_private")
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{snapshot.name}.", suffix=".partial", dir=snapshot.parent
    )
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    try:
        with database.open("rb") as source, os.fdopen(fd, "wb") as output:
            while True:
                block = source.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o600)
        if temporary.stat().st_size != database.stat().st_size:
            raise RuntimeError("analysis_snapshot_copy_size_mismatch")
        os.replace(temporary, snapshot)
        require_integrity(snapshot)
        return digest.hexdigest()
    finally:
        temporary.unlink(missing_ok=True)


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

        restore_snapshot(snapshot, database)
        with tempfile.TemporaryDirectory(prefix="traffic-analysis-cycle-") as temp:
            trans_database = inflate_trans_checkpoint(checkpoint, Path(temp))
            run_cli(database, "import-trans", "--trans-database", str(trans_database))
        run_cli(database, "neural-classify", "--model", str(model),
                "--llama-server", str(server), "--limit", "1000",
                "--allow-memory-overcommit")
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
