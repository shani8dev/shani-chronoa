"""The user's calendar, read from the desktop's own calendar service.

GNOME keeps calendars in Evolution Data Server (EDS) - every account added in
GNOME Online Accounts, plus the local "Personal" calendar - and exposes them
through the ECal/EDataServer GObject bindings, which the GNOME image ships.
Plasma's equivalent is Akonadi, which the Plasma image does not ship; there the
answer is "no calendar service", never "no events".

Recurring events are expanded into their occurrences (`generate_instances`),
so "the standup every weekday" shows up on the day it actually happens.
"""

from __future__ import annotations

import time
from typing import NamedTuple, Optional

UNAVAILABLE_NO_BINDINGS = "this desktop's calendar service is not here (no Evolution Data Server bindings)"


class Event(NamedTuple):
    start: float
    end: float
    summary: str
    location: str
    calendar: str
    uid: str
    all_day: bool


class CalendarUnavailable(Exception):
    """The calendar could not be read - which is not the same as an empty calendar."""


def _bindings():
    try:
        import gi
        gi.require_version("ECal", "2.0")
        gi.require_version("EDataServer", "1.2")
        for v in ("4.0", "3.0"):
            try:
                gi.require_version("ICalGLib", v)
                break
            except ValueError:
                continue
        from gi.repository import ECal, EDataServer, ICalGLib  # noqa: F401
        return ECal, EDataServer, ICalGLib
    except (ImportError, ValueError) as exc:
        raise CalendarUnavailable(UNAVAILABLE_NO_BINDINGS) from exc


def _timet(t) -> float:
    for name in ("as_timet", "as_timet_with_zone"):
        fn = getattr(t, name, None)
        if fn is None:
            continue
        try:
            return float(fn() if name == "as_timet" else fn(t.get_timezone()))
        except TypeError:
            continue
    return 0.0


def events_between(start: float, end: float, timeout: int = 10) -> "list[Event]":
    """Every occurrence overlapping [start, end) across enabled calendars, sorted by start."""
    ECal, EDataServer, ICalGLib = _bindings()
    try:
        registry = EDataServer.SourceRegistry.new_sync(None)
    except Exception as exc:  # noqa: BLE001 - GLib.Error and friends
        raise CalendarUnavailable(f"the calendar registry did not answer ({exc})") from exc
    out = []
    for source in registry.list_sources(EDataServer.SOURCE_EXTENSION_CALENDAR):
        if not source.get_enabled():
            continue
        try:
            client = ECal.Client.connect_sync(source, ECal.ClientSourceType.EVENTS, timeout, None)
        except Exception:  # noqa: BLE001 - one broken account must not hide the others
            continue

        def collect(icomp, istart, iend, *_args, _name=source.get_display_name()):
            out.append(Event(start=_timet(istart), end=_timet(iend), summary=icomp.get_summary() or "(no title)",
                             location=icomp.get_location() or "", calendar=_name, uid=icomp.get_uid() or "",
                             all_day=bool(getattr(istart, "is_date", lambda: False)())))
            return True

        try:
            client.generate_instances_sync(int(start), int(end), None, collect)
        except Exception:  # noqa: BLE001
            continue
    return sorted(out, key=lambda e: e.start)


def describe(e: Event, now: Optional[float] = None) -> str:
    when = "all day" if e.all_day else time.strftime("%a %d %b %H:%M", time.localtime(e.start))
    return f"{when} - {e.summary}" + (f" @ {e.location}" if e.location else "") + f" ({e.calendar})"
