"""Skill: actually free the disk space `cleanup_report` just measured.

`cleanup_report` can tell you precisely what is taking the space and name the
command that would free it - and then you run it yourself, in a terminal,
copying a string out of a reply. For a local assistant holding a fixed whitelist
of skills that is the wrong answer to "free up 20 GB": it has the measurement,
it knows the command, and it hands the work back.

This is the action half, for the two of its four recommendations that are
**per-user and need no root**:

- `cache` - the user's cache directory (`XDG_CACHE_HOME`, else `~/.cache`).
  Everything under a cache directory is by definition rebuildable, which is why
  it can be removed without a recovery path. `cleanup_report`'s own caveat
  stands and is repeated in the answer: apps rebuild what they need, so do it
  with them closed or a running app may misbehave until restarted.
- `flatpak` - runtimes no installed app uses (`flatpak uninstall --unused`).
  Flatpak refuses anything still referenced, so this cannot remove a runtime an
  app needs.

**Deliberately not here: the system journal** (`cleanup_report` names
`journalctl --vacuum-time=2weeks`). That is a root action, and the sandbox
blocks `setuid` on purpose - `sandbox/executor.py` records that a filter
allowing it "would surface as a polkit prompt that never [appears]", because
nothing in a sandboxed child can answer one. The one root path in this tree is
a `usr/bin/` helper the *person* runs through `pkexec`, the way
`shani-chronoa-lab-network` is; this skill is not that, and claiming to be would
be the alternative.

**And not the trash**: `empty_trash` already does it, behind
`trash-empty-enabled`. Two skills emptying the same directory is how two
permissions for one act drift apart.

Consent is its own key, `cleanup-enabled`, and it is **destructive**: this is a
recursive delete of everything under a directory the person owns, with no undo
ring and no trash. It therefore also asks first every time, and a "yes, for
this session" grant does not cover it - which follows from the consent key
rather than being written out here, so a skill that stops being destructive
stops being asked.

**The scope is the report's own list, not the model's.** `target` is an enum of
exactly these two; there is no path argument, so there is nothing for the model
to widen this into. A cache directory is emptied by its *contents* - the
directory itself is not removed, because some tools recreate a cache path they
expect to already exist.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "cleanup-enabled"
_TARGETS = ("cache", "flatpak")

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "cleanup_apply",
        "description": (
            "Actually free the disk space cleanup_report measured: empty your "
            "cache folder, or remove Flatpak runtimes no app uses. Both are "
            "recoverable or provably unused. Requires the 'cleanup-enabled' "
            "consent key. The system log is not here - that needs root, which "
            "Chronoa cannot ask for from inside a tool call."),
        "parameters": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "enum": list(_TARGETS),
                    "description": "What to clean: 'cache' or 'flatpak'.",
                },
            },
            "required": ["target"],
        },
    },
}


def _consent(config: ChronoaConfig) -> tuple[bool, str]:
    """Return (allowed, reason). Fail-closed: an undeclared key denies."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            f"freeing disk space this way is turned off (enable "
            f"'{_CONSENT_KEY}' in Settings). Looking is not: `cleanup_report` "
            f"will still tell you what is taking the space and the safe way to "
            f"free it."
        )
    return True, ""


def _cache_dir() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")


def _empty_directory(directory: Path) -> tuple[int, int, list[str]]:
    """Remove the *contents* of `directory`; returns (files_gone, bytes, failures).

    Counting bytes is for the person reading the answer, not for deciding
    whether it worked - see `POST_CONDITION` for why the post-condition reads
    the filesystem instead of trusting this arithmetic.
    """
    removed = 0
    freed = 0
    failures: list[str] = []
    for entry in sorted(directory.iterdir()):
        try:
            if entry.is_dir() and not entry.is_symlink():
                for root, _dirs, names in os.walk(entry):
                    for name in names:
                        try:
                            freed += os.lstat(os.path.join(root, name)).st_size
                            removed += 1
                        except OSError:
                            pass
                shutil.rmtree(entry)
            else:
                freed += entry.lstat().st_size
                entry.unlink()
        except OSError as exc:
            failures.append(f"{entry.name} ({exc.__class__.__name__})")
    return removed, freed, failures


def _run(arguments: dict) -> str:
    target = (arguments.get("target") or "").strip().lower()
    if target not in _TARGETS:
        return f"Target must be one of {', '.join(_TARGETS)}, not {target!r}."

    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to clean anything: {reason}"

    if target == "flatpak":
        if shutil.which("flatpak") is None:
            return ("Flatpak is not installed on this machine, so there are no "
                    "runtimes to remove. Nothing was changed.")
        try:
            proc = subprocess.run(["flatpak", "uninstall", "--unused", "-y"],
                                  capture_output=True, text=True, timeout=300,
                                  check=False)
        except subprocess.TimeoutExpired:
            return ("Flatpak did not finish removing the unused runtimes in "
                    "300s. Nothing is known to have changed.")
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            return ("Flatpak did not remove the unused runtimes: "
                    f"{detail[-1][:160] if detail else f'exit {proc.returncode}'}")
        removed = [line for line in proc.stdout.splitlines() if "removing" in line.lower()]
        if not removed:
            return "No unused Flatpak runtimes to remove. Nothing was changed."
        return (f"Removed {len(removed)} unused Flatpak runtime(s). No installed "
                f"app referenced them.")

    cache = _cache_dir()
    if not cache.exists():
        return f"{cache} does not exist, so there is nothing to clear. Nothing was changed."
    try:
        files.refuse_catalogue(cache, "clear")
        files.refuse_sensitive(cache, "clear")
    except files.PathProblem as exc:
        return str(exc)

    removed, freed, failures = _empty_directory(cache)
    if not removed:
        if failures:
            return f"Could not clear {cache}: {failures[0]}. Nothing was removed."
        return f"{cache} was already empty. Nothing was changed."
    note = (f" {len(failures)} item(s) could not be removed ({failures[0]})."
            if failures else "")
    return (
        f"Cleared {removed} item(s) from {cache}, about {files.human_size(freed)}. "
        f"Apps rebuild what they need - do this with them closed, or restart any "
        f"you had open.{note}"
    )


def _post_condition(arguments: dict) -> "tuple[bool, str] | None":
    """Is the cache actually empty, read back from the filesystem?

    A recursive delete reports success from its own arithmetic - it knows how
    many entries it believes it removed. The honest instrument is the directory
    afterwards, so this reads that: a cache that still holds a subdirectory was
    not emptied, whatever the answer said it removed.

    Only `cache` is checkable this way. Flatpak's removal is proven by Flatpak
    itself, which refuses to remove a runtime any app still references, so a
    post-condition there would be a different question than the one asked.
    """
    target = (arguments.get("target") or "").strip().lower()
    if target != "cache":
        return None
    cache = _cache_dir()
    if not cache.exists():
        return False, f"{cache} does not exist, so nothing could have been cleared"
    try:
        left = [name for name in os.listdir(cache)]
    except OSError as exc:
        return False, f"the cache could not be read back: {exc.__class__.__name__}"
    if left:
        return False, (f"{cache} still holds {len(left)} entries after clearing "
                       f"({', '.join(left[:3])}), so it was not emptied")
    return True, f"{cache} is empty"


POST_CONDITION = _post_condition

SKILLS = [Skill(name="cleanup_apply", schema=_SCHEMA, run=_run)]