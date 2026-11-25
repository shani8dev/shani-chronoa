"""Backup adapters: one thin uniform surface over the tools already present.

`backup_status` answers the important question for restic - is there a snapshot,
and does the repository pass its own check - and it answers it from real output
measured on a ShaniOS slot. This is the optional P3 integration around it: the
smallest useful version of "backup backend adapters" that does not pretend a
copy exists when it has not made one.

Three adapters, one shape:

- `restic` - delegates `check` to `backup_status`, so there is one parser of the
  snapshot table, not two that can disagree;
- `borg` - reads `borg list --json` only when borg is installed and a repository
  was named. An unreadable repository is an honest "could not check";
- `rsync` - a copy to a destination, not a repository. It has no history to
  check, and saying that is the useful answer rather than inventing one.

`plan` builds the exact argv for a backup and **never runs it**. It is gated by
an explicit confirmation, which is what "off by default" means here: there is
no standing switch to leave on by accident, and no data leaves the machine
until a person has seen the plan and said yes. Even then this skill only prints
the command - running it is a separate act. Paths stay inside the home
directory, and every invocation is an argv list rather than a shell string.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from pathlib import Path

from shani_chronoa import approvals, files
from shani_chronoa.skills import Skill

_TIMEOUT = 60
_BACKENDS = ("restic", "borg", "rsync")
_ACTIONS = ("list", "check", "plan")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "backup_adapters",
        "description": (
            "A small uniform view of local backup tools. list: which of restic, borg and rsync are "
            "installed; check: read a repository's own history (read-only); plan: print the exact backup "
            "command for a repository and paths, after asking. plan never runs the command."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(_ACTIONS)},
                "backend": {"type": "string", "enum": list(_BACKENDS)},
                "repository": {
                    "type": "string",
                    "description": "a local repository or destination path",
                },
                "paths": {
                    "type": "string",
                    "description": "plan: comma-separated folders inside your home",
                },
                "name": {
                    "type": "string",
                    "description": "plan: snapshot/archive name (default: chronoa)",
                },
            },
            "required": ["action"],
        },
    },
}


def installed() -> dict[str, str]:
    return {name: (shutil.which(name) or "") for name in _BACKENDS}


def _local(raw: str, what: str) -> Path:
    try:
        return files.resolve_in_home(str(raw or ""))
    except files.PathProblem as exc:
        raise ValueError(f"{what}: {exc}") from exc


def _check_borg(repo: Path) -> str:
    binary = shutil.which("borg")
    if not binary:
        return files.tool_missing("borg", "read a borg repository")
    try:
        proc = subprocess.run(
            [binary, "list", "--json", str(repo)],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"Could not ask borg about that repository: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        return "Could not read that borg repository: " + (
            detail[-1][:160] if detail else f"borg exited {proc.returncode}"
        )
    try:
        archives = json.loads(proc.stdout or "[]")
        count = len(archives) if isinstance(archives, list) else 0
    except ValueError:
        return "borg answered, but its archive list could not be read"
    return (
        f"{count} archive(s) in that borg repository. A count is not proof the "
        "data is intact; run borg check for that."
    )


def _plan(backend: str, repository: str, paths: str, name: str) -> str:
    binary = shutil.which(backend)
    if not binary:
        return files.tool_missing(backend, "prepare a backup command")
    repo = _local(repository, "repository")
    folders = [p.strip() for p in str(paths or "").split(",") if p.strip()]
    if not folders:
        return "Give the folders to back up, separated by commas."
    sources = [_local(p, "folder") for p in folders]
    label = (
        "".join(c for c in str(name or "chronoa") if c.isalnum() or c in "-_")[:48]
        or "chronoa"
    )
    if backend == "restic":
        argv = [binary, "backup", "--repo", str(repo), *map(str, sources)]
    elif backend == "borg":
        argv = [binary, "create", "--stats", f"{repo}::{label}", *map(str, sources)]
    else:
        if len(sources) != 1:
            return "rsync takes one source folder; give a single path."
        argv = [binary, "-a", str(sources[0]), str(repo)]
    ok, why = approvals.confirm(
        "Prepare a backup command",
        f"Build the {backend} command for {len(sources)} folder(s) into {repo.name}? "
        "Nothing will be run. Allow once?",
    )
    if not ok:
        return f"No command was prepared: {why}."
    return (
        f"Planned (not run): {shlex.join(argv)}\n\nRun this yourself when you are ready. "
        "This skill never starts a backup."
    )


def _run(arguments: dict) -> str:
    args = arguments if isinstance(arguments, dict) else {}
    action = str(args.get("action") or "").strip().lower()
    if action not in _ACTIONS:
        return f"action must be one of {', '.join(_ACTIONS)}."
    found = installed()
    if action == "list":
        lines = [
            f"{name}: {found[name] or 'not installed'} - "
            + {
                "restic": "deduplicating backups with snapshots and an integrity check",
                "borg": "deduplicating archives with a repository and a check command",
                "rsync": "a plain copy to a destination; no history to check",
            }[name]
            for name in _BACKENDS
        ]
        return "Backup adapters on this machine:\n" + "\n".join(lines)

    backend = str(args.get("backend") or "restic").strip().lower()
    if backend not in _BACKENDS:
        return f"backend must be one of {', '.join(_BACKENDS)}."
    if action == "check":
        if not found[backend]:
            return files.tool_missing(backend, "check a backup")
        if backend == "restic":
            from shani_chronoa.skills import backup_status

            return backup_status._run({"repository": str(args.get("repository") or "")})
        if backend == "borg":
            try:
                repo = _local(str(args.get("repository") or ""), "repository")
            except ValueError as exc:
                return str(exc)
            return _check_borg(repo)
        return (
            "rsync copies files to a destination; it keeps no history to check. "
            "Check the destination yourself - this adapter will not pretend a "
            "copy is a backup."
        )

    try:
        return _plan(
            backend,
            str(args.get("repository") or ""),
            str(args.get("paths") or ""),
            str(args.get("name") or ""),
        )
    except ValueError as exc:
        return str(exc)


SKILLS = [Skill(name="backup_adapters", schema=SCHEMA, run=_run)]
