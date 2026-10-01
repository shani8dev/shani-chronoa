"""Tool call tracking module for shani-chronoa agents."""

import json
import logging
import os
import time
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
LOG_DIR = Path(os.path.expanduser("~/.local/share/shani-chronoa/logs"))
LOG_FILE = LOG_DIR / "tool_calls.log"

# Who asked for this call. The audit trail's whole point for the trigger
# engine is being able to tell a user-initiated actuation from an
# unattended one: an armed rule that fires `notify-send` unprompted is the
# behaviour most likely to erode trust, and "it was logged" is not the same
# as "it was logged AS UNATTENDED". One field, on the existing record -
# deliberately NOT a second log file (see AGENTS.md).
ORIGIN_USER = "user"
ORIGIN_UNATTENDED = "unattended"

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

    def __init__(self, log_dir: Path = LOG_DIR, max_in_memory: int = 100):
        self.log_dir = log_dir
        self.log_file = log_dir / "tool_calls.log"
        # In-memory ring buffer for conversation-context use (last N calls);
        # the on-disk log below is append-only and unbounded, this is not.
        self._calls: deque[ToolCallRecord] = deque(maxlen=max_in_memory)
        self._ensure_log_dir()

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
        fd = os.open(self.log_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(json.dumps(record.to_log_dict(), default=repr) + "\n")
        files.restrict_file(self.log_file)
