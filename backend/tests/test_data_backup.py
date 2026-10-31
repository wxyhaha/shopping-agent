import json
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from app.data_backup import BackupError, backup_data, restore_data, verify_backup
from tests.test_research_agent import (
    application,
    observations,
    report,
    seed,
    start,
    task_items,
    tool,
    wait_run,
)


def seed_database(directory, status="needs_input"):
    directory.mkdir()
    with closing(sqlite3.connect(directory / "shopping_agent.sqlite3")) as connection, connection:
        connection.executescript(
            "CREATE TABLE agent_runs(status TEXT); CREATE TABLE alembic_version(version_num TEXT);"
        )
        connection.execute("INSERT INTO agent_runs VALUES (?)", (status,))
        connection.execute("INSERT INTO alembic_version VALUES ('test')")
    with (
        closing(sqlite3.connect(directory / "agent_checkpoints.sqlite3")) as connection,
        connection,
    ):
        connection.execute("CREATE TABLE marker(value TEXT)")
        connection.execute("INSERT INTO marker VALUES ('checkpoint')")


def test_backup_restore_pair_is_verified_and_never_overwrites(tmp_path):
    source, backup, restored = (tmp_path / name for name in ("source", "backup", "restored"))
    seed_database(source)
    manifest = backup_data(source, backup)
    assert len(manifest["files"]) == 2
    assert manifest["contains_private_data"] and not manifest["browser_sessions_included"]
    assert verify_backup(backup) == manifest
    assert restore_data(backup, restored) == manifest
    with closing(sqlite3.connect(restored / "agent_checkpoints.sqlite3")) as connection:
        assert connection.execute("SELECT value FROM marker").fetchone()[0] == "checkpoint"
    with pytest.raises(BackupError, match="覆盖"):
        restore_data(backup, restored)
    with pytest.raises(BackupError, match="存在"):
        backup_data(source, backup)


@pytest.mark.parametrize("status", ["running", "queued"])
def test_running_research_prevents_backup(tmp_path, status):
    source, destination = tmp_path / "source", tmp_path / "backup"
    seed_database(source, status)
    with pytest.raises(BackupError, match="研究"):
        backup_data(source, destination)
    assert not destination.exists()


def test_missing_checkpoint_for_paused_research_is_not_a_complete_backup(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "backup"
    seed_database(source)
    (source / "agent_checkpoints.sqlite3").unlink()
    with pytest.raises(BackupError, match="检查点"):
        backup_data(source, destination)
    assert not destination.exists()


@pytest.mark.parametrize("damage", ["data", "traversal", "duplicate", "missing"])
def test_corrupt_or_unsafe_backup_does_not_create_restore_directory(tmp_path, damage):
    source, backup, destination = (tmp_path / name for name in ("source", "backup", "restore"))
    seed_database(source)
    manifest = backup_data(source, backup)
    if damage == "data":
        with (backup / "shopping_agent.sqlite3").open("ab") as stream:
            stream.write(b"tampered")
    elif damage == "traversal":
        manifest["files"][0]["name"] = "../other.sqlite3"
    elif damage == "duplicate":
        manifest["files"].append(manifest["files"][0])
    else:
        (backup / "manifest.json").unlink()
    if damage in {"traversal", "duplicate"}:
        (backup / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(BackupError):
        restore_data(backup, destination)
    assert not destination.exists()


def test_restored_paused_agent_resumes_from_checkpoint(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "app.research_routes.get_settings",
        lambda: SimpleNamespace(llm_base_url="http://gateway.invalid/v1", llm_model="test-model"),
    )

    def model(messages, _):
        seen = observations(messages)
        if not seen:
            return tool("read_product_evidence", {"item_id": task_items(messages)[0]["item_id"]})
        if not any("answer" in result for result in seen):
            return tool("ask_user", {"question": "需要什么规格？", "reason": "合成恢复验收"})
        return tool("submit_report", report(seen[0]["evidence"][0]["ref"]))

    source = tmp_path / "source"
    source.mkdir()
    with TestClient(application(source / "shopping_agent.sqlite3", model)) as client:
        task_id = seed(client)
        start(client, task_id)
        paused = wait_run(client, task_id, {"needs_input"})
        backup_data(source, tmp_path / "backup")
    restore_data(tmp_path / "backup", tmp_path / "restored")
    with TestClient(application(tmp_path / "restored" / "shopping_agent.sqlite3", model)) as client:
        assert (
            client.post(
                f"/api/v1/tasks/{task_id}/research/{paused['id']}/resume",
                json={"answer": "只报告已有证据"},
            ).status_code
            == 202
        )
        result = wait_run(client, task_id, {"completed"})
        assert result["report"]["findings"][0]["evidence_refs"]
