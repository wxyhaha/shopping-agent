from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import URL

from app.main import create_app


def make_client(database_path: Path) -> TestClient:
    database_url = URL.create("sqlite", database=database_path.as_posix())
    return TestClient(create_app(database_url=database_url))


def create_payload(url: str = "https://item.jd.com/100000000001.html") -> dict[str, object]:
    return {
        "title": "卧室空气净化器",
        "items": [{"url": url, "page_title": "商品页", "source": "web", "browser": None}],
    }


def test_create_task_persists_links_and_returns_not_started_state(tmp_path: Path) -> None:
    with make_client(tmp_path / "tasks.sqlite3") as client:
        response = client.post(
            "/api/v1/tasks",
            json=create_payload(),
            headers={"Idempotency-Key": "create-task-0001"},
        )

    assert response.status_code == 201
    task = response.json()
    assert task["title"] == "卧室空气净化器"
    assert task["item_count"] == 1
    assert task["items"][0]["url"] == "https://item.jd.com/100000000001.html"
    assert task["items"][0]["capture_status"] == "not_started"


def test_task_creation_is_idempotent_and_rejects_key_reuse(tmp_path: Path) -> None:
    with make_client(tmp_path / "idempotency.sqlite3") as client:
        first = client.post(
            "/api/v1/tasks",
            json=create_payload(),
            headers={"Idempotency-Key": "create-task-0002"},
        )
        replay = client.post(
            "/api/v1/tasks",
            json=create_payload(),
            headers={"Idempotency-Key": "create-task-0002"},
        )
        conflict = client.post(
            "/api/v1/tasks",
            json=create_payload("https://item.jd.com/100000000002.html"),
            headers={"Idempotency-Key": "create-task-0002"},
        )

    assert first.status_code == replay.status_code == 201
    assert first.json()["id"] == replay.json()["id"]
    assert conflict.status_code == 409


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "https://user:password@example.com/item",
        "https://example.com:invalid/item",
    ],
)
def test_rejects_unsafe_or_malformed_urls(tmp_path: Path, url: str) -> None:
    with make_client(tmp_path / "invalid-url.sqlite3") as client:
        response = client.post(
            "/api/v1/tasks",
            json=create_payload(url),
            headers={"Idempotency-Key": "invalid-url-0001"},
        )

    assert response.status_code == 422


def test_add_item_is_idempotent_and_scoped_to_task(tmp_path: Path) -> None:
    with make_client(tmp_path / "add-item.sqlite3") as client:
        first_task = client.post(
            "/api/v1/tasks",
            json=create_payload(),
            headers={"Idempotency-Key": "create-task-0010"},
        ).json()
        second_task = client.post(
            "/api/v1/tasks",
            json=create_payload("https://item.jd.com/100000000002.html"),
            headers={"Idempotency-Key": "create-task-0011"},
        ).json()
        item_payload = {
            "url": "https://detail.tmall.com/item.htm?id=12345",
            "page_title": "来自浏览器的商品",
            "source": "extension",
            "browser": "edge",
        }
        headers = {"Idempotency-Key": "capture-item-0001"}
        first = client.post(
            f"/api/v1/tasks/{first_task['id']}/items",
            json=item_payload,
            headers=headers,
        )
        replay = client.post(
            f"/api/v1/tasks/{first_task['id']}/items",
            json=item_payload,
            headers=headers,
        )
        other_task = client.post(
            f"/api/v1/tasks/{second_task['id']}/items",
            json=item_payload,
            headers=headers,
        )

    assert first.status_code == replay.status_code == 201
    assert first.json()["id"] == replay.json()["id"]
    assert other_task.status_code == 409
    assert replay.json()["browser"] == "edge"


def test_missing_task_returns_not_found(tmp_path: Path) -> None:
    with make_client(tmp_path / "missing-task.sqlite3") as client:
        response = client.get("/api/v1/tasks/does-not-exist")

    assert response.status_code == 404


def test_task_survives_application_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "restart.sqlite3"
    with make_client(database_path) as first_client:
        created = first_client.post(
            "/api/v1/tasks",
            json=create_payload(),
            headers={"Idempotency-Key": "create-task-0020"},
        ).json()

    with make_client(database_path) as restarted_client:
        ready = restarted_client.get("/health/ready")
        recovered = restarted_client.get(f"/api/v1/tasks/{created['id']}")

    assert ready.status_code == 200
    assert recovered.status_code == 200
    assert recovered.json()["id"] == created["id"]
    assert recovered.json()["items"][0]["url"] == "https://item.jd.com/100000000001.html"
