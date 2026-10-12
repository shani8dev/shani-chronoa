"""What the machine is watching, said out loud.

**53 senses ship and nothing on screen said which were live.** A person who has
enabled location has no way to confirm it polls, and one who has refused
something has no way to confirm the refusal took. Both are questions this
repository keeps getting wrong in the other direction - reporting what *would*
be allowed rather than what *is*.

Two things are tested here:

- the **Sensing** section, which counts what polls and what is refused, and
- a **gate on every rail row that opens a panel**, because that is where a real
  bug was found: `link()` lost its `icon` parameter, three call sites kept
  passing one, and an icon theme name was being used as the surface name. The
  row rendered correctly, the tooltip read plausibly, and clicking it asked the
  window for a panel that does not exist. Nothing raised.
"""

import sys
from collections import deque
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa.gui import rail as rail_module  # noqa: E402
from shani_chronoa.gui import surfaces as surfaces_module  # noqa: E402


def _entry(name, *, will_poll=True, allowed=True, denial="", refusal=""):
    """One row of `scheduler.plan()`, with the fields the rail reads."""
    return SimpleNamespace(
        name=name, registered=True, ambient=True, poll_interval=60.0,
        allowed=allowed, denial=denial, refusal=refusal, will_poll=will_poll)


def _walk(root):
    queue = deque([root])
    while queue:
        node = queue.popleft()
        yield node
        if hasattr(node, "get_first_child"):
            child = node.get_first_child()
            while child is not None:
                queue.append(child)
                child = child.get_next_sibling()


def _buttons(root):
    return [n for n in _walk(root) if isinstance(n, Gtk.Button)]


def _button_opening(root, surface):
    """The one row that opens `surface`.

    Selected by what it says it opens rather than by position: the rail's
    sections are populated independently, so "the first button" is the first
    row of whichever section happened to have content - three `diff` rows on a
    working tree. A positional selector here passes for the wrong reason.
    """
    for button in _buttons(root):
        if button.get_tooltip_text() == f"Open the {surface} panel":
            return button
    return None


def _text_of(root):
    return " ".join(n.get_label() for n in _walk(root)
                   if isinstance(n, Gtk.Label) and n.get_label())


def _sensing_section(opened, senses):
    """A rail whose only populated section is Sensing."""
    rail = rail_module.NowRail(get_senses=lambda: senses, on_open=opened.append)
    rail.refresh()
    return rail


def _sensing_text(opened, senses):
    return _text_of(_sensing_section(opened, senses))


# -- what is polling --------------------------------------------------------


def test_the_count_of_polling_senses_is_shown():
    """The thing wanted at a glance. 53 senses exist; the count is the point."""
    senses = [_entry(n) for n in ("location", "calendar", "clipboard")]
    said = _sensing_text([], senses)
    assert "3" in said, said


def test_the_names_are_human_readable():
    """`file_watch` is a registry key, not something a person reads."""
    said = _sensing_text([], [_entry("file_watch")])
    assert "file watch" in said, said
    assert "file_watch" not in said, said


def test_the_names_are_capped_with_a_count_of_the_rest():
    """A turn of names is not a summary. Twelve senses is not a glance."""
    senses = [_entry(f"sense_{i}") for i in range(12)]
    said = _sensing_text([], senses)
    assert "and 8 more" in said, said


# -- what is refused --------------------------------------------------------


def test_a_refused_sense_is_counted_not_hidden():
    """Reporting only what is allowed makes a refusal look like an absence -
    which is the one thing a refusal must never do."""
    senses = [_entry("location"), _entry("calendar", allowed=False,
                                         denial="you turned this off")]
    said = _sensing_text([], senses)
    assert "1 sense(s) refused" in said, said
    assert "you turned this off" in said, said


def test_a_refusal_states_its_reason_when_it_has_one():
    senses = [_entry("camera", allowed=False, denial="no consent key")]
    assert "no consent key" in _sensing_text([], senses)


def test_a_refusal_with_no_reason_still_counts():
    """A bare count is better than silence, which is indistinguishable from a
    section that never refreshed."""
    said = _sensing_text([], [_entry("camera", allowed=False)])
    assert "1 sense(s) refused" in said, said


# -- when there is nothing --------------------------------------------------


def test_an_empty_plan_says_so():
    """Silence would be indistinguishable from a rail that has not refreshed."""
    assert "No ambient sense is polling" in _sensing_text([], [])


def test_an_unreadable_plan_does_not_take_the_rail_down():
    """One throwing callback must not take the other seven sections with it -
    `refresh()`'s per-section guard is there for exactly this, and the claim
    needs checking rather than assuming."""

    def _boom():
        raise RuntimeError("the scheduler is locked")

    rail = rail_module.NowRail(get_senses=_boom, get_state=lambda: "thinking",
                               on_open=lambda _n: None)
    rail.refresh()
    assert "thinking" in _text_of(rail), "a failing sense read cost the whole rail"


def test_the_failing_section_says_it_failed():
    rail = rail_module.NowRail(
        get_senses=lambda: (_ for _ in ()).throw(RuntimeError("locked")),
        on_open=lambda _n: None)
    rail.refresh()
    assert "Could not read what is being watched" in _text_of(rail), _text_of(rail)


# -- the row opens a panel that exists --------------------------------------


def test_the_sensing_row_opens_the_senses_panel():
    """**This is the bug class.** `link()` lost its `icon` parameter and the
    call sites kept passing one, so an icon theme name was used as the surface
    name: the row rendered fine, the tooltip read plausibly, and the click
    asked for a panel that does not exist."""
    opened = []
    rail = _sensing_section(opened, [_entry("location")])
    row = _button_opening(rail, "senses")
    assert row is not None, "no row claims to open the senses panel"
    row.emit("clicked")
    assert opened == ["senses"], f"clicked and got {opened}"


def test_every_rail_row_opens_a_panel_that_exists(tmp_path):
    """The gate, and it is the one that would have caught all three broken rows
    rather than the one this session happened to notice.

    Every button in the rail is clicked and the name it asks for is checked
    against the real registry - not against a list written here, because a
    hand-copied list drifts and then asserts nothing.

    **`tmp_path` holds a real file on purpose.** Artifacts are filtered to paths
    that exist by the time they reach the rail, so a made-up path leaves the
    Made section empty - and the first version of this test used one. Restoring
    the Made row's bug under it passed: the row under test was never built, and
    a gate that does not build its subject is a gate that does not gate.
    """
    made = tmp_path / "one.png"
    made.write_bytes(b"\x89PNG\r\n\x1a\n")

    opened = []
    rail = rail_module.NowRail(
        get_state=lambda: "thinking",
        get_tool=lambda: "qwen3:0.6b",
        get_artifacts=lambda: [made],
        get_events=lambda: [SimpleNamespace(
            name="calendar", detail="invite in 4 minutes", ok=True)],
        get_senses=lambda: [_entry("location")],
        on_open=opened.append)
    rail.refresh()

    real = set(surfaces_module.available_surfaces())
    buttons = _buttons(rail)
    assert buttons, "the rail rendered no rows at all"
    for button in buttons:
        opened.clear()
        button.emit("clicked")
        assert len(opened) == 1, "a rail row did not open exactly one panel"
        assert opened[0] in real, (
            f"a row asks for {opened[0]!r}, which is not a panel. "
            f"Known: {sorted(real)}")


def test_the_made_section_is_actually_under_the_gate(tmp_path):
    """The other half of the fix above, stated so it cannot rot.

    A gate that only ever exercises the sections whose data happens to be
    present reports green while the rows nobody populated are broken. This
    asserts the Made row exists at all, so the gate's coverage is visible
    rather than assumed.
    """
    made = tmp_path / "two.png"
    made.write_bytes(b"\x89PNG\r\n\x1a\n")
    opened = []
    rail = rail_module.NowRail(get_artifacts=lambda: [made], on_open=opened.append)
    rail.refresh()
    assert _button_opening(rail, "artifact_store") is not None, (
        f"no Made row was rendered; tooltips were "
        f"{[b.get_tooltip_text() for b in _buttons(rail)]}")


def test_the_tooltip_names_the_panel_and_not_the_icon():
    """The tooltip is read aloud by a screen reader, so a wrong one is not a
    cosmetic problem: it announces a panel that does not exist."""
    opened = []
    rail = _sensing_section(opened, [_entry("location")])
    row = _button_opening(rail, "senses")
    assert row is not None, "no row claims to open the senses panel"
    assert row.get_tooltip_text() == "Open the senses panel", (
        row.get_tooltip_text())


# -- what the window offers -------------------------------------------------


class _Source:
    def __init__(self, rows):
        self._rows = rows

    def plan(self):
        return list(self._rows)


def _window_with(source):
    window = SimpleNamespace()
    window._event_source = source
    window._rail_senses = rail_sense_reader(window)
    return window


def rail_sense_reader(window):
    """The real `_rail_senses`, bound to a stand-in window.

    Taken off the class rather than reimplemented: a test that copies the
    method under test passes when the method is wrong.
    """
    from shani_chronoa.gui import window as window_module

    return window_module.ChronoaWindow._rail_senses.__get__(window)


def test_the_window_offers_the_schedulers_plan():
    rows = [_entry("location"), _entry("calendar")]
    window = _window_with(_Source(rows))
    assert [e.name for e in window._rail_senses()] == ["location", "calendar"]


def test_no_scheduler_means_no_senses_rather_than_an_error():
    """A rail tick must never raise: the rail refreshes on a timer and an
    exception there is a red traceback every two seconds."""
    window = _window_with(None)
    assert window._rail_senses() == ()


def test_a_scheduler_that_raises_reports_nothing_and_stays_up():
    class _Broken:
        def plan(self):
            raise RuntimeError("the lock is held")

    window = _window_with(_Broken())
    assert window._rail_senses() == ()