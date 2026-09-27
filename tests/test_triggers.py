"""A percept firing a whitelisted actuator, under consent, with cooldowns.

Every assertion here was established by driving the engine directly. The
subtle one is consent-before-cooldown: a rule that already fired sits in its
cooldown window, so checking cooldown first makes a switched-off sense look
identical to "nothing matched" - and a refusal nobody can see is the one
outcome a consent gate must never produce.
"""

import os
import time

import pytest

from shani_chronoa.senses import Percept, SENSITIVITY_PRIVATE
from shani_chronoa.triggers import (
    MATCH_SUBSTRING,
    TriggerEngine,
    build_rule,
)


class FakeConfig:
    def __init__(self, hearing: bool = True, input_control: bool = False):
        self._hearing = hearing
        self.input_control_enabled = input_control

    def sense_allowed(self, sense: str) -> bool:
        return self._hearing if sense == "hearing" else True

    def sense_allowed_reason(self, sense: str) -> str:
        return "" if self.sense_allowed(sense) else "the hearing sense is turned off"


@pytest.fixture
def calls():
    return []


@pytest.fixture
def engine(tmp_path, monkeypatch, calls):
    monkeypatch.setenv("HOME", str(tmp_path))
    return TriggerEngine(
        config_factory=FakeConfig,
        dispatch=lambda name, args, **kw: calls.append((name, args, kw.get("origin"))),
    )


def _percept(text: str) -> Percept:
    return Percept(
        sense="hearing", kind="utterance", content=text, created_at=time.time(),
        ttl_seconds=300, source="mic", sensitivity=SENSITIVITY_PRIVATE,
    )


def _rule(name="doorbell", actuator="notify", arguments=None, substring="doorbell"):
    from shani_chronoa.tools import _HANDLER_FNS
    rule, reason = build_rule(
        name=name, sense="hearing", match_mode=MATCH_SUBSTRING, actuator=actuator,
        arguments=arguments or {"summary": "Doorbell"}, substring=substring,
        skills=_HANDLER_FNS,
    )
    assert rule is not None, reason
    return rule


def test_a_rule_cannot_be_armed_for_a_non_whitelisted_actuator():
    from shani_chronoa.tools import _HANDLER_FNS

    rule, reason = build_rule(
        name="evil", sense="hearing", match_mode=MATCH_SUBSTRING, actuator="rm_rf_slash",
        arguments={}, substring="x", skills=_HANDLER_FNS,
    )

    assert rule is None
    assert "whitelisted" in reason


def test_a_matching_percept_fires_the_actuator_as_unattended(engine, calls):
    engine.store().add(_rule())

    results = engine.evaluate(_percept("that was the doorbell"))

    assert [r.fired for r in results] == [True]
    assert calls == [("notify", {"summary": "Doorbell"}, "unattended")], (
        "the audit log must be able to tell an unattended action from a user's"
    )


def test_a_percept_that_does_not_match_does_nothing(engine, calls):
    engine.store().add(_rule())

    assert engine.evaluate(_percept("what time is it")) == []
    assert calls == []


def test_cooldown_suppresses_an_immediate_re_fire(engine, calls):
    engine.store().add(_rule())
    engine.evaluate(_percept("doorbell"))
    calls.clear()

    assert engine.evaluate(_percept("doorbell")) == []
    assert calls == [], "a repeatedly-matching percept must not spam the actuator"


def test_a_disabled_sense_denies_rather_than_silently_skipping(tmp_path, calls):
    engine = TriggerEngine(
        config_factory=lambda: FakeConfig(hearing=False),
        dispatch=lambda *a, **k: calls.append(a),
    )
    engine.store().add(_rule())
    # Fire once so the rule is inside its cooldown: a denial must still be
    # reported, not hidden behind the cooldown window.
    engine.evaluate(_percept("doorbell"))
    calls.clear()

    results = engine.evaluate(_percept("doorbell"))

    assert [r.denied for r in results] == [True], "a consent denial must be reported"
    assert calls == []


def test_input_actuators_need_their_own_consent_key(engine, calls):
    engine.store().add(_rule(name="click", actuator="move_pointer",
                             arguments={"x": 1, "y": 2}, substring="click"))

    results = engine.evaluate(_percept("click here"))

    assert [r.denied for r in results] == [True]
    assert "input-control-enabled" in results[0].reason
    assert calls == []


def test_one_failing_rule_does_not_stop_the_others(tmp_path):
    seen = []

    def flaky(name, args, **kw):
        seen.append(name)
        if name == "notify":
            raise RuntimeError("actuator is down")

    engine = TriggerEngine(config_factory=FakeConfig, dispatch=flaky)
    engine.store().add(_rule(name="a", actuator="notify", substring="alpha"))
    engine.store().add(_rule(name="b", actuator="speak", substring="alpha"))

    results = engine.evaluate(_percept("alpha"))

    assert sorted(seen) == ["notify", "speak"], "the second rule must still be attempted"
    assert any(not r.fired and r.reason for r in results)


def test_a_disabled_rule_does_not_fire(engine, calls):
    rule = _rule()
    rule.enabled = False
    engine.store().add(rule)

    assert engine.evaluate(_percept("doorbell")) == []
    assert calls == []


class TestDefaultWhitelistPath:
    """`build_rule`'s default was broken in a way its own tests could not see.

    `discover_skills()` returns `(TOOLS, handlers)`. The default branch assigned
    that tuple to `skills` and then tested `actuator not in skills`, which
    compares against the two container objects and never a skill name - so every
    default-path call rejected every actuator. The suite never caught it
    because it passes `skills=` explicitly. It stayed invisible until the CLI
    (the first production caller) used the default.
    """

    def test_a_real_whitelisted_skill_is_accepted_by_default(self):
        from shani_chronoa.triggers import build_rule

        rule, problem = build_rule(
            name="r", sense="memory", match_mode="substring",
            actuator="notify", arguments={}, substring="x",
        )
        assert rule is not None, f"default path rejected a real skill: {problem}"
        assert rule.actuator == "notify"

    def test_a_genuinely_unknown_skill_is_still_rejected_by_default(self):
        from shani_chronoa.triggers import build_rule

        rule, problem = build_rule(
            name="r", sense="memory", match_mode="substring",
            actuator="definitely_not_a_skill", arguments={}, substring="x",
        )
        assert rule is None
        assert "not a whitelisted skill" in (problem or "")


class TestDryRun:
    """There was no way to ask "would this fire?" without firing it."""

    def test_dry_run_reports_without_dispatching(self, engine, calls):
        engine.store().add(_rule())
        percept = _percept("that was the doorbell")
        results = engine.evaluate(percept, dry_run=True)
        assert calls == [], "dry run dispatched a real action"
        assert any("would fire" in (r.reason or "") for r in results)

    def test_non_dry_run_still_dispatches(self, engine, calls):
        engine.store().add(_rule())
        engine.evaluate(_percept("that was the doorbell"))
        assert calls, "the normal path stopped dispatching"
