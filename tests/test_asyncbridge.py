"""RED tests: do_shutdown() must stop and join the AsyncBridge thread.

Each test fails for a named defect in the current code (failing-first / RED phase).
"""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

import pytest


class TestShutdownCleanup:
    """App shutdown must tear down the AsyncBridge worker thread."""

    def test_do_shutdown_stops_async_bridge_thread(self, stubbed_app, monkeypatch):
        # Given: a running app with a live AsyncBridge
        from shani_chronoa.asyncbridge import AsyncBridge
        stubbed_app._async = AsyncBridge()
        try:
            assert stubbed_app._async._thread.is_alive()
            # And: the GTK chain-up is neutralized (no D-Bus unregister in tests)
            monkeypatch.setattr(Gtk.Application, "do_shutdown", lambda self: None)
            # When: the app shuts down
            stubbed_app.do_shutdown()
            # Then: the bridge thread must be stopped and joined
            assert not stubbed_app._async._thread.is_alive()
        finally:
            # Hermetic cleanup: the assertion above already ran; this only stops
            # the daemon thread so it does not leak across the test session.
            stubbed_app._async.shutdown()

    def test_shutdown_cancels_in_flight_coroutine(self):
        # Given: a bridge with an in-flight coroutine that never completes on its own
        import asyncio
        import threading
        from shani_chronoa.asyncbridge import AsyncBridge
        bridge = AsyncBridge()
        started = threading.Event()
        cancelled = threading.Event()

        async def _never_ends():
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        bridge.run(_never_ends())
        assert started.wait(timeout=2)
        try:
            # When: the bridge shuts down
            bridge.shutdown()
            # Then: the in-flight coroutine was cancelled before shutdown returned
            assert cancelled.is_set()
            assert not bridge._thread.is_alive()
        finally:
            bridge.shutdown()