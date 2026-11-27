"""Buffered canvas tests: clipping, diffing, forcing rows, one flush."""

import io

from shani_chronoa.tui.canvas import Canvas, sanitize
from shani_chronoa.tui.layout import Rect


class CountingStream:
    def __init__(self):
        self.writes = []
        self.flushes = 0

    def write(self, text):
        self.writes.append(text)

    def flush(self):
        self.flushes += 1

    def value(self):
        return "".join(self.writes)


def test_sanitize_drops_escape_controls_and_del():
    # ESC is dropped, so the leftover "[31m" is inert text, not a sequence.
    assert sanitize("a\x1b[31mb\x00c\x7fd\x9fe\r\n") == "a[31mbcde"
    assert sanitize("héllo → ✓") == "héllo → ✓"


def test_text_clips_to_grid():
    canvas = Canvas(2, 4)
    canvas.text(0, 2, "ABCDE")
    assert canvas.diff(None) == [(0, "  AB"), (1, "    ")]


def test_diff_emits_only_changed_rows():
    canvas = Canvas(3, 5)
    canvas.text(0, 0, "hello")
    canvas.text(2, 0, "bye")
    previous = canvas.commit(io.StringIO(), None, cursor=None, visible=False)
    canvas.text(1, 0, "mid")
    assert canvas.diff(previous) == [(1, "mid  ")]


def test_commit_forces_input_row_into_every_frame():
    canvas = Canvas(2, 4)
    canvas.text(1, 0, "ab")
    previous = canvas.commit(io.StringIO(), None, cursor=None, visible=False)

    stream = io.StringIO()
    canvas.commit(stream, previous, cursor=None, visible=False, force_rows=(1,))
    assert stream.getvalue() == "\x1b[2;1H" + "ab  " + "\x1b[K\x1b[?25l"


def test_commit_positions_cursor_and_sets_visibility():
    stream = io.StringIO()
    Canvas(1, 2).commit(stream, None, cursor=(2, 3), visible=True)
    assert stream.getvalue().endswith("\x1b[?25h\x1b[3;4H")
    stream = io.StringIO()
    Canvas(1, 2).commit(stream, None, cursor=None, visible=False)
    assert "\x1b[?25l" in stream.getvalue()


def test_styles_emit_sgr_only_with_color():
    canvas = Canvas(1, 4, color=True)
    canvas.text(0, 0, "ok", "bold")
    assert canvas.diff(None) == [(0, "\x1b[1mok\x1b[0m  ")]
    plain = Canvas(1, 4)
    plain.text(0, 0, "ok", "bold")
    row = plain.diff(None)[0][1]
    assert "\x1b[" not in row
    assert row == "ok  "


def test_box_draws_unicode_border_and_title():
    canvas = Canvas(3, 10)
    canvas.box(Rect(0, 0, 10, 3), "APP")
    assert canvas.diff(None) == [
        (0, "┌ APP ───┐"),
        (1, "│        │"),
        (2, "└────────┘"),
    ]


def test_commit_writes_once_flushes_once_and_never_clears_screen():
    stream = CountingStream()
    canvas = Canvas(2, 3)
    canvas.text(0, 0, "hi")
    snapshot = canvas.commit(stream, None, cursor=(0, 2), visible=True)
    assert len(stream.writes) == 1
    assert stream.flushes == 1
    frame = stream.value()
    assert "\x1b[H\x1b[J" not in frame
    assert frame.startswith("\x1b[1;1Hhi \x1b[K")
    assert snapshot.diff(None) == [(0, "hi "), (1, "   ")]

    stream2 = CountingStream()
    canvas.commit(stream2, snapshot, cursor=None, visible=False)
    assert len(stream2.writes) == 1
    assert stream2.flushes == 1
    assert stream2.value() == "\x1b[?25l"
