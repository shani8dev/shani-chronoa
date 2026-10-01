"""Settings window: real Adwaita pages, every sense reachable, no hardcoded theme.

Three things were wrong with the previous version, and only the first was
cosmetic.

**The consent model was unreachable.** Seventeen senses ship, each behind its
own `*-sense-enabled` key, and the settings window mentioned none of them -
zero. A user could install Chronoa, be told a sense is turned off, and have no
way to turn it on except `gsettings set` from a terminal. The switches here are
generated from the registry and the consent table rather than hand-listed, so a
sense added tomorrow appears without anyone editing this file, and one whose
key is missing from the installed schema is shown as ungrantable rather than
silently missing.

**It hardcoded a dark palette.** `window { background-color: #1a1a2e; }` and
hand-picked entry colours meant a user with a light GTK theme got a dark
dialog, and the window fought Adwaita rather than joining it. There are no
colours here now: the window uses whatever the desktop theme provides, which
is the only version that looks right on the light, dark and high-contrast
themes a user may already have chosen.

**It hand-rolled its rows out of `Gtk.Box`.** Every switch and entry was a
label plus a widget in a box, so there were no Adwaita group semantics, no
keyboard navigation the way a settings dialog is expected to behave, and no
accessibility roles. These are `Adw.SwitchRow` / `Adw.EntryRow` /
`Adw.PreferencesGroup`, which is what they should have been.

On top of that: a search entry that filters as you type, because twenty-odd
rows in one scroll is not navigable; API-key rows that can reveal what you
pasted instead of leaving you unable to check it; and a Models section showing
which model is *actually* in effect and why, via `models.py`, so the hardware
tier's guess is visible as a guess rather than presented as a decision.

## What this window is not

There is deliberately no "approve this" dialog here. The prompt that appears
while Chronoa is waiting on an answer is raised from the assistant's tool loop
and rendered by the main window's presenter, and a second dialog built in a
settings window would be a second place where Escape could mean something
different - which is the exact ambiguity the three-stage approval flow exists to
remove. What belongs here is the *policy view*: what the three answers are, what
"allow for this session" would actually permit for each capability, whether
anyone is present to answer at all, and what has already been answered this
session.
"""

import json
import logging
from datetime import datetime
from typing import NamedTuple, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # type: ignore

from shani_chronoa import capabilities, files, models, permissions, pipewire, tool_tracking
from shani_chronoa.config import _SENSE_CONSENT_KEYS
from shani_chronoa.senses import discover_senses
from shani_chronoa.tool_tracking import ORIGIN_UNATTENDED
from shani_chronoa.verification import Verdict

logger = logging.getLogger(__name__)

# What each sense is called, and said in a line, in the settings window.
#
# A sense's `description` is the LLM's *tool documentation*, not interface copy.
# Deriving the row from it gave raw module names as titles and subtitles
# ranging from one clause to a 300-character run-on, and reading the rendered
# window is the only reason that was noticed. So the visible text is written
# here and the schema description stays as the tooltip.
#
# An unlisted sense falls back to a title-cased name, and a test fails until it
# is listed: adding a sense should mean deciding what to call it, not
# inheriting its filename.
SENSE_LABELS = {
    "vision": (
        "Take and describe photos",
        "Take a photo when asked, if a camera is attached",
    ),
    "ocr": (
        "Read text in images",
        "Extract words from a picture you point it at",
    ),
    "filesystem": (
        "Files and folders",
        "Find, read and write files on this machine",
    ),
    "web": (
        "Web search",
        "Look things up online, and open pages",
    ),
    "memory": (
        "Remembering",
        "Keep facts you ask it to, on this machine only",
    ),
    "hearing": (
        "Transcribe speech",
        "Hear you through the microphone",
    ),
    "privilege": (
        "Who holds sensitive access",
        "Report which software can act as administrator",
    ),
    "display": (
        "Screen, brightness and monitors",
        "Report connected outputs, and set the backlight",
    ),
    "idle": (
        "Whether anyone is at this machine",
        "How long since anyone last used the keyboard or mouse",
    ),
    "accessibility": (
        "Which applications are open",
        "Which applications the desktop is showing, read over the "
        "accessibility bus",
    ),
    "bluetooth": (
        "Bluetooth",
        "Adapters, and whether rfkill has blocked them",
    ),
    "rfsense": (
        "Movement",
        "Sense motion from Wi-Fi signal strength, not what moved",
    ),
    "thermalgrid": (
        "Infrared array",
        "Thermal grid sensors on the I2C bus, if one is wired up",
    ),
    "modelfit": (
        "Which models fit",
        "What this machine can run, and what is installed",
    ),
    "power": (
        "Battery",
        "Charge, how worn the battery is, and whether a charger is plugged in",
    ),
    "storage": (
        "Disks",
        "Which drives this machine has, how big, and how worn the NVMe ones are",
    ),
    "cpu": (
        "Processor and load",
        "How busy the machine is, what memory is free, and the power governor",
    ),
    "gpu": (
        "Graphics",
        "Which GPU this machine has, its driver, and how much video memory",
    ),
    "security": (
        "Firmware security",
        "Secure Boot, TPM presence, lockdown mode and the security modules in effect",
    ),
    "devices": (
        "Connected hardware",
        "What is on the PCI and USB buses, and whether any device has no driver",
    ),
    "audio": (
        "Audio devices",
        "What the machine can play and record, and at what volume",
    ),
    "capture": (
        "Cameras and microphones",
        "Which capture devices exist, and what is holding them open",
    ),
    "hwmon": (
        "Temperatures, fans and power",
        "Every sensor the firmware exposes, and whether a fan has stopped",
    ),
    "filesystems": (
        "Filesystems and room",
        "What is mounted, and how much space and how many files remain",
    ),
    "services": (
        "Service health",
        "Which system services systemd has marked as failed, with the reason",
    ),
    "timebase": (
        "Clock trustworthiness",
        "Whether the system clock is synchronised, which every reminder and "
        "timer depends on",
    ),
    "usb": (
        "USB devices",
        "What is plugged into a port, with its class and the speed it negotiated",
    ),
    "coredumps": (
        "Crashes",
        "Programs that actually crashed, and whether crashes are recorded at all",
    ),
    "firewall": (
        "Firewall",
        "Whether packets are being filtered, not just whether a firewall is installed",
    ),
    "resources": (
        "Running out of things",
        "Zombies, swap in use, and file-descriptor pressure - none look busy",
    ),
    "faults": (
        "Recent errors",
        "What the system journal has complained about recently",
    ),
    "updates": (
        "Waiting updates",
        "Updates waiting, and how old the database behind that count is",
    ),
    "snapshots": (
        "Rollback points",
        "Btrfs rollback points and the subvolume currently booted",
    ),
    "sessions": (
        "Who is on this machine",
        "Logged-in sessions, and what runs as root outside the service tree",
    ),
    "network": (
        "Network and DNS",
        "Every interface, its link speed, whether it is wireless, and the resolvers",
    ),
    "printing": (
        "Printers and scanners",
        "Which printers are set up, and which scanners are plugged in",
    ),
    # The machine's own immutable and current facts. `git` is the exception and
    # the only one of this batch that is off by default - see its own entry.
    "hardware": (
        "Machine identity",
        "The make and model of this machine, from the firmware",
    ),
    "kernel": (
        "Kernel and boot",
        "Which kernel is running, and whether this is a container",
    ),
    "cgroup": (
        "Resource limits",
        "The memory, CPU and process limits this process is held to",
    ),
    "containers": (
        "Containers",
        "Which containers are running, and which were killed",
    ),
    "listeners": (
        "Listening ports",
        "Which programs are listening on this machine's ports",
    ),
    "stale": (
        "Outdated programs",
        "Programs still running a version that has been replaced",
    ),
    "boots": (
        "Boot history",
        "When it last booted, and whether it shut down cleanly",
    ),
    # Off by default, unlike the seven above: filenames, the branch and the
    # unpushed count are the user's work product. Same reasoning as
    # `accessibility` and `idle` - see `config._SENSE_CONSENT_KEYS`.
    "git": (
        "Git working trees",
        "Whether your working tree is clean, and how far it has drifted",
    ),
}


# The senses, grouped by what a person would be trying to do, and the order
# they are offered in.
#
# Seventeen switches in one alphabetical list is not a settings page, it is the
# registry printed out. Nothing in it answers the only question someone opening
# this has - "which of these do I turn on?" - and the honest answer depends on
# what they want the assistant to be able to do, not on where its modules sort.
#
# `SUGGESTED` is the everyday baseline, and deliberately excludes the senses
# that observe the room or the machine: those are opt-in on their own merits.
SENSE_CATEGORIES = [
    ("Talking to Chronoa",
     "Hearing you, and remembering what you asked it to keep",
     ["hearing", "memory"]),
    ("Looking at things",
     "Reading text out of pictures, and describing what a camera sees",
     ["vision", "ocr"]),
    ("Getting work done",
     "Files, folders, git and the web - what makes it able to act rather than "
     "only answer",
     ["filesystem", "git", "web"]),
    ("Is anything broken",
     "Failed services, errors the system has logged, updates that are waiting, "
     "and the running code and containers that have died - the ways a machine "
     "says it needs attention",
     ["services", "faults", "updates", "coredumps", "stale", "containers"]),
    ("Plugged in and running out",
     "What is attached by USB, and the ways a process runs out of something "
     "without the machine ever looking busy - including the limits it is held "
     "to rather than the memory it can see",
     ["usb", "resources", "cgroup"]),
    ("The screen",
     "Connected monitors, what mode they are in, the backlight, and which "
     "applications the desktop is currently showing",
     ["display", "accessibility", "idle"]),
    ("Network and wireless",
     "Interfaces and resolvers, audio devices, Bluetooth, and motion from Wi-Fi signal",
     ["network", "audio", "bluetooth", "rfsense"]),
    ("Disks and room",
     "What is mounted, how much of it is left, and whether the clock can be "
     "trusted - so a reminder means what it says",
     ["filesystems", "timebase", "snapshots"]),
    ("The machine itself",
     "Which machine this is, the kernel it is running, its processor load, "
     "battery, disks and their health, graphics, temperature, fans, arrays, and "
     "when it last booted",
     ["hardware", "kernel", "boots",
      "cpu", "power", "storage", "gpu", "hwmon", "thermalgrid"]),
    ("Security and privacy",
     "Firmware security, connected hardware, which software can act as "
     "administrator, what is already using your camera, which ports are open "
     "to the network, and who else is currently on this machine",
     ["security", "devices", "privilege", "capture", "sessions", "firewall",
      "listeners"]),
    ("Printers and scanners",
     "Whether anything is set up to print, and anything is there to scan",
     ["printing"]),
    ("Model capability",
     "What this machine can run, and whether the configured model fits",
     ["modelfit"]),
]

# The everyday baseline, offered as a named action. `memory` is already on by
# default, so the useful part is hearing plus the two that let the assistant do
# something. Nothing that watches the room or the machine is in here.
SUGGESTED = ["hearing", "filesystem", "web", "display"]


#: A believable value for each resource argument, so the sample prompt on
#: screen reads like something a user would recognise rather than a placeholder.
#: Read by argument *name*, so a new scoped tool gets one without an edit.
_EXAMPLE_TARGETS = {
    "path": "/home/you/notes.txt",
    "unit": "nginx.service",
    "device": "/dev/sdb1",
}


def _stt_model_is_ready(config) -> str:
    """The installed STT model filename, or "" when there is none."""
    from shani_chronoa import stt_provision
    try:
        path = stt_provision.model_path(
            stt_provision.resolve_key(config.whisper_model or "base")
        )
    except Exception:  # noqa: BLE001 - an unusable row is better than a crash
        return ""
    return path.name if path.is_file() else ""


# -- tool activity: the audit trail, made readable ----------------------------

#: A record's `verdict`, and the words it is allowed to have on screen.
#:
#: `None` is the one that gets mistreated. It means the call never reached
#: verification at all - a non-zero exit, or an exception - which is not the same
#: claim as "this did not take effect", and a panel that renders it as a failure
#: is telling the user something was checked and did not work when in fact
#: nothing checked it. `tests/test_tool_tracking.py` pins that distinction, and
#: pins the harder case behind it: two records whose `result` strings are
#: byte-identical with opposite verdicts.
#:
#: Keyed on `verification.Verdict`'s own values rather than on a second set of
#: literals, so a verdict added to that enum has to be answered here rather than
#: falling through to a confident-looking default.
_VERDICT_WORDS = {
    Verdict.VERIFIED.value: "verified to have taken effect",
    Verdict.FAILED.value: "verified not to have taken effect",
    Verdict.UNVERIFIED.value: "reported done, with nothing observing it",
    None: "never reached verification",
}

#: Rows built at most. A settings panel holding three hundred rows is a list
#: nobody scrolls, and the log behind this is unbounded by design, so the cap is
#: disclosed with `files.withheld_note` rather than applied silently - a list that
#: is quietly shortened reads as a complete one.
_TOOL_ROW_LIMIT = 40

#: Lines of the on-disk log read for history, and the ceiling on bytes read to
#: get them. That log is append-only and unbounded - 13.5 MB measured on the
#: machine this was written for - and this read happens on a GTK callback every
#: time the window is shown, so `read_text()` on it is not an option. Reading
#: forwards from a byte near the end costs the same whatever the file weighs, and
#: a log bigger than the cap is disclosed rather than quietly summarised.
_TOOL_LOG_TAIL_LINES = 400
_TOOL_LOG_TAIL_BYTES = 512 * 1024

#: Characters of any one free-text field in a row. `result` is the action's own
#: prose and `evidence` is a post-condition's own prose - either can be a whole
#: paragraph - so both are cut here and kept whole in the row's tooltip.
_TOOL_TEXT_CHARS = 90


def _verdict_words(verdict: Optional[str]) -> str:
    """The phrase for one verdict, including the ones we do not recognise."""
    if verdict in _VERDICT_WORDS:
        return _VERDICT_WORDS[verdict]
    return f"carries verdict {verdict!r}, which this panel does not recognise"


def _verdict_value(value) -> Optional[str]:
    """A verdict as a hashable str-or-None, whatever shape it arrived in.

    `None` stays `None` rather than becoming `""`, because `""` is a value the
    reader would be told to believe somebody wrote down.
    """
    if value is None:
        return None
    return value if isinstance(value, str) else repr(value)


def _text(value) -> str:
    """`value` as a string, or its `repr` when it is not one."""
    if value is None:
        return ""
    return value if isinstance(value, str) else repr(value)


def _number(value) -> Optional[float]:
    """`value` as a float, or None when it cannot be read as one.

    None rather than zero: this panel states durations, and "0 ms" for a
    duration nobody recorded is a measurement that was never taken.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _when(value) -> Optional[datetime]:
    """A recorded timestamp as a datetime, or None if it cannot be read.

    `None` rather than "now" - the one thing this panel must never invent is a
    time, because every ordering here and every "how long ago" follows from it. An
    offset-less ISO timestamp is read as local time, which is what it means.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _flatten(value) -> str:
    """`value` with every run of whitespace collapsed to one space.

    Newlines reach this window from a skill's own return value and from a
    post-condition's own message, and a raw newline in an `Adw.ActionRow`
    subtitle wraps at a width nobody chose. `split()` rather than `replace()`
    because evidence routinely carries an indented traceback, and half of that
    indentation is noise on a row.
    """
    return " ".join(_text(value).split())


def _clip(text: str, limit: int) -> str:
    """`text` cut to `limit` characters, marked when it was.

    A limit of zero or less means no cap, following `files.cap_list()`: every
    caller here passes a positive constant, and a zero that silently truncated
    would be the more dangerous of the two surprises. The mark is not
    decoration - it is what tells a reader the row is not showing everything, and
    the tooltip is where the rest is.
    """
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _args_digest(args, limit: int = _TOOL_TEXT_CHARS) -> str:
    """A call's arguments as one readable line, or "" when there are none.

    JSON for the same reason `ToolTracker.export_csv()` uses it: a logged
    argument can be bytes or a nested structure, and `str()` on one of those puts
    a Python repr in the middle of interface copy. `sort_keys` because two runs of
    the same call should read the same way.
    """
    if not args:
        return ""
    try:
        encoded = json.dumps(args, default=repr, sort_keys=True)
    except (TypeError, ValueError):
        encoded = repr(args)
    return _clip(_flatten(encoded), limit)


class _Call(NamedTuple):
    """One tool call as this panel needs it, from either source.

    The live tracker hands out `ToolCallRecord` objects and the log hands out
    plain dicts, so both are normalised into this here rather than in the
    renderer. One shape means one rendering path - a second path is where the two
    sources would quietly start disagreeing about the same event.
    """

    tool_name: str
    args: object
    result: object
    duration_ms: Optional[float]
    when: Optional[datetime]
    origin: str
    verdict: Optional[str]
    evidence: str
    #: True when this process recorded it. Only ever false for history, and the
    #: panel says so, because "shown here" and "done by the window you are
    #: looking at" are different claims.
    live: bool


class _LogTail(NamedTuple):
    """What could be read from the on-disk log, and what could not.

    Four separate fields rather than one value, because this panel has to tell a
    log that is not there from one it cannot read from one that is empty from one
    full of lines that are not records - and a user acts on those four
    differently. `readers of sessions.load` hit the same ambiguity: it swallows
    `OSError` at debug level and answers `[]`, so an empty list genuinely cannot
    say which of the two happened.
    """

    exists: bool
    size: int
    lines: list
    #: Started mid-file, so the log is larger than what was read.
    cut: bool
    #: "" unless the read itself failed.
    unreadable: str


def _log_tail(path) -> _LogTail:
    """The newest lines of the on-disk log, without reading all of it."""
    try:
        if not path.exists():
            return _LogTail(False, 0, [], False, "")
        size = path.stat().st_size
    except OSError as exc:
        # `exists()` can raise as well as answer False: a log directory the user
        # cannot search is not an absent log.
        return _LogTail(True, 0, [], False, str(exc))
    start = max(0, size - _TOOL_LOG_TAIL_BYTES)
    try:
        with path.open("rb") as handle:
            if start:
                handle.seek(start)
            blob = handle.read(_TOOL_LOG_TAIL_BYTES)
    except OSError as exc:
        return _LogTail(True, size, [], start > 0, str(exc))
    text = blob.decode("utf-8", "replace")
    if start:
        # Whatever was cut off the front is a fragment of a record, not a record.
        # Keeping it would put a guaranteed parse failure in front of every read
        # of a log larger than the cap, and this panel would then report a
        # corrupt log on a perfectly healthy one.
        text = text.split("\n", 1)[1] if "\n" in text else ""
    lines = list(files.iter_lines(text))
    if len(lines) > _TOOL_LOG_TAIL_LINES:
        lines = lines[-_TOOL_LOG_TAIL_LINES:]
    return _LogTail(True, size, lines, start > 0, "")


def _live_calls() -> tuple:
    """The in-memory ring, newest first, and why it is empty if it could not be read.

    `tools._TRACKER` and never a `ToolTracker()` of our own: the ring buffer is
    per-instance, so a fresh one answers `[]` however full the real one is, and
    this panel would then claim no action had ever been taken by a process that
    had just taken some. The singleton is the only object holding what
    `execute_tool()` recorded.
    """
    try:
        from shani_chronoa import tools
        records = list(tools._TRACKER.get_calls())
    except Exception as exc:  # noqa: BLE001 - an unreadable ring is not a blank panel
        logger.debug("tool activity: the live tracker is unreadable: %s", exc)
        return [], str(exc)
    return [
        _Call(
            tool_name=_text(record.tool_name),
            args=record.args,
            result=record.result,
            duration_ms=_number(record.duration_ms),
            when=record.timestamp if isinstance(record.timestamp, datetime) else None,
            origin=_text(record.origin),
            verdict=_verdict_value(record.verdict),
            evidence=_text(record.evidence),
            live=True,
        )
        for record in records
    ], ""


def _calls_from_log(lines) -> tuple:
    """Records from JSONL log lines, and how many lines were not records.

    One `to_log_dict()` per line, appended by a single write, so the only lines
    that ought to fail are a partial final line from a process killed mid-append.
    They are counted rather than dropped: a log that has stopped being readable
    and a log that is empty mean very different things, and this panel is the
    only place either is visible.

    A line that parses as JSON but is not a record is rejected for the same
    reason. `to_log_dict()` writes all eight keys every time, so a line without
    them was not written by `record_call()` - and reading the absent `verdict`
    as `None` would then display it as "never reached verification", which is a
    claim about an action rather than about a line of text. Two keys are checked
    rather than all eight so a writer that adds a field later is not turned into
    unreadable history.
    """
    calls, rejected = [], 0
    for line in lines:
        try:
            raw = json.loads(line)
        except ValueError:
            rejected += 1
            continue
        if not isinstance(raw, dict) or not {"tool_name", "verdict"} <= raw.keys():
            rejected += 1
            continue
        calls.append(_Call(
            tool_name=_text(raw.get("tool_name")),
            args=raw.get("args"),
            result=raw.get("result"),
            duration_ms=_number(raw.get("duration_ms")),
            when=_when(raw.get("timestamp")),
            origin=_text(raw.get("origin")),
            verdict=_verdict_value(raw.get("verdict")),
            evidence=_text(raw.get("evidence")),
            live=False,
        ))
    return calls, rejected


def _call_key(call: _Call) -> tuple:
    """What says "the same call, from two sources".

    Every `record_call()` appends to the ring *and* to the log, so the newest
    calls arrive twice and one event must be shown once - a duplicated row is
    indistinguishable from two actions that really happened, which is the one
    failure this panel must not have.

    Keyed on the timestamp, the tool and the duration rather than on the
    serialised line, because the log *caps* argument values: `_loggable()`
    replaces anything past 4096 characters of JSON with a stand-in, so a byte
    comparison would fail to match exactly the calls carrying a large payload -
    the ones most worth showing once. The deliberate cost is that two distinct
    calls to one tool, in the same microsecond, taking the same number of
    milliseconds collapse into a single row. That is rarer than a call with a
    large argument, so it is the trade this makes.
    """
    when = call.when.isoformat() if call.when else ""
    return (when, call.tool_name, call.duration_ms)


def _merge_calls(live, history) -> list:
    """Live and historical calls together, newest first, overlap removed.

    Live first on purpose: where the two sources disagree - and for a capped
    argument they can, because the ring holds the real value and the log holds a
    stand-in - the copy that keeps the argument is the one shown.
    """
    merged, seen = [], set()
    for call in list(live) + list(history):
        key = _call_key(call)
        if key in seen:
            continue
        seen.add(key)
        merged.append(call)
    # `.timestamp()` rather than the datetimes themselves: a hand-written log
    # line can carry an offset-less timestamp, and sorting that against the aware
    # timestamps the tracker writes raises TypeError - inside a GTK callback,
    # where nothing reports it and the panel just quietly stops updating.
    merged.sort(key=lambda c: (c.when is None, -(c.when.timestamp() if c.when else 0.0)))
    return merged


class SettingsWindow(Gtk.Window):
    """Every setting Chronoa has, in one searchable window."""

    def __init__(self, app) -> None:
        super().__init__(application=app, title="Chronoa Settings")
        self.app = app
        self.set_default_size(560, 720)
        if app.window:
            self.set_transient_for(app.window)
        # (widget, lowercase text to match against) for the search filter.
        self._searchable = []
        # Sense switches, so their real state can be re-derived after a change
        # instead of trusting what the user just clicked.
        self._sense_rows = {}
        self._build_ui()

    # -- construction --------------------------------------------------------

    def _build_ui(self) -> None:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=True)
        self._search = Gtk.SearchEntry(hexpand=True)
        self._search.set_placeholder_text("Search settings")
        self._search.connect("search-changed", self._on_search)
        header.pack_start(self._search)
        # Titlebar only. Adding it to `outer` as well is a second parent, and
        # GTK rejects that with "gtk_widget_get_parent (child) == NULL" - a
        # window can only ever have one.
        self.set_titlebar(header)

        page = Adw.PreferencesPage()
        self._build_senses(page)
        self._build_privacy(page)
        self._build_approvals(page)
        self._build_tool_activity(page)
        self._build_voice(page)
        self._build_models(page)
        self._build_system(page)

        scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scrolled.set_child(page)
        scrolled.set_vexpand(True)
        outer.append(scrolled)
        self.set_child(outer)

        self.connect("notify::visible", self._on_window_visible)

    def _group(self, page, title, description="") -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title=title, description=description or None, margin_top=14, margin_bottom=14
        )
        page.add(group)
        self._searchable.append((group, f"{title} {description}".lower()))
        group._needle_extra = []  # rows to reveal if only they match
        return group

    def _action_button(self, group, title, subtitle, action_name,
                       sensitive=True, tooltip=""):
        """A row that fires one of the app's own GActions.

        Same shape as `_switch`, so the button reaches the action through the
        identical path a keyboard shortcut or the D-Bus name would - not by
        writing the setting directly, which is how a control and its shortcut
        drift apart.
        """
        row = Adw.ActionRow(title=title, subtitle=subtitle or None)
        button = Gtk.Button(label="Download", valign=Gtk.Align.CENTER)
        button.set_sensitive(bool(sensitive))
        if tooltip:
            button.set_tooltip_text(tooltip)
            row.set_tooltip_text(tooltip)
        row.add_suffix(button)
        row.set_activatable_widget(button)
        button.connect("clicked", lambda _b: self._app_toggle(action_name, True))
        group.add(row)
        group._needle_extra.append((row, f"{title} {subtitle}".lower()))
        return row

    def _switch(self, group, title, subtitle, active, on_toggle, enabled=True, tooltip=""):
        row = Adw.SwitchRow(title=title, subtitle=subtitle or None, active=bool(active))
        if not enabled:
            row.set_subtitle("no consent key in the installed schema - this cannot be granted")
            row.set_sensitive(False)
        if tooltip:
            row.set_tooltip_text(tooltip)
        row.connect("notify::active", lambda r, *_: on_toggle(r.get_active()))
        group.add(row)
        group._needle_extra.append((row, f"{title} {subtitle}".lower()))
        return row

    def _entry(self, group, title, subtitle, value, on_changed, secret=False):
        row = Adw.EntryRow(title=title)
        if subtitle:
            row.set_tooltip_text(subtitle)
        entry = Gtk.Entry(text=str(value or ""), hexpand=True)
        if secret:
            entry.set_visibility(False)
        entry.connect("changed", lambda e: on_changed(e.get_text()))
        row.add_suffix(entry)
        if secret:
            reveal = Gtk.ToggleButton(icon_name="view-reveal-symbolic", valign=Gtk.Align.CENTER)
            reveal.add_css_class("flat")
            reveal.set_tooltip_text("Show this key")
            reveal.connect("toggled", lambda b: entry.set_visible(b.get_active()))
            row.add_suffix(reveal)
        group.add(row)
        group._needle_extra.append((row, f"{title}".lower()))
        return row

    def _info_row(self, group, title, subtitle):
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        group.add(row)
        group._needle_extra.append((row, f"{title} {subtitle}".lower()))
        return row

    # -- the consent surface, generated from the registry --------------------

    def _build_senses(self, page) -> None:
        """One switch per registered sense, carrying that sense's own description.

        Generated rather than hand-listed. A hand-written list of seventeen
        rows is a list that is wrong the moment a sense is added, and wrong
        silently - which is precisely what the previous version was.
        """
        try:
            registry = discover_senses()
        except Exception as exc:  # noqa: BLE001 - one broken sense must not blank the window
            group = self._group(page, "Senses", "could not be loaded")
            self._info_row(group, "Sense registry failed to load", str(exc))
            return

        config = self.app.config
        enabled_first = sorted(n for n in registry if config.sense_allowed(n))
        disabled = sorted(n for n in registry if not config.sense_allowed(n))

        self._build_suggested(page, registry, config, enabled_first)

        for cat_title, cat_desc, names in SENSE_CATEGORIES:
            present = [n for n in names if n in registry]
            if not present:
                continue
            ordered = [n for n in enabled_first if n in present] + [
                n for n in disabled if n in present
            ]
            on = sum(1 for n in ordered if config.sense_allowed(n))
            group = self._group(
                page, cat_title,
                f"{cat_desc}. {on} of {len(ordered)} on.",
            )
            for name in ordered:
                self._add_sense_row(group, registry, config, name)

    def _add_sense_row(self, group, registry, config, name: str) -> None:
        sense = registry[name]
        key = _SENSE_CONSENT_KEYS.get(name)
        description = sense.schema.get("function", {}).get("description", "")
        title, summary = SENSE_LABELS.get(
            name, (name.replace("_", " ").capitalize(), "")
        )
        subtitle = summary or (description.split(". ")[0].rstrip(".") if description else "")
        if sense.is_ambient():
            subtitle += f" - polled every {int(sense.poll_interval)}s"
        row = self._switch(
            group, title, subtitle,
            bool(key) and config.sense_allowed(name),
            lambda active, n=name: self._set_sense(n, active),
            enabled=bool(key),
            tooltip=description or None,
        )
        self._sense_rows[name] = row
        # The needle carries the module name and the schema text as well as the
        # title, so someone who knows this code can type "hwmon" and find the
        # row titled "Hardware sensors".
        group._needle_extra.append((row, f"{name} {description}".lower()))

    def _build_suggested(self, page, registry, config, enabled_first) -> None:
        """One action for the everyday case, and only after a confirmation.

        Turning sensing on is a consent decision, so a bulk version of it gets
        the same care an individual toggle does: the dialog names every sense
        that will change before anything is written, the change is additive
        only (it never turns anything *off*, so it cannot quietly revoke a
        choice), and the button is gone once there is nothing left to suggest.
        """
        missing = [n for n in SUGGESTED
                   if n in registry and n not in enabled_first]
        group = self._group(
            page, "Getting started",
            "Everything here is off by default. A sense that is off is never "
            "invoked at all - the check happens before anything is read.",
        )
        if not missing:
            self._info_row(
                group, "Suggested setup is on",
                ", ".join(SENSE_LABELS.get(n, (n, ""))[0] for n in SUGGESTED
                          if n in registry) + " are already enabled.",
            )
            return
        names = ", ".join(SENSE_LABELS.get(n, (n, ""))[0] for n in missing)
        row = Adw.ActionRow(
            title="Turn on the usual ones",
            subtitle=f"Would enable: {names}. Nothing else changes.",
            activatable=True,
        )
        row.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [f"Turn on the suggested senses: {names}"],
        )
        row.connect("activated", self._on_suggest_activated, missing)
        group.add(row)
        self._suggested_row = row

    def _on_suggest_activated(self, _row, names: list) -> None:
        dialog = Adw.AlertDialog(
            heading="Turn these on?",
            body=(
                "Chronoa will be able to use:\n\n"
                + "\n".join(f"  •  {SENSE_LABELS.get(n, (n, ''))[0]}" for n in names)
                + "\n\nNothing will be turned off, and you can change any of "
                "these afterwards."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("apply", "Turn on")
        dialog.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_suggest_response, names)
        dialog.present(self.get_root() or self)
        self._suggest_dialog = dialog

    def _on_suggest_response(self, dialog, response: str, names: list) -> None:
        if response != "apply":
            return
        for name in names:
            key = _SENSE_CONSENT_KEYS.get(name)
            if key:
                self.app.config.set(key, "true")
        self._refresh_sense_switches()

    def _set_sense(self, name: str, active: bool) -> None:
        key = _SENSE_CONSENT_KEYS.get(name)
        if not key:
            return
        self.app.config.set(key, "true" if active else "false")
        self._refresh_sense_switches()

    def _refresh_sense_switches(self) -> None:
        """Re-derive each switch from the config rather than from the click.

        A switch showing a state the system no longer agrees with is the most
        misleading thing this window can do, and a rejected write is exactly
        when that happens.
        """
        config = self.app.config
        for name, row in self._sense_rows.items():
            if _SENSE_CONSENT_KEYS.get(name):
                row.set_active(config.sense_allowed(name))

    # -- sections ------------------------------------------------------------

    def _build_privacy(self, page) -> None:
        config = self.app.config
        group = self._group(
            page, "Privacy and network",
            "Privacy mode is the master switch: with it on, Chronoa keeps speech, "
            "screen and sensed data on this machine and sends nothing to a cloud provider.",
        )
        self._switch(group, "Privacy mode (local only)", "Master switch for leaving this machine",
                     config.privacy_mode, lambda a: self._app_toggle("toggle-privacy", a))
        self._switch(
            group, "Cloud fallback when Ollama is unavailable",
            "Separate opt-in on purpose - this never turns on from one switch alone",
            config.cloud_fallback_enabled,
            lambda a: self._app_toggle("toggle-cloud-fallback", a))

        # The gate every actuator passes through. `triggers.py` refuses any
        # action without it and names it, so a user whose armed rules do
        # nothing had no way to turn it on except `gsettings set` from a
        # terminal - the same trap the seventeen sense switches were in.
        self._switch(
            group, "Let Chronoa act on this machine",
            "Off: Chronoa can answer but cannot run actions. Every trigger "
            "rule needs this, so an armed rule does nothing until it is on.",
            self._read_bool("input-control-enabled"),
            lambda a: self._set_bool("input-control-enabled", a),
            tooltip="triggers.py refuses every actuator while this is off",
        )

        # Every new destructive action needs a row here, or the key exists in
        # the schema and nowhere a person can reach it - which is the same trap
        # as a refusal that names a setting with no switch.
        for label, key, blurb, tip in (
            ("Let Chronoa delete files", "file-delete-enabled",
             "Off: Chronoa can read, search, create and move files but never "
             "delete one. Deletion here is permanent and does not use the trash.",
             "delete_file refuses while this is off"),
            ("Let Chronoa stop processes", "process-kill-enabled",
             "Off: Chronoa can list running processes but cannot stop one. A "
             "wrong process id can take down unsaved work.",
             "kill_process refuses while this is off"),
            ("Let Chronoa close windows", "window-close-enabled",
             "Off: Chronoa can list and focus windows but cannot ask one to "
             "close, which can discard unsaved work.",
             "close_window refuses while this is off"),
            ("Let Chronoa mount disks", "mount-control-enabled",
             "Off: Chronoa can list what is mounted but cannot mount or "
             "unmount anything. Mounting runs code from a device that was not "
             "there a moment ago.",
             "manage_mount refuses while this is off"),
            ("Let Chronoa change when the screen blanks", "idle-timeout-enabled",
             "Off: Chronoa can report the current idle timeout and whether the "
             "screen locks, but cannot change either. Its own key rather than "
             "sharing the lock skill's, because whether the machine locks itself "
             "is a standing policy decision, not a one-off action.",
             "set_screensaver refuses while this is off"),
            ("Let Chronoa hold the machine awake", "sleep-inhibit-enabled",
             "Off: Chronoa can report what is holding the machine awake, but cannot "
             "take a hold of its own. Every hold is bounded and lapses on its own, "
             "so turning this on cannot leave the machine unable to sleep.",
             "set_sleep_inhibit refuses while this is off"),
            ("Let Chronoa change the desktop look", "appearance-control-enabled",
             "Off: Chronoa can report whether the desktop is set to light or dark "
             "but cannot change it. Restyling a desktop unasked, mid-document, is "
             "disruptive in a way that reading it is not.",
             "set_theme refuses to change it while this is off"),
            ("Let Chronoa change the timezone", "timezone-control-enabled",
             "Off: Chronoa can report the current timezone and list what is "
             "available, but cannot change it. It is a system-wide change that "
             "moves every timestamp at once, and it needs root.",
             "set_timezone refuses while this is off"),
            ("Let Chronoa switch Bluetooth", "bluetooth-control-enabled",
             "Off: Chronoa can report which Bluetooth devices are paired and "
             "whether the adapter is on, but cannot turn it off. Separate from "
             "the bluetooth sense's own permission, because noticing a headset "
             "and agreeing to have it disconnected mid-call are different "
             "things.",
             "toggle_bluetooth refuses to switch while this is off"),
            ("Let Chronoa mute the microphone", "mic-control-enabled",
             "Off: Chronoa can report whether the microphone is muted but "
             "cannot change it. Output volume and mute need no such permission; "
             "this covers the input side, which is the more privacy-relevant of "
             "the two.",
             "set_mic_mute refuses while this is off"),
            ("Let Chronoa lock this session", "screen-lock-enabled",
             "Off: Chronoa cannot lock the screen. Its own permission rather "
             "than sharing input control, because it does not act on the "
             "interface - it ends the session's access to the machine.",
             "lock_screen refuses while this is off"),
            ("Let Chronoa empty the trash", "trash-empty-enabled",
             "Off: Chronoa can list what is in the trash but cannot empty it. "
             "Not the same permission as deleting files: the trash is already "
             "recoverable, and emptying it is what makes it not.",
             "empty_trash refuses while this is off"),
            ("Let Chronoa change system services", "service-control-enabled",
             "Off: Chronoa can list services and read their logs but cannot "
             "start, stop or restart one. These are root-owned units, and the "
             "wrong one can take down something another person is using.",
             "control_service refuses while this is off"),
            ("Let Chronoa edit many files at once", "bulk-edit-enabled",
             "Off: find-and-replace runs as a dry run and only lists what it "
             "would change. Writing one named file still works without this.",
             "find_and_replace refuses to write while this is off"),
            ("Let Chronoa change WiFi", "wifi-connect-enabled",
             "Off: Chronoa can list nearby networks but cannot join or leave "
             "one. Changing the connection changes what this machine can reach.",
             "connect_wifi refuses while this is off"),
            # The Help window names a switch for every gate it reports as off
            # ("Off. Switch on “…” in Settings to use this"), so a gate with no
            # row here is a promise the settings window does not keep - the user
            # is sent to a switch that does not exist and the skill stays
            # unreachable with no way to enable it. The gates skills and
            # triggers.py read had no row at all; the senses sharing this table
            # get theirs from the generated Senses group above, which is why a
            # grep of this file alone cannot find the missing ones.
            ("Let Chronoa edit your files", "file-edit-enabled",
             "Off: Chronoa can read and search your files but cannot write to "
             "them. Turning this on lets a tool call change your work.",
             "edit_file and undo_last_change refuse while this is off"),
            ("Let Chronoa keep a task list", "todo-list-enabled",
             "Off: Chronoa cannot keep a to-do list between turns.",
             "todo_list refuses while this is off"),
            ("Let Chronoa arm automatic rules", "trigger-control-enabled",
             "Off: Chronoa cannot create rules that act on their own, such as "
             "running something when a file changes.",
             "manage_triggers refuses while this is off"),
            ("Let Chronoa watch files for changes", "fswatch-sense-enabled",
             "Off: an automatic rule cannot trigger on a file being written.",
             "a fswatch trigger is refused while this is off"),
            ("Let Chronoa act on system failures", "failure-sense-enabled",
             "Off: an automatic rule cannot trigger on a service failing.",
             "a failure trigger is refused while this is off"),
            ("Let Chronoa watch stored deadlines", "expiry-sense-enabled",
             "Off: an automatic rule cannot trigger when a stored deadline "
             "passes.",
             "an expiry trigger is refused while this is off"),
            ("Let Chronoa act on container runs", "containerrun-sense-enabled",
             "Off: an automatic rule cannot trigger on a container starting or "
             "stopping.",
             "a container-run trigger is refused while this is off"),
            ("Let Chronoa watch system units", "unithealth-sense-enabled",
             "Off: an automatic rule cannot trigger on a systemd unit changing "
             "state.",
             "a unit-health trigger is refused while this is off"),
            ("Restrict tools to a seccomp sandbox", "sandbox-seccomp-enabled",
             "Off: tools run without a seccomp filter. Turning this on drops "
             "the syscalls a tool may make, at the cost of some tools refusing "
             "to run.",
             "the sandbox executor adds the seccomp filter when this is on"),
        ):
            self._switch(group, label, blurb, self._read_bool(key),
                         (lambda k: (lambda a: self._set_bool(k, a)))(key),
                         tooltip=tip)

        free = self._group(
            page, "Free cloud providers",
            "Optional. These work without a key at a lower rate limit; a key raises it. "
            "Applies when the cloud fallback next activates, not to a running session.",
        )
        for label, key in (
            ("LLM7 API key", "llm7-api-key"),
            ("Kilo Gateway API key", "kilo-api-key"),
            ("BlockRun API key", "blockrun-api-key"),
        ):
            self._entry(free, label, "", config.get(key, ""),
                        lambda text, k=key: config.set(k, text.strip()), secret=True)

        byok = self._group(
            page, "Cloud providers that require a key",
            "Anthropic, OpenAI, Google and Groq all rejected an unauthenticated request "
            "when tested live, so a key here is mandatory rather than optional. Tried ahead "
            "of the free providers when set.",
        )
        for label, key in (
            ("Anthropic (Claude) API key", "anthropic-api-key"),
            ("OpenAI API key", "openai-api-key"),
            ("Google Gemini API key", "google-api-key"),
            ("Groq API key", "groq-api-key"),
        ):
            self._entry(byok, label, "", config.get(key, ""),
                        lambda text, k=key: config.set(k, text.strip()), secret=True)

    def _build_voice(self, page) -> None:
        config, app = self.app.config, self.app
        group = self._group(page, "Voice", "How Chronoa listens and speaks.")
        self._switch(group, "Wake-word activation", "Start listening without being clicked",
                     app._wake_word_active, lambda a: self._app_toggle("toggle-wake-word", a))
        self._entry(group, "Wake-word model", "openWakeWord model file",
                    config.wake_word_model, lambda t: config.set("wake-word-model", t.strip()))
        self._switch(
            group, "Speak answers aloud",
            "Read replies and timers back through Piper. The notify skill also "
            "refuses while this is off, which is why it can look like a skill "
            "that does nothing.",
            config.get_bool("notification-enabled", True),
            lambda a: self._set_bool("notification-enabled", a),
        )
        self._switch(
            group, "Interrupt while replying (barge-in)",
            "No echo cancellation, so speaker output can self-interrupt; best with headphones",
            config.barge_in_vad_enabled, lambda a: self._app_toggle("toggle-barge-in-vad", a))
        model_ready = _stt_model_is_ready(config)
        self._action_button(
            group, "Download speech model now",
            "Fetch the speech model and verify it against a pinned SHA-256"
            if not model_ready else
            f"A speech model is already installed ({model_ready})",
            "download-speech-model",
            sensitive=not model_ready,
        )
        self._switch(
            group, "Download speech model on first use",
            "Fetches a whisper.cpp model from HuggingFace once, verifies it "
            "against a pinned SHA-256, and refuses it if it does not match; "
            "off by default",
            config.model_download_enabled,
            lambda a: self._app_toggle("toggle-model-download", a))
        self._entry(group, "Speech language (whisper.cpp)", "e.g. en, or auto",
                    config.language, lambda t: config.set("language", t.strip()))
        self._device_picker(group, "input", "Microphone",
                            config.audio_input_device)
        self._device_picker(group, "output", "Speakers",
                            config.audio_output_device)

    def _device_picker(self, group, kind: str, title: str, current: str) -> None:
        """Choose the microphone or the speakers, from the live graph.

        The app has always honoured these two keys and resolves them against
        the graph before every capture, because `pw-record` and `pw-play`
        *silently ignore* an unknown target and use the default device instead
        - a chosen headset that is unplugged looks honoured while the recording
        comes from the laptop. All of that machinery was unreachable, because
        there was no control here to set either key.
        """
        if not pipewire.is_available():
            row = Adw.ActionRow(
                title=title,
                subtitle="No PipeWire graph to read. Connect a device and reopen this window.",
            )
            group.add(row)
            return

        try:
            devices = pipewire.list_inputs() if kind == "input" else pipewire.list_outputs()
        except Exception as exc:  # noqa: BLE001 - a broken graph must not break settings
            group.add(Adw.ActionRow(title=title, subtitle=f"Could not read devices: {exc}"))
            return

        names = [d.name for d in devices]
        # A ComboRow displays whatever its model holds, and a PipeWire node name
        # is `alsa_input.pci-0000_00_1f.3.analog-stereo` - so the model carries
        # the readable label and the node name is kept behind it. A value list
        # of raw node names would be a settings page nobody can choose from.
        labels = ["System default"] + [d.label for d in devices]
        values = [""] + names

        # Gtk.StringList, not Adw.StringList: the latter is libadwaita 1.6 and
        # this system has 1.5, so naming it would be an AttributeError on the
        # machine this actually ships to.
        row = Adw.ComboRow(title=title, model=Gtk.StringList.new(labels))
        selected = names.index(current) + 1 if current in names else 0
        row.set_selected(selected)
        if current and current not in names:
            row.set_subtitle(
                f"Saved as '{current}', which is not connected - the system "
                "default is being used until it is"
            )
        else:
            row.set_subtitle("Which device this capture and playback uses")
        row._values = values
        row.connect("notify::selected", self._on_device_selected, kind)
        group.add(row)

    def _on_device_selected(self, row, kind: str) -> None:
        index = row.get_selected()
        values = getattr(row, "_values", None)
        value = values[index] if values and 0 <= index < len(values) else ""
        self.app.config.set(f"audio-{kind}-device", value)

    def _read_bool(self, key: str) -> bool:
        # get_bool(), not get() == "true": ChronoaConfig.get() is documented
        # for string keys and returns its default for a boolean one, so the
        # comparison would be False forever and this gate would render itself
        # permanently off while the setting was on.
        try:
            return self.app.config.get_bool(key, False)
        except Exception:  # noqa: BLE001 - an absent key is simply off
            return False

    def _set_bool(self, key: str, value: bool) -> None:
        self.app.config.set(key, "true" if value else "false")
        self._refresh_sense_switches()

    # -- the approval policy, as a view rather than a second dialog -----------

    def _build_approvals(self, page) -> None:
        """Show what an approval question is and what answering it would permit.

        Every row here is either read live from `permissions.py` or generated
        from the same constants the runtime prompt is composed from, so this
        page cannot describe a policy the app does not enforce. The only control
        is one that revokes - forgetting this session's answers - because a
        user who has over-trusted needs the un-grant and has no other way to
        reach it; there is deliberately no switch here that *grants* anything,
        since every grant in this app belongs to a consent-key row above and a
        second path to one would be two switches disagreeing.
        """
        self._approvals_page = page
        group = self._group(
            page, "Approvals",
            "What happens when Chronoa needs a permission it does not have. The "
            "question appears in the main window, not here.",
        )
        self._info_row(group, "What a question looks like",
                       self._example_request().question)

        asking = permissions.can_ask()
        self._info_row(
            group, "Who can answer",
            "Somebody is listening - Chronoa will ask before running a gated "
            "action."
            if asking else
            "Nobody is listening. A gated action is refused rather than asked "
            "about, and the refusal names the switch that would allow it. This "
            "is the state a headless run and a trigger rule that fires "
            "unprompted are always in.",
        )

        self._info_row(
            group, "How long a question waits",
            f"{int(permissions.DECISION_TIMEOUT_SECONDS)} seconds, then it is "
            "treated as no. An unanswered question is never an allow.",
        )

        for action, key in sorted(capabilities.GATED.items()):
            self._info_row(group, capabilities.tool_title(action),
                           self._scope_sentence(action, key))

        self._forget_group = self._group(
            page, "Answers given this session",
            "Grants and refusals recorded while Chronoa has been running. All of "
            "them die when it quits; none of them is written to disk.",
        )
        self._approvals_state = None
        self._render_session_answers()

    def _example_request(self) -> permissions.ApprovalRequest:
        """A real composed prompt, built from a capability that actually exists.

        Picked from `capabilities.GATED` and `tools._RESOURCE_ARGUMENT` rather
        than written out, so the sample on screen is the prompt a gated tool
        would really produce - including the fact that a scoped one enumerates
        one target while an unscoped one does not.
        """
        for action, key in sorted(capabilities.GATED.items()):
            argument = self._resource_argument(action)
            if argument is None:
                continue
            return permissions.approval_request(
                action, _EXAMPLE_TARGETS.get(argument, f"a {argument}"),
                key, capabilities.tool_title(action).lower())
        return permissions.approval_request(
            next(iter(sorted(capabilities.GATED)), "an action"), None,
            "a permission")

    def _scope_sentence(self, action: str, key: str) -> str:
        """What 'allow for this session' would cover, for one capability.

        Read from `permissions.always_patterns()` rather than described here, so
        the pattern named on screen is the pattern `add_rule()` writes.
        """
        patterns = permissions.always_patterns(action, None)
        wildcard = patterns[0][1]
        target = self._resource_argument(action)
        if target is None:
            return (f"Needs '{key}'. If you allow it for the session it covers "
                    f"every {action} until Chronoa quits, because this action "
                    f"names no specific target.")
        return (f"Needs '{key}'. If you allow it for the session it is written "
                f"against '{target}' - a {target} value with a wildcard in it "
                f"would widen the grant, and '{wildcard}' would widen it to "
                f"everything.")

    def _resource_argument(self, action: str):
        """Which argument names this tool's target, or None if it names none.

        Read from `tools._RESOURCE_ARGUMENT` because that is the table the
        dispatch path actually uses to build the grant, so a hand-kept copy here
        would drift from the permission it describes.
        """
        try:
            from shani_chronoa import tools
            return tools._RESOURCE_ARGUMENT.get(action)
        except Exception:  # noqa: BLE001 - an unreadable table is not a blank page
            return None

    def _session_state(self):
        return (tuple(permissions.rules()), permissions.can_ask())

    def _render_session_answers(self) -> None:
        group = self._forget_group
        for row in getattr(group, "_rows", []):
            group.remove(row)
        group._needle_extra = []
        rows = []
        rules = permissions.rules()
        said = permissions.reasons()

        if not rules:
            self._info_row(group, "Nothing has been allowed or refused yet",
                           "The first time Chronoa needs a permission, it will "
                           "ask rather than refuse.")
            rows.append(group._needle_extra[-1][0])
        for action, pattern, decision in rules:
            verdict = {
                permissions.Decision.ALLOW_ONCE: "allowed once",
                permissions.Decision.ALLOW_SESSION: "allowed for the session",
                permissions.Decision.DENY_ONCE: "refused",
                permissions.Decision.DENY_SESSION: "refused",
                permissions.Decision.CANCEL: "refused, and the turn was stopped",
            }.get(decision, decision)
            scope = f"on {pattern}" if pattern != "*" else "on any target"
            reason = said.get((action, pattern), "")
            row = self._info_row(
                group, f"{action} {scope} - {verdict}",
                f"You said: {reason}" if reason else
                f"Pattern on record: {action} / {pattern}",
            )
            rows.append(row)

        if rules:
            forget = Adw.ActionRow(
                title="Forget these answers",
                subtitle=f"Forgets the {len(rules)} answer(s) above so the next "
                         "time asks again. It does not change any switch.",
                activatable=True,
            )
            forget.update_property(
                [Gtk.AccessibleProperty.LABEL],
                ["Forget every permission answer given this session"],
            )
            forget.connect("activated", self._on_forget_answers, list(rules))
            group.add(forget)
            group._needle_extra.append(
                (forget, "forget answers revoke clear".lower()))
            rows.append(forget)

        group._rows = rows
        self._approvals_state = self._session_state()

    def _on_forget_answers(self, _row, rules: list) -> None:
        dialog = Adw.AlertDialog(
            heading="Forget these answers?",
            body=("Chronoa will ask again about:\n\n"
                  + "\n".join(
                      f"  •  {action} on {pattern}"
                      for action, pattern, _decision in rules)
                  + "\n\nNo switch changes. Nothing was written to disk, so this "
                    "only forgets what is in memory right now."),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("forget", "Forget them")
        dialog.set_response_appearance("forget",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_forget_response)
        dialog.present(self.get_root() or self)
        self._forget_dialog = dialog

    def _on_forget_response(self, _dialog, response: str) -> None:
        if response != "forget":
            return
        permissions.clear(session_only=True)
        self._render_session_answers()

    def _on_window_visible(self, window, _param) -> None:
        if not window.get_visible():
            return
        if self._session_state() != self._approvals_state:
            self._render_session_answers()
        if self._tool_activity_state() != self._tool_state:
            self._render_tool_activity()

    # -- tool activity, as a view of the audit trail ---------------------------

    def _build_tool_activity(self, page) -> None:
        """What Chronoa has actually run, and whether it took effect.

        The live session plus a capped tail of the on-disk log, rebuilt when
        either moved. There is deliberately no consent gate here and none is
        added: `privacy-mode` governs what leaves this machine, and this reads a
        file the app itself wrote on this machine, in this account, for the same
        purpose that file already exists. Whether a user may *read their own*
        history is a product decision, not a bug fix - and a gate here would be a
        second kind of refusal that the window's own docstring says it has none
        of.
        """
        self._tool_group = self._group(
            page, "Tool activity",
            "Every action Chronoa has run, newest first, led by whether it took "
            "effect rather than by what the action claimed. \"Never reached "
            "verification\" is not a failure verdict: the action exited non-zero "
            "or raised before anything could check it. \"Unattended\" means an "
            "armed rule fired it without being asked. Long text is shortened "
            "here; hover a row for all of it.",
        )
        self._tool_group.add_css_class("tool-activity-group")
        self._tool_state = None
        self._render_tool_activity()

    def _tool_activity_state(self):
        """Enough to notice a change, cheap enough for a visibility callback.

        No file is opened to ask: a 13.5 MB log read on every show/hide of a
        window is the thing this avoids. The tracker's count is `-1` when it
        cannot be read rather than `0`, because "the ring is empty" and "the ring
        is unreadable" need different copy and would otherwise compare equal and
        never re-render.
        """
        try:
            from shani_chronoa import tools
            live = len(tools._TRACKER.get_calls())
        except Exception:  # noqa: BLE001 - an unreadable ring is not a blank panel
            live = -1
        try:
            stat = tool_tracking.LOG_FILE.stat()
            log = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            log = None
        return (live, log)

    def _tool_activity_view(self):
        """(calls newest first, notes) for the panel.

        Every note here is a state this panel can genuinely *tell apart*. The
        tempting version - "no tool calls recorded" whenever the list is empty -
        is a confident wrong answer in three of the four ways it can be reached: no
        log, an unreadable log, and a log full of lines that are not records are
        three different problems and the user acts on them differently. The
        fourth, an empty ring, is genuinely ambiguous - the ring is also empty on
        a machine that ran actions in an earlier process - so that copy says what
        it cannot know rather than claiming nothing has happened.
        """
        live, live_problem = _live_calls()
        tail = _log_tail(tool_tracking.LOG_FILE)
        history, rejected = _calls_from_log(tail.lines)
        calls = _merge_calls(live, history)
        notes = []

        if live_problem:
            notes.append((
                "This process's own calls are not visible",
                f"The in-memory record could not be read ({live_problem}). "
                f"Everything below comes from the log file instead, so an action "
                f"this process takes while this window is open may not appear in "
                f"it.",
            ))
        if tail.unreadable:
            notes.append((
                "The log is there, and could not be read",
                f"{tail.unreadable}. That is a read failure rather than an "
                f"absence of calls, and nothing from earlier runs is shown.",
            ))
        elif not tail.exists:
            notes.append((
                "No log file on disk",
                f"There is nothing at {tool_tracking.LOG_FILE}, so this panel "
                f"cannot say whether any action has been taken: a run that ended "
                f"before this window opened leaves nothing behind to read."
                + ("" if live else " The log may never have been created, or it "
                   "may have been moved or deleted - neither is visible from "
                   "here."),
            ))
        elif not tail.lines:
            notes.append((
                "The log is there and empty",
                "Zero bytes. Either nothing has been recorded since it was "
                "created or it was truncated, and a zero-length file cannot tell "
                "those two apart.",
            ))
        elif not history:
            notes.append((
                "The log holds lines, and none is a record",
                f"{len(tail.lines)} line(s) read and not one parsed as a tool "
                f"call. A partial final line from a process killed mid-write is "
                f"the ordinary cause; this panel does not assume that.",
            ))

        if calls:
            shown, withheld = files.cap_list(calls, _TOOL_ROW_LIMIT)
            if withheld:
                notes.append((
                    "This is not the whole trail",
                    files.withheld_note(
                        "earlier call", withheld,
                        "The log file on disk holds all of them; this panel reads "
                        "and lists a bounded tail of it."),
                ))
            if tail.cut:
                notes.append((
                    "Only the end of the log was read",
                    f"The log weighs {files.human_size(tail.size)} and the newest "
                    f"{files.human_size(_TOOL_LOG_TAIL_BYTES)} of it were read, so "
                    f"older calls are neither shown nor counted.",
                ))
            if rejected:
                notes.append((
                    "Some log lines could not be read",
                    f"{rejected} of the last {len(tail.lines)} line(s) did not "
                    f"parse as a record and are not counted here.",
                ))
            if not live and not live_problem:
                notes.append((
                    "None of these were recorded by this process",
                    "Every row below was read from the log file, so none of it is "
                    "something this window just did.",
                ))
            calls = shown
        return calls, notes

    def _tool_call_row(self, group, call: _Call):
        """One call, led by its verdict rather than by its result.

        The order is the whole point and it is not a style preference. `result`
        is the action's own account of what it did, so for an action that did not
        take effect it reads "All done successfully." beside `verdict="failed"` -
        `tests/test_tool_tracking.py` pins two records with a byte-identical
        result and opposite verdicts for exactly that reason. Leading with the
        result would draw those two rows identically, which is the one thing an
        audit trail must not do.
        """
        verdict = _verdict_words(call.verdict)
        title = f"{_clip(_flatten(call.tool_name), 48)} - {verdict}"
        if call.origin == ORIGIN_UNATTENDED:
            # The origin field exists so a user-initiated action and an armed rule
            # firing unprompted are told apart; dropping it here would throw away
            # the only reason that field was added.
            title += f" ({ORIGIN_UNATTENDED})"

        bits = [call.when.astimezone().strftime("%H:%M:%S") if call.when
                else "time unreadable"]
        bits.append(f"{call.duration_ms:.0f} ms" if call.duration_ms is not None
                    else "duration unreadable")
        digest = _args_digest(call.args)
        if digest:
            bits.append(f"args {digest}")
        said = _clip(_flatten(call.result), _TOOL_TEXT_CHARS)
        if said:
            bits.append(f'said "{said}"')
        seen = _clip(_flatten(call.evidence), _TOOL_TEXT_CHARS)
        if seen:
            bits.append(f'observed "{seen}"')

        row = Adw.ActionRow(title="")
        # Before any text goes in. Adw parses a row's title and subtitle as Pango
        # markup, and what lands here is a skill's prose and a post-condition's
        # prose - verified on the installed libadwaita 1.5 that an unescaped `&`
        # raises a Gtk-WARNING and leaves the label rendering *empty*, so the row
        # silently loses its whole text rather than raising. Escaping was the
        # other option; not parsing is one call and cannot be half-done.
        row.set_use_markup(False)
        row.set_title(title)
        row.set_subtitle(" - ".join(bits))
        row.add_css_class("tool-call-row")
        row.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [f"{title}, {('run by an armed rule' if call.origin == ORIGIN_UNATTENDED else 'asked for')}"],
        )
        tip = " | ".join(filter(None, [
            f"args: {_args_digest(call.args, 0)}" if call.args else "",
            f"result: {_flatten(call.result)}" if _text(call.result) else "",
            f"evidence: {_flatten(call.evidence)}" if call.evidence else "",
            f"verdict on record: {call.verdict!r}",
            f"origin on record: {call.origin!r}",
        ]))
        if tip:
            row.set_tooltip_text(tip)
        group.add(row)
        # The needle carries the verdict wording, the argument names and the tool
        # name, so someone who remembers what they asked for, or the argument they
        # passed, finds the row by either.
        group._needle_extra.append((row, f"{title} {digest} {call.evidence}".lower()))
        return row

    def _render_tool_activity(self) -> None:
        """Rebuild the panel's rows, as `_render_session_answers` does."""
        group = self._tool_group
        for row in getattr(group, "_rows", []):
            group.remove(row)
        group._needle_extra = []
        calls, notes = self._tool_activity_view()
        rows = [self._info_row(group, title, subtitle) for title, subtitle in notes]
        for call in calls:
            rows.append(self._tool_call_row(group, call))
        group._rows = rows
        self._tool_state = self._tool_activity_state()

    def _build_models(self, page) -> None:
        config = self.app.config
        group = self._group(
            page, "Models",
            "A pin wins over the hardware tier. Blank means no pin, so the tier decides.",
        )
        self._entry(group, "Text and tool-calling model", "Blank = hardware tier decides",
                    config.model, lambda t: config.set("model", t.strip()))
        self._entry(group, "Vision model", "Blank = hardware tier decides",
                    config.vision_model, lambda t: config.set("vision-model", t.strip()))
        self._entry(group, "Whisper model (speech to text)", "Blank = hardware tier decides",
                    config.whisper_model, lambda t: config.set("whisper-model", t.strip()))
        self._entry(group, "Ollama host", "Where the local model server is",
                    config.ollama_host, lambda t: config.set("ollama-host", t.strip()))
        self._entry(group, "Piper voice", "TTS voice name",
                    config.piper_voice, lambda t: config.set("piper-voice", t.strip()))

        # What is actually in effect, and why. A tier that guesses should look
        # like a guess, not be presented as a decision.
        effective = self._group(
            page, "In effect right now",
            "Resolved by Chronoa's own resolver. A pin is your choice; anything "
            "else is the hardware tier's guess.",
        )
        for task in models.tasks():
            chosen = models.resolve(task, config=config)
            self._info_row(
                effective, task,
                f"{chosen} - {models.describe(task)}" if chosen
                else "unknown - no pin and no tier default",
            )
            effective._needle_extra[-1][0].set_tooltip_text(models.explain(task, config=config))

    def _build_system(self, page) -> None:
        config = self.app.config
        group = self._group(page, "System")
        self._switch(group, "Start on login", "Launch Chronoa when you log in",
                     config.auto_start, lambda a: self._app_toggle("toggle-auto-start", a))
        self._switch(group, "Debug logging", "Verbose logs, including tool calls",
                     config.debug_mode, lambda a: self._app_toggle("toggle-debug", a))

    # -- behaviour -----------------------------------------------------------

    def _app_toggle(self, action: str, active: bool) -> None:
        """Route a switch through the app's own action.

        The same path the keyboard shortcut uses, rather than a second one that
        can drift from it.
        """
        current = {
            "toggle-privacy": self.app.config.privacy_mode,
            "toggle-cloud-fallback": self.app.config.cloud_fallback_enabled,
            "toggle-wake-word": self.app._wake_word_active,
            "toggle-barge-in-vad": self.app.config.barge_in_vad_enabled,
            "toggle-model-download": self.app.config.model_download_enabled,
            "toggle-auto-start": self.app.config.auto_start,
            "toggle-debug": self.app.config.debug_mode,
        }.get(action)
        if current is not None and bool(current) != bool(active):
            self.app.activate_action(action, None)

    def _on_search(self, entry: Gtk.SearchEntry) -> None:
        """Filter groups and rows as you type.

        A group stays visible when it matches *or* when a row inside it does,
        so a search never leaves an empty heading on screen looking like a bug -
        and a matching row inside a non-matching group is revealed rather than
        hidden behind a heading that does not contain the needle.
        """
        needle = (entry.get_text() or "").strip().lower()
        for group, haystack in self._searchable:
            group_matches = not needle or needle in haystack
            row_hits = [
                (row, text) for row, text in group._needle_extra
                if needle and needle in text
            ]
            group.set_visible(bool(group_matches) or bool(row_hits))
            for row, _text in group._needle_extra:
                # A row is visible when the group matched outright, or when it
                # is itself the hit.
                row.set_visible(bool(group_matches) or bool(row_hits and (row, _text) in row_hits))
