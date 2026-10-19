"""The reference recording is checked, and the refusal says what is wrong with it.

`voice_clone.clip_problem(path)` measures whether a *reference clip* is usable -
there, empty, far too big - and its own docstring says it "is what the voice
surface and `speak`'s own refusal print". **Neither asked it**: both asked
`clone_problem()`, which answers a different question - whether the *engine* can
run on this machine at all.

So the case the engine's answer cannot cover was covered by nobody:

- **too big.** A recording of an hour is a hundred megabytes, its digest still
  matches the registration, so `check()` allows it - and the gate opened on a
  file the synthesis would choke on, with a refusal that said nothing about the
  file.

The other two cases - the recording is gone, or it has been replaced - were
already covered, by `check()`'s digest, which refuses them first and says
which of the two happened. **This is not a duplicate refusal: it is the one
refusal `check()` cannot make.**

And the voice surface's row named the file without saying whether the file was
there, so a recording that had been deleted read like an agreement that was
fine.
"""

from __future__ import annotations

import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "usr", "lib", "shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
Gtk = pytest.importorskip("gi.repository.Gtk", reason="GTK 4 is not installed")
Adw = pytest.importorskip("gi.repository.Adw", reason="libadwaita is not installed")

from shani_chronoa import tts, voice_clone  # noqa: E402


class _Settings:
    """The two reads this module makes, so nothing else can be a variable."""

    def __init__(self, clone_on: bool, chosen: str = ""):
        self.clone_on = clone_on
        self.chosen = chosen

    def get_bool(self, key, default=None):
        assert key == voice_clone.CONSENT_KEY, (
            f"voice_clone read an unrelated key {key!r}")
        return self.clone_on

    def get(self, key, default=None):
        assert key == tts.PiperTTS.CLONE_VOICE_KEY, (
            f"tts read an unrelated preference {key!r}")
        return self.chosen


@pytest.fixture
def clip(tmp_path):
    """A small real file, because the gate hashes bytes rather than names."""
    path = tmp_path / "alice-5s.wav"
    path.write_bytes(b"RIFF----WAVEfmt " + b"\x00" * 200)
    return path


def _register(clip, voice="Alice", speaker="Alice", basis="written consent"):
    return voice_clone.register(voice=voice, clip=str(clip),
                                speaker=speaker, basis=basis)


def _piper(**attrs):
    """A `PiperTTS` with the attributes the cascade reads and nothing else."""
    piper = tts.PiperTTS.__new__(tts.PiperTTS)
    piper.piper_path = "/nonexistent/piper"
    piper.voice_path = "/nonexistent/voice"
    piper.piper_trial = False
    piper.kokoro_trial_voice = ""
    piper.rate = 1.0
    piper._config = _Settings(attrs.get("clone_on", False),
                              attrs.get("chosen", ""))
    return piper


class TestTheRecordingIsMeasured:
    """`clip_problem` is the measurement, and it is now asked."""

    def test_a_recording_that_is_too_big_is_refused(self, clip, monkeypatch):
        """**The hole this closes.**

        The digest matches, so `check()` allows it: the gate opened on a file
        the synthesis would choke on, and the refusal named the *engine*
        instead of the recording.
        """
        _register(clip)
        # **Patched after the registration**, because `register()` refuses a
        # clip over the same limit - so the size has to be legal to agree to
        # and then illegal to use, which is exactly the state this is about.
        monkeypatch.setattr(voice_clone, "MAX_CLIP_BYTES", 16)
        piper = _piper(clone_on=True, chosen="Alice")

        reason = piper._clone_reason()

        assert reason, (
            "a recording far too big was accepted: the refusal is empty, and "
            "'' is what the cascade reads as *nothing is in the way*")
        assert "recording" in reason.lower(), (
            f"the refusal does not name the recording: {reason}")

    def test_a_recording_of_a_usable_size_is_not_refused(self, clip, monkeypatch):
        """**The control, and it is the reason the other one means anything.**

        A check that refuses every recording would satisfy the test above.
        """
        monkeypatch.setattr(voice_clone, "MAX_CLIP_BYTES", 1 << 20)
        _register(clip)
        piper = _piper(clone_on=True, chosen="Alice")

        reason = piper._clone_reason()

        # The engine is not installed here, so the refusal is about the
        # *engine* - and it is not about the recording.
        assert "recording" not in reason.lower(), (
            f"a recording of a usable size was refused: {reason}")

    def test_the_recording_is_found_by_its_voice(self, clip):
        """The path, not the name.

        `clip_problem` measures a *file*, so handing it a name would be a
        file that is not there - which is the very failure the measurement
        exists to catch.
        """
        _register(clip)
        assert voice_clone.clip_of("Alice") == str(clip)
        assert voice_clone.clip_of("Nobody") == ""


class TestTheVoiceSurfaceSaysSo:
    """The row named the file; now it says whether the file is there."""

    @staticmethod
    def _labels(widget):
        out, stack = [], [widget]
        while stack:
            node = stack.pop()
            if isinstance(node, Gtk.Label):
                out.append(node.get_label())
            child = node.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
        return out

    def test_the_row_says_when_the_recording_is_gone(self, clip, tmp_path):
        from shani_chronoa.settings_window import voice as voice_page

        _register(clip)
        clip.unlink()          # the recording is gone; the agreement is not
        page = voice_page.VoicePage.__new__(voice_page.VoicePage)
        page._clone_rows_built = []
        # **`_switch` belongs to the window, which mixes this page in.** The
        # row under test is built here, so the one helper the window owns is
        # stubbed - and stubbed to do nothing, so it cannot contribute the text
        # the assertion looks for.
        page._switch = lambda *a, **k: None
        group = Adw.PreferencesGroup()
        page._clone_rows(group, _Settings(True, "Alice"))

        text = " ".join(self._labels(group))
        assert "cannot be used" in text, (
            f"the row did not say the recording is unusable: {text}")
