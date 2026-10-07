"""Event sink: the seam between the agent core and any UI.

sayri's core (`core.py:34-89`) owns the loop and emits events into a sink;
every surface - GTK orb, tray, headless daemon - consumes the same events.
Chronoa's `Assistant.handle` has grown one callback at a time
(`on_tool_call`, `on_text`, `on_tool_result`), which is the same idea
without the name: each caller that wants events passes callbacks, and
there is no single place a new surface can subscribe. `EventSink` is the
small dataclass that names the set, with every field optional - a sink
that only cares about tool results does not need the rest.

Deliberately duplicative of the legacy callbacks: `Assistant.handle`
accepts both, legacy wining on conflict, so existing callers change
nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional


@dataclass
class EventSink:
    """What the assistant emits while a turn runs. Every field optional.

    `on_tool_start(name, arguments)`, `on_tool_finish(name, arguments,
    result, ok)`, `on_text(delta)` for streamed prose, `on_state(state)`
    for lifecycle edges ("thinking", "tool_call", "speaking"). A sink that
    does not implement a field simply does not get that event.
    """

    on_tool_start: Optional[Callable[[str, dict], None]] = None
    on_tool_finish: Optional[Callable[[str, dict, str, bool], None]] = None
    on_text: Optional[Callable[[str], None]] = None
    on_state: Optional[Callable[[str], None]] = None
    #: Fired when a request's context was condensed or truncated to fit the
    #: window, with one sentence saying so. cline's `CompactionRow` and
    #: OpenHands' `CondensationEvent` are the same event, shown.
    on_compaction: Optional[Callable[[str], None]] = None
    #: Fired when a turn is answered by a cloud provider rather than the local
    #: model, with the provider's display name. "This turn left the machine" is
    #: something a person has to be told, not something they have to infer from
    #: a settings key they may have forgotten.
    on_cloud_turn: Optional[Callable[[str], None]] = None

    def fire(self, what: str, *args) -> None:
        """Invoke one event, and never let a broken sink break the turn."""
        fn = getattr(self, what, None)
        if fn is None:
            return
        try:
            fn(*args)
        except Exception:  # noqa: BLE001 - a sink is audience, not machinery
            pass


def fan_in(*sinks: Optional[EventSink]) -> EventSink:
    """One sink that re-emits every event to every given one. Useful for
    wiring a GUI sink and a log sink to the same turn."""
    live = [s for s in sinks if s is not None]
    if not live:
        return EventSink()

    def _forward(what: str, *args):
        def _call(s: EventSink):
            s.fire(what, *args)
        for s in live:
            _call(s)

    return EventSink(
        on_tool_start=lambda n, a: _forward("on_tool_start", n, a),
        on_tool_finish=lambda n, a, r, ok: _forward("on_tool_finish", n, a, r, ok),
        on_text=lambda d: _forward("on_text", d),
        on_state=lambda st: _forward("on_state", st),
        on_compaction=lambda s: _forward("on_compaction", s),
        on_cloud_turn=lambda s: _forward("on_cloud_turn", s),
    )
