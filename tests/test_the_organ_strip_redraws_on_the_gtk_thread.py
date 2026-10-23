"""The organ strip redrew itself on whatever thread changed the body register.

`body.done()` notifies on the calling thread, and a tool run from a panel's
worker thread (the Triggers panel's `work`) unlights its hands there. The strip
then set labels and CSS classes off the GTK thread, and GTK aborted the
process: CI's test run ended with "Fatal Python error: Aborted" in
`OrganStrip.refresh` and every later test went unreported.
"""

from __future__ import annotations

import threading
import time

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib  # noqa: E402

from shani_chronoa import body as body_module  # noqa: E402
from shani_chronoa.gui import organs  # noqa: E402


def _pump(seconds=0.5):
    context = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(0.01)


def test_a_change_from_a_worker_thread_is_redrawn_on_the_gtk_thread(monkeypatch):
    threads = []
    original = organs.OrganStrip.refresh

    def recording(self):
        threads.append(threading.current_thread())
        original(self)

    # patched on the class before construction, so the strip's own subscription
    # is the one under test, whichever way it holds `refresh`
    monkeypatch.setattr(organs.OrganStrip, "refresh", recording)
    strip = organs.OrganStrip(None)
    try:
        threads.clear()
        worker = threading.Thread(target=body_module.body._notify)
        worker.start()
        worker.join()
        assert all(th is threading.main_thread() for th in threads), (
            "the strip redrew on the worker thread that changed the register")
        _pump()
        assert threads and all(th is threading.main_thread() for th in threads)
    finally:
        strip.dispose()


def test_a_change_on_the_gtk_thread_is_redrawn_at_once(monkeypatch):
    calls = []
    listener = organs._on_gtk_thread(lambda: calls.append(threading.current_thread()))
    listener(body_module.body)
    assert calls == [threading.main_thread()], "a main-thread change waited for an idle turn"
