"""A running skill must say how long it has been running.

A 40-second `generate_image` showed a status line that said "Generate image..."
and never changed. The only question a person has during a long tool call is
*working or hung*, and a static line answers neither.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa.app.conversation import ConversationMixin  # noqa: E402


class _FakeWindow:
    def __init__(self):
        self.statuses = []

    def set_status(self, text):
        self.statuses.append(text)


class _Host(ConversationMixin):
    """The mixin with just the window it needs, and no application."""

    def __init__(self):
        self.window = _FakeWindow()


def _start(host, name):
    host._on_tool_call_main(name)


def test_a_skill_that_just_started_says_its_name():
    host = _Host()
    _start(host, "generate_image")
    assert host.window.statuses[-1] == "Generate image...", host.window.statuses


def test_the_status_counts_up_once_a_second_has_passed():
    host = _Host()
    _start(host, "generate_image")
    host._tool_clock_started = time.monotonic() - 12.4      # as if 12s old
    host._on_tool_clock_tick()
    assert host.window.statuses[-1] == "Generate image... 12s", \
        host.window.statuses


def test_the_clock_stops_when_the_skill_finishes():
    """A tick landing after the result would overwrite the status the card left."""
    host = _Host()
    _start(host, "generate_image")
    host._on_tool_result_main("generate_image", {}, "Saved it.", True, ())
    assert host._tool_clock is None, "the clock is still ticking after the result"
    before = len(host.window.statuses)
    host._on_tool_clock_tick()
    assert len(host.window.statuses) == before, (
        "a stale tick still wrote a status: "
        f"{host.window.statuses[before:]}")


def test_a_stale_tick_removes_itself_rather_than_continuing():
    """The control for the token check: a tick that is no longer current must
    return SOURCE_REMOVE, or it ticks forever writing nothing."""
    from gi.repository import GLib

    host = _Host()
    _start(host, "generate_image")
    host._stop_tool_clock()
    host._tool_clock_started = time.monotonic() - 5
    assert host._on_tool_clock_tick() == GLib.SOURCE_REMOVE
    assert host.window.statuses[-1] == "Generate image...", (
        "a stale tick wrote a status anyway")


def test_two_skills_in_a_row_do_not_share_a_clock():
    host = _Host()
    _start(host, "get_datetime")
    _start(host, "generate_image")
    host._tool_clock_started = time.monotonic() - 8
    host._on_tool_clock_tick()
    assert host.window.statuses[-1] == "Generate image... 8s", \
        f"the first skill's clock kept running: {host.window.statuses}"


def test_no_window_means_no_clock_to_start():
    host = _Host()
    host.window = None
    _start(host, "generate_image")
    assert host._tool_clock is None
