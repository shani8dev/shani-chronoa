"""Skills: read/set system output volume and mute state via wpctl."""

import logging
import re
import subprocess
from typing import NamedTuple

from shani_chronoa import pipewire
from shani_chronoa.skills import Skill

logger = logging.getLogger(__name__)


def _run_wpctl(*args: str) -> "subprocess.CompletedProcess[str]":
    return pipewire.run_wpctl(*args)


#: The device every one of these skills used before `device` existed, and still
#: the default. Worth keeping as the default rather than requiring a device:
#: "turn it down" almost always means the one the user is listening to.
_DEFAULT = "@DEFAULT_AUDIO_SINK@"


#: Returned by `_device` when the argument cannot be used. A sentinel rather
#: than a string, so a caller cannot forget to check: a device name that
#: happened to start with "Invalid device" would otherwise pass as valid.
class _Refusal(NamedTuple):
    message: str


def _device(arguments: dict, default: str = _DEFAULT):
    """Resolve the `device` argument, refusing anything that could be injected.

    The value reaches a wpctl command line, so it is validated rather than
    trusted. What is rejected is anything the shell would treat as structure -
    quotes, backticks, `$`, `;`, `|`, `&`, redirections. A name containing one
    of those is not a device name wpctl printed, so refusing costs nothing real.

    **A device is passed as its numeric node id**, because that is what `wpctl`
    accepts. Passing the human-readable name fails with

        Error: '...DisplayPort 3 Output' is not a valid number

    which is what the first version of this did and what the run against a real
    sink caught. The names are for *reading* - the audio sense reports them
    alongside their ids - and the id is what goes to the command.

    So the accepted form is digits and nothing else, which also happens to be
    the strongest possible guard: there is no string that is a number and also
    a command. Rejecting the metacharacters separately would be redundant.

    Names are still accepted as a convenience, resolved against `wpctl status`
    here, because a model reading a device list will naturally reach for the
    name it was shown. An unresolvable name is an error naming what to use
    instead, not a silent fall-through to the default - the default is a
    different device, and quietly acting on the wrong output is the failure
    this whole argument is about avoiding.
    """
    device = arguments.get("device")
    if device is None:
        return default
    if isinstance(device, int) and not isinstance(device, bool):
        return str(device)
    if not isinstance(device, str) or not device.strip():
        return _Refusal(
            f"Invalid device: expected a node id from `wpctl status`, not {device!r}."
        )
    device = device.strip()
    if device.isdigit():
        return device
    resolved = _resolve_name(device)
    if resolved is not None:
        return resolved
    return _Refusal(
        f"Unknown audio device {device!r}. Pass the numeric id instead - run "
        f"`wpctl status` and use the number before the name, for example 51."
    )


def _resolve_name(name: str) -> "str | None":
    """The node id for a device name, or None if no device answers to it."""
    try:
        result = _run_wpctl("status")
    except Exception as exc:  # noqa: BLE001 - an unreadable list is not fatal here
        logger.debug("could not resolve %r to an id: %s", name, exc)
        return None
    if result is None or result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        match = re.search(r"^[^0-9]*(\d+)\.\s+(.*?)\s*\[vol:", line)
        if match and match.group(2).strip() == name:
            return match.group(1)
    return None


def _run_get_volume(arguments: dict) -> str:
    device = _device(arguments)
    if isinstance(device, _Refusal):
        return device.message
    try:
        result = _run_wpctl("get-volume", device)
    except Exception as e:
        return f"Could not read volume: {e}"
    if result.returncode != 0:
        return f"Could not read volume for {device}."
    # `wpctl get-volume` prints the device name alongside the level, so the
    # name is repeated here deliberately: the caller asked about a specific
    # device, and on a four-output machine "0.50" alone does not say which one.
    return result.stdout.strip()


def _run_set_volume(arguments: dict) -> str:
    percent = arguments.get("percent")
    if isinstance(percent, bool) or not isinstance(percent, int):
        return "Invalid volume percentage: expected an integer between 0 and 100."
    device = _device(arguments)
    if isinstance(device, _Refusal):
        return device.message
    percent = max(0, min(percent, 100))
    try:
        result = _run_wpctl("set-volume", device, f"{percent}%")
    except Exception as e:
        return f"Failed to set volume: {e}"
    if result.returncode != 0:
        return f"Failed to set volume: {result.stderr.strip()}"
    # The device is named only when one was asked for. Printing
    # "@DEFAULT_AUDIO_SINK@" to a user is noise, and "Volume set to 50%." is
    # the answer they asked for; naming the device is for the case where they
    # named one, where not repeating it would be ambiguous.
    if device == _DEFAULT:
        return f"Volume set to {percent}%."
    return f"Volume set to {percent}% on {device}."


def _run_set_mute(arguments: dict) -> str:
    mute = arguments.get("mute")
    if not isinstance(mute, bool):
        return "Invalid mute argument: expected true or false."
    device = _device(arguments)
    if isinstance(device, _Refusal):
        return device.message
    try:
        result = _run_wpctl("set-mute", device, "1" if mute else "0")
    except Exception as e:
        return f"Failed to change mute state: {e}"
    if result.returncode != 0:
        return f"Failed to change mute state: {result.stderr.strip()}"
    return "Muted." if mute else "Unmuted."


SKILLS = [
    Skill(
        name="get_volume",
        schema={
            "type": "function",
            "function": {
                "name": "get_volume",
                "description": (
                    "Get the current volume and mute state of an audio output, "
                    "or the default one. On a machine with more than one output "
                    "- several monitors, headphones and a dock - 'the volume' is "
                    "ambiguous, so pass the device exactly as `wpctl status` "
                    "prints it to ask about a specific one."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "device": {
                            "type": "string",
                            "description": (
                                "Numeric node id from `wpctl status` - the "
                                "number before the name, e.g. 51. Omit for "
                                "the default output."
                            ),
                        },
                    },
                },
            },
        },
        run=_run_get_volume,
    ),
    Skill(
        name="set_volume",
        schema={
            "type": "function",
            "function": {
                "name": "set_volume",
                "description": "Set the system output volume to an exact percentage.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "percent": {"type": "integer", "description": "Target volume percentage, 0-100."},
                        "device": {
                            "type": "string",
                            "description": (
                                "Numeric node id from `wpctl status` - the "
                                "number before the name, e.g. 51. Omit for "
                                "the default output."
                            ),
                        },
                    },
                    "required": ["percent"],
                },
            },
        },
        run=_run_set_volume,
    ),
    Skill(
        name="set_mute",
        schema={
            "type": "function",
            "function": {
                "name": "set_mute",
                "description": "Mute or unmute the system output.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "mute": {"type": "boolean", "description": "true to mute, false to unmute."},
                        "device": {
                            "type": "string",
                            "description": (
                                "Numeric node id from `wpctl status` - the "
                                "number before the name, e.g. 51. Omit for "
                                "the default output."
                            ),
                        },
                    },
                    "required": ["mute"],
                },
            },
        },
        run=_run_set_mute,
    ),
]
