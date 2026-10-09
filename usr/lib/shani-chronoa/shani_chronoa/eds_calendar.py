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
    #: Which EDS source the event lives in, so an edit goes back to the same one.
    source_uid: str = ""
    #: Part of a repeating series. Editing one occurrence is not offered.
    recurring: bool = False


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

        def collect(icomp, istart, iend, *_args, _name=source.get_display_name(), _src=source.get_uid()):
            out.append(Event(start=_timet(istart), end=_timet(iend), summary=icomp.get_summary() or "(no title)",
                             location=icomp.get_location() or "", calendar=_name, uid=icomp.get_uid() or "",
                             all_day=bool(getattr(istart, "is_date", lambda: False)()),
                             source_uid=_src or "", recurring=_is_recurring(icomp, ICalGLib)))
            return True

        try:
            client.generate_instances_sync(int(start), int(end), None, collect)
        except Exception:  # noqa: BLE001
            continue
    return sorted(out, key=lambda e: e.start)


def describe(e: Event, now: Optional[float] = None) -> str:
    when = "all day" if e.all_day else time.strftime("%a %d %b %H:%M", time.localtime(e.start))
    return f"{when} - {e.summary}" + (f" @ {e.location}" if e.location else "") + f" ({e.calendar})"


# --- Writing -----------------------------------------------------------------
#
# Every write is read back before it is reported: a created event is fetched by
# the uid EDS handed out and its start compared, a moved one is fetched again,
# and a removed one must no longer be found. "Added to your calendar" followed
# by a calendar that does not have it is the failure worth refusing to ship.
#
# Times are written in UTC. Every calendar client shows a UTC time in the
# viewer's own zone, and it sidesteps building a VTIMEZONE for the local zone,
# which ICalGLib 3 and 4 do differently.


class CalendarWriteFailed(Exception):
    """The calendar answered, but the change did not happen (or did not hold)."""


def _is_recurring(icomp, ICalGLib) -> bool:
    try:
        kinds = ICalGLib.PropertyKind
        for kind in (kinds.RRULE_PROPERTY, kinds.RDATE_PROPERTY, kinds.RECURRENCEID_PROPERTY):
            if icomp.get_first_property(kind) is not None:
                return True
    except Exception:  # noqa: BLE001 - an unreadable component is not "recurring"
        return False
    return False


def _registry(EDataServer):
    try:
        return EDataServer.SourceRegistry.new_sync(None)
    except Exception as exc:  # noqa: BLE001
        raise CalendarUnavailable(f"the calendar registry did not answer ({exc})") from exc


def _connect(ECal, source, timeout: int):
    try:
        return ECal.Client.connect_sync(source, ECal.ClientSourceType.EVENTS, timeout, None)
    except Exception as exc:  # noqa: BLE001
        raise CalendarUnavailable(f"could not open the calendar {source.get_display_name()!r} ({exc})") from exc


def _utc(ICalGLib, t: float):
    return ICalGLib.Time.new_from_timet_with_zone(int(t), False, ICalGLib.Timezone.get_utc_timezone())


def _escape(text: str) -> str:
    """RFC 5545 TEXT escaping, so a title with a comma or newline stays one title.

    The semicolon needs its backslash for the same reason the comma does: it is
    a list separator in RFC 5545, and an unescaped one makes the property value
    malformed. It arrived here written as a backslash in front of the semicolon
    in a **non-raw** string literal, which Python reads as just the semicolon -
    a backslash in front of a character that is not itself escapable is
    dropped. So the escaping silently did nothing for any title containing one,
    and raised a SyntaxWarning on every import of this module. Both halves were
    real: the warning was visible, the broken output was not.
    """
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", ""))


def _fetch(client, uid: str):
    try:
        ok, comp = client.get_object_sync(uid, None, None)
    except Exception as exc:  # noqa: BLE001 - GLib.Error "Object not found"
        raise CalendarWriteFailed(f"the event {uid} is not in the calendar ({exc})") from exc
    if not ok or comp is None:
        raise CalendarWriteFailed(f"the event {uid} is not in the calendar")
    return comp


def default_calendar_name(timeout: int = 10) -> str:
    """Display name of the calendar new events go to."""
    _ECal, EDataServer, _ICal = _bindings()
    source = _registry(EDataServer).ref_default_calendar()
    if source is None:
        raise CalendarUnavailable("there is no default calendar")
    return source.get_display_name()


def create_event(summary: str, start: float, end: float, location: str = "",
                 description: str = "", timeout: int = 10) -> Event:
    """Write a new event to the default calendar and return it as read back."""
    ECal, EDataServer, ICalGLib = _bindings()
    source = _registry(EDataServer).ref_default_calendar()
    if source is None:
        raise CalendarUnavailable("there is no default calendar to add to")
    client = _connect(ECal, source, timeout)
    if client.is_readonly():
        raise CalendarWriteFailed(f"the default calendar {source.get_display_name()!r} is read-only")
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    fmt = "%Y%m%dT%H%M%SZ"
    lines = ["BEGIN:VEVENT", f"DTSTAMP:{stamp}",
             f"DTSTART:{time.strftime(fmt, time.gmtime(start))}",
             f"DTEND:{time.strftime(fmt, time.gmtime(end))}",
             f"SUMMARY:{_escape(summary)}"]
    if location:
        lines.append(f"LOCATION:{_escape(location)}")
    if description:
        lines.append(f"DESCRIPTION:{_escape(description)}")
    lines.append("END:VEVENT")
    comp = ICalGLib.Component.new_from_string("\r\n".join(lines) + "\r\n")
    if comp is None:
        raise CalendarWriteFailed("the event could not be built")
    try:
        ok, uid = client.create_object_sync(comp, ECal.OperationFlags(0), None)
    except Exception as exc:  # noqa: BLE001 - GLib.Error from the backend
        raise CalendarWriteFailed(f"the calendar refused the new event ({exc})") from exc
    if not ok or not uid:
        raise CalendarWriteFailed("the calendar did not return the new event's id")
    back = _fetch(client, uid)
    if abs(_timet(back.get_dtstart()) - int(start)) > 1:
        raise CalendarWriteFailed("the event was written but its start time did not read back as asked")
    return Event(start=_timet(back.get_dtstart()), end=_timet(back.get_dtend()),
                 summary=back.get_summary() or summary, location=back.get_location() or "",
                 calendar=source.get_display_name(), uid=uid, all_day=False,
                 source_uid=source.get_uid() or "", recurring=False)


def _client_for_event(event: Event, timeout: int):
    ECal, EDataServer, ICalGLib = _bindings()
    registry = _registry(EDataServer)
    source = registry.ref_source(event.source_uid) if event.source_uid else None
    if source is None:
        raise CalendarWriteFailed(f"the calendar {event.calendar!r} that holds this event is gone")
    client = _connect(ECal, source, timeout)
    if client.is_readonly():
        raise CalendarWriteFailed(f"the calendar {event.calendar!r} is read-only")
    return ECal, ICalGLib, client


def move_event(event: Event, new_start: float, new_end: float, timeout: int = 10) -> Event:
    """Reschedule a single (non-repeating) event; returns it as read back."""
    if event.recurring:
        raise CalendarWriteFailed("it is one of a repeating series")
    ECal, ICalGLib, client = _client_for_event(event, timeout)
    comp = _fetch(client, event.uid)
    comp.set_dtstart(_utc(ICalGLib, new_start))
    if comp.get_first_property(ICalGLib.PropertyKind.DURATION_PROPERTY) is None:
        comp.set_dtend(_utc(ICalGLib, new_end))
    try:
        ok = client.modify_object_sync(comp, ECal.ObjModType.THIS, ECal.OperationFlags(0), None)
    except Exception as exc:  # noqa: BLE001
        raise CalendarWriteFailed(f"the calendar refused the change ({exc})") from exc
    if not ok:
        raise CalendarWriteFailed("the calendar did not accept the change")
    back = _fetch(client, event.uid)
    if abs(_timet(back.get_dtstart()) - int(new_start)) > 1:
        raise CalendarWriteFailed("the change was sent but the event still reads back at its old time")
    return event._replace(start=_timet(back.get_dtstart()), end=_timet(back.get_dtend()))


def remove_event(event: Event, timeout: int = 10) -> None:
    """Delete a single (non-repeating) event and confirm it is gone."""
    if event.recurring:
        raise CalendarWriteFailed("it is one of a repeating series")
    ECal, _ICal, client = _client_for_event(event, timeout)
    try:
        ok = client.remove_object_sync(event.uid, None, ECal.ObjModType.THIS, ECal.OperationFlags(0), None)
    except Exception as exc:  # noqa: BLE001
        raise CalendarWriteFailed(f"the calendar refused to delete it ({exc})") from exc
    if not ok:
        raise CalendarWriteFailed("the calendar did not delete it")
    try:
        _fetch(client, event.uid)
    except CalendarWriteFailed:
        return  # not found any more: the success case
    raise CalendarWriteFailed("the delete was sent but the event is still there")
