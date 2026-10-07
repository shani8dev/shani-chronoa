"""Skill: report or change the system language and regional formats.

`set_keyboard_layout` covers the keyboard and `set_timezone` the clock; the
language and the formats for dates, numbers, money and paper size had no
surface. "Use British date formats" and "switch the system to German" are real
requests, and `localectl set-locale` (systemd-localed, via polkit) is how both
desktops expect it done.

Gated by `locale-control-enabled`: it changes the language of every program on
the login screen and in every new session.

Honesty rules:

- **`localectl set-locale` replaces every setting it is given with exactly the
  list passed**, so changing only LANG would silently drop an existing LC_TIME.
  The current assignments are read first and passed back with only the asked
  for one changed.
- A locale is accepted only if `localectl list-locales` lists it - a locale
  that is not generated on this machine would be recorded and then ignored by
  every program, falling back to C.
- The setting is read back, and the reply says plainly that it takes effect at
  the next login, not in the session already running.
"""

from __future__ import annotations

import shutil
import subprocess

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "locale-control-enabled"
_TIMEOUT = 60
_MAX_LISTED = 60
_FORMATS = {"language": "LANG", "dates": "LC_TIME", "numbers": "LC_NUMERIC",
            "money": "LC_MONETARY", "measurement": "LC_MEASUREMENT", "paper": "LC_PAPER"}

SCHEMA = {
    "type": "function",
    "function": {
        "name": "set_locale",
        "description": (
            "Report the system language and regional formats, list the locales "
            "available on this machine, or change one: the system language, or "
            "the date format (British day/month or American month/day order), "
            "number format (decimal comma or point), money, measurement or paper "
            "size (e.g. what='dates', locale='en_GB.UTF-8'). Takes effect at the "
            "next login. Changing requires the 'locale-control-enabled' consent key."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["status", "list", "set"],
                           "description": "status (default), list or set."},
                "locale": {"type": "string", "description": "set: e.g. 'de_DE.UTF-8'."},
                "what": {"type": "string", "enum": sorted(_FORMATS),
                         "description": "set: which part to change (default language)."},
            },
        },
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (f"changing the system language and formats is turned off (enable "
                       f"'{_CONSENT_KEY}' in Settings). It changes the language of every "
                       f"program and of the login screen.")
    return True, ""


def _localectl(*args: str) -> "subprocess.CompletedProcess | None":
    try:
        return subprocess.run(["localectl", *args], capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None


def parse_status(stdout: str) -> "dict[str, str]":
    """{'LANG': 'en_US.UTF-8', 'LC_TIME': ...} from `localectl status`."""
    found, in_locale = {}, False
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("System Locale:"):
            in_locale = True
            stripped = stripped.split(":", 1)[1].strip()
        elif ":" in stripped:
            in_locale = False
        if in_locale and "=" in stripped:
            key, _, value = stripped.partition("=")
            found[key.strip()] = value.strip()
    return found


def current() -> "dict[str, str] | None":
    proc = _localectl("status")
    if proc is None or proc.returncode != 0:
        return None
    return parse_status(proc.stdout)


def available() -> "list[str] | None":
    proc = _localectl("list-locales")
    if proc is None or proc.returncode != 0:
        return None
    return [l.strip() for l in proc.stdout.splitlines() if l.strip()]


def _describe(settings: "dict[str, str]") -> str:
    if not settings:
        return "No system locale is set (programs fall back to C/POSIX)."
    names = {v: k for k, v in _FORMATS.items()}
    return "\n".join(f"  {names.get(k, k):<12} {v}  ({k})" for k, v in settings.items())


def _run(arguments: dict) -> str:
    if shutil.which("localectl") is None:
        return files.tool_missing("localectl", "read or change the system language")
    action = (arguments.get("action") or "status").strip().lower()
    settings = current()
    if settings is None:
        return "localectl could not read the system locale, so it is UNKNOWN."
    if action == "status":
        return "System language and formats:\n" + _describe(settings)
    known = available()
    if action == "list":
        if not known:
            return "The list of locales could not be read, so what is available is UNKNOWN."
        shown = known[:_MAX_LISTED]
        tail = [f"  and {len(known) - len(shown)} more"] if len(known) > len(shown) else []
        return "\n".join([f"{len(known)} locale(s) available on this machine:",
                          *[f"  {l}" for l in shown], *tail])
    if action != "set":
        return f"Action must be status, list or set, not {action!r}."
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to change the locale: {reason}"
    what = (arguments.get("what") or "language").strip().lower()
    if what not in _FORMATS:
        return f"'what' must be one of {', '.join(sorted(_FORMATS))}, not {what!r}."
    locale = (arguments.get("locale") or "").strip()
    if not locale:
        return "No locale was named, so nothing was changed."
    if known is not None and locale not in known:
        near = [l for l in known if l.lower().startswith(locale.split(".")[0].lower()[:2])][:6]
        hint = f" Available ones that look close: {', '.join(near)}." if near else ""
        return (f"Refusing to set {locale!r}: it is not generated on this machine, so every "
                f"program would ignore it and fall back to C.{hint}")
    key = _FORMATS[what]
    if settings.get(key) == locale:
        return f"The {what} setting is already {locale}, so nothing was changed."
    wanted = dict(settings)
    wanted[key] = locale
    proc = _localectl("set-locale", *[f"{k}={v}" for k, v in wanted.items()])
    if proc is None or proc.returncode != 0:
        detail = "" if proc is None else (proc.stderr or proc.stdout or "").strip()
        return f"Could not change the locale: {detail or 'localectl did not finish'}. Nothing changed."
    after = current() or {}
    if after.get(key) == locale:
        kept = [k for k in settings if k != key and after.get(k) == settings[k]]
        note = f" Kept unchanged: {', '.join(kept)}." if kept else ""
        return (f"The {what} setting is now {locale} (verified by reading it back).{note} "
                f"It takes effect the next time you log in.")
    return f"localectl reported success but {key} reads back as {after.get(key, 'unset')}, so this is not verified."


def _post_condition(arguments: dict):
    if (arguments.get("action") or "status").strip().lower() != "set":
        return None
    key = _FORMATS.get((arguments.get("what") or "language").strip().lower())
    locale = (arguments.get("locale") or "").strip()
    now = current()
    if not key or not locale or now is None:
        return None
    return now.get(key) == locale, f"{key} reads back as {now.get(key, 'unset')}"


POST_CONDITION = _post_condition

SKILLS = [Skill(name="set_locale", schema=SCHEMA, run=_run)]
