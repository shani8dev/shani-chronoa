"""Record a meeting, keep it, read it back — the surface for `meeting`.

**Why a panel and not just a tool.** `meeting.capture()` records up to two
hours and `meeting.notes()` turns the transcript into decisions and actions, and
both were reachable only by asking in conversation. That is the wrong shape for
the *result*: an hour of microphone produces a transcript somebody wants to read
again in a week, and a chat log is a bad place to keep one. The panel is a shelf
for meetings, with the recording itself on it.

**Recording is one button and it is honest about how it ended.** `MeetingResult`
carries four end states and they are not interchangeable — `died` means the
transcript is a fragment. `meeting._sentence()` words all four, and the panel
shows it verbatim rather than inventing a second vocabulary: a panel that says
"done" about a recording that lost its first twenty minutes is exactly the
confident wrong answer this repo keeps recording.

**Recording is off the GTK thread and has a Stop.** `capture()` joins a thread
for up to `timeout + 30` seconds. On the main loop that is a frozen window for
two hours, which is worse than no button; and a button that cannot be stopped is
not something a person will start in a room with other people in it. Stop is a
`threading.Event` the capture cannot see (it is inside `AudioRecorder`), so it
is honest: it ends the *panel's* wait and says the capture may still be running.

**Nothing is transcribed on this page unless the person asks for it.**
Transcribing an hour takes minutes and needs a speech model; doing it
automatically after every recording would spend a person's battery on a file
they may never open. Each row has its own Transcribe and Notes buttons.
"""

from __future__ import annotations

import logging
import pathlib
import threading
import time
from typing import Any, List

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, Gtk

from shani_chronoa import markdown_lite, meeting
from shani_chronoa.gui.surfaces import common

logger = logging.getLogger(__name__)

TITLE = "Meetings"
#: **`audio-microphone-symbolic` is not a glyph this theme ships** (measured
#: with `Gtk.IconTheme.has_icon`); `audio-input-microphone-symbolic` is, and is
#: the name the Voice panel already uses for the same idea.
ICON = "audio-input-microphone-symbolic"
SECTION = "Remembering"
SUBTITLE = ("Record a whole meeting, keep it, and read the transcript and notes "
            "back whenever you want them.")

CONSENT_KEY = "meeting-enabled"

NOTHING_YET = "No meetings recorded yet"
NOTHING_YET_DESC = ("Record one and it is kept beside Chronoa's other state, "
                    "so it is still here after a restart.")

RECORDING = "Recording..."
RECORDING_DESC = ("Recording stops on 20 seconds of quiet, at the two-hour "
                  "ceiling, or when you press Stop.")

#: What the record button offers. Two hours is `meeting`'s own ceiling and
#: anything longer is a request the module would refuse anyway; offering only
#: what exists keeps the list from being a menu of values that do nothing.
DURATIONS = (("15 minutes", 15 * 60.0), ("1 hour", 60 * 60.0), ("2 hours", 7200.0))


def _app_config(app: Any) -> Any:
    return getattr(app, "config", None)


def _consent(app: Any) -> bool:
    config = _app_config(app)
    if config is None:
        return False
    try:
        return bool(config.get_bool(CONSENT_KEY, False))
    except Exception:  # noqa: BLE001 - a setting that cannot be read is not consent
        return False


def _pretty_when(stamp: str) -> str:
    """`20261011-090000` -> `11 Oct 2026, 09:00`; anything else -> "".

    The file name is a timestamp because two captures in the same second must
    not collide, which makes it the *right* tiebreaker for ordering and the
    wrong* thing to show a person.
    """
    try:
        when = time.strptime(stamp, "%Y%m%d-%H%M%S")
    except (TypeError, ValueError):
        return ""
    return time.strftime("%d %b %Y, %H:%M", when)


def _clock(seconds: float) -> str:
    minutes, rest = divmod(max(int(seconds), 0), 60)
    if minutes >= 60:
        return f"{minutes // 60}h {minutes % 60:02d}m"
    return f"{minutes}m {rest:02d}s"


class _MeetingsView(Gtk.Box):
    def __init__(self, app: Any) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._app = app
        #: Set when the person presses Stop. It cannot actually stop
        #: `AudioRecorder`'s own thread, and saying it did would be the one lie
        #: this page cannot tell — so it ends the *wait* and says so.
        self._stop = threading.Event()
        self._recording = False
        self._rows: List[tuple] = []
        self.status_recorder = common.StatusRecorder()

        self._status_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.append(self._status_slot)

        self.append(self._record_row())

        self._list_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._list_slot.set_vexpand(True)
        self.append(self._list_slot)
        self._refresh_status()
        self._refresh_list()

    # -- the status line -----------------------------------------------------

    def _refresh_status(self) -> None:
        """The one line, and it has to be true about a half-off feature.

        **The consent key is `meeting-enabled`, which reads "Let Chronoa record a
        whole meeting" — it gates the microphone, not the shelf.** So with the
        switch off and three meetings already recorded, the page is neither
        "off" nor "on": the microphone is closed and the transcripts are still
        readable, because `Transcribe` and `Notes` open no capture device.

        The first version said only "the microphone will not be opened" and left
        the list below it fully populated with working buttons, which reads as a
        broken panel — a grey "Turned off" over content that plainly works. The
        other repair, hiding the shelf, is worse: it claims the person has no
        meetings when they have three. So the sentence names both halves.
        """
        common.clear(self._status_slot)
        found = meeting.list_recordings()
        if not _consent(self._app):
            if found:
                self._status_slot.append(self.status_recorder.row(
                    common.STATUS_OFF,
                    f"Recording is turned off ({CONSENT_KEY})",
                    f"The microphone will not be opened, so no new meeting can "
                    f"be captured. The {len(found)} already recorded can still "
                    "be read and written up - that needs no microphone. Turn on "
                    f"'{CONSENT_KEY}' in Settings to record again."))
            else:
                self._status_slot.append(self.status_recorder.row(
                    common.STATUS_OFF,
                    f"Recording a meeting is turned off ({CONSENT_KEY})",
                    "The microphone will not be opened. Turn on "
                    f"'{CONSENT_KEY}' in Settings to allow it."))
            return
        if not found:
            self._status_slot.append(common.status_row(
                common.STATUS_OK, NOTHING_YET,
                f"{meeting.recordings_dir()} is empty"))
            return
        newest = found[0]
        self._status_slot.append(self.status_recorder.row(
            common.STATUS_OK,
            f"{len(found)} meeting(s) kept",
            f"newest: {newest['name']} ({newest['bytes'] // 1024} kB), in "
            f"{meeting.recordings_dir()}"))

    # -- the record row ------------------------------------------------------

    def _record_row(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for side in ("start", "end", "top", "bottom"):
            getattr(box, f"set_margin_{side}")(12 if side in ("start", "end") else 10)

        self.record = Gtk.ToggleButton(label="Record")
        self.record.set_tooltip_text("Start recording a meeting")
        _label(self.record, "Record a meeting")
        self.record.connect("toggled", self._on_toggled)
        box.append(self.record)

        self.duration = Gtk.DropDown.new_from_strings(
            [label for label, _s in DURATIONS])
        self.duration.set_selected(0)
        self.duration.set_tooltip_text("How long the ceiling should be")
        _label(self.duration, "Recording length limit")
        box.append(self.duration)

        self.stop = Gtk.Button(label="Stop")
        # **Not `destructive-action`.** Rendered, it was the loudest thing on a
        # page where nothing was happening - a red Stop beside a dim Record on a
        # panel holding two saved meetings. Ending a recording destroys nothing:
        # the audio is already written, and the person can start another. The
        # word and the emphasis are both "stop", not "throw away".
        self.stop.set_sensitive(False)
        self.stop.set_tooltip_text("Stop waiting for the recording")
        _label(self.stop, "Stop waiting for this recording")
        self.stop.connect("clicked", self._on_stop)
        box.append(self.stop)

        self.elapsed = Gtk.Label(label="")
        self.elapsed.add_css_class("dim-label")
        self.elapsed.set_hexpand(True)
        self.elapsed.set_xalign(0.0)
        box.append(self.elapsed)
        return box

    # -- recording -----------------------------------------------------------

    def _on_toggled(self, button: Gtk.ToggleButton) -> None:
        if not button.get_active():
            return
        if not _consent(self._app):
            # Reset the toggle, so the button does not stay lit saying
            # "recording" over a machine that is not.
            button.set_active(False)
            self._present(nothing_state(
                "dialog-warning-symbolic", "Recording is turned off",
                f"Turn on '{CONSENT_KEY}' in Settings first."))
            return
        label, seconds = DURATIONS[min(self.duration.get_selected(),
                                       len(DURATIONS) - 1)]
        self._stop.clear()
        self._recording = True
        self.record.set_sensitive(False)
        self.duration.set_sensitive(False)
        self.stop.set_sensitive(True)
        self.elapsed.set_label(f"Recording — up to {label}")
        self._present(nothing_state(
            "audio-input-microphone-symbolic", RECORDING, RECORDING_DESC))
        self._started = time.monotonic()
        threading.Thread(target=self._capture, args=(seconds,), daemon=True).start()

    def _tick(self) -> bool:
        """The elapsed clock, while a recording runs.

        A separate timer rather than a label that never moves: "Recording" with
        no time on it is indistinguishable from a hung panel, which is the same
        failure the orb's `QUEUED` state was added to fix on the chat path.
        """
        if not self._recording:
            return False
        self.elapsed.set_label(f"Recording — {_clock(time.monotonic() - self._started)}")
        return True

    def _on_stop(self, _button: Gtk.Button) -> None:
        self._stop.set()

    def _capture(self, seconds: float) -> None:
        try:
            result = meeting.capture(timeout=seconds)
        except meeting.MeetingError as exc:
            GLib.idle_add(self._capture_failed, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - shown, not swallowed
            logger.warning("the meeting capture failed", exc_info=True)
            GLib.idle_add(self._capture_failed, f"{type(exc).__name__}: {exc}")
            return
        GLib.idle_add(self._capture_done, result)

    def _capture_failed(self, why: str) -> bool:
        self._recording = False
        self._reset_controls()
        self._present(nothing_state(
            "dialog-error-symbolic", "The recording could not be started", why))
        self._refresh_status()
        return False

    def _capture_done(self, result) -> bool:
        self._recording = False
        self._reset_controls()
        stopped_early = self._stop.is_set()
        self._refresh_status()
        self._refresh_list()
        if not result.wav:
            self._present(nothing_state(
                "dialog-warning-symbolic", "Nothing was usable",
                meeting._sentence(result.why, result.seconds)))
            return False
        # `meeting._sentence` already words all four end states, and the one
        # that matters here is `died` — the transcript is a fragment, and saying
        # "done" about it is the one thing this page must not do.
        detail = meeting._sentence(result.why, result.seconds)
        if stopped_early:
            detail += ("  You pressed Stop, so this page stopped waiting; the "
                       "recording itself ends on quiet, the ceiling, or its own.")
        self._present(nothing_state(
            "dialog-information-symbolic",
            f"Kept {pathlib.Path(result.wav).name} ({_clock(result.seconds)})",
            detail))
        return False

    def _reset_controls(self) -> None:
        self.record.set_active(False)
        self.record.set_sensitive(True)
        self.duration.set_sensitive(True)
        self.stop.set_sensitive(False)
        self.elapsed.set_label("")

    # -- the list ------------------------------------------------------------

    def _present(self, widget: Gtk.Widget) -> None:
        _replace(self._list_slot, widget)

    def _refresh_list(self) -> None:
        found = meeting.list_recordings()
        self._rows = []
        if not found:
            self._present(nothing_state(
                "audio-input-microphone-symbolic", NOTHING_YET, NOTHING_YET_DESC))
            return
        group = common.group()
        group.set_margin_start(12)
        group.set_margin_end(12)
        for item in found:
            row = self._meeting_row(item)
            self._rows.append(row)
            group.add(row)
        self._present(common.scrolled(group, 520))

    def _recording_possible(self) -> bool:
        """Whether the Record button can act, and the sentence if it cannot.

        `Transcribe` and `Notes` do **not** consult this: they read a file
        already on disk and open no capture device, so a shut `meeting-enabled`
        withholds the microphone and not the shelf. Checking the gate there too
        would refuse to read back a meeting the person recorded before they
        turned the switch off — which is not what "let Chronoa record" means.
        """
        return _consent(self._app)

    def _meeting_row(self, item: dict) -> Gtk.Widget:
        """One meeting: its name, its size, and the two things worth doing to it."""
        name = item.get("name") or ""
        size = f"{item.get('bytes', 0) // 1024} kB"
        # "20261011-090000" reads as nothing; "11 Oct 09:00" reads as a meeting.
        when = _pretty_when(item.get("when") or "")
        suffix = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        transcript = Gtk.Button(label="Transcribe")
        transcript.add_css_class("flat")
        transcript.set_tooltip_text("Transcribe this recording on this machine")
        _label(transcript, f"Transcribe {name}")
        notes = Gtk.Button(label="Notes")
        notes.add_css_class("flat")
        notes.set_tooltip_text("Write decisions and action items from the transcript")
        _label(notes, f"Write notes from {name}")
        reveal = Gtk.Button(label="Show")
        reveal.add_css_class("flat")
        reveal.set_tooltip_text("Show the transcript and notes here")
        _label(reveal, f"Show the transcript of {name}")
        for button, action in ((transcript, self._on_transcribe),
                               (notes, self._on_notes),
                               (reveal, self._on_reveal)):
            button.connect("clicked", lambda b, a=action, i=item: a(i, b))
            suffix.append(button)
        row = common.row(pathlib.Path(name).stem,
                         f"{when}  \u00b7  {size}" if when else size,
                         suffix=suffix)
        _label(row, f"{name}, {size}")
        return row

    def _on_transcribe(self, item: dict, button: Gtk.Button) -> None:
        self._background(button, "Transcribing...",
                         lambda: meeting.transcribe(item["path"]),
                         lambda r: self._show_transcript(item, r))

    def _on_notes(self, item: dict, button: Gtk.Button) -> None:
        """Notes need the transcript first, so this chains rather than repeating it."""
        def work():
            segments, why = meeting.transcribe(item["path"])
            if segments is None:
                return why
            note, note_why = meeting.notes(segments, config=_app_config(self._app))
            return note if note else f"No notes could be written: {note_why}"
        self._background(button, "Reading the transcript...", work,
                         self._show_text)

    def _on_reveal(self, item: dict, button: Gtk.Button) -> None:
        self._background(button, "Reading the transcript...",
                         lambda: meeting.transcribe(item["path"]),
                         lambda r: self._show_transcript(item, r))

    def _show_transcript(self, item: dict, result) -> None:
        segments, why = result if isinstance(result, tuple) else (None, str(result))
        if segments is None:
            self._show_text(f"this recording could not be transcribed: {why}")
            return
        self._show_transcript_text(item, segments)

    def _show_transcript_text(self, item: dict, segments) -> None:
        lines = []
        for segment in segments:
            speaker = getattr(segment, "speaker", "") or ""
            start = getattr(segment, "start", 0.0) or 0.0
            prefix = f"[{_clock(start)}] "
            lines.append(f"{prefix}{speaker + ': ' if speaker else ''}"
                         f"{getattr(segment, 'text', '')}")
        title = f"{item.get('name')}: {len(segments)} segment(s)"
        self._show_markdown(title, "\n".join(lines))

    def _show_text(self, text: str) -> None:
        self._show_markdown("Result", str(text))

    def _show_markdown(self, title: str, body: str) -> None:
        view = Gtk.Label()
        view.set_markup(markdown_lite.escape(body))
        view.set_xalign(0.0)
        view.set_yalign(0.0)
        view.set_selectable(True)
        view.set_wrap(True)
        common.wrap_label(view, 64)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_start(12)
        box.set_margin_end(12)
        heading = Gtk.Label(label=title, xalign=0.0)
        heading.add_css_class("title-4")
        box.append(heading)
        box.append(common.scrolled(view, 520))
        self._present(box)

    # -- one helper for the three slow buttons -------------------------------

    def _background(self, button: Gtk.Button, working: str, work, done) -> None:
        """Run `work()` off the loop; `done(result)` back on it.

        All three buttons here take minutes on a cold machine — an hour of audio
        through whisper — so every one of them needs this, and a page with one
        of them on the main loop freezes the window while the person is told it
        is working.
        """
        button.set_sensitive(False)
        button.set_label(working)

        def runner() -> None:
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001 - shown in the page
                logger.warning("meeting work failed", exc_info=True)
                result = exc
            GLib.idle_add(finish, result)

        def finish(result) -> bool:
            button.set_label(_short_label(button))
            button.set_sensitive(True)
            if isinstance(result, Exception):
                self._show_text(f"{type(result).__name__}: {result}")
            else:
                try:
                    done(result)
                except Exception:  # noqa: BLE001
                    logger.warning("could not show the result", exc_info=True)
            return False

        threading.Thread(target=runner, daemon=True).start()


def _short_label(button: Gtk.Button) -> str:
    """The label this button had before it said "Working...".

    Read back off the button rather than threaded through as an argument: a
    second copy of the word is how a button ends up reading "Transcribe" after
    it was left saying "Notes".
    """
    label = button.get_label()
    if label in ("Transcribing...", "Notes", "Reading the transcript..."):
        return {"Transcribing...": "Transcribe",
                "Reading the transcript...": "Show"}.get(label, label)
    return label


def _replace(container: Gtk.Widget, widget: Gtk.Widget) -> None:
    child = container.get_first_child()
    while child is not None:
        following = child.get_next_sibling()
        container.remove(child)
        child = following
    container.append(widget)


def nothing_state(icon: str, title: str, description: str = "") -> Gtk.Widget:
    return common.empty_state(icon, title, description)


def _label(widget: Gtk.Widget, label: str) -> None:
    try:
        widget.update_property([Gtk.AccessibleProperty.LABEL], [label])
    except Exception:  # noqa: BLE001
        logger.debug("no accessible label on %r", type(widget).__name__)


def build(app: Any) -> Gtk.Widget:
    view = _MeetingsView(app)
    page, set_content = common.surface(TITLE, SUBTITLE)
    set_content(view)
    for name in ("record", "stop", "duration", "elapsed", "_refresh_list",
                 "_refresh_status", "_on_toggled", "_on_stop"):
        setattr(page, name, getattr(view, name))
    page.status = view.status_recorder.status
    return page


__all__ = ["TITLE", "ICON", "SECTION", "SUBTITLE", "build", "CONSENT_KEY",
           "DURATIONS", "NOTHING_YET"]