"""Which version of each file the model last read: edits are made against it.

**The OpenDots shape, applied to files.** Its `pages.update` carries the
revision the writer read (`expectedRevision`), and a write against an older one
is refused rather than allowed to win. Here a file read by `read_text_file`
records the SHA-256 of what was read, and `edit_file` checks it:

- **never read** - the edit is refused, and the refusal shows the file, which
  counts as the read. Measured 2026-10-15: Qwen3-1.7B, told to read first, sent
  `edit_file` with text from the test's description instead of the file's, and
  drifted off the task. One refused call now hands it what it needed.
- **changed since it was read** - by you, an editor, or another tool - the
  edit is refused with the current text, so a change is never patched into a
  file the model is no longer looking at.

After a successful edit the new contents are recorded, so a run of edits does
not need a read between each. Kept in the cache directory because skills run in
a sandbox where that is writable and the state directory is not; losing it
costs one extra read, never a wrong edit.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Optional

#: Paths remembered; the oldest go first, so the file stays small.
MAX_ENTRIES = 500


def _ledger() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "shani-chronoa" / "reads.json"


def revision(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load() -> dict:
    try:
        value = json.loads(_ledger().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def record(path, data: bytes) -> str:
    """Remember that `path` was seen as `data`; returns its revision."""
    rev = revision(data)
    entries = _load()
    entries.pop(str(path), None)
    entries[str(path)] = rev
    while len(entries) > MAX_ENTRIES:
        entries.pop(next(iter(entries)))
    target = _ledger()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(entries), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        pass  # an unwritable ledger costs a read later, never a wrong edit
    return rev


def last(path) -> Optional[str]:
    """The revision of `path` the model last saw, or None if never."""
    return _load().get(str(path))
