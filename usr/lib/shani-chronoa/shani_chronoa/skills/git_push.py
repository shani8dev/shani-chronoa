"""Skill: push a branch to a remote - the one action here that publishes.

Kept out of `git_write.py` on purpose. A local commit you regret is one
`git reset --soft HEAD~1` away; a pushed commit may already be in somebody's
history. Everything else in the git corner acts on this machine and nothing
else, and bundling the two would put a publish action behind a key whose name
sounds like local bookkeeping.

So this one differs in three ways, each deliberate:

- **Its own key**, `git-push-enabled`, so turning on local commits never turns
  on publishing.
- **It is destructive**, so it asks before every push and a standing "yes, for
  this session" does not cover it. That follows from the consent key rather
  than being written out, and it is the property a person would assume about a
  push without having to read this.
- **The remote is a required argument.** There is no default, and no
  `git push --all`: pushing "whatever upstream is" from a machine driven by a
  language model is the shape nobody wants. The name is checked against the
  remotes the repository actually has, and a name that is not one of them is
  refused rather than created.

**Force-pushing is refused outright**, including `--force-with-lease`. It
destroys work that exists only on the remote, and the recovery is a reflog
somebody else has to dig out of a server they may not own. There is no version
of this that is a convenience.
"""

from __future__ import annotations

from pathlib import Path

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill
from shani_chronoa.senses.git import run_git

#: Destructive on purpose - see the module docstring. The membership is derived
#: from this name in `capabilities.DESTRUCTIVE_CONSENT_KEYS`, so the switch and
#: the "asks first, always" the capability list prints cannot disagree.
_CONSENT_KEY = "git-push-enabled"

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "git_push",
        "description": (
            "Push a branch to a named remote. Requires the 'git-push-enabled' "
            "consent key, and asks before every push. The remote must be one the "
            "repository already has, and must be named - there is no default and "
            "no 'push everything'. Force-pushing is refused."),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The repository. Defaults to the working directory.",
                },
                "remote": {
                    "type": "string",
                    "description": "The remote to push to, exactly as the repository names it.",
                },
                "branch": {
                    "type": "string",
                    "description": ("The branch to push. Defaults to the one checked out; "
                                    "it is pushed to the same name on the remote."),
                },
                "set_upstream": {
                    "type": "boolean",
                    "description": "Also set this branch to track the remote one.",
                },
            },
            "required": ["remote"],
        },
    },
}


def _consent(config: ChronoaConfig) -> tuple[bool, str]:
    """Return (allowed, reason). Fail-closed: an undeclared key denies."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            f"pushing to a remote is turned off (enable '{_CONSENT_KEY}' in "
            f"Settings). Recording commits locally is a different switch "
            f"('git-write-enabled') and is unaffected."
        )
    return True, ""


def _repository(argument: str) -> tuple[Path | None, str]:
    try:
        target = files.resolve_in_home((argument or "").strip() or ".")
    except files.PathProblem as exc:
        return None, str(exc)
    if not target.is_dir():
        return None, f"{target} is not a directory."
    probe = run_git(target, "rev-parse", "--show-toplevel")
    if probe is None:
        return None, ("git is not installed, so nothing can be pushed. "
                      "Nothing was changed.")
    if probe.returncode != 0:
        return None, f"{target} is not inside a git repository. Nothing was pushed."
    return target, ""


def _current_branch(target: Path) -> str:
    proc = run_git(target, "rev-parse", "--abbrev-ref", "HEAD")
    if proc is None or proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def _run(arguments: dict) -> str:
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to push: {reason}"

    remote = (arguments.get("remote") or "").strip()
    if not remote:
        return ("Name the remote to push to. There is no default, because "
                "pushing whatever upstream happens to be is the shape nobody "
                "wants from a language model.")
    if remote.startswith("-"):
        return f"Refusing the remote name {remote!r}: it starts with '-', which git reads as an option."

    target, problem = _repository(arguments.get("path") or "")
    if target is None:
        return problem

    known = run_git(target, "remote")
    if known is None:
        return "git is not installed, so nothing was pushed."
    remotes = [line.strip() for line in (known.stdout or "").splitlines() if line.strip()]
    if remote not in remotes:
        return (f"This repository has no remote called {remote!r}. It has "
                f"{', '.join(repr(r) for r in remotes) if remotes else 'none'}, and "
                f"adding one is a separate thing to do deliberately. "
                f"Nothing was pushed.")

    branch = (arguments.get("branch") or "").strip() or _current_branch(target)
    if not branch:
        return ("Could not work out which branch to push, so nothing was pushed. "
                "Name it.")
    if branch.startswith("-"):
        return f"Refusing the branch name {branch!r}: it starts with '-', which git reads as an option."

    argv = ["push"]
    if arguments.get("set_upstream"):
        argv += ["--set-upstream"]
    argv += [remote, branch]
    proc = run_git(target, *argv)
    if proc is None:
        return "git is not installed, so nothing was pushed."
    if proc.returncode != 0:
        detail = [line for line in (proc.stderr or proc.stdout or "").strip().splitlines()
                  if line.strip()]
        return ("Nothing was pushed: " +
                (detail[-1][:180] if detail else f"git exited {proc.returncode}"))
    return f"Pushed {branch} to {remote}."


def _post_condition(arguments: dict) -> "tuple[bool, str] | None":
    """Is the branch actually on the remote, asked of the remote?

    `git push` exiting 0 means the remote accepted it; asking the remote what
    it has is a different question and the only one that answers "published".
    `ls-remote` talks to the server, so this is UNVERIFIED rather than FAILED
    when it cannot - a network problem is not a refused push.
    """
    remote = (arguments.get("remote") or "").strip()
    if not remote:
        return None
    target, _problem = _repository(arguments.get("path") or "")
    if target is None:
        return None
    branch = (arguments.get("branch") or "").strip() or _current_branch(target)
    if not branch:
        return None

    local = run_git(target, "rev-parse", "HEAD")
    if local is None or local.returncode != 0:
        return None
    want = (local.stdout or "").strip()

    asked = run_git(target, "ls-remote", remote, f"refs/heads/{branch}")
    if asked is None or asked.returncode != 0:
        # Cannot reach the remote. That is not evidence the push failed.
        return None
    found = [(line or "").split()[0] for line in (asked.stdout or "").splitlines()
             if line.strip()]
    if not found:
        return False, f"the remote {remote} has no branch {branch}"
    if found[0] != want:
        return False, (f"{remote}/{branch} is at {found[0][:8]}, not the "
                       f"{want[:8]} on this machine")
    return True, f"{remote}/{branch} is at {want[:8]}, which is HEAD"


POST_CONDITION = _post_condition

SKILLS = [Skill(name="git_push", schema=_SCHEMA, run=_run)]