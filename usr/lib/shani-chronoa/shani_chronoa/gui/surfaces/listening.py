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
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402
from shani_chronoa.gui.surfaces import common  # noqa: E402
from shani_chronoa.words import plural

SURFACE_ID = "listening"
#: Listening was the only panel with no section, so it landed in the catch-all -
#: measured on the real sidebar: six sections, and "Everything else" holding
#: exactly one row, Listening, under a heading that named no category a person
#: could act on. Its two siblings in the same job declare "Sensing" (voice.py,
#: calendar.py), and listening is the third sense of the three: what Chronoa
#: hears. Declared here rather than added to `DEFAULT_SECTION`, because that table
#: is the fallback for panels that predate sections and this one never did.
SECTION = "Sensing"

#: **"App audio", not "Listening".** This panel asks *"can Chronoa transcribe
#: what an application is playing?"* - it is system audio capture, not the
#: microphone. "Listening" in an app whose centrepiece is a microphone orb means
#: the microphone, so this sat beside **Voice** under a name that pointed at
#: neither of the two things it is.
TITLE = "Transcribe"
#: Required by `surfaces.all_surfaces()` (TITLE, ICON, build) - a surface
#: without one raises AttributeError inside that loop, which takes the
#: whole registry down rather than just this page. Found by walking the
#: built window, not by reading the module.
ICON = "audio-input-microphone-symbolic"


def _strip(box: Gtk.Widget) -> None:
    child = box.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt


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


#: The switch that lets an application's audio be captured - the microphone's
#: own consent, since a call records whoever is speaking in it.
CONSENT_KEY = "heard-sound-sense-enabled"
#: Kept for callers that still open the page that writes it.
CONSENT_TARGET = "settings:privacy"


def _consent_route(app: Any, allowed: Optional[bool],
                   on_change=None) -> Optional[Gtk.Widget]:
    """A "Turn on" banner for a refused permission, and nothing else.

    **Only on refusal.** Granted has nothing to fix; `None` is a fact with no
    verdict, and a button would claim the certainty the page just said it
    lacks; `app is None` is a headless build with nothing to press. It used to
    open Privacy settings and leave the person to find the row - the switch is
    now turned on here, by its Settings name (`common.consent_banner`).
    """
    if allowed is not False or app is None:
        return None
    return common.consent_banner(app, CONSENT_KEY,
                                 "Nothing is captured until it is on.",
                                 on_change=on_change)


def _consent() -> "tuple[str, Optional[bool]]":
    """(sentence, allowed). Read through the function the skill itself uses."""
    from shani_chronoa.config import ChronoaConfig

    config = ChronoaConfig()
    if config.sense_allowed("heard-sound"):
        return ("Allowed - 'Let Chronoa name a sound when you ask' is on", True)
    return ("Not allowed - 'Let Chronoa name a sound when you ask' is off", False)


#: How long to listen, as chips rather than a spin box: these are the four
#: lengths people ask for, and 5-600 seconds is the skill's own range.
DURATIONS = ((30, "30 sec"), (60, "1 min"), (300, "5 min"), (600, "10 min"))


def build(app: Any = None) -> Gtk.Widget:
    """Transcribe what an application is playing: pick it, start, read it.

    **Why it was rebuilt (2026-10-14).** The page was a diagnostics table: an
    essay for a heading, red key/value rows, a sandbox's address-space ceiling
    explained to the person, a permission row that sent them to Settings, no
    list of apps to choose from, and the controls floating at the bottom. Its
    Start button also never said *which* app, so with two playing the skill
    always refused ("say which one with stream="). Now: blockers are banners
    that fix themselves, the playing apps are a list you pick from, duration
    is four chips, Start is one button, and the transcript lands in a card
    with Copy and Send to conversation. The engineering is behind Details.
    """
    page = common.page_body(16)
    for side in ("top", "bottom", "start", "end"):
        getattr(page, f"set_margin_{side}")(18)
    state = {"streams": [], "chosen": "", "ok": None, "consent": None, "seconds": 60,
             "running": False}

    hero = Gtk.Box(spacing=14)
    hero.append(Gtk.Image(icon_name="audio-x-generic-symbolic", pixel_size=48))
    words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
    title = Gtk.Label(label="Transcribe what's playing", xalign=0.0)
    title.add_css_class("title-2")
    words.append(title)
    words.append(_dim("A call, a video, a podcast - Chronoa writes down what is said, "
                      "on this computer."))
    hero.append(words)
    page.append(hero)

    blockers = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    page.append(blockers)

    # -- what to capture ---------------------------------------------------
    sources = Adw.PreferencesGroup(title="Playing now")
    refresh = Gtk.Button(icon_name="view-refresh-symbolic", valign=Gtk.Align.CENTER,
                         tooltip_text="Look again")
    refresh.add_css_class("flat")
    sources.set_header_suffix(refresh)
    stream_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    stream_list.add_css_class("boxed-list")
    sources.add(stream_list)
    mic_note = _dim("")
    mic_note.set_visible(False)
    sources.add(mic_note)
    page.append(sources)

    # -- how long, and go --------------------------------------------------
    controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                       halign=Gtk.Align.CENTER)
    chips = Gtk.Box(spacing=0, halign=Gtk.Align.CENTER)
    chips.add_css_class("linked")
    first = None
    for seconds_value, label in DURATIONS:
        chip = Gtk.ToggleButton(label=label)
        if first is None:
            first = chip
        else:
            chip.set_group(first)
        chip.set_active(seconds_value == state["seconds"])
        chip.connect("toggled", lambda b, v=seconds_value: b.get_active()
                     and state.update(seconds=v))
        chips.append(chip)
    controls.append(_dim("Listen for", center=True))
    controls.append(chips)
    go = Gtk.Button(label="Start transcribing")
    go.add_css_class("pill")
    go.add_css_class("suggested-action")
    go.set_size_request(240, -1)
    controls.append(go)
    why_not = _dim("", center=True)
    controls.append(why_not)
    page.append(controls)

    # -- the transcript ----------------------------------------------------
    result = Adw.PreferencesGroup(title="Transcript")
    result.set_visible(False)
    text = Gtk.TextView(editable=False, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                        top_margin=12, bottom_margin=12, left_margin=12, right_margin=12)
    text.add_css_class("card")
    result.add(text)
    actions = Gtk.Box(spacing=8, margin_top=8)
    copy = Gtk.Button(label="Copy")
    copy.add_css_class("pill")
    send = Gtk.Button(label="Send to conversation")
    send.add_css_class("pill")
    actions.append(copy)
    actions.append(send)
    result.add(actions)
    page.append(result)

    # -- other ways in, and the details ------------------------------------
    more = Adw.PreferencesGroup()
    file_row = Adw.ActionRow(title="Transcribe a recording file…",
                             subtitle="An audio or video file, speakers told apart",
                             activatable=True)
    file_row.add_prefix(Gtk.Image(icon_name="document-open-symbolic"))
    file_row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
    file_row.connect("activated", lambda *_: app is not None
                     and app.activate_action("transcribe-file", None))
    more.add(file_row)
    details = Adw.ExpanderRow(title="Details", subtitle="What this machine can do, and why")
    engine_row = Adw.ActionRow(title="Speech engine", subtitle="checking…", subtitle_selectable=True)
    consent_row = Adw.ActionRow(title="Permission", subtitle="checking…")
    caveat_row = Adw.ActionRow(title="From a skill", subtitle="checking…")
    tip_row = Adw.ActionRow(title="Ask in conversation",
                            subtitle="\u201ctranscribe what's playing in Chrome for the next minute\u201d",
                            subtitle_selectable=True)
    for row in (engine_row, consent_row, caveat_row, tip_row):
        details.add_row(row)
    more.add(details)
    page.append(more)

    # -- behaviour ---------------------------------------------------------
    def can_start() -> str:
        if state["running"]:
            return "Listening…"
        if state["ok"] is False:
            return "Needs a speech model first."
        if state["consent"] is False:
            return "Needs the permission above."
        if not state["chosen"]:
            return "Play something, then pick it above." if not state["streams"] else "Pick what to transcribe."
        return ""

    def update_go() -> None:
        reason = can_start()
        go.set_sensitive(not reason)
        why_not.set_text(reason if not state["running"] else
                         f"Listening to it for {state['seconds']} seconds, then transcribing…")

    def fill(streams: Iterable, problem: str) -> None:
        _strip(stream_list)
        found = list(streams)
        live = [s for s in found if s.recordable]
        mics = [s for s in found if not s.recordable]
        state["streams"] = live
        if state["chosen"] not in {s.name for s in live}:
            state["chosen"] = live[0].name if len(live) == 1 else ""
        if problem:
            stream_list.append(Adw.ActionRow(title="Could not read what is playing",
                                             subtitle=problem))
        elif not live:
            stream_list.append(Adw.ActionRow(
                title="Nothing is playing right now",
                subtitle="Start the video or call, then press refresh. A quiet machine, not a fault."))
        group_head = None
        for st in live:
            radio = Gtk.CheckButton(valign=Gtk.Align.CENTER)
            if group_head is None:
                group_head = radio
            else:
                radio.set_group(group_head)
            radio.set_active(st.name == state["chosen"])
            radio.connect("toggled", lambda b, n=st.name: b.get_active()
                          and (state.update(chosen=n), update_go()))
            # The capture name too: `media.name` is what a person recognises,
            # `node.name` is what `pw-record --target` takes.
            row = Adw.ActionRow(title=GLib.markup_escape_text(st.label),
                                subtitle=GLib.markup_escape_text(st.name),
                                activatable_widget=radio)
            row.add_prefix(radio)
            stream_list.append(row)
        mic_note.set_text(
            f"{plural(len(mics), 'app')} using the microphone - an app's own microphone "
            f"cannot be captured, only audio leaving it." if mics else "")
        mic_note.set_visible(bool(mics))
        update_go()

    def show(streams, problem, can, ok, consent, consent_ok, caveat) -> bool:
        refresh.set_sensitive(True)
        state.update(ok=ok, consent=consent_ok)
        _strip(blockers)
        if ok is False:
            blockers.append(common.banner(
                f"Chronoa cannot hear yet: {can}", "Download a speech model",
                (lambda: app.activate_action("download-speech-model", None)) if app else (lambda: None)))
        route = _consent_route(app, consent_ok, on_change=again)
        if route is not None:
            blockers.append(route)
        engine_row.set_subtitle(can)
        consent_row.set_subtitle(consent)
        caveat_row.set_subtitle(caveat or "not read")
        fill(streams, problem)
        return False

    def read_off_loop() -> None:
        """**Off the main loop.** `pw-dump` and a whisper probe are
        subprocesses; probing in an idle callback froze the app for seconds."""
        streams, problem = _what_is_playing()
        can, ok = _can_transcribe()
        consent, consent_ok = _consent()
        caveat = _sandbox_caveat()
        GLib.idle_add(show, streams, problem, can, ok, consent, consent_ok, caveat)

    def again(*_a) -> None:
        refresh.set_sensitive(False)
        threading.Thread(target=read_off_loop, daemon=True).start()

    def start(_button=None) -> None:
        state["running"] = True
        update_go()
        wanted, seconds = state["chosen"], state["seconds"]

        def work():
            from shani_chronoa import tools
            out = tools.execute_tool("recording", {"action": "capture", "seconds": seconds,
                                                   "stream": wanted})
            GLib.idle_add(done, getattr(out, "text", str(out)))
        threading.Thread(target=work, daemon=True).start()

    def done(said: str) -> bool:
        state["running"] = False
        update_go()
        text.get_buffer().set_text(said)
        result.set_visible(True)
        return False

    def copy_text(*_a) -> None:
        buffer = text.get_buffer()
        page.get_clipboard().set(buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False))

    def send_text(*_a) -> None:
        buffer = text.get_buffer()
        window = getattr(app, "window", None)
        if window is not None and hasattr(window, "set_input_text"):
            window.set_input_text(buffer.get_text(buffer.get_start_iter(),
                                                  buffer.get_end_iter(), False))
            window.present()

    refresh.connect("clicked", again)
    go.connect("clicked", start)
    copy.connect("clicked", copy_text)
    send.connect("clicked", send_text)
    update_go()
    again()

    page.status = lambda: (common.STATUS_ATTENTION if state["ok"] is False or state["consent"] is False
                           else common.STATUS_OK if state["ok"] else common.STATUS_UNKNOWN)
    shell, set_content = common.surface(TITLE)
    set_content(common.scrolled(page))
    shell.status = page.status
    return shell


def _dim(text: str, center: bool = False) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0.5 if center else 0.0, wrap=True, max_width_chars=60)
    if center:
        label.set_justify(Gtk.Justification.CENTER)
    label.add_css_class("dim-label")
    return label
