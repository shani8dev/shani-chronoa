"""The Wearables surface: what a paired Bluetooth LE device says about itself.

**This panel adds no transport.** Every value on it comes out of
`skills/bluetooth_gatt.py` — the same module the assistant's own skill entry
point calls, behind the same `bluetooth-gatt-enabled` gate, and asking it for
permission through its own `_consent()` helper so the refusal sentence is the
module's and cannot drift from the assistant's. That is the arrangement
`phone.py` and `devices.py` already state for themselves, and it is the only
one that keeps the promise those two make: *a switch that stops the assistant
stops this panel too*.

**The gap it closes is narrow and real.** `bluetooth_gatt` was reachable exactly
one way — a model deciding to call it. That is the right shape for "what does my
watch's battery say" said out loud and the wrong one for a person who owns the
strap: there was no way to see what was paired, what a device exposes, or what
a stored value decodes to, without asking the assistant to go and find out.
`devices.py` excludes this on purpose and says why: *"nothing in `phone.py` asks
`bluetoothctl` for anything, and a panel listing BT devices would be answering
through a mechanism that is not wired to this one."* This surface is that wiring,
in the app that already owns the module.

**The tuple shapes below are the module's, not a guess, and two of them are not
what its docstrings say.** `attributes()` and `services()` both return
**three**-tuples — `(handle, uuid, kind)` — while both docstrings claim
`[(handle, uuid)]`. `gatttool` prints no type field at all, so the kind is added
by the parser; a reader written to the documented two-tuple shape raises on the
first row of every device. Read from the returns, as here.

**Nothing slow runs on the main thread.** `paired()`, `attributes()`,
`services()` and `read_value()` each shell out to `bluetoothctl` or `gatttool`
and carry a 25-second timeout in the module; `phone.py` measured ~24 s for 813
contacts over PBAP. So every read here runs in a thread and hands its result back
with `GLib.idle_add`, and the page is built before any of them start.

**`listen` is offered, and it is the one button here that takes real time.**
`notifications()` is the module's own public subscribe-and-collect, and the
assistant reaches it the same way. It collects for one to sixty seconds and the
device has to be worn and in use for it to push anything — `Heart Rate
Measurement` has no stored value to read at all, so there is no `read` that
answers for it. The button says that on its own row rather than letting someone
discover it by pressing Read and seeing nothing.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Tuple

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib, Gtk, Pango  # type: ignore

logger = logging.getLogger(__name__)

from shani_chronoa.skills import bluetooth_gatt as gatt  # noqa: E402

from shani_chronoa.capabilities import gate_label  # noqa: E402
from shani_chronoa.words import plural

from . import common

TITLE = "Wearables"
SECTION = "Sensing"
ICON = "bluetooth-symbolic"
SUBTITLE = ("Paired Bluetooth Low Energy devices and what they say about "
            "themselves - a watch's battery, a band's firmware, a sensor's "
            "stored reading. Read by the same module the assistant uses. "
            "Connecting a device, and turning the adapter off, are in your "
            "desktop's own Bluetooth settings; what neither desktop has is "
            "this layer - what a paired device exposes and what it says.")

#: Which settings section holds the switch. `bluetooth-gatt-enabled` is a
#: privacy switch, so this is `privacy` - the same target `devices.py` and
#: `phone.py` carry. A panel with no route here gets no gear on its row, and the
#: rule is that the gear opens the page governing this panel's subject rather
#: than a page of unrelated switches.
SETTINGS_TARGET = "privacy"

STATE_PERMITTED = "permitted"
STATE_REFUSED = "not permitted"
STATE_UNREADABLE = "adapter not answering"
STATE_NONE_PAIRED = "nothing paired"

#: Three of the four states are "could not determine", and that is the honest
#: reading: none is a fault in the machine, and none is a healthy reading
#: either. A panel that reported a missing adapter as "nothing wrong" would be
#: making a claim about a device it never reached.
HEADLINE = {
    STATE_PERMITTED: "Paired devices",
    STATE_REFUSED: "Reading a Bluetooth device is turned off",
    STATE_UNREADABLE: "Bluetooth did not answer",
    STATE_NONE_PAIRED: "No Bluetooth devices are paired",
}

HEALTH = {
    STATE_REFUSED: common.STATUS_OFF,
    STATE_UNREADABLE: common.STATUS_UNKNOWN,
    STATE_NONE_PAIRED: common.STATUS_UNKNOWN,
}

BODY = {
    STATE_PERMITTED: "",
    # **No "Enable the switch in Settings".** The banner above names the
    # switch and turns it on from here.
    STATE_REFUSED: (
        "Nothing was read, and nothing on this page came from a device."),
    STATE_UNREADABLE: (
        "The adapter did not answer, so whether anything is paired is "
        "unknown - which is not the same as nothing being paired."),
    STATE_NONE_PAIRED: (
        "Pair a device from your desktop's own Bluetooth panel first. A device "
        "has to already be known to this computer before this protocol can ask "
        "it anything."),
}

#: state -> one clause for the status row, and **not** `BODY`. The row and the
#: empty state below it are two places saying one thing, and putting the
#: paragraph in both prints it twice on one screen.
HEALTH_SUMMARY = {
    STATE_REFUSED: "the switch is off",
    STATE_UNREADABLE: "the adapter did not answer",
    STATE_NONE_PAIRED: "nothing is paired",
}

#: States a person can act on, and where. A gate with nothing to fix offers no
#: button, because a button that opens an unrelated page is worse than none.
ROUTES = {
    STATE_REFUSED: ("Open Privacy settings", "privacy"),
    STATE_NONE_PAIRED: ("Open Bluetooth settings", "privacy"),
}

#: How many characteristics one device gets before the panel says it is showing
#: a subset. Each is a `gatttool` round trip, so this is a budget for the reader
#: as much as for the screen.
MAX_ROWS = 24

#: How long the one live reading collects for, in seconds. The module's own
#: default, kept rather than chosen: a different number here would be a second
#: answer to a question the assistant already answers with this one.
LISTEN_SECONDS = 10


@dataclass(frozen=True)
class State:
    """The panel's own answer when there is no list to show.

    A property on the state rather than a word chosen at each call site, because
    the word is the claim and the state is the fact - and two places deciding it
    are two things that can disagree. `build()` reads it both to draw the status
    row and to record the sidebar's dot, so those cannot drift apart.
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
        """The generic explanation, plus what *this* instance was refused for.

        The generic part alone is not enough, and losing the specific reason is
        how "these settings cannot answer" and "the switch says no" end up
        printed identically - two different facts about the machine wearing one
        sentence.
        """
        generic = BODY.get(self.state, "")
        # **A reason that names the switch is the banner's sentence.** The
        # banner above names it, says it is off and turns it on, so the
        # skill's own refusal - which says the same thing - would be a
        # second copy of it. A reason that does *not* name it is a
        # different fact ("this app has no settings to check it in"), and
        # that one is kept.
        if self.why and gate_label("bluetooth-gatt-enabled") in self.why:
            return generic or self.headline
        if not self.why or self.why in generic:
            return generic or self.why or self.headline
        return f"{generic}\n\nThis time: {self.why}" if generic else self.why


def _row(title: Any, subtitle: Any = "",
         suffix: Optional[Gtk.Widget] = None) -> Gtk.Widget:
    """One row, filled in at construction and never patched afterwards.

    **Not pre-escaped.** `common.row()` escapes both the title and the subtitle
    itself - it switched to doing so on 2026-10-11, because `Adw.ActionRow`'s
    title *and* subtitle are both Pango markup - so escaping here as well renders
    a device named `Ann & Bob` as `Ann &amp; Bob`. `devices.py` records having
    shipped exactly that, which is why this comment exists rather than the
    absence of one.
    """
    return common.row(str(title), str(subtitle), suffix)


# --- a MoYoung ("Da Fit") watch, which is a different protocol --------------
#
# **Moved here from the Phone surface, 2026-10-13, and the two are genuinely
# different subjects.** `skills/watch.py` speaks Gadgetbridge's MoYoung protocol
# through `moyoung.py` - measured on an FB BGS002 - while everything above this
# comment is `bluetooth_gatt`. A watch that supports neither can still be read
# over GATT for its battery and firmware; a watch that supports MoYoung can be
# measured, messaged and synced through it. Keeping one on each side of the
# console was the arrangement before, and it meant the only page that could set
# `watch-companion-enabled` was not the page the switch is about.
#
# `watch-companion-enabled` came with it deliberately: it is a setting about how
# the *phone* keeps hold of the watch, and the switch's own note says the Da Fit
# app on the phone cannot reach the watch while it is on. Leaving it behind would
# have meant a user opening the phone page to change a watch setting.
COMPANION_KEY = "watch-companion-enabled"

#: The watch itself is drawn by `watch_dashboard.WatchDashboard` (activity
#: ring, sleep stages, stress line, measurement tiles); the nine buttons that
#: printed sentences into one label are gone.


def _in_thread(work: "Callable[[], Any]", done: "Callable[[Any], None]") -> None:
    """Run `work` off the main thread and hand its result, or the exception, to
    `done`. Copied rather than imported: these are module-private in `phone.py`
    and that surface is becoming the phone's, so reaching across for its helpers
    would couple the two panels over four lines of plumbing.
    """
    def run() -> None:
        try:
            result = work()
        except Exception as exc:  # noqa: BLE001 - shown on the row, not raised
            logger.warning("wearables surface: %s", exc, exc_info=True)
            result = exc
        GLib.idle_add(lambda: (done(result), False)[1])
    threading.Thread(target=run, daemon=True).start()


def _tool(name: str, arguments: dict) -> str:
    """The app's own tool dispatcher - the same one the assistant goes through.

    Not the skill module: `phone.py` calls `tools.execute_tool` for `watch`,
    `find_device` and `bluetooth_gatt`, so the panel and the assistant ask the
    same question of the same code and get the same refusal.
    """
    from shani_chronoa import tools
    return str(tools.execute_tool(name, arguments))


def _label(text: str, dim: bool = False) -> Gtk.Label:
    lab = Gtk.Label(label=text, xalign=0.0, wrap=True, selectable=False)
    lab.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if dim:
        lab.add_css_class("dim-label")
    return lab


def _button(label: str, on_click: "Callable[[], Any]", suggested: bool = False
            ) -> Gtk.Button:
    btn = Gtk.Button(label=label)
    if suggested:
        btn.add_css_class("suggested-action")
    btn.connect("clicked", lambda _b: on_click())
    btn.set_valign(Gtk.Align.CENTER)
    return btn


def _watch_call(name: str, arguments: dict, note: Gtk.Label) -> None:
    """One `watch` action, and the sentence that says it is still working."""
    slow = arguments.get("action") == "measure"
    note.set_text("Measuring - keep the watch on and still, up to a minute..."
                  if slow else "Asking the watch...")
    _in_thread(lambda: _tool("watch", {**arguments, "device": name}),
               lambda r: note.set_text(
                   str(r).split(" (unverified")[0]))


def _moyoung_watches() -> List[Tuple[str, str]]:
    """Paired BLE devices that are **not phones**.

    The exclusion is not cosmetic: `phone.py` lists the phone over the same
    protocol, and a device appearing on both pages would be a phone described as
    a band. `phone_bluez.phones()` is what tells the two apart, so the filter
    uses it rather than a name heuristic.
    """
    from shani_chronoa import phone_bluez
    from shani_chronoa.skills import bluetooth_gatt as bg
    phones = {a for a, _n, _c in phone_bluez.phones()}
    return [(mac, name) for mac, name in bg.paired()
            if bg.looks_like_low_energy(mac) and mac not in phones]


def _companion_row(config: Any, note: Gtk.Label) -> Gtk.Widget:
    """The `watch-companion-enabled` switch, with its consequence on the row.

    **A failed write puts the switch back.** A switch left showing a state the
    user did not choose is a page asserting something untrue, so a refused write
    reverts the switch and says why.

    **The guard is the value the config actually holds, not a flag around the
    revert.** The first version set `sw._reverting = True`, called
    `set_active()`, and cleared the flag again - and it looped until the window
    froze, because the echo notification arrives *after* the flag is cleared.
    Measured, with a config that refuses every write:

        entry 1  wanted=True  _reverting unset -> write refused -> revert
        entry 2  wanted=False _reverting=False -> guard sees nothing -> revert
        entry 3  wanted=True  _reverting=False -> ... unbounded

    The flag guarded a window the notification never fell inside, so every entry
    saw it already cleared. `phone.py` had the same guard at line 733 before the
    watch half moved out of it, so this is a defect the move carried rather than
    one it caused.

    Comparing against the value **the config really has** cannot loop: the
    revert leaves the switch showing that same value, so its echo is recognised
    as an echo and ignored. A toggle to any other value still writes. Measured
    after the change: 2 entries, switch back at `False`, reason shown.
    """
    box = Gtk.Box(spacing=8)
    granted = _on(config)
    switch = Gtk.Switch(active=granted, valign=Gtk.Align.CENTER)
    text = _label(
        "Keep my watch connected to this computer - its find-my-phone, music "
        "and camera buttons then work here. The Da Fit app on your phone "
        "cannot reach the watch while this is on.", dim=True)
    text.set_hexpand(True)
    box.append(text)
    box.append(switch)

    #: The value the config is known to hold. Set before the handler is
    #: connected, so the first echo already has something to compare against.
    switch._granted = granted  # type: ignore[attr-defined]

    def toggled(sw: Gtk.Switch, _pspec: Any) -> None:
        wanted = bool(sw.get_active())
        if wanted == getattr(sw, "_granted", False):
            # The switch is showing what the config already holds: this is the
            # echo of a change that was refused, not a new request.
            return
        try:
            config.set(COMPANION_KEY, "true" if wanted else "false")
        except Exception as exc:  # noqa: BLE001 - shown, and the switch put back
            note.set_text(f"Could not change it: {exc}")
            sw.set_active(not wanted)  # `_granted` unchanged, so the echo stops
        else:
            sw._granted = wanted  # type: ignore[attr-defined]

    switch.connect("notify::active", toggled)
    return box


def _on(config: Any) -> bool:
    """Whether a privacy switch is granted, treating an unreadable one as off."""
    try:
        return bool(config.get_bool(COMPANION_KEY, False))
    except Exception:  # noqa: BLE001 - an unreadable switch is not a granted one
        return False


def _app_config(app: Any) -> Any:
    """The app's `ChronoaConfig`, or `None` when there is none.

    An app with no config still builds a page - the not-permitted one, because a
    permission that could not be checked is not a granted one.
    """
    return getattr(app, "config", None)


def _permission(config: Any) -> State:
    """The gate, asked through the module's own helper.

    **`gatt._consent`, not an inline `config.get_bool`.** That helper is what
    `test_skill_gates_are_enforced.py` looks for by reading the module's source,
    and it returns the refusal sentence the assistant would have returned - so
    the panel and the assistant cannot come to disagree about what the switch
    says, or drift apart when that sentence is reworded.
    """
    if config is None:
        return State(state=STATE_REFUSED,
                     why="This app has no settings to check the switch in.")
    allowed, problem = gatt._consent(config)
    if not allowed:
        return State(state=STATE_REFUSED, why=problem)
    return State(state=STATE_PERMITTED)


def _paired() -> "Tuple[List[Tuple[str, str]], Optional[State]]":
    """Every paired device as `(mac, name)`, or the state saying why not.

    `paired()` shells out and can raise anything the subprocess raises;
    `GattUnavailable` is a `RuntimeError`. Both mean one thing to a reader - the
    adapter did not answer - and neither is the machine's fault, so they are one
    state rather than two. "Bluetooth did not answer" and "nothing is paired"
    are different facts and do get different sentences.
    """
    try:
        devices = list(gatt.paired())
    except Exception:  # noqa: BLE001 - any refusal is the same answer here
        return [], State(state=STATE_UNREADABLE)
    if not devices:
        return [], State(state=STATE_NONE_PAIRED)
    return devices, None


def _wearable_kinds(mac: str) -> List[str]:
    """Which of `WEARABLE_SERVICES` this device actually advertises.

    Matched on the module's own `uuid_name`, because `WEARABLE_SERVICES` is
    keyed by the short UUID form and a device advertises the long one. An
    unreadable service list returns `[]` - "we could not tell" is not "it is
    not a wearable", and the two would otherwise print identically.
    """
    try:
        rows = gatt.services(mac)
    except Exception:  # noqa: BLE001 - a probe is not worth a traceback
        return []
    advertised = {gatt.uuid_name(uuid).lower() for _, uuid, _ in rows}
    return [name for short, name in sorted(gatt.WEARABLE_SERVICES.items())
            if gatt.uuid_name(gatt._BASE.format(short)).lower() in advertised]


def _device_row(mac: str, name: str) -> Gtk.Widget:
    """One paired device: its name, its address, and what it looks like.

    **A capability, never a measurement.** Nothing has been read from the device
    at this point, so nothing on this row may be a battery figure or a
    measurement - the value rows are added by `build()` only after a read.
    """
    kinds = _wearable_kinds(mac)
    subtitle = mac
    if kinds:
        subtitle = f"{mac}  -  exposes {', '.join(kinds)}"
    return _row(name, subtitle)


def _value_row(label: str, raw: str) -> Gtk.Widget:
    """One decoded value, or the refusal the module gave instead.

    `describe_value()` is the module's own decoder and it never invents a name
    for a UUID it does not know. What it cannot decode comes back as raw hex and
    says so - a plausible-looking value is worse than an unreadable one on a
    body measurement, so this shows the module's own answer rather than
    interpreting it again.
    """
    value, note = gatt.describe_value(label, raw)
    subtitle = value if not note else f"{value}  -  {note}"
    return _row(label or "Value", subtitle)


def _listen_row(label: str, values: List[str], problem: str) -> Gtk.Widget:
    """What the device pushed while the panel listened, or why nothing arrived.

    **An empty collection is a result and says so.** Nothing was pushed because
    the device was not worn, not in range, or not subscribed to that
    characteristic - three different causes and one honest answer, which is that
    it pushed nothing in the time given. Rendering that as a blank row would
    read as a rendering failure.
    """
    if problem:
        return _row(label or "Live reading", problem)
    if not values:
        return _row(label or "Live reading",
                    f"Nothing was pushed in {LISTEN_SECONDS}s. The device has "
                    f"to be worn and in use for this one, and it has to "
                    f"advertise it. Nothing is wrong with this panel.")
    return _row(label or "Live reading", ", ".join(str(v) for v in values))


def _device_detail(mac: str, name: str) -> Gtk.Widget:
    """Everything this panel can ask one device, in the module's own order.

    Services first, then characteristics, because that is the order the
    assistant's `services` answer uses and a person reading both should not find
    two different shapes. A device that will not answer is **one empty answer
    here**, not an empty panel: the refusal lands under the device's name, so
    the list of paired devices stays true.

    **Read on a thread, drawn on the loop.** This ran `gatttool` (services,
    then characteristics) straight from the idle callback that builds the list,
    so with a real watch nearby the whole window froze for as long as gatttool
    took to connect - measured 2026-10-15, a window test hung past its 300s cap
    inside `gatt.services` on the main thread.
    """
    column = common.page_body(8)
    column.append(_row(name, mac))
    reading = _row("Reading", "asking the device what it offers…")
    column.append(reading)

    def work():
        try:
            svc_rows = list(gatt.services(mac))
        except Exception as exc:  # noqa: BLE001 - the refusal is the answer
            return ("services", exc, None, None)
        kinds = _wearable_kinds(mac)
        try:
            chars = list(gatt.attributes(mac))
        except Exception as exc:  # noqa: BLE001
            return ("chars", exc, svc_rows, kinds)
        values = {}
        for handle, uuid, _kind in chars[:MAX_ROWS]:
            label = gatt.uuid_name(uuid)
            # **A characteristic the device will not return on a plain read is
            # not read at all.** The module names those (`_NOTIFY_ONLY` is its
            # own list and its own reason for each), and the row below would
            # throw the value away anyway - it shows the note and a Listen
            # button instead. Reading it first cost a gatttool connection per
            # characteristic and returned nothing, and the read is the slow
            # part: the whole point of this being on a thread is that a
            # connection to a real device takes seconds.
            if gatt._NOTIFY_ONLY.get(label) is not None:
                continue
            try:
                values[handle] = gatt.read_value(mac, handle, label, uuid)
            except Exception as exc:  # noqa: BLE001 - shown on its row
                values[handle] = exc
        return ("ok", (chars, values), svc_rows, kinds)

    def done(result) -> None:
        column.remove(reading)
        if isinstance(result, Exception):
            column.append(_row("Could not be read", str(result)))
            return
        stage, value, svc_rows, kinds = result
        if stage == "services":
            column.append(_row("Could not be read", str(value)))
            return
        column.append(_row("Looks like",
                           ", ".join(kinds) if kinds
                           else "No wearable service in the list it advertises"))
        column.append(_row("Services", _names(svc_rows) or "None advertised"))
        if stage == "chars":
            column.append(_row("Characteristics", f"Not read: {value}"))
            return
        chars, values = value
        if not chars:
            column.append(_row("Characteristics",
                               "None readable. This device advertises services "
                               "but gatttool listed no characteristics on it."))
            return
        shown = chars[:MAX_ROWS]
        for handle, uuid, kind in shown:
            column.append(_readable_row(mac, handle, uuid, column, values.get(handle)))
        if len(chars) > len(shown):
            # **Say how many were not shown.** A truncated list with no count
            # reads as the whole list.
            column.append(_row(
                f"{plural(len(chars) - len(shown), 'more characteristic')}",
                f"This panel shows the first {MAX_ROWS}. Ask the assistant for a "
                f"specific one by name."))

    _in_thread(work, done)
    return column


def _names(rows: list) -> List[str]:
    """The distinct friendly names of `(handle, uuid, kind)` rows."""
    seen: List[str] = []
    for _handle, uuid, _kind in rows:
        name = gatt.uuid_name(uuid)
        if name and name not in seen:
            seen.append(name)
    return seen


def _listen_button(label: str, mac: str, handle: int,
                   column: Gtk.Widget) -> Optional[Gtk.Widget]:
    """A Listen button, for a characteristic the device only ever pushes.

    **Only for the two the module names as notify-only.** A plain read on either
    answers `Attribute can't be read` - measured on real hardware - so a panel
    offering Read alone would show a dead end for the one thing a heart-rate
    strap exists to provide. `_NOTIFY_ONLY` is the module's own list and its own
    reason for each, so the button's tooltip quotes that rather than explaining
    it a second time.

    The collect blocks for `LISTEN_SECONDS`, so it runs on a thread and the
    result is appended when it lands - the same shape as every other slow read
    here, and for the same reason.
    """
    why = gatt._NOTIFY_ONLY.get(label)
    if why is None:
        return None
    button = Gtk.Button(label=f"Listen ({LISTEN_SECONDS}s)",
                        valign=Gtk.Align.CENTER)
    button.set_tooltip_text(
        f"{label}: {why}. The device has to be worn and in use - it pushes "
        f"nothing otherwise, which is an answer about the device rather than a "
        f"failure here.")

    def _landed(values: List[str], problem: str) -> bool:
        button.set_sensitive(True)
        button.set_label(f"Listen ({LISTEN_SECONDS}s)")
        column.append(_listen_row(label, values, problem))
        return False

    def on_clicked(_b: Gtk.Widget) -> None:
        button.set_sensitive(False)
        button.set_label("Listening\u2026")

        def work(_d: Any = None) -> None:
            try:
                values, problem = gatt.notifications(mac, handle,
                                                     LISTEN_SECONDS)
            except Exception as exc:  # noqa: BLE001 - reported on the row
                values, problem = [], str(exc)
            GLib.idle_add(_landed, values, problem)

        GLib.Thread.new("chronoa-wearables-listen", work, ())

    button.connect("clicked", on_clicked)
    return button


def _readable_row(mac: str, handle: int, uuid: str,
                  column: Optional[Gtk.Widget] = None, preread: Any = None) -> Gtk.Widget:
    """One characteristic, with its stored value read through the module.

    The read is `read_value()`'s, and the value handle is the one
    `attributes()` returned precisely because that is what `--char-read -a`
    needs. A failure is a sentence on that row and not a row that says the
    device has nothing - "not readable" and "not there" are different answers.
    """
    label = gatt.uuid_name(uuid)
    listen = (_listen_button(label, mac, handle, column)
              if column is not None else None)
    if listen is not None:
        # A notify-only characteristic has no stored value to read, so this row
        # is the button and the module's own reason - not an empty read that
        # looks like a device with nothing to say.
        row = _row(label, "Live only - it is not stored, it is pushed.")
        row.add_suffix(listen)
        return row
    try:
        # `preread` is the value read on `_device_detail`'s thread; reading here
        # would run gatttool on the GTK loop.
        if isinstance(preread, Exception):
            raise preread
        raw, problem = preread if preread is not None else gatt.read_value(mac, handle, label, uuid)
    except Exception as exc:  # noqa: BLE001 - one unreadable value is not a page
        return _row(label, f"Could not be read: {exc}")
    if problem:
        return _row(label, problem)
    if not raw:
        return _row(label, "No stored value - the device publishes this one "
                          "and holds nothing for it.")
    return _value_row(label, raw)


def _append_moyoung(listing: Gtk.Widget, app: Any) -> None:
    """The MoYoung watch section, appended after the general GATT listing.

    On its own thread because `paired()` runs again here - it is the one list
    that cannot come out of the GATT read above, since the exclusion needs
    `phone_bluez` to say which devices are phones.
    """
    note = _label("", dim=True)
    holder = common.page_body(6)
    listing.append(holder)

    def done(result: Any) -> None:
        if isinstance(result, Exception):
            holder.append(_row("Could not list watches",
                               f"Could not list watches: {result}"))
            return
        watches, problem = result
        if problem:
            holder.append(_row("Watches", problem))
            return
        holder.append(_row("Watches",
                           f"{len(watches)} MoYoung (Da Fit) watch or watches"
                           if watches else
                           "No MoYoung (Da Fit) watch is paired. Anything "
                           "else paired above is still read over GATT - the "
                           "measurements, messages and settings below are one "
                           "vendor's protocol, not the general one."))
        if not watches:
            return
        holder.append(_companion_row(_app_config(app), note))
        # **(mac, name) pairs.** This loop used to hand each whole pair to
        # `_moyoung_card(name)` as if it were the name - never seen, because no
        # MoYoung watch was ever listed: it does not bond, and `paired()` only
        # listed bonded devices until it learned Trusted (2026-10-14).
        from shani_chronoa.gui.surfaces.watch_dashboard import WatchDashboard, find as watch_find
        for mac, name in watches:
            card_note = _label("", dim=True)
            dashboard = WatchDashboard(mac, name, actions=[
                # MoYoung's own find (cmd 97): the generic `find_device`
                # needs Immediate Alert, which this watch does not have.
                ("Find", "find-location-symbolic",
                 lambda m=mac, l=card_note: watch_find(m, l.set_text)),
                ("Sync time", "preferences-system-time-symbolic",
                 lambda n=name, l=card_note: _watch_call(n, {"action": "sync_time"}, l)),
                ("Settings", "emblem-system-symbolic",
                 lambda n=name, l=card_note: _watch_call(n, {"action": "settings"}, l)),
            ])
            holder.append(dashboard)
            text = Gtk.Entry(hexpand=True, placeholder_text="Message to show on the watch")
            message = Gtk.Box(spacing=6)
            message.append(text)
            message.append(_button("Send to watch", lambda n=name, t=text, l=card_note: _watch_call(
                n, {"action": "notify", "sender": "Chronoa", "text": t.get_text()}, l)))
            holder.append(message)
            holder.append(card_note)
            dashboard.refresh()
        holder.append(note)

    def work() -> Any:
        try:
            return _moyoung_watches(), ""
        except Exception as exc:  # noqa: BLE001 - reported on the row
            return [], str(exc)

    _in_thread(work, done)


def build(app) -> Gtk.Widget:
    """The paired LE devices for `app`, asked once at build time.

    Asked once, and never while the gate is shut - a permission that could not
    be checked is not a granted one, so a missing config builds the refused page
    rather than an empty one.
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
        """An empty state, *with* the status row that matches its own word.

        Every gate ends here, and each is a different answer. Each used to return
        the empty state alone, which left the sidebar's dot asserting a word
        nothing on screen said - caught on the sibling panel by
        `tests/test_surface_health_status.py`. So the row is drawn here rather
        than only on the happy path.
        """
        column = common.page_body(18)
        column.set_margin_top(12)
        column.append(recorder.row(state.health, state.headline,
                                   HEALTH_SUMMARY.get(state.state, "")))
        route = ROUTES.get(state.state)
        if route is not None:
            label, target = route
            # The switch by its Settings name, turned on here - see
            # `common.gate_banner`.
            column.append(common.gate_banner(
                app, "bluetooth-gatt-enabled" if state.state == STATE_REFUSED else None,
                "wearables", label, target))
        column.append(common.empty_state(
            ICON, state.headline, state.body,
            child=(common.connect_button(app, "bluetooth", "Pair a watch or band")
                   if state.state == STATE_NONE_PAIRED else None)))
        return common.scrolled(column)

    refusal = _permission(_app_config(app))
    if refusal.state != STATE_PERMITTED:
        recorder.set(refusal.health)
        set_content(gated(refusal))
        return page

    # Everything past here shells out. The page is built first and the reads
    # hand their results back through `GLib.idle_add`, so a slow adapter delays
    # the rows and never the window.
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
        common.STATUS_UNKNOWN, "Reading paired devices",
        "asking the machine what is paired")
    column.append(interim)

    def deliver(widget: Gtk.Widget, health: str, headline: str,
                summary: str) -> None:
        column.append(widget)
        recorder.set(health)
        column.append(recorder.row(health, headline, summary))
        column.remove(interim)

    def when_ready(devices: list) -> None:
        listing = common.page_body(8)
        listing.append(_row("Paired devices",
                            f"{len(devices)} paired with this computer"))
        for mac, name in devices:
            listing.append(_device_row(mac, name))
            listing.append(_device_detail(mac, name))
        # **The MoYoung half second**, because it is a different protocol and a
        # device that supports both appears twice - once as a GATT device and
        # once as a watch with measurements. Reading them in one order rather
        # than the other would be arbitrary; this order is the general case
        # first, so a strap that is not a MoYoung watch is not made to look like
        # one by sitting under a heading that says "MoYoung".
        _append_moyoung(listing, app)
        deliver(listing, common.STATUS_OK, "Paired devices",
                f"{len(devices)} paired")

    def failed(state: State) -> None:
        deliver(gated(state), state.health, state.headline,
                HEALTH_SUMMARY.get(state.state, ""))

    def on_paired() -> None:
        devices, problem = _paired()
        if problem is not None:
            GLib.idle_add(failed, problem)
            return
        GLib.idle_add(when_ready, devices)

    GLib.Thread.new("chronoa-wearables-paired", lambda _d: on_paired(), ())
    return page