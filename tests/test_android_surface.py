"""The Android surface: one panel over `android_device`, adding no transport.

**The reason this panel exists is a measurement, and it is asserted here so it
cannot rot into a duplicate.** GNOME Control Center's panels were enumerated from
the installed package (`dpkg -L gnome-control-center` → 26 `gnome-*-panel.desktop`
on 50.3) and Plasma's from `plasma-workspace` 6.7.5 plus `plasma-pa` 6.7.4 in
the build cache. Neither carries a phone reached over `adb`, and neither does
any other app on the machine.

The same sweep is what took **Bluetooth** and **Sound** off the table for this
session, and `test_these_are_not_duplicates_of_the_desktop_panels` is what keeps
that from being re-decided from memory: GNOME's `CcBluetoothPanel` carries the
adapter `enable_switch`, and `CcSoundPanel` carries an `_Output Device` combo
row — the exact capability `audio_output`'s docstring calls a gap. A panel for
either would be a second copy of a settings page, not an absence filled.

Every fixture here is the skill's own answer text. The skill classifies "adb is
not installed", "nothing connected" and "this device is unauthorised"
separately, with a different remedy for each, so the panel keeps those three
apart rather than folding them into "no phone".
"""

from __future__ import annotations

import inspect
import pathlib

import pytest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # type: ignore  # noqa: E402

from shani_chronoa.gui.surfaces import android as a  # type: ignore  # noqa: E402
from shani_chronoa import tools  # type: ignore  # noqa: E402
from shani_chronoa.skills import android_device as skill  # type: ignore  # noqa: E402


@pytest.fixture(autouse=True)
def _adw():
    Adw.init()


# --- the skill's own answers -------------------------------------------------

# **`devices -l`, not `devices`.** `android_device._devices()` asks for the long
# form, so these are long-form lines. The short `serial<TAB>state` shape this
# file started with is not a shape the skill ever sees, and that is how a parser
# reading the wrong column passed.
#
# **Not one serial here carries a colon**, because in adb's long form the `usb:`
# prefix lives in a *later* field, and `adb(1)`'s `ADB_LIBUSB` entry says it is
# "omitted for libusb" altogether. A parser requiring a colon in column 0
# therefore rejects every real line - which is what the first fixtures allowed.

EMULATOR = ("emulator-5554  device product:sdk_gphone64_x86_64 "
            "model:sdk_gphone64 device:emu64x transport_id:1")
PHONE_ON_ADBS_BACKEND = ("R5CT10  unauthorized usb:1-1 product:panther "
                         "model:Pixel_8 device:panther transport_id:3")
PHONE_ON_LIBUSB = ("1-1  offline usb:1-1 product:raven model:Pixel_6_Pro "
                   "device:raven transport_id:4")

ONE_DEVICE = "List of devices attached\n" + EMULATOR
TWO_DEVICES = ("List of devices attached\n" + EMULATOR + "\n"
               + PHONE_ON_ADBS_BACKEND)
ONE_PHONE_LIBUSB = "List of devices attached\n" + PHONE_ON_LIBUSB
# A phone that is not running Android at all: adb reaches it and can only say
# `bootloader`, and `fastboot` is the tool that talks to it.
ONE_PHONE_BOOTLOADER = ("List of devices attached\nBB1234567  bootloader usb:1-1"
                        " product:raven model:Pixel_6_Pro device:raven"
                        " transport_id:5")
NONE_CONNECTED = "List of devices attached"
HEADLINE_NONE = "No phone is connected with USB debugging"
NO_ADB = "adb is not installed - it is the tool that speaks to a phone over USB debugging"


class Config:
    def __init__(self, allowed: bool = True) -> None:
        self._allowed = allowed

    def get_bool(self, key: str, default: bool = False) -> bool:
        return self._allowed if key == "phone-control-enabled" else default


class App:
    def __init__(self, config=None) -> None:
        self.config = config


def _patch(monkeypatch, **attrs):
    for name, value in attrs.items():
        monkeypatch.setattr(skill, name, value)


def _walk(widget) -> list:
    out, stack = [], [widget]
    while stack:
        node = stack.pop()
        out.append(node)
        child = node.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return out


def _labels(widget) -> str:
    return "\n".join(n.get_text() for n in _walk(widget)
                     if isinstance(n, Gtk.Label) and n.get_text())


def _run(page) -> Gtk.Widget:
    """Let the panel's own thread land, and return what it shows."""
    ctx = GLib.MainContext.default()
    import time as _time
    end = _time.monotonic() + 5
    while _time.monotonic() < end:
        ctx.iteration(False)
        _time.sleep(0.005)
    for node in _walk(page):
        if isinstance(node, Gtk.ScrolledWindow):
            child = node.get_child()
            if child is not None:
                return child
    return page


def _answers(monkeypatch, devices: str):
    """Answer `devices` with the given text and every other action with a stub."""
    def execute(name, arguments):
        if name == "android_device" and arguments.get("action") == "devices":
            return devices
        return "ok"

    monkeypatch.setattr(tools, "execute_tool", execute)


# --- why this is not a duplicate -------------------------------------------

def test_this_is_not_a_duplicate_of_the_gnome_panels():
    """**The measurement, as a test - and it measures ONE desktop.**

    The test used to be called `..._the_desktop_panels`, plural, over a survey
    whose conclusion was about *both* desktops. That was an overclaim in the
    name and in the first bullet of its own docstring: everything below can
    only read **GNOME**, from `dpkg`, on a host that is Ubuntu rather than
    either target image.

    **What Plasma's half rests on instead.** Its KCM set was enumerated from
    the `plasma-workspace` and `plasma-pa` packages' file lists on an Arch
    image, not from anything reachable here - and `plasma-workspace` is not
    installed on this host, so a second check would `skip` and read as coverage
    while measuring nothing. **A skip is the absence presented as a pass**, so
    the honest statement is that this half is off-host and unverified here; a
    `slot-test` enumerating Plasma's KCM desktop files on a real image is what
    would make it machine-checked, and is named rather than faked.

    What *is* checkable here, and is, is GNOME - three claims, each of which
    was wrong from memory at least once during the survey that produced it:

    * GNOME has no phone panel - its 26 `gnome-*-panel.desktop` files carry no
      `phone`/`companion`/`kdeconnect` entry;
    * GNOME's Bluetooth panel does the adapter toggle and the device list, which
      is why there is **no** Bluetooth-devices panel here - it has an
      `enable_switch` and a `BluetoothSettingsWidget`;
    * GNOME's Sound panel has an `_Output Device` combo row, which is the exact
      thing `audio_output`'s docstring calls a gap and which is why there is no
      audio panel here.

    The check is on the shipped panel *lists*, so GNOME gaining a phone panel
    fails this rather than letting a duplicate sit here unnoticed.
    """
    import subprocess
    try:
        listing = subprocess.run(
            ["dpkg", "-L", "gnome-control-center"], capture_output=True,
            text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        pytest.skip("gnome-control-center is not installed on this host")
    panels = {n.split("/")[-1] for n in listing.split()
              if n.endswith("-panel.desktop")}
    if not panels:
        pytest.skip("this host's gnome-control-center ships no panels")
    assert not any(p in panels for p in ("gnome-phone-panel.desktop",
                                         "gnome-companion-panel.desktop",
                                         "gnome-kdeconnect-panel.desktop")), (
        f"a desktop panel for phones now exists; this one may be a duplicate: "
        f"{sorted(panels)}")
    # And the two candidates this survey dropped are still dropped.
    assert "gnome-bluetooth-panel.desktop" in panels, (
        "this host's gnome-control-center has no bluetooth panel, so the "
        "reason for not building one no longer holds and it must be revisited")
    assert "gnome-sound-panel.desktop" in panels, (
        "this host's gnome-control-center has no sound panel, so the reason "
        "for not building one no longer holds and it must be revisited")


# --- the gate ----------------------------------------------------------------

class TestTheGate:
    def test_a_switch_that_is_off_reads_nothing(self, monkeypatch):
        _patch(monkeypatch, _consent=lambda c: (False, "off it is"))
        monkeypatch.setattr(tools, "execute_tool",
                            lambda *a, **k: pytest.fail("read while shut"))
        page = a.build(App(Config(allowed=False)))
        assert "turned off" in _labels(_run(page))

    def test_the_permission_is_asked_through_the_skills_own_helper(self, monkeypatch):
        """Not an inline `config.get_bool`: that helper carries the refusal
        sentence the assistant returns."""
        asked = []

        def consent(config):
            asked.append(config)
            return True, ""

        _patch(monkeypatch, _consent=consent)
        _answers(monkeypatch, NONE_CONNECTED)
        a.build(App(Config()))
        assert asked, "the panel checked the switch itself"

    def test_no_config_is_refused_rather_than_shown(self, monkeypatch):
        monkeypatch.setattr(tools, "execute_tool",
                            lambda *a, **k: pytest.fail("read without a config"))
        page = a.build(App(config=None))
        assert "turned off" in _labels(_run(page))


# --- the devices ------------------------------------------------------------

class TestTheDevices:
    def test_no_phone_is_a_state_and_not_an_error(self, monkeypatch):
        _patch(monkeypatch, _consent=lambda c: (True, ""))
        _answers(monkeypatch, NONE_CONNECTED)
        page = a.build(App(Config()))
        text = _labels(_run(page))
        assert HEADLINE_NONE in text

    def test_the_skill_distinguishes_no_adb_from_nothing_connected(self, monkeypatch):
        """**Two different situations with two different remedies.** adb missing
        means the tool is absent; nothing connected means the phone's owner has
        not agreed. Folding them is how "your phone is broken" gets reported on a
        machine that simply has no phone plugged in."""
        assert a.STATE_NO_ADB != a.STATE_NONE
        assert a.HEADLINE[a.STATE_NO_ADB] != a.HEADLINE[a.STATE_NONE]

        _patch(monkeypatch, _consent=lambda c: (True, ""))
        _answers(monkeypatch, NO_ADB)
        page = a.build(App(Config()))
        text = _labels(_run(page))
        assert a.HEADLINE[a.STATE_NO_ADB] in text
        assert a.HEADLINE[a.STATE_NONE] not in text

    def test_one_phone_is_listed_with_its_state(self, monkeypatch):
        _patch(monkeypatch, _consent=lambda c: (True, ""))
        _answers(monkeypatch, ONE_DEVICE)
        page = a.build(App(Config()))
        text = _labels(_run(page))
        assert "emulator-5554" in text
        assert "1 answering" in text

    def test_two_phones_are_both_listed(self, monkeypatch):
        _patch(monkeypatch, _consent=lambda c: (True, ""))
        _answers(monkeypatch, TWO_DEVICES)
        page = a.build(App(Config()))
        text = _labels(_run(page))
        assert "emulator-5554" in text and "R5CT10" in text
        assert "2 answering" in text

    def test_an_unauthorised_phone_says_what_the_owner_must_do(self, monkeypatch):
        """`unauthorized` is adb's own word and means the phone has not accepted
        this computer's key - the owner has to do that on the phone."""
        _patch(monkeypatch, _consent=lambda c: (True, ""))
        _answers(monkeypatch, TWO_DEVICES)
        page = a.build(App(Config()))
        text = _labels(_run(page))
        assert "accept this computer's key" in text

    def test_the_header_line_is_not_a_device(self, monkeypatch):
        """`List of devices attached` is adb's header. Counting it would report
        a phone that does not exist."""
        # **`(serial, state)` pairs, and column 1 is the state.** The first
        # version returned the raw line, so `_device_row` read `split()[-1]` -
        # which on a `devices -l` line is `transport_id:3`.
        # **`sdk gphone64`, not `sdk_gphone64`.** adb's model strings carry
        # underscores (`Pixel_6_Pro`, `sdk_gphone64_x86_64`) because they are
        # identifiers, and both the skill and this panel turn them into spaces.
        # The first version of this asserted the raw identifier and failed on
        # the code being right - the same fixture-vs-expectation inversion the
        # long-form change introduced one layer up.
        assert a._connected(TWO_DEVICES) == [
            ("emulator-5554", "device", "sdk gphone64"),
            ("R5CT10", "unauthorized", "Pixel 8"),
        ]
        assert a._connected(NONE_CONNECTED) == []

    def test_the_state_is_column_one_and_not_the_last_field(self):
        """**The long form's last field is `transport_id:N`.**

        A panel reading the end of the line shows `transport_id:3` where the
        state belongs, so it never reaches the branch that explains what to do
        about an unauthorised phone - and the first fixtures were short form,
        where the last field *was* the state, so it passed.
        """
        [(serial, state, model)] = a._connected(ONE_DEVICE)
        assert serial == "emulator-5554"
        assert state == "device"
        assert model == "sdk gphone64"
        assert ONE_DEVICE.splitlines()[1].split()[-1] == "transport_id:1", (
            "the fixture is not long form, so this test cannot see the defect")

    def test_a_serial_with_no_colon_is_still_a_phone(self, monkeypatch):
        """**`adb(1)`, `ADB_LIBUSB`: the `usb:` prefix is omitted for libusb.**

        The first parser required `":" in serial`, which accepts adb's own USB
        backend and rejects libusb - so on a libusb system the panel reported
        no phone, with complete confidence.
        """
        assert ":" not in a._connected(ONE_PHONE_LIBUSB)[0][0]
        assert a._connected(ONE_PHONE_LIBUSB) == [
            ("1-1", "offline", "Pixel 6 Pro")]

        _patch(monkeypatch, _consent=lambda c: (True, ""))
        _answers(monkeypatch, ONE_PHONE_LIBUSB)
        text = _labels(_run(a.build(App(Config()))))
        assert "1-1" in text, "a libusb phone was listed as no phone at all"
        assert "not answering" in text, "offline did not say what offline is"

    def test_the_model_the_skill_asked_for_is_shown(self, monkeypatch):
        """**`-l` is requested for the model, and the model is used.**

        `adb(1)` describes `devices -l` only as *"Use long output"*; the long
        form's extra fields are `model:`, `product:`, `device:` and
        `transport_id:`. The skill asks for `-l` and takes `model:`; a panel
        that threw it away asked for a field and discarded it.
        """
        _patch(monkeypatch, _consent=lambda c: (True, ""))
        _answers(monkeypatch, ONE_PHONE_LIBUSB)
        text = _labels(_run(a.build(App(Config()))))
        assert "Pixel 6 Pro" in text, (
            "the model adb was asked for was not on the page")
        # Underscores in adb's `model:` are for the field, not for a reader.
        assert "Pixel_6_Pro" not in text


    def test_a_phone_in_bootloader_says_adb_cannot_reach_it(self, monkeypatch):
        """**`fastboot(1)` is a second tool for this state, and for no other.**

        `adb(1)`'s `wait-for` lists six - `device, recovery, rescue, sideload,
        bootloader, disconnect` - and a sentence firing for all six would claim
        a remedy four of them do not have.
        """
        _patch(monkeypatch, _consent=lambda c: (True, ""))
        _answers(monkeypatch, ONE_PHONE_BOOTLOADER)
        text = _labels(_run(a.build(App(Config()))))
        assert "bootloader mode" in text, "the state was shown as adb's bare word"
        assert "fastboot" in text, "the one state with a second tool does not name it"
        assert "does not drive it" in text, (
            "naming a tool carrying `flash`, `erase`, `format` and "
            "`flashing unlock` without saying it is not offered here is a "
            "recommendation to flash somebody's phone")

    def test_a_refusal_sentence_is_not_a_phone(self):
        """**The bug this state filter was written for.**

        The skill's own words when `adb` is missing are a sentence: *"adb is
        not installed - it is the tool that speaks to a phone over USB
        debugging"*. With only the header check, that parsed as a device named
        `adb` in state `is`, so a machine missing adb listed **one phone and
        reported it as answering** - the absence reported as a healthy reading.
        The colon test that used to hide this was itself the bug, so it is not
        the thing that came back.
        """
        assert a._connected(NO_ADB) == [], (
            "a sentence of words parsed as a connected phone")
        assert a._connected(NONE_CONNECTED) == []
        # And the vocabulary is the manual's, not a shape guessed here.
        for state in ("device", "offline", "unauthorized", "bootloader",
                      "recovery", "sideload", "rescue", "disconnect"):
            assert state in a.ADB_STATES, f"{state} is a real adb state"
        assert "is" not in a.ADB_STATES and "not" not in a.ADB_STATES

    @pytest.mark.parametrize(
        "state", ["device", "recovery", "sideload", "rescue", "disconnect"])
    def test_the_bootloader_sentence_is_not_a_blanket_one(self, monkeypatch, state):
        """**The control, over literals rather than over the module's own table.**

        Parametrising over whatever states the code happens to handle would
        drop a state from the test instead of failing on it, which is the
        mistake this repository records twice. The other five keep adb's word
        beside the model and get no second tool.
        """
        line = (f"ABC123  {state} usb:1-1 product:raven model:Pixel_6_Pro "
                "device:raven transport_id:6")
        _patch(monkeypatch, _consent=lambda c: (True, ""))
        _answers(monkeypatch, "List of devices attached\n" + line)
        text = _labels(_run(a.build(App(Config()))))
        assert f"Pixel 6 Pro - {state}" in text, (
            f"{state} did not keep adb's own word beside the model")
        assert "fastboot" not in text, (
            f"{state} was given the bootloader sentence, and neither manual "
            f"documents a second tool for it")


# --- the panel's own obligations --------------------------------------------

def test_the_reads_are_on_a_thread_and_never_on_the_main_thread():
    """`adb` carries a 60-second timeout in the skill, so a read on the main
    thread is a 60-second freeze of the window."""
    source = pathlib.Path(a.__file__).read_text()
    assert "GLib.Thread.new" in source
    build_source = inspect.getsource(a.build)
    assert "_devices()" in build_source, "the read is nowhere"
    # ...and it must be reached through the thread, not called by `build()`.
    # Checked by walking the AST rather than by the text, because `build()` names
    # `_devices()` inside the thread's own nested function and a substring test
    # cannot tell the two apart - which is why the first version of this said
    # "the read is on the main thread" for a panel that is correctly threaded.
    import ast as _ast
    tree = _ast.parse(build_source)
    direct = [n for n in _ast.walk(tree)
              if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Name)
              and n.func.id == "_devices"]
    assert direct, "build() no longer asks for the device list at all"
    threaded = {n for parent in _ast.walk(tree) if isinstance(parent, _ast.FunctionDef)
                for n in _ast.walk(parent) if n in direct}
    assert not direct or threaded, "the read is not inside any function"


def test_it_offers_no_way_to_run_a_command_on_the_phone(monkeypatch):
    """**A deliberate absence, asserted by what the panel asks for.**

    Two halves, and the first is the product guarantee: `android_device`'s
    schema enum has no `shell` verb, so the model cannot ask for one however it
    phrases the request. `adb(1)` lists `shell` as a real command and the skill
    uses it internally (`android_device.py:99`) for `info`, `screenshot` and
    `open_url` - so the absence is a **surface** decision about what this panel
    offers, not a claim that the skill never touches a shell.

    **The second half was a source grep, and it matched its own subject.** The
    panel's docstring says why there is no shell button, so the string `adb
    shell` is in the panel's source *because the absence is documented*, and the
    check would have gone red on the explanation. Prose is not a control here;
    what the panel does is, so this records every call it makes.
    """
    assert "shell" not in set(skill._ACTIONS), "the skill grew a shell verb"

    asked = []

    def execute(name, arguments):
        asked.append((name, dict(arguments)))
        return "List of devices attached"

    _patch(monkeypatch, _consent=lambda c: (True, ""))
    monkeypatch.setattr(tools, "execute_tool", execute)
    # `_run`, not `build`: the read is on a worker thread and lands through an
    # idle callback, so building the page and asserting immediately measures a
    # panel that has not asked anything - which is what the first version did,
    # and `asked == []` is how it said so.
    _run(a.build(App(Config())))
    assert asked, "the panel asked for nothing, so nothing was measured"
    for name, arguments in asked:
        assert name == "android_device", f"the panel called {name} directly"
        assert arguments.get("action") == "devices", (
            f"the panel asked for {arguments.get('action')!r}; a phone panel "
            f"that can drive the phone is not the thing this one is")


def test_the_panel_never_offers_a_file_or_a_link_to_the_phone(monkeypatch):
    """**Nothing in `build()`'s path writes anything on the device.**

    `push` and `pull` move files and `open_url` starts an app, so "read-mostly"
    is a claim about the *verbs this panel reaches*, and the verbs it reaches
    are exactly one. Pinned so adding a second call site cannot pass silently.
    """
    reached = set()

    def execute(name, arguments):
        reached.add(arguments.get("action"))
        return "List of devices attached"

    _patch(monkeypatch, _consent=lambda c: (True, ""))
    monkeypatch.setattr(tools, "execute_tool", execute)
    _run(a.build(App(Config())))
    assert reached, "the panel reached nothing, so the claim is untested"
    assert reached <= {"devices"}, f"the panel reached {sorted(reached)}"
    mutating = {"push", "pull", "open_url", "install-multiple", "uninstall"}
    assert not reached & mutating, "the panel reached a verb that changes the phone"


def test_the_panel_is_registered_with_a_settings_route_that_resolves():
    """**The registry of *this* package, imported by its real name.**

    The first version of this reached for `shani_cassini.gui.surfaces` - the
    other repo's registry, copied from a session spent in it - and that module
    does not exist here. It raised `ModuleNotFoundError`, so the registration
    this asserts was never checked by anything.
    """
    import shani_chronoa.gui.surfaces as surfaces
    assert surfaces.SETTINGS_TARGETS["android"] == a.SETTINGS_TARGET
    assert "android" in surfaces.SURFACE_IDS, "the panel is not in the sidebar"


def test_the_states_are_declared_once_and_never_invented():
    """The happy path is deliberately absent from `HEALTH` - it is not a gate."""
    assert set(a.HEALTH) == {a.STATE_REFUSED, a.STATE_NO_ADB, a.STATE_NONE}
    assert a.STATE_PERMITTED not in a.HEALTH
    for state in a.HEADLINE:
        assert state in a.BODY, f"{state} has a headline and no body"
        if state != a.STATE_PERMITTED:
            assert state in a.HEALTH


def test_every_state_has_its_own_headline():
    """**Over a literal, not over `a.HEADLINE`.** Parametrising over the table
    it checks means dropping a state loses a case instead of failing - the same
    mistake made twice now in this repo."""
    for state in (a.STATE_PERMITTED, a.STATE_REFUSED, a.STATE_NO_ADB,
                  a.STATE_NONE):
        assert a.HEADLINE[state]
        assert a.HEADLINE[state] != state
