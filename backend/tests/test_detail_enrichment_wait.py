"""A reader must wait for the follow-up detail-image read instead of racing it."""

import time

from app.research_agent import ResearchRuntime


class _Record:
    def __init__(self, state):
        self.detail_enrichment = state


class _Session:
    def __init__(self, states):
        self.states = list(states)
        self.reads = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, _model, _id):
        self.reads += 1
        state = self.states[min(self.reads - 1, len(self.states) - 1)]
        return _Record(state)


def _runtime(states, monkeypatch):
    """Build a runtime whose sessions yield the given enrichment states in order."""
    runtime = object.__new__(ResearchRuntime)
    sessions = _Session(states)

    def factory():
        return sessions

    runtime.sessions = factory
    return runtime, sessions


def test_returns_immediately_when_already_done(monkeypatch):
    runtime, sessions = _runtime(["done"], monkeypatch)
    started = time.monotonic()
    runtime._await_detail_enrichment("snap", deadline=time.monotonic() + 10)
    assert time.monotonic() - started < 1
    assert sessions.reads == 1


def test_waits_until_pending_clears(monkeypatch):
    runtime, sessions = _runtime(["pending", "pending", "done"], monkeypatch)
    monkeypatch.setattr("app.research_agent.time.sleep", lambda _s: None)
    runtime._await_detail_enrichment("snap", deadline=time.monotonic() + 30)
    assert sessions.reads == 3


def test_missing_snapshot_does_not_block(monkeypatch):
    runtime = object.__new__(ResearchRuntime)

    class _Empty:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, _model, _id):
            return None

    runtime.sessions = lambda: _Empty()
    started = time.monotonic()
    runtime._await_detail_enrichment("gone", deadline=time.monotonic() + 10)
    assert time.monotonic() - started < 1


def test_wait_is_bounded_by_the_run_deadline(monkeypatch):
    """A pending read must never push the run past its own deadline."""
    runtime, _ = _runtime(["pending"], monkeypatch)
    monkeypatch.setattr("app.research_agent.time.sleep", lambda _s: time.sleep(0.01))
    started = time.monotonic()
    # Deadline already nearly elapsed: the wait must end long before the 90 s default.
    runtime._await_detail_enrichment("snap", deadline=time.monotonic() + 0.05)
    assert time.monotonic() - started < 1
