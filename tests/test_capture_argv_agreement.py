"""The two capture argv builders must not drift apart.

`audio._stream_capture_cmd` and `wakeword.WakeWordListener._record_cmd` both open a
microphone: the first for a turn (`AudioRecorder.start_auto_stop`) and for the barge-in
layer (`BargeInMonitor.start`), the second for the always-on wake-word listener. They were
two independent builders, and `wakeword`'s hardcoded `"16000"`/`"1"` in *both* of its
branches, so editing `_SAMPLE_RATE` in `audio.py` left the wake-word listener recording
at the old rate with nothing complaining.

That is the whole bug class here, and it was invisible to the guard that already existed:
`tests/test_audio_generation_guard.py:748` compared only `--latency`, so a rate divergence
passed it. **Nothing is mocked away below except the backend choice**, which is a
`shutil.which` probe and not behaviour. The builders are called for real, on a real
`WakeWordListener`, through the real `__init__` - the constructor is where `_backend` and
`_target` are assigned, so driving it through the real constructor is what makes the
assertion mean anything.

Every assertion here is on the **entire argv**, list equality, not on a slice: the latency
is one token out of eleven, and comparing it alone is what let the drift through in the
first place.

The controls at the bottom exist because a suite of equality assertions can pass
vacuously if everything being compared happens to be equal anyway.
"""

from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import audio as audio_mod  # noqa: E402
from shani_chronoa import wakeword as wakeword_mod  # noqa: E402

_TARGET_NODE = "alsa_input.usb_Generic_USB_Audio.__source"


def _listener(monkeypatch, backend, target=None):
    """A real `WakeWordListener`, with only the backend probe decided.

    `_record_cmd` is callable straight after `__init__`: the constructor is where `_backend`
    and `_target` are assigned (`wakeword.py:89`/`:93`) and `start()` never touches either
    of them. So this is not a shortcut around a lifecycle requirement - it is the lifecycle.
    """
    monkeypatch.setattr(
        wakeword_mod.WakeWordListener, "_detect_backend", lambda self: backend
    )
    return wakeword_mod.WakeWordListener(target=target)


class TestBothBuildersProduceTheSameArgv:
    @pytest.mark.parametrize(
        ("backend", "target"),
        [
            ("pw-record", None),
            ("pw-record", _TARGET_NODE),
            ("pw-record", ""),
            ("arecord", None),
            ("arecord", _TARGET_NODE),
            (None, None),
        ],
    )
    def test_argv_is_identical(self, monkeypatch, backend, target):
        assert _listener(monkeypatch, backend, target)._record_cmd() == (
            audio_mod._stream_capture_cmd(backend, target or None)
        )

    def test_a_target_set_after_construction_agrees_too(self, monkeypatch):
        """`set_target()` is how a live app retargets the listener at a chosen microphone."""
        listener = _listener(monkeypatch, "pw-record")
        listener.set_target(_TARGET_NODE)
        assert listener._record_cmd() == audio_mod._stream_capture_cmd(
            "pw-record", _TARGET_NODE
        )

    def test_the_rate_follows_the_constant_at_call_time(self, monkeypatch):
        """Not a frozen copy: change the rate and the listener's argv moves with it.

        Pre-fix this fails, because `wakeword` had its own literal. That is the drift
        itself, so it is asserted directly rather than only implied by list equality.
        """
        listener = _listener(monkeypatch, "pw-record")
        before = listener._record_cmd()

        monkeypatch.setattr(audio_mod, "_SAMPLE_RATE", 8000)

        assert listener._record_cmd() != before, (
            "the listener's argv ignored a changed _SAMPLE_RATE, so it is holding a copy "
            "of the capture argv rather than deriving it"
        )
        assert listener._record_cmd() == audio_mod._stream_capture_cmd("pw-record")


class TestTheRateHasOneSource:
    """The comparison above is only as good as the builder it delegates to."""

    def test_capture_derives_rate_and_channels_from_the_module_constants(self):
        cmd = audio_mod._stream_capture_cmd("pw-record")
        assert cmd[cmd.index("--rate") + 1] == str(audio_mod._SAMPLE_RATE)
        assert cmd[cmd.index("--channels") + 1] == str(audio_mod._CHANNELS)

    def test_wakeword_declares_no_capture_argv_of_its_own(self):
        """The guard against the duplicate builder being reintroduced.

        Source-level on purpose: the equality tests above would keep passing if someone
        re-added a divergent copy that only some code path used, but this fails the moment
        a second builder - or a hardcoded rate - exists in the module at all.
        """
        source = inspect.getsource(wakeword_mod)
        assert "--rate" not in source, (
            "wakeword.py builds its own capture argv again; it must delegate to "
            "audio._stream_capture_cmd so the two cannot drift"
        )
        assert '"16000"' not in source and "'16000'" not in source, (
            "wakeword.py hardcodes the sample rate; the rate belongs to audio._SAMPLE_RATE"
        )

    def test_wakeword_imports_the_builder_not_the_scalar(self):
        """Delegation, not a shared constant - a shared scalar is how the drift started."""
        source = inspect.getsource(wakeword_mod)
        assert "_stream_capture_cmd" in source, (
            "wakeword.py should call audio._stream_capture_cmd, not reimplement it"
        )
        assert "_CAPTURE_LATENCY" not in source, (
            "wakeword.py imports _CAPTURE_LATENCY directly; importing the builder instead "
            "is what removes the possibility of the two argv builders diverging"
        )

    def test_audio_does_not_import_wakeword(self):
        """No cycle: the dependency has to stay one-way for delegation to be sound.

        Parsed rather than grepped, because `audio.py`'s own docstring mentions
        `wakeword.py` in prose - only a real import statement counts.
        """
        imported = set()
        for node in ast.walk(ast.parse(inspect.getsource(audio_mod))):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert not any("wakeword" in name for name in imported), (
            f"audio.py imports wakeword ({sorted(imported)}); wakeword.py imports audio.py, "
            "so that is a cycle"
        )


class TestTheComparisonIsNotVacuous:
    def test_the_two_backends_are_not_the_same_argv(self):
        """The control: what the tests above compare *can* differ.

        Without this, an equality assertion passes just as happily against two builders
        that both return a constant - the same failure as a test that always passes, and
        worse than no test because it reads as coverage.
        """
        assert audio_mod._stream_capture_cmd("pw-record") != audio_mod._stream_capture_cmd(
            "arecord"
        )

    def test_the_listener_argv_is_not_trivially_empty(self, monkeypatch):
        """Stops a builder returning `[]` on both sides from counting as agreement."""
        assert _listener(monkeypatch, "pw-record")._record_cmd()