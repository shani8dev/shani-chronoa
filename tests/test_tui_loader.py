"""PanelLoader: bounded background fetches, no bus and no terminal.

A fake client blocks on a threading.Event, so a call that waited on the worker
would hang the test. The assertions measure that start()/request() return at
once, that the timeout reaches the client, that failures are captured, and
that a stale generation never overwrites a newer one.
"""

import threading
import time

from shani_chronoa.tui.loader import PanelLoader


def _wait(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _data(loader, section):
    entry = loader.snapshot(section)
    return entry["data"] if entry is not None else None


class BlockingClient:
    def __init__(self, gate):
        self.gate = gate
        self.calls = []

    def inventory(self, section, *, timeout_ms=500):
        self.calls.append((section, timeout_ms))
        self.gate.wait(timeout=3)
        return {"section": section, "items": []}


class FastClient:
    def __init__(self, error_sections=()):
        self.error_sections = set(error_sections)
        self.calls = []

    def inventory(self, section, *, timeout_ms=500):
        self.calls.append((section, timeout_ms))
        if section in self.error_sections:
            raise RuntimeError(f"{section} went away")
        return {"section": section, "items": [{"task": "conversion"}]}


def test_start_and_request_never_block_the_caller():
    gate = threading.Event()
    loader = PanelLoader(BlockingClient(gate), ("status", "models"), timeout_ms=250)
    try:
        before = time.monotonic()
        loader.start()
        assert time.monotonic() - before < 0.2
        before = time.monotonic()
        loader.request("status")
        assert time.monotonic() - before < 0.2
        assert _wait(lambda: len(loader.client.calls) == 1)
        gate.set()  # let the blocked call finish so the queued ones can run
        assert _wait(lambda: len(loader.client.calls) == 3)
        assert all(timeout == 250 for _, timeout in loader.client.calls)
    finally:
        gate.set()
        loader.stop()


def test_errors_are_captured_and_do_not_kill_the_worker():
    loader = PanelLoader(FastClient(error_sections={"status"}), ("status", "models"))
    loader.start()
    try:
        assert _wait(
            lambda: (
                loader.snapshot("status") is not None
                and loader.snapshot("models") is not None
            )
        )
        status = loader.snapshot("status")
        assert status["error"] == "status went away"
        assert status["data"] == {"error": "status went away"}
        assert set(status) == {"data", "at", "error", "generation"}
        # The worker survived: the section after the failure still landed.
        assert loader.snapshot("models")["data"]["items"][0]["task"] == "conversion"
    finally:
        loader.stop()


def test_stale_generation_is_dropped():
    gate = threading.Event()
    calls = []

    client = FastClient()
    original = client.inventory

    def inventory(section, *, timeout_ms=500):
        calls.append(section)
        if len(calls) == 1:
            gate.wait(timeout=3)
            return {"section": section, "items": ["old"]}
        return original(section, timeout_ms=timeout_ms)

    client.inventory = inventory
    loader = PanelLoader(client, ("models",))
    loader.start()
    try:
        assert _wait(lambda: len(calls) == 1)
        loader.mark_stale("models")
        loader.request("models", force=True)
        gate.set()
        assert _wait(
            lambda: (
                (_data(loader, "models") or {}).get("items") == [{"task": "conversion"}]
            )
        )
        # Only the fresh answer was published; the old one was discarded.
        assert loader.drain()["models"]["data"]["items"][0]["task"] == "conversion"
    finally:
        gate.set()
        loader.stop()


def test_recent_request_is_skipped_and_drain_is_one_shot():
    loader = PanelLoader(FastClient(), ("models",))
    loader.start()
    try:
        assert _wait(lambda: loader.snapshot("models") is not None)
        assert len(loader.drain()) == 1
        assert loader.drain() == {}
        loader.request("models")  # recent, not forced
        time.sleep(0.05)
        assert len(loader.client.calls) == 1
        loader.request("models", force=True)
        assert _wait(lambda: len(loader.client.calls) == 2)
    finally:
        loader.stop()


def test_stop_joins_the_worker():
    loader = PanelLoader(FastClient(), ("status",))
    loader.start()
    assert _wait(lambda: loader.snapshot("status") is not None)
    thread = loader._thread
    loader.stop()
    assert not thread.is_alive()
    # idempotent, and a later request must not queue work after stop
    before = len(loader.client.calls)
    loader.stop()
    loader.request("status", force=True)
    assert len(loader.client.calls) == before


def test_snapshot_of_unknown_section_is_none():
    loader = PanelLoader(FastClient(), ())
    assert loader.snapshot("status") is None
    assert loader.drain() == {}
