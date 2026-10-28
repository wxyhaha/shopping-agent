import json
import os
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from app.browser_login import BrowserLoginError, BrowserLoginManager
from app.config import Settings
from tests.test_research_agent import application, seed


class Page:
    def goto(self, url, **kwargs):
        self.url = url

    def is_closed(self):
        return False

    def wait_for_timeout(self, _):
        time.sleep(0.01)


class Context:
    def __init__(self):
        self.closed = False

    def new_page(self):
        return Page()

    def close(self):
        self.closed = True

    def storage_state(self, **_):
        return {
            "cookies": [
                {"domain": ".jd.com", "name": "synthetic", "value": "test"},
                {"domain": ".unrelated.invalid", "name": "unrelated", "value": "test"},
            ],
            "origins": [
                {"origin": "https://item.jd.com", "localStorage": []},
                {"origin": "https://unrelated.invalid", "localStorage": []},
            ],
        }


def launcher(context):
    @contextmanager
    def factory():
        def launch(**kwargs):
            assert kwargs["headless"] is False
            return SimpleNamespace(new_context=lambda **_: context, close=lambda: None)

        yield SimpleNamespace(chromium=SimpleNamespace(launch=launch))

    return factory


def wait_state(manager, task_id="task", item_id="item", expected="awaiting_user"):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        state = manager.read(task_id, item_id)
        if state["status"] == expected:
            return state
        if state["status"] == "failed":
            raise AssertionError(state["error"])
        time.sleep(0.02)
    raise AssertionError("login state timeout")


def test_user_login_single_window_save_private_state_and_reuse(tmp_path):
    context = Context()
    manager = BrowserLoginManager(
        Settings(_env_file=None, data_dir=tmp_path), launcher=launcher(context)
    )
    try:
        opened = manager.start("task", "item", "https://item.jd.com/100001.html")
        state = wait_state(manager)
        assert (
            manager.start("task", "item", "https://item.jd.com/100001.html")["id"] == opened["id"]
        )
        with pytest.raises(BrowserLoginError):
            manager.start("other", "item", "https://item.jd.com/100001.html")
        assert manager.read("other", "item") is None
        assert not manager.settings.browser_session_dir.exists()
        saved = manager.command("task", "item", state["id"], "save")
        assert saved["status"] == "saved"
        assert context.closed
        path = manager.settings.browser_session_dir / "jd.json"
        data = json.loads(path.read_text())
        assert [cookie["domain"] for cookie in data["cookies"]] == [".jd.com"]
        assert len(data["origins"]) == 1
        assert path.stat().st_mode & 0o777 == 0o600
        assert manager.command("task", "item", state["id"], "save")["status"] == "saved"
    finally:
        manager.close()


def test_cancel_and_expire_do_not_save_state(tmp_path):
    for action in ["cancel", "expire"]:
        manager = BrowserLoginManager(
            Settings(_env_file=None, data_dir=tmp_path),
            launcher=launcher(Context()),
            ttl_seconds=0.15,
        )
        try:
            state = manager.start("task", "item", "https://item.jd.com/100001.html")
            if action == "cancel":
                manager.command("task", "item", state["id"], "cancel")
            manager.done.wait(2)
            assert manager.read("task", "item")["status"] == (
                "expired" if action == "expire" else "cancelled"
            )
            assert not manager.settings.browser_session_dir.exists()
        finally:
            manager.close()


def test_routes_require_task_scope_and_reject_foreign_origins(tmp_path):
    app = application(tmp_path / "login.sqlite3", lambda *_: None)
    app.state.browser_login.launcher = launcher(Context())
    app.state.browser_login.settings = Settings(_env_file=None, data_dir=tmp_path)
    with TestClient(app) as client:
        task_id = seed(client)
        item_id = client.get(f"/api/v1/tasks/{task_id}").json()["items"][0]["id"]
        path = f"/api/v1/tasks/{task_id}/items/{item_id}/browser-login"
        assert client.post(path, headers={"Origin": "https://untrusted.invalid"}).status_code == 403
        assert client.post(path.replace(task_id, "wrong-task")).status_code == 404
        started = client.post(path)
        assert started.status_code == 202
        state = wait_state(app.state.browser_login, task_id, item_id)
        assert client.post(f"{path}/wrong-id/save").status_code == 409
        response = client.post(f"{path}/{state['id']}/save")
        assert response.status_code == 200
        assert response.json()["status"] == "saved"
        assert "cookies" not in response.text and "storage" not in response.text


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="Requires a visible WSL/X desktop")
def test_actual_headed_chromium_window_and_save(tmp_path):
    @contextmanager
    def factory():
        with sync_playwright() as p:

            def launch(**kwargs):
                browser = p.chromium.launch(**kwargs)

                def context(**options):
                    result = browser.new_context(**options)
                    result.route(
                        "**/*",
                        lambda route: route.fulfill(
                            content_type="text/html; charset=utf-8",
                            body=(
                                "<title>Shopping Agent 登录窗口测试</title>"
                                "<h1>独立可见浏览器测试，不需要登录账号</h1>"
                            ),
                        ),
                    )
                    return result

                return SimpleNamespace(new_context=context, close=browser.close)

            yield SimpleNamespace(chromium=SimpleNamespace(launch=launch))

    manager = BrowserLoginManager(Settings(_env_file=None, data_dir=tmp_path), launcher=factory)
    try:
        state = manager.start("task", "item", "https://item.jd.com/100001.html")
        wait_state(manager)
        assert manager.command("task", "item", state["id"], "save")["status"] == "saved"
        assert (manager.settings.browser_session_dir / "jd.json").exists()
    finally:
        manager.close()
