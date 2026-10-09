"""Bridge that lets the `browse` skill drive the in-app browser window.

`browse` used to drive a headless Chromium only, which nobody watches and which
dies inside the skill sandbox ("browser closed the DevTools pipe"). When
Chronoa's own window is running there is a better browser already on screen:
`gui/browser.py`'s WebKit window, where the person can see every page the model
loads and every field it fills. This module is how a tool call reaches it.

Same shape as `ask_bridge`, for the same reason:

- The GUI installs a *provider* at startup - a callable that returns the
  browser window, building and presenting it on first use. With no provider (a
  headless MCP session, the sandbox child) `has_provider()` is False and
  `browse` keeps its Chromium path.
- Tools run off the GTK thread (the assistant's tool loop, a goal's worker), so
  every touch of the window is queued with `GLib.idle_add` and the caller waits
  on a `threading.Event`. Called *on* the main thread instead - a test, or a
  caller nobody expected - it iterates the main context while it waits, so it
  cannot deadlock waiting for a callback that only its own loop can run.
- A wait that runs out is an error, never a guess: `MainThreadTimeout` says how
  long it waited.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Optional

#: Returns the browser window. Called on the main thread only.
Provider = Callable[[], Any]

_provider: Optional[Provider] = None
_available: Optional[Callable[[], bool]] = None


class MainThreadTimeout(RuntimeError):
    """The main thread did not finish the work in time."""


def set_provider(fn: Optional[Provider], available: Optional[Callable[[], bool]] = None) -> None:
    """Install (or clear, with None) the in-app browser provider.

    `available` is asked on every `has_provider()` - the GUI passes
    `gui.browser.is_available`, so a machine without WebKitGTK reports no
    provider rather than one that raises on first use.
    """
    global _provider, _available
    _provider = fn
    _available = available


def has_provider() -> bool:
    """Whether a browse call can be served by the in-app browser right now."""
    if _provider is None:
        return False
    try:
        return bool(_available()) if _available is not None else True
    except Exception:  # noqa: BLE001 - an availability probe that breaks is "no"
        return False


def window() -> Any:
    """The provider's window. Main thread only."""
    if _provider is None:
        raise RuntimeError("the in-app browser is not available in this process")
    return _provider()


def on_main(work: Callable[[Callable[..., None]], None], timeout: float) -> Any:
    """Run `work(finish)` on the GTK main thread and return what it finished with.

    `work` is handed `finish(value=None, error=None)` and must call it exactly
    once, then or later (WebKit's calls are asynchronous, so "later" is the
    usual case). An exception raised by `work` itself, or passed as `error`, is
    re-raised in the caller.
    """
    from gi.repository import GLib

    done = threading.Event()
    box: dict = {}

    def finish(value: Any = None, error: Optional[BaseException] = None) -> None:
        if done.is_set():
            return
        box["value"], box["error"] = value, error
        done.set()

    def run() -> bool:
        try:
            work(finish)
        except BaseException as exc:  # noqa: BLE001 - handed back to the caller
            finish(error=exc)
        return False

    context = GLib.MainContext.default()
    GLib.idle_add(run)
    if context.is_owner() or threading.current_thread() is threading.main_thread():
        # On the main thread (or holding the context): nobody else will run the
        # idle, so run it here. The main-thread test matters before a loop is
        # running, when `is_owner()` is still False.
        import time
        deadline = time.monotonic() + timeout
        while not done.is_set() and time.monotonic() < deadline:
            context.iteration(False)
            if not done.is_set():
                time.sleep(0.01)
    else:
        done.wait(timeout)
    if not done.is_set():
        raise MainThreadTimeout(f"the browser did not answer within {timeout:g}s")
    if box.get("error") is not None:
        raise box["error"]
    return box.get("value")


__all__ = ["MainThreadTimeout", "has_provider", "on_main", "set_provider", "window"]
