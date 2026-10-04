"""The Machine surface: what this machine says about itself, asked once.

**Readings, not permissions.** The `senses` panel is where a permission is
granted or taken away; this panel grants nothing and has no switch. It calls
the machine-state senses - the ones that read hardware rather than the user's
world - exactly once each, when the panel is built, and shows what each one said
at that moment. Nothing here polls, schedules, threads or downloads, and nothing
here deposits a percept: a reading on this panel is shown to the person who
opened it and goes no further. Rebuild the panel to read again.

**Why once, and why the timestamp.** A sense that is polled in the background
and one that is asked on demand answer the same question, but only one of them
can say *when*. Every row therefore carries the moment it was read, so a panel
left open overnight is visibly a panel of yesterday's readings rather than a
live one that quietly stopped updating. A sense whose own percept is dated well
before this panel asked is called out, because a stale answer presented as a
current one is the failure this repo keeps paying for.

**The honest-unknown rule is the whole point of the panel.** A sense that
raises, that outruns its budget, that declines on permission, or that reports
`UNKNOWN` is rendered as its own state and never as a clean reading. That is
this package's standing rule - "a sense whose failure mode is a plausible-looking
wrong answer is worse than a sense that fails" - and it is why the states below
are six rather than two. The failure direction chosen here is always toward
saying less, never toward saying something clean:

- `not registered` - no module of that name exists in this build. Shown, because
  a name on a panel with nothing behind it reads as a bug in the sense.
- `not read - permission` - the sense's own consent key says no. The sense is
  **not invoked** at all, which is what "a sense that is off is never invoked at
  all" means, and the row names the key so the `senses` panel can be found.
- `not read - unverifiable` - the permission could not be checked at all (no
  settings loaded, or the check raised). Also not invoked: an unverified
  permission is not a granted one.
- `failed` - the sense raised. The exception's own type and message are shown,
  because "hwmon blew up" and "hwmon said it could not read" are different
  facts and this panel keeps them apart.
- `timed out` - the sense outran its per-sense budget and was interrupted. A
  budget is imposed here rather than left to a sense's own politeness because a
  panel that hangs on one wedged sense shows nothing about the other fifteen.
- `UNKNOWN` - the sense answered, and its answer says it could not determine the
  thing: it printed `UNKNOWN`, it declined, or it returned a percept with no
  source and no metadata. Its own words are still shown in full - the state
  describes how much to trust the text, and never replaces it.

The `UNKNOWN` test is a *declaration* list, not a parser: the sense's own word,
a refusal opening, or a percept with nothing to check it against. That last one
matters more than it looks. A bare string carries no provenance at all, and
"what it is showing it from" is the standard every other panel in this package
holds itself to, so a reading that cannot be attributed is shown as unattributed
rather than as fact. It errs toward hiding a number the sense did produce, and
that is the deliberate direction: the number is still on screen.

**A sense that needs something is named, not started.** `thermalgrid` reads an
I2C bus and needs root; `updates` needs pacman and a synced database; `services`
needs systemd. When one of those is missing the row says which thing was absent.
This panel starts no service, asks for no password and syncs no database.
"""

from __future__ import annotations

import contextlib
import logging
import re
import signal
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Tuple

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa.gui.surfaces import common  # noqa: E402
from shani_chronoa.senses import Percept, discover_senses  # noqa: E402

logger = logging.getLogger(__name__)

TITLE = "Machine"
ICON = "computer-symbolic"

#: The rows this panel shows, grouped, in the order it shows them. Read
#: top-to-bottom it is roughly "what keeps it alive, what it is made of, what
#: it is doing, what is plugged into it".
#:
#: `battery` is here on purpose even though no sense module carries that name:
#: the pack is read by `power`, and a list that quietly folded the two together
#: would show one row where the reader expects two. It renders as not-registered
#: and says where the battery actually is.
SENSE_GROUPS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("Power and battery", ("power", "battery")),
    ("Disks and filesystems", ("storage", "filesystems")),
    ("Processor, memory and pressure", ("cpu", "memory", "resources")),
    ("Sensors", ("thermalgrid", "hwmon")),
    ("Network and display", ("network", "display")),
    ("Devices and buses", ("devices", "usb")),
    ("Services, packages and snapshots", ("services", "updates", "snapshots")),
)

#: Every sense name this panel asks for, in group order. One row each, so one
#: answer per sense per build - a sense asked twice would show two readings of
#: the same moment and the difference between them would look like drift.
SENSE_NAMES: Tuple[str, ...] = tuple(
    name for _title, names in SENSE_GROUPS for name in names
)

#: Arguments for the senses that take some. Every one of these is read-only by
#: construction, which is why the panel can call them at all: `memory` without
#: an operation answers "Unknown operation ''", which is a refusal wearing the
#: shape of a reading, and its default operation is not something to leave to a
#: guess. `remember` and `forget` are deliberately not here.
SENSE_ARGUMENTS: Dict[str, Dict[str, Any]] = {
    "memory": {"operation": "history"},
}

#: What a sense needs before it can answer at all, said in its own row rather
#: than in a help page. Only shown when the row is not a clean reading, because
#: the note's whole job is to explain a missing answer.
SENSE_REQUIRES: Dict[str, str] = {
    "thermalgrid": "an I2C bus and root to read it; this panel asks for no password",
    "hwmon": "/sys/class/hwmon to be readable; there is no binary to start",
    "services": "systemd, queried with systemctl",
    "updates": "pacman and a synced local database; this panel never runs pacman -Sy",
    "snapshots": "the btrfs tool",
    "display": "the DRM connectors in sysfs; nothing to start",
    "network": "the kernel's net sysfs; ethtool is used when present",
}

#: One extra line for a name a reader could get wrong. Shown on the row whatever
#: the state: `battery` has no module behind it and would otherwise look like a
#: broken sense, and `memory` is the assistant's own durable memory rather than
#: this machine's RAM, which is the one name on this panel whose reading is not
#: about the hardware.
SENSE_EXTRA_NOTE: Dict[str, str] = {
    "battery": "the pack itself is read by the power sense in this group",
    "memory": "what the assistant has remembered on this machine, not free RAM",
}

#: Wall-clock budget for one sense. Not a politeness setting for the senses: it
#: is here so that one wedged sense costs one row rather than the whole panel,
#: and it is enforced with an interval timer rather than a watchdog thread so
#: that nothing outlives this call.
SENSE_TIMEOUT_SECONDS = 10.0

#: Longest reading shown in a row. A `devices` or `services` reading is a list
#: and can be hundreds of lines; the cut says how much is missing rather than
#: letting a truncated list read as the whole one.
MAX_READING_LINES = 14

#: The six states a row can be in. Deliberately more than "answer" and "no
#: answer": each of these is a different fact about the machine, and collapsing
#: any two of them together is how a panel ends up confidently wrong.
STATE_READING = "reading"
STATE_UNKNOWN = "unknown"
STATE_FAILED = "failed"
STATE_TIMEOUT = "timeout"
STATE_REFUSED = "refused"
STATE_ABSENT = "absent"
STATE_UNVERIFIABLE = "unverifiable"

#: Every state a row can be in, so a caller can tell "one of ours" from "a state
#: this panel invented".
STATES = (
    STATE_READING,
    STATE_UNKNOWN,
    STATE_FAILED,
    STATE_TIMEOUT,
    STATE_REFUSED,
    STATE_ABSENT,
    STATE_UNVERIFIABLE,
)

#: state -> the word a row leads with. The word is first in every case because
#: it is what a reader meets before the reason, and a row that buries it under a
#: sentence is a row whose state can be missed.
STATE_LINE = {
    STATE_READING: "reading",
    STATE_UNKNOWN: "UNKNOWN",
    STATE_FAILED: "failed",
    STATE_TIMEOUT: "timed out",
    STATE_REFUSED: "not read, permission is off",
    STATE_ABSENT: "not read, no such sense in this build",
    STATE_UNVERIFIABLE: "not read, permission could not be checked",
}

#: What a sense prints when it is telling you it could not determine the answer.
#: A declaration list, not a parser: matching a sense's prose properly would mean
#: reimplementing every sense's wording, and a missed phrase is a clean-looking
#: wrong answer. `UNKNOWN` is the word these senses already use for it.
_UNKNOWN_WORD = re.compile(r"\bUNKNOWN\b")
_DECLINE_OPENERS = ("Not ", "Nothing ", "Unknown", "UNKNOWN", "Cannot ", "Could not ")

#: Css class on every row, so a test can find rows without reading a list the
#: module stashed on itself. See the note in AGENTS.md about counting rows by
#: walking the widget that holds them.
ROW_CSS = "machine-reading"
WHEN_CSS = "machine-when"

_FOOTER = (
    "These readings are for this window only. Nothing is polled in the background "
    "from here, no percept is deposited, and no permission is changed: a sense "
    "whose permission is off is not invoked at all, and the Senses panel is where "
    "that permission is granted or taken away."
)


@dataclass(frozen=True)
class Reading:
    """One sense's answer, and how much of it can be believed."""

    name: str
    state: str
    text: str = ""
    detail: str = ""
    read_at: Optional[float] = None
    elapsed: Optional[float] = None

    @property
    def is_reading(self) -> bool:
        return self.state == STATE_READING

    def state_line(self) -> str:
        """The one line that says what kind of answer this is."""
        line = STATE_LINE.get(self.state, self.state)
        if self.detail:
            line = f"{line} - {self.detail}"
        if self.elapsed is not None:
            line = f"{line} ({self.elapsed * 1000:.0f} ms)"
        return line

    def when_text(self) -> str:
        """When it was read. An unrun sense has no timestamp, and says so."""
        if self.read_at is None:
            return "not read"
        stamp = time.strftime("%H:%M:%S", time.localtime(self.read_at))
        return f"read at {stamp}"


class _SenseTimeout(Exception):
    """Raised inside a sense's own `run` when its budget runs out."""


def _alarm(_signum, _frame) -> None:
    raise _SenseTimeout("the sense outran its reading budget")


@contextlib.contextmanager
def _budget(seconds: float) -> Iterator[None]:
    """Bound the block to `seconds` of wall clock, leaving nothing behind.

    `setitimer` rather than a thread on purpose: a watchdog thread would have to
    be joined, killed or leaked, and a leaked one outliving the panel is a
    process that does not exit. An interval timer is armed for this block and
    cancelled in the same `finally` that restores the previous handler, so a
    panel that raises leaves the process exactly as it found it.

    Available only on the main thread of a POSIX process; elsewhere the block
    runs unbounded and the row records that the budget was not enforced, because
    a timeout that silently did not happen is worse than no timeout.
    """
    if seconds <= 0 or not _can_alarm():
        # The caller adds "the budget was not enforced" to the row; a timeout
        # that silently did not happen is worse than no timeout at all.
        yield
        return
    previous = signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous)


def _can_alarm() -> bool:
    return (
        hasattr(signal, "SIGALRM")
        and hasattr(signal, "setitimer")
        and threading.current_thread() is threading.main_thread()
    )


def _permission(config: Any, name: str) -> Tuple[Optional[str], str]:
    """`("", "")` when the sense may run; otherwise `(state, why not)`.

    Failing closed, and the state is returned rather than left to be inferred
    from the wording - "no settings at all" and "the key says no" are different
    facts about the machine and this panel shows both. No exceptions either way:
    this is called once per sense per build, and a raise here would take the
    panel down rather than one row.
    """
    if config is None:
        return STATE_UNVERIFIABLE, "this window has no settings to check permission with"
    check = getattr(config, "sense_allowed", None)
    if not callable(check):
        return (
            STATE_UNVERIFIABLE,
            "these settings cannot answer which senses are permitted",
        )
    try:
        if check(name):
            return "", ""
    except Exception as exc:  # noqa: BLE001 - an unreadable gate is a closed gate
        return STATE_UNVERIFIABLE, f"checking the permission raised {type(exc).__name__}: {exc}"
    reason = ""
    explain = getattr(config, "sense_allowed_reason", None)
    if callable(explain):
        try:
            reason = str(explain(name) or "")
        except Exception:  # noqa: BLE001 - the fallback below is the honest one
            reason = ""
    return STATE_REFUSED, reason or "the sense is not permitted"


def _unknown_reason(percept: Percept) -> Tuple[bool, str]:
    """`(is it unknown, why)`.

    Checked against the percept's own words and against its provenance, in that
    order, and both are the sense telling the truth about itself rather than a
    guess made here. The provenance check is what catches the case no phrase list
    can: a sense that returns a bare string produces a percept with no `source`
    and no `metadata`, so nothing about the number can be checked against the
    thing that produced it.
    """
    text = str(getattr(percept, "content", "") or "")
    flat = text.strip()
    if not flat:
        return True, "the sense returned nothing at all"
    if _UNKNOWN_WORD.search(flat):
        return True, "the sense says its own answer is UNKNOWN"
    if flat.startswith(_DECLINE_OPENERS):
        return True, "the sense declined to answer"
    source = str(getattr(percept, "source", "") or "").strip()
    metadata = getattr(percept, "metadata", None)
    if not source and not metadata:
        return True, "the percept carries no source and no metadata to check it against"
    return False, ""


def _clip(text: Any, name: str) -> str:
    """The reading, cut at `MAX_READING_LINES` with the cut admitted."""
    lines = str(text or "").splitlines()
    if len(lines) <= MAX_READING_LINES:
        return "\n".join(lines).rstrip()
    hidden = len(lines) - MAX_READING_LINES
    kept = lines[:MAX_READING_LINES]
    kept.append(
        f"... {hidden} more line(s) not shown; shani-chronoa-sense run {name} "
        f"--no-store prints the whole reading"
    )
    return "\n".join(kept)


def _read_sense(
    name: str,
    sense: Any,
    config: Any,
    timeout: Optional[float] = None,
) -> Reading:
    """Ask one sense, once, and turn whatever comes back into a `Reading`.

    Every path out of here is a state, including the ones where the sense itself
    misbehaves. Nothing raises into the panel: a surface that raises takes the
    window with it, and the window is the product.

    `timeout` defaults to `SENSE_TIMEOUT_SECONDS` *by reading it here* rather
    than in the signature: a default bound at import is a constant wearing a
    parameter's clothes, and the one caller that wants a different budget (a
    test that must not wait ten seconds) could not have it.
    """
    if timeout is None:
        timeout = SENSE_TIMEOUT_SECONDS
    if sense is None:
        return Reading(
            name,
            STATE_ABSENT,
            detail=f"no sense module named {name!r} is registered in this build",
        )

    state, why_not = _permission(config, name)
    if state:
        return Reading(name, state, detail=why_not)

    started = time.time()
    arguments = dict(SENSE_ARGUMENTS.get(name, {}))
    try:
        with _budget(timeout):
            result = sense.run(arguments)
    except _SenseTimeout:
        ended = time.time()
        return Reading(
            name,
            STATE_TIMEOUT,
            detail=f"gave up after {timeout:g}s rather than keep the panel waiting",
            read_at=started,
            elapsed=ended - started,
        )
    except Exception as exc:  # noqa: BLE001 - a sense's own failure is its own state
        ended = time.time()
        logger.warning("machine surface: sense %r failed", name, exc_info=True)
        return Reading(
            name,
            STATE_FAILED,
            detail=f"{type(exc).__name__}: {exc}",
            read_at=started,
            elapsed=ended - started,
        )

    ended = time.time()
    # `run` may return a bare string; the loader wraps it in a Percept carrying
    # this sense's declared kind and lifetime. Same normalisation the CLI does,
    # so a reading here is the same reading `shani-chronoa-sense run` prints.
    percept = result if isinstance(result, Percept) else sense.to_percept(result)
    unknown, why = _unknown_reason(percept)
    if not unknown and timeout > 0 and not _can_alarm():
        why = "the reading budget was not enforced here, so nothing bounded this call"
    return Reading(
        name,
        STATE_UNKNOWN if unknown else STATE_READING,
        text=_clip(getattr(percept, "content", ""), name),
        detail=why or _staleness_note(percept, started),
        read_at=started,
        elapsed=ended - started,
    )


#: How far a percept's own timestamp may sit behind the moment this panel asked
#: for it before the row says so. Wide enough that a normal read is never
#: remarked on, narrow enough to catch a sense answering from cache.
STALE_AFTER_SECONDS = 120.0


def _staleness_note(percept: Percept, asked_at: float) -> str:
    """Say so when the sense dated its own observation well before we asked.

    `updates` reporting the age of its sync database is the honest version of
    this; a sense that hands back a percept claiming to be from last week is the
    same problem one layer up, and rendering it beside a fresh timestamp with
    nothing said would make the fresh one look like a claim about the reading.
    """
    created = getattr(percept, "created_at", None)
    if not isinstance(created, (int, float)) or isinstance(created, bool):
        return ""
    age = asked_at - float(created)
    if age < STALE_AFTER_SECONDS:
        return ""
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(created)))
    return f"the sense dated this observation {when}, {age / 60:.0f} min before it was asked for"


def _registry() -> Optional[Dict[str, Any]]:
    """The discovered senses, or None if the registry itself would not load.

    `discover_senses` skips a malformed sense with a log line rather than
    raising, so the failure worth surviving here is the package import itself -
    and then this panel has nothing to read and has to say that instead of
    raising into the window.
    """
    try:
        registry = discover_senses()
    except Exception:  # noqa: BLE001 - a hole in the panel, not a broken window
        logger.warning("machine surface: sense registry failed to load", exc_info=True)
        return None
    return registry if isinstance(registry, dict) else None


def _app_config(app: Any) -> Any:
    """`app.config`, or None - including when reading the attribute raises.

    `getattr` with a default does not help if the property itself throws, and a
    property that throws while a window is being built is a real state.
    """
    try:
        return getattr(app, "config", None)
    except Exception:  # noqa: BLE001 - reported as "no settings" by _permission
        logger.debug("machine surface: app.config raised", exc_info=True)
        return None


def _add_row(container: Gtk.Widget, row: Gtk.Widget) -> None:
    """Put `row` into a group built by `common.group()`.

    `Adw.PreferencesGroup` is a `Gtk.ListBox` and takes `add`; the plain `Gtk.Box`
    the non-Adw path returns takes `append`. One branch rather than a
    capabilities check, because the box is a box and the list box is not.
    """
    if isinstance(container, Gtk.Box):
        container.append(row)
    else:
        container.add(row)


def _when_label(reading: Reading) -> Gtk.Widget:
    label = Gtk.Label(xalign=1.0)
    label.add_css_class(WHEN_CSS)
    label.add_css_class("dim-label")
    label.set_valign(Gtk.Align.CENTER)
    label.set_text(reading.when_text())
    return label


def _reading_row(reading: Reading) -> Gtk.Widget:
    """One sense: its name, whether the answer can be believed, and the answer.

    The state's own line comes first and the sense's own text second, so a
    reader meets "UNKNOWN" before they meet the sentence that says why. The note
    about what a sense needs is added when the sense did not answer - "pacman is
    not installed" is only actionable next to the fact that the sense wanted
    pacman - and the note about what a name actually means is always there.
    """
    parts = [reading.state_line()]
    if reading.text:
        parts.append(reading.text)
    if not reading.is_reading:
        note = SENSE_REQUIRES.get(reading.name, "")
        if note:
            parts.append(note)
    extra = SENSE_EXTRA_NOTE.get(reading.name, "")
    if extra:
        parts.append(extra)
    widget = common.row(
        title=reading.name,
        subtitle="\n".join(parts),
        suffix=_when_label(reading),
    )
    widget.add_css_class(ROW_CSS)
    return widget


def _revealed(notice: Gtk.Widget) -> Gtk.Widget:
    """Make a banner actually visible.

    `Adw.Banner` starts hidden - `revealed` is `FALSE` until something sets it -
    so a caller that appends one and stops has put nothing on screen while every
    assertion about the banner's *text* still passes. Found by rendering this
    panel to a PNG and looking at it: the count of senses that could not answer
    was in the widget tree and absent from the pixels.

    The plain-GTK box the non-Adw path returns has no such property, so this is
    a no-op there rather than a special case.
    """
    setter = getattr(notice, "set_revealed", None)
    if callable(setter):
        setter(True)
    return notice


def _summary_label(readings: List[Reading]) -> Gtk.Widget:
    said = sum(1 for reading in readings if reading.is_reading)
    label = Gtk.Label(xalign=0.0, wrap=True)
    label.add_css_class("dim-label")
    stamp = time.strftime("%H:%M:%S")
    label.set_text(
        f"{said} of {len(readings)} machine-state senses answered, read once at "
        f"{stamp}. A sense that could not answer says so in its own row; it is "
        f"never shown as one that did."
    )
    return label


def build(app) -> Gtk.Widget:
    """The machine readings for `app` - anything with a `config` will do.

    Each sense is asked exactly once, in `SENSE_GROUPS` order, and its answer is
    kept. An app with no settings still builds every row, in the state that says
    the permission could not be checked - a panel that raised because settings
    were not loaded yet would take the window down for a missing permission.
    """
    page, set_content = common.surface(TITLE)
    config = _app_config(app)
    registry = _registry()

    if registry is None:
        set_content(common.scrolled(common.empty_state(
            ICON,
            "The sense registry could not be loaded",
            "Nothing on this panel was read. That is a failure to load the "
            "senses, not a machine with nothing to say.",
        )))
        return page
    if not registry:
        set_content(common.scrolled(common.empty_state(
            ICON,
            "No senses are registered",
            "No module under shani_chronoa/senses/ declares a SENSES list and "
            "~/.config/shani-chronoa/senses/ is empty, so there is nothing this "
            "panel could ask.",
        )))
        return page

    readings = [
        _read_sense(name, registry.get(name), config) for name in SENSE_NAMES
    ]

    body = common.page_body()
    body.set_margin_top(12)
    body.set_margin_bottom(12)
    body.set_margin_start(12)
    body.set_margin_end(12)

    unread = [reading for reading in readings if not reading.is_reading]
    if unread:
        body.append(_revealed(common.banner(
            f"{len(unread)} of {len(readings)} machine-state senses are not "
            f"showing a reading. Each row names which and why."
        )))
    body.append(_summary_label(readings))

    for title, names in SENSE_GROUPS:
        group = common.group(title)
        for reading in readings:
            if reading.name in names:
                _add_row(group, _reading_row(reading))
        body.append(group)

    body.append(common.monospace("shani-chronoa-sense list"))
    footer = Gtk.Label(xalign=0.0, wrap=True)
    footer.add_css_class("dim-label")
    footer.set_text(_FOOTER)
    body.append(footer)

    set_content(common.scrolled(body))
    return page


__all__ = ["TITLE", "ICON", "build"]