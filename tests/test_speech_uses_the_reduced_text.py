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

_APP = _REPO / "usr" / "lib" / "shani-chronoa" / "shani_chronoa" / "app.py"


def _source() -> str:
    return _APP.read_text()


def _speak_call_line() -> str:
    """The line that hands text to the speech coroutine."""
    for line in _source().splitlines():
        if "self._speak(" in line and "async def" not in line and "def _speak" not in line:
            return line.strip()
    raise AssertionError("app.py has no call to _speak; the voice path moved")


class TestTheVoicePathIsWiredToTheReducer:
    def test_speak_is_given_the_reduced_text(self):
        line = _speak_call_line()
        assert "to_speech" in line, (
            f"the voice path hands the TTS engine the raw reply: {line!r}. The "
            f"window renders the same text through to_pango, so every `**` and "
            f"backtick is pronounced aloud."
        )

    def test_the_reducer_is_not_applied_twice(self):
        # to_speech is not idempotent in general - a second pass over already
        # reduced text is harmless for markers, but a stray unmatched `_` could
        # pair with a later one. Cheap to pin, and a double call is a real
        # mistake that a test should catch.
        line = _speak_call_line()
        assert line.count("to_speech") == 1, (
            f"to_speech is applied more than once on the path: {line!r}")

    def test_the_window_still_renders_through_to_pango(self):
        # The two paths must use the same module. If the window moved to another
        # renderer the spoken and displayed forms would start disagreeing about
        # what the reply said, which is the whole reason the reducer lives here.
        gui = (_REPO / "usr" / "lib" / "shani-chronoa" / "shani_chronoa"
               / "gui.py").read_text()
        assert "to_pango" in gui, "the window no longer renders through to_pango"
        assert "to_speech" not in gui, \
            "to_speech is a voice-path reducer; the window must not use it"

    def test_the_module_is_imported_rather_than_referenced_by_luck(self):
        # A NameError here would only surface when a reply is actually spoken,
        # which is exactly the "compiles fine, fails when run" shape.
        assert "import markdown_lite" in _source() or \
            "from shani_chronoa import markdown_lite" in _source(), (
            "app.py calls markdown_lite.to_speech without importing it")
