"""Bridge between asyncio coroutines and the GTK/GLib main loop.

GLib.idle_add and GTK signal callbacks never await coroutines - calling an
`async def` from them only creates a coroutine object that is immediately
discarded. This module runs a dedicated asyncio event loop on a background
thread and marshals results back onto the GLib main loop via GLib.idle_add,
so callers can safely touch GTK widgets in their completion callback.
"""

import asyncio
import concurrent.futures
import logging
import threading
from typing import Any, Callable, Coroutine, Optional

from gi.repository import GLib  # type: ignore

logger = logging.getLogger(__name__)


class AsyncBridge:
    """Runs coroutines on a background asyncio loop, delivers results on the GTK thread."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._shutdown = False
        self._thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run(
        self,
        coro: Coroutine[Any, Any, Any],
        callback: Optional[Callable[[Any], None]] = None,
    ) -> None:
        """Schedule `coro` on the background loop.

        If `callback` is given, it is invoked on the GTK main thread with the
        coroutine's result, or with the raised exception if it failed.
        """
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)

        if callback is None:
            return

        def _on_done(fut: "asyncio.Future[Any]") -> None:
            try:
                result: Any = fut.result()
            except asyncio.CancelledError:
                # Coroutine cancelled during shutdown - nothing to deliver.
                return
            except Exception as e:  # noqa: BLE001 - forwarded to callback
                result = e
            GLib.idle_add(callback, result)

        future.add_done_callback(_on_done)

    def shutdown(self) -> None:
        """Stop the background loop and join its thread.

        Cancels any in-flight coroutines so none is abandoned mid-flight,
        then stops the loop and joins the thread. Bounded: waits at most a
        short timeout for the loop to wind down. Safe to call more than once.
        """
        if self._shutdown:
            return
        self._shutdown = True
        if not self._thread.is_alive():
            return

        async def _cancel_pending() -> None:
            pending = [
                task for task in asyncio.all_tasks()
                if task is not asyncio.current_task()
            ]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        cancel = asyncio.run_coroutine_threadsafe(_cancel_pending(), self._loop)
        try:
            cancel.result(timeout=2)
        except concurrent.futures.TimeoutError:
            # Bounded shutdown: if the loop is wedged, stop it anyway.
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2)
