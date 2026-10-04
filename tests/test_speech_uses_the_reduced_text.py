"""A markdown reply was spoken with its markup intact.

`markdown_lite.to_speech` reduces a reply to text worth hearing, and the window
renders the same reply through `to_pango`. But a reducer nothing calls is the
"built, unit-tested, and never wired into anything that runs" class this
project's own rules call out - and that is exactly what the first version of
this was.

Verified: reverting the one line in `app.py` that routes `_speak` through
`to_speech` left the **whole suite green at 1798 passed**. The 14 tests in
`test_markdown_lite.py` all still passed, because they exercise the reducer
directly and were never about who calls it.

So this file checks the call site. It reads `app.py`'s source rather than
constructing a real `Gtk.Application`, because the thing being pinned is a
one-line routing decision and the alternative - a full application with a real
TTS engine, a real player and a real async bridge - would test the harness far
more than the decision.

The behavioural half is in `test_markdown_lite.py::TestTheSpokenForm`, where
the reduction is measured against a real TTS engine: 275,258 bytes of audio
before, 180,078 after, and 180,078 is byte-for-byte what the same sentence
synthesises to when it never had markup in it.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

_APP = _REPO / "usr" / "lib" / "shani-chronoa" / "shani_chronoa" / "app"


def _source() -> str:
    """Every module of the app package (it was one app.py)."""
    return "".join(f.read_text() for f in sorted(_APP.glob("*.py")))


def _speak_call_line() -> str:
    """The line that hands text to the speech coroutine."""
    for line in _source().splitlines():
        if "self._speak(" in line and "async def" not in line and "def _speak" not in line:
            return line.strip()
    raise AssertionError("app.py has no call to _speak; the voice path moved")


class TestTheVoicePathIsWiredToTheReducer:
    def test_speak_is_given_the_reduced_text(self):
        """Replies are spoken sentence by sentence through speech.SpeechQueue,
        which reduces each sentence with to_speech before synthesis - the
        behaviour, checked by running it rather than by reading app.py."""
        from shani_chronoa import speech
        said = []
        q = speech.SpeechQueue(lambda text: said.append(text) or b"RIFF", lambda wav: True)
        q.speak(["You have **3** updates."])
        q.close()
        assert q.wait(5)
        assert said == ["You have 3 updates."], said
        assert "_new_speech_queue" in _source() and "speech.SpeechQueue(" in _source(), \
            "app.py no longer speaks replies through the reducing queue"

    def test_the_reducer_is_not_applied_twice(self):
        # to_speech is applied once, inside SpeechQueue.speak; the app hands it
        # raw sentences. A second pass is a real mistake a test should catch.
        from shani_chronoa import speech
        import inspect
        assert inspect.getsource(speech.SpeechQueue.speak).count("to_speech") == 1
        assert "queue.speak(markdown_lite.to_speech" not in _source()

    def test_the_window_still_renders_through_to_pango(self):
        # The two paths must use the same module. If the window moved to another
        # renderer the spoken and displayed forms would start disagreeing about
        # what the reply said, which is the whole reason the reducer lives here.
        from _source import package_source
        gui = package_source("gui")
        assert "to_pango" in gui, "the window no longer renders through to_pango"
        assert "to_speech" not in gui, \
            "to_speech is a voice-path reducer; the window must not use it"

    def test_the_module_is_imported_rather_than_referenced_by_luck(self):
        # A NameError here would only surface when a reply is actually spoken,
        # which is exactly the "compiles fine, fails when run" shape.
        assert "import markdown_lite" in _source() or \
            "from shani_chronoa import markdown_lite" in _source(), (
            "app.py calls markdown_lite.to_speech without importing it")
