"""Skill: record a change in a repository - commit it, or start a branch.

`git_inspect` can tell you precisely what changed and how far the branch is from
its upstream. Nothing in Chronoa could act on that: of every skill, one touched
git, and it only read. So "what's changed?" had an answer and "commit that" did
not, which for an assistant on a working machine is the wrong way round.

**What is gated, and why.** `git-write-enabled`, off by default. A commit is
not destructive - `git reset --soft HEAD~1` puts it back, and the working tree is
untouched by it - so it is gated rather than asked about every time. But it
writes durable state into a repository, which is what the switch says.

**What is deliberately not here.** `push`. It is the one outward-facing action
in this corner: it publishes to a remote you may not own, from a machine driven
by a language model, and a local commit you regret is one `reset` away while a
pushed commit may already be in somebody's history. It has its own skill, its own
key, and asks every time - see `git_push.py`.

**Only the paths you name are staged.** There is no `-A`, no `-a`, and no
default to "everything": a sweep is the one thing that turns a bounded request
into an unbounded one, and `edit_file` already carries a separate key for
exactly that reason (`bulk-edit-enabled`). If the named files are clean, this
says so and stops, rather than staging whatever else happened to be lying about.

Git is reached through `senses.git.run_git`, the single hardened entry point
with the repository-configuration hardening already applied. Building a second
argv here is how the diff path would drift from this one.
"""

from __future__ import annotations

from pathlib import Path

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill
from shani_chronoa.senses.git import run_git

_CONSENT_KEY = "git-write-enabled"
_ACTIONS = ("commit", "branch")

def _consent(config: ChronoaConfig) -> tuple[bool, str]:
    """Return (allowed, reason). Fail-closed: an undeclared key denies."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            f"writing to a repository is turned off (enable '{_CONSENT_KEY}' in "
            f"Settings). Reading one is not: `git_inspect` still reports what "
            f"changed, the diff, and how far the branch is from its upstream."
        )
    return True, ""


def _repository(argument: str) -> tuple[Path | None, str]:
    """The repository to act in, refusing anything that is not one."""
    try:
        target = files.resolve_in_home((argument or "").strip() or ".")
    except files.PathProblem as exc:
        return None, str(exc)
    if not target.is_dir():
        return None, f"{target} is not a directory."
    probe = run_git(target, "rev-parse", "--show-toplevel")
    if probe is None:
        return None, ("git is not installed, so nothing can be recorded. "
                      "Nothing was changed.")
    if probe.returncode != 0:
        return None, f"{target} is not inside a git repository. Nothing was changed."
    return target, ""


def _branch_now(target: Path) -> str:
    proc = run_git(target, "rev-parse", "--abbrev-ref", "HEAD")
    if proc is None or proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def _do_commit(target: Path, arguments: dict) -> str:
    named = [str(p).strip() for p in (arguments.get("files") or []) if str(p).strip()]
    if not named:
        return (
            "Name the files to commit. Nothing is staged by default - a sweep "
            "of the whole tree is how a bounded request becomes an unbounded "
            "one, and `edit_file` has its own switch for exactly that."
        )
    message = (arguments.get("message") or "").strip()
    if not message:
        return "Give a commit message. Nothing was staged or committed."

    # Stage exactly what was named, and nothing else. `--` so a path beginning
    # with a dash cannot be read as an option.
    staged = run_git(target, "add", "--", *named)
    if staged is None:
        return "git is not installed, so nothing was staged."
    if staged.returncode != 0:
        detail = (staged.stderr or staged.stdout or "").strip().splitlines()
        return ("Nothing was staged: " +
                (detail[-1][:160] if detail else f"git exited {staged.returncode}"))

    # What actually got staged, read back rather than assumed: a named file may
    # be clean, and then `git commit` would succeed with nothing in it.
    check = run_git(target, "diff", "--cached", "--name-only")
    if check is None or check.returncode != 0:
        return "Could not read the staged changes, so nothing was committed."
    actually = [line.strip() for line in (check.stdout or "").splitlines() if line.strip()]
    if not actually:
        return ("Those files have no changes to record, so nothing was "
                "committed. Nothing was changed.")

    done = run_git(target, "commit", "-m", message)
    if done is None:
        return "git is not installed, so nothing was committed."
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip().splitlines()
        return ("Nothing was committed: " +
                (detail[-1][:160] if detail else f"git exited {done.returncode}") +
                f" {len(actually)} file(s) are still staged.")
    head = run_git(target, "rev-parse", "--short", "HEAD")
    sha = (head.stdout or "").strip() if head is not None and head.returncode == 0 else "?"
    return (f"Committed {len(actually)} file(s) as {sha} on "
            f"{_branch_now(target) or 'the current branch'}: {message}\n"
            + "\n".join(f"  {p}" for p in actually[:20]))


def _do_branch(target: Path, arguments: dict) -> str:
    name = (arguments.get("name") or "").strip()
    if not name:
        return "Give the branch to create."
    # A name git would accept: no leading dash (an option, not a branch), no
    # whitespace, not a revision that looks like one.
    if name.startswith("-"):
        return f"Refusing the branch name {name!r}: it starts with '-', which git reads as an option."
    if any(character.isspace() for character in name):
        return f"Refusing the branch name {name!r}: a branch name cannot contain whitespace."

    existing = run_git(target, "branch", "--list", name)
    if existing is not None and (existing.stdout or "").strip():
        switched = run_git(target, "switch", name)
        if switched is None or switched.returncode != 0:
            return (f"A branch called {name!r} already exists and could not be "
                    f"switched to. Nothing was changed.")
        return (f"{name} already existed; switched to it. Nothing was created or "
                f"changed.")

    created = run_git(target, "switch", "-c", name)
    if created is None:
        return "git is not installed, so no branch was created."
    if created.returncode != 0:
        detail = (created.stderr or created.stdout or "").strip().splitlines()
        return ("No branch was created: " +
                (detail[-1][:160] if detail else f"git exited {created.returncode}"))
    return f"Created and switched to {name}."


def _run(arguments: dict) -> str:
    action = (arguments.get("action") or "").strip().lower()
    if action not in _ACTIONS:
        return f"Action must be one of {', '.join(_ACTIONS)}, not {action!r}."

    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to {action} anything: {reason}"

    target, problem = _repository(arguments.get("path") or "")
    if target is None:
        return problem

    if action == "commit":
        return _do_commit(target, arguments)
    return _do_branch(target, arguments)


def _post_condition(arguments: dict) -> "tuple[bool, str] | None":
    """Read the repository back rather than trusting git's exit code.

    Two claims, each observable: a commit's subject is the newest commit on
    this branch, and a branch request is the branch now checked out. Both are
    asked of the repository itself - `git commit` exiting 0 says git accepted
    the request, which is not the same as the commit being there.

    The commit check compares against `HEAD~1` so a commit whose subject
    already matched an earlier one still counts as new, rather than passing on
    an older message that happened to be identical.
    """
    action = (arguments.get("action") or "").strip().lower()
    target, _problem = _repository(arguments.get("path") or "")
    if target is None:
        return None  # nothing was attempted, so nothing to observe

    if action == "branch":
        name = (arguments.get("name") or "").strip()
        if not name:
            return None
        now = _branch_now(target)
        if now == name:
            return True, f"HEAD is on {name}"
        return False, f"HEAD is on {now or 'an unknown branch'}, not {name}"

    message = (arguments.get("message") or "").strip()
    if not message:
        return None
    head = run_git(target, "log", "-1", "--format=%s")
    if head is None or head.returncode != 0:
        return False, "the newest commit could not be read"
    if (head.stdout or "").strip() != message:
        return False, (f"the newest commit's subject is "
                       f"{(head.stdout or '').strip()[:80]!r}, not {message[:80]!r}")
    previous = run_git(target, "log", "-1", "--format=%s", "HEAD~1")
    if previous is not None and previous.returncode == 0 and \
            (previous.stdout or "").strip() == message:
        return False, ("the newest commit already had this subject before "
                       "this call, so nothing new was recorded")
    sha = run_git(target, "rev-parse", "--short", "HEAD")
    return True, (f"HEAD is {(sha.stdout or '').strip()}: {message[:80]}")


POST_CONDITION = _post_condition

def _base(description: str, properties: dict, required: list) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "",
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


_PATH = {
    "type": "string",
    "description": "The repository. Defaults to the working directory.",
}


def _commit_schema() -> dict:
    """Its own schema, not the shared one: the model reads these.

    `git_commit` without `files` and `message` in the schema is a tool the
    model has to guess at, and `git_write`'s combined schema would put an
    `action` enum in front of it that it cannot use.
    """
    schema = _base(
        "Commit the files you name in a git repository, with a message. Only "
        "the paths given are staged - nothing is swept up from the rest of the "
        "tree. Requires the 'git-write-enabled' consent key. Pushing is a "
        "separate skill: that publishes.",
        {
            "path": _PATH,
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": ("The files to stage, relative to the repository. "
                                "Nothing else is staged. Required - there is no "
                                "default, because a sweep of the whole tree turns "
                                "a bounded request into an unbounded one."),
            },
            "message": {"type": "string", "description": "The commit message."},
        },
        ["files", "message"],
    )
    schema["function"]["name"] = "git_commit"
    return schema


def _branch_schema() -> dict:
    schema = _base(
        "Create a branch in a git repository and switch to it, or switch to a "
        "branch that already exists. Requires the 'git-write-enabled' consent key.",
        {
            "path": _PATH,
            "name": {
                "type": "string",
                "description": "The branch to create and switch to.",
            },
        },
        ["name"],
    )
    schema["function"]["name"] = "git_branch"
    return schema


SKILLS = [
    Skill(name="git_commit", schema=_commit_schema(), run=_run),
    Skill(name="git_branch", schema=_branch_schema(), run=_run),
]