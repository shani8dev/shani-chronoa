"""Why a firing failed, as data rather than as an English prefix.

`_dispatch_now` has three ways to fail and until now all three reached
`_failed()` as one `reason: str`, so "the actuator raised", "the actuator never
ran" and "the actuator ran and verification failed" were three *wordings*. A
caller that wants to branch - a metrics sink, a retry policy, a UI that says
"the tool never started" instead of dumping `RuntimeError: ...` at the user -
has to string-match, and string-matching prose is the failure mode this repo
keeps recording.

Three things are asserted here rather than assumed:

- **The kinds are distinguishable without reading prose.** Three different
  dispatch seams produce three different `failure_kind` values, and the test
  compares the *values*, never the message.
- **The prose is unchanged.** `reason` still says what it always said; the kind
  is additive. A refactor that fixed the data and reworded the message would
  break the two tests in `test_trigger_event_types.py` that assert
  `RETRY_TERMINAL in result.reason`.
- **The percept path agrees with the event path.** It duplicated the same three
  doors and the same two prose strings, and the duplication is why hardening
  only one of them would leave two overlapping guards - the trap `AGENTS.md`
  warns about. Both engines are driven here for all three doors.
"""

import time

import pytest

from shani_chronoa import verification
from shani_chronoa.senses import Percept, SENSITIVITY_PRIVATE
from shani_chronoa.tools import DispatchResult
from shani_chronoa.triggers import (
    FAILURE_ACTUATOR_DID_NOT_RUN,
    FAILURE_ACTUATOR_RAISED,
    FAILURE_NONE,
    FAILURE_VERIFICATION_FAILED,
    MATCH_ANY,
    MATCH_SUBSTRING,
    RETRY_RETRYABLE,
    TRIGGER_CONTROL_KEY,
    DurableFingerprints,
    Event,
    EventEngine,
    EventRule,
    EventRuleStore,
    TriggerEngine,
    build_rule,
)

EVENT_GIT = "git"


class FakeConfig:
    """Consent stub, same shape as the one the two existing suites use."""

    def __init__(self):
        self._keys = {TRIGGER_CONTROL_KEY: True}

    def get_bool(self, key, default=False):
        return self._keys.get(key, default)

    def sense_allowed(self, sense):
        return True

    def sense_allowed_reason(self, sense):
        return ""


def _raising(*args, **kwargs):
    raise RuntimeError("the actuator is on fire")


def _never_ran(*args, **kwargs):
    """`ran=False`: the fourth row of `tools.DispatchResult`."""
    return DispatchResult(
        "unknown tool", verification.Verdict.UNVERIFIED, False, ""
    )


def _verification_failed(*args, **kwargs):
    """`ran` absent entirely - the seam shape most of the suite injects."""
    return verification.Result(verification.Verdict.FAILED, "the display never changed")


def _ok(*args, **kwargs):
    return verification.Result(verification.Verdict.VERIFIED, "it happened")


# --- the event path --------------------------------------------------------

def _event_engine(tmp_path, dispatch):
    return EventEngine(
        store=EventRuleStore(tmp_path / "event_rules.json"),
        config_factory=FakeConfig,
        dispatch=dispatch,
        fingerprint_store=DurableFingerprints(tmp_path / "prints.json"),
    )


def _armed(engine, **overrides):
    fields = {
        "name": "r", "event_type": EVENT_GIT, "source": "/tmp/repo",
        "actuator": "notify", "arguments": {"summary": "x"},
        "match_mode": MATCH_ANY, "debounce_seconds": 0.0,
        "cooldown_seconds": 0.0, "retry_policy": RETRY_RETRYABLE,
    }
    fields.update(overrides)
    rule = EventRule(**fields)
    engine.store().add(rule)
    return rule


def _event(fingerprint):
    return Event(
        kind=EVENT_GIT, subject="s", summary="HEAD moved", fingerprint=fingerprint,
        detail={}, created_at=0.0,
    )


@pytest.mark.parametrize("dispatch,expected", [
    (_raising, FAILURE_ACTUATOR_RAISED),
    (_never_ran, FAILURE_ACTUATOR_DID_NOT_RUN),
    (_verification_failed, FAILURE_VERIFICATION_FAILED),
])
def test_each_way_an_event_firing_can_fail_has_its_own_kind(
    tmp_path, dispatch, expected
):
    """The whole point: three doors, three values, no prose inspection."""
    engine = _event_engine(tmp_path, dispatch)
    rule = _armed(engine)
    # The baseline. Arming is inert; nothing changed yet.
    assert engine.feed(rule, _event("baseline"), now=1000.0).fired is False

    result = engine.feed(rule, _event("moved"), now=1000.0)

    assert result.fired is False
    assert result.failure_kind == expected


def test_the_three_kinds_are_distinguishable_from_each_other(tmp_path):
    """Not three spellings of one value, and not all defaulting to "".

    Driven as one comparison over three independently-produced evaluations, so
    a `failure_kind` that is constant (or always `FAILURE_NONE`) fails here even
    though each single-door assertion above would still pass.
    """
    seen = []
    for index, dispatch in enumerate((_raising, _never_ran, _verification_failed)):
        engine = _event_engine(tmp_path / f"e{index}", dispatch)
        rule = _armed(engine)
        engine.feed(rule, _event("baseline"), now=1000.0)
        seen.append(engine.feed(rule, _event("moved"), now=1000.0).failure_kind)

    assert len(set(seen)) == 3, seen
    assert FAILURE_NONE not in seen, "a failing firing reported no kind at all"


def test_a_successful_event_firing_has_no_failure_kind(tmp_path):
    """`FAILURE_NONE` is a value, not an absence: an unattended action that
    reported a failure kind it never had is how a metrics sink starts crying
    wolf."""
    engine = _event_engine(tmp_path, _ok)
    rule = _armed(engine)
    engine.feed(rule, _event("baseline"), now=1000.0)

    result = engine.feed(rule, _event("moved"), now=1000.0)

    assert result.fired is True
    assert result.failure_kind == FAILURE_NONE


def test_a_parked_rule_reports_its_kind_and_keeps_the_prose(tmp_path):
    """`failure_kind` is additive. The message that reaches the user through
    `manage_triggers`' `parked ({rule.parked_reason})` is unchanged."""
    engine = _event_engine(tmp_path, _raising)
    rule = _armed(engine)
    engine.feed(rule, _event("baseline"), now=1000.0)

    clock = 1000.0
    last = None
    for index in range(10):
        last = engine.feed(rule, _event(f"fp{index}"), now=clock)
        if rule.parked:
            break
        clock = max(clock + 120.0, (rule.retry_at or clock) + 1.0)

    assert rule.parked is True
    assert last.failure_kind == FAILURE_ACTUATOR_RAISED
    assert "RuntimeError" in rule.parked_reason, (
        "the human-facing reason lost the exception it has always carried"
    )


def test_the_ran_false_tolerance_survives(tmp_path):
    """A seam returning a bare `str` has no `.ran`. Treating that as a refusal
    would break every injected seam in the suite; treating it as a *success* is
    the other half of the same mistake. It must stay UNKNOWN, not False."""
    engine = _event_engine(tmp_path, lambda *a, **k: "it happened, plainly")
    rule = _armed(engine)
    engine.feed(rule, _event("baseline"), now=1000.0)

    result = engine.feed(rule, _event("moved"), now=1000.0)

    assert result.fired is True, (
        "a seam with no `ran` attribute was treated as `ran=False`, and every "
        "test that injects a plain string would now report a refusal"
    )
    assert result.failure_kind == FAILURE_NONE


# --- the percept path ------------------------------------------------------

def _percept(text):
    return Percept(
        sense="hearing", kind="utterance", content=text, created_at=time.time(),
        ttl_seconds=300, source="mic", sensitivity=SENSITIVITY_PRIVATE,
    )


def _percept_engine(dispatch):
    from shani_chronoa.tools import _HANDLER_FNS

    return TriggerEngine(config_factory=FakeConfig, dispatch=dispatch), _HANDLER_FNS


def _percept_rule(engine, skills):
    rule, problem = build_rule(
        name="doorbell", sense="hearing", match_mode=MATCH_SUBSTRING,
        actuator="notify", arguments={"summary": "Doorbell"}, substring="doorbell",
        skills=skills,
    )
    assert rule is not None, problem
    engine.store().add(rule)
    return rule


@pytest.mark.parametrize("dispatch,expected", [
    (_raising, FAILURE_ACTUATOR_RAISED),
    (_never_ran, FAILURE_ACTUATOR_DID_NOT_RUN),
    (_verification_failed, FAILURE_VERIFICATION_FAILED),
])
def test_each_way_a_percept_firing_can_fail_has_the_same_three_kinds(
    dispatch, expected
):
    """The percept path duplicated all three doors and both prose strings.

    It is also the path `manage_triggers` makes reachable from an LLM turn, so a
    kind present on only one of the two engines is a half-feature that reads as
    a whole one.
    """
    engine, skills = _percept_engine(dispatch)
    _percept_rule(engine, skills)

    (result,) = engine.evaluate(_percept("that was the doorbell"), now=1000.0)

    assert result.fired is False
    assert result.failure_kind == expected


def test_the_percept_path_prose_is_unchanged():
    """Both duplicated strings, pinned exactly as they read today."""
    engine, skills = _percept_engine(_verification_failed)
    _percept_rule(engine, skills)
    (failed,) = engine.evaluate(_percept("that was the doorbell"), now=1000.0)

    engine, skills = _percept_engine(_never_ran)
    _percept_rule(engine, skills)
    (refused,) = engine.evaluate(_percept("that was the doorbell"), now=1000.0)

    assert failed.reason == (
        "actuator ran but verification failed: the display never changed"
    )
    assert refused.reason == "the actuator did not run: unknown tool"


def test_a_denied_percept_rule_is_not_a_failure(tmp_path, monkeypatch):
    """A consent refusal is not one of the three doors and must not borrow one.

    `denied=True` already says it; a `failure_kind` that claimed otherwise would
    make a policy that retries on kind retry a rule the user just switched off.
    """
    class Denying(FakeConfig):
        def sense_allowed(self, sense):
            return False

        def sense_allowed_reason(self, sense):
            return "the hearing sense is turned off"

    from shani_chronoa.tools import _HANDLER_FNS

    monkeypatch.setenv("HOME", str(tmp_path))
    engine = TriggerEngine(config_factory=Denying, dispatch=_ok)
    rule, problem = build_rule(
        name="doorbell", sense="hearing", match_mode=MATCH_SUBSTRING,
        actuator="notify", arguments={"summary": "Doorbell"},
        substring="doorbell", skills=_HANDLER_FNS,
    )
    assert rule is not None, problem
    engine.store().add(rule)

    (result,) = engine.evaluate(_percept("that was the doorbell"), now=1000.0)

    assert result.denied is True
    assert result.fired is False
    assert result.failure_kind == FAILURE_NONE


def test_a_dry_run_is_not_a_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    engine, skills = _percept_engine(_raising)
    _percept_rule(engine, skills)

    (result,) = engine.evaluate(_percept("that was the doorbell"), dry_run=True)

    assert result.fired is False
    assert result.failure_kind == FAILURE_NONE, (
        "a dry run reports what would happen; reporting a failure kind for "
        "something that never ran makes `dry_run` indistinguishable from a "
        "genuinely broken actuator"
    )
