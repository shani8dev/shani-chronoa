"""ADB workflow: the smallest useful, opt-in bridge to an Android phone.

`android_device` already exposes shani-link's full phone engine through the
`phone-control-enabled` switch, and `adb_pair` (in the app and in Shani Link)
walks a person through pairing. This P3 integration is the narrow assistant
half that was missing: a way to see the ADB state and to copy one file in either
direction, without a second consent switch and without a shell.

The phone's owner is the first gate - Android must have USB or wireless
debugging on and must have accepted this computer's key. Chronoa's own gate is
the same default-off `phone-control-enabled` key every other phone skill uses,
so a fresh install cannot reach a phone by accident. A file copy additionally
asks for an explicit confirmation: it names the local file and the remote
folder, and a dismissal is a no.

Every call is an argv list with a timeout - never `shell=True`, never a command
string built from model text. Local paths are confined to the home directory;
remote paths must be a plain path under `/sdcard/` or `/storage/emulated/` with
no `..` and no shell metacharacters. That is not a general ADB shell: it is the
one thing a person actually asks for, "get this file off my phone".
"""

from __future__ import annotations

import re
import shutil
import subprocess

from shani_chronoa import approvals, files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

CONSENT_KEY = "phone-control-enabled"
_TIMEOUT = 120
_REMOTE = re.compile(r"^/(sdcard|storage/emulated)/[A-Za-z0-9._/ -]+$")
_ACTIONS = ("status", "pair", "push", "pull")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "adb_workflow",
        "description": (
            "A small Android Debug Bridge workflow. status: connected devices and their state; pair: "
            "the steps to authorise this computer; push and pull: copy one file between this computer "
            "and the phone. Needs 'phone-control-enabled' and a yes for each copy. Local paths stay "
            "inside your home; remote paths must be under /sdcard/."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(_ACTIONS)},
                "serial": {
                    "type": "string",
                    "description": "Which phone, when more than one is connected.",
                },
                "local": {
                    "type": "string",
                    "description": "push/pull: a file path inside your home",
                },
                "remote": {
                    "type": "string",
                    "description": "push/pull: a path under /sdcard/",
                },
            },
            "required": ["action"],
        },
    },
}


def _consent(config: ChronoaConfig | None = None) -> tuple[bool, str]:
    from shani_chronoa import capabilities

    cfg = config or ChronoaConfig()
    if not cfg.get_bool(CONSENT_KEY, False):
        return False, (
            f"using your phone is off - turn on "
            f"\u201c{capabilities.gate_label(CONSENT_KEY)}\u201d in Settings"
        )
    return True, ""


def _adb(argv: list[str]) -> str:
    binary = shutil.which("adb")
    if not binary:
        return files.tool_missing("adb", "talk to an Android phone")
    try:
        proc = subprocess.run(
            [binary, *argv],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"adb could not answer: {exc}"
    body = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    if proc.returncode != 0:
        return f"adb exited {proc.returncode}: {body[:300]}"
    return body[:2000] or "adb succeeded."


def _serial_arg(serial: str) -> list[str]:
    text = re.fullmatch(r"[A-Za-z0-9:._-]{1,128}", serial.strip() or "")
    if serial.strip() and not text:
        raise ValueError("that device id is not a serial adb would use")
    return ["-s", serial.strip()] if serial.strip() else []


def _remote_path(raw: str) -> str:
    path = str(raw or "").strip()
    if not _REMOTE.fullmatch(path) or ".." in path or "//" in path:
        return ""
    return path


def _run(arguments: dict) -> str:
    args = arguments if isinstance(arguments, dict) else {}
    action = str(args.get("action") or "").strip().lower()
    allowed, why = _consent()
    if not allowed:
        return f"Refusing: {why}."
    if action not in _ACTIONS:
        return f"action must be one of {', '.join(_ACTIONS)}."

    try:
        serial = _serial_arg(str(args.get("serial") or ""))
    except ValueError as exc:
        return str(exc)

    if action == "pair":
        return (
            "On the phone: Settings -> Developer options -> Wireless debugging (or connect "
            "by USB and accept this computer's key). Then run 'adb pair HOST:PORT CODE' "
            "with the code the phone shows, and 'adb connect HOST:PORT'. Chronoa does not "
            "type that code for you. Current state: " + _adb(["devices", "-l"])
        )

    if action == "status":
        body = _adb(["devices", "-l"])
        if "List of devices attached" not in body:
            return body
        lines = [line.strip() for line in body.splitlines()[1:] if line.strip()]
        ready = [
            line
            for line in lines
            if len(line.split()) > 1 and line.split()[1] == "device"
        ]
        if not ready:
            return "No phone is authorised for adb yet. " + (
                "\n".join(lines) if lines else body
            )
        return f"{len(ready)} phone(s) ready:\n" + "\n".join(ready)

    remote = _remote_path(str(args.get("remote") or ""))
    if not remote:
        return "Give a plain remote path under /sdcard/ or /storage/emulated/."
    try:
        local = files.resolve_in_home(str(args.get("local") or ""))
    except files.PathProblem as exc:
        return str(exc)
    if action == "push" and not local.is_file():
        return f"{local} is not a file."
    if action == "pull" and local.exists():
        return f"{local} already exists; give a new file name."

    direction = "to" if action == "push" else "from"
    ok, reason = approvals.confirm(
        "Copy a file with ADB",
        f"Copy {local.name} {direction} your phone at {remote}? Allow once?",
    )
    if not ok:
        return f"Nothing was copied: {reason}."
    pair = (
        [action, str(local), remote]
        if action == "push"
        else [action, remote, str(local)]
    )
    body = _adb([*serial, *pair])
    if body.startswith("adb exited") or body.startswith("adb could not"):
        return body
    return f"{action.capitalize()}ed {local.name} {direction} {remote}.\n{body[:400]}"


SKILLS = [Skill(name="adb_workflow", schema=SCHEMA, run=_run)]
