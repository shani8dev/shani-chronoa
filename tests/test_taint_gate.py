"""A page may ask; only a person may authorise a change.

`provenance.fence` makes untrusted text *look* like data to the model. That is
a statement in prose, and a model can be talked past prose. This file covers the
half that does not depend on the model's cooperation: a turn that has read a
page, a file or a tool's output may still read and talk, but a call that can
delete or overwrite goes to the person through `approvals.ask`, and is refused
unless they explicitly allow it.

The tests assert the gate, not that prompt injection is solved. What is
verifiable is that the decision no longer rests on the model noticing a fence.
"""

import contextvars
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import capabilities, provenance, tools  # noqa: E402
from shani_chronoa.tool_tracking import ORIGIN_UNATTENDED, ORIGIN_USER  # noqa: E402


@pytest.fixture(autouse=True)
def clean_taint():
    """Every test starts and ends like a fresh turn."""
    provenance.clear_untrusted()
    yield
    provenance.clear_untrusted()


class Asked:
    """Stands in for the desktop's notification question."""

    def __init__(self, answer):
        self.answer = answer
        self.questions = []

    def __call__(self, title, body, seconds=None):
        self.questions.append((title, body, seconds))
        return self.answer


def _asker(monkeypatch, answer):
    asked = Asked(answer)
    monkeypatch.setattr(tools, "_ask_person", asked)
    return asked


# ------------------------------------------------------------ the clean turn

def test_a_turn_that_read_nothing_is_never_questioned(monkeypatch):
    """The gate must not exist for ordinary work, or it teaches people to dismiss it."""
    asked = _asker(monkeypatch, "deny")
    assert tools._taint_refuses("delete_file", {"path": "~/x"}, ORIGIN_USER) is None
    assert asked.questions == []


def test_a_new_message_forgets_what_the_last_turn_read():
    provenance.note_untrusted("web")
    assert provenance.turn_is_tainted()
    provenance.clear_untrusted()
    assert provenance.untrusted_since_user() == ()


def test_the_same_source_is_recorded_once():
    provenance.note_untrusted("web")
    provenance.note_untrusted("web")
    provenance.note_untrusted("file")
    assert provenance.untrusted_since_user() == ("web", "file")


def test_taint_does_not_leak_into_another_context():
    """Turns and subagents are separate contexts; a global would share this."""
    provenance.note_untrusted("web")
    assert contextvars.Context().run(provenance.untrusted_since_user) == ()
    assert provenance.untrusted_since_user() == ("web",)


# ------------------------------------------------------------ what is gated

@pytest.mark.parametrize("answer", ["deny", "timeout", "unavailable", "", None])
def test_only_an_explicit_allow_lets_the_change_through(monkeypatch, answer):
    _asker(monkeypatch, answer)
    provenance.note_untrusted("web")
    refused = tools._taint_refuses("delete_file", {"path": "~/x"}, ORIGIN_USER)
    assert refused is not None and refused.ran is False
    assert refused.text.startswith("Not run.")
    assert "web" in refused.text
    assert "do not retry" in refused.text.lower()


def test_the_question_names_the_tool_and_where_it_came_from(monkeypatch):
    asked = _asker(monkeypatch, "deny")
    provenance.note_untrusted("a web page")
    tools._taint_refuses("delete_file", {}, ORIGIN_USER)
    title, body, _seconds = asked.questions[0]
    assert "delete_file" in title
    assert "a web page" in body
    assert "not from you" in body


def test_a_person_can_allow_the_one_change(monkeypatch):
    _asker(monkeypatch, "allow")
    provenance.note_untrusted("web")
    assert tools._taint_refuses("delete_file", {}, ORIGIN_USER) is None


def test_a_write_that_carries_no_destructive_key_is_still_gated(monkeypatch):
    """`write_text_file` is not in a destructive key set, and can still overwrite."""
    _asker(monkeypatch, "deny")
    provenance.note_untrusted("file")
    assert capabilities.effect_of("write_text_file") != capabilities.EFFECT_DESTRUCTIVE
    assert tools._taint_refuses("write_text_file", {"path": "~/notes"}, ORIGIN_USER) is not None


# ------------------------------------------------------------ what is not

def test_reading_after_reading_is_still_allowed(monkeypatch):
    asked = _asker(monkeypatch, "deny")
    provenance.note_untrusted("web")
    for name in ("read_text_file", "web_search", "clock"):
        assert tools._taint_refuses(name, {}, ORIGIN_USER) is None
    assert asked.questions == []


def test_talking_after_reading_is_still_allowed(monkeypatch):
    asked = _asker(monkeypatch, "deny")
    provenance.note_untrusted("web")
    for name in sorted(capabilities.TALKS_ONLY):
        assert tools._taint_refuses(name, {}, ORIGIN_USER) is None
    assert asked.questions == []


def test_undo_is_the_remedy_and_is_never_gated(monkeypatch):
    """Requiring consent to *reverse* an injected change would be backwards."""
    asked = _asker(monkeypatch, "deny")
    provenance.note_untrusted("web")
    assert tools._taint_refuses("undo_last_change", {}, ORIGIN_USER) is None
    assert asked.questions == []


def test_an_unattended_rule_keeps_its_own_gate(monkeypatch):
    """A trigger has no turn to be tainted by, and already asked before firing."""
    asked = _asker(monkeypatch, "deny")
    provenance.note_untrusted("web")
    assert tools._taint_refuses("delete_file", {}, ORIGIN_UNATTENDED) is None
    assert asked.questions == []


# ------------------------------------------------------------ it is in front of the body

def test_the_gate_sits_in_front_of_execution(monkeypatch):
    ran = []

    def fake_inner(name, arguments, by_reference=False, origin=ORIGIN_USER):
        ran.append(name)
        return tools.DispatchResult("ran", tools.verification.Verdict.VERIFIED, True)

    monkeypatch.setattr(tools, "_dispatch_inner", fake_inner)
    for name in ("_reaction_refuses", "_guardrail_refuses", "_advise",
                 "_light_hands", "_unlight_hands"):
        monkeypatch.setattr(tools, name, lambda *a, **k: None)
    _asker(monkeypatch, "deny")
    provenance.note_untrusted("web")

    refused = tools._dispatch("delete_file", {"path": "~/x"})
    assert refused.ran is False and ran == []


def test_an_allowed_call_reaches_the_body(monkeypatch):
    ran = []

    def fake_inner(name, arguments, by_reference=False, origin=ORIGIN_USER):
        ran.append(name)
        return tools.DispatchResult("ran", tools.verification.Verdict.VERIFIED, True)

    monkeypatch.setattr(tools, "_dispatch_inner", fake_inner)
    for name in ("_reaction_refuses", "_guardrail_refuses", "_advise",
                 "_light_hands", "_unlight_hands"):
        monkeypatch.setattr(tools, name, lambda *a, **k: None)
    _asker(monkeypatch, "allow")
    provenance.note_untrusted("web")

    result = tools._dispatch("delete_file", {"path": "~/x"})
    assert result.ran is True and ran == ["delete_file"]


# ------------------------------------------------------------ fence and note are one act

def test_fencing_a_result_is_what_marks_the_turn():
    fenced = provenance.fence_and_note("some page text", "web")
    assert fenced.fenced
    assert provenance.untrusted_since_user() == ("web",)


def test_plain_fence_stays_pure_for_history_reloads():
    """`_fence_restored` re-fences stored text; that must not taint a new turn."""
    fenced = provenance.fence("some page text", "web")
    assert fenced.fenced
    assert provenance.untrusted_since_user() == ()
