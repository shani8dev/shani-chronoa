"""Privacy actuators: mute the microphone, disable the camera, blank the screen.

These three are the actuators that exist so a person can *stop* something rather
than start it, and they are grouped together for a reason — they are the
controls a user reaches for in a meeting, and on a machine where a camera is
merely powered but not disabled, a green LED means nothing.

**Muting an audio source is not the same as muting the device.** `wpctl set-mute
@DEFAULT_AUDIO_SOURCE@ 1` sets the *stream* mute, which is a user-level
statement of intent that any client can and does override — a conferencing app
that starts a new stream will start unmuted. The hardware-level switch is
different, and on a laptop that is usually a keyboard function key that sets an
ALSA control the kernel exposes. So this skill does the thing that works
without root, and **says which one it did**, rather than reporting "muted" in a
way that implies the hardware is off.

**Disabling a camera needs root, and saying "disabled" when nothing changed is
the worst outcome available here.** This is the same failure the brightness
skill documents: `/sys/class/video4linux/*/disable` exists only on a subset of
drivers, is root-owned where it does exist, and on this machine it does not
exist at all — the driver here does not support the disable path. So the skill
reports per-device whether a control was found, reports the EACCES or ENOENT
distinction, and never claims a camera is off when it is not.

**Blanking the screen is display-server specific, and the three desktops differ.**
`xset dpms force off` works on X11 only and is absent under Wayland, which is
the default on all four ShaniOS desktop profiles. The Wayland path is
`org.freedesktop.portal.ScreenCast`, which requires a **user prompt** — so
blanking cannot be automated under Wayland without a permission dialog, and this
skill says so instead of trying to route around a security boundary. What *is*
portable is the idle timeout in the desktop's own settings, and that is what it
offers.

Every action here is reversible and the skill states the reversal, because a
user who mutes their mic in a meeting needs it unmuted afterwards and cannot
find the switch again.
"""

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from shani_chronoa.config import ChronoaConfig
from shani_chronoa import pipewire
from shani_chronoa.skills import Skill

logger = logging.getLogger(__name__)

_VIDEO4LINUX = Path("/sys/class/video4linux")
_TIMEOUT = 20

SCHEMA = {
    "type": "function",
    "function": {
        "name": "set_privacy",
        "description": (
            "Control the machine's privacy: mute or unmute the microphone, "
            "enable or disable a camera, or blank the screen. Muting the "
            "microphone is a user-level stream mute that any new application "
            "can override, and the reply says so rather than implying the "
            "hardware is off. Disabling a camera needs root and is only "
            "possible on drivers that expose a disable control, so a device "
            "with no such control is reported as unsupported rather than "
            "disabled. Blanking works on X11; under Wayland it would need a "
            "permission prompt, so the idle timeout is offered instead. Every "
            "action says how to undo it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "status", "mute_mic", "unmute_mic",
                        "disable_camera", "enable_camera", "blank_screen",
                    ],
                    "description": "What to do.",
                },
                "device": {
                    "type": "string",
                    "description": (
                        "Camera name or /dev/videoN, for the camera actions. "
                        "Omit to apply to every camera."
                    ),
                },
            },
            "required": ["action"],
        },
    },
}


def _wpctl(*arguments: str) -> subprocess.CompletedProcess:
    return pipewire.run_wpctl(*arguments)


def _audio_targets() -> List[dict]:
    """Audio *sources* from `wpctl status`, with their ids and mute state.

    Two formats have to be handled, and the first version of this only handled
    the second and so reported "no audio source" on a machine that had two:
    `wpctl status` prints sources as

        │  *   56. Some Microphone [vol: 0.43]

    with a numeric id, a `*` marking the default, and no `@NAME@` token at
    all. The `@DEFAULT_AUDIO_SOURCE@` spelling is what the *other* wpctl
    subcommands accept as input, not what `status` prints. Both are read, so a
    pipewire that prints tokens is not silently invisible.
    """
    if shutil.which("wpctl") is None:
        return []
    try:
        proc = _wpctl("status")
    except (OSError, subprocess.SubprocessError):
        return []
    text = proc.stdout or ""
    if not text.strip():
        return []

    found: List[dict] = []
    in_sources = False
    # `wpctl status` draws a tree, so every line carries box-drawing characters:
    # `├─ Sources:` for a heading and `│  *   56. Name [vol: 0.43]` for an
    # entry. Stripping only `│` left the `├─ ` on the heading, which then failed
    # a "no spaces" test and so was never recognised as a section.
    box = "│├└─ "
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        bare = line
        for character in box:
            bare = bare.replace(character, " ")
        bare = bare.replace("*", " ").strip()
        if bare.endswith(":"):
            in_sources = bare.rstrip(":").strip() == "Sources"
            continue
        if not in_sources:
            continue
        token = bare.split()[0] if bare.split() else ""
        # `@DEFAULT_AUDIO_SOURCE@.alsa_input.pci-0000_00_1f.3.analog-stereo` —
        # the token is the `@NAME@` *prefix* and the node path follows it, so
        # an endswith("@") test never matches this form.
        named = re.match(r"^(@[A-Za-z0-9_]+@)", token)
        if named:
            described = bare[len(named.group(1)):].strip()
            found.append({"id": named.group(1),
                          "name": described,
                          "default": "*" in line,
                          "muted": pipewire.status_muted(described)})
            continue
        match = re.match(r"^(\d+)\.\s+(.*)$", bare)
        if not match:
            continue
        name = re.sub(r"\s*\[vol:.*$", "", match.group(2)).strip()
        found.append({
            "id": match.group(1),
            "name": name,
            "default": "*" in line,
            "muted": pipewire.status_muted(match.group(2)),
        })
    return found


def _mute_mic(muted: bool) -> dict:
    if shutil.which("wpctl") is None:
        return {"text": (
            "Cannot mute the microphone: wpctl is not installed. It comes with "
            "the wireplumber package, which shani-multimedia ships. Until "
            "then the hardware switch on the keyboard is the only route, and "
            "that is a manual action this skill cannot take."
        ), "changed": [], "failed": []}
    sources = _audio_targets()
    if not sources:
        return {"text": (
            "No audio source is available to mute. That usually means no "
            "capture device is present or the audio server is not running, "
            "which is different from a microphone that is already muted."
        ), "changed": [], "failed": []}
    done, failed = [], []
    for source in sources:
        try:
            proc = _wpctl("set-mute", source["id"], "1" if muted else "0")
        except (OSError, subprocess.SubprocessError) as exc:
            failed.append(f"{source['name']} ({exc})")
            continue
        if proc.returncode == 0:
            done.append(source["name"])
        else:
            failed.append(source["name"])
    state = "muted" if muted else "unmuted"
    lines = []
    if done:
        lines.append(f"Microphone {state}: {', '.join(done)}.")
    if failed:
        lines.append(f"Could not change {', '.join(failed)}.")
    if not muted:
        lines.append(
            "Unmuted. To undo a mute, run this again with action=mute_mic."
        )
    lines.append(
        "Note this is the user-level stream mute: an application that opens a "
        "new capture stream starts unmuted, so this is a statement of intent "
        "rather than a hardware switch."
    )
    return {"text": " ".join(lines), "changed": done, "failed": failed}


def _cameras() -> List[dict]:
    try:
        entries = sorted(Path(_VIDEO4LINUX).glob("video*"))
    except OSError:
        return []
    found = []
    for entry in entries:
        name = None
        try:
            name = (entry / "name").read_text().strip()
        except OSError:
            pass
        found.append({
            "node": entry.name,
            "path": f"/dev/{entry.name}",
            "name": name or "unnamed",
            "disable_control": (entry / "disable").exists(),
        })
    return found


def _select(cameras: "List[dict]", device: "Optional[str]") -> "tuple[list, str]":
    """The cameras a named `device` refers to, and a reason if there are none.

    **A name that matches nothing is a refusal, never an empty action.** The
    schema advertises `device` as "Camera name or /dev/videoN ... Omit to apply
    to every camera", and the first version of that promise had no reader at all
    - `_set_camera` acted on every camera whatever was passed, so a user asking
    to disable the external webcam alone had *every* camera disabled and was
    told the right thing had happened. On a privacy action that is the bad
    direction for a dead control to be wrong in.

    The three spellings are matched because all three are what a person or a
    model supplies: the node (`video0`), the path (`/dev/video0`) and the
    driver's own name, which may carry a version and needs a substring match
    (`UVC Camera (046d:0825)` is what `/sys/class/video4linux/video0/name`
    reads, and a user says "logitech").
    """
    if not device:
        return cameras, ""
    wanted = device.strip()
    # **The blank check is on the stripped value, not on the argument.** With
    # `if not device` alone, a `device` of `"  "` falls through to the matcher,
    # where `"" in "any name"` is True for every camera - so the omission works
    # by an accident of substring semantics that holds today and is one edit
    # away from silently selecting nothing. A whitespace-only value is the
    # omission, and it is checked as such rather than relied upon.
    if not wanted:
        return cameras, ""
    lowered = wanted.lower()
    matched = [c for c in cameras
               if c["node"] == wanted or c["path"] == wanted
               or c["name"].lower() == lowered
               or lowered in c["name"].lower()]
    if not matched:
        known = ", ".join(f"{c['name']} ({c['path']})" for c in cameras)
        return [], (f"No camera matches {wanted!r}. This machine has: {known}. "
                    "Nothing was changed.")
    return matched, ""


def _set_camera(enable: bool, device: "Optional[str]" = None) -> dict:
    # Turning a camera *off* is protective and is never gated: a user who asks
    # for it is the consent, and gating it would mean the one action nobody
    # objects to is the one an assistant cannot take. Turning one *on* is the
    # opposite — it re-enables a sensor that may have been deliberately
    # disabled — so it is refused unless the camera consent is granted, which is
    # the existing "may Chronoa use the camera" consent rather than a new one
    # invented for this skill.
    #
    # That consent is the `capture` sense. It was `camera` until the camera
    # enumeration and device-contention senses were merged, and leaving the old
    # name here made this path permanently unreachable: `camera` is no longer a
    # registered sense, so `sense_allowed("camera")` is False no matter what any
    # key is set, and the refusal below told the user to enable a setting that
    # no longer has a row. Consent gates are a naming contract, not a lookup.
    if enable:
        config = ChronoaConfig()
        if not config.sense_allowed("capture"):
            return {"text": (
                "Refusing to enable a camera: the capture sense is turned off. "
                "Turning a sensor on is the opposite of what this skill is "
                "usually for, and it re-enables something that may have been "
                "disabled on purpose. Enable 'capture-sense-enabled' in Settings "
                "if you do want the assistant to use the camera at all. "
                "Disabling one needs no permission and is always available."
            ), "changed": [], "unsupported": []}
    cameras = _cameras()
    if not cameras:
        return {"text": (
            "No camera was found under /sys/class/video4linux. That means the "
            "machine has no camera the kernel knows about, which is different "
            "from a camera that exists but could not be reached."
        ), "changed": [], "unsupported": []}
    cameras, unmatched = _select(cameras, device)
    if unmatched:
        return {"text": unmatched, "changed": [], "unsupported": []}
    # The kernel's disable file is 1 = DISABLED, so  must write 0.
    # Inverted, this skill would switch a camera ON when asked to disable it.
    value = "0" if enable else "1"
    verb = "enabled" if enable else "disabled"
    reversed_verb = "disabled" if enable else "enabled"
    lines, changed, unsupported = [], [], []
    for camera in cameras:
        if not camera["disable_control"]:
            unsupported.append(camera)
            continue
        node = Path(_VIDEO4LINUX) / camera["node"] / "disable"
        try:
            node.write_text(value + "\n")
            changed.append(camera)
        except PermissionError:
            lines.append(
                f"{camera['name']} ({camera['path']}): needs root - the "
                f"disable control is root-owned, so nothing was changed."
            )
        except OSError as exc:
            lines.append(f"{camera['name']} ({camera['path']}): {exc.strerror}.")
    if changed:
        lines.insert(0, f"{verb}: {', '.join(c['name'] for c in changed)}.")
        lines.append(f"To undo, run this again to get it {reversed_verb}.")
    if unsupported:
        names = ", ".join(f"{c['name']} ({c['path']})" for c in unsupported)
        lines.append(
            f"{len(unsupported)} camera(s) expose no disable control at all, so "
            f"they were NOT {verb}: {names}. A driver without that control "
            f"cannot be turned off this way, and claiming otherwise would be "
            f"the worst possible answer - a user who believes their camera is "
            f"off and is not."
        )
    if not changed and not lines:
        lines.append("Nothing was changed and no reason was reported.")
    return {"text": " ".join(lines), "changed": changed, "unsupported": unsupported}


def _session_type() -> str:
    """Which session this process is in, from its environment.

    Read from `os.environ` rather than a parent's, because the skill may run in
    a process whose parent is the assistant rather than the session.
    """
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "unknown"


def _blank_screen() -> dict:
    """Blank the display, or explain why it cannot be done here."""
    if shutil.which("xset") is not None:
        try:
            proc = subprocess.run(
                ["xset", "dpms", "force", "off"],
                capture_output=True, text=True, timeout=_TIMEOUT, check=False,
            )
            if proc.returncode == 0:
                return {"text": (
                    "Screen blanked. Any key press or mouse movement wakes it. "
                    "To change when it blanks on its own, the idle timeout is "
                    "in your desktop's power settings."
                )}
        except (OSError, subprocess.SubprocessError):
            pass

    if _session_type() == "wayland":
        return {"text": (
            "Cannot blank the screen without a permission dialog. Under "
            "Wayland the only route is the ScreenCast portal, which prompts "
            "the user every time - and routing around that would be defeating "
            "a security boundary, so this skill will not. What you can change "
            "is how long the screen waits before blanking, in your desktop's "
            "power settings."
        )}
    return {"text": (
        "Cannot blank the screen: xset is not installed and this session is "
        "not X11. The idle timeout in your desktop's power settings is the "
        "portable route."
    )}


def _status() -> str:
    """What the privacy controls currently are, without changing anything."""
    cameras = _cameras()
    mic = _audio_targets()
    lines = []
    if mic:
        for source in mic:
            muted = source.get("muted")
            state = ("unknown" if muted is None
                     else ("muted" if muted else "unmuted"))
            default = " (default)" if source["default"] else ""
            lines.append(
                f"microphone {source['id']}: {source['name']}{default} - {state}"
            )
    else:
        lines.append(
            "no audio source available, so the microphone state is unknown"
        )
    if cameras:
        for camera in cameras:
            lines.append(
                f"camera {camera['path']}: {camera['name']}, "
                + ("can be disabled" if camera["disable_control"]
                   else "exposes no disable control, so it cannot be turned off")
            )
    else:
        lines.append("no camera found")
    return " ".join(lines)


def _run(arguments: dict) -> str:
    action = str(arguments.get("action") or "status").strip()
    if action == "status":
        return _status()
    if action in ("mute_mic", "unmute_mic"):
        result = _mute_mic(action == "mute_mic")
    elif action in ("disable_camera", "enable_camera"):
        # **The `device` argument, which the schema has always advertised.**
        # Passing it here rather than dropping it is what makes the control
        # real: `test_no_dead_schema_parameters` flags the first version of
        # this call, because a parameter the handler never reads is a control
        # the model can move to no effect.
        result = _set_camera(action == "enable_camera",
                             arguments.get("device"))
    elif action == "blank_screen":
        result = _blank_screen()
    else:
        return (
            f"Unknown action {action!r}. Valid actions are: status, mute_mic, "
            f"unmute_mic, disable_camera, enable_camera, blank_screen."
        )
    return result["text"] if isinstance(result, dict) else result


def _post_privacy(arguments: dict):
    """Read the state back, rather than trusting the write.

    This is the first mutator given a post-condition, and it is the cheapest
    kind there is: the effect is a change to the audio server's mute state, so
    verifying it means reading that state back with the same tool the write
    used. That turns "Chronoa believes it muted the microphone" into something
    checkable, which is what makes the recorded verdict `verified` rather than
    `unverified`.

    **92% of the recorded traffic is `unverified`, and that is the reason
    nothing in the learning layer can train.** `learning.train_and_save()`
    refuses with "the training split contains no example of verified", and
    `evaluate_bandit()` reports `beats_chance: False`. Both are downstream of
    mutators like this one having no post-condition, so the gap is worth
    closing one skill at a time rather than by assertion.

    Returns `(ok, evidence)` because "it says it is muted" and "the audio server
    says it is muted" are different claims, and the evidence should say which.

    **It used to read a GSettings key.** The docstring said "the effect is a
    GSettings key, so verifying it means reading that key" - but the effect is
    not a GSettings key. `_mute_mic` runs `wpctl set-mute`, and
    `privacy-mic-muted` is written by nothing and is not even declared in the
    schema, so `get_bool` took its Python default on every call. The
    consequence was that the check could not distinguish its two cases: muting
    was reported as FAILED every single time even when the microphone really was
    muted, and unmuting was reported as VERIFIED every single time without
    having checked anything. The effect is now read back from the audio server,
    which is where the write put it.
    """
    action = str(arguments.get("action") or "").strip()
    if action not in ("mute_mic", "unmute_mic"):
        return None  # nothing to check: not a mutating action

    expected = action == "mute_mic"
    try:
        targets = _audio_targets()
    except Exception as exc:  # noqa: BLE001 - cannot check is not a failure
        # **None, not a verdict** - see the note on the other "could not
        # determine" branch below. A check that could not answer is UNVERIFIED.
        logger.warning("could not read the microphone state back: %s",
                       type(exc).__name__)
        return None
    if not targets:
        # "I could not determine" is not "the effect is absent", so this is a
        # refusal to answer rather than a verdict.
        return None

    states = []
    for target in targets:
        muted = target.get("muted")
        if muted is None:
            continue
        states.append((str(target.get("name") or target["id"]), muted))
    if not states:
        # **None, not a verdict.** The state could not be read, and a check
        # that could not answer is UNVERIFIED - reporting it as a failed write
        # tells the caller the microphone is not muted, which is the opposite
        # of what is known. `verification.verify` draws exactly this line: a
        # callable that returns None is "this call changed nothing I can
        # check", never FAILED.
        return None

    names = ", ".join(name for name, _ in states)
    if all(muted == expected for _, muted in states):
        return (True, f"microphone is {'muted' if expected else 'unmuted'} on "
                      f"{names}, read back from the audio server")
    return (False, f"expected the microphone to be "
                   f"{'muted' if expected else 'unmuted'}, but the audio "
                   f"server reports {names} as "
                   f"{'muted' if any(m for _, m in states) else 'unmuted'}")


POST_CONDITION = _post_privacy

SKILLS = [Skill(name="set_privacy", schema=SCHEMA, run=_run)]
