"""bluez refuses the first `Connect` to a watch that is still coming up, and that
used to end the whole watch skill.

bluez answers a watch whose LE link is still rising with *"Operation already in
progress"*, and one it has not finished registering with *"UnknownObject"* -
**both transient, both within a second or two of the call**. Before the retry,
either one ended `moyoung.DirectWatch` at construction time, with a watch
sitting on the desk advertising.

## Why `_retry` takes its sleeper as a parameter

`speech_gate.wait` takes a clock for the same reason, and the docstring says it:
the things worth proving are *that the delay grows* and *that the loop stops*,
and a test that has to sleep 2.8 real seconds to watch them is a slow test
rather than a clear one. So `wait` is injected, and in production it is the
class's own `_pump` - **not `time.sleep`**, which would block the very
`PropertiesChanged` replies the retry is waiting for.

## The wiring is the half a helper test misses

Every test in the first class calls `_retry` directly, which says nothing about
whether the `Connect` call site uses it. A `_retry` that is perfect and never
called is the dead-control shape this repository records more than once, so the
second class checks the call site structurally - by **AST**, because a substring
over the source cannot tell a `Connect` that is inside the retry from one that
merely appears in the same function - and carries a control so a scan that finds
nothing cannot pass.
"""

import ast
import inspect
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr/lib/shani-chronoa"))

moyoung = pytest.importorskip("shani_chronoa.moyoung")


class _Boom(Exception):
    """A stand-in for bluez's error, which moves between versions."""


def _failing(n=99, exc=None):
    """A `do` that fails `n` times, recording how often it was called."""
    calls = []

    def do():
        calls.append(1)
        if len(calls) <= n:
            raise (exc or _Boom(f"attempt {len(calls)}"))
        return "connected"

    do.calls = calls
    return do


class _Waiter:
    """Records every delay it is asked for, so the growth is measured not slept."""

    def __init__(self):
        self.delays = []

    def __call__(self, seconds):
        self.delays.append(seconds)


class TestTheRetryLoop:

    def test_it_calls_once_and_raises_when_asked_for_one_attempt(self):
        """`attempts <= 1` must still *call* - the obvious loop is `range(attempts)`
        and `range(0)` would never run, so `last` would be `None` and the
        `assert` would fire instead of the caller learning what bluez said."""
        for attempts in (0, 1, -3):
            do = _failing()
            with pytest.raises(_Boom):
                moyoung._retry(do, attempts, 0.4, _Waiter())
            assert len(do.calls) == 1, (
                f"attempts={attempts} must still try once, tried {len(do.calls)}")

    def test_it_stops_after_the_attempts_it_was_given(self):
        waiter = _Waiter()
        do = _failing()
        with pytest.raises(_Boom):
            moyoung._retry(do, 4, 0.4, waiter)
        assert len(do.calls) == 4, (
            "the shipped default is CONNECT_ATTEMPTS=4; the loop must stop "
            f"there rather than run away, called {len(do.calls)} times")
        assert len(waiter.delays) == 3, (
            "one wait *between* attempts, not one after the last: "
            f"{waiter.delays}")

    def test_it_raises_the_last_error_and_not_the_first(self):
        """The message a person reads is the *final* refusal, so the wrong one
        to raise sends them looking at a transient that has already gone."""
        first, last = _Boom("Operation already in progress"), _Boom("UnknownObject")
        seen = []

        def do():
            seen.append(1)
            raise first if len(seen) == 1 else last

        with pytest.raises(_Boom) as caught:
            moyoung._retry(do, 4, 0.4, _Waiter())
        assert len(seen) == 4, seen
        assert caught.value is last, f"raised {caught.value!r}, not the last error"
        assert caught.value is not first

    def test_the_delay_grows_and_is_capped(self):
        """Measured, not slept: 0.4 doubling to the cap, and a backoff above the
        cap handed to `wait` already clamped."""
        waiter = _Waiter()
        with pytest.raises(_Boom):
            moyoung._retry(_failing(), 6, 0.4, waiter)
        assert waiter.delays == [0.4, 0.8, 1.6, 3.2, 4.0], (
            "doubling from CONNECT_BACKOFF, clamped at CONNECT_BACKOFF_CAP: "
            f"{waiter.delays}")

        big = _Waiter()
        with pytest.raises(_Boom):
            moyoung._retry(_failing(), 3, 10.0, big)
        assert big.delays == [moyoung.CONNECT_BACKOFF_CAP, moyoung.CONNECT_BACKOFF_CAP], (
            f"a backoff above the cap must be clamped, not passed through: {big.delays}")

    def test_a_first_success_never_waits(self):
        """The common case is a watch that connects immediately, and a test that
        cannot see the difference between "did not wait" and "waited for
        nothing" is not testing the retry."""
        waiter = _Waiter()
        assert moyoung._retry(lambda: "connected", 4, 0.4, waiter) == "connected"
        assert waiter.delays == [], f"waited on a first-try success: {waiter.delays}"

    def test_a_late_success_stops_the_loop_and_returns(self):
        waiter = _Waiter()
        do = _failing(n=2)
        assert moyoung._retry(do, 4, 0.4, waiter) == "connected"
        assert len(do.calls) == 3, f"kept going after success: {len(do.calls)} calls"
        assert waiter.delays == [0.4, 0.8], waiter.delays

    def test_it_does_not_swallow_an_interrupted_turn(self):
        """Only `Exception` is caught, so a `KeyboardInterrupt` from the main
        loop still gets out on the first try - a retry that ate it would hang
        the person out of the app."""
        calls = []

        def do():
            calls.append(1)
            raise KeyboardInterrupt()

        with pytest.raises(KeyboardInterrupt):
            moyoung._retry(do, 4, 0.4, _Waiter())
        assert len(calls) == 1, f"retried an interrupt: {len(calls)}"


# --- the call site -----------------------------------------------------------------

def _init_source() -> ast.Module:
    # `inspect.getsource` on a *method* hands back the block with its original
    # indentation, so `ast.parse` rejects it outright ("unexpected indent") -
    # measured, and the reason this helper exists rather than an inline parse.
    return ast.parse(textwrap.dedent(inspect.getsource(moyoung.BluezWatch.__enter__)))


def _retry_calls(tree):
    """Every `_retry(...)` call in a parsed function body."""
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "_retry"]


def _strings_in(node):
    return {n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)}


class TestTheConnectSiteGoesThroughIt:
    """**The half that `_retry`'s own tests cannot reach.**

    A helper that is correct and never called is the dead-control shape, so this
    asks of the *call site* rather than of the helper. Structural (AST) because
    a substring cannot tell a `Connect` inside the retry from one that merely
    appears in the same function - which is precisely the defect being guarded,
    since the two would be on adjacent lines.

    **The site is `BluezWatch.__enter__`, not `DirectWatch.__init__`** - which
    is worth stating because the first version of this file asserted against the
    latter and found nothing: that constructor is three lines and does not touch
    bluez at all. `DirectWatch` reaches the retry through `open_watch`, and
    `test_the_site_is_reachable_from_the_public_entry_point` is what holds that
    hop rather than assuming it.
    """

    def test_connect_is_the_call_retry_is_making(self):
        calls = _retry_calls(_init_source())
        assert len(calls) == 1, (
            "BluezWatch.__enter__ must route its Connect through exactly one "
            f"_retry call, found {len(calls)}")
        # The lambda inside the retry's arguments must be the Connect.
        assert "Connect" in _strings_in(calls[0]), (
            "the retried call is not a Connect - the helper may be wired to "
            "something harmless and this would still pass")

    def test_it_retries_the_module_constants_not_a_local_number(self):
        """A literal `4` typed at the call site would keep working after
        `CONNECT_ATTEMPTS` changed - and that constant is the documented budget."""
        call = _retry_calls(_init_source())[0]
        passed = {a.id for a in call.args if isinstance(a, ast.Name)}
        assert "CONNECT_ATTEMPTS" in passed, (
            "the attempt budget must be read from the module constant")
        assert "CONNECT_BACKOFF" in passed, (
            "the backoff must be read from the module constant")
        assert not (call.args and isinstance(call.args[0], ast.Constant)), (
            "a literal first argument would be `do`, not a number")

    def test_the_sleeper_is_the_class_pump_and_not_time_sleep(self):
        """**This is the part with a reason attached.** `_pump` drains the pushed
        GLib main context, so the `PropertiesChanged` that says "connected" can
        land while the retry waits. A bare `time.sleep` blocks it, so the retry
        would wait out its whole budget against a reply it is preventing."""
        tree = _init_source()
        call = _retry_calls(tree)[0]
        last = call.args[-1]
        assert isinstance(last, ast.Attribute) and last.attr == "_pump", (
            "the sleeper must be self._pump; got "
            f"{ast.dump(last) if last else None}")
        sleeps = [n for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                  and n.func.attr == "sleep"]
        assert sleeps == [], (
            "a time.sleep anywhere in __enter__ would block the main context "
            f"this class pushes: {len(sleeps)} call(s)")

    def test_a_refusal_names_the_number_of_tries(self):
        """The message is the only thing a person sees, and *"after 4 tries"*
        is what tells them the watch is gone rather than sulking.

        Asserted on the **name** in the source, not the number: the sentence is
        an f-string, so the file holds `{CONNECT_ATTEMPTS}` and only the
        interpolation produces "4". The first version of this assertion searched
        for the substituted text and therefore could never have matched - the
        pattern-read-as-an-absence trap, and a check that fails on correct code
        is the same defect as one that cannot fail.
        """
        src = textwrap.dedent(inspect.getsource(moyoung.BluezWatch.__enter__))
        assert "CONNECT_ATTEMPTS} tries" in src, (
            "the WatchError should quote CONNECT_ATTEMPTS, so raising the "
            "budget does not leave the message saying four")

    def test_the_site_is_reachable_from_the_public_entry_point(self):
        """**The hop that made the first version of this file find nothing.**

        `DirectWatch` is the documented way in and its `__enter__` only calls
        `open_watch`, which tries `BluezWatch` first. If that changed - if
        `open_watch` went straight to the gatttool `Watch` - every assertion
        above would still pass on a site nobody reaches, which is the dead-control
        shape one level further out.
        """
        src = textwrap.dedent(inspect.getsource(moyoung.open_watch))
        assert "BluezWatch" in src, (
            "open_watch must still try the bluez path first")
        entry = textwrap.dedent(inspect.getsource(moyoung.DirectWatch.__enter__))
        assert "open_watch" in entry, (
            "DirectWatch.__enter__ is the documented entry point and must call "
            f"open_watch; it reads {entry.strip()[:80]!r}")

    def test_the_scan_can_fail(self):
        """**The control.** Every assertion above is `len(calls) == 1` or a
        search inside that one call, so a tree where `_retry` is not called at
        all satisfies none of them - unless the search itself is broken, in
        which case `calls[0]` raises and the class errors rather than passes.
        Proved here rather than assumed, because a scanner that finds nothing
        reports the same result as one that works."""
        empty = ast.parse("def f():\n    return 1\n")
        assert _retry_calls(empty) == [], (
            "the search matches a name that is not there; every assertion in "
            "this class would be reading an empty list")

        real = _retry_calls(_init_source())
        # `self._DEV` is an Attribute, not a string, so the only literal inside
        # the retried call is "Connect" - which is the discrimination wanted.
        assert _strings_in(real[0]) == {"Connect"}, (
            "the string reader must see the retried method name and nothing "
            f"else: got {sorted(_strings_in(real[0]))}")
        # And it must be *scoped*: the same reader over the whole method sees
        # strictly more, so the exact-set assertion above is not passing because
        # the reader returns one thing whatever you point it at.
        whole = _strings_in(_init_source())
        assert "Connect" in whole and len(whole) > 1, (
            f"the reader is not discriminating - over the whole method it saw "
            f"{sorted(whole)}")