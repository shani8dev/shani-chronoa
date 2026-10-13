"""Tool call tracking module for shani-chronoa agents."""

import json
import logging
import os
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from shani_chronoa import files

logger = logging.getLogger(__name__)

# Chronoa runs as a normal desktop user, never root - /var/log is not
# writable by that user (confirmed live: ToolTracker() with the default
# raised PermissionError on a real, unprivileged install). Use the same
# per-user XDG data location the sandbox executor already uses for its
# own state (~/.local/share/shani-chronoa/...).
#
# **Resolved on every use, never at import.** These were module constants and
# `tools._TRACKER` is built at import - during test collection, before a test
# redirects `XDG_DATA_HOME` - so every test that dispatched a tool appended to
# the real user's log. Measured on the development machine: the 18,000-line log
# the outcome model trains on carried 374 `liar`, 378 `unver` and 369
# `add_reminder` fixture calls, and running four test files added two more.
def log_dir() -> Path:
    return files.data_home() / "shani-chronoa" / "logs"


#: The active log is rotated once it would grow past this. **Generous on
#: purpose.** Measured on this machine: 990 bytes per record and 764 records a
#: day mean, so 16 MiB holds about 16,900 calls - some three weeks of ordinary
#: use. The hot reader (`learning._durable_records`) tails this file, so the
#: cap is also what keeps that read bounded.
ACTIVE_MAX_BYTES = 16 * 1024 * 1024

#: How many rotated chunks to keep. Eight of them plus the active file is about
#: 144 MiB and 145,000 calls - roughly six months - against a log that was
#: measured growing at 276 MB a year with nothing pruning it and a `log_files`
#: skill whose own description reads "still growing, and unrotated".
ARCHIVE_KEEP = 8

#: The rotated chunks, oldest first: `.8` is the oldest kept, `.1` the newest,
#: and the active log is newer than all of them. **Derived from the active log's
#: own path**, so a caller that points the log somewhere else gets its archives
#: beside it under its own name - `rotate()` and `log_files()` used to disagree
#: about the filename, and a test with a log called `calls.jsonl` is what said so.
def archive_path(active: Path, n: int) -> Path:
    active = Path(active)
    return active.parent / ("%s.%d" % (active.name, n))


def log_files(active: Optional[Path] = None) -> list:
    """Every kept log, **oldest first**, so a plain concatenation is in time
    order. Empty when nothing has been written yet.

    **Takes the active log's path, not a directory and not a filename.** My
    first version took a directory and globbed `tool_calls.log*` inside it,
    which silently ignored any caller that had pointed the log somewhere else -
    and `learning._log_path()` is redirected by tests that name a *different
    file*, so that caller's log was read as empty and a real test failed with
    "no lessons yet". The path is what the caller knows; the archives are
    derived from it, so no name is assumed anywhere.

    **Readers must go through this rather than naming the file.** Rotation moves
    the oldest records out of the active log and into `<name>.N`, and a reader
    that still opened the active file alone would silently lose history - the
    outcome model, the calibrations and the bandit all train on this, and none
    of them would report that it had stopped seeing records. That is the
    absence-shaped wrong answer this repository keeps meeting, and a rotation
    that made it invisible would be worse than no rotation.
    """
    active = Path(active) if active is not None else log_dir() / "tool_calls.log"
    d = active.parent
    out = []
    for n in range(ARCHIVE_KEEP, 0, -1):
        candidate = d / ("%s.%d" % (active.name, n))
        if candidate.exists():
            out.append(candidate)
    if active.exists():
        out.append(active)
    return out


def rotate(active: Optional[Path] = None) -> list:
    """Shift the active log into an archive and drop what falls off the end.

    **Returns the chunks it moved, oldest first**, so a caller can report what
    happened without re-reading the directory - and so a rotation that moved
    nothing says so rather than looking like one that did.

    Held under an exclusive `flock` for the whole shift: two processes each
    deciding to rotate at once would otherwise both rename and lose a chunk.
    The lock is `O_CREAT` and lives beside the log; the kernel releases it when
    the holder exits, so a dead holder cannot wedge the log.
    """
    active = Path(active) if active is not None else log_dir() / "tool_calls.log"
    if not active.exists():
        return []
    if active.is_dir():
        # **A directory here means the caller handed over a log *directory*.**
        # The rename would then move the whole directory to `<dir>.1` -
        # silently, with no error raised and no record kept. I made exactly
        # that mistake while writing these tests, and the shipped tree has one
        # call site that gets it right, which is not the same as the next caller
        # being unable to get it wrong.
        logger.warning("refusing to rotate %s: that is a directory, not a log",
                       active)
        return []
    moved = []
    try:
        fd = os.open(archive_path(active, 0).with_name(active.name + ".rotate.lock"),
                     os.O_WRONLY | os.O_CREAT, 0o600)
    except OSError as exc:
        logger.warning("could not lock for rotation: %s", exc)
        return []
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX)
        # Oldest chunk falls off the end; everything else shifts up one.
        try:
            os.unlink(archive_path(active, ARCHIVE_KEEP))
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("could not drop the oldest archive: %s", exc)
        for n in range(ARCHIVE_KEEP - 1, 0, -1):
            src = archive_path(active, n)
            if src.exists():
                os.replace(src, archive_path(active, n + 1))
        os.replace(active, archive_path(active, 1))
        files.restrict_file(archive_path(active, 1))
        for n in range(ARCHIVE_KEEP, 0, -1):
            moved.append(archive_path(active, n))
        moved.reverse()
    except OSError as exc:
        logger.warning("could not rotate the call log: %s", exc)
        return []
    finally:
        os.close(fd)
    logger.info("Rotated the call log; keeping %d chunk(s)", ARCHIVE_KEEP)
    return moved


def __getattr__(name: str) -> Path:
    # `tool_tracking.LOG_DIR` / `.LOG_FILE` keep working for readers, resolved now.
    if name == "LOG_DIR":
        return log_dir()
    if name == "LOG_FILE":
        return log_dir() / "tool_calls.log"
    raise AttributeError(name)


# Who asked for this call. The audit trail's whole point for the trigger
# engine is being able to tell a user-initiated actuation from an
# unattended one: an armed rule that fires `notify-send` unprompted is the
# behaviour most likely to erode trust, and "it was logged" is not the same
# as "it was logged AS UNATTENDED". One field, on the existing record -
# deliberately NOT a second log file (see AGENTS.md).
ORIGIN_USER = "user"
ORIGIN_UNATTENDED = "unattended"
# A rule fired unattended, a person tapped "Allow once" on its notification, and
# only then did it run: neither typed by a person nor done without one.
ORIGIN_APPROVED = "approved"

# One tool call can now legitimately carry a multi-megabyte argument: a binary
# payload reaches a skill by reference (see argfile.py), and the reference is
# recorded here alongside everything else. This log is append-only and
# unbounded, so a single call must not be able to write a megabyte per
# invocation. Values past the cap are replaced in the log only - the exact
# value is still in the in-memory ring, which is the thing
# `get_calls()` exists for.
MAX_LOGGED_ARGS_CHARS = 4096


def _loggable(value: Any) -> Any:
    """`value` itself if it is small and JSON-serializable, else a stand-in.

    `default=repr` alone would not be enough: `repr()` of a megabyte of bytes
    is a three-megabyte string, which defeats the point of being able to log
    the call at all. Confirmed live that neither is hypothetical - a
    `TypeError: Object of type bytes is not JSON serializable` raised out of
    `_write_to_log` propagated into `tools.py:execute_tool()`, whose `except`
    turned a skill that had already run successfully into
    "Tool 'x' failed: ...".
    """
    try:
        encoded = json.dumps(value)
    except (TypeError, ValueError):
        return f"<{type(value).__name__}, {len(repr(value))} chars, not JSON-serializable>"
    if len(encoded) > MAX_LOGGED_ARGS_CHARS:
        return f"<{type(value).__name__}, {len(encoded)} chars of JSON, omitted from the log>"
    return value


class ToolCallRecord:
    """Represents a single tool call record."""

    def __init__(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: Any,
        duration_ms: float,
        timestamp: Optional[datetime] = None,
        origin: str = ORIGIN_USER,
        verdict: Optional[str] = None,
        evidence: str = "",
    ):
        self.tool_name = tool_name
        self.args = args
        self.result = result
        self.duration_ms = duration_ms
        self.timestamp = timestamp or datetime.now(timezone.utc)
        self.origin = origin
        # "verified" / "failed" / "unverified", or None when the call never
        # reached verification at all (non-zero exit, or an exception). The
        # result string says this in prose too, but an audit trail has to be
        # sortable, and "which of my actions actually took effect" is the
        # first question asked of one.
        self.verdict = verdict
        # What verification actually observed. `result` holds the skill's own
        # account, which for a failed action is the confident claim that it
        # worked; without the evidence here the log reads "All done
        # successfully." next to verdict=failed and never says why.
        self.evidence = evidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "tool_name": self.tool_name,
            "args": self.args,
            "result": self.result,
            "duration_ms": self.duration_ms,
            "origin": self.origin,
            "verdict": self.verdict,
            "evidence": self.evidence,
        }

    def to_log_dict(self) -> dict[str, Any]:
        """`to_dict()` with every argument value made safe to serialize."""
        return {**self.to_dict(), "args": {k: _loggable(v) for k, v in self.args.items()}}


class ToolTracker:
    """Tracks tool calls made by shani-chronoa agents."""

    def __init__(self, log_dir: Optional[Path] = None, max_in_memory: int = 100):
        #: An explicit directory is fixed; None follows `XDG_DATA_HOME` per write.
        self._log_dir = log_dir
        # In-memory ring buffer for conversation-context use (last N calls);
        # the on-disk log below is append-only and unbounded, this is not.
        self._calls: deque[ToolCallRecord] = deque(maxlen=max_in_memory)
        self._ensure_log_dir()

    @property
    def log_dir(self) -> Path:
        return self._log_dir if self._log_dir is not None else log_dir()

    @property
    def log_file(self) -> Path:
        return self.log_dir / "tool_calls.log"

    def _ensure_log_dir(self) -> None:
        """Ensure the log directory exists, and is not readable by anyone else.

        The highest-traffic state surface in the package: every tool name,
        argument and result lands here, and the file grows unbounded (13.5 MB on
        the machine this was measured on). A `mkdir` with no mode landed the
        directory at 0775 under umask 002, which leaves the log's own 0600 as the
        only thing standing between another local account and the transcript.
        """
        files.ensure_private_dir(self.log_dir)

    def record_call(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: Any,
        duration_ms: float,
        origin: str = ORIGIN_USER,
        verdict: Optional[str] = None,
        evidence: str = "",
    ) -> ToolCallRecord:
        """Record a tool call.

        `origin` distinguishes who asked for the call. Every caller in the
        shipped tree defaults to `ORIGIN_USER`; only the trigger engine
        passes `ORIGIN_UNATTENDED`, which is the whole point of the field.

        `verdict` is verification's conclusion, passed by callers that have
        one. It defaults to None because a call that died before verification
        has no verdict, and inventing one would be worse than recording none.
        """
        record = ToolCallRecord(
            tool_name, args, result, duration_ms, origin=origin,
            verdict=verdict, evidence=evidence,
        )
        self._calls.append(record)
        self._write_to_log(record)
        logger.info("Recorded call to %s (%.2fms)", tool_name, duration_ms)
        return record

    def get_calls(self) -> list[ToolCallRecord]:
        """Get all recorded calls."""
        return list(self._calls)

    def get_calls_by_tool(self, tool_name: str) -> list[ToolCallRecord]:
        """Get calls filtered by tool name."""
        return [c for c in self._calls if c.tool_name == tool_name]

    def export_csv(self, output_path: Path) -> None:
        """Export all calls to a CSV file."""
        import csv
        with open(output_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                ["timestamp", "tool_name", "args", "result", "duration_ms",
                 "origin", "verdict", "evidence"]
            )
            for record in self._calls:
                writer.writerow([
                    record.timestamp.isoformat(),
                    record.tool_name,
                    json.dumps(record.to_log_dict()["args"], default=repr),
                    json.dumps(record.result, default=repr),
                    record.duration_ms,
                    record.origin,
                    record.verdict or "",
                    record.evidence,
                ])
        logger.info("Exported %d calls to %s", len(self._calls), output_path)

    def _write_to_log(self, record: ToolCallRecord) -> None:
        """Append a call record to the log file.

        Created with its mode rather than chmod'd after the write, so there is no
        window in which the log is group- and world-readable - `egress.py` fixed
        the same shape for the same reason. The trailing chmod is kept anyway: it
        is what tightens a file that already existed at a looser mode.
        """
        self._ensure_log_dir()
        line = json.dumps(record.to_log_dict(), default=repr) + "\n"
        # **Rotate before appending, never after.** A writer that appended and
        # then rotated would lose the record it had just written; rotating first
        # means the new record always lands in the newest chunk. And a writer
        # that already holds the old inode when another process renames it
        # appends into the renamed file - which is the archive, not a hole -
        # because the open was O_APPEND against a file, not a name.
        try:
            if self.log_file.stat().st_size + len(line) > ACTIVE_MAX_BYTES:
                rotate(self.log_file)
        except OSError:
            pass
        fd = os.open(self.log_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(line)
        files.restrict_file(self.log_file)
