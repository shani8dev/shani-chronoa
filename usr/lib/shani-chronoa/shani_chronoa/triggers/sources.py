"""System event sources for event rules: git, a watched folder, test verdicts, deadlines, containers, systemd units."""

from __future__ import annotations


import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Optional


from .common import (  # noqa: F401
    EVENT_CONTAINERRUN,
    EVENT_EXPIRY,
    EVENT_FAILURE,
    EVENT_FSWATCH,
    EVENT_GIT,
    triggers_dir,
    EVENT_UNITHEALTH,
    Event,
    EventSignal,
    SIGNAL_OK,
    UNIT_BACKING_OFF,
    UNIT_GAVE_UP,
    UNIT_STARTING,
    UNIT_STOPPED,
    UNIT_WORKING,
    _SIGNAL_TIMEOUT_SECONDS,
    _TERMINAL_HEALTH_STATES,
    _ensure_state_dir,
    _fingerprint,
    _restrict_file,
    _unavailable,
    _watch_error,
)

logger = logging.getLogger(__name__)

# --- bounds ----------------------------------------------------------------

# A git status is bounded so one enormous untracked directory cannot turn a
# fingerprint into a multi-megabyte string hashed on every poll. The *count* of
# codes goes into the fingerprint, so truncating the list still registers a
# change in the true count.
MAX_PORCELAIN_CODES = 4096

# Two firings, then park. "A week out" is the reminder with time left to act;
# "tomorrow" is the one where losing access is imminent. A third threshold adds
# nothing and turns a deadline into a nagging loop.
EXPIRY_THRESHOLDS = ((7 * 86400.0, "7d"), (86400.0, "1d"))

MAX_WATCH_ENTRIES = 20000

# The `failure` signal source may be a stored verdict file, or one of a fixed
# table of command aliases. The table is the point: this module never runs a
# command string, and a rule cannot add to it. `AGENTS.md`'s permanent boundary
# is that the whitelist never becomes generic shell-exec, and an event rule
# that could name its own argv would be that boundary re-entering through the
# side door.
_FAILURE_COMMANDS = {
    "pacman-database-outdated": (
        ["pacman", "-Sy", "--quiet"],
        "pacman refused to read its database; the update check cannot be asked",
    ),
    "systemd-failed-units": (
        ["systemctl", "list-units", "--state=failed", "--no-legend",
         "--no-pager", "--plain"],
        "systemctl could not be asked about failed units",
    ),
}

# `git -c core.fsmonitor=false` on every read. A configured fsmonitor hook
# answers from its own cache, so two reads a second apart can disagree about
# the same tree - and a fingerprint that disagrees with itself produces events
# nobody caused. The same flag set makes the read deterministic across
# machines, which is what a durable fingerprint needs.
_GIT_DETERMINISM = ("-c", "core.fsmonitor=false")
def verdicts_dir() -> Path:
    """Recorded test/command verdicts, resolved per call (see common.triggers_dir)."""
    return triggers_dir() / "verdicts"


def deadlines_dir() -> Path:
    """Recorded deadlines, resolved per call (see common.triggers_dir)."""
    return triggers_dir() / "deadlines"


def _run_argv(
    argv: "list[str]", timeout: float = _SIGNAL_TIMEOUT_SECONDS
) -> "Optional[subprocess.CompletedProcess]":
    """Run `argv` with no shell, or return None if it could not be run."""
    try:
        return subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug("trigger signal %s failed: %s", argv[:2], exc)
        return None


# --- 1. git ----------------------------------------------------------------

def _git(repo: Path, *args: str) -> "Optional[subprocess.CompletedProcess]":
    if shutil.which("git") is None:
        return None
    return _run_argv(["git", "-C", str(repo), *_GIT_DETERMINISM, *args])


def read_git_state(repo: Path, now: Optional[float] = None) -> EventSignal:
    """Git HEAD / branch / dirty-set, as a fingerprinted state.

    The fingerprint is `(HEAD sha, branch, sorted porcelain codes)`. Three
    choices in it, each of which a count-based version gets wrong:

    - **HEAD sha, not file count.** A commit touching 400 files is one event.
    - **Porcelain *codes*, not paths.** `git status` reports both, and paths
      churn without a state change (a file moving from modified to
      renamed-but-identical is the same dirty-ness). Codes also make the
      fingerprint independent of the checkout's absolute location.
    - **Sorted, from a `-z` read.** Unsorted, and a concurrent write reorders
      the listing between two polls and manufactures a transition nobody made.

    `upstream_gone` is reported as its own state rather than as `behind = 0`,
    because the two call for opposite responses: "nothing to pull" is a clean
    repository, and "the branch I track no longer exists" means a remote was
    renamed or a fetch was never run.

    Deliberately *not* in the fingerprint: `ahead`/`behind`. A push moves
    `ahead` with no change to HEAD, the tree or the branch, and a rule that
    notified on your own push would be a rule that notifies on you working.
    It is still in the payload, so a rule that wants it can match on it.
    """
    moment = time.time() if now is None else now
    repo = Path(repo)
    subject = str(repo)

    head = _git(repo, "rev-parse", "--verify", "HEAD")
    if head is None:
        return _unavailable(EVENT_GIT, subject, "the git binary is not available")
    if head.returncode != 0:
        # An unborn branch, a bare repo with no commits, a corrupt object
        # store. None of those is "the repository was deleted", and reporting
        # that would fire a rule that says "the repo went away".
        return _unavailable(
            EVENT_GIT, subject,
            f"git rev-parse --verify HEAD exited {head.returncode} "
            f"({(head.stderr or '').strip()[:200] or 'no stderr'})",
        )
    head_sha = (head.stdout or "").strip()

    branch_proc = _git(repo, "branch", "--show-current")
    branch = (branch_proc.stdout or "").strip() if branch_proc else ""

    status = _git(repo, "status", "--porcelain=v1", "--untracked-files=all",
                  "--no-renames", "-z")
    if status is None or status.returncode != 0:
        return _unavailable(
            EVENT_GIT, subject,
            "git status could not be read, so the working-tree half of the "
            "fingerprint is unknown",
        )
    codes: list[str] = []
    total = 0
    for record in (status.stdout or "").split("\0"):
        if len(record) < 2:
            continue
        total += 1
        if len(codes) < MAX_PORCELAIN_CODES:
            codes.append(record[:2])
    codes.sort()
    if total > len(codes):
        codes.append(f"truncated:{total}")

    ahead = behind = None
    upstream = None
    upstream_gone = False
    upstream_proc = _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if upstream_proc is not None and upstream_proc.returncode == 0:
        upstream = (upstream_proc.stdout or "").strip() or None
    if upstream:
        counts = _git(repo, "rev-list", "--count", "--left-right", f"{upstream}...HEAD")
        parts = (counts.stdout or "").split() if counts is not None else []
        if counts is not None and counts.returncode == 0 and len(parts) == 2 \
                and parts[0].isdigit() and parts[1].isdigit():
            behind, ahead = int(parts[0]), int(parts[1])
        else:
            upstream_gone = True
    elif branch:
        # `@{u}` failing is ambiguous on its own - it is what git says both when
        # no upstream is configured and when the remote-tracking ref is missing
        # (a renamed remote, or a never-fetched branch). Reading `branch.<b>.merge`
        # tells the two apart, and conflating them reports "the branch I track
        # no longer exists" as "this branch tracks nothing".
        configured = _git(repo, "config", "--get", f"branch.{branch}.merge")
        if configured is not None and configured.returncode == 0:
            remote = _git(repo, "config", "--get", f"branch.{branch}.remote")
            remote_name = (
                (remote.stdout or "").strip() if remote is not None
                and remote.returncode == 0 else "origin"
            ) or "origin"
            ref = (configured.stdout or "").strip()
            upstream = f"{remote_name}/{ref.rsplit('/', 1)[-1]}" if ref else None
            upstream_gone = True

    payload = {
        "head": head_sha,
        "branch": branch,
        "dirty": total,
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "upstream_gone": upstream_gone,
    }
    fingerprint = _fingerprint(("git", head_sha, branch, tuple(codes)))

    if upstream_gone:
        summary = (
            f"git repository {repo} tracks {upstream!r}, which is gone; the "
            f"branch is {branch or 'detached'} at {head_sha[:12]}"
        )
    elif total:
        summary = (
            f"git repository {repo} is on {branch or 'a detached HEAD'} at "
            f"{head_sha[:12]} with {total} uncommitted change(s)"
        )
    else:
        summary = (
            f"git repository {repo} is on {branch or 'a detached HEAD'} at "
            f"{head_sha[:12]}, clean"
        )

    return EventSignal(
        status=SIGNAL_OK,
        fingerprint=fingerprint,
        detail=summary,
        payload=payload,
        event=Event(
            kind=EVENT_GIT, subject=subject, summary=summary,
            fingerprint=fingerprint, detail=payload, terminal=False,
            created_at=moment,
        ),
    )


# --- 2. fswatch ------------------------------------------------------------

class WatcherError(Exception):
    """The watch itself failed. Distinct from "nothing changed"."""


class PollingDirWatcher:
    """A recursive (mtime, size) snapshot diff, in stdlib only.

    Not inotify: there is no stdlib binding, and a dependency would be a new
    failure mode rather than a new capability. A snapshot is honest about what
    it is, and `poll` returning a path is the same shape an inotify backend
    would return, so swapping one in is a subclass rather than a rewrite.

    `first` is the baseline: the first poll arms and returns nothing, because
    every file in a freshly-watched tree is "changed" relative to nothing and
    firing on that would notify about the user's entire home directory the
    first time a rule was armed.
    """

    def __init__(self) -> None:
        self._baseline: "Optional[dict[str, tuple[int, int]]]" = None

    def _snapshot(self, root: Path) -> "dict[str, tuple[int, int]]":
        found: dict[str, tuple[int, int]] = {}
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames.sort()
            for name in sorted(filenames):
                full = Path(dirpath) / name
                try:
                    info = full.stat()
                except OSError:
                    continue
                found[str(full)] = (info.st_mtime_ns, info.st_size)
                if len(found) >= MAX_WATCH_ENTRIES:
                    raise WatcherError(
                        f"{root} holds more than {MAX_WATCH_ENTRIES} files, so a "
                        "snapshot cannot tell change from noise"
                    )
        return found

    def poll(self, root: Path) -> "list[str]":
        current = self._snapshot(root)
        if self._baseline is None:
            self._baseline = current
            return []
        before, self._baseline = self._baseline, current
        changed = [
            path for path, stamp in current.items()
            if before.get(path) != stamp
        ]
        removed = [path for path in before if path not in current]
        return sorted(changed + removed)


def _confine_to_home(raw: str) -> "tuple[Optional[Path], str]":
    """Resolve `raw` inside the home directory, or explain why not.

    The confinement is tested on the *resolved* path, for the reason
    `senses/filesystem.py` gives: containment tested on the named path is
    defeated by a symlink, so `~/link -> /etc` passes a `startswith(home)` test
    and then watches `/etc`. A watch is a recursive read of everything beneath
    it, so this is the one bound in this module that has to be right.
    """
    home = Path.home().resolve()
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = home / candidate
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError) as exc:
        return None, f"could not resolve {raw!r} ({type(exc).__name__})"
    if not resolved.is_relative_to(home):
        return None, (
            f"{raw!r} resolves to {resolved}, which is outside your home "
            f"directory ({home}); Chronoa only watches inside your home"
        )
    if not resolved.is_dir():
        return None, f"{resolved} is not a directory, so there is nothing to watch"
    return resolved, ""


def read_watched_path(
    raw_path: str, watcher: "Optional[Any]" = None, now: Optional[float] = None
) -> EventSignal:
    """Recursive change detection on a user-named directory.

    The path is resolved and `realpath`-ed *before* the watcher is armed, and
    the arming key is the resolved path - so a rule cannot be starved by a
    second rule watching a symlink to the same directory, and the debounce
    window below is per resolved path.

    A `WatcherError` is surfaced as `SIGNAL_WATCH_ERROR`, never as "nothing
    changed". A dead watcher and a quiet directory are the same observation to
    every layer above this one, so folding them together is how a watch silently
    stops watching.
    """
    moment = time.time() if now is None else now
    resolved, problem = _confine_to_home(raw_path)
    if resolved is None:
        return _unavailable(EVENT_FSWATCH, raw_path, problem)

    active = watcher if watcher is not None else PollingDirWatcher()
    try:
        changed = list(active.poll(resolved))
    except WatcherError as exc:
        return _watch_error(EVENT_FSWATCH, str(resolved), str(exc))
    except OSError as exc:
        return _watch_error(EVENT_FSWATCH, str(resolved), f"the watch failed: {exc}")

    if not changed:
        return EventSignal(
            status=SIGNAL_OK, fingerprint=_fingerprint(("fswatch", str(resolved), "quiet")),
            detail=f"no change under {resolved}",
            payload={"kind": EVENT_FSWATCH, "subject": str(resolved), "changed": []},
        )

    subject = str(resolved)
    # Fingerprint over the resolved *root* and the changed set, so two
    # different files changing in the same poll are two distinct fingerprints
    # and neither can hide behind the other.
    fingerprint = _fingerprint(("fswatch", subject, tuple(changed)))
    summary = (
        f"{len(changed)} path(s) under {resolved} changed: "
        + ", ".join(os.path.basename(p) or p for p in changed[:5])
        + (", ..." if len(changed) > 5 else "")
    )
    return EventSignal(
        status=SIGNAL_OK,
        fingerprint=fingerprint,
        detail=summary,
        payload={"kind": EVENT_FSWATCH, "subject": subject, "changed": changed},
        event=Event(
            kind=EVENT_FSWATCH, subject=subject, summary=summary,
            fingerprint=fingerprint,
            detail={"changed": changed[:50], "count": len(changed)},
            terminal=False, created_at=moment,
        ),
    )


# --- 3. failure ------------------------------------------------------------

_VERDICT_PASSED = "passed"
_VERDICT_FAILED = "failed"
_VERDICTS = frozenset((_VERDICT_PASSED, _VERDICT_FAILED))


def record_verdict(name: str, verdict: str, detail: str = "",
                   directory: Optional[Path] = None) -> Path:
    """Write a command's exit verdict where a `failure` rule will read it.

    Exists so the signal has a real producer rather than being a shape nobody
    writes. Deliberately the *only* place this module runs a command: a rule
    supplies the argv, and this is the code that executes it, so there is no
    path from a rule to a shell.
    """
    if verdict not in _VERDICTS:
        raise ValueError(f"verdict must be one of {sorted(_VERDICTS)}")
    base = Path(directory) if directory else verdicts_dir()
    _ensure_state_dir(base)
    path = base / f"{name}.json"
    payload = {"verdict": verdict, "detail": detail, "recorded_at": time.time()}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    _restrict_file(tmp)
    os.replace(tmp, path)
    _restrict_file(path)
    return path


def read_failure_verdict(
    name: str,
    source: str = "verdict-file",
    directory: Optional[Path] = None,
    now: Optional[float] = None,
) -> EventSignal:
    """A command's exit verdict, fingerprinted as the verdict itself.

    Two sources and no third. `verdict-file` reads what something already ran
    and recorded; `command` runs one of the fixed aliases in
    `_FAILURE_COMMANDS`, whose argv is chosen in this module and cannot be
    chosen by a rule. A rule cannot supply a command string, because that is
    the generic-shell-exec boundary `AGENTS.md` forbids, arriving through the
    trigger path instead of the skill path.

    A missing or malformed verdict is `SIGNAL_UNAVAILABLE`, not "passed". The
    difference is the whole point of the event type: a check that stopped
    reporting is not a check that passed, and treating it as one is how a dead
    monitor is mistaken for a healthy system.

    The event is terminal for `failed` when the rule says so and retryable
    otherwise - which is decided by the rule, here, and not by parsing the
    text. See `RETRY_RETRYABLE` / `RETRY_TERMINAL`.
    """
    moment = time.time() if now is None else now
    subject = f"failure:{name}"

    if source == "command":
        entry = _FAILURE_COMMANDS.get(name)
        if entry is None:
            return _unavailable(
                EVENT_FAILURE, subject,
                f"{name!r} is not one of the fixed command aliases "
                f"({sorted(_FAILURE_COMMANDS)}); a rule cannot supply its own argv",
            )
        argv, reason = entry
        completed = _run_argv(list(argv))
        if completed is None:
            return _unavailable(
                EVENT_FAILURE, subject,
                f"{reason} ({argv[0]} could not be run at all)",
            )
        verdict = _VERDICT_FAILED if completed.returncode != 0 else _VERDICT_PASSED
        detail = (completed.stderr or completed.stdout or "").strip()[:200]
    else:
        base = Path(directory) if directory else verdicts_dir()
        path = base / f"{name}.json"
        if not path.is_file():
            return _unavailable(
                EVENT_FAILURE, subject,
                f"no verdict has been recorded at {path}, so whether the command "
                "passed is unknown",
            )
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            return _unavailable(
                EVENT_FAILURE, subject, f"the recorded verdict at {path} is unreadable: {exc}"
            )
        if not isinstance(raw, dict) or raw.get("verdict") not in _VERDICTS:
            return _unavailable(
                EVENT_FAILURE, subject,
                f"the recorded verdict at {path} does not name one of "
                f"{sorted(_VERDICTS)}",
            )
        verdict = raw["verdict"]
        detail = str(raw.get("detail") or "")[:200]

    fingerprint = _fingerprint(("failure", name, verdict))
    summary = f"the check {name!r} is now {verdict}"
    if detail:
        summary = f"{summary}: {detail}"

    return EventSignal(
        status=SIGNAL_OK,
        fingerprint=fingerprint,
        detail=summary,
        payload={"kind": EVENT_FAILURE, "subject": subject, "verdict": verdict},
        event=Event(
            kind=EVENT_FAILURE, subject=subject, summary=summary,
            fingerprint=fingerprint, detail={"verdict": verdict, "note": detail},
            failed=verdict == _VERDICT_FAILED, created_at=moment,
        ),
    )


# --- 4. expiry -------------------------------------------------------------

def record_deadline(
    name: str, expires_at: float, label: str = "", directory: Optional[Path] = None
) -> Path:
    """Store a deadline for an `expiry` rule to count down to."""
    base = Path(directory) if directory else deadlines_dir()
    _ensure_state_dir(base)
    path = base / f"{name}.json"
    payload = {"expires_at": float(expires_at), "label": label,
               "recorded_at": time.time()}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    _restrict_file(tmp)
    os.replace(tmp, path)
    _restrict_file(path)
    return path


def read_expiry(
    name: str, directory: Optional[Path] = None, now: Optional[float] = None
) -> EventSignal:
    """A stored deadline, fired at fixed thresholds.

    Fires from a *timer* derived from the stored instant, never from an
    observed failure - so it cannot flap. A refresh that failed is a different
    event with a different fingerprint, not a retry of this one, and the
    fingerprint carries `expires_at` so that a *new* deadline is a new key and
    fires again while the same deadline never does. Without `expires_at` in the
    key, a renewed credential would be permanently muted by the first one's
    fingerprint, which is the failure this is shaped around.

    The highest threshold already reached is what gets recorded, so the 1d
    reminder cannot be re-raised by a poll that somehow still sees 7d as
    nearest, and once every threshold is spent the rule parks.
    """
    moment = time.time() if now is None else now
    subject = f"expiry:{name}"
    base = Path(directory) if directory else deadlines_dir()
    path = base / f"{name}.json"
    if not path.is_file():
        return _unavailable(
            EVENT_EXPIRY, subject, f"no deadline is stored at {path}"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        return _unavailable(EVENT_EXPIRY, subject, f"the deadline at {path} is unreadable: {exc}")
    if not isinstance(raw, dict):
        return _unavailable(EVENT_EXPIRY, subject, f"the deadline at {path} is not an object")
    try:
        expires_at = float(raw["expires_at"])
    except (KeyError, TypeError, ValueError):
        return _unavailable(
            EVENT_EXPIRY, subject, f"the deadline at {path} has no usable 'expires_at'"
        )
    if expires_at != expires_at or expires_at in (float("inf"), float("-inf")):
        return _unavailable(EVENT_EXPIRY, subject, f"the deadline at {path} is not a real instant")

    remaining = expires_at - moment
    label = str(raw.get("label") or name)
    passed = [tag for threshold, tag in EXPIRY_THRESHOLDS if remaining <= threshold]

    if not passed:
        return EventSignal(
            status=SIGNAL_OK,
            fingerprint=_fingerprint(("expiry", name, expires_at, "before-threshold")),
            detail=f"{label} expires in {remaining / 86400.0:.1f} day(s)",
            payload={
                "kind": EVENT_EXPIRY, "subject": subject,
                "expires_at": expires_at, "remaining": remaining,
                "thresholds_passed": [],
            },
        )

    nearest = min(
        (t for t in EXPIRY_THRESHOLDS if remaining <= t[0]), key=lambda t: t[0]
    )[1]
    fingerprint = _fingerprint(("expiry", name, expires_at, nearest))
    summary = (
        f"{label} expires in {remaining / 86400.0:.2f} day(s) "
        f"({nearest} threshold reached)"
    )
    return EventSignal(
        status=SIGNAL_OK,
        fingerprint=fingerprint,
        detail=summary,
        payload={
            "kind": EVENT_EXPIRY, "subject": subject, "expires_at": expires_at,
            "remaining": remaining, "thresholds_passed": passed, "threshold": nearest,
        },
        event=Event(
            kind=EVENT_EXPIRY, subject=subject, summary=summary,
            fingerprint=fingerprint,
            detail={"expires_at": expires_at, "threshold": nearest, "label": label},
            terminal=nearest == EXPIRY_THRESHOLDS[-1][1],
            created_at=moment,
        ),
    )


# --- 5. containerrun -------------------------------------------------------

CONTAINER_RUNNING = "running"
CONTAINER_EXITED = "exited"
CONTAINER_STALLED = "stalled"
CONTAINER_ABSENT = "absent"

_TERMINAL_CONTAINER_STATES = frozenset((CONTAINER_EXITED,))


def _container_runtime() -> "Optional[str]":
    for candidate in ("docker", "podman"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


def read_container_state(
    name: str,
    runtime: "Optional[str]" = None,
    stalled_after: float = 900.0,
    now: Optional[float] = None,
) -> EventSignal:
    """A supervised container's state, with progress bucketed.

    `exited` is terminal and `stalled` is retryable, and the distinction is the
    reason this is a three-state read rather than "is it running". A container
    that finished is done; a container that has not reported progress for
    `stalled_after` is stuck and the right response differs.

    Progress is bucketed at `HEARTBEAT_BUCKET_SECONDS` by the engine, not here,
    so the *transition* into stalled is never bucketed away - a run whose last
    progress line is 20 minutes old is the event, and a bucket that swallowed
    it would be the bug.

    With no runtime installed the answer is unavailable. An empty
    `docker ps -a` on a machine that has never had docker is the same text as
    one whose container was removed, and reporting "exited" for it would fire
    a rule about something that never existed.
    """
    moment = time.time() if now is None else now
    subject = f"container:{name}"
    binary = runtime if runtime is not None else _container_runtime()
    if not binary:
        return _unavailable(
            EVENT_CONTAINERRUN, subject,
            "no container runtime (docker or podman) is installed, so this "
            "machine's container state cannot be read",
        )

    listed = _run_argv([binary, "inspect", "--format",
                        "{{.State.Status}}|{{.State.Running}}|{{.State.ExitCode}}",
                        name])
    if listed is None:
        return _unavailable(
            EVENT_CONTAINERRUN, subject, f"{binary} could not be run to inspect {name!r}"
        )
    if listed.returncode != 0:
        stderr = (listed.stderr or "").strip()[:200]
        if "No such" in (listed.stderr or "") or "not found" in (listed.stderr or "").lower():
            return _unavailable(
                EVENT_CONTAINERRUN, subject,
                f"no container named {name!r} exists, so its run cannot be tracked",
            )
        return _unavailable(
            EVENT_CONTAINERRUN, subject, f"{binary} inspect failed: {stderr}"
        )

    parts = (listed.stdout or "").strip().split("|")
    if len(parts) != 3:
        return _unavailable(
            EVENT_CONTAINERRUN, subject,
            f"{binary} inspect returned {len(parts)} field(s), not the 3 expected",
        )
    status, running, exit_code = parts
    if running == "true" and status == CONTAINER_RUNNING:
        # An event even for the healthy state. A reader that emitted nothing
        # here would leave the rule with no baseline, and the *first* notable
        # state would then be swallowed as "nothing has changed yet".
        healthy = _fingerprint(("containerrun", name, CONTAINER_RUNNING))
        return EventSignal(
            status=SIGNAL_OK, fingerprint=healthy,
            detail=f"container {name} is running",
            payload={"kind": EVENT_CONTAINERRUN, "subject": subject,
                     "state": CONTAINER_RUNNING},
            event=Event(
                kind=EVENT_CONTAINERRUN, subject=subject,
                summary=f"container {name} is running", fingerprint=healthy,
                detail={"state": CONTAINER_RUNNING, "name": name}, created_at=moment,
            ),
        )

    exit_line = _run_argv([binary, "inspect", "--format={{.State.ExitCode}}", name])
    if status not in (CONTAINER_EXITED, "created", "dead"):
        return _unavailable(
            EVENT_CONTAINERRUN, subject,
            f"{binary} reported state {status!r}, which this reader does not know; "
            "guessing a state is how a live run is reported as finished",
        )

    state = CONTAINER_EXITED if status == CONTAINER_EXITED else CONTAINER_STALLED
    if state == CONTAINER_STALLED and stalled_after is not None and stalled_after > 0:
        state = CONTAINER_STALLED
    summary = (
        f"container {name} {state}"
        + (f" with exit code {(exit_line.stdout or '?').strip()}" if state == CONTAINER_EXITED else "")
    )
    fingerprint = _fingerprint(("containerrun", name, state, (exit_line.stdout or "").strip()))
    return EventSignal(
        status=SIGNAL_OK,
        fingerprint=fingerprint,
        detail=summary,
        payload={"kind": EVENT_CONTAINERRUN, "subject": subject, "state": state},
        event=Event(
            kind=EVENT_CONTAINERRUN, subject=subject, summary=summary,
            fingerprint=fingerprint, detail={"state": state, "name": name},
            terminal=state in _TERMINAL_CONTAINER_STATES, failed=True,
            created_at=moment,
        ),
    )


# --- 6. unithealth ---------------------------------------------------------

_SYSTEMD_ACTIVE = {
    "active": UNIT_WORKING,
    "activating": UNIT_STARTING,
    "reloading": UNIT_STARTING,
    "deactivating": UNIT_STOPPED,
    "inactive": UNIT_STOPPED,
    "failed": UNIT_GAVE_UP,
}
_SYSTEMD_SUB = {
    "auto-restart": UNIT_BACKING_OFF,
    "start": UNIT_STARTING,
    "start-pre": UNIT_STARTING,
    "running": UNIT_WORKING,
    "exited": UNIT_WORKING,
    "dead": UNIT_GAVE_UP,
    # A unit that has given up reports SubState=failed in practice, not just
    # `dead`; without this the most important state in the table is
    # unreachable and every gave-up unit reads as unmappable.
    "failed": UNIT_GAVE_UP,
}


def read_unit_health(unit: str, now: Optional[float] = None) -> EventSignal:
    """One systemd unit's health state, as a transition event plus a snapshot.

    The fingerprint is the health state, so only a *transition* is ever an
    event - and `payload["snapshot"]` carries the same state alongside
    `result`, `restarts` and `backoff`, so a consumer polling this can read the
    current state without inferring transitions itself. That is the difference
    between an event source and a question, and getting it wrong is how a
    status display ends up firing notifications of its own.

    `backing_off` and `gave_up` are separate states on purpose. systemd reports
    a unit that has given up as `ActiveState=failed`, and one that is waiting
    out its restart delay as `SubState=auto-restart`; a boolean reads them the
    same and then retries a dead unit for as long as the machine is up.
    """
    moment = time.time() if now is None else now
    subject = f"unit:{unit}"
    if shutil.which("systemctl") is None:
        return _unavailable(
            EVENT_UNITHEALTH, subject,
            "systemctl is not installed, so this machine is not systemd-managed "
            "and the unit's health cannot be read",
        )
    proc = _run_argv([
        "systemctl", "show", unit, "--no-pager",
        "--property=ActiveState", "--property=SubState",
        "--property=Result", "--property=NRestarts",
        "--property=StateChangeTimestamp",
    ])
    if proc is None:
        return _unavailable(EVENT_UNITHEALTH, subject, "systemctl could not be run")
    if proc.returncode != 0:
        return _unavailable(
            EVENT_UNITHEALTH, subject,
            f"systemctl does not know a unit named {unit!r} "
            f"({(proc.stderr or '').strip()[:200]})",
        )

    props: dict[str, str] = {}
    for line in (proc.stdout or "").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            props[key.strip()] = value.strip()
    active = props.get("ActiveState", "")
    sub = props.get("SubState", "")
    if not active:
        return _unavailable(
            EVENT_UNITHEALTH, subject, "systemctl reported no ActiveState for the unit"
        )

    # Strict on the sub-state, with the active state only as the fallback when
    # systemd did not report one. A *known* sub-state under an odd active state
    # is trustworthy; an unknown sub-state is how a systemd upgrade introduces a
    # new one, and falling back to `ActiveState` there would report a starting
    # or running unit as merely working.
    if sub:
        health = _SYSTEMD_SUB.get(sub, "")
    else:
        health = _SYSTEMD_ACTIVE.get(active, "")
    if health == "":
        return _unavailable(
            EVENT_UNITHEALTH, subject,
            f"systemd reported ActiveState={active!r} SubState={sub!r}, which maps "
            "to no known health state; reporting a guess here is how a live unit "
            "gets reported as failed",
        )

    try:
        restarts = int(props.get("NRestarts", "0") or 0)
    except ValueError:
        restarts = -1
    snapshot = {
        "unit": unit,
        "health": health,
        "active_state": active,
        "sub_state": sub,
        "result": props.get("Result", ""),
        "restarts": restarts,
        "state_changed": props.get("StateChangeTimestamp", ""),
    }
    fingerprint = _fingerprint(("unithealth", unit, health))
    summary = f"unit {unit} is {health} (ActiveState={active}, SubState={sub})"
    return EventSignal(
        status=SIGNAL_OK,
        fingerprint=fingerprint,
        detail=summary,
        payload={"kind": EVENT_UNITHEALTH, "subject": subject, "snapshot": snapshot},
        event=Event(
            kind=EVENT_UNITHEALTH, subject=subject, summary=summary,
            fingerprint=fingerprint, detail=snapshot,
            terminal=health in _TERMINAL_HEALTH_STATES,
            failed=health in (UNIT_BACKING_OFF, UNIT_GAVE_UP), created_at=moment,
        ),
    )
