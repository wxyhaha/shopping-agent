"""User-initiated visible browser login. No model or tool can operate this window."""

import json
import os
import queue
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from app.background_collector import product_navigation_url, session_name
from app.capture_urls import product_url_identity
from app.config import Settings

ACTIVE = {"opening", "awaiting_user", "saving"}


def save_private_state(context, path: Path, platform: str):
    """Save only this platform's state; never expose cookies via the API or logs."""
    suffixes = ("taobao.com", "tmall.com") if platform == "taobao" else ("jd.com",)

    def allowed(host):
        host = (host or "").lower().lstrip(".")
        return any(host == suffix or host.endswith(f".{suffix}") for suffix in suffixes)

    state = context.storage_state(indexed_db=True)
    state["cookies"] = [cookie for cookie in state["cookies"] if allowed(cookie["domain"])]
    state["origins"] = [
        entry for entry in state["origins"] if allowed(urlsplit(entry["origin"]).hostname)
    ]
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(state, stream, ensure_ascii=False)
            stream.write("\n")
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class BrowserLoginError(Exception):
    pass


class BrowserLoginManager:
    def __init__(self, settings: Settings, *, launcher=sync_playwright, ttl_seconds=600):
        self.settings = settings
        self.launcher = launcher
        self.ttl_seconds = ttl_seconds
        self.lock = threading.RLock()
        self.state = None
        self.commands = queue.Queue()
        self.thread = None
        self.done = threading.Event()
        self.closing = False

    def read(self, task_id, item_id):
        with self.lock:
            if self.state and (self.state["task_id"], self.state["item_id"]) == (task_id, item_id):
                return dict(self.state)
        return None

    def _set(self, status, error=None):
        with self.lock:
            self.state.update(status=status, error=error)

    def start(self, task_id, item_id, url):
        identity = product_url_identity(url)
        if not identity:
            raise BrowserLoginError("此链接不是支持的商品详情页。")
        with self.lock:
            if self.closing:
                raise BrowserLoginError("服务正在关闭，请稍后重试。")
            if self.thread and self.thread.is_alive():
                current = self.read(task_id, item_id)
                if current:
                    return current
                raise BrowserLoginError("另一个商品的登录窗口仍在使用中，请先关闭或完成。")
            self.state = {
                "id": str(uuid4()),
                "task_id": task_id,
                "item_id": item_id,
                "platform": session_name(identity.platform),
                "status": "opening",
                "error": None,
            }
            self.commands = queue.Queue()
            self.done = threading.Event()
            self.thread = threading.Thread(
                target=self._work, args=(identity, url), daemon=True, name="shopping-browser-login"
            )
            self.thread.start()
            return dict(self.state)

    def command(self, task_id, item_id, login_id, action):
        with self.lock:
            state = self.read(task_id, item_id)
            if not state or state["id"] != login_id:
                raise BrowserLoginError("登录窗口不存在或已被替换，请刷新状态。")
            if action == "save" and state["status"] == "saved":
                return state
            if state["status"] == "saving":
                raise BrowserLoginError("会话正在保存，请稍后刷新状态。")
            if state["status"] not in ACTIVE:
                raise BrowserLoginError("登录窗口已经结束，请重新打开。")
            if action == "save" and state["status"] != "awaiting_user":
                raise BrowserLoginError("请等待登录窗口加载完成后再保存。")
            self._set("saving" if action == "save" else "cancelled")
            self.commands.put(action)
        if action == "save":
            if not self.done.wait(timeout=12):
                raise BrowserLoginError("会话仍在保存，请刷新状态后继续，勿重复登录。")
            state = self.read(task_id, item_id)
            if state["status"] != "saved":
                raise BrowserLoginError(state["error"] or "未保存会话，请重新打开登录窗口。")
        return self.read(task_id, item_id)

    def _work(self, identity, url):
        browser = None
        try:
            with self.launcher() as playwright:
                browser = playwright.chromium.launch(headless=False, timeout=15000)
                path = self.settings.browser_session_dir / f"{session_name(identity.platform)}.json"
                context = browser.new_context(
                    locale="zh-CN",
                    accept_downloads=False,
                    storage_state=str(path) if path.is_file() else None,
                )
                try:
                    page = context.new_page()
                    try:
                        page.goto(
                            product_navigation_url(identity, url),
                            wait_until="domcontentloaded",
                            timeout=20000,
                        )
                    except PlaywrightTimeout:
                        pass  # The visible page can still be used for manual login.
                    with self.lock:
                        if self.state["status"] != "cancelled":
                            self._set("awaiting_user")
                    deadline = time.monotonic() + self.ttl_seconds
                    while time.monotonic() < deadline and not self.closing:
                        try:
                            action = self.commands.get_nowait()
                        except queue.Empty:
                            action = None
                        if action == "cancel" or page.is_closed():
                            self._set("cancelled")
                            return
                        if action == "save":
                            save_private_state(context, path, session_name(identity.platform))
                            self._set("saved")
                            return
                        try:
                            page.wait_for_timeout(200)
                        except PlaywrightError:
                            if page.is_closed():
                                self._set("cancelled")
                                return
                            raise
                    self._set("expired" if not self.closing else "cancelled")
                finally:
                    context.close()
                    browser.close()
        except (PlaywrightError, OSError, ValueError):
            self._set("failed", "无法打开或保存独立登录窗口。请检查后台图形桌面；也可用扩展接管。")
        finally:
            self.done.set()

    def close(self):
        self.closing = True
        self.commands.put("cancel")
        if self.thread:
            self.thread.join(timeout=40)
