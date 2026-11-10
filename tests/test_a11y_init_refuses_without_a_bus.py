"""`Atspi.init()` must never be called without a bus to initialise against.

**`Atspi.init()` aborts the whole process when there is no accessibility
bus.** It raises a `g_error`, not a Python exception, so no `try`/`except`
can catch it: the interpreter dies with *"Fatal Python error: Aborted"* and
every test after that point is reported as an error rather than as a result.

That is not theoretical. Measured on a CI runner with no a11y bus
(2026-11-10): the pytest process died at 28% with a core dump, exit 134 and
**no summary line at all** - the file whose test happened to be running had
*passed* every one of its cases first, and the 72% of the suite behind it
was never measured at all.

So `ui_driver._a11y_init()` asks the question first (the session bus
publishes the a11y address through `org.a11y.Bus.GetAddress`, and the name
is absent when there is no bus) and refuses to call `Atspi.init()` when the
answer is no.

**These tests are about the guard, not about the tree.** The a11y tree's
own behaviour is covered by the driver tests; what is pinned here is that the
one call that can kill the interpreter is not made when it cannot succeed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import ui_driver  # noqa: E402


@pytest.fixture
def atspi():
    """The real Atspi module, or a skip where the typelib is absent."""
    import gi

    try:
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
    except (ImportError, ValueError) as exc:
        pytest.skip(f"Atspi is not installed here: {exc}")
    return Atspi


def _not_yet_initialised(monkeypatch) -> None:
    """Pretend AT-SPI has not been initialised yet.

    Another test in the same run may have initialised it for real - the
    driver tests do - and then the guard correctly *skips* the init, which
    is the other branch's behaviour. So every test here states the state it
    is about, rather than depending on what ran before it.
    """
    import gi

    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi

    monkeypatch.setattr(Atspi, "is_initialized", lambda: False)


def test_the_guard_asks_the_session_bus_before_it_initialises(monkeypatch):
    """The question is asked, and its answer is what decides.

    **Without a bus the answer is no** - which is the whole point: the
    question has to be asked of the thing that knows, not guessed from a
    socket path (the module's own `_a11y_env` comment records that a guessed
    path named a socket that did not exist).
    """
    asked = []
    monkeypatch.setattr(ui_driver, "_a11y_bus_available",
                        lambda: asked.append(True) or False)
    _not_yet_initialised(monkeypatch)

    assert ui_driver._a11y_init() is False
    assert asked, "the guard decided without asking whether there is a bus"


def test_no_bus_means_atspi_init_is_never_called(monkeypatch, atspi):
    """**The abort, prevented.**

    `Atspi.init()` is the one call in this module that can kill the
    interpreter outright, so with no bus it must not be reached - not
    "reached and caught", because it cannot be caught.
    """
    called = []
    monkeypatch.setattr(ui_driver, "_a11y_bus_available", lambda: False)
    _not_yet_initialised(monkeypatch)
    monkeypatch.setattr(atspi, "init", lambda: called.append(True))

    assert ui_driver._a11y_init() is False
    assert not called, "Atspi.init() was called with no bus to initialise against"


def test_a_bus_means_atspi_is_initialised(monkeypatch, atspi):
    """**The control, and the reason the first test means anything.**

    A guard that simply refused everything would pass the test above and
    break every a11y feature on a real desktop. So the other direction is
    pinned too: with a bus, AT-SPI *is* initialised.
    """
    called = []
    monkeypatch.setattr(ui_driver, "_a11y_bus_available", lambda: True)
    # **Not already initialised**: another test in the same run may have
    # initialised AT-SPI for real, and then the guard correctly skips the
    # init - which is the other test's behaviour, not this one's.
    _not_yet_initialised(monkeypatch)
    monkeypatch.setattr(atspi, "init", lambda: called.append(True))

    assert ui_driver._a11y_init() is True
    assert called, "a bus was reported and AT-SPI was not initialised"


def test_a_bus_that_goes_away_mid_call_is_not_a_crash(monkeypatch, atspi):
    """The bus can disappear between the question and the answer."""
    def _raise():
        raise RuntimeError("the bus went away")

    monkeypatch.setattr(ui_driver, "_a11y_bus_available", lambda: True)
    _not_yet_initialised(monkeypatch)
    monkeypatch.setattr(atspi, "init", _raise)

    assert ui_driver._a11y_init() is False


def test_every_atspi_init_in_the_module_goes_through_the_guard():
    """**No second way in.**

    A guard at one call site is a guard at one call site. This reads the
    module's source and asserts that every `Atspi.init()` in it is the one
    inside `_a11y_init()` - so a new reader that inits AT-SPI directly
    fails here rather than taking a CI run down at 28%.
    """
    source = (_REPO / "usr" / "lib" / "shani-chronoa" / "shani_chronoa"
              / "ui_driver.py").read_text(encoding="utf-8")
    # **A bare call, not the string.** The module's own docstrings name
    # `Atspi.init()` to explain why it is guarded, and a search that counted
    # those would report every explanation as a second way in.
    calls = [i for i, line in enumerate(source.splitlines(), 1)
             if re.fullmatch(r"\s*Atspi\.init\(\)\s*", line)]
    assert len(calls) == 1, (
        f"Atspi.init() is called {len(calls)} times in ui_driver.py (lines "
        f"{calls}); every one of them must go through _a11y_init(), because "
        f"the call aborts the process when there is no bus")
