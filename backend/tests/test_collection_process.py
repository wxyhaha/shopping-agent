import json
import sys
import time

import psutil
import pytest

from app.collection_process import (
    CollectionDeadline,
    CollectionProcessError,
    run_collection_process,
)

COMMAND = [sys.executable, "-m", "tests.collection_probe_worker"]


def test_websocket_page_finishes_in_real_browser_subprocess(tmp_path):
    started = time.monotonic()
    result = run_collection_process(
        {"data_dir": tmp_path.as_posix()},
        deadline=started + 15,
        check_cancel=lambda: None,
        command=COMMAND,
    )
    assert result["snapshot"]["status"] in {"complete", "partial"}
    assert "10米" in result["snapshot"]["selected_variant"]["label"]
    assert any("42.00" in e["excerpt"] for e in result["snapshot"]["evidence"])
    assert time.monotonic() - started < 15


@pytest.mark.parametrize("cancel", [False, True])
def test_hung_worker_deadline_and_cancel_reap_descendants(tmp_path, cancel):
    marker = tmp_path / "pids.json"
    started = time.monotonic()

    class Stopped(Exception):
        pass

    def guard():
        if cancel and marker.exists():
            raise Stopped()

    # A Windows worker needs roughly three seconds to import Playwright and launch Chromium
    # before it can write the marker, so a three-second deadline races process startup
    # rather than the hang. Seven seconds keeps the hang well past the deadline on both
    # platforms while preserving the same proportional assertion margin.
    deadline_seconds = 7
    with pytest.raises(Stopped if cancel else CollectionDeadline):
        run_collection_process(
            {"mode": "hang", "pids": marker.as_posix()},
            deadline=started + deadline_seconds,
            check_cancel=guard,
            command=COMMAND,
        )
    assert time.monotonic() - started < deadline_seconds + 4
    assert marker.exists(), "Worker must start before checking cleanup"
    for pid in json.loads(marker.read_text()):
        assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    # A new request can still be served after the previous process was killed.
    with pytest.raises(CollectionProcessError):
        run_collection_process(
            {"mode": "malformed"},
            deadline=time.monotonic() + 5,
            check_cancel=lambda: None,
            command=COMMAND,
        )
