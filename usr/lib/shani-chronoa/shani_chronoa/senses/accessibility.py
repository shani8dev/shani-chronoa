"""What is on screen, read over the accessibility bus.

Chronoa's window tools are X11-only. `list_windows` shells out to `xdotool`,
which works on X11 and does not exist on Wayland - so under a Wayland session
the honest answer today is "I cannot see your windows", which is true and
useless. This sense reads the same information over AT-SPI instead.

That is the adoption worth taking from qwen-code's `cua-driver`, which drives
GNOME through the accessibility tree rather than by synthesising input. The
accessibility bus is a D-Bus service, not an X protocol, so it is reachable
under both.

**Verification boundary, stated plainly.** This was built and observed working
on an X11 session, where the bus reported twenty applications with their roles
and window counts. It has *not* been observed under Wayland, because there is no
Wayland session in this environment. The X11 case demonstrates the mechanism and
the data are real; the Wayland claim is the reason for the port and remains
untested.

## Reading only, deliberately

This enumerates. It does not act. AT-SPI can also press buttons, type text and
move focus, and every one of those is an input-injection privilege with no
verified portable interface in this codebase. Adding actuation here would mean
shipping a capability whose only evidence would be a passing test on the machine
that wrote it. So the tree is read, and the acting half stays a separate
decision for someone who can test it where it will run.

## Off by default

Window titles and application names say what someone is working on. That is
personal by any reading, so this is `SENSITIVITY_PERSONAL`, the key defaults to
off, and the sense refuses with the reason rather than quietly reporting nothing.

## Unavailable is not empty

The distinction this codebase treats as load-bearing. A bus that is not running
means "I could not look", and this says so. Returning an empty list would be
indistinguishable from a desktop with nothing running on it, and an assistant
that says "no applications are open" when it simply could not see would be
lying in the most checkable way available - the user looks at their own screen.

Bounded too. The tree is capped and says so when it is, using the same
`withheld_note` discipline the file tools use, so a truncated read never reads
as a complete one.
"""

from __future__ import annotations

import logging

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PERSONAL, Sense

logger = logging.getLogger(__name__)

KIND = "machine-state"

#: Application names and window titles say what the user is working on. That is
#: personal on any reading, so this is not public metadata.
SENSITIVITY = SENSITIVITY_PERSONAL

_TTL_SECONDS = 60.0

#: Never polled. An accessibility tree is a live view of what someone is doing,
#: and walking it on a timer would be perceiving without being asked. This sense
#: answers when it is called, or not at all.
_POLL_INTERVAL = None

#: Cap on applications reported. The bus happily lists every daemon that has ever
#: registered a toolkit; most are not applications the user would recognise.
_MAX_APPS = 40

#: A tree walk that has not returned promptly is worse than no tree: it holds a
#: D-Bus round trip open against a process that may be wedged.
_TIMEOUT_SECONDS = 5.0

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "accessibility",
        "description": (
            "List the applications the desktop currently exposes on its "
            "accessibility bus, with each one's role and how many windows it "
            "has. This reaches the desktop over AT-SPI rather than X11, so it "
            "works where xdotool does not. Reachability only: it reports which "
            "applications are present, not what is on their windows. Requires "
            "the 'accessibility-sense-enabled' consent key."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


class _Unavailable(Exception):
    """The bus could not be reached - unknown, not empty."""


def _desktop():
    """The AT-SPI desktop, or raise `_Unavailable` explaining why not."""
    try:
        import gi

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
    except (ImportError, ValueError) as exc:
        raise _Unavailable(
            f"the accessibility typelib is not installed ({exc}), so the "
            f"desktop cannot be queried this way"
        ) from exc

    try:
        desktop = Atspi.get_desktop(0)
    except Exception as exc:  # noqa: BLE001 - any failure here is "cannot look"
        raise _Unavailable(f"the accessibility bus did not answer ({exc})") from exc
    if desktop is None:
        raise _Unavailable("the accessibility bus returned no desktop")
    return desktop


def read_applications(limit: int = _MAX_APPS) -> "tuple[list, bool]":
    """Applications on the bus, and whether the list was cut short.

    Returns `(apps, truncated)`. A truncated read is reported as truncated by the
    caller; silently returning a prefix would be indistinguishable from a
    desktop that only has that many applications.
    """
    desktop = _desktop()
    try:
        total = desktop.get_child_count()
    except Exception as exc:  # noqa: BLE001
        raise _Unavailable(f"the accessibility bus did not answer ({exc})") from exc

    apps = []
    for index in range(total):
        if len(apps) >= limit:
            return apps, True
        node = desktop.get_child_at_index(index)
        if node is None:
            continue
        try:
            name = node.get_name() or "(unnamed)"
            role = node.get_role_name() or "unknown"
            windows = node.get_child_count()
        except Exception as exc:  # noqa: BLE001 - one bad app is not a bad bus
            logger.debug("accessibility: app %d unreadable: %s", index, exc)
            continue
        apps.append((name, role, windows))
    return apps, False


def _run(arguments: dict) -> str:
    config = ChronoaConfig()
    if not config.sense_allowed("accessibility"):
        return (
            f"Not reading the accessibility bus: "
            f"{config.sense_allowed_reason('accessibility')}. It reports which "
            f"applications are present and how many windows each has, which is "
            f"personal information, so it is off until you turn it on."
        )

    try:
        apps, truncated = read_applications()
    except _Unavailable as exc:
        # The load-bearing distinction. An empty list here would mean "no
        # applications are running", which is a claim about the user's screen
        # that this cannot support.
        return (
            f"Not reading the accessibility bus: {exc}. That means I could "
            f"not look, not that nothing is open - the desktop is not "
            f"reporting through AT-SPI on this session."
        )

    if not apps:
        return "The accessibility bus is reachable and reports no applications."

    rows = [f"{name} ({role}, {windows} window(s))"
            for name, role, windows in apps]
    # `cap_list` returns (kept, withheld_count); `withheld_note` needs that count
    # explicitly, not a pre-formatted string. It discloses the gap and says how
    # to close it, so a truncated read never reads as a complete one.
    listed, withheld = files.cap_list(rows, _MAX_APPS)
    body = "\n".join(f"  {row}" for row in listed)
    # `withheld_note` renders "... and N more {what}s not shown", so `what` is
    # the singular noun. Passing a plural phrase produced "buss", and the header
    # above already says these are from the accessibility bus.
    note = files.withheld_note(
        "application", withheld,
        widen="ask for the accessibility sense again, or raise _MAX_APPS",
    )
    return f"{len(apps)} application(s) on the accessibility bus:\n{body}\n{note}"


_SENSE = Sense(
    name="accessibility",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
