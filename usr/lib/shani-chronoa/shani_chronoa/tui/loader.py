"""Bounded background loads of Inventory sections for the full-screen TUI.

The terminal is a thin client. One daemon worker calls ``client.inventory``
sequentially, so a single shared Gio connection is never used from two threads.
Main-thread calls only touch a lock, a queue and a dict: no D-Bus, no waiting.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Optional


class PanelLoader:
    """Load Inventory sections in the background, with bounded, stale-safe results.

    ``start()`` queues one fetch per section in ``sections``. ``request()``
    re-fetches one section, skipping a recent answer unless ``force`` is set.
    Every fetch carries a generation; a result whose generation no longer
    matches is dropped, so an older answer can never overwrite a newer request.
    """

    def __init__(
        self,
        client,
        sections: tuple[str, ...],
        *,
        timeout_ms: int = 500,
        stale_after: float = 30.0,
    ):
        self.client = client
        self.sections = tuple(sections)
        self.timeout_ms = int(timeout_ms)
        self.stale_after = float(stale_after)
        self._lock = threading.Lock()
        self._queue: queue.Queue = queue.Queue()
        self._entries: dict[str, dict] = {}
        self._ready: dict[str, dict] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- main-thread API --------------------------------------------------

    def start(self) -> None:
        """Start the one worker and queue the startup pass. Never blocks."""
        with self._lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._run, name="chronoa-panels", daemon=True
            )
            self._thread.start()
        for section in self.sections:
            self.request(section, force=True)

    def request(self, section: str, *, force: bool = False) -> None:
        """Queue a fetch for ``section`` unless a recent one still stands."""
        if self._stop.is_set():
            return
        with self._lock:
            entry = self._entries.setdefault(section, self._new_entry())
            now = time.monotonic()
            if (
                not force
                and entry["data"] is not None
                and entry["error"] is None
                and now - entry["at"] < self.stale_after
            ):
                return  # recent answer; nothing to do
            entry["generation"] += 1
            generation = entry["generation"]
        self._queue.put((section, generation))

    def snapshot(self, section: str) -> Optional[dict]:
        """Current ``{data, at, error, generation}`` entry, or None if unloaded.

        A queued but unfinished fetch has no data yet and reads as None, so a
        caller can show a placeholder instead of an empty answer.
        """
        with self._lock:
            entry = self._entries.get(section)
            if entry is None or entry["data"] is None:
                return None
            return dict(entry)

    def drain(self) -> dict[str, dict]:
        """Take the sections that finished since the last drain."""
        with self._lock:
            ready, self._ready = self._ready, {}
        return ready

    def mark_stale(self, section: str) -> None:
        """Invalidate in-flight work so the next request reloads the section."""
        with self._lock:
            entry = self._entries.setdefault(section, self._new_entry())
            entry["generation"] += 1
            entry["at"] = 0.0

    def stop(self) -> None:
        """Signal the worker, wake it, and join it."""
        self._stop.set()
        self._queue.put(None)
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(5.0, self.timeout_ms / 1000 + 2.0))

    # -- worker -----------------------------------------------------------

    @staticmethod
    def _new_entry() -> dict:
        return {"data": None, "at": 0.0, "error": None, "generation": 0}

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if item is None:
                break
            section, generation = item
            try:
                data = self.client.inventory(section, timeout_ms=self.timeout_ms)
                self._finish(section, generation, data, None)
            except Exception as exc:  # noqa: BLE001 - one section must not kill the worker
                message = str(exc)
                self._finish(section, generation, {"error": message}, message)
            finally:
                self._queue.task_done()

    def _finish(self, section: str, generation: int, data, error) -> None:
        with self._lock:
            entry = self._entries.setdefault(section, self._new_entry())
            if entry["generation"] != generation:
                return  # stale: a newer request already superseded this answer
            entry["data"] = data
            entry["error"] = error
            entry["at"] = time.monotonic()
            self._ready[section] = dict(entry)
