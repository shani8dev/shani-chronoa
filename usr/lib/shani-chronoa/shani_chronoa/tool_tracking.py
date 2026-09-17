"""Tool call tracking module for shani-chronoa agents."""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

LOG_DIR = Path("/var/log/shani-chronoa")
LOG_FILE = LOG_DIR / "tool_calls.log"


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


class ToolTracker:
    """Tracks tool calls made by shani-chronoa agents."""

    def __init__(self, log_dir: Path = LOG_DIR):
        self.log_dir = log_dir
        self.log_file = log_dir / "tool_calls.log"
        self._calls: list[ToolCallRecord] = []
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
            for call in self._calls:
                writer.writerow([
                    call.timestamp.isoformat(),
                    call.tool_name,
                    json.dumps(call.args),
                    json.dumps(call.result),
                    call.duration_ms,
                ])
        logger.info("Exported %d calls to %s", len(self._calls), output_path)

    def _write_to_log(self, record: ToolCallRecord) -> None:
        """Append a call record to the log file."""
        with open(self.log_file, "a") as f:
            f.write(json.dumps(record.to_dict()) + "\n")
