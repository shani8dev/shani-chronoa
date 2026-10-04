"""Run a short command for its output, never raising: the one implementation skills and senses share.

`None` means it could not run at all (missing, timed out, not executable);
otherwise the completed process, whatever its exit code - callers decide what
a non-zero exit means. No shell is ever involved: `argv` is a list.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Sequence

logger = logging.getLogger(__name__)


def run(argv: Sequence[str], timeout: float = 20, env: Optional[dict] = None,
        input: Optional[str] = None) -> "subprocess.CompletedProcess | None":
    if not argv or shutil.which(argv[0]) is None and not Path(argv[0]).is_absolute():
        return None
    try:
        return subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout, check=False,
                              env=env, input=input)
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("%s failed: %s", argv[0], exc)
        return None


def cmdline(pid) -> Optional[str]:
    """A process's command line with NULs as spaces; "" for a kernel thread; None when it is gone or unreadable."""
    try:
        raw = (Path("/proc") / str(pid) / "cmdline").read_bytes()
    except (OSError, ValueError):
        return None
    return raw.replace(b"\x00", b" ").strip().decode("utf-8", "replace")
