"""A question that starts the local model waits for it to load, off the main loop.

Found by an AI agent driving Chronoa in a shani-testbed slot (2026-10-23): with a
model installed but its server stopped, asking a question logged "llama-server is
not answering; starting it" and, in the same second, "the local model server was
started but is not answering" - and the question was never answered.
`start_service()` returns when `systemctl restart` does, before the weights are in
memory, and `_use_local_server` probed `is_up()` once straight afterwards.
"""

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from gi.repository import GLib  # noqa: E402

from shani_chronoa import local_llm  # noqa: E402
from shani_chronoa.app import brain  # noqa: E402


class _Fake(brain.BrainMixin):
    window = None
    assistant = None


def _loading_server(monkeypatch, answers_after: int):
    probes = {"n": 0}

    def is_up(timeout=1.5):
        probes["n"] += 1
        return probes["n"] > answers_after
    monkeypatch.setattr(local_llm, "is_up", is_up)
    monkeypatch.setattr(local_llm, "start_service", lambda: "")
    monkeypatch.setattr(local_llm, "LocalLLM", lambda: object())
    return probes


def test_a_started_but_loading_server_is_reported_as_loading(monkeypatch):
    _loading_server(monkeypatch, answers_after=3)
    app = _Fake()
    assert app._use_local_server() is False
    assert app._local_server_loading is True


def test_the_turn_runs_once_the_model_answers(monkeypatch):
    _loading_server(monkeypatch, answers_after=3)
    app = _Fake()
    app._use_local_server()
    loop = GLib.MainLoop()
    seen = []
    caller = threading.current_thread()

    def then():
        seen.append(("then", threading.current_thread() is caller))
        loop.quit()

    def otherwise():
        seen.append(("otherwise", None))
        loop.quit()

    app._wait_for_local_server(then, otherwise, timeout=20)
    GLib.timeout_add_seconds(30, loop.quit)
    loop.run()
    assert seen == [("then", True)], seen      # answered, and on the main loop
    assert app._local_server_loading is False
    assert app._ollama_available is True


def test_a_model_that_never_answers_gives_up_and_says_so(monkeypatch):
    _loading_server(monkeypatch, answers_after=10**9)
    app = _Fake()
    app._use_local_server()
    loop = GLib.MainLoop()
    seen = []
    app._wait_for_local_server(lambda: (seen.append("then"), loop.quit()),
                               lambda: (seen.append("otherwise"), loop.quit()), timeout=2)
    GLib.timeout_add_seconds(15, loop.quit)
    loop.run()
    assert seen == ["otherwise"]
