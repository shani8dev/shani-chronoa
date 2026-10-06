"""The Devices surface: the phone and the paired devices Chronoa already talks to.

This panel adds no transport. Everything on it comes out of two modules that
already exist and already run somewhere else in the app:

- `shani_chronoa/phone.py` - GSConnect's D-Bus ObjectManager on GNOME,
  `kdeconnect-cli` plus `busctl` for the battery on Plasma. `backend()`,
  `devices()`, `battery()`.
- `shani_chronoa/triggers/desktop_sources.py`'s `read_phone()` - the `phone`
  trigger, which watches the same devices for connect / disconnect / low battery.

No SMS, no notification reading, no remote input, and no bluetooth. The first
three are `phone.py`'s own deliberate omissions; bluetooth is simply not read by
it - KDE Connect happens to run over bluetooth underneath, but nothing in
`phone.py` asks `bluetoothctl` for anything, and a panel listing BT devices would
be answering through a mechanism that is not wired to this one.

**Four different things can be true here, and the panel says which.** This is the
whole point of the module: `phone.py`'s own docstring calls out that "no service,
no paired device and no reachable device are three different answers", and a
fourth sits above all of them - the permission is not granted. Collapsing any two
is how a panel ends up confidently wrong, so each gets its own message and none
of them is rendered as an empty list:

| state | what it means | what the panel shows |
|---|---|---|
| `not permitted` | `phone-control-enabled` is off | the refusal, the key, and where it is granted |
| `no phone link` | neither GSConnect nor KDE Connect is installed | the same refusal `phone.py` would raise, naming both backends |
| `link not answering` | a backend is installed but will not answer | the error the link itself gave |
| `no device paired` | the link answered and has nothing paired | that, and not an empty list |

**The permission is checked before anything is asked, and nothing is asked while
it is off.** `skills/phone.py` refuses before it calls `ph.devices()`, and the
`machine` surface's rule is that a sense whose permission is off is never invoked
at all. This panel holds to the same rule: with the gate shut it calls neither
`devices()` nor `battery()` nor `read_phone()`. So the not-permitted state is
also the cheapest one - a `get_bool` and a `which` - and a panel that asked
anyway would be doing the thing the gate exists to prevent.

The check **fails closed**: no settings, a settings object that cannot answer,
or a check that raises are all reported as not permitted rather than as permitted.
An unverified permission is not a granted one, and reporting it the other way
round is a panel that tells the user their phone is off-limits when it is not.

**What a device row says, and where each word came from.** `phone.Device` is
`id, name, reachable, paired` - there is no device-*type* field, so this panel
does not claim to know whether a thing is a handset, a tablet or a laptop. Its
"kind" line says what the link actually reports: paired or not, reachable or not.
Battery is `phone.battery()`, which returns `None` wherever the backend reports
none, and which is asked only of a reachable device for the same reason
`skills/phone.py` asks only of a reachable one - an unreachable phone's last
battery reading is not a current one, and showing it beside a fresh timestamp
would make the timestamp lie.

**Every row is stamped with when it was read.** The panel reads once, at build
time, exactly as `surfaces/machine.py` does, and says so. A device list left open
overnight is then visibly a list of yesterday's, rather than a live one that
quietly stopped updating. "The last thing Chronoa knew" is this read and nothing
else: `phone.py` keeps no cache and no history, so there is no older answer to
show and pretending to one would be inventing provenance.

**Escaping is load-bearing, and both row shapes want opposite treatment.**
Measured on libadwaita 1.5 rather than assumed:

- `Adw.PreferencesRow.use-markup` defaults to **True**, and `Adw.StatusPage`'s
  `description` is markup too. Handed raw, a device named `Ann & <b>Bob</b>
  phone` does not render wrong - it renders as **nothing at all**, with only a
  `Gtk-WARNING` about an unterminated entity to say so.
- The no-Adw fallback is `Gtk.Label(label=...)`, which takes no markup, so
  escaping there would put a literal `&amp;` on screen.

So every string is escaped on exactly the condition `common.row()` itself uses to
choose the shape, and nothing is filled in afterwards: `Gtk.Box` has no
`set_subtitle` and no `add` (measured), so a row built and then patched would
raise on the fallback path and take the window with it.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk  # noqa: E402

from shani_chronoa import markdown_lite  # noqa: E402
from shani_chronoa import phone as ph  # noqa: E402
from shani_chronoa.gui.surfaces import common  # noqa: E402
from shani_chronoa.triggers import desktop_sources  # noqa: E402
from shani_chronoa.triggers.common import SIGNAL_OK  # noqa: E402

logger = logging.getLogger(__name__)

TITLE = "Devices"
ICON = "phone-symbolic"
SECTION = "This machine"
SUBTITLE = ("The phone and the devices paired with this machine, through the "
            "desktop's own phone link.")

#: The gate. Off by default, and checked here before anything is asked of the
#: link - the same key, and the same refusal, as `skills/phone.py`.
CONSENT_KEY = "phone-control-enabled"

#: What the `phone` *trigger* may watch. A different key on purpose:
#: `phone-sense-enabled` lets a rule act on the phone connecting and says nothing
#: about Chronoa being allowed to use the phone. This panel names both and never
#: treats one as the other.
TRIGGER_KEY = "phone-sense-enabled"

#: The `phone` trigger source this panel reads. `connected` with no name covers
#: every paired device, so it is one read rather than one per row.
TRIGGER_SOURCE = "connected"

#: The four states in which there is no list to show, plus the one there is.
#: Declared rather than inferred from the wording, so a caller can tell "one of
#: ours" from a state this panel invented.
STATE_PERMITTED = "permitted"
STATE_REFUSED = "not permitted"
STATE_NO_LINK = "no phone link"
STATE_LINK_DOWN = "link not answering"
STATE_NONE_PAIRED = "no device paired"

STATES = (
    STATE_PERMITTED,
    STATE_REFUSED,
    STATE_NO_LINK,
    STATE_LINK_DOWN,
    STATE_NONE_PAIRED,
)

#: state -> the headline it is shown under, and the body under that. One pair per
#: state, so the three messages this panel is judged on cannot drift apart from
#: each other or from the state names above.
HEADLINE = {
    STATE_REFUSED: "Using your phone is turned off",
    STATE_NO_LINK: "No phone link is installed",
    STATE_LINK_DOWN: "The phone link did not answer",
    STATE_NONE_PAIRED: "No device is paired",
}

#: state -> the status word it is, beside `State.health`. `STATE_PERMITTED` is
#: absent deliberately: it is not a gate, it is the absence of one, and the
#: health of a panel that has devices to show comes from that count instead.
HEALTH = {
    STATE_REFUSED: common.STATUS_ATTENTION,
    STATE_NO_LINK: common.STATUS_UNKNOWN,
    STATE_LINK_DOWN: common.STATUS_UNKNOWN,
    STATE_NONE_PAIRED: common.STATUS_UNKNOWN,
}

#: state -> one clause for the status row, and **not** `BODY`.
#:
#: The row and the empty state below it are two places saying one thing, and the
#: obvious implementation - put the body in both - prints the same paragraph twice
#: on the same screen, which is what a rendered window showed before this line
#: existed: the status row carried all four lines of explanation, then the empty
#: state carried them again, with the route banner wedged between.
#:
#: So the row gets the *reason the word is that word* and the empty state gets the
#: full explanation. One clause above, one paragraph below, and the paragraph is
#: read once.
HEALTH_SUMMARY = {
    STATE_REFUSED: f"'{CONSENT_KEY}' is off, so nothing about the phone was asked",
    STATE_NO_LINK: "neither GSConnect nor kdeconnect-cli is installed here",
    STATE_LINK_DOWN: "a backend is installed but would not answer",
    STATE_NONE_PAIRED: "the link answered, with nothing paired to it",
}

#: state -> (button label, the `pages.show` target that fixes it). Only for the
#: gates a person can act on.
#:
#: **A table, and only where there is something to open.** Two of the four
#: states are absent on purpose: a machine with no phone link needs a package
#: installed, not a setting flipped, and nothing paired needs a phone paired.
#: Both of those still say exactly what to do in their body text. Only the
#: refused gate has a switch behind it, so only the refused gate gets a button -
#: a button on the others would open Settings and change nothing, which is a
#: worse dead end than the sentence it replaced.
ROUTES = {
    STATE_REFUSED: ("Open Privacy settings", "settings:privacy"),
}

BODY = {
    STATE_REFUSED: (
        "Nothing on this panel was asked of your phone link, because "
        f"'{CONSENT_KEY}' is off. That is the same gate the 'phone' and "
        "'android_device' skills refuse behind, so a shut gate stops this panel "
        "as well as the assistant. Grant it in Settings, under Privacy."
    ),
    STATE_NO_LINK: (
        "This machine has neither backend phone.py knows about: no GSConnect (the "
        "GNOME Shell extension, which also needs its 'gjs' runtime) and no "
        "kdeconnect-cli (Plasma). That is a machine with no phone link, not a "
        "machine with no phone."
    ),
    STATE_LINK_DOWN: (
        "A backend is installed but would not answer, which is a different fact "
        "from no backend and a different fact again from a paired device. On GNOME "
        "that usually means the GSConnect extension is switched off."
    ),
    STATE_NONE_PAIRED: (
        "The link answered and has nothing paired to it. Pair a phone in "
        "GSConnect or KDE Connect, or with kdeconnect-cli on Plasma, and it "
        "appears here."
    ),
}

#: Css class on every row, so a test finds rows by walking the built widget
#: rather than off a list this module stashed on itself. See AGENTS.md: a count
#: read back out of the thing being counted is how fifteen assertions once passed
#: against a window that rendered nothing.
ROW_CSS = "devices-row"
#: On the device rows alone, so "one row per paired device" can be counted without
#: also counting the backend row and the trigger row, which share `ROW_CSS`.
DEVICE_CSS = "devices-device"
WHEN_CSS = "devices-when"

#: What each backend is called to a person, rather than by the module's own slug.
BACKEND_TITLE = {
    "gsconnect": "GSConnect (GNOME)",
    "kdeconnect": "KDE Connect (Plasma)",
}

#: Longest error text shown. `phone.py` truncates its own messages; this is for
#: the ones it raises from a subprocess, and the cut says how much is missing
#: rather than letting a truncated answer read as the whole one.
MAX_DETAIL = 200

_FOOTER = (
    "Read once when this panel was built; rebuild it to ask again. Chronoa "
    "cannot send SMS, read the phone's notifications or drive its screen, and "
    "nothing here changes a permission: Senses, and the Privacy settings, are "
    f"where '{CONSENT_KEY}' is granted or taken away."
)


@dataclass(frozen=True)
class State:
    """The panel's own answer when there is no list to show."""

    state: str
    why: str = ""

    @property
    def health(self) -> str:
        """Which of the three status words this state is.

        **A property on the state rather than a word chosen at each call site**,
        because the word is the claim and the state is the fact - and a second
        place to decide it is a second thing that can disagree. `build()` reads
        it to record the sidebar's dot and to draw the row at the top of the
        gate, and those two then cannot differ: both come from here.

        Three of the four states are "could not determine". That is deliberate
        and it is the honest reading: none of them is a fault in the machine or
        in the pairing, and none of them is a healthy reading either - in each
        case the panel simply has no answer about the phone, which is not the
        same as "nothing is wrong with the phone".
        """
        return HEALTH.get(self.state, common.STATUS_UNKNOWN)

    @property
    def headline(self) -> str:
        return HEADLINE.get(self.state, self.state)

    @property
    def body(self) -> str:
        """The generic explanation for this state, plus whatever *this* instance
        was refused for.

        The generic part alone is not enough, and losing the specific reason is
        how "these settings cannot answer" and "the key says no" end up printed
        identically - two different facts about the machine wearing one sentence.
        """
        generic = BODY.get(self.state, "")
        if not self.why or self.why in generic:
            return generic or self.why or self.headline
        return f"{generic}\n\nThis time: {self.why}" if generic else self.why


def _text(value: Any) -> str:
    """`value` escaped for the row shape `common.row()` is about to build.

    `common.row()` branches on `common.adw_ready()` - an `Adw.ActionRow` when it
    is true, a plain `Gtk.Box` of `Gtk.Label`s when it is not - and that is the
    same condition used here rather than a proxy for it, because the two shapes
    want opposite treatments (see the module docstring). `common.empty_state()`
    branches on the same condition for the same reason.
    """
    return markdown_lite.escape(str(value)) if common.adw_ready() else str(value)


def _row(title: Any, subtitle: Any = "") -> Gtk.Widget:
    """One row, filled in at construction and never patched afterwards.

    `Gtk.Box` has no `set_subtitle` and no `add` (measured on GTK 4), so building
    the row and then setting its subtitle raises on the fallback path - which is
    exactly the shape a headless builder gets.
    """
    widget = common.row(_text(title), _text(subtitle))
    widget.add_css_class(ROW_CSS)
    return widget


def _stamp(read_at: float) -> Gtk.Widget:
    """The label saying when a row's reading was taken."""
    label = Gtk.Label(xalign=1.0)
    label.add_css_class(WHEN_CSS)
    label.add_css_class("dim-label")
    label.set_valign(Gtk.Align.CENTER)
    label.set_text(time.strftime("%H:%M:%S", time.localtime(read_at)))
    return label


def _app_config(app: Any) -> Any:
    """`app.config`, or None - including when reading the attribute raises.

    `getattr` with a default does not help if the property itself throws, and a
    property that throws while a window is being built is a real state.
    """
    try:
        return getattr(app, "config", None)
    except Exception:  # noqa: BLE001 - reported as not permitted by _permission
        logger.debug("devices surface: app.config raised", exc_info=True)
        return None


def _permission(config: Any) -> State:
    """`State(STATE_PERMITTED)`, or the refusal and why.

    Fails closed, and returns a state rather than leaving one to be read out of
    the wording: "no settings at all", "these settings cannot answer" and "the key
    says no" are three different facts and this panel shows all three. Never
    raises either - this is called once per build, and an exception here would
    take the window down rather than one panel.
    """
    if config is None:
        return State(STATE_REFUSED,
                     f"there are no settings in this window to check '{CONSENT_KEY}' with")
    reader = getattr(config, "get_bool", None)
    if not callable(reader):
        return State(STATE_REFUSED,
                     f"these settings cannot answer whether '{CONSENT_KEY}' is on")
    try:
        allowed = reader(CONSENT_KEY, False)
    except Exception as exc:  # noqa: BLE001 - an unreadable key is not a granted one
        return State(STATE_REFUSED,
                     f"'{CONSENT_KEY}' could not be checked "
                     f"({type(exc).__name__}: {str(exc)[:MAX_DETAIL]})")
    if not allowed:
        return State(STATE_REFUSED, f"'{CONSENT_KEY}' is off")
    return State(STATE_PERMITTED)


def _backend() -> Optional[str]:
    """`phone.backend()`, or None if there is none.

    `backend()` is a `which` over `kdeconnect-cli` plus a glob for GSConnect's
    daemon - no D-Bus and no subprocess - so a failure here is a filesystem that
    could not be searched, and reporting that as "no phone link" would be the
    confident wrong answer this panel exists to avoid. Such a failure is reported
    as an undetermined backend and the read is attempted anyway.
    """
    try:
        return ph.backend()
    except Exception as exc:  # noqa: BLE001 - the traceback, not an empty list
        logger.warning("devices surface: phone.backend() failed", exc_info=True)
        return f"undetermined ({type(exc).__name__}: {str(exc)[:MAX_DETAIL]})"


def _paired() -> Tuple[List[Any], Optional[State]]:
    """The paired devices, or the state saying why there are none.

    Unpaired entries are dropped rather than listed: `phone.devices()` returns
    everything the link has ever seen, and a row for a device the user has
    unpaired is a device Chronoa may not act on.
    """
    try:
        every = ph.devices()
    except ph.PhoneUnavailable as exc:
        return [], State(STATE_LINK_DOWN, str(exc)[:MAX_DETAIL])
    except Exception as exc:  # noqa: BLE001 - "failed" and "not answering" differ
        return [], State(STATE_LINK_DOWN,
                         f"{type(exc).__name__}: {str(exc)[:MAX_DETAIL]}")
    return [d for d in every if getattr(d, "paired", False)], None


def _kind(device: Any) -> str:
    """What the link actually reports about this device - not its type.

    `phone.Device` has no type field, so a row claiming "phone", "tablet" or
    "laptop" would be inventing one.
    """
    if not getattr(device, "paired", False):
        return "known to the link but not paired"
    if getattr(device, "reachable", False):
        return "paired, reachable right now"
    return "paired, not reachable (off, out of range, or on another network)"


def _battery(device: Any) -> str:
    """`phone.battery()` in words, including every way it can decline.

    Asked only of a reachable device, for the reason in the module docstring, and
    only where the backend reports one: `battery()` returns `None` on GSConnect
    and wherever `busctl` is absent, and no reading is not 0%.
    """
    if not getattr(device, "reachable", False):
        return "battery: not read, the device is not reachable right now"
    try:
        reading = ph.battery(device)
    except Exception as exc:  # noqa: BLE001 - a battery is not a reason to fail a row
        return (f"battery: could not be read "
                f"({type(exc).__name__}: {str(exc)[:MAX_DETAIL]})")
    if not reading:
        return "battery: this backend does not report one"
    percent, charging = reading[0], bool(reading[1])
    return f"battery: {percent}%{' and charging' if charging else ''}"


def _add_row(container: Gtk.Widget, row: Gtk.Widget) -> None:
    """Put `row` into a group built by `common.group()`.

    `Adw.PreferencesGroup` is a `Gtk.ListBox` and takes `add`; the plain
    `Gtk.Box` the no-Adw path returns takes `append` on GTK4, where `add` no
    longer exists (measured). One branch rather than a capabilities check,
    because the box is a box and the list box is not.
    """
    adder = getattr(container, "add", None)
    if callable(adder):
        adder(row)
    else:
        container.append(row)


def _revealed(notice: Gtk.Widget) -> Gtk.Widget:
    """Make a banner actually visible.

    `Adw.Banner` starts hidden - `revealed` is FALSE until something sets it - so
    a caller that appends one and stops has put nothing on screen while every
    assertion about the banner's *text* still passes. The plain-GTK box has no
    such property, so this is a no-op there rather than a special case.
    """
    setter = getattr(notice, "set_revealed", None)
    if callable(setter):
        setter(True)
    return notice


def _device_row(device: Any, read_at: float) -> Gtk.Widget:
    """One paired device: name, kind, battery, and when it was read.

    The name is the device's own and is untrusted text - it came off a D-Bus
    ObjectManager or `kdeconnect-cli` - which is why it goes through `_row`'s
    escaping rather than straight into a label.
    """
    name = getattr(device, "name", "") or getattr(device, "id", "") or "(unnamed)"
    subtitle = "\n".join((
        "kind: " + _kind(device),
        _battery(device),
        "last known at " + time.strftime("%H:%M:%S", time.localtime(read_at)),
    ))
    widget = common.row(_text(name), _text(subtitle), suffix=_stamp(read_at))
    widget.add_css_class(ROW_CSS)
    widget.add_css_class(DEVICE_CSS)
    return widget


def _trigger_row() -> Gtk.Widget:
    """The `phone` trigger, in its own words.

    `read_phone()` returns an `EventSignal`, and `status != SIGNAL_OK` is the
    module's own "signal unavailable" contract: a `None` fingerprint is not to be
    compared against a real one, because "unavailable" and "unchanged" then look
    alike. So an unavailable signal is shown with the reason the link gave, and
    never as "nothing happened".
    """
    title = f"phone:{TRIGGER_SOURCE}"
    try:
        signal = desktop_sources.read_phone(TRIGGER_SOURCE)
    except Exception as exc:  # noqa: BLE001 - a trigger that raises is a hole, not a crash
        logger.warning("devices surface: read_phone() failed", exc_info=True)
        return _row(title, f"the trigger raised {type(exc).__name__}: "
                           f"{str(exc)[:MAX_DETAIL]}")
    if getattr(signal, "status", "") == SIGNAL_OK:
        said = getattr(signal, "detail", "") or "(the trigger said nothing)"
    else:
        said = getattr(signal, "detail", "") or "the trigger could not answer"
    return _row(title, said)


def build(app) -> Gtk.Widget:
    """The paired devices for `app` - anything with a `config` will do.

    Asked once, at build time, and never while the gate is shut. An app with no
    settings still builds a page: the not-permitted one, because a permission
    that could not be checked is not a granted one.
    """
    page, set_content = common.surface(TITLE, SUBTITLE)
    #: One recorder for the panel's health, whether it reaches the rows below or
    #: returns an empty state at one of four gates. Every one of those returns
    #: used to hand back a page with no status at all - which is precisely the
    #: information the sidebar dot needs, since each of them is a different
    #: answer: not permitted, no link, could not read, and nothing paired.
    recorder = common.StatusRecorder()

    def gated(state: "State") -> Gtk.Widget:
        """An empty state, *with* the status row that matches its own word.

        Every one of the four gates below ends here, and each is a different
        answer - not permitted, no link, could not read, nothing paired. Each
        used to return the empty state alone, which left the sidebar's dot
        asserting a word that nothing on screen said: `tests/
        test_surface_health_status.py::test_the_dot_agrees_with_the_row_the_
        panel_shows` caught exactly that, on the not-permitted gate.

        So the row is drawn here rather than only on the happy path. The empty
        state still carries the prose; the row above it carries the one word,
        and both are read off the same recorder as the dot.
        """
        column = common.page_body(18)
        column.set_margin_top(12)
        # `recorder.row` - not `common.status_row`: this is the write that
        # decides the sidebar's dot, and a plain row here would leave the dot
        # reading whatever the previous panel said. The summary is the one-clause
        # reason, not the body - see `HEALTH_SUMMARY` for why the two places this
        # panel says the same thing must not say it identically.
        column.append(recorder.row(state.health, state.headline,
                                   HEALTH_SUMMARY.get(state.state, "")))
        # **A gate that can be opened says so with a button, not a sentence.**
        # The body for the refused gate used to end "Grant it in Settings, under
        # Privacy." - naming the switch and providing no way to reach it, which is
        # the dead end this panel existed to be the answer to. One place names
        # which page fixes it; a gate with nothing to fix offers nothing, because
        # a button that opens an unrelated page is worse than no button.
        route = ROUTES.get(state.state)
        if route is not None:
            label, target = route
            column.append(common.banner(
                "This is a setting on this machine, not a fault.",
                label,
                lambda _l=label, _t=target: common.open_page(app, _t)))
        column.append(common.empty_state(ICON, _text(state.headline),
                                         _text(state.body)))
        return common.scrolled(column)

    refusal = _permission(_app_config(app))
    if refusal.state != STATE_PERMITTED:
        # `gated()` records through `State.health`, so the word beside the
        # sidebar row and the row on screen are one value. Not permitted is
        # "needs attention" and not a clean reading: `senses.py` treats a refused
        # sense as working as designed, and the difference is that a sense's
        # refusal is still a *reading* of something, while here there is no
        # reading of the phone at all.
        set_content(gated(refusal))
        page.status = recorder.status
        return page

    which = _backend()
    if which is None:
        # No backend at all: the panel cannot tell whether anything is paired,
        # so this is "could not determine", not "nothing paired". Those read the
        # same in an empty state and mean opposite things in a dot.
        set_content(gated(State(STATE_NO_LINK)))
        page.status = recorder.status
        return page

    read_at = time.time()
    devices, problem = _paired()
    if problem is not None:
        set_content(gated(problem))
        page.status = recorder.status
        return page
    if not devices:
        # The link answered and there is genuinely nothing paired. That is a
        # clean answer about an empty thing, not a fault - but it is also not
        # "ready", so it is the one state where neither is the whole truth.
        set_content(gated(State(STATE_NONE_PAIRED)))
        page.status = recorder.status
        return page

    # The panel's own health, above the paired devices: are
    # devices paired, is the link up, or could it not be told?
    # One row, one dot, one word - the question the panel is
    # opened for, before the rows that hold the devices.
    body = common.page_body(18)
    body.set_margin_top(12)
    body.set_margin_bottom(12)
    body.append(recorder.row(
        common.STATUS_OK,
        f"{len(devices)} device(s) paired",
        f"read once at {time.strftime('%H:%M:%S', time.localtime(read_at))}"))
    body.append(_revealed(common.banner(
        f"{len(devices)} paired device{'' if len(devices) == 1 else 's'}, read once "
        f"at {time.strftime('%H:%M:%S', time.localtime(read_at))}. Rebuild this "
        "panel to ask again; nothing here is polled in the background."
    )))

    link = common.group("Phone link", "The backend this panel reads.")
    _add_row(link, _row(
        BACKEND_TITLE.get(str(which), str(which)),
        "Read through shani_chronoa/phone.py only. No SMS, no notification "
        "reading, no remote input, and no bluetooth - nothing in phone.py asks "
        "bluetoothctl for anything.",
    ))
    body.append(link)

    group = common.group("Paired devices")
    for device in devices:
        _add_row(group, _device_row(device, read_at))
    body.append(group)

    trigger = common.group(
        "The phone trigger",
        "What the 'phone' trigger itself reports. Its own gate is "
        f"'{TRIGGER_KEY}', which is a different permission from the one above.",
    )
    _add_row(trigger, _trigger_row())
    body.append(trigger)

    footer = Gtk.Label(xalign=0.0, wrap=True)
    footer.add_css_class("dim-label")
    footer.set_text(_FOOTER)
    body.append(footer)

    set_content(common.scrolled(body))
    page.status = recorder.status
    return page


__all__ = ["TITLE", "ICON", "SECTION", "build"]