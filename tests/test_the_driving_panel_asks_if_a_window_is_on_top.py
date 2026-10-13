"""The Driving panel asked four facts about driving a desktop, and not the one
that decides whether a click lands where the reply says it did.

Its own docstring: *"One question per row - is this piece of Chronoa wired to the
desktop on this machine? - and every answer read from the thing that would make
it true rather than from a copy of it."* The rows asked whether a position can be
read (`knowable()`), whether the RemoteDesktop portal answers, what the cue
socket is, and where the windows are.

None of them asks whether a window is **actually the thing on top**.

`ui_driver.is_on_top()` existed and had **no caller in the tree**. Its docstring is
the record of why it was written, and it is not hypothetical:

    `flatpak run com.slack.Slack` is the only working way to raise a window, and
    it is fragile for a reason that is easy to miss: the moment anything runs from
    a terminal, *the terminal raises itself*, and it covers Slack's left column.
    Measured by OCR of the region a click was about to use - it returned `deepu`,
    `Jisha`, `Nevin`, `Abdul` and the click did nothing, because those words were
    on screen in **this agent's own terminal output**, not in Slack. Every click at
    the right pixel for a whole afternoon was a click on the terminal.

**This adds a row, and does not change `click()`.** The click path deliberately
*reports* the evidence rather than refusing on it - "the evidence still goes in
the reply, where the caller can see it" - and overriding that would replace one
documented decision with another. A panel that reports the fact is the smaller,
honest change.

**All three answers are asserted, because there are three.** `is_on_top`'s own
docstring is emphatic that `None` is *not* a pass: "a check that answers 'yes' when
it could not look has no power to refuse anything." Rendering `None` as KNOWN here
would reintroduce exactly that, one layer up.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

from shani_chronoa.gui.surfaces import driving  # noqa: E402


def _walk(node, out=None):
    out = [] if out is None else out
    if isinstance(node, Gtk.Widget):
        out.append(node)
    child = node.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out


class _App:
    window = None
    config = None

    def activate_action(self, name, arg):
        pass


def _said(page):
    out = []
    for w in _walk(page):
        if isinstance(w, Gtk.Label) and w.get_text():
            out.append(w.get_text())
        try:
            sub = w.get_subtitle()
            if sub:
                out.append(sub)
        except Exception:  # noqa: BLE001 - a widget without a subtitle is fine
            pass
    return " ".join(out)


def _page(answer, monkeypatch):
    monkeypatch.setattr(driving.ui_driver, "is_on_top", lambda probe: answer)
    monkeypatch.setattr(driving.ui_driver, "windows",
                        lambda *a, **k: [("Slack", "0x1")])
    return _said(driving.build(_App()))


def test_could_not_tell_is_not_a_pass(monkeypatch):
    """**The state this box is actually in.** No capture, no OCR - and the row
    must say so rather than reading as a pass."""
    said = _page(None, monkeypatch)
    assert "could not tell" in said, said[:240]
    assert "no text to read" in said, (
        f"could-not-tell is presented as a pass: {said[:240]!r}")


def test_a_covered_window_is_named_as_covered(monkeypatch):
    said = _page(False, monkeypatch)
    assert "something is over it" in said, said[:240]
    assert "terminal" in said, (
        "the measured cause is not named, so the reader has to guess what is "
        f"covering their window: {said[:240]!r}")


def test_a_visible_window_says_so(monkeypatch):
    said = _page(True, monkeypatch)
    assert "on top" in said, said[:240]
    assert "something is over it" not in said, said[:240]


def test_the_probe_is_a_real_window_title(monkeypatch):
    """**Not "the screen".** `is_on_top` compares the probe's words against what
    OCR found, so asking about nothing in particular answers nothing in
    particular."""
    seen = {}

    def capture(probe):
        seen["probe"] = probe
        return None

    monkeypatch.setattr(driving.ui_driver, "is_on_top", capture)
    monkeypatch.setattr(driving.ui_driver, "windows",
                        lambda *a, **k: [("Slack", "0x1")])
    driving.build(_App())

    assert seen.get("probe") == "Slack", (
        f"the probe was {seen.get('probe')!r}, not a window this driver can see")


def test_is_on_top_is_now_called_at_all(monkeypatch):
    """The smallest claim that was false: the function had no caller."""
    calls = []
    monkeypatch.setattr(driving.ui_driver, "is_on_top",
                        lambda probe: calls.append(probe))
    monkeypatch.setattr(driving.ui_driver, "windows", lambda *a, **k: [])
    driving.build(_App())

    assert calls, "is_on_top() still has no caller"
