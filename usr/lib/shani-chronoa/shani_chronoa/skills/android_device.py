"""Skill: a USB/Wi-Fi-debugging Android phone over adb - info, battery, screenshot, apps, files, open a link.

Found by the CLI matrix: both images ship `adb` (android-tools) and nothing in
Chronoa used it. qwen-code's mobile-mcp drives a phone the same way; this keeps
to a fixed set of read-mostly verbs rather than taps and shell commands, so
"what's my phone's battery", "grab a screenshot from my phone" and "copy that
file to my phone" work without a free-form `adb shell`.

The phone itself is the first consent: adb only reaches a device whose owner
turned on USB debugging and accepted this computer's key. Chronoa's own gate is
`phone-control-enabled`, the same one the KDE Connect / GSConnect `phone` skill
uses.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "phone-control-enabled"
_ACTIONS = ("devices", "info", "screenshot", "apps", "push", "pull", "open_url")
_TIMEOUT = 60

SCHEMA = {
    "type": "function",
    "function": {
        "name": "android_device",
        "description": (
            "An Android phone connected with USB debugging (adb). devices: which are connected; info: model, "
            "Android version, battery, storage; screenshot: save the phone's screen as a PNG; apps: installed "
            "apps (query filters); push: copy a file to the phone (to, default /sdcard/Download); pull: copy a "
            "file from the phone; open_url: open a web link on the phone. Requires 'phone-control-enabled'."
        ),
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": list(_ACTIONS)},
            "serial": {"type": "string", "description": "Which device, when more than one is connected."},
            "path": {"type": "string"}, "to": {"type": "string"}, "url": {"type": "string"},
            "query": {"type": "string"}, "output": {"type": "string"},
        }, "required": ["action"]},
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    if not config.get_bool(_CONSENT_KEY, False):
        return False, f"using your phone is turned off (enable '{_CONSENT_KEY}' in Settings)"
    return True, ""


def _adb(args, serial: str = "", binary: bool = False, timeout: int = _TIMEOUT):
    argv = ["adb"] + (["-s", serial] if serial else []) + list(args)
    proc = subprocess.run(argv, capture_output=True, timeout=timeout, check=False, text=not binary)
    return proc


def _devices() -> "list[tuple[str, str, str]]":
    proc = _adb(["devices", "-l"])
    out = []
    for line in (proc.stdout or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            model = next((p.split(":", 1)[1] for p in parts[2:] if p.startswith("model:")), "")
            out.append((parts[0], parts[1], model.replace("_", " ")))
    return out


def _pick(serial: str) -> "tuple[str, str]":
    """(serial, problem): the one ready device, or why there is none."""
    devs = _devices()
    ready = [d for d in devs if d[1] == "device"]
    if serial:
        match = [d for d in devs if d[0] == serial]
        if not match:
            return "", f"no device {serial!r} is connected"
        return (serial, "") if match[0][1] == "device" else ("", _state_hint(match[0][1]))
    if not devs:
        return "", ("no Android device is connected - plug it in with USB debugging on "
                    "(Settings > Developer options), or pair it with 'adb pair'")
    if not ready:
        return "", _state_hint(devs[0][1])
    if len(ready) > 1:
        return "", "more than one device is connected: " + ", ".join(f"{d[0]} ({d[2]})" for d in ready) + \
            "; say which with serial"
    return ready[0][0], ""


def _state_hint(state: str) -> str:
    return {"unauthorized": "the phone has not accepted this computer yet - unlock it and allow USB debugging",
            "offline": "the phone is connected but offline - reconnect the cable"}.get(state, f"the device is {state}")


def _shell(serial: str, *cmd: str) -> str:
    return (_adb(["shell", *cmd], serial).stdout or "").strip()


def _run(arguments: dict) -> str:
    action = (arguments.get("action") or "").strip().lower()
    if action not in _ACTIONS:
        return f"action must be one of {', '.join(_ACTIONS)}."
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing: {reason}."
    if shutil.which("adb") is None:
        return "adb (android-tools) is not installed."
    try:
        if action == "devices":
            devs = _devices()
            if not devs:
                return "No Android device is connected."
            return "Connected:\n" + "\n".join(f"- {s} {m or ''} ({st if st != 'device' else 'ready'})"
                                             for s, st, m in devs)
        serial, problem = _pick((arguments.get("serial") or "").strip())
        if problem:
            return f"Cannot reach a phone: {problem}."
        if action == "info":
            prop = lambda k: _shell(serial, "getprop", k)
            battery = _shell(serial, "dumpsys", "battery")
            level = re.search(r"level:\s*(\d+)", battery)
            charging = re.search(r"(AC|USB|Wireless) powered:\s*true", battery)
            df = _shell(serial, "df", "-h", "/sdcard").splitlines()
            free = df[-1].split()[3] if len(df) > 1 and len(df[-1].split()) > 3 else "?"
            return (f"{prop('ro.product.manufacturer')} {prop('ro.product.model')}, Android "
                    f"{prop('ro.build.version.release')} (security patch {prop('ro.build.version.security_patch')}); "
                    f"battery {level.group(1) + '%' if level else '?'}"
                    f"{', charging over ' + charging.group(1) if charging else ''}; {free} free on internal storage.")
        if action == "screenshot":
            target = files.resolve_in_home(arguments.get("output") or
                                           f"~/Pictures/phone-{time.strftime('%Y%m%d-%H%M%S')}.png")
            if target.exists():
                return f"{target} already exists."
            proc = _adb(["exec-out", "screencap", "-p"], serial, binary=True)
            if proc.returncode != 0 or not proc.stdout.startswith(b"\x89PNG"):
                return "The phone did not return a screenshot (is it unlocked?)."
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(proc.stdout)
            return f"Saved the phone's screen to {target} ({files.human_size(len(proc.stdout))})."
        if action == "apps":
            query = (arguments.get("query") or "").lower()
            pkgs = sorted(l.split(":", 1)[1] for l in _shell(serial, "pm", "list", "packages", "-3").splitlines()
                          if l.startswith("package:"))
            pkgs = [p for p in pkgs if query in p.lower()]
            return f"{len(pkgs)} installed app(s){' matching ' + repr(query) if query else ''}:\n" + \
                "\n".join(f"- {p}" for p in pkgs[:80]) if pkgs else "No matching apps."
        if action == "push":
            source = files.resolve_in_home(arguments.get("path") or "")
            if not source.is_file():
                return f"{source} is not a file."
            dest = (arguments.get("to") or "/sdcard/Download/").strip()
            if not dest.startswith("/sdcard/"):
                return "Files go under /sdcard/ (the phone's shared storage)."
            proc = _adb(["push", str(source), dest], serial, timeout=600)
            return f"Copied {source.name} to the phone ({dest})." if proc.returncode == 0 else \
                f"adb push failed: {(proc.stderr or proc.stdout).strip()[:200]}"
        if action == "pull":
            remote = (arguments.get("path") or "").strip()
            if not remote.startswith("/sdcard/"):
                return "Give a path on the phone's shared storage, starting /sdcard/."
            target = files.resolve_in_home(arguments.get("to") or f"~/Downloads/{remote.rsplit('/', 1)[-1]}")
            if target.exists():
                return f"{target} already exists."
            target.parent.mkdir(parents=True, exist_ok=True)
            proc = _adb(["pull", remote, str(target)], serial, timeout=600)
            return f"Copied {remote} to {target}." if proc.returncode == 0 and target.exists() else \
                f"adb pull failed: {(proc.stderr or proc.stdout).strip()[:200]}"
        url = (arguments.get("url") or "").strip()
        if not re.match(r"^https?://[^\s'\"]+$", url):
            return "Give an http:// or https:// link."
        out = _shell(serial, "am", "start", "-a", "android.intent.action.VIEW", "-d", url)
        return f"Opened {url} on the phone." if "Error" not in out else f"The phone refused: {out[:200]}"
    except files.PathProblem as exc:
        return str(exc)
    except subprocess.TimeoutExpired:
        return "The phone did not answer in time."


SKILLS = [Skill(name="android_device", schema=SCHEMA, run=_run)]
