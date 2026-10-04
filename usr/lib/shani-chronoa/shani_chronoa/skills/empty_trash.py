"""Skill: empty the trash.

The only action in this package that destroys something a person has already
decided to throw away. That is genuinely different from deleting files, which is
why it does not share `file-delete-enabled`: a file in the trash is already
removed from where it was, recoverable, and putting it there was the user's own
earlier decision. Emptying the trash is what makes it unrecoverable, and there
is no undo for it.

Gated because there is no confirmation step after the fact and no way to see
what was in there afterwards. The item count is always listed first, so the user
can see the scale of what is about to go before it goes.

Only the freedesktop trash directories are touched, through the same `gio trash`
interface the desktop's own "Empty Trash" uses, so a non-standard trash location
is handled the way the desktop handles it rather than being reinvented here. No
`rm -rf ~/.local/share/Trash` fallback: that path is wrong on any system that
relocates its trash, and getting it wrong deletes something that was not in the
trash.

Honesty rules:

- **The count is taken before emptying, and the trash is re-listed after.** An
  empty-trash that reported success without either would be unanswerable if it
  failed.
- If the trash cannot be listed, nothing is emptied. Without a count there is no
  way to say what was destroyed, which defeats the purpose of asking.
"""

from __future__ import annotations

import shutil
import subprocess

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "trash-empty-enabled"
_TIMEOUT = 120

SCHEMA = {
    "type": "function",
    "function": {
        "name": "empty_trash",
        "description": (
            "Permanently delete everything in the desktop trash, after listing "
            "what is in there first. There is no way to undo this, so it "
            "requires the 'trash-empty-enabled' consent key. Use 'list' to see "
            "the contents without emptying anything."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "'list' or 'empty'. Defaults to list.",
                }
            },
        },
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """Return (allowed, reason). Fail-closed: an undeclared key denies."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            f"emptying the trash is turned off (enable '{_CONSENT_KEY}' in "
            f"Settings). This is not the same permission as deleting files: "
            f"everything here was already removed from where it was and is "
            f"recoverable, and emptying the trash is the step that makes it "
            f"unrecoverable. Listing the contents needs no permission."
        )
    return True, ""


def _gio(*args: str):
    if shutil.which("gio") is None:
        return None
    try:
        return subprocess.run(["gio", "trash", *args], capture_output=True,
                              text=True, timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None


def _listing() -> "tuple[list | None, str]":
    """(uris, error). uris is None when the trash could not be listed."""
    proc = _gio("--list")
    if proc is None:
        return (None, "gio is not installed, so the trash cannot be listed.")
    if proc.returncode != 0:
        return (None, (proc.stderr or "gio refused to list the trash").strip())
    uris = [l.strip() for l in proc.stdout.splitlines() if l.strip()]
    return (uris, "")


def _run(arguments: dict) -> str:
    action = (arguments.get("action") or "list").strip().lower()
    if action not in ("list", "empty"):
        return f"Action must be list or empty, not {action!r}."

    if shutil.which("gio") is None:
        return ("gio is not installed, so the trash cannot be read or emptied. "
                "No fallback to deleting the trash directories directly: the "
                "standard location is not where every desktop keeps it, and "
                "removing the wrong directory deletes things that were never in "
                "the trash.")

    uris, error = _listing()
    if uris is None:
        return (f"The trash could not be listed ({error}), so nothing was "
                f"emptied. Without a count there is no way to say what would "
                f"have been destroyed.")

    if action == "list":
        if not uris:
            return "The trash is already empty."
        shown = uris[:20]
        out = [f"{len(uris)} item(s) in the trash:"]
        out += [f"  {u}" for u in shown]
        if len(uris) > len(shown):
            out.append(f"  and {len(uris) - len(shown)} more")
        out.append("Emptying these is permanent and cannot be undone.")
        return "\n".join(out)

    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return (f"Refusing to empty the trash: {reason} "
                f"There are {len(uris)} item(s) in it.")

    if not uris:
        return "The trash was already empty, so nothing was removed."

    proc = _gio("--empty")
    if proc is None or proc.returncode != 0:
        detail = "gio did not answer" if proc is None else (proc.stderr or "").strip()
        return (f"gio could not empty the trash ({detail or 'no detail given'}). "
                f"The {len(uris)} item(s) that were there may or may not be gone "
                f"- this is not a confirmed failure, so check before assuming.")

    after, _ = _listing()
    if after is not None and not after:
        return (f"Emptied the trash: {len(uris)} item(s) removed, and re-listing "
                f"it now shows nothing.")
    if after is None:
        return (f"gio reported the trash was emptied ({len(uris)} item(s)), but "
                f"re-listing it did not work, so this is unverified.")
    return (f"gio reported success, but {len(after)} item(s) are still listed. "
            f"Something was not removed; the original {len(uris)} are not all "
            f"confirmed gone.")


SKILLS = [Skill(name="empty_trash", schema=SCHEMA, run=_run)]
