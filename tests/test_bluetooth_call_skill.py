"""The `bluetooth_call` skill, and the finding that made it worth writing.

**PipeWire, not bluez and not ModemManager, does Bluetooth telephony.** This
corrects a conclusion this repo reached twice and got wrong both times, from
belief rather than measurement:

- bluez ships **no telephony profile**. Its `profiles/` directory is `audio
  battery cups deviceinfo fastpair gap iap input midi network ranging
  scanparam`, and `src/` contains no HFP handler and no ofono reference.
  `ofono` itself returns 0 matching packages on archlinux.org.
- **PipeWire ships `spa/plugins/bluez5/telephony.c`** and a Telephony D-Bus
  service, `org.pipewire.Telephony`, with `AudioGateway1`/`Call1` and `Dial()`,
  `ReleaseAndAnswer()`, `SendReleaseAndHangup()`, tones and volume. Its own
  `README-Telephony.md` offers to register as `org.ofono` on the system bus, "a
  drop-in replacement for ofono... only for the Bluetooth-based voice calls".

So the replacement for ofono is PipeWire, and ModemManager is not in the running
at all - it manages *data* bearers and has no telephony model.

**Verified on a real machine, not inferred.** `busctl --user list` shows
`org.pipewire.Telephony` owned by wireplaster, and `GetModems` on
`org.ofono.Manager` answers with an empty gateway list. **Empty is correct with
no phone connected** - gateways exist only while one is connected as a
hands-free unit - so the tests below insist that an empty list is reported as
"nobody is connected" and never as a failure or as an empty calendar.

The rest of the file is the properties this skill must not break:

- **the consent key exists in the schema**, because two skills in this batch
  shipped refusing every call while naming a switch that was not there;
- **dialling confirms first**, because a call rings another person;
- **"not connected" is not "not paired"** - a paired-but-absent phone is the
  single most likely reason this does nothing, and saying so is the difference
  between a useful refusal and a dead end;
- **an unreachable service is never reported as "no calls"**.

Run: `python3 -m pytest tests/test_bluetooth_call_skill.py`
"""

from __future__ import annotations

import subprocess
import sys

import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")

from shani_chronoa.skills import bluetooth_call as bc  # noqa: E402


# `busctl --json=short` output. The first two shapes were captured live on
# 2026-10-08 with a real phone (acer ZX) connected over HFP to PipeWire 1.4:
# GetModems lists the gateway with no fields at all. An earlier version of these
# fixtures was the *text* rendering written by hand with a count before every
# key - a shape busctl never prints - which is how a parser that kept only the
# first field of every object passed its own tests.
GETMODEMS_REAL = '{"type":"a{oa{sv}}","data":[{"/org/pipewire/Telephony/ag1":{}}]}'
MANAGED_REAL = (
    '{"type":"a{oa{sa{sv}}}","data":[{"/org/pipewire/Telephony/ag1":{'
    '"org.pipewire.Telephony.AudioGateway1":{"Address":{"type":"s","data":"74:6B:AB:67:7F:91"},'
    '"SpeakerVolume":{"type":"y","data":7},"MicrophoneVolume":{"type":"y","data":15}},'
    '"org.pipewire.Telephony.AudioGatewayTransport1":{"Codec":{"type":"y","data":0},'
    '"State":{"type":"s","data":"idle"},"RejectSCO":{"type":"b","data":false}}}}]}'
)
GETMODEMS_TWO = (
    '{"type":"a{oa{sv}}","data":[{'
    '"/org/pipewire/Telephony/ag0":{"Name":{"type":"s","data":"Pixel 8"},'
    '"Address":{"type":"s","data":"AA:BB:CC:DD:EE:FF"},"Powered":{"type":"b","data":true}},'
    '"/org/pipewire/Telephony/ag1":{"Name":{"type":"s","data":"Tablet"}}}]}'
)
GETMODEMS_ONE = (
    '{"type":"a{oa{sv}}","data":[{"/org/pipewire/Telephony/ag0":{'
    '"Name":{"type":"s","data":"Pixel 8"},"Address":{"type":"s","data":"AA:BB:CC:DD:EE:FF"}}}]}'
)
GETMODEMS_NONE = '{"type":"a{oa{sv}}","data":[{}]}'
GETCALLS_ONE = (
    '{"type":"a{oa{sv}}","data":[{"/org/pipewire/Telephony/ag0/call0":{'
    '"State":{"type":"s","data":"active"},"Direction":{"type":"s","data":"incoming"}}}]}'
)


# --- the finding -------------------------------------------------------------



def test_the_consent_key_is_in_the_schema():
    """Two skills in this batch shipped naming a switch that was not there."""
    import re
    from pathlib import Path

    schema = Path("usr/share/glib-2.0/schemas/org.shani.chronoa.gschema.xml").read_text()
    keys = set(re.findall(r'<key\s+name="([^"]+)"', schema))
    assert bc._CONSENT_KEY in keys, (
        f"{bc._CONSENT_KEY} is not in the schema, so no user action can grant it"
    )


def test_it_is_gated_and_defaults_off():
    from shani_chronoa import capabilities

    assert capabilities.GATED.get("bluetooth_call") == bc._CONSENT_KEY

    class _Off:
        def get_bool(self, key, default=False):
            return default

    original = bc.ChronoaConfig
    bc.ChronoaConfig = _Off
    try:
        result = bc._run({"action": "status"})
    finally:
        bc.ChronoaConfig = original
    assert bc._CONSENT_KEY in result, (
        f"the refusal does not name the switch that would open it: {result}"
    )


# --- parsing the real busctl rendering --------------------------------------











# --- "not connected" is not "not paired" ------------------------------------

def test_no_gateway_says_nobody_is_connected(monkeypatch):
    """The single most likely reason this does nothing, said plainly.

    A paired phone that is not connected has no hands-free link, so there is
    nothing to call through. Saying "0 calls" or an empty calendar would send
    somebody looking for a phone that is already paired and simply switched off.
    """
    monkeypatch.setattr(bc._engine, "service_present", lambda: True, raising=True)
    monkeypatch.setattr(bc._engine, "_busctl", lambda *a, **k: type(
        "R", (), {"returncode": 0, "stdout": GETMODEMS_NONE, "stderr": ""})(), raising=True)

    class _On:
        def get_bool(self, key, default=False):
            return True

    original = bc.ChronoaConfig
    bc.ChronoaConfig = _On
    try:
        result = bc._run({"action": "dial", "number": "+441234567890"})
    finally:
        bc.ChronoaConfig = original

    assert "Nothing was dialled" in result, result
    assert "connected" in result.lower(), (
        f"the refusal does not say the phone is not connected: {result}"
    )


def test_an_absent_service_is_never_reported_as_no_calls(monkeypatch):
    """'I could not look' and 'there is nothing' are different answers."""
    monkeypatch.setattr(bc._engine, "service_present", lambda: False, raising=True)

    class _On:
        def get_bool(self, key, default=False):
            return True

    original = bc.ChronoaConfig
    bc.ChronoaConfig = _On
    try:
        result = bc._run({"action": "status"})
    finally:
        bc.ChronoaConfig = original
    assert "not on the session bus" in result, result
    assert "0 calls" not in result and "no calls" not in result.lower(), result


def test_two_phones_are_refused_rather_than_one_being_guessed(monkeypatch):
    monkeypatch.setattr(bc._engine, "service_present", lambda: True, raising=True)
    monkeypatch.setattr(bc._engine, "_busctl", lambda *a, **k: type(
        "R", (), {"returncode": 0, "stdout": GETMODEMS_TWO, "stderr": ""})(), raising=True)

    class _On:
        def get_bool(self, key, default=False):
            return True

    original = bc.ChronoaConfig
    bc.ChronoaConfig = _On
    try:
        result = bc._run({"action": "answer"})
    finally:
        bc.ChronoaConfig = original
    assert "2 phones" in result, result
    assert "Nothing was answered" in result, (
        f"the refusal does not say that answering is what did not happen: {result}"
    )


# --- dialling confirms ------------------------------------------------------

def test_dialling_asks_before_it_calls(monkeypatch):
    """A call rings another person, so this is confirmed every time.

    The refusal path is what is asserted: a stub that answers "yes" would also
    pass a skill that had stopped asking.
    """
    monkeypatch.setattr(bc._engine, "service_present", lambda: True, raising=True)
    monkeypatch.setattr(bc._engine, "_busctl", lambda *a, **k: type(
        "R", (), {"returncode": 0, "stdout": GETMODEMS_ONE, "stderr": ""})(), raising=True)
    asked = []

    def refuse_confirm(title, body, seconds=0):
        asked.append((title, body))
        return False, "nobody confirmed"

    monkeypatch.setattr(bc.approvals, "confirm", refuse_confirm, raising=True)

    class _On:
        def get_bool(self, key, default=False):
            return True

    original = bc.ChronoaConfig
    bc.ChronoaConfig = _On
    try:
        result = bc._run({"action": "dial", "number": "+441234567890"})
    finally:
        bc.ChronoaConfig = original

    assert asked, "dialling did not ask anybody"
    title, body = asked[0]
    assert "call" in title.lower(), title
    assert "+441234567890" in body, (
        f"the question does not show the number that would be dialled: {body}"
    )
    assert "Not dialled" in result, result


def test_a_refused_confirmation_never_reaches_the_bus(monkeypatch):
    """The write is the assertion, not the sentence."""
    calls_made = []

    monkeypatch.setattr(bc._engine, "service_present", lambda: True, raising=True)
    monkeypatch.setattr(bc._engine, "_busctl", lambda *a, **k: type(
        "R", (), {"returncode": 0, "stdout": GETMODEMS_ONE, "stderr": ""})(), raising=True)
    monkeypatch.setattr(bc.approvals, "confirm", lambda *a, **k: (False, "no"), raising=True)

    def record(*args, **kwargs):
        calls_made.append(args)
        return True, ""

    monkeypatch.setattr(bc._engine, "_gateway_call", record, raising=True)

    class _On:
        def get_bool(self, key, default=False):
            return True

    original = bc.ChronoaConfig
    bc.ChronoaConfig = _On
    try:
        bc._run({"action": "dial", "number": "+441234567890"})
    finally:
        bc.ChronoaConfig = original
    assert calls_made == [], (
        f"the bus was called after the confirmation was refused: {calls_made}"
    )


def test_dial_with_no_number_asks_what_to_dial(monkeypatch):
    monkeypatch.setattr(bc._engine, "service_present", lambda: True, raising=True)
    monkeypatch.setattr(bc._engine, "_busctl", lambda *a, **k: type(
        "R", (), {"returncode": 0, "stdout": GETMODEMS_ONE, "stderr": ""})(), raising=True)

    class _On:
        def get_bool(self, key, default=False):
            return True

    original = bc.ChronoaConfig
    bc.ChronoaConfig = _On
    try:
        result = bc._run({"action": "dial"})
    finally:
        bc.ChronoaConfig = original
    assert "Dial what" in result, result
    assert "Nothing was dialled" in result, result




# --- reachability -----------------------------------------------------------

def test_the_skill_is_reachable_through_the_real_dispatch_path():
    from shani_chronoa import tools

    outcome = tools.execute_tool_outcome("bluetooth_call", {"action": "status"})
    assert outcome.ran, "the skill did not run through the real path"
    assert isinstance(outcome.text, str) and outcome.text, outcome


def test_the_schema_names_what_it_can_do_and_what_it_needs():
    """The description is what the model reads before choosing a tool."""
    description = bc.SCHEMA["function"]["description"].lower()
    for action in ("status", "dial", "answer", "hangup"):
        assert action in description, f"the schema does not mention {action!r}"
    assert bc._CONSENT_KEY in description, (
        "the schema does not name the consent key, so the model cannot explain the refusal"
    )
    assert "confirm" in description, (
        "the schema does not say a call is confirmed first, which is the part a "
        "person would want to know before asking for one"
    )

# --- the audio half ---------------------------------------------------------











#: Copied from a real `wpctl status` on a machine with a Bluetooth headset, box
#: glyphs and all. A hand-written fixture got this wrong twice - it used `.profile:`
#: (that is WirePlaster config, not wpctl output) and omitted the `ââ` prefixes -
#: and both mistakes made the parser return nothing while the fixture looked
#: plausible.
WPCTL_STATUS = """PipeWire 'pipewire-0' [1.6.2, user@host, cookie:1234]
 └─ Clients:
 │        84. Mutter                               [1.6.2, user@host, pid:4115]
 │        112. Terminal                            [1.6.2, user@host, pid:260714]

Audio
 ├─ Devices:
 │      53. Tiger Lake-LP Smart Sound Technology Audio Controller [alsa]
 │      48. acer_ZX [bluez]
 │      │     Profile 0: hfp_hf (sco) active
 │      │     Profile 1: hsp_hs (sco) available
 │      │
 ├─ Sinks:
 │      60. Tiger Lake-LP Smart Sound Technology Audio Controller Speaker [vol: 0.41]
 │      │
 ├─ Sources:
 │      62. Tiger Lake-LP Smart Sound Technology Audio Controller Digital Microphone [vol: 1.00]
 │      │
 └─ Streams:

Video
 ├─ Devices:
 │      51. Integrated Camera                   [v4l2]
 │      │
"""







def test_the_schema_documents_the_new_actions():
    description = bc.SCHEMA["function"]["description"].lower()
    for action in ("audio", "profile", "hfp_hf", "hsp_hs"):
        assert action in description, (
            f"the schema does not mention {action!r}, so the model will not use it"
        )
    params = bc.SCHEMA["function"]["parameters"]["properties"]
    assert "profile" in params and "card" in params


# --- the two regressions found with a real phone connected (2026-10-08) ------

def _stub_busctl(monkeypatch, answers):
    """Answer each busctl call by the method it names; record what was asked."""
    asked = []

    def fake(*args, **kwargs):
        asked.append(args)
        out = next((v for k, v in answers.items() if k in args), "")
        return subprocess.CompletedProcess(["busctl", *args], 0, out, "")
    monkeypatch.setattr(bc._engine, "_busctl", fake)
    monkeypatch.setattr(bc._engine, "service_present", lambda: True)
    monkeypatch.setattr(bc._engine, "_device_name", lambda address: "acer ZX" if address else "")
    return asked






# --- the three call methods, as the real gateway exposes them -----------------

def _granted_call(monkeypatch):
    class On:
        def get_bool(self, key, default=False):
            return True
    monkeypatch.setattr(bc, "ChronoaConfig", On)
    monkeypatch.setattr(bc.approvals, "confirm", lambda *a, **k: (True, ""))






