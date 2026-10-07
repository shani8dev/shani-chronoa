"""Skill: which app opens a kind of file, and change it.

"Why does this PDF open in the browser?" and "make VLC the default for videos"
are the freedesktop MIME-default questions. `xdg-mime query default` reads what
the desktop would launch, `xdg-mime default` changes it (written to the user's
own `mimeapps.list`, which both GNOME and Plasma honour), and `xdg-settings`
does the same for the default web browser.

Changing it is gated by its own key, `default-apps-enabled`: it silently changes
what every later double-click does, in every application, which is not
something to discover after the fact. Reading it needs no permission.

Honesty rules:

- An app is accepted only if a matching `.desktop` file exists in the XDG
  application directories (Flatpak exports included). `xdg-mime default`
  accepts any string and would happily record an app that does not exist,
  leaving the file type opening nothing.
- An app named by display name that matches more than one entry is refused with
  the candidates, never guessed.
- The new default is read back (and checked again by `POST_CONDITION`).
"""

from __future__ import annotations

import mimetypes
import os
import shutil
import subprocess
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "default-apps-enabled"
_TIMEOUT = 15
_GLOBS = Path("/usr/share/mime/globs2")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "default_apps",
        "description": (
            "Show or change which application opens a kind of file - by MIME "
            "type ('application/pdf'), extension ('.pdf', 'mp4') or an example "
            "file - or the default web browser (target 'browser'). Use for 'what "
            "opens PDFs', 'make VLC open my videos', 'set Firefox as my default "
            "browser'. Changing a default requires the 'default-apps-enabled' "
            "consent key; showing one does not."
        ),
        "parameters": {
            "type": "object",
            # `target` first: mcp.py builds a Python signature in property
            # order, and a required parameter after an optional one is a
            # ValueError that takes the whole MCP server down.
            "properties": {
                "target": {"type": "string",
                           "description": "A MIME type, file extension, file path, or 'browser'."},
                "action": {"type": "string", "enum": ["status", "set"],
                           "description": "status (default) or set."},
                "app": {"type": "string",
                        "description": "set: the app, as a desktop id (org.videolan.VLC.desktop) or its name (VLC)."},
            },
            "required": ["target"],
        },
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (f"changing default apps is turned off (enable '{_CONSENT_KEY}' in "
                       f"Settings). It changes what every later double-click opens, in "
                       f"every application.")
    return True, ""


def _cmd(*argv: str) -> "subprocess.CompletedProcess | None":
    try:
        return subprocess.run(list(argv), capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None


def application_dirs() -> "list[Path]":
    home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    dirs = [home, *(os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")]
    return [Path(d) / "applications" for d in dirs if d]


def desktop_entries() -> "dict[str, str]":
    """desktop id -> display name, first directory wins (the XDG precedence)."""
    found = {}
    for base in application_dirs():
        try:
            paths = sorted(base.glob("*.desktop"))
        except OSError:
            continue
        for path in paths:
            if path.name in found:
                continue
            name = path.stem
            try:
                for line in path.read_text(errors="replace").splitlines():
                    if line.startswith("Name="):
                        name = line[5:].strip()
                        break
            except OSError:
                pass
            found[path.name] = name
    return found


def resolve_app(wanted: str, entries: "dict[str, str]") -> "tuple[str | None, list[str]]":
    wanted = wanted.strip()
    if not wanted:
        return None, []
    ident = wanted if wanted.endswith(".desktop") else f"{wanted}.desktop"
    if ident in entries:
        return ident, [ident]
    low = wanted.lower()
    exact = [k for k, v in entries.items() if v.lower() == low]
    if len(exact) == 1:
        return exact[0], exact
    hits = exact or [k for k, v in entries.items() if low in v.lower() or low in k.lower()]
    return (hits[0] if len(hits) == 1 else None), hits


def mime_for(target: str) -> "str | None":
    target = target.strip()
    if not target:
        return None
    path = Path(os.path.expanduser(target))
    if path.is_file():
        proc = _cmd("xdg-mime", "query", "filetype", str(path))
        if proc is not None and proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip().split(";")[0]
    if "/" in target and not target.startswith((".", "/", "~")):
        return target.lower()
    ext = target if target.startswith(".") else f".{target}"
    guessed, _ = mimetypes.guess_type(f"x{ext.lower()}")
    if guessed:
        return guessed
    try:
        for line in _GLOBS.read_text().splitlines():
            parts = line.split(":")
            if len(parts) >= 3 and parts[2].lower() == f"*{ext.lower()}":
                return parts[1]
    except OSError:
        pass
    return None


def current_default(mime: str) -> "str | None":
    if mime == "browser":
        proc = _cmd("xdg-settings", "get", "default-web-browser")
    else:
        proc = _cmd("xdg-mime", "query", "default", mime)
    if proc is None or proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _target(arguments: dict) -> "str | None":
    raw = (arguments.get("target") or "").strip()
    return "browser" if raw.lower() in ("browser", "web browser", "web") else mime_for(raw)


def _run(arguments: dict) -> str:
    if shutil.which("xdg-mime") is None:
        return files.tool_missing("xdg-mime", "read or change default apps")
    raw = (arguments.get("target") or "").strip()
    target = _target(arguments)
    if not target:
        return f"Could not tell what kind of file {raw!r} is, so no default could be looked up."
    label = "Web links" if target == "browser" else f"Files of type {target}"
    entries = desktop_entries()
    now = current_default(target)
    now_text = f"{entries.get(now, now)} ({now})" if now else "nothing set"
    action = (arguments.get("action") or "status").strip().lower()
    if action == "status":
        return f"{label} open with: {now_text}."
    if action != "set":
        return f"Action must be status or set, not {action!r}."
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to change the default: {reason}"
    app, hits = resolve_app(arguments.get("app") or "", entries)
    if app is None:
        if not hits:
            return (f"No installed application matches {arguments.get('app')!r}, so the "
                    f"default was not changed (it is still {now_text}).")
        names = "; ".join(f"{entries[h]} ({h})" for h in hits[:8])
        return f"{arguments.get('app')!r} matches several applications - {names}. Name one exactly."
    if now == app:
        return f"{label} already open with {entries[app]}, so nothing was changed."
    if target == "browser":
        if shutil.which("xdg-settings") is None:
            return files.tool_missing("xdg-settings", "change the default browser")
        proc = _cmd("xdg-settings", "set", "default-web-browser", app)
    else:
        proc = _cmd("xdg-mime", "default", app, target)
    if proc is None or proc.returncode != 0:
        detail = "" if proc is None else (proc.stderr or proc.stdout or "").strip()
        return f"Could not change the default: {detail or 'the command did not finish'}. It is still {now_text}."
    after = current_default(target)
    if after == app:
        return f"{label} now open with {entries[app]} (verified by reading it back)."
    return f"The change was accepted but the default for {label.lower()} reads back as {after or 'nothing'}, so it is not verified."


def _post_condition(arguments: dict):
    if (arguments.get("action") or "status").strip().lower() != "set":
        return None
    target = _target(arguments)
    app, _ = resolve_app(arguments.get("app") or "", desktop_entries())
    if not target or not app:
        return None
    now = current_default(target)
    return now == app, f"read back: {now or 'nothing'}"


POST_CONDITION = _post_condition

SKILLS = [Skill(name="default_apps", schema=SCHEMA, run=_run)]
