"""The Phone-page dialer reflects the landline's live call state.

The dialer places a landline call when the line is up, and Answer/Hang-up act
on whichever line holds the call. These are wired to the landline *facade*
(`landline.listen`, `landline.dial`), never the SIP engine, so the assertions
are about the UI reacting to events - not about SIP.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr" / "lib" / "shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import GLib  # noqa: E402

from shani_chronoa import landline  # noqa: E402
from shani_chronoa.gui.surfaces.phone import PhonePanel  # noqa: E402


class _Note:
    def __init__(self):
        self.text = ""

    def set_text(self, t):
        self.text = t


class _Button:
    def __init__(self, visible=False):
        self.sensitive = None
        self.visible = visible

    def set_sensitive(self, on):
        self.sensitive = on

    def set_visible(self, on):
        self.visible = on

    def get_visible(self):
        return self.visible


def _panel():
    panel = PhonePanel.__new__(PhonePanel)
    panel.call_note = _Note()
    panel.answer_button = _Button()
    return panel


def _drain():
    ctx = GLib.MainContext.default()
    for _ in range(50):
        if not ctx.pending():
            break
        ctx.iteration(False)


def test_events_update_the_dialer(monkeypatch):
    panel = _panel()
    # Capture the listener the panel registers, and drive it directly.
    captured = {}
    monkeypatch.setattr(landline, "listen", lambda cb: captured.setdefault("cb", cb) or (lambda: None))
    panel._wire_landline_events()
    cb = captured["cb"]

    cb("ringing", number="+919876543210")
    _drain()
    assert "Calling" in panel.call_note.text and "9876543210" in panel.call_note.text
    assert panel.answer_button.sensitive is False

    cb("incoming", name="Amma", number="+911112223334")
    _drain()
    assert "Incoming" in panel.call_note.text and "Amma" in panel.call_note.text
    assert panel.answer_button.sensitive is True, "Answer must be live while ringing in"

    cb("answered", number="+919876543210")
    _drain()
    assert "On a landline call" in panel.call_note.text
    assert panel.answer_button.sensitive is False

    cb("ended", number="+919876543210", reason="hung up")
    _drain()
    assert "ended" in panel.call_note.text and "hung up" in panel.call_note.text


def test_dialer_prefers_the_landline_when_it_is_up(monkeypatch):
    panel = _panel()
    panel.book = []
    monkeypatch.setattr(PhonePanel, "_landline_ready", staticmethod(lambda: True))
    dialed = []
    monkeypatch.setattr(landline, "dial", lambda n: dialed.append(n) or "+91" + n)
    panel.call("9876543210")
    assert dialed == ["9876543210"], "a ready landline should take the call"
    assert "landline" in panel.call_note.text.lower()


def test_calling_a_typed_name_resolves_to_a_number(monkeypatch):
    panel = _panel()
    panel.book = [("Amma", ("+919527916219",)), ("Bank", ("18001234",))]
    monkeypatch.setattr(PhonePanel, "_landline_ready", staticmethod(lambda: True))
    dialed = []
    monkeypatch.setattr(landline, "dial", lambda n: dialed.append(n) or n)
    panel.call("Amma")
    assert dialed == ["+919527916219"], "a typed name should dial its number"
    panel.call("nobody here")
    assert "No contact" in panel.call_note.text


def test_the_keypad_sends_dtmf_during_a_call(monkeypatch):
    panel = _panel()

    class _Entry:
        def __init__(self): self.t = ""
        def get_text(self): return self.t
        def set_text(self, v): self.t = v
    panel.number = _Entry()
    tones = []
    monkeypatch.setattr(landline, "in_call", lambda: True)
    monkeypatch.setattr(landline, "dtmf", lambda d: tones.append(d) or True)
    panel._key_pressed("5")
    assert tones == ["5"], "a key during a call is a tone, not just text"
    monkeypatch.setattr(landline, "in_call", lambda: False)
    panel._key_pressed("7")
    assert tones == ["5"], "no tone when there is no call"
    assert panel.number.get_text() == "57"


def test_the_call_cue_offers_the_call_tools():
    from shani_chronoa import tool_select as ts
    tools = [{"function": {"name": n}} for n in ("landline_call", "bluetooth_call", "web_search")]
    offered = {t["function"]["name"] for t in ts.select_tools("call Amma", tools)}
    assert "landline_call" in offered and "bluetooth_call" in offered


class _Call:
    """A stand-in for `ua.Call`: the two fields the controls read."""

    def __init__(self, state="active", held=False):
        self.state, self.held = state, held


def _controls():
    panel = PhonePanel.__new__(PhonePanel)
    panel.call_note = _Note()
    panel.answer_button = _Button()
    return panel


def test_hold_and_swap_act_on_the_landline_and_not_the_paired_phone(monkeypatch):
    """The controls read "Hold this call" and "Swap"; the paired phone's route
    toggle is a different thing and has no far-end call to hold. Falling through
    to it would send a `bluetooth_call` action for a press that meant the other."""
    panel = _controls()
    done, texts = [], []
    monkeypatch.setattr(landline, "hold", lambda: done.append("hold") or True)
    monkeypatch.setattr(landline, "swap", lambda: done.append("swap") or True)
    for action in ("hold", "swap"):
        panel.call_action(action)
        texts.append(panel.call_note.text)
    assert done == ["hold", "swap"], f"the actions that ran were {done}"
    # One note, so each press is read before the next overwrites it.
    assert "Held" in texts[0], f"the hold press said {texts[0]!r}"
    assert "Swapped" in texts[1], f"the swap press said {texts[1]!r}"


def test_a_hold_that_does_not_take_says_so(monkeypatch):
    """`hold()` answers whether it took. A chip reading "Held" while the line is
    still on the call is the two-widgets-disagree failure."""
    panel = _controls()
    monkeypatch.setattr(landline, "hold", lambda: False)
    panel.call_action("hold")
    assert "Held" not in panel.call_note.text, (
        f"the panel claimed a hold that did not happen: {panel.call_note.text!r}")


def test_hold_and_swap_are_hidden_until_there_is_something_to_hold(monkeypatch):
    """A dialer with one call carrying a Hold that refuses is the dead-control
    class, so both are driven off the line's own call state."""
    panel = _controls()
    panel.hold_button = _Button()
    panel.swap_button = _Button()
    cases = [
        ([], False, False),                                 # no call at all
        ([_Call("active")], True, False),                   # one live call
        ([_Call("dialing")], False, False),                 # still ringing
        ([_Call("active", held=True), _Call("active")], True, True),   # swap
    ]
    for calls, hold, swap in cases:
        monkeypatch.setattr(landline, "calls", lambda c=calls: list(c))
        panel._refresh_call_controls()
        assert panel.hold_button.visible == hold and panel.swap_button.visible == swap, (
            f"{len(calls)} call(s) -> hold {panel.hold_button.visible} "
            f"swap {panel.swap_button.visible}, wanted {hold}/{swap}")


def test_a_refused_swap_names_the_reason(monkeypatch):
    panel = _controls()
    monkeypatch.setattr(landline, "swap", lambda: (_ for _ in ()).throw(
        RuntimeError("swap needs two answered calls")))
    panel.call_action("swap")
    assert "two answered calls" in panel.call_note.text, (
        f"the reason was lost: {panel.call_note.text!r}")
