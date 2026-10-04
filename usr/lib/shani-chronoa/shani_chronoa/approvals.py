"""Approve from a notification: an unattended rule asks a person before it acts.

An event rule armed with `ask_first` does not run its actuator when it fires.
It posts a desktop notification with **Allow once** and **Deny** buttons, and
only an Allow, within APPROVAL_SECONDS, runs it. That is what lets a rule reach
an actuator an unattended rule may not (a destructive one, a file overwrite):
at the moment it acts, a person has said yes to this one action.

Delivered with `notify-send --action ... --wait` rather than Gio.Notification,
for three reasons measured on the images: it speaks org.freedesktop.Notifications,
which GNOME Shell and Plasma both implement (the GNOME and Plasma images both
ship it, with --action); Gio's GNOME path needs a desktop file named after the
application id (dev.shani.chronoa), which does not exist; and it needs no GTK
main loop, so the same code works when the window is closed.

Each request waits on its own daemon thread, so a notification nobody answers
never stalls the poller. Silence, a closed notification, an expired one and a
desktop with no notification server are all "not allowed" - only an explicit
Allow acts.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from typing import Callable

logger = logging.getLogger(__name__)

APPROVAL_SECONDS = 900
ALLOW, DENY, TIMEOUT, UNAVAILABLE = "allow", "deny", "timeout", "unavailable"


def ask(title: str, body: str, seconds: int = APPROVAL_SECONDS) -> str:
    """Show the question and block until it is answered: allow, deny, timeout or unavailable."""
    if shutil.which("notify-send") is None:
        return UNAVAILABLE
    argv = ["notify-send", "--app-name=Shani Chronoa", "--urgency=critical",
            f"--expire-time={seconds * 1000}", "--action=allow=Allow once", "--action=deny=Deny",
            title[:120], body[:400]]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=seconds + 10, check=False)
    except subprocess.TimeoutExpired:
        return TIMEOUT
    except OSError:
        return UNAVAILABLE
    if proc.returncode != 0:
        logger.info("notify-send could not ask: %s", (proc.stderr or "").strip()[:200])
        return UNAVAILABLE
    answer = (proc.stdout or "").strip().splitlines()
    if answer and answer[-1] == ALLOW:
        return ALLOW
    if answer and answer[-1] == DENY:
        return DENY
    return TIMEOUT  # closed or expired without a choice


class NotifyApprover:
    """Asks on a thread per request; calls `on_answer(answer)` when it is known."""

    def __init__(self, asker: Callable[[str, str, int], str] = ask, seconds: int = APPROVAL_SECONDS) -> None:
        self._ask = asker
        self._seconds = seconds
        self._lock = threading.Lock()
        self._pending: dict = {}

    def available(self) -> bool:
        return self._ask is not ask or shutil.which("notify-send") is not None

    def pending(self) -> "list[str]":
        with self._lock:
            return sorted(self._pending)

    def request(self, key: str, title: str, body: str, on_answer: Callable[[str], None]) -> bool:
        """Ask once per key; False if the same question is already waiting."""
        with self._lock:
            if key in self._pending:
                return False
            self._pending[key] = True

        def worker() -> None:
            try:
                answer = self._ask(title, body, self._seconds)
            except Exception as exc:  # noqa: BLE001 - a broken asker is a "no", never a crash
                logger.warning("approval request failed: %s", exc)
                answer = UNAVAILABLE
            finally:
                with self._lock:
                    self._pending.pop(key, None)
            try:
                on_answer(answer)
            except Exception as exc:  # noqa: BLE001
                logger.warning("approval callback failed: %s", exc)

        threading.Thread(target=worker, name=f"approval-{key[:24]}", daemon=True).start()
        return True


def describe(rule_name: str, actuator: str, arguments: dict, summary: str) -> "tuple[str, str]":
    """Title and body a person can decide on: what happened, and exactly what would run."""
    args = ", ".join(f"{k}={v!r}" for k, v in sorted(arguments.items()))[:200]
    return (f"Chronoa rule “{rule_name}” wants to act",
            f"{summary}\n\nIt would run {actuator}({args}). Allow once?")


__all__ = ["APPROVAL_SECONDS", "ALLOW", "DENY", "TIMEOUT", "UNAVAILABLE", "ask", "describe",
           "NotifyApprover"]
