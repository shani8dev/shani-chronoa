"""Shorten `ask_bridge.ask`'s wait, **by parameter name**.

Every test that needs an approval to settle quickly reached for
`ask_bridge.ask.__defaults__ = ("", [], 2.0)` - a positional triple written when
`ask` took three parameters. `ask` then grew a fourth, `preview`, and the tuple
bound `[]` to `timeout` and `2.0` to `preview`, so `threading.Event.wait([])`
raised `TypeError` inside `ask`.

**A fixture that predates the signature fails as a broken product.** Ten sites
across two files reported `TypeError: '>' not supported between instances of
'list' and 'int'` or `assert 16 == 20`, which reads as a leaking question slot
rather than a helper that was never updated - the same shape AGENTS.md records
for `fake_bin`'s `PATH` and for the `GSETTINGS_BACKEND` leak.

So the timeout is named here rather than counted:

    with ask_timeout(0.1):
        ask_bridge.ask("q", ["a", "b"])

and a parameter added or reordered later cannot move it, because the slot is
found by name against the signature read at call time.
"""

from __future__ import annotations

import contextlib
import inspect

from shani_chronoa import ask_bridge


def defaults_with_timeout(seconds: float) -> tuple:
    """`ask.__defaults__` with its **timeout** slot set to `seconds`.

    By name, so a parameter added or reordered later cannot move the number into
    a different argument - which is what the positional triples did.
    """
    names = list(inspect.signature(ask_bridge.ask).parameters)
    defaults = list(ask_bridge.ask.__defaults__ or ())
    # `__defaults__` holds the **trailing** parameters' defaults, not every
    # parameter's: `ask(question, options, timeout, preview)` gives
    # `(DEFAULT_TIMEOUT_SECONDS, None)`. `question` and `options` are required,
    # so the slot is the name's index shifted left by how many are required.
    first_default = len(names) - len(defaults)
    slot = names.index("timeout") - first_default
    if not 0 <= slot < len(defaults):
        # Unreachable for `ask` as it stands, and worth refusing rather than
        # writing a tuple that means something else: a wrong slot here is the
        # exact defect this module exists to stop. (The first version of this
        # asserted the counts were equal and raised on every call, because
        # `ask` has two required parameters.)
        raise AssertionError(
            f"ask takes {len(names)} parameters ({', '.join(names)}) with "
            f"{len(defaults)} default(s); 'timeout' is not one of them"
        )
    defaults[slot] = seconds
    return tuple(defaults)


@contextlib.contextmanager
def ask_timeout(seconds: float):
    """`ask_bridge.ask` waits `seconds` instead of its default, for the block.

    The restore is in a `finally`, so an assertion failure inside the block does
    not leave a 0.05-second timeout behind for every later test in the file -
    the same reason the mutation harness restores rather than re-deriving.
    """
    original = ask_bridge.ask.__defaults__
    ask_bridge.ask.__defaults__ = defaults_with_timeout(seconds)
    try:
        yield
    finally:
        ask_bridge.ask.__defaults__ = original