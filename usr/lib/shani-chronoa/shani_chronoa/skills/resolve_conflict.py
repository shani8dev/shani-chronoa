"""Skill: what is conflicting, and the three resolutions git can actually do.

Among the skills here, none could answer *"I have a conflict - what is it, and
which side is mine?"* `git_inspect` reports the branch, the diff and the log;
git's own status line prints `UU path/to/file` and stops there. Everything
after that - what the two sides actually say, which is base, which is yours,
which is theirs - is the part a person has to work out by hand.

**Git keeps all three versions, and that is what makes this answerable.** An
unmerged path has three blobs in the index: stage 1 the common ancestor, stage 2
yours, stage 3 theirs. `git ls-files -u` names them and `git show :2:path`
prints one. So the answer is read, not guessed at - there is no heuristic here
about which line "looks right".

**Four actions, and what is deliberately not among them.** `show`, `ours`,
`theirs`, `abort`. There is no auto-merge that decides which side is correct,
because that is a judgement about the code and only the person knows it. What
git can do mechanically - take one whole side, or abandon the operation - is
what this offers, and it says which it did.

**Two consent keys, and the line between them.** `show` reads the conflicted
files and their contents, which is the same class of fact `git_inspect` already
reads, so it follows the `git-sense-enabled` switch that skill is off by
default. The other three **write to the working tree**, so they take
`git-write-enabled` - the key that already exists for `git_commit` and
`git_branch`, because the question they answer is the same one: may Chronoa
change a repository.

**`ours` and `theirs` are named from your side, not git's.** In a rebase git's
`ours` and `theirs` mean the *opposite* of what they mean in a merge, because
the commits being replayed swap roles. So every action here reports which side
it took **and which file**, rather than trusting the person - or the model - to
remember which operation they are in the middle of.
"""

from __future__ import annotations

import pathlib
import shutil
from typing import List, Optional

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_TIMEOUT = 20
_READ_KEY = "git-sense-enabled"
_WRITE_KEY = "git-write-enabled"

#: The operations that can stop half-done, and how each is abandoned. Order
#: matters: rebase is checked before merge because a rebase leaves MERGE_HEAD
#: behind in some versions, and the wrong `abort` for the operation in progress
#: is its own kind of damage.
_OPERATIONS = (
    ("rebase", ("rebase-merge", "rebase-apply"), "rebase --abort"),
    ("cherry-pick", ("CHERRY_PICK_HEAD",), "cherry-pick --abort"),
    ("revert", ("REVERT_HEAD",), "revert --abort"),
    ("merge", ("MERGE_HEAD",), "merge --abort"),
)

#: The index stage each side is stored at. These are git's numbers, not a
#: choice this file made, and `git ls-files -u` prints them.
_STAGES = {1: "the common ancestor (base)", 2: "YOUR side (ours)",
           3: "THEIR side (theirs)"}

_ACTIONS = ("show", "ours", "theirs", "mark", "abort")


def _consent(config: ChronoaConfig, key: str) -> tuple:
    """Return (allowed, reason) for one key. Fail-closed: undeclared denies.

    The same shape `git_write.py` uses, and deliberately so - the gate table
    in `capabilities.GATED` names one key per tool, so the second key has to be
    checked in the module, and this is where the package keeps that check.
    """
    if not config.get_bool(key, False):
        return False, (_REFUSAL_REASON if key == _READ_KEY else _WRITE_REASON)
    return True, ""


def _git(path, *args: str):
    """`git -C path ...`, or None when git is absent or slow.

    Routed through the sense's own runner, so a timeout and a vanished binary
    both become "git did not answer" in one place rather than per call site.
    """
    from shani_chronoa.senses import git
    return git.run_git(path, *args)


def _git_dir(path: pathlib.Path) -> pathlib.Path:
    """The repository's `.git`, whether `path` is the root or a subdirectory."""
    top = _git(path, "rev-parse", "--absolute-git-dir")
    if top is not None and top.returncode == 0 and top.stdout.strip():
        return pathlib.Path(top.stdout.strip())
    return path / ".git"


def _in_progress(path: pathlib.Path) -> List[tuple]:
    """`[(name, abort argv...)]` for every operation currently half-done."""
    gd = _git_dir(path)
    found = []
    for name, markers, _abort in _OPERATIONS:
        if any((gd / m).exists() for m in markers):
            found.append((name, _abort))
    return found


def _unmerged(path: pathlib.Path) -> Optional[List[dict]]:
    """The unmerged paths and which side each stage holds.

    `git ls-files -u` prints one line per **stage**, not per path, so the same
    file appears three times - once for the base, once for yours, once for
    theirs. A path with only two stages is not a normal merge conflict and is
    reported as what it is rather than being papered over.
    """
    out = _git(path, "ls-files", "-u")
    if out is None:
        return None
    rows: dict[str, dict] = {}
    for line in out.stdout.splitlines():
        head, _tab, name = line.partition("\t")
        parts = head.split()
        if len(parts) != 3 or not name.strip():
            continue
        stage = parts[2]
        if not stage.isdigit():
            continue
        entry = rows.setdefault(name, {"path": name, "stages": {}})
        entry["stages"][int(stage)] = parts[1]
    return [rows[k] for k in sorted(rows)]


def _side_text(path: pathlib.Path, name: str, stage: int,
               limit: int = 400) -> str:
    """One side's text for a conflicted file, from the index."""
    out = _git(path, "show", f":{stage}:{name}")
    if out is None or out.returncode != 0:
        return ""
    body = out.stdout
    return body if len(body) <= limit else body[:limit] + "\n... (cut off)"


def _operation_header(path: pathlib.Path, rows: List[dict]) -> str:
    ops = _in_progress(path)
    if ops:
        names = ", ".join(n for n, _a in ops)
        return (f"There is a {names} in progress in {path} and "
                f"{len(rows)} file(s) are unmerged.")
    return (f"{len(rows)} file(s) are unmerged in {path}, but git reports no "
            f"merge, rebase, cherry-pick or revert in progress - so these were "
            f"left over from an operation that was already stopped.")


def _show(path: pathlib.Path) -> str:
    rows = _unmerged(path)
    if rows is None:
        return ("The conflicted files are UNKNOWN - git did not answer "
                "`ls-files -u` within the timeout. Nothing was inspected.")
    if not rows:
        return (f"Nothing is conflicted in {path} right now. If you were "
                f"expecting a conflict, the operation may not have started, or "
                f"may have been finished or aborted already - `git log -1` and "
                f"`git status` will show which.")

    lines = [_operation_header(path, rows), ""]
    for row in rows:
        stages = row["stages"]
        lines.append(f"{row['path']}")
        if set(stages) != set(_STAGES):
            # Not a three-way conflict. Report the shape; do not guess.
            lines.append(f"  unusual: stages present are "
                         f"{sorted(stages)} (a normal content conflict has 1, 2, 3)")
        for stage in sorted(stages):
            label = _STAGES.get(stage, f"stage {stage}")
            lines.append(f"  stage {stage} - {label}")
            body = _side_text(path, row["path"], stage)
            for line in (body.splitlines() or ["(empty)"])[:12]:
                lines.append(f"      {line}")
            if body.count("\n") > 12:
                lines.append("      ... (more)")
        lines.append("  to take a whole side: action=ours or action=theirs with "
                     "this path; to resolve by editing, fix the file then "
                     "action=mark.")
        lines.append("")
    lines.append("No automatic merge was attempted. Deciding which line is "
                 "correct is a judgement about the code, and only you can make "
                 "it.")
    return "\n".join(lines)


def _resolve(path: pathlib.Path, args: dict, side: int) -> str:
    rows = _unmerged(path)
    if rows is None:
        return "Nothing was changed: git did not answer `ls-files -u`."
    if not rows:
        return (f"Nothing is conflicted in {path}, so no file was taken from "
                f"either side.")

    wanted = [p.strip() for p in str(args.get("paths") or "").split(",")
              if p.strip()]
    targets = [r["path"] for r in rows if not wanted or r["path"] in wanted]
    unknown = [p for p in wanted if p not in {r["path"] for r in rows}]
    if not targets:
        return (f"Nothing was changed. {', '.join(unknown)} "
                f"{'is not a conflicted file' if len(unknown) == 1 else 'are not conflicted files'}"
                f" here; the conflicted files are "
                f"{', '.join(r['path'] for r in rows)}.")

    label = "YOUR side" if side == 2 else "THEIR side"
    done, problems = [], []
    for name in targets:
        checkout = _git(path, "checkout", "--ours" if side == 2 else "--theirs",
                        "--", name)
        if checkout is None or checkout.returncode != 0:
            problems.append(f"{name} (checkout failed)")
            continue
        add = _git(path, "add", "--", name)
        if add is None or add.returncode != 0:
            problems.append(f"{name} (could not mark it resolved)")
            continue
        done.append(name)

    out = [f"Took {label} in {len(done)} file(s) and marked them resolved."]
    for name in done:
        out.append(f"  {name}")
    if problems:
        out.append(f"Not changed: {', '.join(problems)}.")
    left = _unmerged(path)
    if left:
        out.append(f"{len(left)} file(s) are still unmerged - run this again for "
                   f"them, or edit them by hand.")
    return "\n".join(out)


def _mark(path: pathlib.Path, args: dict) -> str:
    """Mark files resolved after the person has edited them by hand."""
    rows = _unmerged(path)
    if rows is None:
        return "Nothing was changed: git did not answer `ls-files -u`."
    if not rows:
        return (f"Nothing is unmerged in {path}, so there was nothing to mark. "
                f"Use write_text_file or edit_file to change a file, then "
                f"action=mark with its path.")
    conflicted = {r["path"] for r in rows}
    wanted = [p.strip() for p in str(args.get("paths") or "").split(",")
              if p.strip()]
    if not wanted:
        return (f"Name the file to mark resolved. The unmerged files are "
                f"{', '.join(sorted(conflicted))}.")
    targets = [p for p in wanted if p in conflicted]
    if not targets:
        return (f"Nothing was changed. {', '.join(wanted)} "
                f"{'is not' if len(wanted) == 1 else 'are not'} a conflicted file "
                f"here; the unmerged files are {', '.join(sorted(conflicted))}.")

    done, problems = [], []
    for name in targets:
        add = _git(path, "add", "--", name)
        if add is None or add.returncode != 0:
            problems.append(name)
        else:
            done.append(name)
    out = [f"Marked {len(done)} file(s) resolved" + (f": {', '.join(done)}."
                                                       if done else ".")]
    if problems:
        out.append(f"Could not mark: {', '.join(problems)}.")
    left = _unmerged(path)
    out.append(f"{len(left)} file(s) are still unmerged."
               if left else "Nothing is unmerged any more - the operation can "
                            "be finished with `git commit`.")
    return "\n".join(out)


def _abort(path: pathlib.Path, args: dict) -> str:
    ops = _in_progress(path)
    if not ops:
        return (f"There is no merge, rebase, cherry-pick or revert in progress "
                f"in {path}, so there was nothing to abort and nothing was "
                f"changed.")
    name, abort = ops[0]
    result = _git(path, *abort.split())
    if result is None:
        return (f"Could not abort the {name} - git did not answer. Nothing is "
                f"known to have changed.")
    if result.returncode != 0:
        detail = (result.stderr.strip().splitlines() or ["no message"])[-1][:200]
        return (f"git refused to abort the {name}: {detail}. Nothing was "
                f"changed by this.")
    return (f"Aborted the {name} in {path}. The working tree is back to how it "
            f"was before it started, and the commits that were being applied are "
            f"still there to try again.")


def _run(args: dict) -> str:
    action = (args.get("action") or "show").strip().lower()
    if action not in _ACTIONS:
        return (f"Action must be one of {', '.join(_ACTIONS)}, not {action!r}.")

    try:
        target = files.resolve_in_home((args.get("path") or "").strip() or "~")
    except files.PathProblem as exc:
        return str(exc)
    if not target.exists():
        return files.describe(FileNotFoundError(2, "No such file or directory"),
                              target, "read")
    if not target.is_dir():
        return f"{target} is a file, not a directory."
    if shutil.which("git") is None:
        return ("Resolving conflicts needs git, and there is no git on this "
                "machine. Nothing was changed.")

    inside = _git(target, "rev-parse", "--is-inside-work-tree")
    if inside is None:
        return ("Whether this is a repository is UNKNOWN - git did not answer "
                "within the timeout. Nothing was inspected or changed.")
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return (f"{target} is not inside a git repository, so there are no "
                f"conflicts in it to resolve.")

    config = ChronoaConfig()
    allowed, reason = _consent(config, _READ_KEY)
    if not allowed:
        return f"Refusing to read conflicted files: {reason}"

    if action == "show":
        return _show(target)

    allowed, reason = _consent(config, _WRITE_KEY)
    if not allowed:
        return f"Refusing to change the working tree: {reason}"
    if action == "ours":
        return _resolve(target, args, 2)
    if action == "theirs":
        return _resolve(target, args, 3)
    if action == "mark":
        return _mark(target, args)
    return _abort(target, args)


_REFUSAL_REASON = (
    "conflicted file names and their contents are your work, not the machine's "
    "state, which is why the git sense is off by default")
_WRITE_REASON = (
    "taking one side of a conflict and marking files resolved change the working "
    "tree, which is what the git write switch says")


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "resolve_conflict",
        "description": (
            "Work out what is conflicting in a git repository and resolve it. "
            "'show' lists every unmerged file and prints all three sides git "
            "holds - the common ancestor, your side and theirs - so there is no "
            "guessing about which is which; 'ours' and 'theirs' take a whole "
            "side of the named files and mark them resolved; 'mark' marks files "
            "you have edited by hand; 'abort' abandons the merge, rebase or "
            "cherry-pick and leaves the commits to try again. There is no "
            "automatic merge, because deciding which line is right is a "
            "judgement about the code and only you can make it. Reading needs "
            "'git-sense-enabled'; changing the working tree needs "
            "'git-write-enabled'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_ACTIONS),
                    "description": "'show' what is conflicted; 'ours'/'theirs' take a side; 'mark' a hand-edited file resolved; 'abort' the operation.",
                },
                "path": {
                    "type": "string",
                    "description": "The repository, inside your home directory. Defaults to the home directory.",
                },
                "paths": {
                    "type": "string",
                    "description": "Comma-separated conflicted files, for 'ours', 'theirs' and 'mark'. All of them when omitted.",
                },
            },
            "required": ["action"],
        },
    },
}

SKILLS = [Skill(name="resolve_conflict", schema=_SCHEMA, run=_run)]