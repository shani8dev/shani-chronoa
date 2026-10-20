"""How full the model's window is, where the person can see it.

**"It forgot what I said" has to be distinguishable from "the window is
full"**, and at the width the app opens at the rail - the only place that
distinction was drawn - is deleted.

So the bar is above the composer, at every width, and it appears **only
when it has something to say**: the rail's own meter says where the
forgetting starts (75% of what can still be sent), and a window below that
is a window working normally.

**It shows the *usable* share, not the raw one.** A window that is 80%
full of input while 1,024 tokens are held back for the reply is 91% used,
and the number that answers "can another question still fit" is the second
one. The raw percentage is in the tooltip, so nothing is lost.
"""

from __future__ import annotations

import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "usr", "lib", "shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
Gtk = pytest.importorskip("gi.repository.Gtk", reason="GTK 4 is not installed")

from shani_chronoa import context_meter  # noqa: E402
from shani_chronoa.gui import context_bar as context_bar_module  # noqa: E402


def _report(total=8000, limit=8192, reserve=1024, elided=0):
    """A report with a known fill."""
    return context_meter.Report(
        limit=limit,
        total_tokens=total,
        total_chars=total * 4,
        reply_reserve=reserve,
        elided_messages=elided,
        dropped_messages=elided,
    )


class TestTheBarOnlySpeaksWhenItHasSomethingToSay:
    def test_a_window_working_normally_shows_no_bar(self):
        """**A bar for a window at 15% is chrome on every window**, for a
        reading nobody needs - and the window's minimum height is a
        promise."""
        bar = context_bar_module.ContextBar()
        bar.set_report(_report(total=1000))
        assert not bar.get_visible(), (
            "the context bar is on screen for a window that is barely used")

    def test_a_filling_window_shows_the_bar(self):
        """**The threshold is the rail's own**, so the bar and the rail's
        card read the same way."""
        bar = context_bar_module.ContextBar()
        bar.set_report(_report(total=6000))
        assert bar.get_visible(), (
            "the context bar is hidden for a window that is filling")

    def test_the_threshold_is_the_rails_own(self):
        """**One threshold, not two** - the rail's level bar uses 0.75 for
        the colour that means 'it forgot what I said', and a bar that
        appeared at a different point would be a second answer."""
        assert context_bar_module.WARN_AT == 0.75, (
            "the context bar's threshold is not the rail's")

    def test_no_report_means_no_bar(self):
        """**A window whose size cannot be read is not a window at 0%** -
        the meter answers None for the percentage rather than a guess
        against a made-up denominator."""
        bar = context_bar_module.ContextBar()
        bar.set_report(None)
        assert not bar.get_visible()
        bar.set_report(_report(limit=None, total=5000))
        assert not bar.get_visible(), (
            "the bar is on screen for a window whose size is unknown")


class TestTheBarShowsTheUsableShare:
    def test_the_bar_shows_the_usable_share_not_the_raw_one(self):
        """**A window that is 80% full of input and wants 20% of output
        left over is not 80% usable** - and the number that answers "can
        another question still fit" is the second one."""
        # 7,168 of 8,192 raw is 87.5%; of the 7,168 that can still be
        # sent it is 100% - so the raw share would be the alarming one and
        # the usable one says there is nothing left.
        bar = context_bar_module.ContextBar()
        bar.set_report(_report(total=7168))
        assert bar.get_visible()
        assert bar._bar.get_value() == pytest.approx(1.0), (
            "the bar is not showing the usable share")

    def test_the_raw_share_is_in_the_tooltip(self):
        """**Nothing is lost**: the raw percentage and the reserve are in
        the hover, so a person can see both numbers."""
        bar = context_bar_module.ContextBar()
        bar.set_report(_report(total=6000))
        tip = bar._bar.get_tooltip_text() or ""
        assert "6,000" in tip or "6000" in tip, tip
        assert "held back" in tip, tip

    def test_the_tooltip_carries_the_breakdown(self):
        """**What is filling the window** is the other half of the
        question - a percentage alone does not say what to do about it."""
        report = _report(total=6000)
        report.segments = [context_meter.Segment("system", 4000),
                           context_meter.Segment("user", 2000)]
        bar = context_bar_module.ContextBar()
        bar.set_report(report)
        tip = bar._bar.get_tooltip_text() or ""
        # **The meter's own labels**, not a copy of them: a test that
        # hard-codes the words is a second answer to what the segment is
        # called.
        for segment in report.segments:
            assert segment.label in tip, (segment.label, tip)

    def test_a_dropped_part_of_the_conversation_is_said(self):
        """**"It forgot what I said"** - the thing the bar exists to
        explain."""
        bar = context_bar_module.ContextBar()
        bar.set_report(_report(total=6000, elided=3))
        tip = bar._bar.get_tooltip_text() or ""
        assert "dropped" in tip, tip


class TestTheBarNeverTakesTheWindowDown:
    def test_a_report_that_raises_leaves_the_bar_hidden(self):
        """**A glance must not take the window down**, and the bar is
        refreshed from the window's tick."""

        class _Broken:
            @property
            def usable_percent(self):
                raise RuntimeError("no report")

        bar = context_bar_module.ContextBar()
        bar.set_report(_Broken())
        assert not bar.get_visible()
