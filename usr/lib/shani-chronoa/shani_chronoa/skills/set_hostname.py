"""Skill: report or change this machine's name.

`system_info` reads the hostname; nothing could change it. The name is what the
machine is called on the network, in the terminal prompt, in Bluetooth pairing
lists and in KDE Connect/GSConnect - and a fresh install is often named
something nobody chose.

`hostnamectl set-hostname` goes through systemd-hostnamed and polkit, so on a
desktop the user is asked for their password by the desktop itself; Chronoa
never handles it. On Shanios `/etc` is an overlay whose upper layer lives on
`@data`, so the name survives slot switches.

Gated by `hostname-control-enabled`: other machines on the network see the new
name, and some services (SSH known_hosts on other machines, network shares)
notice the change.

Honesty rules: a name is validated as a single RFC 1123 label before being
passed on - `hostnamectl` would otherwise turn "My Laptop!" into a different
name than the one asked for and report success; the name is read back.
"""

from __future__ import annotations

import re
import shutil
import socket
import subprocess

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "hostname-control-enabled"
_TIMEOUT = 60  # polkit may be waiting for the person to type a password
_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "set_hostname",
        "description": (
            "Report this computer's name (hostname), or rename it - the name "
            "other devices on the network and in Bluetooth see. The name must be "
            "letters, digits and hyphens only. Renaming requires the "
            "'hostname-control-enabled' consent key and the administrator "
            "password; reporting needs neither."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["status", "set"],
                           "description": "status (default) or set."},
                "name": {"type": "string", "description": "set: the new name, e.g. 'kitchen-laptop'."},
            },
        },
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (f"renaming this computer is turned off (enable '{_CONSENT_KEY}' in "
                       f"Settings). Other machines on the network see the new name.")
    return True, ""


def current_hostname() -> str:
    if shutil.which("hostnamectl") is not None:
        try:
            proc = subprocess.run(["hostnamectl", "hostname"], capture_output=True,
                                  text=True, timeout=15, check=False)
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
        except (subprocess.TimeoutExpired, OSError):
            pass
    return socket.gethostname() or "UNKNOWN"


def validate(name: str) -> "str | None":
    """The reason `name` is not a usable hostname, or None."""
    if not name:
        return "no name was given"
    if name != name.lower():
        return "hostnames are lower-case; use " + repr(name.lower())
    if not _LABEL.match(name):
        return ("a hostname is 1-63 letters, digits and hyphens, not starting or "
                "ending with a hyphen (no spaces, dots or underscores)")
    return None


def _run(arguments: dict) -> str:
    action = (arguments.get("action") or "status").strip().lower()
    before = current_hostname()
    if action == "status":
        return f"This computer is named {before}."
    if action != "set":
        return f"Action must be status or set, not {action!r}."
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to rename the computer: {reason}"
    name = (arguments.get("name") or "").strip()
    problem = validate(name)
    if problem:
        return f"Refusing to use {name!r} as the computer's name: {problem}. It is still {before}."
    if name == before:
        return f"The computer is already named {name}, so nothing was changed."
    if shutil.which("hostnamectl") is None:
        return files.tool_missing("hostnamectl", "rename the computer")
    try:
        proc = subprocess.run(["hostnamectl", "set-hostname", name], capture_output=True,
                              text=True, timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return f"hostnamectl did not finish within {_TIMEOUT}s (was a password prompt left open?). It is still {current_hostname()}."
    except OSError as exc:
        return f"hostnamectl could not run ({exc}); the name is still {before}."
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return f"Could not rename the computer: {detail or 'no detail'}. It is still {before}."
    after = current_hostname()
    if after == name:
        return (f"The computer is now named {after} (verified by reading it back). "
                f"Other devices may show the old name until they look again.")
    return f"hostnamectl reported success but the name reads back as {after}, so this is not verified."


def _post_condition(arguments: dict):
    if (arguments.get("action") or "status").strip().lower() != "set":
        return None
    name = (arguments.get("name") or "").strip()
    if validate(name):
        return None
    now = current_hostname()
    return now == name, f"read back: {now}"


POST_CONDITION = _post_condition

SKILLS = [Skill(name="set_hostname", schema=SCHEMA, run=_run)]
