"""Controlled subprocess fixtures. Never used by production collection."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
from playwright.sync_api import sync_playwright

from app.background_collector import BackgroundCollector
from app.capture_urls import product_url_identity
from app.config import Settings
from tests.test_background_collector import PAGE, fixture_route


def main():
    payload = json.loads(Path(sys.argv[1]).read_text())
    if payload.get("mode") == "hang":
        with sync_playwright() as p:
            p.chromium.launch(headless=True)
            subprocess.Popen([sys.executable, "-c", "import time; time.sleep(90)"])
            pids = [
                os.getpid(),
                *[child.pid for child in psutil.Process().children(recursive=True)],
            ]
            Path(payload["pids"]).write_text(json.dumps(pids))
            time.sleep(90)
        return
    if payload.get("mode") == "malformed":
        Path(payload["output_path"]).write_text("not json")
        return
    service = BackgroundCollector(
        Settings(_env_file=None, data_dir=Path(payload["data_dir"]), browser_max_scrolls=1),
        route_fixture=fixture_route(
            PAGE + "<script>new WebSocket('wss://ws.taobao.com/probe')</script>"
        ),
        resolver=lambda _: ["93.184.216.34"],
    )
    identity = product_url_identity("https://item.jd.com/100001.html")
    result = service._collect(identity, identity.safe_url, time.monotonic() + 15, lambda: None)
    Path(payload["output_path"]).write_text(
        json.dumps(
            {
                "snapshot": result.snapshot.model_dump(mode="json"),
                "error_code": result.error_code,
                "message": result.message,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
