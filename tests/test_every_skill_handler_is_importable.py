"""Every skill the model is offered must actually run.

Three skills - `calendar_month`, `date_math` and `stopwatch` - were declared as
`run=lambda a: _run(a)`. They passed every check the loader made, because a
lambda *is* callable, and they were advertised to the model and to every MCP
client. They failed on every single call, because a non-local skill is executed
in a sandboxed child process whose program is built by interpolating the
handler's name:

    from shani_chronoa.skills.stopwatch import <lambda>; ...

which is a `SyntaxError`. What the model saw as the tool's result was a Python
traceback.

The suite missed it because the tests for those three called the private
`_run()` directly, which works perfectly. **A test that calls the function is
not a test that the skill is reachable**, and the gap between those two things
is exactly where this lived.

So this file asserts the transport-level property instead: for every registered
skill, the handler is a named function that can be imported by name, which is
what `_dispatch_inner` does with it.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import skills as skills_pkg  # noqa: E402


def _registered():
    """Every skill the loader accepted, with the module it came from."""
    _schemas, handlers = skills_pkg.discover_skills()
    return handlers


class TestEveryHandlerCanBeImportedByName:
    def test_no_handler_is_an_anonymous_name(self):
        """The exact defect. `<lambda>` is what `__name__` returns, and it is
        not an identifier, so the generated child program cannot parse."""
        anonymous = sorted(
            name for name, handler in _registered().items()
            if not getattr(handler, "__name__", "").isidentifier()
        )
        assert not anonymous, (
            f"these skills are advertised but cannot be dispatched: {anonymous}. "
            f"A non-local skill is executed by importing its handler by name, so "
            f"`run=` must be a named function, not a lambda or a partial."
        )

    def test_every_handler_imports_from_its_own_module(self):
        """Stronger than a name check: the generated program is
        `from <handler's module> import <name>`, so the name has to resolve
        there. A handler moved between modules while keeping its name passes an
        identifier test and still fails at dispatch."""
        unimportable = []
        for name, handler in _registered().items():
            handler_name = getattr(handler, "__name__", "")
            if not handler_name.isidentifier():
                continue
            module = getattr(handler, "__module__", "")
            try:
                module_object = importlib.import_module(module)
            except Exception:  # noqa: BLE001 - reported below
                unimportable.append(f"{name} (module {module!r} does not import)")
                continue
            if getattr(module_object, handler_name, None) is not handler:
                unimportable.append(
                    f"{name} -> {module}.{handler_name} is not the registered handler"
                )
        assert not unimportable, (
            f"handlers the dispatcher could not import: {unimportable}"
        )

    def test_the_three_that_were_broken_are_now_callable_by_name(self):
        """Named explicitly, so this file still means something if the general
        checks above are ever loosened."""
        handlers = _registered()
        for name in ("calendar_month", "date_math", "stopwatch"):
            assert name in handlers, f"{name} is not registered at all now"
            assert handlers[name].__name__ == "_run", (
                f"{name} still has handler {handlers[name].__name__!r}"
            )
