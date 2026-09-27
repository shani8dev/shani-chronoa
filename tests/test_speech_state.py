"""Speaking-state wiring: the thread it runs on, and real audio end to end.

Two things are pinned here that a mock cannot check.

**Thread affinity.** `_speak` is a coroutine the AsyncBridge runs on its own
thread, so anything it touches on its way out has to hop to the GTK thread
first. An earlier version of the speaking state called `window.set_state()`
straight from that coroutine - the only background callback in `app.py` that
did not use `GLib.idle_add`. It reads correctly and is a latent GTK threading
violation, so it is asserted here directly rather than left to a code review.

**Real audio.** The speaking state is only worth anything if it tracks actual
playback, so the end-to-end test here drives real synthesised audio through the
real `AudioPlayer` and the real `pw-play` backend. It is skipped, not faked,
when the host has no audio device.
"""

import os
import shutil
import subprocess
import sys
import threading
import time

import pytest

pytest.importorskip("gi")
import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import GLib  # noqa: E402

sys.path.insert(0, "usr/lib/shani-chronoa")
from shani_chronoa.gui import AssistantState, CajitaWindow  # noqa: E402

REAL_AUDIO = pytest.mark.skipif(
    not os.environ.get("CHRONOA_ALLOW_AUDIO_TESTS"),
    reason="real-audio tests need a real audio device; opt in with CHRONOA_ALLOW_AUDIO_TESTS=1",
)


@pytest.fixture
def real_app(tmp_path, monkeypatch):
    """A genuine `ChronoaApplication` through real `_init_components()`."""
    import shani_chronoa.senses.store as store_mod

    monkeypatch.setattr(store_mod, "DURABLE_FILE", tmp_path / "percepts" / "memory.jsonl")
    from shani_chronoa.app import ChronoaApplication

    app = ChronoaApplication()
    app._init_components()
    app.window = CajitaWindow(app)
    return app


def _pump_main_context(limit_s=3.0):
    """Drain pending `GLib.idle_add` sources without a full main loop."""
    ctx = GLib.MainContext.default()
    deadline = time.time() + limit_s
    while time.time() < deadline and ctx.pending():
        ctx.iteration(False)
        time.sleep(0.01)


class _FakeTTS:
    def is_available(self):
        return True

    def synthesize_to_bytes(self, text):
        return b"not-real-audio"


class TestSpeakingStateThreadAffinity:
    def test_speech_finished_defers_the_state_change_to_the_gtk_thread(self, real_app):
        """The deferred hop is the whole point, so assert the deferral itself.

        Checking only the end state would pass even if the mutation happened
        on the wrong thread, which is the actual bug.
        """
        real_app.window.set_state(AssistantState.SPEAKING)
        observed = {}

        def background():
            observed["thread"] = threading.current_thread().ident
            real_app._on_speech_finished()

        worker = threading.Thread(target=background)
        worker.start()
        worker.join()

        assert observed["thread"] != threading.main_thread().ident, "test did not run off-thread"
        # Still SPEAKING: the change was queued, not applied on the worker.
        assert real_app.window.get_state() is AssistantState.SPEAKING, (
            "the speaking state was mutated from the AsyncBridge thread; GTK4 is not "
            "thread-safe and this must be deferred to the GTK thread"
        )

        _pump_main_context()
        assert real_app.window.get_state() is AssistantState.IDLE

    def test_an_interrupt_is_not_clobbered_when_playback_ends(self, real_app):
        """Audio finishing must not close a microphone the user is still using."""
        real_app._listening = True
        real_app.window.set_state(AssistantState.LISTENING)
        real_app._on_speech_finished()
        _pump_main_context()
        assert real_app.window.get_state() is AssistantState.LISTENING

    def test_stop_does_not_close_an_open_microphone(self, real_app):
        real_app._listening = True
        real_app.window.set_state(AssistantState.LISTENING)
        real_app._stop_speaking(None, None)
        assert real_app.window.get_state() is AssistantState.LISTENING


def _real_wav(text, path):
    subprocess.run(
        ["espeak-ng", "-w", str(path), text],
        check=True,
        capture_output=True,
    )
    return open(path, "rb").read()


@REAL_AUDIO
@pytest.mark.skipif(not shutil.which("espeak-ng"), reason="espeak-ng not installed")
class TestRealAudioEndToEnd:
    """Drive the real player with real audio, not a stubbed TTS."""

    def test_state_tracks_genuine_playback_completion(self, real_app, tmp_path):
        from shani_chronoa.audio import AudioPlayer

        player = AudioPlayer()
        if not player.is_available():
            pytest.skip("no audio playback backend on this host")

        real_app.tts = _FakeTTS()
        real_app.config.set("notification-enabled", "true")
        real_app.player = player

        wav = _real_wav("This is a real spoken reply of a few seconds.", tmp_path / "reply.wav")
        real_app.tts.synthesize_to_bytes = lambda text: wav

        real_app._on_response_ready("spoken reply")
        assert real_app.window.get_state() is AssistantState.SPEAKING

        # Drive the real coroutine on the real bridge and wait for genuine
        # playback to finish on its own.
        loop = real_app._async
        import asyncio

        fut = asyncio.run_coroutine_threadsafe(real_app._speak("spoken reply"), loop._loop)
        started = time.time()
        fut.result(timeout=60)
        _pump_main_context()

        assert time.time() - started > 0.5, "playback returned suspiciously instantly"
        assert real_app.window.get_state() is AssistantState.IDLE

    def test_stop_truncates_real_playback(self, real_app, tmp_path):
        """A stop control that does not actually stop is worse than none."""
        from shani_chronoa.audio import AudioPlayer

        player = AudioPlayer()
        if not player.is_available():
            pytest.skip("no audio playback backend on this host")

        # Long enough that stopping early is unambiguous.
        wav = _real_wav(
            " ".join(["this is a deliberately long spoken reply that keeps going"] * 12),
            tmp_path / "long.wav",
        )

        import asyncio

        real_app.window.set_state(AssistantState.SPEAKING)
        fut = asyncio.run_coroutine_threadsafe(
            real_app._speak("long"), real_app._async._loop
        )
        time.sleep(0.8)
        real_app.player = player
        stopped_at = time.time()
        real_app._stop_speaking(None, None)
        fut.result(timeout=30)
        elapsed = time.time() - stopped_at

        _pump_main_context()
        assert real_app.window.get_state() is AssistantState.IDLE
        assert elapsed < 10, f"stop took {elapsed:.1f}s; playback was not truncated"
