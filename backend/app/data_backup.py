"""Consistent idle-run SQLite backups and verified restore to a new directory."""

import hashlib
import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from app.models import utc_now

DATABASE_FILES = ("shopping_agent.sqlite3", "agent_checkpoints.sqlite3")


class BackupError(Exception):
    """Messages are safe to show; never include database contents."""


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def connect_readonly(path: Path):
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)


def verify_sqlite(path: Path):
    with closing(connect_readonly(path)) as connection:
        if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise BackupError("SQLite 完整性检查未通过。")


def backup_data(source: Path, destination: Path) -> dict:
    source, destination = source.resolve(), destination.resolve()
    database = source / DATABASE_FILES[0]
    if not database.is_file():
        raise BackupError("没有找到业务数据库；未创建备份。")
    if destination.exists():
        raise BackupError("备份目录已存在；请选择新目录，避免覆盖。")
    # A reserved write lock prevents start/resume while both DB snapshots are copied.
    # Back up through independent read connections: backing up the locked connection
    # itself would wait indefinitely on its own open write transaction.
    with closing(sqlite3.connect(database, timeout=5)) as guard:
        try:
            guard.execute("BEGIN IMMEDIATE")
            active = guard.execute(
                "SELECT count(*) FROM agent_runs WHERE status IN ('queued', 'running')"
            ).fetchone()[0]
            if active:
                raise BackupError("存在正在运行或排队的研究，请等待其暂停或结束后备份。")
            resumable = guard.execute(
                "SELECT count(*) FROM agent_runs WHERE status IN ('needs_input', 'interrupted')"
            ).fetchone()[0]
            if resumable and not (source / DATABASE_FILES[1]).is_file():
                raise BackupError("存在待恢复研究但检查点文件缺失；不能生成完整恢复备份。")
            guard.execute("SELECT version_num FROM alembic_version").fetchall()
            destination.mkdir(mode=0o700, parents=True, exist_ok=False)
            files = []
            for name in DATABASE_FILES:
                original = source / name
                if not original.is_file():
                    if name == DATABASE_FILES[0]:
                        raise BackupError("业务数据库不存在。")
                    continue
                target = destination / name
                with (
                    closing(connect_readonly(original)) as reader,
                    closing(sqlite3.connect(target)) as writer,
                ):
                    reader.backup(writer)
                target.chmod(0o600)
                verify_sqlite(target)
                files.append(
                    {"name": name, "sha256": digest(target), "bytes": target.stat().st_size}
                )
            manifest = {
                "format_version": 1,
                "created_at": utc_now().isoformat(),
                "files": files,
                "contains_private_data": True,
                "browser_sessions_included": False,
            }
            manifest_path = destination / "manifest.json"
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            manifest_path.chmod(0o600)
            return manifest
        except sqlite3.Error:
            raise BackupError("数据库忙、结构不兼容或备份失败；未生成可用备份清单。") from None
        finally:
            guard.rollback()


def verify_backup(source: Path) -> dict:
    source = source.resolve()
    manifest_path = source / "manifest.json"
    if (
        manifest_path.is_symlink()
        or not manifest_path.is_file()
        or manifest_path.stat().st_size > 8192
    ):
        raise BackupError("缺少有效备份清单。")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = manifest["files"]
        if manifest["format_version"] != 1 or not isinstance(files, list):
            raise ValueError()
        names = [entry["name"] for entry in files]
        if not names or names[0] != DATABASE_FILES[0] or len(names) != len(set(names)):
            raise ValueError()
        if any(name not in DATABASE_FILES for name in names):
            raise ValueError()
        for entry in files:
            path = source / entry["name"]
            if (
                path.is_symlink()
                or not path.is_file()
                or digest(path) != entry["sha256"]
                or path.stat().st_size != entry["bytes"]
            ):
                raise BackupError("备份文件缺失或校验值不符；拒绝恢复。")
            verify_sqlite(path)
        return manifest
    except (ValueError, KeyError, TypeError, sqlite3.Error, OSError):
        raise BackupError("备份清单或数据库不合法；拒绝恢复。") from None


def restore_data(source: Path, destination: Path) -> dict:
    source, destination = source.resolve(), destination.resolve()
    manifest = verify_backup(source)
    if destination.exists():
        raise BackupError("恢复只写入新目录，不覆盖正在使用的数据。")
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    for entry in manifest["files"]:
        target = destination / entry["name"]
        shutil.copyfile(source / entry["name"], target)
        target.chmod(0o600)
        if digest(target) != entry["sha256"]:
            raise BackupError("恢复期间源文件发生变化；不要使用此次恢复目录。")
        verify_sqlite(target)
    return manifest
