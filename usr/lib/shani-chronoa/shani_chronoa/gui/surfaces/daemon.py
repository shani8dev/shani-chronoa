"""Background mode: the setting, the user unit behind it, and the daemon's log.

Three questions, one panel, each answered from the thing that actually holds
the answer rather than from a copy of it:

- **Is it on?** The `background-mode-enabled` key, read and written through
  `ChronoaConfig.get_bool`/`set` - the same two calls the settings window's
  `_read_bool`/`_set_bool` make (`settings_window/window.py:147`), so the switch
  here and the switch in Settings cannot disagree about how this key is written.
  No second way of writing it is introduced here.
- **Is the unit there, and is it running?** `systemctl --user is-enabled` and
  `is-active`, read-only, with a five second timeout. Nothing on this surface
  starts, stops, enables or disables anything: the unit is changed by the app at
  startup (`app/desktop_integration.py:_sync_background_mode`), and a settings
  panel that quietly became the thing it reports on is how a machine ends up
  with a service nobody chose.
- **What has the daemon been saying?** Its own last lines.

**"Could not ask" is a state, not a failed report.** `systemctl is-*` exits
non-zero for perfectly ordinary answers - measured on this machine, `is-enabled`
on a unit that is not installed and `is-active` on one that is not running both
exit 4 *and print the real answer* - so the exit code cannot be the test, and
treating it as one is how a healthy machine gets a panel insisting its daemon is
stopped every time systemd declines to be explicit. The answer is systemd's own
word on stdout; an unrecognised or empty stdout means the question was never
answered, and that renders as its own sentence. Same reasoning as
`senses/services.py`, which reports UNKNOWN rather than "nothing is failing".

**Where the daemon's log actually is.** `daemon.py` writes no log file of its
own: its `logging.basicConfig` sends records to stderr, and the unit captures a
service's stderr into the user journal. So the tail shown here is
`journalctl --user -u shani-chronoa-daemon.service --no-pager -n`, which reads
and writes nothing. If that ever stops being where the daemon's output lands,
this module is wrong rather than the log having moved silently.

**Built from `gui/surfaces/common.py`, like every other panel.** The page and its
header bar, the groups, the rows and the scroller are all the shared scaffolding
rather than boxes written out again here: eight panels that each grew their own
layout are eight panels that do not look like one product, and this one is the
panel a user opens to decide whether to trust a background daemon. The rows are
`Adw.ActionRow` / `Adw.SwitchRow` because those carry the focus order, the
keyboard behaviour and the single accessible name a screen reader needs to
announce "Unit file" and its sentence as one control - a box of two labels is
announced as two unrelated strings. `Adw.init()` happens inside `common` at
import time, before anything here is constructed; a widget built before Adw is
initialised renders as nothing at all, with no error, which is exactly the
failure this panel would have shipped if the call lived in a constructor.

The sentence under the switch is the property that makes background mode safe
to turn on (the daemon runs rules and senses and never opens the microphone), so
it is stated on the panel instead of being left in `daemon.py`'s docstring where
nobody switching the setting on will read it.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Any, Callable, List, NamedTuple, Optional, Tuple

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa import markdown_lite
from shani_chronoa.gui.surfaces import common

logger = logging.getLogger(__name__)

TITLE = "Background mode"
ICON = "emblem-system-symbolic"

#: The systemd *user* unit `usr/bin/shani-chronoa-daemon` runs. Named here
#: rather than imported from the application class, because reading it must not
#: construct or start anything.
UNIT = "shani-chronoa-daemon.service"

#: The gschema boolean that says whether background mode is wanted.
CONFIG_KEY = "background-mode-enabled"

#: The sentence that makes this setting safe, on the panel rather than only in
#: `daemon.py`'s docstring. It states the property, not the marketing.
MICROPHONE_NOTE = (
    "The microphone stays off while the window is closed. Background mode runs "
    "your rules and your senses; it never listens, so a wake phrase cannot open "
    "it with nothing on screen to answer you."
)

#: The same fact again, next to the control that turns it on, naming the key it
#: writes. The page subtitle above is the reason; this is what someone deciding
#: whether to flip the switch wants to know before flipping it.
SETTING_CAPTION = (
    f"Writes {CONFIG_KEY}, the same key as the switch in Settings. On, the daemon "
    "runs your rules and senses with the window closed - and never listens."
)

#: Why the two systemd answers below are only ever read.
UNIT_CAPTION = (
    "Read-only. Chronoa enables or disables the unit for this switch the next "
    "time it starts; this panel never starts, stops, enables or disables "
    "anything itself."
)

#: Short, because this is a panel someone is looking at: a `systemctl --user`
#: call that has blocked for ten seconds is a window that has stopped
#: repainting. The app's own `_sync_background_mode` allows longer, because it
#: runs at startup where nobody is waiting on a frame.
SYSTEMCTL_TIMEOUT = 5

JOURNAL_TIMEOUT = 8
#: Lines shown before the tail is cut and the cut is admitted.
JOURNAL_LINES = 12
#: A journal line is not a document; a long one is clipped rather than wrapped
#: into something that reads as a different entry.
LINE_LIMIT = 200

#: The words systemd may print. Anything else on stdout - an empty answer, an
#: error page, a shell stub's echo - is not a state and must not become one.
FILE_STATES = frozenset({
    "enabled", "enabled-runtime", "linked", "linked-runtime", "alias",
    "masked", "masked-runtime", "static", "indirect", "disabled",
    "generated", "transient", "bad", "not-found",
})
ACTIVE_STATES = frozenset({
    "active", "reloading", "inactive", "failed", "activating",
    "deactivating", "maintenance",
})

#: Rows carry this so a test can find them by walking the built tree rather than
#: by reading a list the module stashed on itself - which is the count read back
#: out of the thing being counted, the mistake this repo has deleted a test for.
#: None of the three classes is styled anywhere in this tree; they are markers,
#: and saying so is what keeps them honest.
ROW_CSS = "chronoa-daemon-row"
VALUE_CSS = "chronoa-daemon-value"
LOG_CSS = "chronoa-daemon-log"


class _Unit(NamedTuple):
    """What `systemctl --user` said about the daemon unit, or why it did not.

    Each question carries its own reason, so one of them answering is not
    reported as the other answering too.
    """

    file_state: str
    file_reason: str
    active_state: str
    active_reason: str


class _Tail(NamedTuple):
    """The daemon's last output: `state` is lines/empty/unreadable."""

    state: str
    lines: List[str]
    withheld: int
    note: str


def _systemctl(which: str) -> "Optional[subprocess.CompletedProcess[str]]":
    """One read-only `systemctl --user is-<which>` call, or None if it failed.

    Read-only by construction: the only two callers pass `is-enabled` and
    `is-active`. Enabling or disabling from a panel that reports the unit would
    make the report a side effect of looking at it.
    """
    if shutil.which("systemctl") is None:
        return None
    try:
        return subprocess.run(
            ["systemctl", "--user", which, UNIT],
            capture_output=True, text=True, timeout=SYSTEMCTL_TIMEOUT, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("systemctl %s did not answer: %s", which, exc)
        return None


def _query(which: str, states: "frozenset[str]") -> Tuple[str, str]:
    """(state, reason) for one `is-` call; exactly one of the two is set.

    The exit code is deliberately not consulted. On this machine `is-enabled`
    on a unit that is not installed and `is-active` on one that is not running
    both exit 4 *and print their real answer on stdout*, so a non-zero exit is
    the ordinary case, not a failure. A word this module does not recognise -
    including no word at all, with the reason on stderr - is a question that was
    never answered.
    """
    if shutil.which("systemctl") is None:
        return "", "systemctl is not installed on this machine"
    proc = _systemctl(which)
    if proc is None:
        return "", f"systemctl did not answer within {SYSTEMCTL_TIMEOUT}s"
    text = (proc.stdout or "").strip()
    if text in states:
        return text, ""
    noise = ((proc.stderr or "").strip().splitlines() or ["systemctl printed no state"])[0]
    return "", noise[:160]


def _unit() -> _Unit:
    """Ask systemd about the unit. Never raises: it is called from `build`."""
    file_state, file_reason = _query("is-enabled", FILE_STATES)
    active_state, active_reason = _query("is-active", ACTIVE_STATES)
    return _Unit(file_state, file_reason, active_state, active_reason)


def _file_sentence(unit: _Unit) -> str:
    state = unit.file_state
    if not state:
        return (f"could not ask systemd ({unit.file_reason}) - so this does not "
                f"say whether {UNIT} is installed.")
    if state == "not-found":
        return f"not installed - there is no {UNIT} on this machine"
    if state.startswith("masked"):
        return (f"masked ({state}) - a link on this machine makes systemd refuse "
                f"to run it at all")
    if state in ("enabled", "enabled-runtime"):
        return f"installed and {state} - it comes up with your session"
    if state == "disabled":
        return "installed but disabled - nothing runs the rules in the background"
    if state == "bad":
        return f"installed but broken ({state}) - systemd could not read the unit file"
    return f"installed, {state} - it exists, but systemd does not start it on its own"


def _active_sentence(unit: _Unit) -> str:
    state = unit.active_state
    if not state:
        return (f"unknown - could not ask systemd ({unit.active_reason}). "
                f"Nothing on this panel says whether it is running.")
    if state == "active":
        return "running - the daemon is running your rules right now"
    if state == "inactive":
        return "stopped - nothing is running the rules in the background right now"
    if state == "failed":
        return (f"failed ({state}) - systemd started it and it did not stay up; "
                f"the log below is where it says why")
    return f"{state} - it is changing state, not settled"


def _log_tail(limit: int = JOURNAL_LINES) -> _Tail:
    """The daemon's own last lines, and the count of what was withheld.

    Read-only: `journalctl --no-pager -n`, which prints and changes nothing.
    `limit + 1` lines are asked for so that "there was more and here is the
    tail" is a fact rather than a guess.
    """
    if shutil.which("journalctl") is None:
        return _Tail("unreadable", [], 0,
                     "journalctl is not installed on this machine, so the "
                     "daemon's log cannot be read from here.")
    try:
        proc = subprocess.run(
            ["journalctl", "--user", "--unit", UNIT, "--no-pager",
             "--output", "short-iso", "--lines", str(limit + 1)],
            capture_output=True, text=True, timeout=JOURNAL_TIMEOUT, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _Tail("unreadable", [], 0,
                     f"journalctl did not answer within {JOURNAL_TIMEOUT}s ({exc}).")
    stderr = (proc.stderr or "").strip()
    stdout = proc.stdout or ""
    # journalctl answers on stdout and complains on stderr at the same time
    # when there is no journal at all, so both have to be looked at.
    if proc.returncode != 0 or "No journal files were found" in stdout + stderr \
            or "Failed to open journal" in stdout + stderr:
        why = (stderr.splitlines() or [f"journalctl exited {proc.returncode}"])[0]
        return _Tail("unreadable", [], 0,
                     f"The journal could not be read ({why[:160]}). That is a "
                     f"read failure, not an absence of log lines.")
    rows = [line.rstrip() for line in stdout.splitlines() if line.strip()]
    if rows and rows[0].startswith("-- No entries --"):
        return _Tail("empty", [], 0,
                     f"The journal has no entries for {UNIT}. It has not run on "
                     f"this session, or it ran before the journal was kept.")
    rows = [row[:LINE_LIMIT] for row in rows]
    withheld = max(0, len(rows) - limit)
    if withheld:
        note = (f"Showing the last {limit} lines; {withheld} earlier lines of "
                f"this unit's output are not shown.")
    else:
        note = "Showing everything the journal still has for this unit."
    return _Tail("lines", rows[-limit:], withheld, note)


def _prose(text: str) -> Gtk.Label:
    label = Gtk.Label(xalign=0, wrap=True)
    # Log lines and systemd output are not markup, and markup in a label is
    # interpreted rather than shown.
    label.set_use_markup(False)
    label.set_text(text)
    return label


def _find(node: Gtk.Widget,
          wanted: Callable[[Gtk.Widget], bool]) -> Optional[Gtk.Widget]:
    """The first descendant (or `node`) that `wanted` accepts, or None.

    libadwaita builds a row's title and subtitle labels itself, inside boxes of
    its own, so this module cannot hold a reference to them. This is how it
    gets at the two things it has to reach: the switch `common.switch_row()`
    attached, and the label that carries a row's value.
    """
    if wanted(node):
        return node
    child = node.get_first_child()
    while child is not None:
        found = _find(child, wanted)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def _is_switch(node: Gtk.Widget) -> bool:
    return isinstance(node, Gtk.Switch)


def _is_subtitle(node: Gtk.Widget) -> bool:
    """libadwaita gives a row's subtitle label the `subtitle` CSS class."""
    return isinstance(node, Gtk.Label) and "subtitle" in node.get_css_classes()


def _value_label(row: Gtk.Widget) -> Optional[Gtk.Label]:
    """Mark the label carrying this row's value with `VALUE_CSS`, and return it.

    `common.row()` hands the sentence to libadwaita, which builds the label, so
    the marker has to be attached here rather than to a label this module made.
    The class carries no styling anywhere in the tree - it is how this surface's
    own tests read a row's value by walking the built tree instead of trusting a
    list the module stashed on itself - and libadwaita names that label
    `subtitle`, which is what is found here. If a future libadwaita stops using
    that class the value is not marked, the tests fail loudly, and the panel
    itself is unaffected: a missing marker costs nothing but a walk.
    """
    label = _find(row, _is_subtitle)
    if label is None:
        logger.debug("no subtitle label to mark on %s", type(row).__name__)
        return None
    label.add_css_class(VALUE_CSS)
    return label if isinstance(label, Gtk.Label) else None


def _plain(text: str) -> str:
    """`text` as an Adw row's title or subtitle, escaped if that row is one.

    `Adw.PreferencesRow.use-markup` defaults to True (measured on libadwaita
    1.5), and the sentences on this panel carry words systemd printed - a reason
    string read off stderr, which is anything at all - so a bare `&` or `<` in
    one of them leaves the label empty with only a `Gtk-WARNING` on stderr
    (measured: `Failed to set text ... from markup due to error parsing markup`,
    and `get_text()` then returns `''`). A row that says nothing is worse than a
    row that says something broken, because "could not ask systemd" is exactly
    what the user needs to read. The plain-GTK answer from `common.row()` is a
    `Gtk.Label`, which takes no markup and would print the entities themselves,
    so the escape follows whichever branch built the row.
    """
    return markdown_lite.escape(text) if common.adw_ready() else text


def _row(title: str, value: str) -> Gtk.Widget:
    """One `Adw.ActionRow`: the question on the left, its answer underneath.

    `common.row()` because that is what carries the accessible name, the focus
    order and the hover state; the sentence is its subtitle rather than a second
    hand-made label, which is what lets a screen reader read the two as one
    control.
    """
    row = common.row(_plain(title), _plain(value))
    row.add_css_class(ROW_CSS)
    _value_label(row)
    return row


class _DaemonSurface:
    """The panel, and the switch whose position follows the key rather than the
    click.

    `build()` hands out the page; nothing else about the surface is public.
    """

    def __init__(self, app: Any) -> None:
        self._config = getattr(app, "config", None)
        self._updating = False
        self._switch: Optional[Gtk.Switch] = None
        self._state_label: Optional[Gtk.Label] = None
        #: What this panel says about itself, in the two places that show it:
        #: the row at the top of the panel and the dot on its sidebar row.
        self.status_recorder = common.StatusRecorder()
        self.page = self._build()

    # -- the page -------------------------------------------------------------

    def _build(self) -> Gtk.Widget:
        page, set_content = common.surface(TITLE, MICROPHONE_NOTE)
        body = common.page_body(14)

        # **One `systemctl` conversation, read once.** `_unit()` shells out
        # twice (is-enabled, is-active) and the status row and "The unit" group
        # both need it, so it is asked once here and passed to both. It was
        # asked twice - four subprocesses for two facts - which is not just
        # slower: on a machine where `systemctl` is slow or absent, the two
        # reads can disagree, and the panel would then say the unit is not
        # installed in one row and could not be asked in the other.
        # `tests/test_surface_daemon.py` pins the count.
        unit = _unit()

        # The panel's own health, above every group: is the
        # daemon running, is it enabled, or could it not be
        # told? One row, one dot, one word - the question the
        # panel is opened for, before the rows that hold the
        # setting and the unit.
        body.append(self._status_row(unit))

        setting = common.group("", SETTING_CAPTION)
        setting.add(self._setting_row())
        body.append(setting)

        about_unit = common.group("The unit", UNIT_CAPTION)
        about_unit.add(_row("Unit file", _file_sentence(unit)))
        about_unit.add(_row("Running now", _active_sentence(unit)))
        body.append(about_unit)

        body.append(self._log_group())
        set_content(common.scrolled(body))
        return page

    def _status_row(self, unit: "_Unit") -> Gtk.Widget:
        """The panel's own health, from the unit's own state.

        Takes the unit rather than asking for it, so this and "The unit" below
        it are one reading rather than two that can disagree - see `_build`.

        Written through `self.status_recorder` rather than straight to
        `common.status_row`, so the dot the sidebar draws reads the same word
        this row shows.
        """
        row = self.status_recorder.row
        active = unit.active_state
        enabled = unit.file_state
        if not active or not enabled:
            return row(
                common.STATUS_UNKNOWN,
                "The daemon's state could not be read",
                "systemctl did not answer, so nothing here was read")
        if active == "active" and enabled in ("enabled", "enabled-runtime"):
            return row(
                common.STATUS_OK,
                "Background mode is running and enabled",
                "the unit is active and enabled, so Chronoa starts "
                "at login and keeps running")
        if active == "active":
            return row(
                common.STATUS_ATTENTION,
                "Background mode is running but not enabled",
                "the unit is active now, but it is not enabled, so "
                "it will not start at the next login")
        if enabled in ("enabled", "enabled-runtime"):
            return row(
                common.STATUS_ATTENTION,
                "Background mode is enabled but not running",
                "the unit is enabled, but it is not active, so "
                "Chronoa is not running in the background")
        return row(
            common.STATUS_ATTENTION,
            "Background mode is off",
            "the unit is neither active nor enabled, so Chronoa "
            "does not run in the background")

    # -- the setting ---------------------------------------------------------

    def _read(self) -> bool:
        """`background-mode-enabled`, or False when it cannot be read.

        `get_bool`, not `get() == "true"`: the settings window's `_read_bool`
        exists for this reason and `ChronoaConfig.get()` answers its default for
        a boolean key, which would render this gate permanently off.
        """
        if self._config is None:
            return False
        try:
            return bool(self._config.get_bool(CONFIG_KEY, False))
        except Exception:  # noqa: BLE001 - an unreadable key is off, never raised
            logger.debug("cannot read %s", CONFIG_KEY, exc_info=True)
            return False

    def _write(self, value: bool) -> None:
        """The write `settings_window/window.py:_set_bool` makes, unchanged."""
        if self._config is None:
            logger.info("no settings available; %s cannot be written", CONFIG_KEY)
            return
        try:
            self._config.set(CONFIG_KEY, "true" if value else "false")
        except Exception:  # noqa: BLE001 - a refused write is re-derived below
            logger.warning("could not write %s", CONFIG_KEY, exc_info=True)

    def _setting_row(self) -> Gtk.Widget:
        """The one control on this panel, plus the state line under it.

        The switch starts where the key says it is, not where the helper's
        `active=` argument says: `common.switch_row()` takes that argument and
        does not put it on the widget, so the position is set here from the key -
        under `_updating`, so reading the key does not count as the user having
        moved the switch and does not write the key straight back.
        """
        # `False` is what the helper defaults to, and it is not what this row
        # shows: the position comes from the key, further down.
        row = common.switch_row(TITLE, "", False, self._on_toggled)
        row.add_css_class(ROW_CSS)
        self._state_label = _value_label(row)
        switch = _find(row, _is_switch)
        self._switch = switch if isinstance(switch, Gtk.Switch) else None
        if self._switch is None:  # pragma: no cover - the helper always adds one
            logger.warning("%s built with no switch", TITLE)
            return row

        self._updating = True
        try:
            self._switch.set_active(self._read())
        finally:
            self._updating = False
        if self._config is None:
            self._switch.set_sensitive(False)

        spoken = (f"Run Chronoa's rules in the background ({CONFIG_KEY}): "
                  f"{'on' if self._switch.get_active() else 'off'}")
        self._switch.set_tooltip_text(
            f"{spoken}. Writes {CONFIG_KEY}; Chronoa acts on it the next time it "
            f"starts. Never the microphone."
        )
        self._switch.update_property([Gtk.AccessibleProperty.LABEL], [spoken])
        self._refresh_state()
        return row

    def _refresh_state(self) -> None:
        if self._switch is None or self._state_label is None:
            return
        on = self._switch.get_active()
        # Escaped as well as the sentences above: this label is one of
        # libadwaita's, so it parses what it is given when it is rendered, and
        # `set_text()` stores the string without parsing it - the parse happens
        # later, where a stray `&` becomes a blank row and a warning nobody sees.
        self._state_label.set_text(_plain(
            f"{'on' if on else 'off'} ({CONFIG_KEY}) - "
            + ("the unit is enabled or disabled for this the next time Chronoa starts"
               if self._config is not None
               else "no settings available, so this cannot be read or written here"))
        )

    def _on_toggled(self, _active: bool) -> None:
        """`common.switch_row()`'s callback: the switch moved, so write the key.

        The `notify::active` the helper wires passes the new position, and the
        switch itself is read again rather than trusted: a refused write leaves
        a switch showing a setting nobody changed, which is the most misleading
        thing this panel could do.
        """
        if self._updating or self._switch is None:
            return
        self._write(bool(self._switch.get_active()))
        self._updating = True
        try:
            self._switch.set_active(self._read())
        finally:
            self._updating = False
        self._refresh_state()

    # -- the log -------------------------------------------------------------

    def _log_group(self) -> Gtk.Widget:
        """The daemon's last lines, and an admission of anything withheld.

        The log is a block rather than a row subtitle: a journal line is not
        prose, so it keeps its own sideways-scrolling label instead of being
        wrapped to fit, and the cut note sits under it rather than being buried
        in it.
        `ROW_CSS` goes on the group because the group's own title label is the
        heading this section is read by - which is true of the group and not of
        the untitled row inside it.
        """
        group = common.group(
            "What the daemon logged",
            f"{UNIT} writes to the user journal (its stdout and stderr), so this "
            f"is journalctl --user -u {UNIT}.",
        )
        group.add_css_class(ROW_CSS)
        row = common.row("")
        row.add_suffix(self._log_block())
        group.add(row)
        return group

    def _log_block(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_hexpand(True)
        tail = _log_tail()
        if tail.state != "lines":
            # An empty journal and a journal that cannot be read are different
            # answers, and only one of them means the daemon had nothing to say,
            # so the reason is shown in the log's own place - and it is the whole
            # row's content, not a footnote under lines that are not there.
            unreadable = _prose(tail.note)
            unreadable.add_css_class(LOG_CSS)
            box.append(unreadable)
            return box

        text = Gtk.Label(xalign=0, selectable=True)
        text.set_use_markup(False)
        text.set_text("\n".join(tail.lines))
        # `set_monospace` does not exist on a GTK4 `Gtk.Label` - measured on this
        # box's 4.14.5, which exposes no `monospace` property at all - so the
        # fixed-width face comes from the app's own `.reply-code` class in
        # `gui/style.py`, which is what every other piece of command output in
        # this UI uses.
        text.add_css_class("reply-code")
        text.add_css_class(LOG_CSS)
        # Its own scroller rather than `common.scrolled()`: that one pins the
        # horizontal policy to NEVER, which is right for a page of rows and
        # wrong for unwrapped log lines. Clipped at `LINE_LIMIT`, scrolled
        # sideways, never wrapped - a wrapped journal line reads as a different
        # entry, which is how a tail becomes a misreading.
        scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(text)
        box.append(scrolled)
        note = _prose(tail.note)
        note.add_css_class(VALUE_CSS)
        box.append(note)
        return box


def build(app: Any) -> Gtk.Widget:
    """The background-mode panel for `app`.

    `app` is anything with a `config`; one without it builds the panel with the
    switch insensitive rather than raising, because a window that has not
    finished loading its settings is a normal state and not a broken install.
    """
    surface = _DaemonSurface(app)
    # The sidebar's health dot, from the same recorder the panel's own row was
    # written through - so the dot cannot say the unit is running while the row
    # under it says it is not.
    surface.page.status = surface.status_recorder.status
    return surface.page


# Kept for tests and for surfaces that want the same read-only semantics.
__all__ = ["TITLE", "ICON", "build", "UNIT", "CONFIG_KEY", "MICROPHONE_NOTE"]
