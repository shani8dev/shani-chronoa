"""The -p output formatter: text on one side, JSON lines on the other.

Pure functions over an event dict and a stream. No D-Bus, no GTK, no decisions:
the orchestrator supplies the events, so a wrong filter here would leak tool
chatter into a pipe, and a wrong deny label here would approve something.
"""

import io
import json
import sys

from shani_chronoa.tui.noninteractive import (
    deny_label,
    emit_json,
    emit_reply,
    exit_code,
    refusal_notice,
)


APPROVAL = {
    "turn": "t1",
    "event": "approval",
    "id": "a1",
    "text": "Run it?",
    "options": ["Allow this once", "Deny"],
    "detail": "...",
    "kind": "confirm",
}


class FlushSpy(io.StringIO):
    def __init__(self):
        super().__init__()
        self.flushes = 0

    def flush(self):
        self.flushes += 1
        super().flush()


def test_emit_reply_writes_only_reply_text_verbatim():
    out = io.StringIO()
    emit_reply({"event": "reply", "text": "Hello, "}, out)
    emit_reply({"event": "reply", "text": "world."}, out)
    assert out.getvalue() == "Hello, world."


def test_emit_reply_ignores_every_other_event_kind():
    out = io.StringIO()
    for kind in ("tool", "state", "approval", "done", "error"):
        emit_reply({"event": kind, "text": "NOISE"}, out)
    assert out.getvalue() == ""


def test_emit_reply_defaults_to_stdout_and_flushes(monkeypatch):
    out = FlushSpy()
    monkeypatch.setattr(sys, "stdout", out)
    emit_reply({"event": "reply", "text": "streamed"})
    assert out.getvalue() == "streamed"
    assert out.flushes == 1


def test_emit_reply_tolerates_a_reply_without_text():
    out = io.StringIO()
    emit_reply({"event": "reply"}, out)
    assert out.getvalue() == ""


def test_emit_json_one_valid_compact_line_per_event():
    events = [
        {"event": "reply", "text": "café 日本語"},
        APPROVAL,
        {"event": "done", "turn": "t1"},
    ]
    out = io.StringIO()
    for event in events:
        emit_json(event, out)
    lines = out.getvalue().splitlines()
    assert len(lines) == len(events)
    assert [json.loads(line) for line in lines] == events
    assert lines == [
        json.dumps(event, ensure_ascii=False, separators=(",", ":")) for event in events
    ]
    assert "café 日本語" in lines[0]


def test_emit_json_defaults_to_stdout_and_flushes(monkeypatch):
    out = FlushSpy()
    monkeypatch.setattr(sys, "stdout", out)
    emit_json({"event": "reply", "text": "x"})
    assert json.loads(out.getvalue()) == {"event": "reply", "text": "x"}
    assert out.flushes == 1


def test_refusal_notice_shows_allow_and_options_on_one_line():
    err = io.StringIO()
    refusal_notice(APPROVAL, None, err)
    text = err.getvalue()
    assert text.count("\n") == 1
    assert "--allow" in text
    assert "Allow this once" in text
    assert "Deny" in text


def test_refusal_notice_says_a_given_allow_did_not_match():
    err = io.StringIO()
    refusal_notice(APPROVAL, "Yes please", err)
    text = err.getvalue()
    assert text.count("\n") == 1
    assert "did not match" in text
    assert "Yes please" in text
    assert "Allow this once" in text and "Deny" in text


def test_refusal_notice_defaults_to_stderr(monkeypatch):
    err = FlushSpy()
    monkeypatch.setattr(sys, "stderr", err)
    refusal_notice(APPROVAL, None)
    assert "--allow" in err.getvalue()


def test_deny_label_uses_allow_only_on_exact_match():
    assert deny_label(APPROVAL, "Deny") == "Deny"
    assert deny_label(APPROVAL, "deny") == "Deny"
    assert deny_label(APPROVAL, "Allow this once") == "Allow this once"


def test_deny_label_picks_a_deny_option_case_insensitively():
    for label in ("Deny", "no", "REJECT", "refuse"):
        assert deny_label({"options": ["Allow", label]}, None) == label


def test_deny_label_falls_back_to_deny_for_an_unmatched_allow():
    assert deny_label(APPROVAL, "Maybe") == "Deny"


def test_deny_label_returns_empty_when_nothing_matches():
    assert deny_label({"options": ["Allow", "Later"]}, "Maybe") == ""
    assert deny_label({}, None) == ""
    assert deny_label({"options": ["YES", "Sure"]}, None) == ""


def test_exit_code_is_zero_without_an_error():
    assert exit_code({"reply": "ok", "error": None, "turn": "t1", "events": []}) == 0
    assert exit_code({"error": ""}) == 0
    assert exit_code({}) == 0


def test_exit_code_is_one_for_a_truthy_error():
    assert exit_code({"error": "engine down"}) == 1
