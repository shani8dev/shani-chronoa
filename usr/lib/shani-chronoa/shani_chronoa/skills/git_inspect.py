"""Skill: what has changed in a git working tree, and what I committed.

**This is gated on the existing `git-sense-enabled` key, deliberately.** There
is already a `git` *sense* that reports the same facts, and it is off by
default - its own docstring says why, and it is worth repeating because this
skill is where that decision is easy to undo by accident: `--untracked-files`
lists the names of files that do not exist yet, and a branch name plus an
ahead/behind count says where in a project someone is and what they have not
pushed. That is personal on any reading. Shipping this skill ungated would
leave the sense's switch wired to a door that no longer opens anywhere, which
is the same "a gate declared to the UI but absent from the code that acts"
failure this repo has already shipped once. Reusing the sense's key rather
than minting a new one is the same call `list_wifi_networks` makes with
`network-sense-enabled`: noticing a device is not the same agreement as
acting on one, and here the two surfaces report the same data.

**Status, branch and upstream are read through `senses/git.py`'s own
helpers**, not re-implemented. That module is where the porcelain `XY PATH`
parsing, the `%(upstream:track)` `[gone]` case, and the detached-HEAD case
were each worked out against a real repository; a second implementation of
"what branch am I on" is a second thing to get wrong, and this one would have
to be re-derived to match.

Honesty rules, inherited from that module and restated here because the ways
to get them wrong are the ways a language model is most confident:

- **Not a repository is never "clean".** `git status` outside a repository
  exits 128 and prints nothing on stdout; reading that as an empty porcelain
  stream is precisely the bug the `git` sense was written to avoid, and it is
  the most confidently wrong line this skill could emit.
- **`git` missing is UNKNOWN**, checked with `shutil.which` before the call
  rather than inferred from a `FileNotFoundError` inside it, so a missing
  binary and a failed command produce the same honest sentence.
- **A deleted upstream is not "0 behind".** The counts are meaningless when
  the remote branch is gone, and reporting 0/0 would say "you are completely
  up to date" about a branch that no longer has a remote.

**A revision is a value, never an argument.** Every git invocation takes
`argv`, never a shell string, and any user-supplied revision starting with `-`
is refused outright: without that check `revision: "--output=/tmp/x"` is an
option, not a revision, and this skill is reachable by a language model.

**This skill builds no git argv of its own.** Every call goes through
`senses.git.run_git`, which is where the repository-configuration hardening
lives - and it is imported for that, rather than the previous `_run_cmd`
import. Git executes configuration from the repository it is pointed at, so a
hostile repository's own `.git/config` runs a program the moment anyone asks it
for a status or a diff: `core.fsmonitor` and `diff.external` were both
reproduced doing exactly that, through this skill, with consent granted. The
flags that stop it are documented, and measured, in `senses/git.py`'s own
module docstring; the two worth repeating here are that `--no-pager` and not
`-c core.pager=cat` is what defeats `core.pager` (`$GIT_PAGER` outranks
`core.pager`, and a hostile `pager.<command>` beats it too), and that
`diff.external=` must *not* be used to disable an external diff - an empty
value makes git exit 128 with no diff at all. `DIFF_HARDENING` is imported from
the same module for the same reason, so the diff path cannot drift either.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill
from shani_chronoa.senses.git import (
    DIFF_HARDENING,
    UPSTREAM_GONE,
    UPSTREAM_NONE,
    read_branch,
    read_commit_count,
    read_status,
    read_upstream,
    run_git,
)

_CONSENT_KEY = "git-sense-enabled"
_TIMEOUT = 20
_MAX_DIFF_LINES = 300
_MAX_LOG = 30
_SUBCOMMANDS = ("status", "diff", "log", "branch", "upstream", "stash", "reflog")


def _upstream_block(target: Path) -> "tuple[list[str], list[str]]":
    """Where this repository points, and how far ahead or behind it is.

    `lines` is what was read; `unknown` is what could not be, and is reported
    separately rather than folded into the answer. **The three states are kept
    apart because "0 behind" is only true when there is something to be behind**:
    a repository with no remote, a branch with no upstream, and a branch that is
    level with its upstream all print a number, and only the last one means the
    number.

    `rev-list --left-right --count <upstream>...HEAD` is used rather than
    `status -sb`'s `[ahead 1, behind 2]`, because the count is the answer and
    parsing a human-facing summary out of porcelain is a second parser.
    """
    lines: list[str] = []
    unknown: list[str] = []

    remotes = _git(target, "remote", "-v")
    if remotes is None:
        unknown.append("git did not answer for `remote -v`")
    elif not remotes.stdout.strip():
        lines.append("  remotes: none configured - this repository is not "
                     "connected to anything, so 'behind' has no meaning here")
    else:
        seen: dict[str, str] = {}
        for row in remotes.stdout.splitlines():
            parts = row.split()
            if len(parts) >= 2:
                seen.setdefault(parts[0], parts[1])
        for name, url in sorted(seen.items()):
            lines.append(f"  remote {name}: {url}")

    branch = read_branch(target) or ""
    upstream = _git(target, "rev-parse", "--abbrev-ref", "--symbolic-full-name",
                    "@{u}")
    if upstream is None:
        unknown.append("git did not answer for the upstream lookup")
    elif upstream.returncode != 0 or not upstream.stdout.strip():
        lines.append(f"  branch {branch or '(detached HEAD)'}: no upstream set, "
                     "so there is nothing to compare it against")
    else:
        name = upstream.stdout.strip()
        lines.append(f"  branch {branch}: tracks {name}")
        counts = _git(target, "rev-list", "--left-right", "--count",
                      f"{name}...HEAD")
        if counts is None:
            unknown.append(f"git did not answer for the ahead/behind count "
                           f"against {name}")
        elif counts.returncode != 0:
            unknown.append(f"could not compare against {name} - the upstream "
                           "may not have been fetched")
        else:
            parts = counts.stdout.split()
            if len(parts) == 2 and all(p.isdigit() for p in parts):
                behind, ahead = int(parts[0]), int(parts[1])
                if behind == 0 and ahead == 0:
                    lines.append(f"  level with {name} - nothing to pull or push")
                else:
                    lines.append(f"  {behind} commit(s) behind {name}, "
                                 f"{ahead} commit(s) ahead of it")
                    if behind:
                        lines.append("    so a pull would bring in changes you "
                                     "do not have locally")
                    if ahead:
                        lines.append("    so a push would send commits that are "
                                     "not on the remote yet")
    return lines, unknown


def _stash_block(target: Path) -> "tuple[list[str], list[str]]":
    """Stashed work, if any.

    An empty list here is a real answer - "nothing is stashed" - and it is the
    one people most want confirmed, because a stash left behind by an aborted
    rebase is work that exists nowhere else.
    """
    result = _git(target, "stash", "list", "--date=iso")
    if result is None:
        return [], ["git did not answer for `stash list`"]
    rows = [r for r in result.stdout.splitlines() if r.strip()]
    if not rows:
        return ["  no stashed work in this repository"], []
    lines = [f"  {len(rows)} stash(es), newest first:"]
    for row in rows[:20]:
        lines.append(f"    {row.strip()}")
    if len(rows) > 20:
        lines.append(f"    ... and {len(rows) - 20} more")
    return lines, []


def _reflog_block(target: Path) -> "tuple[list[str], list[str]]":
    """Where HEAD has been, which survives rebases and amends.

    `git log` cannot answer "what did I commit yesterday" once a rebase has
    rewritten the branch - the commits are unreachable from it, and the reflog
    is the only record that they happened.
    """
    result = _git(target, "reflog", "--date=iso", "--format=%gd %cd %gs", "-n", "25")
    if result is None:
        return [], ["git did not answer for `reflog`"]
    rows = [r.strip() for r in result.stdout.splitlines() if r.strip()]
    if not rows:
        return ["  this repository has no reflog entries yet - it is either new "
                "or has never had a commit"], []
    lines = [f"  the last {len(rows)} thing(s) HEAD did, newest first:"]
    for row in rows:
        lines.append(f"    {row}")
    return lines, []


_NEW_BLOCKS = {"upstream": _upstream_block, "stash": _stash_block,
               "reflog": _reflog_block}

SCHEMA = {
    "type": "function",
    "function": {
        "name": "git_inspect",
        "description": (
            "Report a git working tree: what has changed and is not committed, "
            "the diff of those changes, recent commits, or the current branch "
            "and how far it is from its upstream. Reports UNKNOWN - never "
            "'clean tree' - when git is missing, the folder is not a "
            "repository, or git fails. Requires the 'git-sense-enabled' consent "
            "key."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "The repository, inside your home directory. Defaults "
                        "to the home directory."
                    ),
                },
                "subcommand": {
                    "type": "string",
                    "description": (
                        f"What to report: {' or '.join(_SUBCOMMANDS)}. "
                        f"Defaults to status."
                    ),
                },
                "revision": {
                    "type": "string",
                    "description": (
                        "Optional commit, branch, or range such as 'HEAD~3' or "
                        "'main...HEAD'. diff compares it against the working "
                        "tree; log starts from it."
                    ),
                },
                "since": {
                    "type": "string",
                    "description": (
                        "log only: commits after a time - 'yesterday', 'today', "
                        "'3 days ago', '2 weeks ago' or a date like 2026-09-30."
                    ),
                },
                "context_lines": {
                    "type": "integer",
                    "description": "Unchanged lines to show around each diff hunk. Defaults to 3.",
                },
            },
        },
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """Return (allowed, reason). Fail-closed: an undeclared key denies."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            f"reading git working trees is turned off (enable '{_CONSENT_KEY}' "
            f"in Settings). Uncommitted filenames and branch names are your "
            f"work, not the machine's state, which is why the git sense is off "
            f"by default - and this reads the same facts."
        )
    return True, ""


def _check_revision(raw) -> "tuple[str, str]":
    """(revision, problem). An unparseable or option-shaped ref is a refusal.

    `argv` rather than a shell string already removes command injection; this
    removes the quieter failure, where a value beginning with `-` is read by
    git as an option of this skill. `--output=` and `--upload-pack=` are both
    options git accepts, so this is not a hypothetical.
    """
    if raw is None:
        return "", ""
    if not isinstance(raw, str):
        return "", f"Revision must be text, not {type(raw).__name__}."
    text = raw.strip()
    if not text:
        return "", ""
    if text.startswith("-"):
        return "", (
            f"Refusing the revision {raw!r}: it starts with '-', which git "
            f"would read as an option rather than a revision. Name a commit, "
            f"branch, or range instead."
        )
    if any(c.isspace() or c == "\0" for c in text):
        return "", f"Refusing the revision {raw!r}: a revision cannot contain whitespace."
    if len(text) > 200:
        return "", f"Refusing a {len(text)}-character revision; that is not a ref."
    return text, ""


def _git(path: Path, *args: str):
    """`git -C path ...`, or None when git is absent, unrunnable, or slow.

    Routed through `senses.git.run_git` rather than calling `subprocess`
    directly, so there is one place where a timeout and a vanished binary
    become "git did not answer" - one place to look when they do not, and one
    place where the repository-configuration hardening is applied. That second
    reason is why this wrapper still exists at all: `shutil.which` is checked
    here rather than only inside `run_git`, because a missing binary has to be
    reportable as one honest sentence, and the test that pins that patches
    `git_inspect.shutil.which`.
    """
    if shutil.which("git") is None:
        return None
    return run_git(path, *args)


def _missing_git() -> str:
    return (
        "Working tree state is UNKNOWN - the git binary is not installed, so "
        "nothing was asked. This is NOT a clean tree: no repository was "
        "inspected."
    )


def _not_a_repo(target: Path) -> str:
    return (
        f"{target} is not inside a git repository, so there is no working tree "
        f"to report. That is not the same as a clean tree - nothing was "
        f"inspected."
    )


def _branch_block(target: Path) -> "tuple[list[str], str]":
    """The branch/upstream lines, shared by `status` and `branch`."""
    lines: list[str] = []
    branch = read_branch(target)
    if branch is None:
        branch = ""
    commits = read_commit_count(target)
    if branch:
        lines.append(f"  branch: {branch}")
    else:
        lines.append("  branch: none - HEAD is detached")
    if commits is not None:
        lines.append(f"  commits reachable from HEAD: {commits}")

    upstream = read_upstream(target, branch)
    if upstream is None:
        lines.append("  upstream: could not be determined")
    elif upstream["state"] == UPSTREAM_GONE:
        lines.append(
            f"  upstream {upstream['name']} is GONE - that branch no longer "
            f"exists on the remote, so there is no meaningful ahead/behind "
            f"count. This is not the same as being up to date."
        )
    elif upstream["state"] == UPSTREAM_NONE:
        lines.append("  upstream: none configured for this branch")
    elif upstream["ahead"] is None:
        lines.append(f"  upstream {upstream['name']}: ahead/behind could not be computed")
    else:
        lines.append(
            f"  upstream {upstream['name']}: {upstream['ahead']} ahead, "
            f"{upstream['behind']} behind"
        )
    return lines, branch


def _run(arguments: dict) -> str:
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to read the working tree: {reason}"

    subcommand = (arguments.get("subcommand") or "status").strip().lower()
    if subcommand not in _SUBCOMMANDS:
        return (
            f"Subcommand must be one of {', '.join(_SUBCOMMANDS)}, not "
            f"{subcommand!r}."
        )
    revision, problem = _check_revision(arguments.get("revision"))
    if problem:
        return f"{problem} Nothing was read."

    try:
        context = int(arguments.get("context_lines") or 3)
    except (TypeError, ValueError):
        context = 3
    context = max(0, min(context, 50))

    try:
        target = files.resolve_in_home((arguments.get("path") or "").strip() or "~")
    except files.PathProblem as exc:
        return str(exc)
    if not target.exists():
        return files.describe(FileNotFoundError(2, "No such file or directory"),
                              target, "read")
    if not target.is_dir():
        return f"{target} is a file, not a directory."

    if shutil.which("git") is None:
        return _missing_git()
    inside = _git(target, "rev-parse", "--is-inside-work-tree")
    if inside is None:
        return (
            f"Working tree state is UNKNOWN - git did not answer within "
            f"{_TIMEOUT}s for {target}."
        )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return _not_a_repo(target)

    if subcommand == "branch":
        lines, _ = _branch_block(target)
        return "\n".join([f"Repository at {target}:", *lines])

    if subcommand in _NEW_BLOCKS:
        lines, unknown = _NEW_BLOCKS[subcommand](target)
        out = [f"Repository at {target}:", *lines]
        if unknown:
            out += ["", "Not determined: " + "; ".join(unknown) +
                    ". Nothing was guessed about it."]
        return "\n".join(out)

    if subcommand == "status":
        status = read_status(target)
        if status is None:
            return (
                f"Working tree state is UNKNOWN - git could not report status "
                f"for {target} even though it is a repository. Nothing was "
                f"inspected."
            )
        if status["changed"] == 0:
            head = f"{target} is clean - no modified, staged or untracked files"
        else:
            head = (
                f"{target}: {status['changed']} changed file(s) "
                f"({status['staged']} staged, {status['unstaged']} modified, "
                f"{status['untracked']} untracked)"
            )
        lines = [head]
        branch_lines, _ = _branch_block(target)
        lines.extend(branch_lines)
        for entry in status["entries"][:_MAX_LOG]:
            lines.append(f"  {entry}")
        if status["changed"] > _MAX_LOG:
            lines.append(f"  ... and {status['changed'] - _MAX_LOG} more, not shown.")
        if status["changed"] == 0:
            lines.append("  Use the diff subcommand for the content of a change.")
        return "\n".join(lines)

    if subcommand == "diff":
        args = ["diff", *DIFF_HARDENING, f"--unified={context}"]
        if revision:
            args.append(revision)
        args.append("--")
        proc = _git(target, *args)
        if proc is None:
            return f"Working tree diff is UNKNOWN - git did not answer within {_TIMEOUT}s."
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            return (
                f"git could not produce a diff for {target} (exit "
                f"{proc.returncode})" + (f": {detail[-1]}" if detail else ".")
                + " Nothing was compared."
            )
        body = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        if not body:
            scope = f"against {revision}" if revision else "in the working tree"
            return (
                f"No diff {scope} for {target}: git compared them and found no "
                f"differences. Note that `git diff` alone shows unstaged changes "
                f"only - a staged change needs `revision: \"--cached\"`... which "
                f"is refused here, so use the status subcommand to see what is "
                f"staged."
            )
        head = f"Diff for {target}" + (f" against {revision}" if revision else "") + ":"
        if len(body) <= _MAX_DIFF_LINES:
            return "\n".join([head, *body])
        return "\n".join([
            head, *body[:_MAX_DIFF_LINES],
            f"... and {len(body) - _MAX_DIFF_LINES} more diff line(s) not "
            f"shown; this is not the whole diff.",
        ])

    args = ["log", f"--max-count={_MAX_LOG}", "--format=%h %ad %an %s",
            "--date=short"]
    since = str(arguments.get("since") or "").strip().lower()
    if since:
        # servers/git `start_timestamp`, kept to shapes git reads unambiguously
        if since == "today":
            since = "midnight"
        if not re.fullmatch(r"yesterday|midnight|\d{4}-\d{2}-\d{2}( \d{2}:\d{2})?|\d{1,3} (minute|hour|day|week|month|year)s? ago", since):
            return ("since must be 'yesterday', 'today', 'N days ago' (or hours/weeks/months) "
                    "or a date like 2026-09-30.")
        args.append(f"--since={since}")
    if revision:
        args.append(revision)
    proc = _git(target, *args)
    if proc is None:
        return f"Commit history is UNKNOWN - git did not answer within {_TIMEOUT}s."
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return (
            f"git could not read the log for {target} (exit {proc.returncode})"
            + (f": {detail[-1]}" if detail else ".")
            + " A repository with no commits yet exits non-zero here, which is "
            "not an error worth hiding - but the history could not be read."
        )
    body = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if not body:
        if since:
            return f"No commits in {target} since {arguments.get('since')}."
        return f"{target} has no commits reachable from HEAD yet."
    head = f"Most recent commits in {target}" + (f" from {revision}" if revision else "") + ":"
    return "\n".join([head, *body])


SKILLS = [Skill(name="git_inspect", schema=SCHEMA, run=_run)]
