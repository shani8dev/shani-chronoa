"""Skill: what could be cleaned up to free disk space - reported, never deleted.

The trash, the user's cache directory, the system journal's size and
Flatpak runtimes no app uses any more, each with its size and the safe way
to free it (empty_trash for the trash). Reading only; nothing is removed.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import Skill

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "cleanup_report",
        "description": "Report what could be cleaned up to free disk space - trash, caches, the system "
                       "log, unused Flatpak runtimes - with sizes. Reads only; deletes nothing.",
        "parameters": {"type": "object", "properties": {}},
    },
}


def _size(path: Path) -> int:
    total = 0
    for root, _dirs, names in os.walk(path, onerror=lambda e: None):
        for n in names:
            try:
                total += os.lstat(os.path.join(root, n)).st_size
            except OSError:
                pass
    return total


def _h(n: int) -> str:
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


def _run(_arguments: dict) -> str:
    out = []
    trash = files.data_home() / "Trash"
    if trash.exists():
        out.append(f"Trash: {_h(_size(trash))} (empty_trash frees it).")
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    if cache.exists():
        out.append(f"Your cache folder ({cache}): {_h(_size(cache))} - apps rebuild what they need, "
                   "but clear it only with them closed.")
    if shutil.which("journalctl"):
        r = subprocess.run(["journalctl", "--disk-usage"], capture_output=True, text=True, timeout=15)
        m = re.search(r"take up ([\d.]+\s*\S+)", r.stdout)
        if m:
            out.append(f"System log: {m.group(1)} (trimmed by 'journalctl --vacuum-time=2weeks', as admin).")
    if shutil.which("flatpak"):
        r = subprocess.run(["flatpak", "uninstall", "--unused", "--assumeno"], capture_output=True, text=True,
                           timeout=60, input="n\n")
        unused = [l.split()[1] for l in r.stdout.splitlines() if re.match(r"\s*\d+\.\s", l) and len(l.split()) > 1]
        out.append((f"{len(unused)} Flatpak runtime(s) no app uses ('flatpak uninstall --unused' removes them)."
                    if unused else "No unused Flatpak runtimes."))
    return " ".join(out) or "Nothing to report."


SKILLS = [Skill(name="cleanup_report", schema=_SCHEMA, run=_run)]
