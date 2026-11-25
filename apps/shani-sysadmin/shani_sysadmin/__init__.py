"""shani-sysadmin: the sysadmin and developer tooling moved out of Chronoa.

See ``README.md`` in the app root for why these skills left Chronoa and which
Chronoa library modules they still import.
"""

from __future__ import annotations

from typing import NamedTuple

from . import bootstrap  # noqa: F401 - importing installs both sys.path roots

bootstrap.install()

class Outcome(NamedTuple):
    """The app's equivalent of Chronoa's ``DispatchResult``.

    ``run`` is the only channel a skill has, so this is deliberately small:
    the text it wrote and whether it got far enough to write one.
    """

    text: str
    ran: bool


__all__ = ["Outcome", "bootstrap", "discover_skills", "discover_senses", "run_skill"]


def run_skill(name: str, arguments: dict) -> Outcome:
    """Dispatch one moved skill. Used by the CLI and by the app's tests."""
    handlers = discover_skills()[1]
    if name not in handlers:
        return Outcome(f"Unknown tool: {name}", False)
    try:
        return Outcome(str(handlers[name](arguments)), True)
    except Exception as exc:  # noqa: BLE001 - the caller reports, not crashes
        return Outcome(f"{name} raised {type(exc).__name__}: {exc}", False)


def discover_skills() -> tuple[list[dict], dict[str, object]]:
    """Load every moved skill, using Chronoa's ``Skill`` contract.

    The return shape is the same as ``shani_chronoa.skills.discover_skills``:
    ``(schemas, handlers)``. Nothing is registered in Chronoa's whitelist by
    this call - the two registries are deliberately separate.
    """
    from .skills import discover_skills as _discover

    return _discover()


def discover_senses() -> dict:
    """Load the moved senses (currently ``kernellog``)."""
    from .senses import discover_senses as _discover

    return _discover()
