"""Skill: what state is this checkout in - git, subversion or mercurial?

`git_inspect`, `git_branch` and `git_commit` answer this for git, and git is
the only one of the three they cover. **`shani-tools-extra` installs
`subversion` and `mercurial` by design**, so a machine can carry a `.svn` or a
`.hg` directory and be told by every existing skill that it is not a
repository - which is a confident wrong answer about a folder that is
genuinely one.

**The kind of repository is read off the directory, not guessed from the
path**, and each system marks its own working copy:

    .git   git        (a file, in a worktree or submodule)
    .svn   subversion (Subversion 1.7+ puts exactly one at the checkout root)
    .hg    mercurial

Reading the marker is what makes "which VCS is this" answerable without
running anything, and it is also how a nested checkout is told from a
top-level one.

**Every output shape below was measured on a real ShaniOS `@blue` slot**
(`shani-testbed/slot-tests/chronoa-cli-formats.sh`, subversion 1.14.5 and
mercurial 7.2.4), because neither tool exists on an Ubuntu dev box and a
parser written from a man page would be a guess:

    svn info        Path: ...
                    Working Copy Root Path: ...
                    URL: file:///...
                    Relative URL: ^/
                    Repository Root: file:///...
                    Repository UUID: ...
                    Revision: 0
                    Node Kind: directory

    hg summary      parent: -1:000000000000 tip (empty repository)
                    branch: default

**Two measured facts that shape the parse:**

- **`Revision: 0` is a real revision** - the empty state of a fresh checkout -
  and not a failure. Reading it as "unset" reports a brand-new working copy as
  unversioned.
- **`hg summary` prints `-1:000000000000` for a repository with no commits**,
  with the words `(empty repository)` beside it. That is a first repository
  being *correct*, and the parent is not a missing value.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import Skill

_TIMEOUT = 30

#: (marker, system, binary). The marker is what identifies a working copy; the
#: binary is what reads it, and they are separate questions - a `.hg` with no
#: `hg` installed is still a mercurial checkout, just one nothing can report on.
_MARKERS = (
    (".git", "git", "git"),
    (".svn", "subversion", "svn"),
    (".hg", "mercurial", "hg"),
)

#: `svn info`'s `Key: value` table. Keys hold spaces and a URL, so the value
#: is everything after the first colon - never a whitespace split.
_SVN_KEYS = ("URL", "Relative URL", "Repository Root", "Revision",
             "Node Kind", "Repository UUID", "Working Copy Root Path",
             "Last Changed Author", "Last Changed Rev", "Last Changed Date")


def _run(command: "list[str]") -> "tuple[str, str, int]":
    try:
        proc = subprocess.run(command, capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return "", f"{command[0]} did not answer within {_TIMEOUT}s", 124
    except OSError as exc:
        return "", str(exc), 127
    return proc.stdout or "", proc.stderr or "", proc.returncode


def detect(path: Path) -> "tuple[str, str]":
    """(system, marker). `system` is "" when the path is not a working copy.

    **Walked upwards**, because a person names a file inside a checkout far
    more often than the checkout root, and "this folder is not a repository"
    is the wrong answer for it.
    """
    current = path if path.is_dir() else path.parent
    for candidate in [current, *current.parents]:
        for marker, system, _ in _MARKERS:
            if (candidate / marker).exists():
                return system, str(candidate / marker)
    return "", ""


def _svn(target: Path) -> "tuple[list, str]":
    out, err, rc = _run(["svn", "info", str(target)])
    if rc == 124:
        return [], err
    if rc != 0:
        # Measured: asking about a path that is not a working copy exits 1
        # with "is not a working copy" - a real answer, not a failure to read.
        detail = (err or out).strip().splitlines()
        return [], (detail[-1] if detail else f"svn exited {rc}")
    rows = []
    for line in out.splitlines():
        key, sep, value = line.partition(":")
        key = key.strip()
        if sep and key in _SVN_KEYS:
            rows.append((key, value.strip()))
    if not rows:
        return [], "svn printed no field this reads"
    return rows, ""


def _hg(target: Path) -> "tuple[list, str]":
    """`hg summary` for the state, `hg root` for the root.

    **Run from inside the repository**, because `hg summary` answers about the
    working copy of the *current directory* - which is measured: run against a
    path outside a checkout it exits 255 and prints nothing.
    """
    root, err, rc = _run(["hg", "root", "-R", str(target)])
    if rc == 124:
        return [], err
    if rc != 0 or not root.strip():
        detail = (err or "").strip().splitlines()
        return [], (detail[-1] if detail
                    else f"hg exited {rc} with no repository root")
    summary, serr, src = _run(["hg", "summary"])
    if src != 0:
        detail = (serr or "").strip().splitlines()
        return [], (detail[-1] if detail else f"hg summary exited {src}")
    rows = [("root", root.strip())]
    for line in summary.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            rows.append((key.strip(), value.strip()))
    return rows, ""


def _git(target: Path) -> "tuple[list, str]":
    out, err, rc = _run(["git", "-C", str(target), "status", "--short",
                         "--branch"])
    if rc == 124:
        return [], err
    if rc != 0:
        detail = (err or out).strip().splitlines()
        return [], (detail[-1] if detail else f"git exited {rc}")
    rows = []
    for line in out.splitlines():
        if line.startswith("## "):
            rows.append(("branch", line[3:].strip()))
        elif line.strip():
            rows.append(("changed", line.rstrip()))
    return rows, ""


def _run_skill(arguments: dict) -> str:
    raw = str(arguments.get("path") or "").strip()
    if not raw:
        return ("I need a folder to look at. Ask me about a path inside a git, "
                "subversion or mercurial checkout.")
    path = Path(files.expand(raw))
    if not path.is_absolute() or not path.exists():
        return f"There is nothing at {path}. Nothing was guessed."

    system, marker = detect(path)
    if not system:
        return (f"{path} is not inside a working copy. I looked for .git, "
                ".svn and .hg in it and every folder above it, and found "
                "none. A plain folder of files is not a repository, so there "
                "is no state to report.")

    binary = dict((s, b) for _, s, b in _MARKERS)[system]
    if shutil.which(binary) is None:
        return (f"This is a {system} working copy (it has {marker}), but "
                f"{binary} is not installed, so nothing can be read out of it. "
                f"That is the {binary} package.")

    if system == "git":
        rows, problem = _git(path)
    elif system == "subversion":
        rows, problem = _svn(path)
    else:
        rows, problem = _hg(path)

    head = f"{path.name} is a {system} working copy."
    if problem:
        # The repository was identified from disk; failing to *read* it is a
        # different fact from there being no repository, and the two are not
        # collapsed here.
        return (f"{head} but {binary} could not report on it: {problem}. "
                "Nothing is claimed about its state.")

    lines = [head]
    for key, value in rows:
        lines.append(f"  {key}: {value}")
    return "\n".join(lines)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "vcs_status",
        "description": (
            "What state a checkout is in, for git, subversion or mercurial: "
            "which version control system it belongs to, the branch or "
            "revision, the repository it points at, and what has changed. "
            "Use when the answer might not be git - 'what is this folder's "
            "state', 'which repository is this', 'what changed in my "
            "checkout'. Read-only; runs nothing that modifies a repository."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": ("Any path inside the checkout. The "
                                         "checkout root is found by walking "
                                         "upwards.")},
            },
            "required": ["path"],
        },
    },
}

SKILLS = [Skill(name="vcs_status", schema=SCHEMA, run=_run_skill)]