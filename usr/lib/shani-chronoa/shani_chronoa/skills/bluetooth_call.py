"""Skill: use a paired phone as a Bluetooth speakerphone - see who's calling,
answer, hang up, dial, and route the call's audio to this computer's speakers.

**PipeWire does the telephony, not bluez and not ModemManager.** This is the
part that took checking against the source rather than believing a forum post,
and it reverses an earlier conclusion of this repo's own:

- bluez ships **no telephony profile at all**. Its `profiles/` directory is
  `audio battery cups deviceinfo fastpair gap iap input midi network ranging
  scanparam` - no HFP handler, no `ofono` reference anywhere in `src/`, and
  Arch's `bluez` package is 19 files of which `/usr/lib/bluetooth/bluetoothd` is
  the only one in that directory. `ofono` itself returns **0 matching packages**
  on archlinux.org.
- **PipeWire has `spa/plugins/bluez5/telephony.c`** and ships a Telephony D-Bus
  service, `org.pipewire.Telephony` on the session bus, with
  `AudioGateway1` / `AudioGatewayTransport1` / `Call1` and `Dial()`,
  `ReleaseAndAnswer()`, `SwapCalls()`, `SendTones()` and per-call volume. Its own
  `README-Telephony.md` notes it can additionally register as **`org.ofono`** on
  the system bus, "a drop-in replacement for ofono... only for the
  Bluetooth-based voice calls". So ofono's role was absorbed into PipeWire; the
  replacement for ofono is not ModemManager, which manages *data* bearers and
  has no telephony model whatsoever.
- Verified live on a Shanios dev box, not inferred: `busctl --user list` shows
  `org.pipewire.Telephony` owned by wireplaster, and it answers
  `GetModems` on `org.ofono.Manager` with an (empty) gateway list. **Empty is
  the correct answer with no phone connected as an audio gateway** - the gateways
  appear only when one is, which is why this module reports "none connected"
  rather than treating an empty list as a failure.

So a call can be dialled, answered and hung up over Bluetooth with no cable, no
ADB and no root. What this cannot do is make the laptop a *modem* into the
mobile network: the phone remains the radio, and PipeWire is the hands-free side
of the link to it.

**Audio is a second, separate step.** Dialling gets the call up; the voice
lands wherever the phone's HFP audio is routed. `route` finds the BlueZ card
that belongs to the phone and reports the profile PipeWire negotiated, which is
the thing worth knowing when a call connects silently.

Every outgoing call is confirmed first, through `approvals.confirm`. Placing a
call speaks for the person to someone else, and a skill that can dial on the
model's say-so is a skill that can make an unwanted phone call.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

from shani_chronoa import approvals, pipewire
from shani_chronoa.skills import audio_output
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

COMPONENT = "skill:bluetooth-call"
_TIMEOUT = 20
_CONSENT_KEY = "bluetooth-call-enabled"

BUS_NAME = "org.pipewire.Telephony"
MANAGER_PATH = "/org/pipewire/Telephony"
MANAGER_IFACE = "org.ofono.Manager"          # the compatible name; the native one has no GetModems
GATEWAY_IFACE = "org.pipewire.Telephony.AudioGateway1"
#: Where `GetCalls` lives on a gateway object. Not `org.ofono.Manager`: measured on
#: PipeWire 1.4, asking that interface answers "Method GetCalls ... doesn't exist",
#: which `calls()` turned into "no calls" - so a ringing phone could never be seen.
CALLMGR_IFACE = "org.ofono.VoiceCallManager"
CALL_IFACE = "org.pipewire.Telephony.Call1"

#: GSM characters a number may contain. PipeWire validates this itself, but a
#: refusal before the call is made is one fewer question on the bus.
_NUMBER = re.compile(r"^[0-9+*#,ABCD]{1,80}$")


class TelephonyUnavailable(RuntimeError):
    """The Telephony service is not here, or said no. Never "there is nothing"."""


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """(allowed, why-not) for Bluetooth call control.

    The house shape, for the reason `tests/test_question_presenter.py` documents:
    it sweeps every gated tool through one `_consent(config) -> (allowed, reason)`
    contract, which is how it proves each one refuses by naming its own switch
    when nobody can be asked.
    """
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (f"Using your phone as a Bluetooth speakerphone is turned off. Nothing was "
                       f"done. Enable '{_CONSENT_KEY}' in Settings to allow it.")
    return True, ""


def _busctl(*args: str, timeout: int = _TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(["busctl", "--user", *args], capture_output=True,
                          text=True, timeout=timeout, check=False)


def service_present() -> bool:
    """Whether `org.pipewire.Telephony` is on the session bus at all.

    Separate from "are there any gateways", because the two answers mean
    different things: a missing service is a package or session problem, while
    an empty gateway list is simply nobody connected.
    """
    if not shutil.which("busctl"):
        return False
    try:
        result = _busctl("list", "--no-legend")
    except (OSError, subprocess.SubprocessError):
        return False
    return BUS_NAME in (result.stdout or "")


def gateways() -> "list[tuple[str, str, str]]":
    """[(path, name, address)] for each connected audio gateway.

    A gateway is a phone that has connected to this machine as a hands-free
    unit. **An empty list is a true answer**, not a failure: with no phone
    connected over HFP there is nothing to call through.
    """
    if not service_present():
        raise TelephonyUnavailable(
            "PipeWire's Bluetooth telephony service is not on the session bus "
            "(it ships with wireplumber's PipeWire and needs pipewire's bluez5 module)"
        )
    try:
        result = _busctl("--json=short", "call", BUS_NAME, MANAGER_PATH, MANAGER_IFACE, "GetModems")
    except (OSError, subprocess.SubprocessError) as exc:
        raise TelephonyUnavailable(f"the Telephony service did not answer ({exc})") from exc
    if result.returncode != 0:
        raise TelephonyUnavailable(
            "the Telephony service refused GetModems: "
            + (result.stderr or result.stdout or "unknown error").strip()
        )
    # Every object GetModems returns *is* a gateway. Measured on PipeWire 1.4 with a
    # real phone connected, it returns the path with **no fields at all**
    # (`{"/org/pipewire/Telephony/ag1":{}}`), so filtering on Name/Address reported
    # "no phone connected" for a connected one. The address lives on the
    # AudioGateway1 interface, read from GetManagedObjects.
    managed = _managed_addresses()
    found = []
    for path, f in _parse_objects(result.stdout or ""):
        address = f.get("Address") or managed.get(path, "")
        name = f.get("Name") or _device_name(address) or path.rsplit("/", 1)[-1]
        found.append((path, name, address))
    return found


def _managed_addresses() -> "dict[str, str]":
    """{gateway path: Bluetooth address} from the ObjectManager, or {} when unreadable."""
    try:
        result = _busctl("--json=short", "call", BUS_NAME, MANAGER_PATH,
                         "org.freedesktop.DBus.ObjectManager", "GetManagedObjects")
        data = json.loads(result.stdout or "")["data"][0]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError, TypeError):
        return {}
    out = {}
    for path, ifaces in (data or {}).items():
        props = (ifaces or {}).get(GATEWAY_IFACE) or {}
        address = (props.get("Address") or {}).get("data")
        if isinstance(address, str):
            out[path] = address
    return out


def _device_name(address: str) -> str:
    """The paired device's name for an address, or "" - never invented."""
    if not address or not shutil.which("bluetoothctl"):
        return ""
    try:
        proc = subprocess.run(["bluetoothctl", "info", address], capture_output=True,
                              text=True, timeout=_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    m = re.search(r"^\s*Alias:\s*(.+)$", proc.stdout or "", re.M) or \
        re.search(r"^\s*Name:\s*(.+)$", proc.stdout or "", re.M)
    return m.group(1).strip() if m else ""


def _parse_objects(text: str) -> list:
    """[(path, {field: value})] from `busctl --json=short` output of an a{oa{sv}}.

    JSON, not the text rendering. The text parser this replaces expected a count
    before *every* key, which busctl never prints (one count per object, then
    `"Key" type value` triples, and values like `y 7` or `b false` are not quoted),
    so any object with two fields kept only the first - and its fixtures had been
    written in the same wrong shape, so its tests passed. Objects with no fields
    are kept: PipeWire's GetModems returns exactly that for a connected phone.
    """
    try:
        data = json.loads(text)["data"][0]
    except (ValueError, KeyError, IndexError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    rows = []
    for path, fields in data.items():
        if not str(path).startswith("/org/pipewire/Telephony/") or not isinstance(fields, dict):
            continue
        rows.append((path, {k: (v.get("data") if isinstance(v, dict) else v)
                            for k, v in fields.items()}))
    return rows


def calls(gateway: str) -> list:
    """(path, state, direction) for each call on a gateway."""
    try:
        result = _busctl("--json=short", "call", BUS_NAME, gateway, CALLMGR_IFACE, "GetCalls")
    except (OSError, subprocess.SubprocessError) as exc:
        raise TelephonyUnavailable(f"the Telephony service did not answer ({exc})") from exc
    if result.returncode != 0:
        return []
    return [(path, f.get("State", ""), f.get("Direction", ""))
            for path, f in _parse_objects(result.stdout or "")
            if "State" in f or "Direction" in f]


def _gateway_call(gateway: str, method: str, *params: str) -> "tuple[bool, str]":
    """(ok, why) for one AudioGateway1 method."""
    return _object_call(gateway, GATEWAY_IFACE, method, *params)


def _object_call(path: str, iface: str, method: str, *params: str) -> "tuple[bool, str]":
    """(ok, why) for one method on a Telephony object (a gateway or a call)."""
    try:
        result = _busctl("call", BUS_NAME, path, iface, method, *params)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"the Telephony service did not answer ({exc})"
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "").strip() or "unknown error"
    return True, ""


TRANSPORT_IFACE = "org.pipewire.Telephony.AudioGatewayTransport1"


def audio_route(gateway: str) -> "tuple[str, str]":
    """(where the call audio goes - "pc" or "phone", the transport State).

    Introspected on PipeWire 1.4: the gateway carries `AudioGatewayTransport1`
    with `Activate()`, a writable `RejectSCO` and `State` (idle/active). An
    active transport is the call audio on this computer; RejectSCO true keeps it
    on the handset.
    """
    def prop(name):
        r = _busctl("get-property", BUS_NAME, gateway, TRANSPORT_IFACE, name)
        return (r.stdout or "").strip().split(" ", 1)[-1].strip('"') if r.returncode == 0 else ""
    state, reject = prop("State"), prop("RejectSCO")
    return ("phone" if reject == "true" or state != "active" else "pc"), state


def set_audio_route(gateway: str, where: str) -> "tuple[bool, str]":
    """Move the call audio to this computer ("pc") or back to the phone ("phone")."""
    if where not in ("pc", "phone"):
        return False, "where must be 'pc' or 'phone'"
    r = _busctl("set-property", BUS_NAME, gateway, TRANSPORT_IFACE, "RejectSCO", "b",
                "true" if where == "phone" else "false")
    if r.returncode != 0:
        return False, (r.stderr or r.stdout or "").strip() or "RejectSCO could not be set"
    if where == "pc":
        return _object_call(gateway, TRANSPORT_IFACE, "Activate")
    return True, ""


#: The wpctl plumbing is reused, not reimplemented. `pipewire.run_wpctl` is the one
#: wrapper every skill in this package uses, and it exists precisely because the
#: same binary was being invoked through three wrappers with three timeouts - a
#: first version of this skill shelled out to `wpctl` itself and became the fourth.
#: `audio_output.parse_status` is the tested Audio-section parser and already walks
#: wpctl's box-drawing tree correctly; re-deriving it here got the `├─` prefixes and
#: the trailing colon wrong and reported **zero audio cards on a machine with one**,
#: and once that was fixed it reported gnome-shell and both webcams as audio
#: hardware until the section scoping went back. shani-cassini's `system_status.py`
#: hit the same traps and wrote them down, which is why they were not rediscovered.
#:
#: `parse_status` gained a "Devices" section for this skill, because the Bluetooth
#: profile lines hang off the *cards* and no sink or source line has one.


def audio_backends() -> "tuple[str, str]":
    """(what is available, what is missing) for the audio half.

    Three independent things, and the most surprising is the third:

    1. **wpctl** (wireplumber) - reads and switches the card's profile. Present
       wherever Chronoa runs, since wireplumber is a hard dependency.
    2. **pactl** (libpulse on Arch, an optdepend) - needed to *load* the
       loopback that bridges the call's voice to the speakers.
    3. **echo cancellation is detection-only.** `pipewire.echo_cancel_active()`
       exists because PipeWire's `module-echo-cancel` is a SPA hook attached by
       a `filter-chain.conf.d` fragment and **cannot be switched on at runtime**.
       A skill that claimed to enable it would be reporting a control it does not
       have, which is the failure this repository keeps meeting.
    """
    missing = []
    if not shutil.which("wpctl"):
        missing.append("wpctl (the 'wireplumber' package) - without it no profile can be read "
                       "or switched")
    if not shutil.which("pactl"):
        missing.append("pactl (the 'libpulse' package) - without it the call's audio "
                       "cannot be bridged, though the profile can still be switched")
    if not pipewire.echo_cancel_active():
        missing.append("echo cancellation is not active (PipeWire attaches it through a "
                       "filter-chain.conf.d fragment, so it is detected but not switched on here)")
    return ("everything" if not missing else "", "; ".join(missing))


def audio_cards() -> list:
    """[(id, name, active profile, [(index, profile)])] for Audio **devices**.

    The cards are where a Bluetooth profile lives; sinks and sources have none.
    """
    try:
        status = pipewire.run_wpctl("status")
    except (OSError, subprocess.SubprocessError):
        return []
    graph = audio_output.parse_status(status.stdout or "")
    rows = []
    lines = (status.stdout or "").split("\n")
    for device in graph.get("Devices", []):
        active, available = "", []
        for i, line in enumerate(lines):
            if not re.match(r"^[\s\u2502\u251c\u2514\u2500]*\*?\s*" + str(device["id"]) + r"\.", line):
                continue
            for follow in lines[i + 1:i + 8]:
                p = re.match(r"^[\s\u2502\u251c\u2514\u2500]*Profile\s+(\d+):\s+(\S+)", follow)
                if p:
                    available.append((p.group(1), p.group(2)))
                    if "active" in follow:
                        active = p.group(2)
            break
        rows.append((str(device["id"]), device["name"], active, available))
    return rows


def bluez_cards() -> list:
    """Only the Bluetooth cards - the ones a phone's call audio rides on.

    Matched on wpctl's `[bluez]` tag as well as the name, because a card's
    description is the device's own and a vendor may call itself anything.
    """
    rows = audio_cards()
    # wpctl pads the name into a column ("acer ZX" + 29 spaces + "[bluez5]",
    # measured); collapsed here so the padding never reaches a spoken answer.
    return [(card, " ".join(str(desc).split()), profile, available)
            for card, desc, profile, available in rows
            if "bluez" in desc.lower() or "bluez" in profile.lower()
            or "bluez" in " ".join(n for _, n in available)]


def set_profile(card: str, profile: str) -> "tuple[bool, str]":
    """Switch a card to a profile. (ok, why).

    `wpctl set-profile` takes an **index**, not a name; passing the name sets
    something else silently, so the profile is looked up in `wpctl status` first.
    """
    if not shutil.which("wpctl"):
        return False, "wpctl (the 'wireplumber' package) is not installed"
    rows = [row for row in audio_cards() if row[0] == str(card)]
    if not rows:
        return False, (f"no audio card numbered {card}; wpctl sees: "
                       + (", ".join(f"{c} ({d})" for c, d, _, _ in audio_cards()) or "none"))
    index = next((i for i, name in rows[0][3] if name == profile), None)
    if index is None:
        offered = ", ".join(name for _, name in rows[0][3]) or "none listed"
        return False, f"that card has no profile called {profile!r}; it offers: {offered}"
    try:
        done = pipewire.run_wpctl("set-profile", str(card), index)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"wpctl could not be run ({exc})"
    if done.returncode == 0:
        return True, ""
    return False, (done.stderr or done.stdout or "").strip() or "unknown error"


def bridge_running() -> "tuple[str, str]":
    """Is a loopback already up, and what is it? (answer, why-not).

    Detected rather than remembered: the honest question is whether the call is
    audible now, not whether this tool once started something.
    """
    if not shutil.which("pactl"):
        return "", "pactl (the 'libpulse' package) is not installed"
    try:
        result = subprocess.run(["pactl", "list", "short", "modules"],
                                capture_output=True, text=True, timeout=_TIMEOUT,
                                check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return "", f"pactl could not be run ({exc})"
    for line in (result.stdout or "").split("\n"):
        if "loopback" in line:
            return line.strip(), ""
    return "", "no loopback is running"


def _audio_report(address: str) -> str:
    """What the call audio is doing, and what is missing if it is doing nothing."""
    _, missing = audio_backends()
    lines = []
    bt = bluez_cards()
    if not bt:
        lines.append("No Bluetooth audio card is present, so a phone's call audio has nowhere "
                     "to go on this machine. A phone that is only paired does not appear - it "
                     "has to be connected.")
    for card, description, profile, available in bt:
        detail = f" on profile {profile}" if profile else ""
        if available and not profile:
            detail = " offers " + ", ".join(n for _, n in available)
        lines.append(f"card {card}: {description}{detail}")
    running, why = bridge_running()
    if running:
        lines.append(f"loopback: {running}")
    else:
        lines.append(f"loopback: not running ({why})")
    if missing:
        lines.append("missing for the full audio path: " + missing)
    return "\n".join(lines)


def _summary() -> str:
    """The state of every gateway, plus any call in progress."""
    try:
        found = gateways()
    except TelephonyUnavailable as exc:
        return str(exc)
    if not found:
        return ("No phone is connected to this machine as a Bluetooth hands-free unit, so "
                "there is nothing to call through. Pair a phone and make sure it is connected; "
                "a phone that is only paired does not answer.")
    lines = []
    for path, name, address in found:
        lines.append(f"{name}" + (f" ({address})" if address else ""))
        active = calls(path)
        for _cpath, state, direction in active:
            lines.append(f"    {direction or 'call'} {state}".rstrip())
        if not active:
            lines.append("    no call in progress")
        for card, description, profile, _avail in bluez_cards():
            lines.append(f"    audio: card {card} {description}"
                         + (f" on profile {profile}" if profile else ""))
    return "\n".join(lines)


def _run(arguments: dict) -> str:
    if not isinstance(arguments, dict):
        return ("The bluetooth_call arguments were not an object; nothing was done. "
                "Pass an action and, for dial, a number.")
    allowed, refusal = _consent(ChronoaConfig())
    if not allowed:
        return refusal

    action = str(arguments.get("action") or "status").strip().lower()
    number = str(arguments.get("number") or "").strip()

    if action == "status":
        return _summary()

    try:
        found = gateways()
    except TelephonyUnavailable as exc:
        return f"{exc}. Nothing was done."

    # Say what specifically was not done. A single "Nothing was done" for a dial,
    # an answer and a hangup reads as a refusal of the whole skill rather than of
    # the one action, and leaves the caller unable to tell whether the phone was
    # unreachable or the assistant declined.
    not_done = {
        "dial": "Nothing was dialled.",
        "answer": "Nothing was answered.",
        "hangup": "Nothing was hung up.",
    }.get(action, "Nothing was done.")

    if not found:
        return ("No phone is connected to this machine as a Bluetooth hands-free unit, so "
                f"there is nothing to call through. {not_done} A phone that is only paired, "
                "and not connected, does not answer - check that it is switched on and in range.")
    if len(found) > 1:
        return (f"{len(found)} phones are connected ({', '.join(n for _, n, _ in found)}), "
                f"so I did not pick one. {not_done}")
    path, name, address = found[0]

    if action == "audio":
        to = str(arguments.get("to") or "").strip().lower()
        if to in ("pc", "computer", "this computer", "laptop"):
            to = "pc"
        if to in ("pc", "phone"):
            ok, why = set_audio_route(path, to)
            if not ok:
                return f"Could not move the call audio to {'this computer' if to == 'pc' else 'the phone'}: {why}."
            return ("Call audio is on this computer - its speaker and microphone."
                    if to == "pc" else "Call audio is on the phone.")
        where, state = audio_route(path)
        return (f"Call audio: {'this computer' if where == 'pc' else 'the phone'} "
                f"(transport {state or 'unknown'}).\n" + _audio_report(address))
    if action == "profile":
        wanted = str(arguments.get("profile") or "").strip()
        if not wanted:
            return ("Which profile? Pass profile, e.g. profile='hfp_hf'. Nothing was changed.")
        found = bluez_cards()
        if not found:
            return ("No Bluetooth audio card is present, so there is no profile to switch. "
                    "A phone has to be connected over Bluetooth first.")
        if len(found) > 1 and not arguments.get("card"):
            return (f"{len(found)} Bluetooth audio cards are present: "
                    + ", ".join(f"{c} ({d})" for c, d, _, _ in found)
                    + ". Nothing was changed - pass card to say which.")
        card = str(arguments.get("card") or found[0][0])
        ok, why = set_profile(card, wanted)
        if not ok:
            return f"Could not set card {card} to {wanted}: {why}. Nothing was changed."
        return f"Card {card} is now on {wanted}."

    if action == "dial":
        if not number:
            return ("Dial what? Pass number, e.g. number='+441234567890'. Nothing was dialled.")
        if not _NUMBER.match(number):
            return ("That is not a dialable number - only digits, spaces, and + * # , are "
                    "allowed. Nothing was dialled.")
        ok, why = approvals.confirm(
            "Make this call from your phone?",
            f"Calling {number} through {name} over Bluetooth. "
            "Both of you will hear it on this computer's speakers.")
        if not ok:
            return f"Not dialled: {why or 'nobody confirmed'}. Nothing was done."
        # busctl takes the signature before the argument; without "s" the number
        # itself was read as the signature and no call could ever be placed.
        done, why = _gateway_call(path, "Dial", "s", number)
        if not done:
            return f"Could not dial {number} through {name}: {why}. Nothing was dialled."
        return f"Dialled {number} through {name} over Bluetooth."

    if action == "answer":
        # The ringing call's own Answer. `ReleaseAndAnswer` (the old route) ends
        # the *active* call to take a waiting one - right for call waiting, wrong
        # for a phone that is simply ringing - so it is only the fallback.
        ringing = [c for c, state, _d in calls(path) if state in ("incoming", "waiting")]
        if ringing:
            done, why = _object_call(ringing[0], CALL_IFACE, "Answer")
        else:
            done, why = _gateway_call(path, "ReleaseAndAnswer")
        if not done:
            return (f"Could not answer through {name}: {why}. "
                    "There may be no call waiting - the phone only offers this while one rings.")
        return f"Answered through {name}."

    if action == "hangup":
        # `HangupAll`: introspected on a real gateway (PipeWire 1.4), the method
        # this used to call, `SendReleaseAndHangup`, does not exist on it.
        done, why = _gateway_call(path, "HangupAll")
        if not done:
            return f"Could not hang up through {name}: {why}. Nothing was done."
        return f"Hung up through {name}."

    return f"action must be status, dial, answer or hangup, not {action!r}."


SCHEMA = {
    "type": "function",
    "function": {
        "name": "bluetooth_call",
        "description": (
            "Use a paired Android phone as a Bluetooth speakerphone, with no cable and no "
            "app on the phone. 'status' says whether a phone is connected as a hands-free unit, "
            "whether a call is in progress, and which audio profile its call audio arrived on. "
            "'dial' places a call - 'dial number=\"+441234567890\"' - asking you to confirm first, "
            "because it rings someone. 'answer' and 'hangup' work on a call already in progress. "
            "'audio' reports where the call's voice is going and names anything missing from that "
            "path, and 'profile' switches the card between wide-band hfp_hf and narrow-band "
            "hsp_hs. "
            "The phone stays the radio; this routes the voice to this computer's speakers and "
            "microphone. Needs the phone connected over Bluetooth, not merely paired, and the "
            "'bluetooth-call-enabled' consent key."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "dial", "answer", "hangup", "audio", "profile"],
                    "description": "status: who is connected and what is happening. dial: place a "
                                   "call. answer: pick up a ringing call. hangup: end the call. "
                                   "audio: report where the call audio is going and what is "
                                   "missing from that path. profile: switch the Bluetooth audio "
                                   "card to a profile such as hfp_hf or hsp_hs.",
                },
                "card": {"type": "string",
                         "description": "For profile: which audio card, by number, from the "
                                        "audio report. Only needed when more than one is present."},
                "profile": {"type": "string",
                            "description": "For profile: the profile to switch to, e.g. 'hfp_hf' "
                                           "(wide-band) or 'hsp_hs' (narrow-band)."},
                "to": {"type": "string", "enum": ["pc", "phone"],
                       "description": "For audio: move the call's audio to this computer's "
                                      "speaker and microphone ('pc') or back to the phone."},
                "number": {
                    "type": "string",
                    "description": "For dial: the number to call, e.g. '+441234567890'. Only "
                                   "digits, spaces and + * # , are accepted.",
                },
            },
            "required": ["action"],
        },
    },
}

SKILLS = [Skill(name="bluetooth_call", schema=SCHEMA, run=_run)]