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

def test_pipewire_really_provides_bluetooth_telephony():
    """The premise, asserted so it cannot rot into folklore.

    Two halves, and the second is the one that was wrong for most of this
    repo's life: bluez has no telephony profile, and PipeWire has telephony.
    If a future bluez grows one, or PipeWire loses this, the reason for the
    skill changes and this should say so.
    """
    assert bc.BUS_NAME == "org.pipewire.Telephony"
    assert bc.MANAGER_PATH == "/org/pipewire/Telephony"
    # GetModems lives on the *compatibility* interface, not the native one -
    # verified by a call that failed with "interface doesn't exist" when it was
    # sent to AudioGateway1.
    assert bc.MANAGER_IFACE == "org.ofono.Manager"


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

def test_one_gateway_is_parsed_with_its_name_and_address():
    rows = bc._parse_objects(GETMODEMS_TWO)
    assert len(rows) == 2, rows
    assert rows[0][1]["Name"] == "Pixel 8", rows
    assert rows[0][1]["Address"] == "AA:BB:CC:DD:EE:FF", rows
    assert rows[1][1]["Name"] == "Tablet", rows


def test_an_empty_gateway_list_parses_to_nothing():
    assert bc._parse_objects(GETMODEMS_NONE) == []


def test_a_call_is_parsed_with_its_state_and_direction():
    """Calls carry State/Direction, not Name/Address.

    A parser that filtered for a name - which is what gateways carry - returned
    an empty list for a call that was very much in progress, and the skill then
    said "no call in progress" on a ringing phone.
    """
    rows = bc._parse_objects(GETCALLS_ONE)
    assert rows, "a call object was not parsed at all"
    assert rows[0][1]["State"] == "active", rows
    assert rows[0][1]["Direction"] == "incoming", rows


def test_parsing_junk_returns_nothing_rather_than_raising():
    """A phone that reports something unexpected must not crash the skill into a
    traceback, which is what a model would then read as the tool result."""
    for junk in ("", "a{oa{sv}} garbage", '"/org/pipewire/Telephony/ag0"', "null"):
        assert bc._parse_objects(junk) == [], junk


def test_a_number_is_validated_before_anything_reaches_the_bus():
    """PipeWire validates too, but a refusal before the call is one fewer
    question on the bus and a clearer sentence for the person."""
    assert bc._NUMBER.match("+441234567890")
    assert bc._NUMBER.match("123")
    for bad in ("", "not a number", "123;rm -rf /", "*" * 81, "1" * 200):
        assert not bc._NUMBER.match(bad), f"{bad!r} was accepted as dialable"


# --- "not connected" is not "not paired" ------------------------------------

def test_no_gateway_says_nobody_is_connected(monkeypatch):
    """The single most likely reason this does nothing, said plainly.

    A paired phone that is not connected has no hands-free link, so there is
    nothing to call through. Saying "0 calls" or an empty calendar would send
    somebody looking for a phone that is already paired and simply switched off.
    """
    monkeypatch.setattr(bc, "service_present", lambda: True, raising=True)
    monkeypatch.setattr(bc, "_busctl", lambda *a, **k: type(
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
    monkeypatch.setattr(bc, "service_present", lambda: False, raising=True)

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
    monkeypatch.setattr(bc, "service_present", lambda: True, raising=True)
    monkeypatch.setattr(bc, "_busctl", lambda *a, **k: type(
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
    monkeypatch.setattr(bc, "service_present", lambda: True, raising=True)
    monkeypatch.setattr(bc, "_busctl", lambda *a, **k: type(
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

    monkeypatch.setattr(bc, "service_present", lambda: True, raising=True)
    monkeypatch.setattr(bc, "_busctl", lambda *a, **k: type(
        "R", (), {"returncode": 0, "stdout": GETMODEMS_ONE, "stderr": ""})(), raising=True)
    monkeypatch.setattr(bc.approvals, "confirm", lambda *a, **k: (False, "no"), raising=True)

    def record(*args, **kwargs):
        calls_made.append(args)
        return True, ""

    monkeypatch.setattr(bc, "_gateway_call", record, raising=True)

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
    monkeypatch.setattr(bc, "service_present", lambda: True, raising=True)
    monkeypatch.setattr(bc, "_busctl", lambda *a, **k: type(
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


def test_an_unknown_action_is_refused_with_the_real_ones():
    result = bc._run({"action": "teleport"})
    # The gate is off by default here, so the gate answers first - which is the
    # correct order, and is what makes the action list untestable without it.
    assert result


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

def test_echo_cancellation_is_reported_as_detect_only(monkeypatch):
    """PipeWire attaches module-echo-cancel through a `filter-chain.conf.d`
    fragment, so it cannot be switched on at runtime - `pipewire.echo_cancel_active()`
    exists to *report* it. A skill that claimed to enable it would be claiming a
    control it does not have."""
    monkeypatch.setattr(bc.shutil, "which", lambda n: "/usr/bin/" + n, raising=True)
    monkeypatch.setattr(bc.pipewire, "echo_cancel_active", lambda: False, raising=True)
    _ok, missing = bc.audio_backends()
    assert "echo cancellation is not active" in missing, missing
    assert "filter-chain" in missing, (
        f"it does not say *why* it cannot be switched on: {missing}"
    )


def test_the_wpctl_plumbing_is_reused_rather_than_duplicated():
    """Four wrappers around one binary is the problem `pipewire.run_wpctl` exists
    to fix, so this skill must go through it rather than adding a fifth."""
    import inspect
    source = inspect.getsource(bc)
    # `shutil.which("wpctl")` is a presence *check* and is fine; what must not
    # exist is building an argv that runs wpctl behind pipewire's back.
    for fragment in ('["wpctl"', 'run(["wpctl"', "run(\n"):
        assert fragment not in source, (
            f"bluetooth_call builds a wpctl argv itself ({fragment!r}); it must "
            "use pipewire.run_wpctl so the package keeps one wrapper and one timeout"
        )
    assert "run_wpctl" in source
    assert "parse_status" in source, (
        "bluetooth_call re-derives the Audio-section parser; it must use "
        "audio_output.parse_status"
    )


def test_missing_audio_tools_are_named_by_package_not_by_absence(monkeypatch):
    """A refusal somebody can act on.

    Chronoa depends on wireplumber (so wpctl is present) but not on
    `libpulse` (Arch's home for pactl) (so pactl often is not), and the loopback modules come
    from `libpipewire`. Saying "audio unavailable" leaves nothing to do; naming
    the three is a five-second fix.
    """
    monkeypatch.setattr(bc.shutil, "which", lambda n: None, raising=True)
    monkeypatch.setattr(bc.pipewire, "echo_cancel_active", lambda: False, raising=True)
    _ok, missing = bc.audio_backends()
    assert "libpulse" in missing, missing
    assert "wireplumber" in missing, missing


def test_the_audio_report_says_what_is_present_and_what_is_not(monkeypatch):
    """Both halves, because a report that only lists problems reads as "nothing
    works" on a machine where the profile switch is fine."""
    monkeypatch.setattr(bc, "bluez_cards",
                        lambda: [("42", "bluez_card.acer_ZX", "hfp_hf", [])], raising=True)
    monkeypatch.setattr(bc, "bridge_running", lambda: ("", "no loopback is running"), raising=True)
    monkeypatch.setattr(bc, "audio_backends",
                        lambda: ("", "pactl (the 'libpulse' package) is not installed"),
                        raising=True)
    report = bc._audio_report("AA:BB:CC:DD:EE:FF")
    assert "hfp_hf" in report, report
    assert "not running" in report, report
    assert "libpulse" in report, (
        f"the report does not say what would fix the missing half: {report}"
    )


def test_a_running_loopback_is_reported_as_running(monkeypatch):
    """Detected, not remembered - the honest question is whether the call is
    audible now, not whether this tool once started something."""
    monkeypatch.setattr(bc, "bluez_cards", lambda: [], raising=True)
    monkeypatch.setattr(bc, "bridge_running", lambda: ("module-loopback id 12", ""), raising=True)
    monkeypatch.setattr(bc, "audio_backends", lambda: ("everything", ""), raising=True)
    report = bc._audio_report("")
    assert "module-loopback id 12" in report, report
    assert "not running" not in report, report


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

def test_only_bluetooth_cards_are_offered_for_a_profile():
    """Sinks, sources and webcams are not cards and have no profiles.

    An unscoped parse returned `gnome-shell`, `Terminal` and both cameras as
    audio hardware; scoping to the Audio/Devices block is what stops `profile`
    offering to switch a window manager to hfp_hf.
    """
    monkey = pytest.MonkeyPatch()
    monkey.setattr(bc.shutil, "which", lambda n: "/usr/bin/" + n, raising=True)
    monkey.setattr(bc.audio_output, "parse_status",
                   lambda text: {"Devices": [{"id": 53, "name": "alsa [alsa]"},
                                            {"id": 48, "name": "acer_ZX [bluez]"}],
                                 "Sinks": [{"id": 60, "name": "Speaker"}],
                                 "Sources": [{"id": 62, "name": "Microphone"}]}, raising=True)
    try:
        assert [c[0] for c in bc.audio_cards()] == ["53", "48"], bc.audio_cards()
        assert [c[0] for c in bc.bluez_cards()] == ["48"], bc.bluez_cards()
    finally:
        monkey.undo()


def test_setting_a_profile_passes_an_index_not_a_name(monkeypatch):
    """`wpctl set-profile` takes an INDEX. Passing the profile's name sets the
    wrong thing silently, so the lookup is the load-bearing part."""
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return type("R", (), {"returncode": 0, "stdout": WPCTL_STATUS, "stderr": ""})()

    monkeypatch.setattr(bc.shutil, "which", lambda n: "/usr/bin/" + n, raising=True)
    monkeypatch.setattr(bc.subprocess, "run", fake_run, raising=True)

    ok, why = bc.set_profile("48", "hsp_hs")
    assert ok, why
    assert seen["argv"][:2] == ["wpctl", "set-profile"], seen["argv"]
    assert seen["argv"][2] == "48", seen["argv"]
    assert seen["argv"][3].isdigit(), (
        f"the profile was passed by name where wpctl wants an index: {seen['argv']}"
    )
    assert seen["argv"][3] == "1", (
        f"hsp_hs is index 1 in the fixture, not {seen['argv'][3]}"
    )


def test_a_profile_the_card_does_not_have_lists_the_ones_it_does(monkeypatch):
    """Refusing with the options is the difference between actionable and not."""
    monkeypatch.setattr(bc.shutil, "which", lambda n: "/usr/bin/" + n, raising=True)
    monkeypatch.setattr(bc, "audio_cards",
                        lambda: [("48", "acer_ZX [bluez]", "hfp_hf", [("0", "hfp_hf"),
                                                                   ("1", "hsp_hs")])], raising=True)
    ok, why = bc.set_profile("48", "a2dp_sink")
    assert not ok, "a profile the card does not have was accepted"
    assert "hfp_hf" in why and "hsp_hs" in why, why
    assert "a2dp_sink" not in why.split("it offers")[-1], why


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
    monkeypatch.setattr(bc, "_busctl", fake)
    monkeypatch.setattr(bc, "service_present", lambda: True)
    monkeypatch.setattr(bc, "_device_name", lambda address: "acer ZX" if address else "")
    return asked


def test_a_connected_phone_whose_gateway_has_no_fields_is_still_found(monkeypatch):
    """PipeWire's GetModems returned the gateway with zero fields, and the skill
    answered "no phone is connected" with the phone connected."""
    _stub_busctl(monkeypatch, {"GetModems": GETMODEMS_REAL,
                               "GetManagedObjects": MANAGED_REAL})
    assert bc.gateways() == [("/org/pipewire/Telephony/ag1", "acer ZX", "74:6B:AB:67:7F:91")]


def test_calls_are_asked_of_the_voice_call_manager(monkeypatch):
    """`GetCalls` is not on org.ofono.Manager; asking there fails, and the
    failure read as "no calls" - so a ringing phone was invisible."""
    asked = _stub_busctl(monkeypatch, {"GetCalls": GETCALLS_ONE})
    assert bc.calls("/org/pipewire/Telephony/ag0") == [
        ("/org/pipewire/Telephony/ag0/call0", "active", "incoming")]
    assert any("org.ofono.VoiceCallManager" in a for a in asked), asked
    assert not any("org.ofono.Manager" in a and "GetCalls" in a for a in asked), asked


# --- the three call methods, as the real gateway exposes them -----------------

def _granted_call(monkeypatch):
    class On:
        def get_bool(self, key, default=False):
            return True
    monkeypatch.setattr(bc, "ChronoaConfig", On)
    monkeypatch.setattr(bc.approvals, "confirm", lambda *a, **k: (True, ""))


def test_dial_passes_the_signature_before_the_number(monkeypatch):
    """Without "s", busctl read the number as the signature and no call was placed."""
    _granted_call(monkeypatch)
    asked = _stub_busctl(monkeypatch, {"GetModems": GETMODEMS_REAL, "GetManagedObjects": MANAGED_REAL})
    out = bc._run({"action": "dial", "number": "+919800000000"})
    dial = [a for a in asked if "Dial" in a]
    assert dial and dial[0][-2:] == ("s", "+919800000000"), (dial, out)


def test_hangup_uses_the_method_the_gateway_has(monkeypatch):
    """`SendReleaseAndHangup` is not on AudioGateway1 (introspected); `HangupAll` is."""
    _granted_call(monkeypatch)
    asked = _stub_busctl(monkeypatch, {"GetModems": GETMODEMS_REAL, "GetManagedObjects": MANAGED_REAL})
    bc._run({"action": "hangup"})
    assert any("HangupAll" in a for a in asked), asked
    assert not any("SendReleaseAndHangup" in a for a in asked), asked


def test_a_ringing_call_is_answered_on_its_own_object(monkeypatch):
    """ReleaseAndAnswer ends the active call to take a waiting one; a phone that
    is just ringing is answered with the call's own Answer."""
    _granted_call(monkeypatch)
    ringing = ('{"type":"a{oa{sv}}","data":[{"/org/pipewire/Telephony/ag1/call1":{'
               '"State":{"type":"s","data":"incoming"}}}]}')
    asked = _stub_busctl(monkeypatch, {"GetModems": GETMODEMS_REAL,
                                       "GetManagedObjects": MANAGED_REAL, "GetCalls": ringing})
    bc._run({"action": "answer"})
    answer = [a for a in asked if "Answer" in a]
    assert answer and "/org/pipewire/Telephony/ag1/call1" in answer[0], asked
    assert not any("ReleaseAndAnswer" in a for a in asked), asked
