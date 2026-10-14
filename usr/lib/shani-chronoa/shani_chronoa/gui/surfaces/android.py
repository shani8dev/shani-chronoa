"""The Android surface: a USB-debugging phone, over adb.

**Neither desktop has a panel for this, and neither has any other app.** GNOME
Control Center's 26 panels and Plasma's KCM set were enumerated from the
installed packages rather than recalled — the same measurement that took the
Bluetooth and Sound candidates off the table for this session. A phone reached
over `adb` is not a wearable, not a Bluetooth peer and not a KDE Connect pair;
it is a device that happens to speak a protocol nobody has a settings page for.

**This panel adds no transport.** Every value comes from
`skills/android_device.py` through the app's own `tools.execute_tool`, behind the
same `phone-control-enabled` switch, and with the same refusals the assistant
gets. A second adb parser here would be a second opinion about which device is
authorised, which is the drift this repository keeps finding.

**Read-mostly on purpose, and the panel says so rather than hiding it.** The
skill was built to a fixed set of verbs — devices, info, screenshot, apps, push,
pull, open_url — deliberately *not* taps and *not* `adb shell`. A person's phone
holds their messages, their photos and their location; a settings panel that
offered a shell prompt at it would be a very different thing from this. So there
is no `adb shell` button here and the surface names the skill for anything more.

**The phone is the first consent, and it is not ours.** `adb` only reaches a
device whose owner turned on USB debugging and accepted this computer's key. A
machine with no such device is not a broken panel, it is a phone that has not
agreed to be spoken to.

**Nothing slow runs on the main thread.** `_adb` carries a 60-second timeout in
the module and `adb devices` plus an `info` read are two round trips; every read
here runs on a thread and hands its result back with `GLib.idle_add`.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib, Gtk  # type: ignore

from . import common

logger = logging.getLogger(__name__)

TITLE = "Android"
SECTION = "Sensing"
ICON = "drive-harddisk-usb-symbolic"
SUBTITLE = ("A phone connected with USB debugging - what it is, how much "
            "battery and storage it has, and a read-mostly way to move a file "
            "either way. Neither desktop has a panel for a phone reached over "
            "adb; the Bluetooth and Sound ones are elsewhere for a reason.")

#: The switch, and it is the same one the KDE Connect / GSConnect phone uses -
#: `phone-control-enabled`, not a new one. Two switches for "may this machine
#: touch a phone" would be two things a user has to discover separately, and one
#: of them would be the one they had not enabled.
SETTINGS_TARGET = "privacy"

STATE_PERMITTED = "permitted"
STATE_REFUSED = "not permitted"
STATE_NO_ADB = "adb not installed"
STATE_NONE = "no phone connected"

HEADLINE = {
    STATE_PERMITTED: "Connected phones",
    STATE_REFUSED: "Reaching a phone is turned off",
    STATE_NO_ADB: "adb is not installed",
    STATE_NONE: "No phone is connected with USB debugging",
}

#: Three of the four are "could not determine". None of them is a fault in the
#: machine, and none is a healthy reading either - a phone that has not agreed
#: to be spoken to is not the same as a phone that is working.
HEALTH = {
    STATE_REFUSED: common.STATUS_OFF,
    STATE_NO_ADB: common.STATUS_UNKNOWN,
    STATE_NONE: common.STATUS_UNKNOWN,
}

BODY = {
    STATE_PERMITTED: "",
    STATE_REFUSED: (
        "Nothing was read, and nothing on this page came from a phone. "
        "Enable the switch in Settings, under Privacy."),
    STATE_NO_ADB: (
        # **The package, because a refusal without a door is a dead end.** Named
        # from `adb(1)`'s own footer - *"Package name: extra/android-tools"* -
        # which is also what `files._PACKAGE_HINTS["adb"]` already answers, so
        # this names the package rather than restating the check.
        #
        # **"Shanios ships it" was verified rather than assumed**, and it is
        # `shani-chronoa`'s `optdepends` that is only an offer: `adb` reaches
        # every image because `shani-tools-extra` is in **Packages-Base of all
        # four profiles** and hard-depends on `android-tools`. So a machine
        # without it is not a normal Shanios install, which is why the sentence
        # says so rather than implying this is how the OS usually arrives.
        "`adb` is the tool that speaks to a phone over USB debugging. Every "
        "Shanios image has it - it comes in with the base tools - so this "
        "machine is missing something it should have; the package that "
        "provides it is android-tools, and without it there is no way to ask "
        "whether a phone is connected."),
    STATE_NONE: (
        "No device has USB debugging turned on and has accepted this "
        "computer's key. That is the phone owner's decision, and it is not "
        "something this page can change from here."),
}

#: state -> one clause for the status row, and **not** `BODY`. The row and the
#: empty state below it are two places saying one thing.
HEALTH_SUMMARY = {
    STATE_REFUSED: "the switch is off",
    STATE_NO_ADB: "adb is not installed",
    STATE_NONE: "no phone is connected",
}

ROUTES = {STATE_REFUSED: ("Open Privacy settings", "privacy")}


@dataclass(frozen=True)
class State:
    """The panel's own answer when there is no list to show.

    A property on the state rather than a word chosen at each call site, because
    the word is the claim and the state is the fact, and two places deciding it
    are two things that can disagree. `build()` reads it both for the status row
    and for the sidebar's dot, so those cannot drift.
    """

    state: str
    why: str = ""

    @property
    def health(self) -> str:
        return HEALTH.get(self.state, common.STATUS_UNKNOWN)

    @property
    def headline(self) -> str:
        return HEADLINE.get(self.state, self.state)

    @property
    def body(self) -> str:
        """The generic explanation, plus what *this* instance was refused for."""
        generic = BODY.get(self.state, "")
        if not self.why or self.why in generic:
            return generic or self.why or self.headline
        return f"{generic}\n\nThis time: {self.why}" if generic else self.why


def _in_thread(work: "Callable[[], Any]", done: "Callable[[Any], None]") -> None:
    """Run `work` off the main thread and hand its result, or the exception, to
    `done`."""
    def run() -> None:
        try:
            result = work()
        except Exception as exc:  # noqa: BLE001 - shown on the row, not raised
            logger.warning("android surface: %s", exc, exc_info=True)
            result = exc
        GLib.idle_add(lambda: (done(result), False)[1])
    threading.Thread(target=run, daemon=True).start()


def _tool(name: str, arguments: dict) -> str:
    """The app's own dispatcher - the same one the assistant goes through."""
    from shani_chronoa import tools
    return str(tools.execute_tool(name, arguments))


def _ask(arguments: dict) -> str:
    return _tool("android_device", arguments)


def _row(title: Any, subtitle: Any = "",
         suffix: Optional[Gtk.Widget] = None) -> Gtk.Widget:
    """One row.

    **Not pre-escaped.** `common.row()` escapes both the title and the subtitle
    itself, so escaping here as well renders an app named `Files & Docs` as
    `Files &amp; Docs` - which `devices.py` records having shipped in this repo's
    sibling panel.
    """
    return common.row(str(title), str(subtitle), suffix)


def _app_config(app: Any) -> Any:
    return getattr(app, "config", None)


def _permission(config: Any) -> State:
    """The gate, through the skill's own helper.

    **`android_device._consent`, not an inline `config.get_bool`.** It carries
    the refusal sentence the assistant returns, so the panel and the assistant
    cannot come to disagree about what the switch says.
    """
    from shani_chronoa.skills import android_device as ad
    if config is None:
        return State(state=STATE_REFUSED,
                     why="This app has no settings to check the switch in.")
    allowed, problem = ad._consent(config)
    if not allowed:
        return State(state=STATE_REFUSED, why=problem)
    return State(state=STATE_PERMITTED)


def _devices() -> Any:
    """Which phones adb can see, as the skill's own answer.

    **The skill's refusal text, kept whole.** `_run` distinguishes "adb is not
    installed" from "nothing is authorised" from "this device is unauthorised",
    and those are three different situations with three different remedies.
    Re-deriving which one happened here would be a second parser for a
    classification the skill has already made carefully.
    """
    return _ask({"action": "devices"})


#: adb's device states, and the filter `adb(1)`'s own vocabulary justifies.
#:
#: `get-state` says *"Print offline | bootloader | device"*; `wait-for` says
#: *"STATE: device, recovery, rescue, sideload, bootloader, or disconnect"*;
#: and `reconnect offline` names the pair *"offline/unauthorized"*. `adb(1)`
#: nowhere documents `authorizing`, `connecting` or `host`, and those are words
#: adb is known to print while a link settles - so they are here because the
#: alternative is a refusal sentence read as a phone.
#:
#: **This filter exists because dropping the colon test exposed a worse bug.**
#: With a colon required, `adb is not installed - it is the tool that speaks to
#: a phone over USB debugging` was refused by accident. Without one, it parsed
#: as a phone named `adb` whose state was `is` - so a machine missing adb
#: entirely would have listed one device and reported "1 answering", which is
#: the absence-shaped wrong answer this file exists to refuse.
ADB_STATES = frozenset({
    "device", "offline", "unauthorized", "authorizing", "bootloader",
    "recovery", "sideload", "rescue", "disconnect", "connecting", "host",
    "no",  # adb prints "no permissions; see [http://...]" as the state
})


def _connected(answer: str) -> List[tuple]:
    """`(serial, state, model)` out of a `devices` answer, or `[]`.

    **The model is read because the skill asked for it.** `adb(1)` says of
    `devices -l` only *"Use long output"*, and the long form's extra fields are
    `model:`, `product:`, `device:` and `transport_id:` - `android_device`
    asks for `-l` and takes `model:` from it. A panel that discards the one
    field the caller requested `-l` for is asking for something and throwing it
    away, which is the same shape as the model row reading one table while the
    chip beside it read another.


    **The state is column 1, and this panel used to read the last field
    instead.** The skill asks for `devices -l`, so the real line is
    `R5CT10  unauthorized usb:1-1 model:Pixel_8 transport_id:3` - and
    `split()[-1]` on that is `transport_id:3`. The sentence about accepting the
    key never fired, and a phone the owner had not authorised read as
    `transport_id:3`. `android_device._devices()` reads column 1; so does this,
    because two answers to "what state is that phone in" is one too many.

    **The serial carries no guaranteed colon.** `adb(1)`, under `ADB_LIBUSB`:
    *"use `adb devices -l` (usb: prefix is omitted for libusb) to identify which
    is in use."* So the serial is `usb:1-1` on adb's own USB backend and `1-1`
    on libusb. Requiring a colon therefore reports **no phone at all** on a
    libusb system, which is what the first version of this function did - and
    which reads exactly like a machine with nothing plugged in.

    **Column 1 must be one of `ADB_STATES`.** The header check alone is not
    enough, and the reason is the bug this replaced: the skill's own refusal
    for a missing `adb` is a sentence of ordinary words, and it parses as a
    device line. Filtering on adb's own vocabulary is the classification the
    manual already made, rather than a second guess about what a line looks
    like.
    """
    out: List[tuple] = []
    for line in str(answer).splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[1] not in ADB_STATES:
            continue
        model = next((p.split(":", 1)[1] for p in parts[2:]
                      if p.startswith("model:")), "")
        out.append((parts[0], parts[1], model.replace("_", " ")))
    return out


def _device_row(serial: str, state: str, model: str = "") -> Gtk.Widget:
    """One phone, with adb's own word for it - and nothing more.

    **Three groups, from two manuals rather than from memory.**

    - `unauthorized` and `offline` are adb's *own* unreachables: `adb(1)`
      names both under `reconnect offline` - *"Reset offline/unauthorized
      devices to force reconnect"* - and each has something the phone's owner
      does about it.
    - `bootloader` is different, and **this panel used to say nothing about it**
      on the reasoning that a mode is not a fault. `fastboot(1)` is a second
      documented tool that exists precisely to reach a device in this state,
      with `fastboot continue` to *"Continue with autoboot"* out of it. So the
      state is not adb's word and nothing more: it is the one state where adb
      **cannot** talk to the phone and something else can.
    - everything else - `device`, `recovery`, `sideload`, `rescue`,
      `disconnect` (`adb(1) wait-for`) - keeps adb's bare word. Neither manual
      documents a second tool for any of them, and inventing a remedy is the
      absence-shaped wrong answer this file records.

    **The bootloader row names `fastboot` and stops.** `fastboot(1)` also
    carries `-w` (*"Erase userdata and cache"*), `erase`, `format`, `flash`,
    and `flashing unlock`. So a row saying "run fastboot" would be pointing at
    a tool that can empty the phone, which is why the sentence names the tool
    and says Chronoa does not drive it rather than telling anyone to use it.
    """
    if state == "unauthorized":
        subtitle = ("authorised by nobody - accept this computer's key on the "
                    "phone")
    elif state == "offline":
        subtitle = ("offline - the phone has USB debugging on but is not "
                    "answering")
    elif state == "bootloader":
        subtitle = ("in bootloader mode - adb cannot reach this phone, and "
                    "fastboot is the tool that can; Chronoa does not drive it")
    else:
        subtitle = state
    if model:
        subtitle = f"{model} - {subtitle}" if subtitle else model
    return _row(serial, subtitle)


def build(app) -> Gtk.Widget:
    """The phones adb can see for `app`, asked once at build time.

    Asked once, and never while the gate is shut - a permission that could not
    be checked is not a granted one.
    """
    page, set_content = common.surface(TITLE, SUBTITLE)
    recorder = common.StatusRecorder()
    # **The recorder is attached to the page, not only written through.** The
    # sidebar's health dot is drawn from `page.status()`, and a panel that builds
    # a recorder without ever assigning it leaves the dot reading a page with no
    # answer - which is the defect `tests/test_surface_health_status.py` exists
    # for, and which this panel's own docstring about the sibling caught.
    page.status = recorder.status

    def gated(state: "State") -> Gtk.Widget:
        """An empty state, *with* the status row that matches its own word."""
        column = common.page_body(18)
        column.set_margin_top(12)
        column.append(recorder.row(state.health, state.headline,
                                   HEALTH_SUMMARY.get(state.state, "")))
        route = ROUTES.get(state.state)
        if route is not None:
            label, target = route
            column.append(common.banner(
                "This is a setting on this machine, not a fault.",
                label,
                lambda _l=label, _t=target: common.open_page(app, _t)))
        column.append(common.empty_state(ICON, state.headline, state.body))
        return common.scrolled(column)

    refusal = _permission(_app_config(app))
    if refusal.state != STATE_PERMITTED:
        recorder.set(refusal.health)
        set_content(gated(refusal))
        return page

    column = common.page_body(14)
    column.set_margin_top(12)
    set_content(common.scrolled(column))

    # **An interim row, because the dot answers at build time.** The read runs
    # on a thread and its real row arrives after it, but `page.status()` is
    # answerable the moment the page exists - and a recorder answering a word
    # with no row drawn for it is exactly what
    # `tests/test_surface_health_status.py`'s dot-agrees test catches. Measured:
    # on a machine with no adapter the read fails, `deliver` draws the row for
    # the failure, and before that the recorder answered `unknown` over an empty
    # panel. A sibling panel passed the same test by finishing first, which is a
    # race rather than a property.
    recorder.set(common.STATUS_UNKNOWN)
    interim = recorder.row(
        common.STATUS_UNKNOWN, "Reading connected phones",
        "asking the machine what is connected")
    column.append(interim)

    def deliver(widget: Gtk.Widget, health: str, headline: str,
                summary: str) -> None:
        column.append(widget)
        recorder.set(health)
        column.append(recorder.row(health, headline, summary))
        column.remove(interim)

    def when_ready(answer: Any) -> None:
        if isinstance(answer, Exception):
            deliver(gated(State(state=STATE_NO_ADB, why=str(answer))),
                    common.STATUS_UNKNOWN, HEADLINE[STATE_NO_ADB],
                    HEALTH_SUMMARY[STATE_NO_ADB])
            return
        text = str(answer)
        listing = common.page_body(8)
        devices = _connected(text)
        if not devices:
            # **Two states, chosen by which refusal the skill actually sent.**
            # `_run` separates "adb is not installed" from "nothing connected",
            # and those need different remedies - one is a missing tool on this
            # machine and the other is a phone whose owner has not agreed to be
            # spoken to. Deciding it here would be a second classifier for a
            # distinction the skill has already made carefully, and getting it
            # wrong would report a broken machine where there is only no phone.
            state = (STATE_NO_ADB if "not installed" in text.lower()
                     else STATE_NONE)
            deliver(gated(State(state=state, why=text.strip())),
                    common.STATUS_UNKNOWN, HEADLINE[state],
                    HEALTH_SUMMARY[state])
            return
        listing.append(_row("Connected phones",
                            f"{len(devices)} answering over USB debugging"))
        for serial, state, model in devices:
            listing.append(_device_row(serial, state, model))
        listing.append(_row(
            "Moving a file, and the rest",
            "Pulling a file off the phone, pushing one to it and opening a link "
            "on it are all here in the skill; so is a screenshot. There is "
            "deliberately no way to run a command on the phone from this panel "
            "- ask the assistant, which asks the same skill."))
        deliver(listing, common.STATUS_OK, "Connected phones",
                f"{len(devices)} answering")

    def on_devices() -> None:
        GLib.idle_add(when_ready, _devices())

    GLib.Thread.new("chronoa-android-devices", lambda _d: on_devices(), ())
    return page