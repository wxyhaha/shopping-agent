"""Bounded process supervision for browser work, including blocked driver callbacks."""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil

BACKEND_ROOT = Path(__file__).resolve().parents[1]
MAX_RESULT_BYTES = 576 * 1024
MAX_REQUEST_BYTES = 32768


class CollectionProcessError(Exception):
    pass


class CollectionDeadline(CollectionProcessError):
    pass


def stop_process_tree(process, tracked):
    """Only stop this worker and its recorded descendants; preserve unrelated browsers."""
    if process.poll() is None:
        try:
            root = psutil.Process(process.pid)
            tracked.update({child.pid: child for child in root.children(recursive=True)})
            tracked[root.pid] = root
        except psutil.Error:
            pass
    targets = list(tracked.values())
    for target in reversed(targets):
        try:
            target.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(targets, timeout=1)
    for target in alive:
        try:
            target.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(alive, timeout=1)
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=1)


def run_collection_process(payload, *, deadline, check_cancel, command=None):
    """The parent owns the clock; it never waits on Playwright's own event loop."""
    tracked = {}
    with tempfile.TemporaryDirectory(prefix="shopping-collection-") as directory:
        output = Path(directory) / "result.json"
        request = Path(directory) / "request.json"
        payload = {**payload, "output_path": output.as_posix()}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(data) > MAX_REQUEST_BYTES:
            raise CollectionProcessError("worker input too large")
        request.write_bytes(data)
        check_cancel()
        if time.monotonic() >= deadline:
            raise CollectionDeadline()
        process = subprocess.Popen(
            [*(command or [sys.executable, "-m", "app.collection_worker"]), str(request)],
            cwd=BACKEND_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=os.name == "posix",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            # Request and result files live in a private directory. No pipe write/read can
            # block supervision, and product URLs/session paths never appear in argv/logs.
            while True:
                check_cancel()
                if time.monotonic() >= deadline:
                    raise CollectionDeadline()
                try:
                    root = psutil.Process(process.pid)
                    tracked.update({child.pid: child for child in root.children(recursive=True)})
                except psutil.Error:
                    pass
                if process.poll() is not None:
                    break
                time.sleep(min(0.15, max(0, deadline - time.monotonic())))
            if (
                process.returncode != 0
                or not output.is_file()
                or output.stat().st_size > MAX_RESULT_BYTES
            ):
                raise CollectionProcessError("worker failed or returned oversized output")
            try:
                result = json.loads(output.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                raise CollectionProcessError("invalid worker result") from None
            if not isinstance(result, dict):
                raise CollectionProcessError("invalid worker result")
            return result
        finally:
            stop_process_tree(process, tracked)
