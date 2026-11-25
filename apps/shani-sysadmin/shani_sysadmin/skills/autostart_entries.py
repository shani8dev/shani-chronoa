"""Skill: what starts automatically on this machine?

`list_services` reports **systemd system units**, which is one of five places a
machine arranges to run something without being asked. The other four have no
answer anywhere in this package, and they are the ones people actually forget
about:

| surface | what it is | who runs it |
|---|---|---|
| `~/.config/autostart/*.desktop` | this user's login entries | the desktop, at login |
| `/etc/xdg/autostart/*.desktop` | the distribution's | the desktop, at login |
| systemd **user** units and timers | this user's background work | `systemd --user` |
| `crontab`, `/etc/cron.*` | scheduled commands | cron |

This is the question behind "this machine is doing something I didn't ask for",
and it is also the one Chronoa is **itself** implicated in: `application.py`'s
`_sync_autostart()` creates and removes an entry in `~/.config/autostart` when
auto-start is switched on and off. The app writes autostart entries and nothing
in this tree could read one back, so the setting and the file it produces were
never visible together.

**The four keys that decide whether an entry runs are not obvious, and three of
them are opt-outs.** Measured on a real `@blue` slot, across its 16
`/etc/xdg/autostart` entries:

- **`Hidden=true`** — the entry is suppressed. A `Hidden=true` file is still
  *present*, so counting files in the directory reports entries that will never
  run.
- **`OnlyShowIn=GNOME;Unity;`** — runs only on those desktops. This is a **list**,
  so `OnlyShowIn=GNOME` means nothing runs here on KDE and a parser that treats
  it as a boolean says the opposite.
- **`NotShowIn=KDE;GNOME;Cinnamon;`** — runs on everything *except* those.
  Both keys can be present and then it is suppressed everywhere.
- **`Type=`** — a `Type=Link` entry has no `Exec` of its own; it points at
  another file, which is exactly what Chronoa's own symlink entry is.

**The measurement that shapes the whole skill: `XDG_CURRENT_DESKTOP` is unset
in a slot.** That is not an empty answer, it is the absence of the one fact the
filter needs - so `OnlyShowIn`/`NotShowIn` cannot be applied and every entry
affected by them is reported as **cannot tell**, separately from the ones that
genuinely will run. Answering "runs" or "does not run" there would be a guess
about which desktop the person is on, presented as a fact about their machine.
"""

from __future__ import annotations

import configparser
import os
from typing import List, Optional, Tuple

from shani_chronoa.skills import Skill

_TIMEOUT = 20

#: Where entries live, user first - a user's own entry wins over the system's,
#: which is the freedesktop rule and is also why the two are listed separately.
#:
#: **The user directory is resolved through the same `files.config_home()` the
#: app uses**, rather than written as a literal `~/.config/autostart`. They
#: agree today, but the app honours `XDG_CONFIG_HOME` and a literal does not -
#: so a user whose config lives elsewhere would be shown an empty autostart
#: directory while the entry Chronoa itself wrote sat in it, unread. Two ways
#: of naming one directory is how they come to disagree; `test_it_reads_the_
#: directory_chronoa_writes_to` asserts the two resolve to the same path.
def _autostart_dirs() -> List[str]:
    from shani_chronoa import files
    return [str(files.config_home() / "autostart"), "/etc/xdg/autostart"]

#: A desktop entry's boolean keys are compared case-insensitively by the
#: specification; `Hidden=true` and `Hidden=True` both suppress an entry.
_TRUE = ("true", "1", "yes")


def _current_desktops() -> List[str]:
    """The desktop names in `XDG_CURRENT_DESKTOP`, which is a `;` list.

    Measured: **unset in a slot**, and the whole `OnlyShowIn`/`NotShowIn`
    question is unanswerable without it. Returned empty for exactly that
    reason, and the caller reports "cannot tell" rather than guessing.
    """
    raw = os.environ.get("XDG_CURRENT_DESKTOP", "")
    return [part.strip() for part in raw.split(";") if part.strip()]


def _parse_entry(path: str) -> Optional[dict]:
    """One `.desktop` entry as `{name, exec, hidden, only, not_, kind, target}`.

    Read with `configparser` because that is what a desktop file is: an INI
    file with a `[Desktop Entry]` group. **A `.desktop` file with no group
    header is not a desktop entry at all** - some directories are full of
    them (an installer dropping a file with only comments parses as empty under
    a naive line reader), and `configparser` raises rather than inventing an
    empty entry, which is the honest outcome.
    """
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        # Opened and fed in by hand rather than via `parser.read(path)`: that
        # takes no `errors=` argument, so a desktop file holding one non-UTF-8
        # byte (a Name= with a stray latin-1 character is common enough in
        # installed entries) would raise out of the helper and take the whole
        # report down with it - one unreadable entry is not a reason to refuse
        # to list the other fifteen.
        with open(path, encoding="utf-8", errors="replace") as handle:
            parser.read_file(handle)
    except (configparser.Error, OSError, UnicodeError):
        return None
    if not parser.has_section("Desktop Entry"):
        return None

    def value(key: str, default: str = "") -> str:
        return parser.get("Desktop Entry", key, fallback=default).strip()

    def listed(key: str) -> List[str]:
        # A `;`-terminated list, and **a trailing semicolon is normal** -
        # measured: `OnlyShowIn=GNOME;Unity;` on a real image.
        return [p.strip() for p in value(key).split(";") if p.strip()]

    return {
        "name": value("Name") or os.path.basename(path),
        "exec": value("Exec"),
        "kind": value("Type") or "Application",
        "hidden": value("Hidden").lower() in _TRUE,
        "only": listed("OnlyShowIn"),
        "not_in": listed("NotShowIn"),
        "target": value("URL") or value("X-GNOME-Autostart-Delay") or "",
        "path": path,
    }


def _what_for(entry: dict) -> str:
    """What an entry actually runs: its `Exec`, or the file a `Type=Link`
    points at, or an honest "nothing"."""
    if entry["exec"]:
        return entry["exec"]
    if entry["target"]:
        return f"-> {entry['target']}"
    return "(no Exec and no target)"


def _entry_line(entry: dict, verdict: str, what: str) -> str:
    """One row: the verdict, the name, and **what it actually runs**.

    The third column is the reason this is a function. A `Type=Link` entry -
    which is **Chronoa's own autostart entry**, a symlink written by
    `_sync_autostart()` - has no `Exec` of its own, so a row built from `Exec`
    alone prints an empty column for the one entry whose whole purpose is to
    show what runs at login. It falls back to the link target.
    """
    mark = {"runs": "starts", "suppressed": "off", "unknown": "??"}[verdict]
    return f"    [{mark:^6}] {entry['name']}  ({what})"


def _verdict(entry: dict, desktops: List[str]) -> Tuple[str, str]:
    """`(verdict, why)` for one entry: `runs`, `suppressed` or `unknown`.

    **`suppressed` and `cannot tell` are different verdicts, deliberately.**
    `Hidden=true` is a fact in the file and is decided without knowing the
    desktop. `OnlyShowIn=KDE;` is also a fact in the file, but *whether it
    matches* needs `XDG_CURRENT_DESKTOP` - and with that unset the entry can be
    in neither state, and saying "runs" is a confident wrong answer about the
    user's own machine.
    """
    if entry["hidden"]:
        return "suppressed", "Hidden=true, so it is in the directory but will not start"
    only, not_in = entry["only"], entry["not_in"]
    if not only and not not_in:
        return "runs", "no desktop filter, so it starts on this session type"
    if not desktops:
        return ("unknown",
                "it is filtered by OnlyShowIn/NotShowIn and this machine does not "
                "say which desktop it is (XDG_CURRENT_DESKTOP is unset), so "
                "whether it starts cannot be told from here")
    # Both present means the entry is excluded everywhere - the spec's rule, and
    # the case a boolean reading of either key gets wrong.
    if only and not_in:
        if set(desktops) & set(only):
            return "suppressed", "NotShowIn includes this desktop, so it will not start"
        return "suppressed", ("OnlyShowIn and NotShowIn are both set and this "
                              "desktop is in neither, so it will not start")
    if only:
        if set(desktops) & set(only):
            return "runs", f"OnlyShowIn lists {'/'.join(only)}"
        return "suppressed", (f"OnlyShowIn lists {'/'.join(only)} and this "
                              f"desktop ({'/'.join(desktops)}) is not among them")
    if set(desktops) & set(not_in):
        return "suppressed", f"NotShowIn lists {'/'.join(not_in)}"
    return "runs", f"not in NotShowIn ({'/'.join(not_in)})"


def _user_units() -> Tuple[List[str], str]:
    """Enabled systemd **user** units and timers, or why they are unknown.

    **A slot has no user session bus**, so this is usually unknown rather than
    empty - measured: `Failed to connect to user scope bus via local transport:
    $DBUS_SESSION_BUS_ADDRESS and $XDG_RUNTIME_DIR not defined`. "no user units"
    on a machine that was never asked is the confident wrong answer.
    """
    import shutil
    import subprocess

    if shutil.which("systemctl") is None:
        return [], "systemctl is not installed."
    out: List[str] = []
    problems: List[str] = []
    # **Both halves read the same column-0 unit name**, and that is a measured
    # choice rather than a convenience. `list-unit-files --state=enabled` gives
    # `NAME STATE PRESET` - measured on a real slot: `gamemoded.service enabled
    # enabled` - so column 0 is the name. `list-timers` is a *table* whose
    # first column is a timestamp (`Mon 2026-10-12 09:00:00 ...`), and the unit
    # is a later column whose position depends on the width of the ones before
    # it. A first version read column 0 of both and would have reported
    # `Mon` as a timer.
    #
    # **That could not be measured here, so it is not guessed.** A slot has no
    # user session bus at all - measured: "Failed to connect to user scope bus
    # via local transport" - so no timer row was ever available to read, and a
    # column index picked without seeing the output is exactly the confident
    # wrong answer this module is about. So timers are asked for through
    # `list-unit-files --type=timer`, whose format is the one measured, and a
    # timer that exists only as a scheduled trigger rather than an enabled unit
    # is therefore not listed. That limit is stated in the module docstring
    # rather than papered over with an unmeasured parser.
    for argv, label in (
        (["systemctl", "--user", "list-unit-files", "--state=enabled",
          "--no-legend", "--no-pager", "--plain"], "enabled unit"),
        (["systemctl", "--user", "list-unit-files", "--type=timer",
          "--no-legend", "--no-pager", "--plain"], "timer"),
    ):
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=_TIMEOUT, check=False)
        except (subprocess.TimeoutExpired, OSError) as exc:
            problems.append(f"{label}s could not be listed ({exc}).")
            continue
        if proc.returncode != 0:
            detail = (proc.stderr or "").strip().splitlines()
            problems.append(f"{label}s: {detail[-1] if detail else f'exit {proc.returncode}'}")
            continue
        for line in (proc.stdout or "").splitlines():
            name = line.split()[0] if line.split() else ""
            # A unit name ends in a known systemd suffix; anything else is a
            # header or a note, and reporting `Mon` as a timer is worse than
            # omitting it.
            if name.endswith((".service", ".timer", ".socket", ".path")):
                out.append(f"{label}: {name}")
    return out, " ".join(problems)


def _cron() -> Tuple[List[str], str]:
    """The user's crontab, if there is one.

    `crontab -l` **exits 1 with "no crontab for <user>"** when there is none,
    which is the ordinary state and not a fault - so that exit is reported as
    "no crontab" rather than as an error.
    """
    import shutil
    import subprocess

    if shutil.which("crontab") is None:
        return [], "crontab is not installed (the cron daemon is not on this image)."
    try:
        proc = subprocess.run(["crontab", "-l"], capture_output=True,
                              text=True, timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return [], f"the crontab could not be read ({exc})."
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip()
        if "no crontab" in detail.lower():
            return [], ""
        return [], detail or f"crontab exited {proc.returncode}"
    return [line for line in (proc.stdout or "").splitlines()
            if line.strip() and not line.strip().startswith("#")], ""


def _describe() -> str:
    desktops = _current_desktops()
    lines: List[str] = []
    lines.append("Desktop: " + (", ".join(desktops) if desktops
                                 else "unknown - XDG_CURRENT_DESKTOP is not set, "
                                      "so entries filtered by OnlyShowIn/NotShowIn "
                                      "cannot be decided from here"))

    lines.append("")
    lines.append("Login entries (autostart .desktop files):")
    runs = suppressed = unknown = 0
    for expanded in _autostart_dirs():
        try:
            names = sorted(n for n in os.listdir(expanded)
                           if n.endswith(".desktop"))
        except OSError:
            continue
        if not names:
            continue
        lines.append(f"  {expanded}/")
        for name in names:
            entry = _parse_entry(os.path.join(expanded, name))
            if entry is None:
                lines.append(f"    {name} - not a desktop entry (no "
                             f"[Desktop Entry] group), so it is not one of "
                             f"these")
                continue
            verdict, why = _verdict(entry, desktops)
            runs += verdict == "runs"
            suppressed += verdict == "suppressed"
            unknown += verdict == "unknown"
            lines.append(_entry_line(entry, verdict, _what_for(entry)))
    if not (runs or suppressed or unknown):
        lines.append("  none found in " + " or ".join(_autostart_dirs()))

    lines.append("")
    lines.append(f"  {runs} will start, {suppressed} will not, "
                 f"{unknown} cannot be told from here.")

    units, why = _user_units()
    lines.append("")
    lines.append("systemd user units and timers:")
    if units:
        lines.extend(f"  {line}" for line in units)
    else:
        lines.append(f"  could not be listed - {why}")

    cron, why = _cron()
    lines.append("")
    lines.append("Scheduled (crontab):")
    if cron:
        lines.extend(f"  {line}" for line in cron[:20])
        if len(cron) > 20:
            lines.append(f"  ... and {len(cron) - 20} more")
    else:
        lines.append(f"  {why or 'the crontab is empty'}")
    return "\n".join(lines)


def _run(arguments: dict) -> str:
    return _describe()


SCHEMA = {
    "type": "function",
    "function": {
        "name": "autostart_entries",
        "description": (
            "Report everything on this machine that starts automatically: the "
            "autostart .desktop files in this user's home and /etc/xdg, the "
            "enabled systemd user units and timers, and the crontab. Use for "
            "'what starts automatically', 'what runs at login', 'what is "
            "scheduled', 'why does this thing keep running', 'what did I set "
            "to start on its own'. Honours Hidden, OnlyShowIn and NotShowIn, "
            "and says 'cannot tell' rather than guessing when the desktop is "
            "unknown - which is the normal state outside a login session. "
            "Read-only; nothing is enabled, disabled or changed."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

SKILLS = [Skill(name="autostart_entries", schema=SCHEMA, run=_run)]