"""The Agent's additive turn options and cancellation.

TurnWith answers three gaps the shell's panel and a terminal client both hit:
attaching a file, continuing/starting a conversation, and a one-shot answer
that leaves no transcript. Cancel stops a turn that is still running. Plain
Turn is left alone; these are additive methods, not a rewrite of it.

The XML is part of the test because the shell copies it: a method present in
`AgentService` but absent from the introspection XML fails as UnknownMethod on
the wire, which a module-level unit test would never see.
"""

import json

from shani_chronoa import agent_service, conversation_store
from shani_chronoa.agent_contract import XML


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


class _Coro:
    def close(self):
        pass


class _Future:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True
        return True


class _Async:
    def __init__(self, future=None):
        self.future = future or _Future()
        self.calls = []

    def run(self, coro, callback):
        self.calls.append((coro, callback))
        getattr(coro, "close", lambda: None)()
        return self.future


class _Assistant:
    def __init__(self):
        self.calls = []
        self.switched = []

    def handle(self, text, sink=None):
        self.calls.append((text, sink))
        return _Coro()

    def switch_session(self, path):
        self.switched.append(path)
        return 0


class _App:
    def __init__(self):
        self.assistant = _Assistant()
        self._async = _Async()
        self.llm = object()
        self.llm_patch_targets = None


def _service(app=None):
    return agent_service.AgentService(app or _App())


def _call(service, method, *args):
    invocation = _Invocation()
    getattr(service, method)(None, None, None, None, method, _Params(args), invocation)
    return invocation


def test_contract_xml_has_the_two_new_methods():
    from xml.etree import ElementTree

    node = ElementTree.fromstring(XML.replace("{IFACE}", "dev.shani.chronoa.Agent"))
    methods = {
        m.get("name"): [
            (a.get("name"), a.get("type"), a.get("direction")) for a in m.iter("arg")
        ]
        for m in node.iter("method")
    }
    assert methods["Turn"] == [("text", "s", "in"), ("turn_id", "s", "out")]
    assert methods["TurnWith"] == [
        ("text", "s", "in"),
        ("options_json", "s", "in"),
        ("turn_id", "s", "out"),
    ]
    assert methods["Cancel"] == [("turn_id", "s", "in"), ("ok", "b", "out")]
    assert "Event" in {s.get("name") for s in node.iter("signal")}


def test_turnwith_rejects_bad_options_without_starting_a_turn():
    app = _App()
    service = _service(app)
    assert (
        _call(service, "TurnWith", "hello", "not json").error[0].endswith("InvalidArgs")
    )
    assert _call(service, "TurnWith", "hello", "[]").error[0].endswith("InvalidArgs")
    assert (
        _call(service, "TurnWith", "hello", '{"paths": "x"}')
        .error[0]
        .endswith("InvalidArgs")
    )
    assert _call(service, "TurnWith", "   ", "{}").error[0].endswith("InvalidArgs")
    assert app.assistant.calls == []


def test_turnwith_attaches_files_using_the_engine_block(tmp_path, monkeypatch):
    note = tmp_path / "note.txt"
    note.write_text("hello", encoding="utf-8")
    app = _App()
    service = _service(app)
    inv = _call(service, "TurnWith", "read my note", json.dumps({"paths": [str(note)]}))
    assert inv.error is None, inv.error
    text, _sink = app.assistant.calls[0]
    assert text.startswith("read my note")
    assert "[Attached files" in text
    assert str(note) in text


def test_turnwith_can_start_a_fresh_conversation(tmp_path, monkeypatch):
    monkeypatch.setattr(conversation_store, "session_dir", lambda: tmp_path)
    app = _App()
    service = _service(app)
    inv = _call(service, "TurnWith", "hello", json.dumps({"conversation": "new"}))
    assert inv.error is None, inv.error
    assert len(app.assistant.switched) == 1
    assert app.assistant.switched[0].parent == tmp_path
    assert (
        app.assistant.switched[0].exists() is False
    )  # the session file, not the index


def test_turnwith_can_resume_a_saved_conversation(tmp_path, monkeypatch):
    monkeypatch.setattr(conversation_store, "session_dir", lambda: tmp_path)
    sid = conversation_store.new_session(tmp_path)
    conversation_store.rename(tmp_path, sid, "Holiday plans")
    app = _App()
    service = _service(app)
    inv = _call(
        service, "TurnWith", "continue", json.dumps({"conversation": "Holiday plans"})
    )
    assert inv.error is None, inv.error
    assert app.assistant.switched == [conversation_store.session_path(tmp_path, sid)]
    assert (
        _call(
            service, "TurnWith", "x", json.dumps({"conversation": "no such chat"})
        ).error
        is not None
    )


def test_turnwith_ephemeral_uses_a_throwaway_assistant():
    app = _App()
    service = _service(app)
    inv = _call(service, "TurnWith", "one shot", json.dumps({"ephemeral": True}))
    assert inv.error is None, inv.error
    # The real assistant is untouched; a spare with session_path=None handled it.
    assert app.assistant.calls == []
    assert len(service._futures) == 1


def test_cancel_stops_a_running_turn_and_closes_its_approval():
    app = _App()
    service = _service(app)
    inv = _call(service, "Turn", "do something")
    turn_id = inv.value[0]
    # Register a pending approval exactly as _AgentSink.ask does.
    import threading

    done = threading.Event()
    answers = []
    service.register(turn_id, done, lambda answer: (answers.append(answer), done.set()))
    emitted = []
    service.emit_event = lambda payload: emitted.append(json.loads(payload))

    cancelled = _call(service, "Cancel", turn_id)
    assert cancelled.error is None, cancelled.error
    assert cancelled.value == (True,)
    assert app._async.future.cancelled is True
    assert done.is_set() and answers == [""]
    assert [e["event"] for e in emitted] == ["notice", "done"]
    assert all(e["turn"] == turn_id for e in emitted)
    assert turn_id not in service._futures


def test_cancel_of_an_unknown_turn_is_a_plain_false():
    service = _service()
    assert _call(service, "Cancel", "turn-nope").value == (False,)


def test_plain_turn_still_uses_the_open_conversation():
    app = _App()
    service = _service(app)
    assert _call(service, "Turn", "ordinary").error is None
    assert app.assistant.calls[0][0] == "ordinary"
    assert app.assistant.switched == []
