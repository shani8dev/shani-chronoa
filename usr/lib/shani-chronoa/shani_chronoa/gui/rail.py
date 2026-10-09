"""The right rail: what Chronoa is doing, what it owes you, and how it is armed.

The window had a left sidebar (the panels) and the conversation, and nothing
else. The conversation is a *transcript* - it grows downward, and the answer
scrolled off the top is gone - so the three questions a person actually has
while talking to an assistant with its hands on their machine had no home:

- **What is it doing right now?** The orb says one word; the tool it is
  running lives in a card that scrolls away.
- **What has it done to me that I can take back?** Every write is already on
  disk by the time anyone could approve it, so the honest place for it is a
  standing list, not a message.
- **What is it allowed to do?** A reply means different things when the
  permission posture is Normal, when nothing is allowed to ask, and when it
  may only look.

**What is deliberately not in it.** Not the sense feeds (the senses panels own
that) and not health (Diagnostics owns it, with a per-row reason). Goals were
left out while `goals.py` had no producer - a card for a queue nothing can
enqueue is a dead control. `skills/manage_goals.py` produces them now, so the
Goals section shows open runs, and hides itself when there are none. Every section below reads a store that already
exists and shows an honest empty state, because a rail that renders decoration
is worse than no rail: it looks like it knows something.

Sections, in the order a person asks the questions:

1. **Now** - the live state and the current tool, from the same
   `AssistantState` the orb uses plus the turn the app already tracks.
2. **Timers** - pending countdowns with the time left, refreshed on a tick,
   because "3m 12s left" that says 3m 12s forever is worse than not showing it.
3. **Changed** - files Chronoa wrote that the undo ring can still put back,
   with the line counts. This is the same data the Diff panel shows in full.
4. **Tasks** - outstanding items from the task list, *only* when that list's
   consent key is on. Consent is read here, not assumed: a rail that lists
   tasks the user switched off is a disclosure behind a switch.
5. **Posture** - permission mode, memory consent, sandbox qualification. Three
   lines, all of which change what a reply is allowed to mean.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gtk, Pango  # noqa: E402 - after require_version

from shani_chronoa import config as config_mod  # noqa: E402

logger = logging.getLogger(__name__)

#: Width of the rail. Wide enough for a filename and a time, narrow enough
#: that the conversation keeps the majority of a 1280px window. Below the
#: breakpoint the rail is hidden rather than shrunk: at 180px it wraps every
#: label and reads as a column of broken words.
RAIL_WIDTH = 280


def _label(text: str, css: str = "") -> Gtk.Widget:
    label = Gtk.Label(label=text)
    label.set_xalign(0.0)
    label.set_wrap(True)
    label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if css:
        label.add_css_class(css)
    return label


class _Section(Gtk.Box):
    """A titled group of rows, with one honest line when it has nothing."""

    def __init__(self, title: str) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_css_class("rail-section")
        self.set_margin_top(10)
        heading = _label(title, "rail-heading")
        heading.add_css_class("heading")
        self.append(heading)
        self._body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.append(self._body)

    def clear(self) -> None:
        child = self._body.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self._body.remove(child)
            child = following

    def row(self, text: str, detail: str = "") -> None:
        self._body.append(_label(text))
        if detail:
            self._body.append(_label(detail, "dim-label"))

    def empty(self, text: str) -> None:
        self._body.append(_label(text, "dim-label"))

    def meter(self, fraction: float, tooltip: str = "") -> None:
        """A level bar under a row: a share of something is read faster as a
        length than as a number, and the context headline was only a number."""
        bar = Gtk.LevelBar(min_value=0.0, max_value=1.0)
        bar.set_value(max(0.0, min(1.0, float(fraction))))
        # Offsets named for the theme's own colours: under 75% normal, then
        # high, then full - where "it forgot what I said" starts.
        bar.add_offset_value("low", 0.75)
        bar.add_offset_value("high", 0.9)
        bar.add_offset_value("full", 1.0)
        bar.add_css_class("rail-meter")
        if tooltip:
            bar.set_tooltip_text(tooltip)
        self._body.append(bar)

    def link(self, text: str, detail: str, surface: str,
             on_open: Callable[[str], None]) -> None:
        """A row that opens the panel holding the rest of the story.

        A rail that reports a fact a person must then go hunting for has moved
        the work rather than removed it. The whole row is the target, not just
        its text, and it is a real button so it is reachable by keyboard and
        announced as one.
        """
        button = Gtk.Button()
        button.add_css_class("flat")
        button.set_has_frame(False)
        button.set_valign(Gtk.Align.START)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content.append(_label(text))
        if detail:
            content.append(_label(detail, "dim-label"))
        button.set_child(content)
        button.set_tooltip_text(f"Open the {surface} panel")
        button.update_property([Gtk.AccessibleProperty.LABEL],
                               [f"Open the {surface} panel: {text}"])
        button.connect("clicked", lambda _b: on_open(surface))
        self._body.append(button)


class RailBreakpoint:
    """Hide the rail on a window too narrow to give it a real column.

    Same libadwaita breakpoint machinery as the left sidebar
    (`sidebar.SidebarBreakpoint`), for the same reason: below the threshold the
    rail's labels wrap into two and three lines each and the conversation - the
    reason the window exists - loses half its width to a column of fragments.
    The threshold is higher than the sidebar's (1040 rather than 720) because
    the window already has a left sidebar eating 260px before the rail is
    considered.

    `apply()` takes **the rail**, not the box that holds the conversation beside
    it. The first version was handed the row and set `visible` on *that* - so
    at 600px it hid the conversation too and the window rendered an empty right
    half with no orb, no transcript and no composer. Found by rendering, not by
    reading: the breakpoint code read correctly and the target was simply the
    wrong widget.
    """

    @staticmethod
    def apply(rail: "Gtk.Widget", max_width: int = 1040) -> "Adw.Breakpoint":
        """Build the breakpoint that hides `rail` below `max_width`.

        It must be handed to `window.add_breakpoint()`: a breakpoint no window
        knows about is never evaluated, which is how this window's sidebar
        breakpoint had been dead for its whole life.
        """
        condition = Adw.BreakpointCondition.parse(f"(max-width: {max_width}px)")
        breakpoint = Adw.Breakpoint.new(condition)
        breakpoint.add_setter(rail, "visible", False)
        return breakpoint


class NowRail(Gtk.Box):
    """The right-hand rail. `refresh()` rebuilds it from the stores.

    Reads are tiny (a JSON file or a config key), so this is a synchronous
    rebuild on demand rather than a second thread: a rail whose sections update
    a frame late from a polling thread is a rail that flickers.

    `on_open(surface_name)` is the one action a row can take - "open the panel
    that has the rest of this". It is a callback rather than a call into the
    window so the rail stays buildable and testable on its own, which is what
    lets every section below be verified without a GTK application.
    """

    def __init__(self, get_state: Optional[Callable[[], str]] = None,
                 get_tool: Optional[Callable[[], str]] = None,
                 on_open: Optional[Callable[[str], None]] = None,
                 get_context: Optional[Callable[[], object]] = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("now-rail")
        self.set_size_request(RAIL_WIDTH, -1)
        self.set_margin_start(12)
        self.get_state = get_state or (lambda: "")
        self.get_tool = get_tool or (lambda: "")
        self.on_open = on_open or (lambda _name: None)
        self.get_context = get_context or (lambda: None)

        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        # **No `set_propagate_natural_width`.** Rendered and measured: with it,
        # the rail asked for its labels' natural width, the box honoured it, and
        # a long posture line ("nothing is remembered, and nothing is disclosed")
        # pushed the rail past its own `size_request` and off the right edge of
        # the window - the one thing a fixed-width column exists to prevent. The
        # scroller is given the rail's width and the labels wrap inside it.
        scroller.set_hexpand(False)
        scroller.set_propagate_natural_height(True)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        inner.set_margin_top(8)
        inner.set_margin_bottom(16)
        inner.set_margin_start(14)
        inner.set_margin_end(14)

        self._now = _Section("Now")
        self._context = _Section("Context")
        self._timers = _Section("Timers")
        self._changed = _Section("Changed")
        self._tasks = _Section("Tasks")
        self._goals = _Section("Goals")
        self._posture = _Section("Posture")
        for section in (self._now, self._context, self._timers, self._changed,
                        self._tasks, self._goals, self._posture):
            inner.append(section)

        scroller.set_child(inner)
        self.append(scroller)
        self.refresh()

    # -- sections ---------------------------------------------------------

    def _fill_now(self) -> None:
        self._now.clear()
        state = str(self.get_state() or "").strip()
        self._now.row(state or "Idle", "" if state else "Nothing is running")
        tool = str(self.get_tool() or "").strip()
        if tool:
            self._now.row(f"Running {tool}", "")

    def _fill_context(self) -> None:
        """How full the model's window is, and what is filling it.

        opencode's session header shows a progress circle with usage, tokens
        and cost (`session-context-usage.tsx:128`) and its tooltip breaks the
        prompt down by `system | user | assistant | tool | other`
        (`session-context-breakdown.ts:4`). cline prints the numbers on its
        compaction row. Chronoa had all of them internally and showed none,
        which made "it forgot what I said" indistinguishable from "the window
        is full" - the single most alarming thing that can happen to a local
        assistant, and the one nobody can see coming.
        """
        self._context.clear()
        report = None
        try:
            report = self.get_context()
        except Exception as exc:  # noqa: BLE001 - a glance must not take the window down
            logger.debug("rail: context report unreadable: %s", exc)
        if report is None:
            self._context.empty("Nothing measured yet")
            return
        self._context.row(report.headline())
        # **The bar shows the *usable* share, not the raw one.** A window that
        # is 80% full of input while 1,024 tokens are held back for the reply
        # is 91% used, and the number that answers "can another question still
        # fit" is the second one. `usable_percent` has been computed on every
        # turn since the meter shipped and had no caller at all, so the rail
        # read the one figure that cannot answer that question - the same
        # "a feature that exists and is unreachable" gap as the organ strip's
        # centring, found by the same never-called scan.
        #
        # The raw percentage stays in the row above it, so nothing is lost.
        # With no reserve set the two are equal and the plain wording is the
        # honest one.
        usable = report.usable_percent
        reserve = report.reply_reserve
        if reserve and usable is not None and report.percent is not None:
            self._context.meter(
                usable / 100.0,
                f"{usable:.0f}% of the room this turn can send "
                f"({report.percent:.0f}% of the raw window, with "
                f"{reserve:,} tokens held back for the reply)")
        elif report.percent is not None:
            self._context.meter(report.percent / 100.0,
                                f"{report.percent:.0f}% of the model's window in use")
        for label, tokens, share in report.segment_rows()[:4]:
            self._context.row(label, f"{tokens:,} tokens ({share:.0f}%)")
        if report.elided:
            # The one line that has to be here: what was cut, so that a change
            # in behaviour has a reason attached to it.
            said = (f"{report.elided_messages} tool result(s) shortened" if
                    report.elided_messages else
                    f"{report.dropped_messages} earlier message(s) left out")
            self._context.row(said, "the window was full")

    def _fill_timers(self) -> None:
        self._timers.clear()
        try:
            from shani_chronoa.skills.timer import active_timers
            pending = active_timers()
        except Exception as exc:  # noqa: BLE001 - a glance must not take the window down
            logger.debug("rail: timers unreadable: %s", exc)
            self._timers.empty("Could not read the timers")
            return
        if not pending:
            self._timers.empty("No timers pending")
            return
        for _identifier, label, left in pending:
            # Not a link: there is no clock/timers panel to open, and a row
            # that pointed at a panel id the window cannot show would answer
            # "That panel is not available" - the one destination worse than
            # none. A countdown is read, not navigated to; cancelling one is a
            # spoken or typed request, not a click here.
            self._timers.row(label, f"{_human_seconds(left)} left")

    def _fill_changed(self) -> None:
        self._changed.clear()
        try:
            from shani_chronoa.skills.undo_last_change import recent_changes
            pairs = recent_changes()
        except Exception as exc:  # noqa: BLE001
            logger.debug("rail: undo ring unreadable: %s", exc)
            self._changed.empty("Could not read what Chronoa changed")
            return
        if not pairs:
            self._changed.empty("Nothing changed since the last undo point")
            return
        shown = pairs[:6]
        for path, before, after in shown:
            added = sum(1 for line in after.splitlines()
                        if line not in before.splitlines())
            self._changed.link(path.name or str(path), f"{added} line(s) added",
                               "diff", self.on_open)
        if len(pairs) > len(shown):
            self._changed.empty(f"and {len(pairs) - len(shown)} more")

    def _fill_tasks(self) -> None:
        self._tasks.clear()
        config = config_mod.ChronoaConfig()
        if not config.get_bool("todo-list-enabled", False):
            # The consent key governs the rail exactly as it governs the skill:
            # a list the user switched off is not shown here either. Saying so
            # is better than silence, because silence would read as "no tasks"
            # when the truth is "you are not looking at them".
            self._tasks.empty("Task list is off (Settings → Privacy)")
            return
        try:
            from shani_chronoa.skills import todo_list
            items, problem = todo_list._load()  # noqa: SLF001 - one reader, same store
        except Exception as exc:  # noqa: BLE001
            logger.debug("rail: task list unreadable: %s", exc)
            self._tasks.empty("Could not read the task list")
            return
        if problem:
            self._tasks.empty("The task list could not be read")
            return
        outstanding = [i for i in (items or []) if i.get("status") != "completed"]
        if not outstanding:
            self._tasks.empty("Nothing outstanding")
            return
        marks = {"pending": "", "in_progress": "(doing) ", "blocked": "(blocked) "}
        for item in outstanding[:8]:
            mark = marks.get(str(item.get("status") or ""), "")
            self._tasks.row(f"{mark}{item.get('content', '')}")
        if len(outstanding) > 8:
            self._tasks.empty(f"and {len(outstanding) - 8} more")

    def _fill_goals(self) -> None:
        """Open goal runs, each a link to the Goals panel. Hidden when none.

        Hidden rather than "No goals" because, unlike tasks, there is no
        consent switch whose state the silence could be mistaken for: saved
        goals are either there or not, and an empty card on every window is
        decoration.
        """
        self._goals.clear()
        from shani_chronoa.gui.surfaces.goals import open_runs
        runs = open_runs()
        self._goals.set_visible(bool(runs))
        for run in runs[:4]:
            word = {"awaiting": "waiting for you", "pending": "not started",
                    "running": "in progress"}.get(run.phase.value, run.phase.value)
            self._goals.link(run.goal, f"{word} - {min(run.index, len(run.steps))}/"
                                       f"{len(run.steps)} steps", "goals", self.on_open)
        if len(runs) > 4:
            self._goals.empty(f"and {len(runs) - 4} more")

    def _fill_posture(self) -> None:
        self._posture.clear()
        try:
            from shani_chronoa import permissions
            mode = permissions.get_mode()
        except Exception:  # noqa: BLE001
            mode = "unknown"
        labels = {"default": "Normal - risky calls ask",
                  "dont_ask": "No questions asked - risky calls are refused",
                  "explore": "Explore - reads only, writes refused"}
        self._posture.row(labels.get(mode, "Unknown mode"))
        config = config_mod.ChronoaConfig()
        # The same question the memory sense answers for itself - the rail is a
        # summary of the senses, so it must not report a different answer than
        # the one the scheduler will act on.
        memory_on = config.memory_sense_enabled
        self._posture.row(
            "Memory is on" if memory_on else "Memory is off",
            "" if memory_on else "nothing is remembered or disclosed")
        try:
            from shani_chronoa.sandbox.qualify import qualify
            proven = qualify().proven
        except Exception:  # noqa: BLE001
            proven = False
        self._posture.row(
            "Sandbox proven here" if proven
            else "Sandbox filter is per-child")
        # The rules file is sent on every turn and appears nowhere in the
        # transcript - so it changed every answer with nothing on screen saying
        # it was there. One line, only when it is.
        #
        # A **refused** file gets its own row rather than no row. It is the same
        # question either way - "are my rules in force?" - and silence reads as
        # "yes, and there happen to be none", which is the one answer that is
        # wrong in both directions at once. The row names the phrase, because a
        # refusal the person cannot locate is a refusal they will not fix.
        try:
            from shani_chronoa import user_prompts
            rules, refusal = user_prompts.rules_verdict()
        except Exception:  # noqa: BLE001
            rules, refusal = "", None
        if refusal:
            self._posture.row(
                "Your rules file was refused",
                f"not sent - {refusal!r} reads like changing the safety rules")
        elif rules:
            lines = len([ln for ln in rules.splitlines() if ln.strip()])
            self._posture.row("Your rules apply to every answer",
                              f"{lines} line(s) from rules.md")

    # -- refresh ----------------------------------------------------------

    def refresh(self) -> None:
        """Rebuild every section. Never raises: a rail that cannot be built
        must leave the window exactly as it was."""
        for fill in (self._fill_now, self._fill_context, self._fill_timers,
                     self._fill_changed, self._fill_tasks, self._fill_goals,
                     self._fill_posture):
            try:
                fill()
            except Exception as exc:  # noqa: BLE001
                logger.warning("rail: %s failed: %s", fill.__name__, exc)


def _human_seconds(seconds: float) -> str:
    """`3m 12s`, `45s`, `1h 04m` - short enough for a 280px rail."""
    remaining = max(0, int(seconds))
    if remaining < 60:
        return f"{remaining}s"
    minutes, secs = divmod(remaining, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"
