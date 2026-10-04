"""Sense: is this working tree dirty, and how far is it from its upstream?

**This sense is off by default and that is a decision, not an oversight.** Every
other fact in the machine-state set is a property of the hardware or the
operating system. This one is a property of the user's work: `--untracked-files=all`
lists the names of files that do not exist yet and are not committed anywhere,
which means draft notes, half-written code, and whatever else is in the tree,
and the branch name and the ahead/behind count say where in a project they are
and what they have not pushed. That is personal on any reading, in the same way
`accessibility` (which window titles are open) and `idle` (when the day starts
and ends) are personal and are gated off for exactly this reason. It is
`SENSITIVITY_PERSONAL` and the switch is `git-sense-enabled`, defaulted false.

Two ways this could report a clean machine that is not clean, both avoided:

- **Not a repository is UNKNOWN, never "clean tree."** `git status` outside a
  repository exits 128 with "not a git repository" on stderr and nothing on
  stdout. Reading that as an empty porcelain stream is the exact shape of the
  `fuser` bug: an unanswered question recorded as a reassuring answer. A
  non-zero exit is UNKNOWN, always.
- **`git` not installed is UNKNOWN, and `git` not on `$PATH` is checked
  explicitly** rather than inferred from a `FileNotFoundError` mid-call, so the
  two produce the same honest answer instead of one crashing.

**`upstreamGone` is its own state, not `behind = 0`.** When the remote branch
is deleted, `%(upstream:track)` reports `[gone]`, and the behind/ahead numbers
are not zero - they are *meaningless*, and reporting 0/0 would say "you are
completely up to date" about a branch whose upstream no longer exists. That is
the most confidently wrong thing this file could emit, so it is separated.

Detached HEAD is reported as itself, with the commit abbreviated, rather than as
a branch named `HEAD`.

Porcelain v1's status line is `XY PATH`: `X` is the index state, `Y` the
worktree state, a position holding a space means "unchanged", and `?`/`!` are
the untracked and ignored markers rather than states at all. `read_status()`
splits on exactly that, so a file that is both staged and modified counts once
in each column and a `??` line counts only as untracked.

**Git executes configuration, and the configuration comes from the repository.**
This is the module's security boundary, and it is load-bearing rather than
decorative: a hostile repository's own `.git/config` names programs to run,
and `git status` runs one of them. Reproduced end-to-end before this comment
existed - `core.fsmonitor` set to a script ran it during `read_status()`, and
`diff.external` set to a script ran it during `git_inspect diff` (and made the
diff come back *empty*, so a repo with an uncommitted change reported "no
differences"). Any untrusted repository is an attack vector: a clone, a
tarball, a submodule.

So every git invocation in Chronoa goes through `run_git()`, and the flags it
applies are defined exactly once, here. `skills/git_inspect.py` imports that
function rather than building its own argv - a second copy of this hardening is
precisely how the two surfaces drifted apart in the first place, since
`triggers.py` has carried `-c core.fsmonitor=false` since long before these
surfaces existed and neither of them had it.

Each flag below was measured on this machine's git (2.43.0) rather than
assumed, because a hardening flag that does nothing is worse than none: it
reads as protection. Two of them are load-bearing in a way that is not obvious,
and one that looks load-bearing is not:

- **`--no-pager` is the real pager defence, not `-c core.pager=cat`.**
  `git help config` states the order of preference as `$GIT_PAGER`, then
  `core.pager`, then `$PAGER`, and that was confirmed: with
  `GIT_PAGER=<script>` in the environment, `-c core.pager=cat` still executed
  the script and `--no-pager` did not. Worse, a hostile `pager.log` in *system*
  config also survived `-c core.pager=cat` and was killed only by `--no-pager`.
  `core.pager=cat` is kept as a backstop because it *is* measured to beat a
  hostile `core.pager` from global config, but it is not what stops this.
  Until `--no-pager` was added, the only thing suppressing the pager was
  `capture_output=True` making stdout a pipe - i.e. the pipe was accidentally
  load-bearing, and it stops being one the moment a caller passes a tty.

- **`core.fsmonitor=false` is the demonstrated RCE and is genuinely
  load-bearing.** The same manual notes git only learned boolean values for
  `core.fsmonitor` in 2.36, and that 2.35.1 and earlier would treat `false` as
  a hook *pathname*; on 2.43.0 it is verified to suppress a hostile hook.

- **`diff.external=` is deliberately absent.** Emptying it looks like the
  tidy way to disable an external diff, and it is what git documents, but
  measured on 2.43.0 an empty value makes git try to run a program named `""`:
  `error: cannot run : No such file or directory`, `fatal: external diff died`,
  **exit 128, no diff at all**. That is a hardening flag that silently breaks
  the feature. `--no-ext-diff` is the flag that actually disables the
  mechanism, and it still returns a real unified diff.

- `core.sshCommand=`, `core.hooksPath=/dev/null`, `core.editor=` and
  `protocol.ext.allow=never` are defence in depth, and this file says so rather
  than implying more than was measured: the manual scopes `core.sshCommand` to
  "git fetch and git push" and `protocol.<name>.allow` to
  "clone/fetch/push commands", and none of `status`/`branch`/`rev-parse`/
  `rev-list`/`for-each-ref`/`diff`/`log` is one of those. All four were
  verified harmless on all seven subcommands (exit 0, empty stderr). They are
  here so that widening this surface later cannot silently reopen them.
- `GIT_CONFIG_NOSYSTEM=1` is *not* redundant. The hostile-`pager.log` case
  above is exactly the system-config vector, and it is live.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import List, Optional, Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PERSONAL, Sense
from shani_chronoa.senses.context import Percept
from shani_chronoa import subproc

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PERSONAL
_TTL_SECONDS = 600.0
_POLL_INTERVAL = 300.0
_TIMEOUT = 15


def _run_cmd(argv, env=None):
    """This module's seam over `subproc.run` (tests replace it), with the module's timeout."""
    return subproc.run(argv, timeout=_TIMEOUT, env=env)

#: Porcelain v1 status codes, split into what they mean. `??` is untracked - the
#: reason `--untracked-files=all` is used rather than the default: the default
#: collapses an untracked *directory* into one line and would under-report
#: exactly the drafts this sense is most sensitive about.
_UNTRACKED = "??"
_MAX_FILES = 15

#: Upstream states. `gone` is separate from ahead/behind and is never collapsed
#: into it.
UPSTREAM_NONE = "none"
UPSTREAM_GONE = "gone"
UPSTREAM_SET = "set"

#: The `-c` overrides every git invocation in Chronoa carries. See the module
#: docstring for which of these are load-bearing and which are defence in
#: depth, and for the measurements behind both claims. Note what is *not* here:
#: `diff.external=`, because emptying it breaks diff outright (exit 128, no
#: output) rather than disabling anything - see `DIFF_HARDENING`.
_GIT_HARDENING: tuple[str, ...] = (
    "-c", "core.fsmonitor=false",
    "-c", "core.pager=cat",
    "-c", "core.sshCommand=",
    "-c", "core.hooksPath=/dev/null",
    "-c", "core.editor=",
    "-c", "protocol.ext.allow=never",
)

#: Overrides for the child environment, for the same reason. `GIT_CONFIG_NOSYSTEM`
#: is the only thing that stops a hostile *system* config (verified: a
#: `pager.log` in system config executes, and does not with this set), and
#: `GIT_PAGER=cat` covers the `$GIT_PAGER` scope that `-c core.pager=cat` loses
#: to. `--no-pager` covers all of it anyway; these are the backstops.
_GIT_CHILD_ENV: dict[str, str] = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_PAGER": "cat",
}

#: `git diff` reaches repository-controlled execution two ways - `diff.external`
#: and `diff.<driver>.command` selected by a `.gitattributes` `diff=` line -
#: and both were verified to run a hostile script. These two flags cover both,
#: and unlike `diff.external=` they leave a real unified diff behind.
DIFF_HARDENING: tuple[str, ...] = ("--no-ext-diff", "--no-textconv")


def _child_env() -> dict:
    """This process's environment plus the hardening overrides."""
    return {**os.environ, **_GIT_CHILD_ENV}


def run_git(path: Path, *args: str):
    """`git --no-pager -C path <hardening> args`, or None when git cannot be run.

    **The single hardened entry point for git in Chronoa.** Both the `git` sense
    and `skills/git_inspect.py` call this; neither builds its own argv. A
    non-zero exit is returned to the caller rather than collapsed here, because
    for these callers a non-zero exit is a distinct answer ("not a repository",
    "no commits yet") rather than a failure.
    """
    if shutil.which("git") is None:
        return None
    return _run_cmd(
        ["git", "--no-pager", "-C", str(path), *_GIT_HARDENING, *args],
        env=_child_env(),
    )


def read_status(path: Path) -> Optional[dict]:
    """Porcelain status, or None when git could not be asked at all."""
    proc = run_git(path, "status", "--porcelain=v1", "--untracked-files=all")
    if proc is None:
        return None
    if proc.returncode != 0:
        return None
    entries = [line for line in (proc.stdout or "").splitlines() if line.strip()]
    staged = []
    unstaged = []
    untracked = []
    for entry in entries:
        # `XY PATH`, where a space means "unchanged" and ?/! are markers, not states
        index_state = entry[0:1]
        tree_state = entry[1:2]
        if index_state == "?":
            untracked.append(entry)
            continue
        if index_state not in (" ", "!"):
            staged.append(entry)
        if tree_state not in (" ", "!"):
            unstaged.append(entry)
    return {
        "entries": entries,
        "changed": len(entries),
        "staged": len(staged),
        "unstaged": len(unstaged),
        "untracked": len(untracked),
    }


def read_branch(path: Path) -> Optional[str]:
    """The checked-out branch name, or '' for a detached HEAD.

    `git branch --show-current` prints an empty line when HEAD is detached,
    which is a real state and not a missing value.
    """
    proc = run_git(path, "branch", "--show-current")
    if proc is None or proc.returncode != 0:
        return None
    return (proc.stdout or "").strip()


def read_commit_count(path: Path) -> Optional[int]:
    """`git rev-list --count HEAD`, or None for a repository with no commits.

    A freshly `git init`'d repository has a HEAD that resolves to nothing, and
    `rev-list` exits 128 for it. That is "no commits yet", not an error worth
    hiding, so it is reported as zero commits rather than as unknown.
    """
    proc = run_git(path, "rev-list", "--count", "HEAD")
    if proc is None:
        return None
    if proc.returncode != 0:
        return 0
    raw = (proc.stdout or "").strip()
    return int(raw) if raw.isdigit() else None


def read_upstream(path: Path, branch: str) -> Optional[dict]:
    """Upstream name and ahead/behind, or None when git could not be asked.

    `%(upstream:track)` is the one git primitive that distinguishes a deleted
    upstream from a synchronised one: it reports `[gone]` where a bare
    `rev-list` against the upstream would fail, and where reading that failure
    as "0 behind" would be the most confidently wrong line in this file.
    """
    if not branch:
        return None
    fmt = "%(upstream:short)%09%(upstream:track)"
    proc = run_git(path, "for-each-ref", f"--format={fmt}", f"refs/heads/{branch}")
    if proc is None or proc.returncode != 0:
        return None
    line = (proc.stdout or "").strip()
    if not line:
        return {"state": UPSTREAM_NONE, "name": None, "ahead": None, "behind": None}

    name, _, track = line.partition("\t")
    if "gone" in track:
        return {"state": UPSTREAM_GONE, "name": name or None,
                "ahead": None, "behind": None}

    if not name:
        return {"state": UPSTREAM_NONE, "name": None, "ahead": None, "behind": None}

    # Exact counts, rather than parsing "[ahead 1, behind 2]" out of the track
    # string: one source, and it is the one git computes against the merge base.
    proc = run_git(path, "rev-list", "--left-right", "--count", f"{name}...HEAD")
    ahead = behind = None
    if proc is not None and proc.returncode == 0:
        parts = (proc.stdout or "").split()
        if len(parts) == 2 and all(p.isdigit() for p in parts):
            behind, ahead = int(parts[0]), int(parts[1])
    return {"state": UPSTREAM_SET, "name": name, "ahead": ahead, "behind": behind}


def _describe_dirty(status: dict) -> List[str]:
    lines = []
    shown = status["entries"][:_MAX_FILES]
    for entry in shown:
        lines.append(f"  {entry}")
    if status["changed"] > len(shown):
        lines.append(f"  and {status['changed'] - len(shown)} more")
    return lines


def _run(arguments: dict) -> Union[str, Percept]:
    config = ChronoaConfig()
    if not config.sense_allowed("git"):
        return f"Not reading the working tree: {config.sense_allowed_reason('git')}."

    raw = str(arguments.get("path") or "").strip()
    home = Path.home().resolve()
    if not raw:
        target = home
    else:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = home / candidate
        try:
            target = candidate.resolve()
        except (OSError, RuntimeError) as exc:
            return (f"Not reading '{raw}': could not resolve that path "
                    f"({type(exc).__name__}).")
        if not target.is_relative_to(home):
            return (
                f"Not reading '{raw}': it resolves to {target}, which is outside "
                f"your home directory ({home})."
            )

    if shutil.which("git") is None:
        return (
            "Working tree state is UNKNOWN - the git binary is not installed, so "
            "nothing was asked. This is NOT a clean tree: no repository was "
            "inspected, and a machine with no git installed is not a machine "
            "whose work is committed."
        )

    status = read_status(target)
    if status is None:
        return (
            f"Working tree state is UNKNOWN - git could not report status for "
            f"{target}. Either it is not a git repository (git exits 128 with "
            f"'not a git repository' and prints nothing), or git failed. Either "
            f"way nothing was inspected, and that is not the same as a clean tree."
        )

    branch = read_branch(target)
    if branch is None:
        branch = ""
    commits = read_commit_count(target)
    upstream = read_upstream(target, branch)

    if status["changed"] == 0:
        headline = f"{target} is clean - no modified, staged or untracked files"
    else:
        headline = (
            f"{target}: {status['changed']} changed file(s) "
            f"({status['staged']} staged, {status['unstaged']} modified, "
            f"{status['untracked']} untracked)"
        )

    lines = [headline]
    if branch:
        lines.append(f"  branch: {branch}")
    else:
        lines.append("  branch: none - HEAD is detached")
    if commits is not None:
        lines.append(f"  commits reachable from HEAD: {commits}")

    if upstream is not None:
        if upstream["state"] == UPSTREAM_GONE:
            lines.append(
                f"  upstream {upstream['name']} is GONE - that branch no longer "
                f"exists on the remote, so there is no meaningful ahead/behind "
                f"count. This is not the same as being up to date."
            )
        elif upstream["state"] == UPSTREAM_SET:
            if upstream["ahead"] is None:
                lines.append(
                    f"  upstream {upstream['name']}: ahead/behind could not be "
                    f"computed"
                )
            else:
                lines.append(
                    f"  upstream {upstream['name']}: {upstream['ahead']} ahead, "
                    f"{upstream['behind']} behind"
                )
        else:
            lines.append("  upstream: none configured for this branch")

    if status["changed"]:
        lines.extend(_describe_dirty(status))

    return _SENSE.to_percept(
        "\n".join(lines),
        source="git",
        metadata={
            "clean": status["changed"] == 0,
            "changed_files": status["changed"],
            "untracked_files": status["untracked"],
            "branch": branch or None,
            "commits": commits,
            "upstream": None if upstream is None else upstream["state"],
            "ahead": None if upstream is None else upstream["ahead"],
            "behind": None if upstream is None else upstream["behind"],
        },
    )


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "git",
        "description": (
            "Report a git working tree: whether it is clean, how many files are "
            "staged, modified or untracked, the current branch, the number of "
            "commits, and how far ahead of or behind its upstream it is. "
            "Distinguishes a deleted upstream from being up to date, and reports "
            "UNKNOWN - never 'clean tree' - when git is not installed, is not a "
            "repository, or fails. Restricted to paths inside your home "
            "directory, and off unless you turn it on: uncommitted filenames and "
            "branch names are your work, not the machine's state."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Repository to inspect, inside your home directory. "
                        "Defaults to your home directory."
                    ),
                },
            },
        },
    },
}


_SENSE = Sense(
    name="git",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
