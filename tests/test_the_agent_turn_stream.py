"""A turn started from the shell gets its own approval channel.

**The wiring was three separate promises and none of them met.** `AgentService`
registered a resolver that no one installed; `ask_bridge` had a process-wide
`set_agent_resolver` that would have routed the *window's* questions to the
shell too; and `_AgentSink` was not an `EventSink`, so the first tool call of
every shell turn raised `AttributeError: 'fire'`. A test per promise is what
keeps the turn stream from being a shape that only looks wired.

The three properties worth asserting directly:

- A question asked while a turn runs reaches that turn's sink, and the answer
  `Approve` gives is the answer `ask()` returns - including from the executor
  thread the assistant actually runs tools on, which inherits the context.
- A question asked outside any turn still reaches the window's presenter, so
  installing the agent did not take the GTK app's own questions away.
- One turn's approval cannot resolve another's, and an approval nobody answers
  is closed rather than left to look answerable later.
"""

import contextvars
import json
import threading

from shani_chronoa import agent_service, ask_bridge

OPTIONS = ["Allow this once", "Deny"]


class _Params:
    def __init__(self, values):
        self._values = values

    def unpack(self):
        return self._values


class _Invocation:
    def __init__(self):
        self.value = None
        self.error = None

    def return_value(self, variant):
        self.value = variant.unpack() if variant is not None else None

    def return_dbus_error(self, name, message):
        self.error = (name, message)


class _AsyncBridgeStub:
    """Records the turn instead of running it, so `Turn` itself is under test."""

    def __init__(self):
        self.calls = []
        self.resolver_bound = None

    def run(self, coro, callback):
        self.resolver_bound = ask_bridge.has_agent_resolver()
        self.calls.append((coro, callback))
        getattr(coro, "close", lambda: None)()


class _CoroStub:
    def close(self):
        pass


class _AssistantStub:
    def __init__(self):
        self.calls = []

    def handle(self, text, sink=None):
        # Regular def, not async: `Turn` only *creates* the coroutine, and a
        # stub that awaited itself would never record the sink it was given.
        self.calls.append((text, sink))
        return _CoroStub()


class _AppStub:
    def __init__(self):
        self.assistant = _AssistantStub()
        self._async = _AsyncBridgeStub()


def _service():
    return agent_service.AgentService(app=None)


def _sink(service, turn="turn-1"):
    """A sink whose emissions are collected instead of sent to the bus."""
    sink = agent_service._AgentSink(service, turn)
    sink.events = []
    sink._emit = lambda event, **kw: sink.events.append({"event": event, **kw})
    return sink


def _approve(service, turn, decision=OPTIONS[0]):
    invocation = _Invocation()
    service.Approve(None, None, None, None, "Approve",
                    _Params((turn, decision)), invocation)
    assert invocation.error is None, invocation.error
    return invocation.value[0]


def _wait_for(predicate, timeout=5.0):
    """Spin until `predicate()` is true, or fail the test at the deadline."""
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    raise AssertionError("the event never arrived")


def test_turn_starts_the_assistant_with_a_bound_sink():
    app = _AppStub()
    service = agent_service.AgentService(app)
    invocation = _Invocation()

    service.Turn(None, None, None, None, "Turn",
                 _Params(("tidy my desktop",)), invocation)

    assert invocation.error is None, invocation.error
    turn_id = invocation.value[0]
    assert turn_id.startswith("turn-")
    text, sink = app.assistant.calls[0]
    assert text == "tidy my desktop"
    # The sink is the turn's id and the resolver was bound while the coroutine
    # was scheduled - the two halves that make `Approve` reach the right turn.
    assert sink.turn_id == turn_id
    assert app._async.resolver_bound is True


def test_a_turn_with_no_text_is_refused_before_it_starts():
    app = _AppStub()
    service = agent_service.AgentService(app)
    invocation = _Invocation()

    service.Turn(None, None, None, None, "Turn", _Params(("   ",)), invocation)

    assert invocation.error is not None
    assert invocation.error[0].endswith("InvalidArgs")
    assert app.assistant.calls == []


def test_a_bound_turn_counts_as_someone_to_ask():
    sink = _sink(_service())
    previous = ask_bridge._presenter
    ask_bridge.set_presenter(None)
    try:
        assert ask_bridge.has_any_presenter() is False
        with ask_bridge.turn_resolver(sink.ask):
            assert ask_bridge.has_any_presenter() is True
        assert ask_bridge.has_any_presenter() is False
    finally:
        ask_bridge.set_presenter(previous)


def test_a_question_on_a_tool_thread_reaches_its_turn_and_returns_the_answer():
    service = _service()
    sink = _sink(service)
    answers = []

    def ask_on_the_worker():
        answers.append(ask_bridge.ask("Move 14 files?", OPTIONS, timeout=5))

    def turn():
        # `Assistant.handle` copies the context into the executor it runs tools
        # on; this is that copy, without a model or a tool loop in the way.
        with ask_bridge.turn_resolver(sink.ask):
            context = contextvars.copy_context()
        worker = threading.Thread(target=context.run, args=(ask_on_the_worker,))
        worker.start()
        _wait_for(lambda: any(e["event"] == "approval" for e in sink.events))
        assert _approve(service, "turn-1") is True
        worker.join(5)
        assert not worker.is_alive(), "the tool thread never woke"

    turn()

    assert answers == [OPTIONS[0]]
    approval = next(e for e in sink.events if e["event"] == "approval")
    assert approval["id"] == "turn-1"
    assert approval["text"] == "Move 14 files?"
    assert approval["options"] == OPTIONS


def test_a_question_outside_a_turn_still_goes_to_the_window():
    service = _service()
    sink = _sink(service)
    asked = []

    def presenter(question, options):
        asked.append((question, list(options)))
        done, resolve = ask_bridge.make_event()
        resolve("No")
        return done

    previous = ask_bridge._presenter
    ask_bridge.set_presenter(presenter)
    try:
        assert ask_bridge.ask("Window's question?", ["Yes", "No"]) == "No"
    finally:
        ask_bridge.set_presenter(previous)

    assert asked == [("Window's question?", ["Yes", "No"])]
    assert sink.events == [], "the window's question was drawn in the shell"


def test_an_unanswered_approval_is_closed_and_a_late_answer_is_refused():
    service = _service()
    sink = _sink(service)

    with ask_bridge.turn_resolver(sink.ask):
        assert ask_bridge.ask("Move 14 files?", OPTIONS, timeout=0.05) == ""

    assert _approve(service, "turn-1") is False
    assert [e["event"] for e in sink.events] == ["approval", "approval_closed"]
    assert sink.events[-1]["id"] == "turn-1"


def test_an_answered_approval_cannot_be_answered_again():
    service = _service()
    sink = _sink(service)
    waiting, _ = sink.ask("Move 14 files?", OPTIONS)

    assert _approve(service, "turn-1") is True
    assert waiting.is_set()
    # The wait is gone once it is answered; a second click is a late click,
    # not a second decision.
    assert _approve(service, "turn-1", OPTIONS[1]) is False

    waiting.chronoa_closed()
    assert [e["event"] for e in sink.events] == ["approval"]


def test_one_turns_approval_cannot_answer_another():
    service = _service()
    first, second = _sink(service, "turn-a"), _sink(service, "turn-b")
    waiting_a, _ = first.ask("first?", OPTIONS)
    waiting_b, _ = second.ask("second?", OPTIONS)

    assert _approve(service, "turn-b") is True
    assert waiting_b.is_set()
    assert not waiting_a.is_set()
    assert waiting_a.wait(0.05) is False

    waiting_a.chronoa_closed()
    assert [e["event"] for e in second.events] == ["approval"]


def test_a_tool_call_becomes_one_pill_and_one_result():
    service = _service()
    sink = _sink(service)

    # `fire` is what the assistant calls; a sink without it raised on the
    # first tool call of the first shell turn.
    sink.fire("on_tool_start", "read_file", {"path": "/tmp/notes.txt"})
    sink.fire("on_tool_finish", "read_file", {"path": "/tmp/notes.txt"},
              "two lines", True, ())

    pill, result = sink.events
    assert pill["event"] == "tool"
    assert "read_file" in pill["text"] and "/tmp/notes.txt" in pill["text"]
    assert result["event"] == "tool_done"
    assert result["ok"] is True
    assert result["text"] == "two lines"
    assert json.dumps(result)  # the wire carries JSON, not tuples or objects
