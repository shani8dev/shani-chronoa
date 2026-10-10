"""Skill: apply a `.patch`/`.diff` file to files on this machine.

Nothing here applied one. `resolve_conflict` answers *which files conflict*
under git and stops there, and a hand-copied hunk into a text editor is the
usual way a diff gets applied - which is also how a diff half-applies.

`patch` is in `shani-tools`, so it is on both images by design.

**`patch`'s exit status is not the signal, and that is the main thing this
skill exists to get right.** Measured on this box (GNU patch 2.8), with the
exact flags this uses:

    a hunk that does not apply    ->  "Hunk #1 FAILED at 1.
                                      1 out of 1 hunk FAILED -- saving rejects
                                      to file bad.txt.rej"       **rc=0**
    an already-applied patch      ->  "Reversed (or previously applied) patch
                                      detected!  Skipping patch."  rc=1

**So a reader that trusts rc=0 reports success for a patch that changed
nothing.** Worse, without `--forward`, `patch` *reverses* an already-applied
patch and exits 0 - the file goes back to its old contents and nothing says so.
`--forward` is what turns that into a refusal.

The other measured trap: `patch` prompts. Without `--batch` it asks
`Assume -R? [n]` and **waits**, which is the skill-hanging shape this package
has been bitten by more than once.

**Two actions, and check is the default.** `check` is a dry run - it prints
what `patch` would do and changes nothing. `apply` writes. A preview that has
to be asked for is a preview nobody asks for, so the safe one is the default.

It also reports the files `patch` leaves behind - a `.rej` for hunks it could
not apply and a `.orig` backup - because a directory with new unexpected files
in it is its own surprise, and it removes neither.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import Skill

_TIMEOUT = 30

#: **`--forward` and `--batch`, always.** `--forward` refuses a reversed or
#: already-applied patch instead of applying it backwards and exiting 0;
#: `--batch` never prompts, so nothing waits on a keystroke that will not come.
_FLAGS = ["--forward", "--batch"]

#: Words in `patch`'s output that mean the file did not get what was asked.
_FAILED = ("hunk failed", "hunks failed", "saving rejects")
_SKIPPED = ("skipping patch", "ignored")
_REVERSED = "reversed (or previously applied)"


def _run(command: "list[str]") -> "tuple[str, str, int]":
    try:
        proc = subprocess.run(command, capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return "", f"patch did not answer within {_TIMEOUT}s", 124
    except OSError as exc:
        return "", str(exc), 127
    return proc.stdout or "", proc.stderr or "", proc.returncode


def _verdict(out: str, err: str, rc: int) -> "tuple[str, str]":
    """(verdict, detail). Read from the text, because the exit status does not
    carry it - a failed hunk exits 0, and a skipped one exits 1."""
    combined = (out + err).lower()
    if _REVERSED in combined:
        return "already-applied", (
            "patch says this patch is reversed or already applied, so it "
            "changed nothing")
    if any(word in combined for word in _SKIPPED):
        return "skipped", "patch skipped it"
    if any(word in combined for word in _FAILED):
        return "failed", "at least one hunk did not apply"
    if rc not in (0, 1):
        detail = (err or out).strip().splitlines()
        return "error", (detail[-1].strip() if detail
                         else f"patch exited {rc}")
    if "patching file" in combined or "checking file" in combined:
        return "applied" if "patching file" in combined else "would-apply", ""
    return "nothing", "patch said nothing this reads as a change"


def _sidecars(target: Path) -> "list[Path]":
    """The files `patch` leaves: `.rej` for what it could not apply, `.orig`
    for the backup. Reported, never removed."""
    found = []
    for suffix in (".rej", ".orig"):
        candidate = Path(str(target) + suffix)
        if candidate.exists():
            found.append(candidate)
    return found


def _run_skill(arguments: dict) -> str:
    action = str(arguments.get("action") or "check").strip().lower()
    if action not in ("check", "apply"):
        return (f"Action must be check or apply, not {action!r}. `check` is the "
                "default and changes nothing.")

    if shutil.which("patch") is None:
        return files.tool_missing("patch", "apply a diff to files")

    raw_patch = str(arguments.get("patch") or "").strip()
    if not raw_patch:
        return "Which patch file should I apply?"
    try:
        patch_file = files.resolve_in_home(raw_patch)
    except files.PathProblem as exc:
        return str(exc)
    if not patch_file.is_file():
        return f"{patch_file} is not a file, so there is no patch to apply."

    # Where to apply it. The patch's own paths are relative to somewhere, and
    # `-p` says how much of them to strip; getting this wrong is the usual
    # reason a diff "applies" nothing.
    raw_target = str(arguments.get("path") or "").strip()
    try:
        target_dir = files.resolve_in_home(raw_target or "~")
    except files.PathProblem as exc:
        return str(exc)
    if not target_dir.is_dir():
        return f"{target_dir} is not a directory to apply the patch in."

    try:
        strip = max(0, min(int(arguments.get("strip") or 0), 9))
    except (TypeError, ValueError):
        return f"{arguments.get('strip')!r} is not a number."

    command = ["patch", *_FLAGS, f"-p{strip}", "--directory", str(target_dir),
               "--input", str(patch_file)]
    if action == "check":
        command.append("--dry-run")

    out, err, rc = _run(command)
    verdict, detail = _verdict(out, err, rc)

    # **The verdict is a phrase, not a bare word.** The first version interpolated
    # the verdict directly, so the already-applied case printed
    # "`patch` already-applied this patch." - a hyphenated verb from a label that
    # was only ever meant to be matched against.
    _PHRASES = {
        "would-apply": "would apply", "already-applied": "found this patch "
        "already applied", "applied": "applied", "failed": "could not fully "
        "apply", "skipped": "skipped", "nothing": "made no change from",
        "error": "could not apply",
    }
    lines = [f"`patch` {_PHRASES.get(verdict, verdict)} this patch"
             + (f" in {target_dir}" if action == "check" else ".")]
    for line in (out + err).splitlines():
        text = line.strip()
        if text and not text.startswith("checking file"):
            lines.append(f"  {text}")

    lines.append("")
    if verdict == "already-applied":
        lines.append("**This patch is already applied** (or reversed), so "
                     "nothing changed. That is a different answer from it "
                     "failing, and patch does not apply it backwards.")
    elif verdict == "failed":
        lines.append("**Some hunks did not apply.** A `.rej` file holds them "
                     "next to the file they were for; the file itself is "
                     "unchanged in the places those hunks cover.")
    elif verdict in ("applied", "would-apply"):
        lines.append("Applied." if action == "apply"
                     else "It would apply. Ask me to `apply` it to do so.")

    sidecars = []
    for token in (out + err).split():
        if token.endswith(".rej"):
            candidate = Path(token) if token.startswith("/") else \
                target_dir / token
            sidecars.append(candidate)
    for path in (target_dir).glob("**/*.rej"):
        if path not in sidecars:
            sidecars.append(path)
    if sidecars:
        lines.append("")
        lines.append(f"`patch` left {len(sidecars)} `.rej` file(s) holding "
                     "what it could not apply:")
        for path in sidecars[:10]:
            lines.append(f"  {path}")
        if len(sidecars) > 10:
            lines.append(f"  ... {len(sidecars) - 10} more")

    lines.append("")
    lines.append("**What this does not claim**: that the result is what the "
                 "patch's author intended. `patch` applies hunks by context, "
                 "so a patch that lands on different code than it was written "
                 "for can apply cleanly and still be wrong.")
    return "\n".join(lines)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "apply_patch",
        "description": (
            "Apply a .patch or .diff file to files on this machine. Use for "
            "'apply this diff', 'here is a patch', 'merge this change in'. "
            "Runs a dry run by default and changes nothing until asked. Reports "
            "when the patch is already applied, which is a refusal rather than "
            "a reverse-applied change, and lists the .rej files patch leaves "
            "for hunks it could not apply. Never prompts."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "patch": {"type": "string",
                          "description": "The patch file to apply."},
                "path": {"type": "string",
                         "description": ("Directory to apply it in. Defaults to "
                                         "your home directory.")},
                "strip": {"type": "integer",
                          "description": ("`-p` level: how many leading path "
                                          "components to strip from the "
                                          "patch's file names. Default 0.")},
                "action": {"type": "string", "enum": ["check", "apply"],
                           "description": ("`check` (default) is a dry run; "
                                           "`apply` writes.")},
            },
            "required": ["patch"],
        },
    },
}

SKILLS = [Skill(name="apply_patch", schema=SCHEMA, run=_run_skill)]
