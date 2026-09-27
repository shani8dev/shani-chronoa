"""Tool call tracking module for shani-chronoa agents."""

import json
import logging
import os
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Chronoa runs as a normal desktop user, never root - /var/log is not
# writable by that user (confirmed live: ToolTracker() with the default
# raised PermissionError on a real, unprivileged install). Use the same
# per-user XDG data location the sandbox executor already uses for its
# own state (~/.local/share/shani-chronoa/...).
LOG_DIR = Path(os.path.expanduser("~/.local/share/shani-chronoa/logs"))
LOG_FILE = LOG_DIR / "tool_calls.log"

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
    ):
        self.tool_name = tool_name
        self.args = args
        self.result = result
        self.duration_ms = duration_ms
        self.timestamp = timestamp or datetime.now(timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "tool_name": self.tool_name,
            "args": self.args,
            "result": self.result,
            "duration_ms": self.duration_ms,
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
        """Ensure the log directory exists."""
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def record_call(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: Any,
        duration_ms: float,
    ) -> ToolCallRecord:
        """Record a tool call."""
        record = ToolCallRecord(tool_name, args, result, duration_ms)
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
            writer.writerow(["timestamp", "tool_name", "args", "result", "duration_ms"])
            for record in self._calls:
                writer.writerow([
                    record.timestamp.isoformat(),
                    record.tool_name,
                    json.dumps(record.to_log_dict()["args"], default=repr),
                    json.dumps(record.result, default=repr),
                    record.duration_ms,
                ])
        logger.info("Exported %d calls to %s", len(self._calls), output_path)

    def _write_to_log(self, record: ToolCallRecord) -> None:
        """Append a call record to the log file."""
        with open(self.log_file, "a") as f:
            f.write(json.dumps(record.to_log_dict(), default=repr) + "\n")
