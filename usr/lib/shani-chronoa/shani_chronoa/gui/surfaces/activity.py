"""The activity surface: Chronoa's tool-call audit trail, as a widget.

Newest first: tool name, a short digest of the arguments, the exit/status, and
when it ran. A filter narrows by tool name. Read-only.

The rows come from a `ToolTracker` the caller hands in (via `app.tool_tracker`)
or, absent one, a fresh `ToolTracker` pointed at a temporary directory - never
from the live `tools._TRACKER` singleton, whose directory is the user's real
state directory. Importing this module instantiates nothing.

**The page is `surfaces/common.py`'s; what is under the toolbar is this
module's.** `common.surface()` builds the `Adw.NavigationPage`, its
`ToolbarView` and its `HeaderBar`, `common.group()`/`common.row()` build the
rows and `common.search_entry()` the filter, so this panel reads as one of the
sidebar's rather than as a hand-built box that happens to sit beside them. The
filter stays pinned above the rows and only the rows scroll, which is why the
scroller is inside the view rather than around it. Both empty answers - "no
tool calls recorded" and "nothing matches this filter" - are said as a page: a
filtered audit trail that matched nothing used to be a rectangle of nothing,
which reads as a bug rather than as an answer.

**`build()` returns the page, so the filter is reachable from what it returns.**
The filter is driven the way a person drives it - typing, then waiting out
`Gtk.SearchEntry`'s debounce - and the entry is reached as `page._search` rather
than by digging through the page for a private box. `rows()` is forwarded for
the same reason.

**Markup is off on every row, and arranging that costs something.** An
`Adw.ActionRow` renders its title and subtitle through Pango, and a tool
call's status and arguments are prose: a `<b>` or a bare `&` in a skill's
output is a parse error, which renders the row *empty* rather than wrong
(measured - `Gtk.Label.set_markup` refuses the string and the label keeps
whatever text it already had). `common.row()` sets the title before a caller
could switch markup off, so such a title emits one `Gtk-WARNING` on stderr and
then renders as the plain text it is: noisy, harmless, and only fixable in
`common.py`.
"""

from __future__ import annotations

import json
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from shani_chronoa import tool_tracking  # noqa: E402
from shani_chronoa.gui.surfaces import common  # noqa: E402

logger = logging.getLogger(__name__)

TITLE = "Activity"
ICON = "view-continuous-symbolic"
SUBTITLE = "Every skill Chronoa ran, newest first, from its own tool-call log."


def _clip(text: Any, limit: int = 80) -> str:
    s = _flatten(text)
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _flatten(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split())
    try:
        return " ".join(json.dumps(value, default=repr).split())
    except (TypeError, ValueError):
        return " ".join(repr(value).split())


def _status(record: dict) -> str:
    verdict = record.get("verdict")
    if verdict:
        return str(verdict)
    result = record.get("result")
    if isinstance(result, str) and result.strip():
        return _clip(result, 40)
    return "no status recorded"


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _records_from_log(path: Path) -> List[dict]:
    out: List[dict] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, UnicodeError):
        return out
    for line in lines:
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(record, dict) or not isinstance(record.get("tool_name"), str):
            continue
        out.append(record)
    return out


def _records(tracker: tool_tracking.ToolTracker) -> List[dict]:
    """Newest first. The log file is the source of truth; the in-memory ring is
    the fallback for a ring that was never written to disk. A missing, empty,
    or unreadable log file is an empty state, never an exception."""
    records = _records_from_log(tracker.log_file)
    if not records:
        try:
            records = [c.to_dict() for c in tracker.get_calls()]
        except Exception:  # a ring that cannot be read is empty, not an error
            records = []

    def _when(record: dict) -> datetime:
        return _parse_timestamp(record.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc)

    records.sort(key=_when, reverse=True)
    return records


def _args_digest(args: Any, limit: int = 80) -> str:
    if not args:
        return ""
    if isinstance(args, dict):
        parts = []
        for key, value in args.items():
            parts.append(f"{key}={_clip(value, 24)}")
        return _clip(", ".join(parts), limit)
    return _clip(args, limit)


def _matches(record: dict, needle: str) -> bool:
    """Case-insensitive substring on the tool name. A filter that always
    matches is the control for the narrowing tests."""
    if not needle:
        return True
    return needle.lower() in str(record.get("tool_name", "")).lower()


def _row(record: dict) -> Gtk.Widget:
    when = _parse_timestamp(record.get("timestamp"))
    when_text = when.astimezone().strftime("%Y-%m-%d %H:%M:%S") if when else "time unreadable"
    tool = _flatten(record.get("tool_name", "")) or "unnamed tool"
    status = _status(record)
    digest = _args_digest(record.get("args"))

    row = common.row(f"{tool} - {status}",
                     " | ".join(part for part in (when_text, digest and f"args {digest}") if part))
    # Adw parses title/subtitle as Pango markup; the audit trail is skill
    # prose, so rendering without markup (rather than escaping every string)
    # is the one call that cannot be half-done. `common.row()` has already set
    # both by this point, which is why a title carrying a bare `&` warns once
    # and then reads correctly - see this module's docstring.
    if isinstance(row, Adw.ActionRow):
        row.set_use_markup(False)
    row.add_css_class("tool-call-row")
    tooltip_bits = []
    if digest:
        tooltip_bits.append(f"args: {digest}")
    result = _flatten(record.get("result"))
    if result:
        tooltip_bits.append(f"result: {_clip(result, 120)}")
    if tooltip_bits:
        row.set_tooltip_text(" | ".join(tooltip_bits))
    return row


def _add(group: Gtk.Widget, row: Gtk.Widget) -> None:
    """Put a row into a group from `common.group()`.

    `Adw.PreferencesGroup` has `add()`; a plain `Gtk.Box` on GTK4 does not -
    `append` replaced `add` - and `common.group()` returns whichever of the two
    libadwaita allowed. Asking which is one line, and it is why the plain-GTK
    fallback is a fallback that works rather than one that raises.
    """
    adder = getattr(group, "add", None)
    if callable(adder):
        adder(row)
    else:
        group.append(row)


class _ActivityView(Gtk.Box):
    """The filter and the rows: everything under the page's toolbar.

    Records are read once, in `__init__`. This is the one place the surface
    knows *which* calls exist; the filter narrows a list already in hand rather
    than re-reading the log on every keystroke, so an unreadable or corrupt log
    is one empty state at open time instead of one per keystroke.
    """

    def __init__(self, tracker: tool_tracking.ToolTracker):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._tracker = tracker
        self._records = _records(tracker)
        # The panel's own health, above the filter and the rows:
        # how many calls the log holds, and whether the log could be
        # read at all. One row, one dot, one word - the question the
        # panel is opened for, before the rows that hold the calls.
        self._status_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.append(self._status_slot)
        #: The same health in two places - the row above, and the dot on the
        #: sidebar's row for this panel. One value, so the two cannot disagree.
        self.status_recorder = common.StatusRecorder()
        self._render_status()
        self._search = common.search_entry("Filter by tool name…", self._on_search_changed)
        self._group = common.group()
        self._rows_area = common.scrolled(self._group)
        self._row_widgets: List[Gtk.Widget] = []
        # One slot, two answers: the rows, or the reason there are none.
        # Stacking them instead would say "no tool calls recorded" under a list
        # of something.
        self._area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._area.set_vexpand(True)
        self.append(self._search)
        self.append(self._area)
        self._render("")
        # Above the per-call rows, because "which skill should I not trust" is the
        # question this panel is opened for; "what happened at 14:03" is the
        # second one.
        self._summary = build_summary(self._records)
        # `build_summary` returns a plain empty `Gtk.Box` when there is nothing
        # to say, and a `Gtk.Box` has no `get_child()` - that call raised
        # `AttributeError` and took the whole Activity panel down on open.
        # `common.group` is an `Adw.PreferencesGroup`, which does have one, so
        # the emptiness test has to be "is this still the empty Box?", not a
        # method call that only one of the two return types supports.
        summary_children = (
            self._summary.get_children() if hasattr(self._summary, "get_children") else [])
        if summary_children:
            self._area.add_css_class("has-summary")
            # The summary goes above the per-call rows, because "which skill
            # should I not trust" is the question this panel is opened for.
            # `Gtk.Box` has no `insert_child_before`, so the order is rebuilt
            # rather than reached for.
            for child in (self._search, self._area):
                self.remove(child)
            self.append(self._summary)
            self.append(self._search)
            self.append(self._area)

    def _on_search_changed(self, needle: str) -> None:
        self._render(needle)

    def _render_status(self) -> None:
        """The panel's own health, before the filter and the rows."""
        common.clear(self._status_slot)
        if not self._records:
            self._status_slot.append(self.status_recorder.row(
                common.STATUS_OK,
                "No tool calls recorded",
                "the log is empty, which is not the same as every call "
                "having succeeded"))
            return
        verified = sum(1 for r in self._records if r.get("verified"))
        failed = sum(1 for r in self._records if r.get("failed"))
        if failed:
            self._status_slot.append(self.status_recorder.row(
                common.STATUS_ATTENTION,
                f"{len(self._records)} calls, {failed} failed",
                f"{verified} verified, {failed} failed, "
                f"{len(self._records) - verified - failed} unverified"))
        elif verified:
            self._status_slot.append(self.status_recorder.row(
                common.STATUS_OK,
                f"{len(self._records)} calls, {verified} verified",
                f"{verified} verified, {len(self._records) - verified} "
                "unverified, none failed"))
        else:
            self._status_slot.append(self.status_recorder.row(
                common.STATUS_UNKNOWN,
                f"{len(self._records)} calls, none verified",
                f"{len(self._records)} unverified, none failed"))

    def _present(self, widget: Gtk.Widget) -> None:
        """One widget in the slot; whatever was there is removed."""
        child = self._area.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self._area.remove(child)
            child = following
        self._area.append(widget)

    def _render(self, needle: str) -> None:
        # Removed by reference, not by walking the group for children:
        # libadwaita keeps its rows inside its own boxes, so a walk finds
        # boxes that are not its children and cannot be removed from it.
        for row in self._row_widgets:
            self._group.remove(row)
        self._row_widgets = []
        shown = 0
        for record in self._records:
            if not _matches(record, needle):
                continue
            row = _row(record)
            _add(self._group, row)
            self._row_widgets.append(row)
            shown += 1
        if shown:
            self._present(self._rows_area)
            return
        # The needle is not repeated in the message: it is on screen in the
        # filter directly above, and it is user text on a widget that renders
        # markup - saying it twice would mean escaping it or risking a parse
        # error for no gain.
        self._present(common.empty_state(
            ICON,
            "No tool calls match this filter." if needle else "No tool calls recorded.",
            "Every tool call is in the log, none of them named that."
            if needle else "Nothing has been written to the tool-call log yet.",
        ))

    def rows(self) -> List[Gtk.Widget]:
        return list(self._row_widgets)


#: How many calls a skill needs before its failure rate means anything. Shown
#: beside every rate, because "91% failed" on four calls and on four thousand are
#: different sentences and this panel used to be able to tell you neither.
MIN_CALLS_FOR_A_RATE = 20


def installed_skills() -> "set[str]":
    """The skills this build actually has.

    Read from the tool registry rather than a list, so it is the real set. It
    matters because **a name in the log is not necessarily a skill you have**:
    measured on this machine, two tools that no longer exist (`liar`,
    `unver`) account for 692 of 12,856 logged calls and **313 of the 378
    failures**. A panel that says "failed 313 of 313" without that is calling a
    fixture a broken skill.
    """
    from shani_chronoa import tools
    return {entry["function"]["name"] for entry in tools.TOOLS}


def per_skill(records, known=None) -> "list[tuple]":
    """`(tool, calls, verified, failed, installed)` per skill, worst rate first.

    **The reason this exists.** `liar` has failed 313 times out of 344 and has
    never once verified - and in a list of per-call rows that is 313 identical
    lines and no total, so a skill that is broken has been collecting its own bug
    report for months without anything showing it. The per-call view answers "what
    happened at 14:03"; this one answers "which of my skills should I not trust",
    which is the question a person opening this panel actually has.

    It is a count, not a model, and it says so: a rate with its denominator
    beside it. No inference, nothing to refuse.
    """
    tally: dict = {}
    for record in records:
        tool = record.get("tool_name") or "?"
        verdict = record.get("verdict")
        if verdict not in ("verified", "failed"):
            continue            # `unverified` is most calls, and averages to noise
        row = tally.setdefault(tool, [0, 0, 0])
        row[0] += 1
        row[1] += 1 if verdict == "verified" else 0
        row[2] += 1 if verdict == "failed" else 0
    known = installed_skills() if known is None else known
    out = [(tool, calls, verified, failed, tool in known)
           for tool, (calls, verified, failed) in tally.items()]
    # Worst first, and a skill that fails every time outranks one that fails
    # often: the first is broken, the second is used more.
    out.sort(key=lambda r: (-(r[3] / r[1] if r[1] else 0), -r[3], r[0]))
    return out


def _rate_sentence(tool: str, calls: int, verified: int, failed: int,
                   installed: bool = True) -> str:
    """One line a person can act on, with the count it is a proportion of.

    **A tool that is no longer installed says so first**, because the two
    readings are completely different and only one of them is a defect: a skill
    that fails every time is broken, and a *fixture* that fails every time was
    doing exactly what it was built to do.
    """
    rate = failed / calls if calls else 0.0
    gone = ("" if installed
            else " - not installed any more, so this is history, not a fault")
    if failed == 0:
        return f"{verified} of {calls} calls verified, none failed{gone}"
    if verified == 0 and calls >= MIN_CALLS_FOR_A_RATE:
        return (f"failed every one of its {calls} calls and never verified - "
                f"this skill looks broken, not unlucky{gone}")
    if calls < MIN_CALLS_FOR_A_RATE:
        return f"{failed} failed of {calls} calls - too few to call it a rate{gone}"
    return (f"{failed} of {calls} calls failed ({rate:.0%}), "
            f"{verified} verified{gone}")


def build_summary(records) -> Gtk.Widget:
    """The per-skill block, or nothing at all when there is nothing to say."""
    rows = per_skill(records)
    if not rows:
        return Gtk.Box()
    group = common.group(
        "By skill",
        "Which skills have been earning their place. Worst first, and every "
        "rate carries the count it is a proportion of.")
    for tool, calls, verified, failed, installed in rows:
        _add(group, common.row(
            tool, _rate_sentence(tool, calls, verified, failed, installed)))
    return group



def build(app) -> Gtk.Widget:
    tracker = getattr(app, "tool_tracker", None)
    if tracker is None:
        tmp = Path(tempfile.mkdtemp(prefix="chronoa-activity-"))
        tracker = tool_tracking.ToolTracker(log_dir=tmp)
    view = _ActivityView(tracker)
    page, set_content = common.surface(TITLE, SUBTITLE)
    set_content(view)
    # `build()` hands back the page, so the two things a caller needs from this
    # surface are reachable on it: the filter to narrow by, and the rows that
    # survived the filter. Set as attributes rather than wrapped, because the
    # page is a libadwaita widget and this is the one shape that works on it.
    page._search = view._search
    page.rows = view.rows
    # What this panel says about itself, for the sidebar's health dot. The
    # recorder is the view's own, so the dot cannot disagree with the row above
    # it in the panel - there is no second count to fall out of date.
    page.status = view.status_recorder.status
    return page


# Kept for tests and other surfaces that want the same filter semantics.
__all__ = ["TITLE", "ICON", "build"]
