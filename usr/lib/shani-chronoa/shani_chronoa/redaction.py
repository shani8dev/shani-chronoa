"""Keep live secrets out of what leaves the process: model prompts, logs a model reads, skill children.

The values themselves are stored in the desktop keyring (`secret_store.py`)
and reach this module at run time - `register` is told the cloud API keys the
app is about to use. Nothing here is written to disk. What it does with them:

- `sanitize(text)` replaces every registered value with `$SECRET:<NAME>`
  before text goes to any model (all three LLM clients and the memory sense
  call it), so a key pasted into a chat or read back by a tool is never sent.
- `child_env()` is the environment a skill subprocess gets: the parent's,
  with every variable that holds a registered value removed. Skills need no
  credentials, and a crashed child with a key in its environment is a key in a
  core dump.

This replaces `secrets_manager.py`, a port of sayri's vault that kept values in
a JSON file "protected" by XOR with a machine-derived salt and base64 - not
encryption - and that production never wrote to; its only live uses were the
redaction and the injection (which, unlike this, handed every key to every
skill).
"""

from __future__ import annotations

import os
import threading
from typing import Dict, List, Optional

#: Shorter values are not redacted: "abc" would be replaced inside ordinary words.
MIN_SECRET_CHARS = 4


class Redactor:
    """The process's registry of live secret values."""

    def __init__(self) -> None:
        self._values: Dict[str, str] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _name(key: str) -> str:
        return key.strip().upper().replace(" ", "_").replace("-", "_")

    def register(self, key: str, value: str) -> None:
        """Remember a live secret (never persisted). A blank value is ignored."""
        if not value or not value.strip():
            return
        with self._lock:
            self._values[self._name(key)] = value.strip()

    def forget(self, key: str) -> bool:
        with self._lock:
            return self._values.pop(self._name(key), None) is not None

    def clear(self) -> None:
        with self._lock:
            self._values.clear()

    def names(self) -> List[str]:
        with self._lock:
            return sorted(self._values)

    def sanitize(self, text):
        """`text` with every registered value replaced by `$SECRET:<NAME>` (longest first)."""
        if not text or not isinstance(text, str):
            return text
        with self._lock:
            items = sorted(self._values.items(), key=lambda kv: -len(kv[1]))
        for name, value in items:
            if len(value) >= MIN_SECRET_CHARS and value in text:
                text = text.replace(value, f"$SECRET:{name}")
        return text

    def child_env(self, base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """A copy of the environment with no variable whose value contains a registered secret."""
        env = dict(os.environ if base is None else base)
        with self._lock:
            values = [v for v in self._values.values() if len(v) >= MIN_SECRET_CHARS]
        for key in [k for k, v in env.items() if any(s in v for s in values)]:
            del env[key]
        return env


redactor = Redactor()
