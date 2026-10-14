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

import inspect
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class EventSink:
    """What the assistant emits while a turn runs. Every field optional.

    `on_tool_start(name, arguments)`, `on_tool_finish(name, arguments,
    result, ok, artifacts)` - the last being the files that dispatch
    **declared** it produced, never a path parsed out of `result` -
    `on_text(delta)` for streamed prose, `on_state(state)`
    for lifecycle edges ("thinking", "tool_call", "speaking"),
    `on_selection(record)` for what this round offered and why. A sink that
    does not implement a field simply does not get that event.
    """

    on_tool_start: Optional[Callable[[str, dict], None]] = None
    on_tool_finish: Optional[Callable[[str, dict, str, bool, tuple], None]] = None
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
    #: Fired once per round with the `tool_select.Selection` that round decided
    #: on - what was offered, with scores, and why. A tool card reads the record
    #: in force when it was built rather than re-asking a question whose answer
    #: may since have changed.
    on_selection: Optional[Callable[[object], None]] = None

    def __post_init__(self) -> None:
        """Refuse a field that is not a callable and not None, at construction.

        **The alternative is a TypeError at the call site, mid-turn.** A sink
        built from anything read at runtime - a config value, a JSON payload, a
        name a test spells wrong - would construct cleanly and then fail on the
        first event, which is the worst place to find a wiring mistake: the model
        has already run, the tool has already been called, and the exception
        arrives in the middle of producing a reply.

        `harness-study/laya-decision`'s `laya/hooks.py` states the rule this
        follows: *"a plain callable is only accepted on the single-event
        `on_predict_start=` parameters, because `hooks=` reads for method names
        and a bare function has none."* An API that introspects its inputs must
        refuse the shape it cannot introspect rather than accept it and never
        call it. Chronoa learned the same lesson in `skills/__init__.py`, where
        `run=lambda a: ...` passed `callable()` and failed on **every** call in a
        subprocess - the fix there was at the loader, not at the caller.

        Every construction site in the tree passes a real callable, so this is
        defence at the seam and not a behaviour change.
        """
        # **Arity as well as callability, because arity is what a widening
        # breaks.** `on_tool_finish` gained a fifth argument - the artifacts a
        # skill declared - and every check here passed: the field was callable,
        # the sink constructed, and the mismatch would have surfaced as a
        # `TypeError` in the middle of a turn. The sink is built once, before
        # any turn runs, so this is the only place the whole mismatch can still
        # be named.
        _ARITY = {"on_tool_start": 2, "on_tool_finish": 5, "on_text": 1,
                  "on_state": 1, "on_compaction": 1, "on_cloud_turn": 1}
        for field_name in ("on_tool_start", "on_tool_finish", "on_text",
                          "on_state", "on_compaction", "on_cloud_turn"):
            value = getattr(self, field_name)
            if value is not None and not callable(value):
                raise TypeError(
                    f"EventSink.{field_name} must be a callable or None, not "
                    f"{type(value).__name__}: {value!r:.40}. A sink is built "
                    f"once, before the turn runs; a value that is not callable "
                    f"would raise here instead of in the middle of a reply.")
            if value is None or not callable(value):
                continue
            wanted = _ARITY[field_name]
            try:
                params = inspect.signature(value).parameters
            except (TypeError, ValueError):
                continue          # a builtin: its signature cannot be read
            # **A `*args` subscriber accepts any arity, so it is not a
            # mismatch** - and the tree has two of them on purpose:
            # `test_a_callable_object_builds` builds a callable *object* whose
            # `__call__(self, *args)` exists to prove the guard is not
            # `isinstance(value, type(lambda: None))`, and
            # `test_a_broken_sink_cannot_break_a_turn` subscribes `explodes(*_args)`.
            # The first version of this check refused both, which is the same
            # error it was written to prevent - a guard that rejects correct
            # code teaches you to distrust it.
            if any(p.kind is inspect.Parameter.VAR_POSITIONAL
                   for p in params.values()):
                continue
            if any(p.kind is inspect.Parameter.VAR_KEYWORD
                   for p in params.values()):
                continue
            have = len(params)
            if have != wanted:
                raise TypeError(
                    f"EventSink.{field_name} is fired with {wanted} arguments "
                    f"but the callable given takes {have}: {value!r:.40}. A "
                    f"sink is built once, before any turn runs, so a mismatch "
                    f"here is a crash in the middle of a turn otherwise.")

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
        on_tool_finish=lambda n, a, r, ok, f: _forward("on_tool_finish", n, a, r, ok, f),
        on_text=lambda d: _forward("on_text", d),
        on_state=lambda st: _forward("on_state", st),
        on_compaction=lambda s: _forward("on_compaction", s),
        on_cloud_turn=lambda s: _forward("on_cloud_turn", s),
    )
