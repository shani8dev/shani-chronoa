"""Skill: run a command in the project - its tests, its build, its linter.

**Why this exists.** Every other coding agent closes the loop: edit, run the
tests, read the failure, edit again. Chronoa could edit (`edit_file`), show the
change (the Diff panel) and commit it (`git_commit`), and could not find out
whether the change *worked* - there was no way to run anything. So a turn that
said "fix the failing test" ended with an edit nobody had run.

**What is gated, and how.** `run-command-enabled`, off by default, and in
`capabilities.DESTRUCTIVE_CONSENT_KEYS`, so with "Ask first" every call is its
own question showing the exact command. A command can do anything the person
can, which is the honest description of the switch.

**What it is deliberately not.**

- Not a shell. `command` is split with `shlex` into an argv and run directly:
  no pipes, redirections, `;`, `&&`, globbing or `$(...)`. One program per
  call, so the approval prompt shows the whole of what will run and the
  sandbox's argv guards can read it. A pipeline is several calls. (`sh -c
  '...'` is still one program and is not refused: it is shown verbatim in
  the question and confined exactly the same way - this rule keeps the
  *common* case readable, it is not the boundary.)
- Not interactive. stdin is closed; a command that waits for input times out.
- Not outside your home. The working directory resolves inside home, and
  defaults to the project folder (`CHRONOA_PROJECT`, set by Build mode).

**Where it runs** (`where`), strongest-isolation first by default:

- `sandbox` (default): bubblewrap + Landlock + seccomp, set up by
  `tools._run_command_sandbox` - read-only system, the project writable in
  place, `~/.ssh` and the rest of home unreadable, network cut unless
  `network` is true.
- `container`: a rootless podman container from `image`, the project mounted
  at `/work`, `--network=none` unless `network`. Through the container skill's
  transient-unit path, because podman cannot start under a skill's ceiling.
- `machine`: an nspawn container or vmspawn VM registered with machined
  (`machinectl list`), through `systemd-run --machine --pipe --wait`. The
  project must exist at the same path inside it; polkit decides whether you
  may.

The answer leads with the exit code, then the output - head and tail kept when
it is long, because a test runner prints its summary last.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import time
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

CONSENT_KEY = "run-command-enabled"

#: Default and ceiling, in seconds. The ceiling matches the sandbox's
#: full-access profile, which `tools.py` gives this tool; a longer request is
#: clamped rather than refused.
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 600

#: How much output comes back. A full test log can be megabytes; the model
#: needs the failures and the summary, which are at the end.
HEAD_CHARS = 2500
TAIL_CHARS = 5500

#: Programs refused outright. Not a security boundary - the consent and the
#: per-call question are - but the ones that are never what "run the tests"
#: meant, and would take the machine or the session with them.
REFUSED_PROGRAMS = frozenset({"sudo", "su", "pkexec", "doas", "shutdown",
                              "reboot", "poweroff", "halt", "mkfs", "dd"})


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    if not config.get_bool(CONSENT_KEY, False):
        return False, ("running commands is turned off - turn on 'Let Chronoa run "
                       "commands in your project' in Settings, Privacy.")
    return True, ""


def _workdir(raw: str) -> "tuple[Path | None, str]":
    """Where to run: the given folder, else the project, else home."""
    text = (raw or "").strip()
    if not text:
        project = os.environ.get(files.PROJECT_ENV, "").strip()
        text = project or "~"
    try:
        path = files.resolve_in_home(text)
    except files.PathProblem as exc:
        return None, str(exc)
    if not path.is_dir():
        return None, f"{path} is not a folder."
    return path, ""


def _clip(text: str) -> str:
    if len(text) <= HEAD_CHARS + TAIL_CHARS:
        return text
    cut = len(text) - HEAD_CHARS - TAIL_CHARS
    return (text[:HEAD_CHARS] + f"\n... [{cut} characters left out] ...\n"
            + text[-TAIL_CHARS:])


def run_command(arguments: dict) -> str:
    config = ChronoaConfig()
    allowed, why = _consent(config)
    if not allowed:
        return f"Not run: {why}"
    raw = str(arguments.get("command") or "").strip()
    if not raw:
        return "Give the command to run, e.g. `pytest -q` or `npm test`."
    try:
        argv = shlex.split(raw)
    except ValueError as exc:
        return f"Not run: the command could not be read ({exc})."
    if not argv:
        return "Give the command to run."
    if any(token in ("|", "||", "&&", ";", ">", ">>", "<", "&") for token in argv):
        return ("Not run: this runs one program, not a shell line - no pipes, "
                "redirections or `;`/`&&`. Run each step as its own command.")
    program = Path(argv[0]).name
    if program in REFUSED_PROGRAMS:
        return f"Not run: `{program}` is not something this skill runs."
    cwd, why = _workdir(str(arguments.get("cwd") or ""))
    if cwd is None:
        return f"Not run: {why}"
    where = str(arguments.get("where") or "sandbox").strip().lower()
    if where not in ("sandbox", "container", "machine"):
        return "Not run: `where` is sandbox, container or machine."
    try:
        timeout = int(arguments.get("timeout_seconds") or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    timeout = max(5, min(timeout, MAX_TIMEOUT))
    started = time.monotonic()
    network = bool(arguments.get("network"))
    if where == "container":
        image = str(arguments.get("image") or "").strip()
        if not image:
            return ("Not run: a container needs an `image` (e.g. python:3.12, "
                    "node:22, rust:1). `podman images` lists those already here.")
        from shani_chronoa.skills.container import _podman
        proc, why = _podman(
            ["run", "--rm", "-i=false", "--userns=keep-id",
             f"--network={'slirp4netns' if network else 'none'}",
             "-v", f"{cwd}:/work:Z", "-w", "/work", image, *argv],
            timeout=timeout)
        if proc is None:
            return f"Not run: {why}"
        place = f"container {image}, project at /work"
    elif where == "machine":
        name = str(arguments.get("machine") or "").strip()
        if not name:
            return ("Not run: name the `machine` - a running nspawn container or "
                    "vmspawn VM from `machinectl list`.")
        try:
            proc = subprocess.run(
                ["systemd-run", f"--machine={name}", "--pipe", "--wait", "--quiet",
                 "--collect", f"--working-directory={cwd}", "--", *argv],
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=timeout, errors="replace")
        except FileNotFoundError:
            return "Not run: systemd-run is not installed."
        except subprocess.TimeoutExpired:
            return f"Stopped after {timeout}s without finishing: `{raw}` on {name}."
        place = f"machine {name}"
    else:
        try:
            proc = subprocess.run(argv, cwd=str(cwd), stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, timeout=timeout,
                                  errors="replace")
        except FileNotFoundError:
            return f"Not run: `{argv[0]}` is not installed or not on PATH."
        except subprocess.TimeoutExpired as exc:
            partial = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            return (f"Stopped after {timeout}s without finishing: `{raw}` in {cwd}.\n"
                    + _clip(partial))
        place = f"{cwd}, sandboxed" + ("" if network else ", no network")
    took = time.monotonic() - started
    output = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    verdict = "succeeded" if proc.returncode == 0 else f"failed (exit {proc.returncode})"
    return (f"`{raw}` {verdict} in {took:.1f}s ({place}).\n"
            + (_clip(output.strip()) or "(no output)"))


SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_command",
        "description": (
            "Run one program in the project folder and get its exit code and "
            "output - the project's tests (pytest, npm test, cargo test), its "
            "build or linter - to check that a change works. One program per "
            "call: no pipes, redirections or shell syntax. Requires the "
            "'run-command-enabled' consent key; asks each time."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string",
                            "description": "The command line, e.g. 'pytest -q tests/test_x.py'."},
                "cwd": {"type": "string",
                        "description": "Folder to run in; default the project folder."},
                "timeout_seconds": {"type": "integer",
                                    "description": f"Up to {MAX_TIMEOUT}; default {DEFAULT_TIMEOUT}."},
                "where": {"type": "string", "enum": ["sandbox", "container", "machine"],
                          "description": "sandbox (default: bwrap+Landlock, project "
                                         "writable, no network), a podman container "
                                         "(needs image), or a running nspawn/vmspawn "
                                         "machine (needs machine)."},
                "image": {"type": "string", "description": "container: the image, e.g. python:3.12."},
                "machine": {"type": "string", "description": "machine: its name in machinectl list."},
                "network": {"type": "boolean",
                            "description": "Allow network access (off by default)."},
            },
            "required": ["command"],
        },
    },
}

SKILLS = [Skill(name="run_command", schema=SCHEMA, run=run_command)]
