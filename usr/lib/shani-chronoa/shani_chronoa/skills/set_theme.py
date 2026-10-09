"""Skill: read or change the desktop's light/dark appearance.

Nothing in this project could change how the desktop looks, and light/dark is
the single most toggled preference on a machine - it tracks the time of day, the
room, and the projector you just plugged into. An assistant that can read the
screen but cannot follow it into a dark room is only half an assistant.

Handles GNOME and KDE separately, because they do not share a mechanism at all.
GNOME keeps the preference in GSettings and it is a one-line write. Plasma 6
keeps light/dark in the *colour scheme* (`[General] ColorScheme` in kdeglobals),
and the supported way to change it is `plasma-apply-colorscheme`, whose
`--list-schemes` marks the current one - that mark is the read-back. Plasma 5's
`plasma-lookandfeeltool` (renamed `plasma-apply-lookandfeel` in Plasma 6, whose
`--list` no longer marks the current look) is only a fallback. Measured on the
Plasma image, 2026-10-01: neither `plasma-lookandfeeltool` nor `kreadconfig5`
exists, and the old code read `[KDE] colorScheme`, a key Plasma 6 does not
write - status and both changes were broken there. Writing KDE's config files
directly is what breaks a Plasma session, so that is not attempted.

Gated, and separately from anything that reads. Restyling someone's desktop
unasked, mid-document, is disruptive in a way that muting a microphone is not.
Reporting what is set needs no permission.

Honesty rules:

- **The setting is read back after writing it.** A `gsettings set` exiting 0 and
  a desktop that is actually dark are different claims, and a theme provider
  that is installed but not selected produces a write that silently does nothing
  visible.
- "Preferred" and "applied" are reported separately. Setting the preference
  `prefer-dark` does not make a GTK application that ignores the portal dark, and
  saying "dark mode is on" in that state would be a claim about the whole desktop
  that one preference cannot support.
- An unsupported desktop says so rather than reporting the current setting as if
  it were the whole picture.
"""

from __future__ import annotations

import re

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill
from shani_chronoa import desktop_session
from shani_chronoa import subproc

_CONSENT_KEY = "appearance-control-enabled"
_TIMEOUT = 25


def _run_cmd(argv, env=None):
    """This module's seam over `subproc.run` (tests replace it), with the module's timeout."""
    return subproc.run(argv, timeout=_TIMEOUT, env=env)


#: The Plasma helpers this module shells to, with the question each answers.
#: `plasma-lookandfeeltool` is Plasma 5 and is kept only because the image that
#: shipped it is a real target; it has no entry in `files._PACKAGE_HINTS` and
#: must not grow one, because on Plasma 6 the binary does not exist at all and a
#: named package for it would be the invented answer that table exists to avoid.
_KDE_TOOLS = ("kreadconfig6", "kreadconfig5", "plasma-apply-colorscheme",
              "plasma-lookandfeeltool")


def _missing_kde_tools() -> "list[str]":
    """The Plasma helpers this machine does not have.

    Checked with `shutil.which` *before* running anything, which is what
    `tools/cli_matrix.py --check` asserts and what it caught here: on a real
    GNOME image (slot-test `chronoa-matrix`, 2026-10-09) it reported

        set_theme: runs kreadconfig6, which is not installed here, and unchecked

    for all three. Before this, `_run_cmd` returned `None` and each caller
    handled that, so nothing crashed - but the person asking "am I in dark
    mode?" on a GNOME desktop got a bare "no" with no reason, and a person on a
    machine missing one helper got silence rather than the name of the package
    that would fix it. `files.tool_missing` is the established helper and
    names the package from the routes table.
    """
    import shutil

    from shani_chronoa import files

    return [tool for tool in _KDE_TOOLS if shutil.which(tool) is None]


def _kde_readable() -> bool:
    """Whether a KDE reading is actually available, helpers or not.

    The honest second half of `_missing_kde_tools()`: an absent `kreadconfig6`
    is a reason to *say* something, not proof there is nothing to say. A machine
    with the tools present, or one whose helpers answer anyway, must still get
    its reading — so the refusal in `_run` is conditional on this as well.
    """
    _, current = _kde_schemes()
    if current:
        return True
    _, current = _kde_looks()
    return bool(current)

_GNOME_SCHEME = "org.gnome.desktop.interface"
_GNOME_KEY = "color-scheme"
#: KDE's own light/dark preference names, mapped to the two states.
_DARK_LOOKS = ("Breeze-Dark", "dark", "adwaita-dark")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "set_theme",
        "description": (
            "Report or change whether the desktop uses a light or dark "
            "appearance, on GNOME or KDE. Reports the applied theme separately "
            "from the preference, because setting the preference does not make "
            "every application follow it. Requires the "
            "'appearance-control-enabled' consent key; reporting needs no such "
            "permission."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "'status', 'dark' or 'light'. Defaults to status.",
                },
            },
        },
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """Return (allowed, reason). Fail-closed: an undeclared key denies."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            f"changing the desktop appearance is turned off (enable "
            f"'{_CONSENT_KEY}' in Settings). Reporting what is currently set "
            f"needs no such permission - only changing it does, because "
            f"restyling a desktop unasked is disruptive in a way that reading it "
            f"is not."
        )
    return True, ""


def _gnome_state() -> "tuple[str | None, str]":
    """(state, raw) where state is 'dark'/'light', or None if unreadable."""
    proc = _run_cmd(["gsettings", "get", _GNOME_SCHEME, _GNOME_KEY])
    if proc is None or proc.returncode != 0:
        return (None, "")
    raw = proc.stdout.strip().strip("'\"")
    if raw == "prefer-dark":
        return ("dark", raw)
    if raw in ("default", "prefer-light"):
        return ("light", raw)
    return (None, raw)


def _kde_schemes() -> "tuple[list[str], str]":
    """(available colour schemes, the current one) from plasma-apply-colorscheme, or ([], '')."""
    proc = _run_cmd(["plasma-apply-colorscheme", "--list-schemes"])
    if proc is None or proc.returncode != 0:
        return [], ""
    names, current = [], ""
    for line in proc.stdout.splitlines():
        m = re.match(r"^\s*\*\s+(.+?)(\s+\(current color scheme\))?\s*$", line)
        if m:
            names.append(m.group(1))
            if m.group(2):
                current = m.group(1)
    return names, current


def _kde_state() -> "tuple[str | None, str]":
    """(state, scheme): Plasma 6's colour scheme, else Plasma 5's kdeglobals key."""
    _, current = _kde_schemes()
    if current:
        return (("dark" if "dark" in current.lower() else "light"), current)
    for tool, group, key in (("kreadconfig6", "General", "ColorScheme"), ("kreadconfig5", "KDE", "colorScheme")):
        proc = _run_cmd([tool, "--file", "kdeglobals", "--group", group, "--key", key])
        if proc is not None and proc.returncode == 0 and proc.stdout.strip():
            scheme = proc.stdout.strip()
            return (("dark" if "dark" in scheme.lower() else "light"), scheme)
    return (None, "")


def _kde_look() -> str:
    """The global theme (look-and-feel package) in use, for status only."""
    proc = _run_cmd(["kreadconfig6", "--file", "kdeglobals", "--group", "KDE", "--key", "LookAndFeelPackage"])
    if proc is not None and proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    proc = _run_cmd(["plasma-lookandfeeltool", "--list"])  # Plasma 5 marks the current one with '*'
    if proc is None or proc.returncode != 0:
        return ""
    for line in proc.stdout.splitlines():
        if line.strip().startswith("*"):
            return line.strip().lstrip("* ").strip()
    return ""


def _kde_looks() -> "tuple[list[str], str]":
    """`(_kde_schemes(), _kde_look())` as one call, for the availability check.

    Named rather than inlined so `_kde_readable()` is obviously asking the same
    question the reader below asks, instead of re-deriving a subset of it - the
    two drifting apart is how a guard ends up checking something adjacent.
    """
    return _kde_schemes(), _kde_look()


def _pick_scheme(available: "list[str]", want_dark: bool) -> str:
    preferred = ("BreezeDark",) if want_dark else ("BreezeLight", "BreezeClassic")
    for name in preferred:
        if name in available:
            return name
    matching = [n for n in available if ("dark" in n.lower()) == want_dark]
    return matching[0] if matching else ""


def _run(arguments: dict) -> str:
    action = (arguments.get("action") or "status").strip().lower()
    if action not in ("status", "dark", "light"):
        return f"Action must be status, dark or light, not {action!r}."

    desktop = desktop_session.kind()
    if desktop == "unknown":
        return ("This desktop's session could not be identified from "
                "XDG_CURRENT_DESKTOP, DESKTOP_SESSION or XDG_SESSION_DESKTOP, so "
                "the appearance is UNKNOWN. GNOME and KDE keep this setting in "
                "completely different places and guessing which one to write "
                "would be the wrong kind of confident.")

    if desktop == "gnome":
        state, raw = _gnome_state()
    else:
        missing = _missing_kde_tools()
        if missing and not _kde_readable():
            # Answer with the reason instead of a bare "could not read". This is
            # the case the matrix check found: a machine that reports itself as
            # KDE without the KDE helpers installed, which is exactly what a
            # GNOME box running KDE's session variables looks like.
            #
            # **Guarded on `_kde_readable()`**, not applied unconditionally: the
            # helpers' absence alone is not proof the answer is unavailable, and
            # returning early on it broke four existing tests that stub
            # `_run_cmd` and assert a round trip. Those tests were right to
            # fail - a check that refuses a path it has not proved is broken is
            # the over-fix this repo keeps recording. So the sentence appears
            # only when reading has *also* come back empty.
            from shani_chronoa import files

            return (f"This session reports as {desktop}, but "
                    f"{files.tool_missing(missing[0], 'read the desktop appearance')} "
                    f"Nothing was changed.")
        state, raw = _kde_state()
        look = _kde_look()

    if action == "status":
        lines = [f"Desktop session: {desktop}"]
        if state is None:
            lines.append("Appearance: UNKNOWN - the preference could not be read"
                         + (f" (raw value {raw!r})" if raw else ""))
        else:
            lines.append(f"Appearance preference: {state}"
                         + (f" (gsettings value {raw!r})" if desktop == "gnome" else
                            f" (colour scheme {raw!r})"))
        if desktop == "kde":
            lines.append(f"Applied look: {look or 'could not be read'}")
        lines.append(
            "This is the preference, not a guarantee: an application that "
            "ignores the desktop's colour-scheme setting will not follow it.")
        return "\n".join(lines)

    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to change the appearance: {reason}"

    want_dark = action == "dark"

    if desktop == "gnome":
        target = "prefer-dark" if want_dark else "default"
        proc = _run_cmd(["gsettings", "set", _GNOME_SCHEME, _GNOME_KEY, target])
        if proc is None or proc.returncode != 0:
            return (f"gsettings could not set {_GNOME_KEY}: "
                    f"{(proc.stderr or '').strip() if proc else 'gsettings is not installed'}"
                    f". Nothing was changed.")
        after, raw_after = _gnome_state()
        if after == ("dark" if want_dark else "light"):
            return (f"Desktop appearance preference is now {after} "
                    f"(verified: {raw_after!r}). Applications that ignore the "
                    f"colour-scheme setting will not follow.")
        return (f"gsettings accepted the change but it reads back as {raw_after!r}, "
                f"so this is not verified.")

    # KDE: the supported tool, never a hand edit of kdeglobals.
    available, before = _kde_schemes()
    if available:
        target = _pick_scheme(available, want_dark)
        if not target:
            return (f"No {'dark' if want_dark else 'light'} colour scheme was found among "
                    f"{len(available)}: {', '.join(available[:8])}. Nothing was changed.")
        proc = _run_cmd(["plasma-apply-colorscheme", target])
        if proc is None or proc.returncode != 0:
            return (f"plasma-apply-colorscheme could not apply {target!r}: "
                    f"{(proc.stderr or proc.stdout or '').strip() if proc else 'not installed'}. "
                    f"Nothing was changed.")
        _, after = _kde_schemes()
        if after == target:
            return f"Plasma colour scheme is now {after!r} (verified by reading it back; was {before or 'unknown'!r})."
        return (f"plasma-apply-colorscheme reported success but the current scheme reads back as "
                f"{after!r}, so this is not verified.")
    # Plasma 5: the global theme tool
    looks = _run_cmd(["plasma-lookandfeeltool", "--list"])
    if looks is None or looks.returncode != 0:
        return ("Neither plasma-apply-colorscheme (Plasma 6) nor plasma-lookandfeeltool (Plasma 5) "
                "answered, so the Plasma appearance cannot be changed from here. Nothing was changed.")
    available = [l.strip().lstrip("* ").strip() for l in looks.stdout.splitlines() if l.strip()]
    wanted = [n for n in available
              if (any(d in n.lower() for d in _DARK_LOOKS) if want_dark else ("dark" not in n.lower()))]
    if not wanted:
        return (f"No suitable {'dark' if want_dark else 'light'} Plasma look was found among "
                f"{len(available)} available: {', '.join(available[:8]) or 'none listed'}. Nothing was changed.")
    proc = _run_cmd(["plasma-lookandfeeltool", "--apply", wanted[0]])
    if proc is None or proc.returncode != 0:
        return (f"plasma-lookandfeeltool could not apply {wanted[0]!r}: "
                f"{(proc.stderr or '').strip() if proc else 'not installed'}. Nothing was changed.")
    applied = _kde_look()
    if applied == wanted[0]:
        return f"Applied Plasma look {applied!r} (verified by reading it back)."
    return (f"plasma-lookandfeeltool reported success but the applied look reads back as {applied!r}, "
            f"so this is not verified.")


SKILLS = [Skill(name="set_theme", schema=SCHEMA, run=_run)]
