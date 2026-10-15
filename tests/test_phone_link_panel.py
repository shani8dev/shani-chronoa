"""The phone-link panel, and the state it could not be driven into.

**Why this file exists beyond the panel.** `_PhoneLinkView` exposed
`_refresh_list()` on its page but the state that method read (`self._devices`)
was not reachable, so a caller setting `page._devices = [...]` wrote an attribute
on the *page* that the *view* never looked at - and the panel sat on its empty
state forever. A panel that cannot be driven into a state it can be in is a panel
whose that-state behaviour has no witness. `show_devices()` is the seam, and it
is one method rather than a settable attribute because two writes that must agree
("set this, then refresh") is two places to fall out of step.

Built in a subprocess on a private Broadway display, because a GDK backend is
chosen when GTK initialises and no fixture can change it afterwards.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

import pytest

PKG = pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"

_HARNESS = r'''
import json, sys
sys.path.insert(0, %(pkg)r)
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk
Adw.init()

from shani_chronoa.gui import surfaces
from shani_chronoa.gui.surfaces import phone_link


class Config:
    def __init__(self, **flags):
        self._flags = flags
    def get_bool(self, key, default=False):
        return bool(self._flags.get(key, default))
    def get(self, key, default=None):
        return default


class App:
    def __init__(self, **flags):
        self.config = Config(**flags)


def walk(widget, out=None):
    """Every string in a tree - labels, and `Adw.ActionRow` titles/subtitles,
    which are properties rather than child widgets and are invisible to a
    child-only walk."""
    if out is None:
        out = []
    if isinstance(widget, Gtk.Label):
        out.append(widget.get_label())
    if hasattr(widget, "get_title"):
        out.append(widget.get_title())
    if hasattr(widget, "get_subtitle"):
        out.append(widget.get_subtitle())
    child = widget.get_first_child()
    while child is not None:
        walk(child, out)
        child = child.get_next_sibling()
    return out


def kinds(widget, out=None):
    if out is None:
        out = []
    out.append(type(widget).__name__)
    child = widget.get_first_child()
    while child is not None:
        kinds(child, out)
        child = child.get_next_sibling()
    return out


def buttons(widget, out=None):
    if out is None:
        out = []
    if isinstance(widget, Gtk.Button):
        out.append(widget.get_label())
    child = widget.get_first_child()
    while child is not None:
        buttons(child, out)
        child = child.get_next_sibling()
    return out


def say(**kw):
    print("RESULT " + json.dumps(kw), flush=True)


case = sys.argv[1]

if case == "empty":
    page = phone_link.build(App(**{"lan-phone-enabled": True}))
    say(kinds=kinds(page), texts=walk(page), buttons=buttons(page))

elif case == "devices":
    page = phone_link.build(App(**{"lan-phone-enabled": True}))
    page.show_devices([{"name": "Pixel 9", "address": "192.168.1.44",
                        "deviceType": "phone"},
                       {"name": "Galaxy", "address": "192.168.1.51"}])
    say(texts=walk(page), buttons=buttons(page))

elif case == "consent_off":
    page = phone_link.build(App(**{"lan-phone-enabled": False}))
    page._refresh_list(); page._refresh_status()
    say(texts=walk(page), buttons=buttons(page))

elif case == "pressed_with_consent_off":
    # **The press, not just the page.** The first version's `_present_refusal`
    # rebuilt the list with the same sentence `_devices_list` already showed,
    # so a mutation deleting it stayed green - nothing had ever pressed Look
    # with the switch off. This case does.
    page = phone_link.build(App(**{"lan-phone-enabled": False}))
    page.find.emit("clicked")
    say(texts=walk(page), note=page.find_note.get_label(),
        sensitive=page.find.get_sensitive())

elif case == "pressed_with_consent_on_finds_nothing":
    # the honest empty answer, reached the way a person reaches it.
    # **The loop has to be pumped.** `_off_thread` finishes on
    # `GLib.idle_add`, and a harness that only ever builds widgets never runs
    # the main loop - so the worker would finish, queue its callback, and the
    # page would sit on "Listening on the network." forever. Measured, and the
    # first version of this test asserted against exactly that.
    from gi.repository import GLib
    import time as _t
    page = phone_link.build(App(**{"lan-phone-enabled": True}))
    page.find.emit("clicked")
    context = GLib.MainContext.default()
    deadline = _t.time() + 15
    while _t.time() < deadline:
        while context.pending():
            context.iteration(False)
        if page.find.get_sensitive() and page.find_note.get_label() != "Listening on the network.":
            break
        _t.sleep(0.15)
    say(note=page.find_note.get_label(),
        list_is_empty="No phone found yet" in " ".join(walk(page)))

elif case == "pairing_not_implemented":
    page = phone_link.build(App(**{"lan-phone-enabled": True}))
    say(texts=walk(page), buttons=buttons(page))

elif case == "paired":
    from shani_chronoa import lan_link
    lan_link._remember_pairing({"deviceId": "f" * 32, "deviceName": "Pixel 9"},
                               "192.168.1.44", 1716)
    page = phone_link.build(App(**{"lan-phone-enabled": True}))
    say(texts=walk(page), buttons=buttons(page))

elif case == "registry":
    from shani_chronoa import lan_link
    available = surfaces.available_surfaces()
    say(present="phone_link" in available,
        all_present=all(m in available for m in available),
        section=surfaces.sections().get("phone_link"),
        target=surfaces.settings_target("phone_link"),
        pairing=lan_link.pairing_status()[0],
        # the panel's own claim, next to the module's
        claims_not_implemented=phone_link.NOT_PAIRED)

elif case == "registry_empty_finds_no_phone":
    # discovery with nothing on the network is a real answer, not a failure
    from shani_chronoa import lan_link
    say(found=lan_link.listen_once(1.0))

else:
    say(error="unknown case " + case)
''' % {"pkg": str(PKG)}


@pytest.fixture(scope="module")
def broadway():
    """A private Broadway display, so no window appears on anybody's desktop."""
    daemon = shutil.which("gtk4-broadwayd")
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if daemon is None or not runtime:
        pytest.skip("no gtk4-broadwayd or no XDG_RUNTIME_DIR, so no headless display")
    base = pathlib.Path(runtime)
    number = next((n for n in range(40, 2630)
                   if not (base / f"broadway{n + 1}.socket").exists()), None)
    if number is None:
        pytest.skip("no free Broadway display number")
    proc = subprocess.Popen([daemon, f":{number}"], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    deadline = time.time() + 10
    while time.time() < deadline and not (base / f"broadway{number + 1}.socket").exists():
        time.sleep(0.1)
    yield number
    proc.terminate()
    proc.wait(timeout=10)


def _run(broadway, case, tmp_path):
    script = tmp_path / f"harness-{case}.py"
    script.write_text(_HARNESS)
    env = dict(os.environ)
    env.update(GDK_BACKEND="broadway", BROADWAY_DISPLAY=f":{broadway}",
               PYTHONDONTWRITEBYTECODE="1", GSETTINGS_BACKEND="memory",
               XDG_STATE_HOME=str(tmp_path / "state"),
               XDG_DATA_HOME=str(tmp_path / "data"))
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    done = subprocess.run([sys.executable, str(script), case],
                          capture_output=True, text=True, timeout=180, env=env)
    for line in done.stdout.splitlines():
        if line.startswith("RESULT"):
            return json.loads(line[6:])
    pytest.fail(f"the harness produced nothing:\n{done.stdout[-900:]}\n"
                f"{done.stderr[-900:]}")


def shown(out) -> str:
    return " | ".join(t for t in out.get("texts", []) if t)


def clicked(out) -> str:
    return " | ".join(b for b in out.get("buttons", []) if b)


class TestItSaysWhatIsNotThere:
    def test_not_paired_leads_with_the_way_to_pair(self, broadway, tmp_path):
        """**First, not after somebody tries** - and with the button that
        does it, not a sentence about a missing feature."""
        out = _run(broadway, "pairing_not_implemented", tmp_path)
        assert "No phone is paired" in shown(out), shown(out)
        assert "Pair a phone" in out["buttons"], out["buttons"]

    def test_paired_names_the_phone_and_offers_forget(self, broadway, tmp_path):
        out = _run(broadway, "paired", tmp_path)
        assert any("Pixel 9" in t and "192.168.1.44" in t for t in out["texts"]), out["texts"]
        assert "Forget this phone" in out["buttons"], out["buttons"]

    def test_the_panels_claim_and_the_modules_agree(self, broadway, tmp_path):
        out = _run(broadway, "registry", tmp_path)
        assert out["pairing"] == "not paired", out
        assert out["claims_not_implemented"] == "No phone is paired", out


class TestTheDeviceState:
    """The state the first version of this panel could not be driven into."""

    def test_a_device_that_answered_is_shown_with_its_address(self, broadway, tmp_path):
        out = _run(broadway, "devices", tmp_path)
        assert "error" not in out, out.get("error")
        assert "Pixel 9" in shown(out), shown(out)
        assert "192.168.1.44" in shown(out), shown(out)
        assert "Galaxy" in shown(out), shown(out)

    def test_the_address_is_the_subtitle_and_it_is_selectable(self, broadway, tmp_path):
        out = _run(broadway, "devices", tmp_path)
        # `Adw.ActionRow`'s subtitle, which `common.row()` sets - and which a
        # child-only walk cannot see, which is how the first version of this
        # test passed against an empty panel.
        assert any(t == "192.168.1.44" for t in out["texts"]), out["texts"]

    def test_each_device_offers_the_one_thing_worth_doing(self, broadway, tmp_path):
        out = _run(broadway, "devices", tmp_path)
        assert clicked(out).count("Send a file") == 2, clicked(out)

    def test_the_empty_state_is_an_empty_state(self, broadway, tmp_path):
        out = _run(broadway, "empty", tmp_path)
        assert "No phone found yet" in shown(out), shown(out)
        assert "Look for phones" in clicked(out), clicked(out)
        assert "Send a file" not in clicked(out), (
            "a device row where no device answered is a control that cannot act")

    def test_the_empty_page_is_a_real_empty_state_not_a_blank_box(self, broadway, tmp_path):
        out = _run(broadway, "empty", tmp_path)
        assert "StatusPage" in out["kinds"], out["kinds"][:8]


class TestThePressSaysWhatHappened:
    """A button that does nothing with no sentence is the dead-control class."""

    def test_pressing_look_with_consent_off_names_the_switch(self, broadway, tmp_path):
        out = _run(broadway, "pressed_with_consent_off", tmp_path)
        assert "error" not in out, out.get("error")
        assert "Let Chronoa find phones on the same Wi-Fi" in (out["note"] or ""), out["note"]
        assert "Nothing was sent" in (out["note"] or ""), out["note"]

    def test_it_does_not_leave_the_button_stuck(self, broadway, tmp_path):
        """A refusal is not a hang: the button comes back."""
        out = _run(broadway, "pressed_with_consent_off", tmp_path)
        assert out["sensitive"] is True, out

    def test_pressing_look_with_consent_on_answers_honestly(self, broadway, tmp_path):
        """Discovery with nothing around is an answer, on the page, and it is
        called out as the thing it is."""
        out = _run(broadway, "pressed_with_consent_on_finds_nothing", tmp_path)
        assert "No phone announced itself" in (out["note"] or ""), out["note"]
        assert out["list_is_empty"] is True, out


class TestConsent:
    def test_it_names_the_switch_and_refuses(self, broadway, tmp_path):
        out = _run(broadway, "consent_off", tmp_path)
        assert "Let Chronoa find phones on the same Wi-Fi" in shown(out), shown(out)
        assert "Nothing will be sent" in shown(out), shown(out)

    def test_consent_off_does_not_offer_a_send(self, broadway, tmp_path):
        out = _run(broadway, "consent_off", tmp_path)
        assert "Send a file" not in clicked(out), clicked(out)
        # **and not a device list either** - a shut gate is not "no phone"
        assert "No device yet" not in shown(out), shown(out)


class TestReachable:
    def test_the_registry_carries_it(self, broadway, tmp_path):
        out = _run(broadway, "registry", tmp_path)
        assert out["present"], out
        assert out["all_present"], (
            "a surface whose module fails to import is a hole in the sidebar")
        # **"Phone and remote", not "Acting"** since 2026-10-12: this panel and
        # three others describe how this computer is *reached* from a phone or
        # over the network, which is not a thing Chronoa was asked to do, and
        # "Acting" had grown to 14 of 33 panels. Asserting the literal is the
        # point - it is what catches the panel being filed back.
        assert out["section"] == "Phone and remote", out
        assert out["target"] == "settings:privacy", out

    def test_discovery_with_nothing_around_is_an_answer_not_a_crash(self, broadway, tmp_path):
        out = _run(broadway, "registry_empty_finds_no_phone", tmp_path)
        assert isinstance(out["found"], list), out
        assert out["found"] == [], (
            "this machine has no KDE Connect phone; an empty list is the real "
            "answer, and the panel must be able to say it")
