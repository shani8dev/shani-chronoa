"""Listening: can Chronoa transcribe what an application is playing?

One question a person actually asks, usually while a call is running in another
window: **can this thing take a transcript of my meeting?**

The answer is not one fact, and saying "yes" when any part is missing is how
somebody joins a call and finds out afterwards. This page reads the four things
that decide it, each from the function that would act on it:

1. **What is playing right now.** `pipewire.list_streams()`, not a copy of it - a
   list of applications this page kept in step by hand would be wrong the day a
   stream appears. A stream *leaving* an application is the capturable kind; an
   application's own microphone is a different thing and is labelled as such
   rather than offered as a target that cannot work.
2. **Whether this browser can make sense of it.** `recordings.whisper_model()`
   raises the sentence that distinguishes **no whisper.cpp** from **no model
   downloaded** - collapsing those two is how somebody reinstalls a package they
   already have and never downloads the model.
3. **Whether it is allowed.** `heard-sound` is the consent the microphone asks
   for and the same one `recording action=capture` requires, read through
   `sense_allowed` so the alias and privacy-mode handling stay in one place.
4. **Whether the capture can work here at all.** The skill child's 512 MB
   address-space ceiling is what whisper.cpp segfaults under (measured both
   ways), so a machine that has everything and still cannot is a real state and
   is named rather than reported as a missing model.

**Everything here is read off the main loop.** `pw-dump` and a whisper probe are
subprocesses, and `Diagnostics` freezing the app for seconds by probing inside an
idle callback is the recorded bug - so this page never does it.
"""

from __future__ import annotations

import threading
from typing import Any, Iterable, Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

SURFACE_ID = "listening"
TITLE = "Listening"
#: Required by `surfaces.all_surfaces()` (TITLE, ICON, build) - a surface
#: without one raises AttributeError inside that loop, which takes the
#: whole registry down rather than just this page. Found by walking the
#: built window, not by reading the module.
ICON = "audio-input-microphone-symbolic"


def _strip(box: Gtk.Box) -> None:
    child = box.get_first_child()
    while child is not None:
        following = child.get_next_sibling()
        box.remove(child)
        child = following


class _StateRow(Gtk.Box):
    """A labelled fact whose value is filled in off the main loop."""

    def __init__(self, label: str, initial: str = "not checked yet") -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name = Gtk.Label(label=label, xalign=0.0, hexpand=True, wrap=True)
        name.add_css_class("dim-label")
        self.value = Gtk.Label(label=initial, xalign=1.0, wrap=True, selectable=True)
        self.append(name)
        self.append(self.value)

    def set_state(self, text: str, ok: Optional[bool] = None) -> None:
        """`ok=None` is a fact with no verdict, and giving it one is how a
        panel invents a problem."""
        self.value.set_label(text)
        for cls in ("success", "error"):
            self.value.remove_css_class(cls)
        if ok is True:
            self.value.add_css_class("success")
        elif ok is False:
            self.value.add_css_class("error")


class _Row(Gtk.Box):
    """One line of "what is playing", with whether it can be transcribed."""

    def __init__(self, label: str, value: str, ok: Optional[bool] = None) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name = Gtk.Label(label=label, xalign=0.0, hexpand=True, wrap=True)
        value_label = Gtk.Label(label=value, xalign=1.0, wrap=True, selectable=True)
        self.append(name)
        self.append(value_label)
        if ok is True:
            value_label.add_css_class("success")
        elif ok is False:
            value_label.add_css_class("error")


class _Card(Gtk.Box):
    """A titled group of rows. `card` is this repo's own class for it."""

    def __init__(self, title: str, *children: Gtk.Widget) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_css_class("card")
        head = Gtk.Label(label=title, xalign=0.0)
        head.add_css_class("heading")
        self.append(head)
        for child in children:
            self.append(child)


def _what_is_playing() -> "tuple[list, str]":
    """(streams, problem). Reads the live graph, never a remembered one."""
    from shani_chronoa import pipewire

    if not pipewire.is_available():
        return [], ("Cannot read the audio graph: pw-dump is not installed, or no "
                    "PipeWire graph is running.")
    try:
        return pipewire.list_streams(), ""
    except Exception as exc:  # noqa: BLE001 - a broken graph is not a dead panel
        return [], f"the audio graph could not be read ({type(exc).__name__}: {exc})"


def _can_transcribe() -> "tuple[str, Optional[bool]]":
    """(sentence, ok). Distinguishes the two failures a person can act on."""
    from shani_chronoa import recordings

    try:
        binary, model = recordings.whisper_model()
    except Exception as exc:  # noqa: BLE001 - both failures arrive as one type
        return str(exc), False
    return (f"whisper.cpp at {binary}, model {model.rsplit('/', 1)[-1]}", True)


def _sandbox_caveat() -> str:
    """Whether transcription can *reach* whisper from where the skill runs.

    **Derived, not asserted.** The ceiling is read off the default profile, so a
    change to the sandbox changes the sentence instead of leaving a stale
    "512 MB" behind.

    This exists because the page would otherwise report "transcription is
    possible" on a machine where `recording action=capture` dies: whisper.cpp
    segfaults in `mmap` under the ceiling, measured both ways - no limit,
    `brown fox jump over the lazy dog` rc=0; `ulimit -v 524288`, segfault. **The
    app is not under that ceiling**, so this says which of the two it is talking
    about rather than reassuring about both.
    """
    try:
        from shani_chronoa.sandbox.profiles import profile_for_origin

        ceiling = profile_for_origin("user").ceiling(300).memory_bytes
    except Exception:  # noqa: BLE001 - no readable profile is not a dead panel
        return ""
    mb = max(1, ceiling // 1048576)
    return (f"From a skill, transcription is capped by the skill sandbox's "
            f"{mb} MB address-space ceiling, which whisper.cpp can crash under. "
            f"The app itself is not under it, so asking in conversation works "
            f"while a skill-run capture may not.")


def _consent() -> "tuple[str, Optional[bool]]":
    """(sentence, allowed). Read through the function the skill itself uses."""
    from shani_chronoa.config import ChronoaConfig

    config = ChronoaConfig()
    if config.sense_allowed("heard-sound"):
        return ("Allowed - 'Let Chronoa name a sound when you ask' is on", True)
    return ("Not allowed - 'Let Chronoa name a sound when you ask' is off", False)


def build(app: Any = None) -> Gtk.Widget:
    """The page. Everything after this runs off the main loop, on demand."""
    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    for margin in ("margin-top", "margin-bottom", "margin-start", "margin-end"):
        page.set_property(margin, 12)

    headline = Gtk.Label(
        label="Chronoa can transcribe what an application is playing - a call in "
              "a browser, a video, a podcast - by capturing that application's "
              "audio stream. Here is whether this machine can do it right now.",
        xalign=0.0, wrap=True)
    headline.add_css_class("title-4")

    can_row = _StateRow("Transcription")
    consent_row = _StateRow("Permission")
    caveat_row = _StateRow("From a skill")

    status = Gtk.Label(label="Reading the audio graph…", xalign=0.0, wrap=True)
    stream_rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

    # The action. **Not decorative.** A page that answers "yes you can" and
    # offers no way to do it is a card over a queue nothing can enqueue, so the
    # button runs the real skill through the same dispatch the model uses.
    seconds = Gtk.SpinButton.new_with_range(5, 600, 5)
    seconds.set_value(60)
    seconds.set_tooltip_text("How many seconds of the application's audio to "
                             "capture and transcribe.")
    go = Gtk.Button(label="Transcribe it now")
    go.add_css_class("suggested-action")
    said = Gtk.Label(label="", xalign=0.0, wrap=True, selectable=True)

    def start(_button=None):
        # Off the main loop: `tools.execute_tool` is a subprocess that runs for
        # as long as the capture, and the app must not freeze for a minute.
        go.set_sensitive(False)
        said.set_label("Capturing and transcribing…")
        wanted = int(seconds.get_value())

        def work():
            from shani_chronoa import tools
            result = tools.execute_tool(
                "recording", {"action": "capture", "seconds": wanted})
            GLib.idle_add(done, getattr(result, "text", str(result)))

        threading.Thread(target=work, daemon=True).start()

    def done(text: str):
        go.set_sensitive(True)
        # The sentence is the tool's own, including its refusals - a consent
        # refusal here must name the switch, not a generic "failed".
        said.set_label(text)
        return False

    go.connect("clicked", start)

    unlock = Gtk.Button(label="Look again", halign=Gtk.Align.START)

    def fill(streams: Iterable, problem: str) -> None:
        _strip(stream_rows)
        if problem:
            status.set_label(problem)
            stream_rows.append(_Row("What is playing", "could not be read"))
            return
        found = list(streams)
        if not found:
            status.set_label("Nothing is playing or recording audio right now.")
            stream_rows.append(_Row("What is playing",
                                    "no application has an audio stream open"))
            return
        live = [s for s in found if s.recordable]
        status.set_label(
            f"{len(live)} of {len(found)} can be transcribed."
            if live else
            "Nothing here can be transcribed - these are applications' "
            "microphones, not audio leaving them.")
        for st in found:
            if st.recordable:
                # The capture name, not only the friendly one: `media.name` is
                # what a person recognises, `node.name` is what
                # `pw-record --target` takes, and a page that shows one while
                # the action needs the other is the dead-control class.
                stream_rows.append(
                    _Row(f"{st.label}  ({st.name})", "can be transcribed", True))
            else:
                stream_rows.append(_Row(st.label, "this app's own microphone"))

    def show(streams: Iterable, problem: str, can: str, ok: Optional[bool],
             consent: str, consent_ok: Optional[bool], caveat: str) -> bool:
        unlock.set_sensitive(True)
        fill(streams, problem)
        can_row.set_state(can, ok)
        consent_row.set_state(consent, consent_ok)
        caveat_row.set_state(caveat or "not read", None)
        return False

    def read_off_loop() -> None:
        """**Off the main loop, on a thread.** `pw-dump` and a whisper probe are
        subprocesses, and probing inside an idle callback is the bug that froze
        the app for seconds - so this never runs here."""
        streams, problem = _what_is_playing()
        can, ok = _can_transcribe()
        consent, consent_ok = _consent()
        caveat = _sandbox_caveat()
        GLib.idle_add(show, streams, problem, can, ok, consent, consent_ok,
                     caveat)

    def again(_button: Gtk.Button = None) -> None:
        unlock.set_sensitive(False)
        threading.Thread(target=read_off_loop, daemon=True).start()

    unlock.connect("clicked", again)

    page.append(headline)
    page.append(_Card("What is possible", can_row, consent_row, caveat_row))
    page.append(_Card("What is playing right now", status, stream_rows))
    controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    controls.append(Gtk.Label(label="Capture for", xalign=0.0))
    controls.append(seconds)
    controls.append(Gtk.Label(label="seconds", xalign=0.0))
    controls.append(go)
    controls.append(unlock)
    page.append(controls)
    page.append(said)
    page.append(Gtk.Label(
        label="Capturing an application records whoever is speaking in it, so it "
              "sits behind the same permission as the microphone.",
        xalign=0.0, wrap=True))
    page.append(Gtk.Label(
        label="Ask for it in conversation with: \"transcribe what's playing in "
              "Chrome for the next minute\".",
        xalign=0.0, wrap=True, selectable=True))

    again()
    return page
