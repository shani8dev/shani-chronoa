"""A percept firing a whitelisted actuator, under consent, with cooldowns.

Every assertion here was established by driving the engine directly. The
subtle one is consent-before-cooldown: a rule that already fired sits in its
cooldown window, so checking cooldown first makes a switched-off sense look
identical to "nothing matched" - and a refusal nobody can see is the one
outcome a consent gate must never produce.
"""

import ast
import os
import time
from pathlib import Path

import pytest

from shani_chronoa import verification
from shani_chronoa.senses import Percept, SENSITIVITY_PRIVATE
from shani_chronoa.triggers import (
    MATCH_SUBSTRING,
    TRIGGER_CONTROL_KEY,
    TriggerEngine,
    build_rule,
)


class FakeConfig:
    """Consent stub.

    `get_bool` was missing until 2026-09-30, and its absence is worth recording
    for the same reason it is worth recording in `test_trigger_event_types.py`:
    `TriggerEngine._consent` now asks the config for `trigger-control-enabled`
    through the same `get_bool(key, default)` accessor every other consent key
    in this codebase uses, and a double implementing only `sense_allowed` and
    `input_control_enabled` raises `AttributeError` on every firing path at once
    rather than producing one honest failure. It returns True so the *other*
    refusals are what a test here is exercising; a test about the arming key
    passes `trigger_control=False`.
    """

    def __init__(self, hearing: bool = True, input_control: bool = False,
                 trigger_control: bool = True):
        self._hearing = hearing
        self.input_control_enabled = input_control
        self._keys: dict = {TRIGGER_CONTROL_KEY: trigger_control}

    def get_bool(self, key: str, default: bool = False) -> bool:
        # Mirrors `ChronoaConfig.get_bool`: an undeclared key yields the supplied
        # default, so a double that knows nothing about a key denies.
        return self._keys.get(key, default)

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


class TestAFailedActionDoesNotGetToLookSuccessful:
    """The gap: `execute_tool` returns a string, and the FAILED verdict inside
    it was only ever prose. The engine took "no exception" as success, so a
    rule whose actuator ran and did nothing was reported fired and then parked
    in a cooldown window - one silent failure per rule per window, with nothing
    in the FireResult saying otherwise.
    """

    @pytest.fixture
    def failing_engine(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        calls = []

        def dispatch(name, args, **kw):
            calls.append(name)
            return verification.Result(
                verification.Verdict.FAILED, "the display never changed"
            )

        engine = TriggerEngine(config_factory=FakeConfig, dispatch=dispatch)
        return engine, calls

    def test_a_failed_post_condition_is_not_reported_as_fired(self, failing_engine):
        engine, _ = failing_engine
        engine.store().add(_rule())
        (result,) = engine.evaluate(_percept("the doorbell is ringing"))

        assert result.fired is False, (
            "the actuator ran but its post-condition says the effect is not "
            "there; reporting this as fired is the bug"
        )
        assert result.verdict is verification.Verdict.FAILED
        assert "the display never changed" in result.reason

    def test_a_failed_action_does_not_consume_the_cooldown(self, failing_engine):
        """Otherwise the rule goes quiet for its whole cooldown having done
        nothing, and one transient failure looks exactly like success."""
        engine, _ = failing_engine
        engine.store().add(_rule())
        rule = engine.store().all()[0]
        assert rule.last_fired_at is None

        engine.evaluate(_percept("the doorbell is ringing"), now=1000.0)
        assert rule.last_fired_at is None, (
            "a failed action must not start the cooldown clock"
        )
        # Still due, so the next matching percept retries rather than the rule
        # silently disabling itself.
        assert rule.due(1000.1) is True

    def test_the_failing_actuator_is_still_retried_on_the_next_percept(self, failing_engine):
        engine, calls = failing_engine
        engine.store().add(_rule())
        engine.evaluate(_percept("the doorbell is ringing"), now=1000.0)
        engine.evaluate(_percept("the doorbell is ringing"), now=1001.0)
        assert len(calls) == 2, "a failure must not suppress the retry"

    def test_a_successful_action_still_records_and_still_cools_down(self, tmp_path, monkeypatch):
        """The fix must not have turned every rule into a retry loop."""
        monkeypatch.setenv("HOME", str(tmp_path))
        engine = TriggerEngine(
            config_factory=FakeConfig,
            dispatch=lambda name, args, **kw: verification.Result(
                verification.Verdict.VERIFIED, "the display is at 40%"
            ),
        )
        engine.store().add(_rule())
        rule = engine.store().all()[0]

        (result,) = engine.evaluate(_percept("the doorbell is ringing"), now=1000.0)
        assert result.fired is True
        assert result.verdict is verification.Verdict.VERIFIED
        assert rule.last_fired_at == 1000.0
        assert rule.due(1001.0) is False, "cooldown must still apply on success"

    def test_a_plain_string_dispatch_is_still_accepted(self, tmp_path, monkeypatch):
        """`dispatch` is a public injection seam and existing callers pass a
        lambda returning None or a string. Not breaking them is the point."""
        monkeypatch.setenv("HOME", str(tmp_path))
        calls = []
        engine = TriggerEngine(
            config_factory=FakeConfig,
            dispatch=lambda name, args, **kw: calls.append(name),
        )
        engine.store().add(_rule())
        (result,) = engine.evaluate(_percept("the doorbell is ringing"), now=1000.0)
        assert result.fired is True
        assert calls == ["notify"]

    def test_a_dispatch_returning_the_failed_marker_is_still_caught(self, tmp_path, monkeypatch):
        """A dispatch that returns a raw string carrying the FAILED marker - the
        shape `execute_tool` itself returns - must not be read as success."""
        monkeypatch.setenv("HOME", str(tmp_path))
        engine = TriggerEngine(
            config_factory=FakeConfig,
            dispatch=lambda name, args, **kw: "Done! (VERIFICATION FAILED: nothing changed)",
        )
        engine.store().add(_rule())
        rule = engine.store().all()[0]
        (result,) = engine.evaluate(_percept("the doorbell is ringing"), now=1000.0)
        assert result.fired is False
        assert rule.last_fired_at is None


# ===========================================================================
# The gates that were missing on the percept path (2026-09-30)
# ===========================================================================
#
# Four defects, found by a security review and each reproduced against the real
# code before it was fixed. They share one shape: the *event* half of this
# module had the guard and the percept half did not, so every one of them is
# invisible to a test that only exercises the path that already worked.
#
# Every assertion here names what it is holding the code to, because a refusal
# that does not name the switch a user has to flip is indistinguishable from a
# bug - the property `TriggerEngine.evaluate` already documents for checking
# consent *before* the cooldown.

from shani_chronoa.tools import _HANDLER_FNS, ToolOutcome
from shani_chronoa.triggers import (
    MAX_ARGUMENTS,
    MAX_ARGUMENT_VALUE_CHARS,
    MATCH_ANY,
    MATCH_KEYWORDS,
    MATCH_SUBSTRING,
    MIN_COOLDOWN_SECONDS,
    TriggerRule,
    _DESTRUCTIVE_ACTUATORS,
    _UNATTENDED_WRITE_ACTUATORS,
    RuleStore,
)


class GatedConfig(FakeConfig):
    """`FakeConfig` with the arming key a test can turn off."""

    def __init__(self, trigger_control: bool = True, **kwargs):
        super().__init__(**kwargs)
        self._keys[TRIGGER_CONTROL_KEY] = trigger_control


class TestTheArmingKeyGatesFiringAndNotOnlyArming:
    """`trigger-control-enabled` read per percept, on the percept path too.

    `EventEngine._consent` gained this check on 2026-09-30. This one, the
    function the percept half runs through, did not have it then and had no
    equivalent at all - so the switch that gates arming a rule gated nothing
    once the rule was armed.

    Latent rather than live, and the tests say so too: `fire_all` and
    `evaluate_many` have one caller (`shani-chronoa-sense trigger run`) and
    `AmbientScheduler` drives the event engine only. It is a gate that cannot
    be used to stop the thing it gates, and it becomes live the moment the
    scheduler is wired to percept rules.
    """

    def test_a_shut_arming_key_refuses_to_fire(self, tmp_path, calls):
        engine = TriggerEngine(
            config_factory=lambda: GatedConfig(trigger_control=False),
            dispatch=lambda name, args, **kw: calls.append(name),
        )
        engine.store().add(_rule())

        (result,) = engine.evaluate(_percept("that was the doorbell"))

        assert result.fired is False
        assert result.denied is True
        assert calls == [], "a rule fired with the key that arms rules switched off"

    def test_the_refusal_names_the_key(self, tmp_path, calls):
        """A user cannot act on a refusal that does not say what to turn on."""
        engine = TriggerEngine(
            config_factory=lambda: GatedConfig(trigger_control=False),
            dispatch=lambda *a: calls.append(a),
        )
        engine.store().add(_rule())

        (result,) = engine.evaluate(_percept("that was the doorbell"))

        assert TRIGGER_CONTROL_KEY in result.reason, (
            "the refusal must name the key, or it is indistinguishable from a bug"
        )

    def test_arming_and_firing_are_unaffected_while_the_key_is_on(self, calls):
        """So the check cannot pass by refusing everything."""
        engine = TriggerEngine(
            config_factory=lambda: GatedConfig(trigger_control=True),
            dispatch=lambda name, args, **kw: calls.append(name),
        )
        engine.store().add(_rule())

        (result,) = engine.evaluate(_percept("that was the doorbell"))

        assert result.fired is True and result.denied is False
        assert calls == ["notify"]

    def test_the_key_is_re_read_per_percept_not_cached_at_arm_time(self, calls):
        """Revoking *after* the rule is armed is the case that was broken."""
        state = {"on": True}

        class Flippable(GatedConfig):
            def get_bool(self, key, default=False):
                if key == TRIGGER_CONTROL_KEY:
                    return state["on"]
                return super().get_bool(key, default)

        engine = TriggerEngine(
            config_factory=Flippable,
            dispatch=lambda name, args, **kw: calls.append(name),
        )
        engine.store().add(_rule())

        assert engine.evaluate(_percept("the doorbell"))[0].fired is True
        state["on"] = False
        # A different percept, past the cooldown, so the only thing that can
        # stop it is the gate.
        denied = engine.evaluate(_percept("the doorbell again"), now=500.0)[0]

        assert denied.denied is True
        assert TRIGGER_CONTROL_KEY in denied.reason
        assert calls == ["notify"], "the second percept dispatched after revocation"

    def test_the_denial_is_reported_even_inside_the_cooldown_window(self, tmp_path, calls):
        """Consent before cooldown, or a revoked key looks like "no match"."""
        engine = TriggerEngine(
            config_factory=lambda: GatedConfig(trigger_control=True),
            dispatch=lambda name, args, **kw: calls.append(name),
        )
        engine.store().add(_rule())
        engine.evaluate(_percept("that was the doorbell"), now=1000.0)
        calls.clear()

        engine._config_factory = lambda: GatedConfig(trigger_control=False)
        (result,) = engine.evaluate(_percept("that was the doorbell"), now=1000.5)

        assert result.denied is True, "the cooldown window hid the consent denial"

    def test_an_undeclared_key_denies_rather_than_permitting(self, tmp_path, calls):
        """`get_bool` yields the supplied default, so a schema predating the key
        refuses every rule instead of allowing every unattended action."""
        class KnowsNothing(FakeConfig):
            def get_bool(self, key, default=False):
                return default

        engine = TriggerEngine(
            config_factory=KnowsNothing,
            dispatch=lambda name, args, **kw: calls.append(name),
        )
        engine.store().add(_rule())

        (result,) = engine.evaluate(_percept("that was the doorbell"))

        assert result.denied is True
        assert calls == []


class TestDestructiveActuatorsOnThePerceptArmPath:
    """`build_rule` accepted all eight; `build_event_rule` refused all eight.

    The percept path is the half `skills/manage_triggers.py` makes reachable
    from a spoken instruction, so it was the permissive one.
    """

    @pytest.mark.parametrize("actuator", sorted(_DESTRUCTIVE_ACTUATORS & set(_HANDLER_FNS)))
    def test_a_destructive_actuator_cannot_be_armed(self, actuator):
        rule, problem = build_rule(
            name="r", sense="hearing", match_mode=MATCH_KEYWORDS,
            keywords=["loud"], actuator=actuator, arguments={},
            skills=_HANDLER_FNS,
        )

        assert rule is None, f"{actuator} was armed on the percept path"
        assert "destructive" in (problem or "")

    def test_every_destructive_entry_that_names_no_shipped_skill_is_visible(self):
        """A deny-list entry that cannot match anything is a guard that is not
        there, and it is invisible: no call can reach it, so nothing fails.

        Measured 2026-09-30: `power_profile` is in `_DESTRUCTIVE_ACTUATORS` and
        the shipped skill is `set_power_profile`, so that one entry protects
        nothing. Left as it is on purpose - correcting the typo would put a
        *real* skill into the destructive set, and deleting the entry would drop
        a name someone put there deliberately. Either way it changes what a user
        may arm unattended, which is a product decision rather than a mechanical
        fix. This test is here so the decision stays visible instead of
        evaporating with the frozenset.
        """
        dead = sorted(_DESTRUCTIVE_ACTUATORS - set(_HANDLER_FNS))

        assert dead == ["power_profile"], (
            "the dead entries in the destructive set changed - re-decide whether "
            f"each of {dead} should map to a real skill name"
        )

    @pytest.mark.parametrize("actuator", sorted(_UNATTENDED_WRITE_ACTUATORS))
    def test_an_unkeyed_overwriting_actuator_cannot_be_armed_at_all(self, actuator):
        """Not even with the opt-in: there is no key to re-check at fire time,
        so an opt-in would be the bypass rather than the escape."""
        rule, problem = build_rule(
            name="r", sense="hearing", match_mode=MATCH_KEYWORDS,
            keywords=["loud"], actuator=actuator, arguments={},
            allow_destructive=True, skills=_HANDLER_FNS,
        )

        assert rule is None
        assert "consent key" in (problem or "")

    def test_the_opt_in_is_real_and_not_a_permanent_no(self):
        rule, problem = build_rule(
            name="r", sense="hearing", match_mode=MATCH_KEYWORDS,
            keywords=["loud"], actuator="delete_file", arguments={"path": "/tmp/x"},
            allow_destructive=True, skills=_HANDLER_FNS,
        )

        assert rule is not None, problem
        assert rule.allow_destructive is True

    def test_the_guard_is_checked_at_fire_time_not_only_at_arm(self, calls):
        """A hand-edited rules file must not be a way around the guard."""
        rule, problem = build_rule(
            name="doorbell", sense="hearing", match_mode=MATCH_SUBSTRING,
            substring="doorbell", actuator="delete_file",
            arguments={"path": "/tmp/x"}, skills=_HANDLER_FNS,
        )
        assert rule is None, "this test is only meaningful while arming refuses"
        engine = TriggerEngine(
            config_factory=FakeConfig,
            dispatch=lambda name, args, **kw: calls.append(name),
        )
        # The same rule, built by hand and put straight into the store.
        engine.store().add(TriggerRule(
            name="doorbell", sense="hearing", match_mode=MATCH_SUBSTRING,
            substring="doorbell", actuator="delete_file",
            arguments={"path": "/tmp/x"}, allow_destructive=False,
        ))

        (result,) = engine.evaluate(_percept("that was the doorbell"))

        assert result.denied is True
        assert "destructive" in result.reason
        assert calls == []

    def test_the_hand_edited_write_rule_is_refused_at_fire_time_too(self, calls):
        engine = TriggerEngine(
            config_factory=FakeConfig,
            dispatch=lambda name, args, **kw: calls.append(name),
        )
        engine.store().add(TriggerRule(
            name="doorbell", sense="hearing", match_mode=MATCH_SUBSTRING,
            substring="doorbell", actuator="write_text_file",
            arguments={"path": "/tmp/x", "content": "y"},
            # Even claiming the opt-in: this actuator has none to claim.
            allow_destructive=True,
        ))

        (result,) = engine.evaluate(_percept("that was the doorbell"))

        assert result.denied is True
        assert calls == []

    def test_a_harmless_actuator_is_still_armable(self):
        """So the guard is a guard and not a blanket refusal."""
        rule, problem = build_rule(
            name="r", sense="hearing", match_mode=MATCH_KEYWORDS,
            keywords=["loud"], actuator="notify", arguments={"summary": "x"},
            skills=_HANDLER_FNS,
        )

        assert rule is not None, problem

    def test_the_opt_in_is_not_a_parameter_the_arming_skill_can_set(self):
        """The same argument the event path makes, now true of both."""
        import json

        from shani_chronoa.skills import manage_triggers

        assert "allow_destructive" not in json.dumps(manage_triggers.SCHEMA), (
            "allow_destructive in the schema would let one argument switch off "
            "both the arm-time and the fire-time destructive guard"
        )
        # An AST scan, not a substring scan: the module's own docstring names
        # `allow_destructive` to explain why it is not exposed, and a prose
        # match would fail on that sentence forever.
        tree = ast.parse(Path(manage_triggers.__file__).read_text(encoding="utf-8"))
        passed = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and any(kw.arg == "allow_destructive" for kw in node.keywords)
        ]
        assert passed == [], (
            "manage_triggers must not pass allow_destructive; it is the "
            "deliberate, non-model-reachable opt-in"
        )


class TestTheBoundsRunForTheDefaultEventMatchMode:
    """`match_mode=any` returned from the whole validator, not from a branch.

    `manage_triggers` hands out `any` as the default for an event rule, so the
    bounds below that line were skipped on the default arming path rather than
    on an exotic input.
    """

    @pytest.mark.parametrize("cooldown", [0.0, -9999.0, 1000000000])
    def test_a_bogus_cooldown_is_refused(self, cooldown):
        rule, problem = build_rule(
            name="r", sense="hearing", match_mode=MATCH_ANY, actuator="notify",
            arguments={"summary": "x"}, cooldown_seconds=cooldown,
            skills=_HANDLER_FNS,
        )

        assert rule is None, f"cooldown {cooldown!r} was accepted with match_mode=any"
        assert "cooldown_seconds" in (problem or "")

    def test_too_many_arguments_are_refused(self):
        rule, problem = build_rule(
            name="r", sense="hearing", match_mode=MATCH_ANY, actuator="notify",
            arguments={f"a{i}": "v" for i in range(MAX_ARGUMENTS + 92)},
            skills=_HANDLER_FNS,
        )

        assert rule is None
        assert f"at most {MAX_ARGUMENTS} arguments" in (problem or "")

    def test_an_oversized_argument_value_is_refused(self):
        rule, problem = build_rule(
            name="r", sense="hearing", match_mode=MATCH_ANY, actuator="notify",
            arguments={"summary": "x" * (MAX_ARGUMENT_VALUE_CHARS + 1000)},
            skills=_HANDLER_FNS,
        )

        assert rule is None
        assert "at most" in (problem or "")

    def test_the_negative_control_the_same_cooldown_is_refused_in_substring_mode(self):
        """The proof that the bounds above are live and not merely present.

        Identical input, one difference: a real match mode. If this one were
        refused and `any` were not, the refusals above would be the substring
        branch rather than the shared bounds.
        """
        rule, problem = build_rule(
            name="r", sense="hearing", match_mode=MATCH_SUBSTRING, substring="loud",
            actuator="notify", arguments={"summary": "x"}, cooldown_seconds=0.0,
            skills=_HANDLER_FNS,
        )

        assert rule is None
        assert f"must be at least {MIN_COOLDOWN_SECONDS:g}s" in (problem or "")

    def test_a_legal_any_rule_is_still_accepted(self):
        """So the fix is not "refuse the mode"."""
        rule, problem = build_rule(
            name="r", sense="hearing", match_mode=MATCH_ANY, actuator="notify",
            arguments={"summary": "x"}, skills=_HANDLER_FNS,
        )

        assert rule is not None, problem


class TestAHandEditedRulesFileIsBoundedAtLoad:
    """`from_dict` applied no bounds, so a rules file could set any number.

    The file is JSON in the user's own data directory, so it is editable by
    hand. The bound here is the same one `build_rule` applies at arm time, and
    it runs in the *restrictive* direction: clamping a cadence can only make a
    rule fire less, so it is repairable, while repairing an argument would
    change what the actuator is told and is refused instead.
    """

    @pytest.mark.parametrize("cooldown,expected", [
        (-1.0, MIN_COOLDOWN_SECONDS),
        (0.0, MIN_COOLDOWN_SECONDS),
        (10**9, 3600.0),
        (30.0, 30.0),
    ])
    def test_the_cooldown_is_clamped_into_range(self, cooldown, expected):
        rule = TriggerRule.from_dict({
            "name": "r", "sense": "hearing", "match_mode": MATCH_KEYWORDS,
            "keywords": ["loud"], "actuator": "notify",
            "arguments": {"summary": "x"}, "cooldown_seconds": cooldown,
        })

        assert rule is not None
        assert rule.cooldown_seconds == expected, (
            "a negative cooldown fires on every percept; a huge one is a "
            "rule that never fires again"
        )

    def test_a_cooldown_of_not_a_number_is_refused(self):
        rule = TriggerRule.from_dict({
            "name": "r", "sense": "hearing", "match_mode": MATCH_KEYWORDS,
            "keywords": ["loud"], "actuator": "notify",
            "arguments": {"summary": "x"}, "cooldown_seconds": "soon",
        })

        assert rule is None

    def test_an_oversized_argument_list_is_refused_not_trimmed(self):
        rule = TriggerRule.from_dict({
            "name": "r", "sense": "hearing", "match_mode": MATCH_KEYWORDS,
            "keywords": ["loud"], "actuator": "notify",
            "arguments": {f"a{i}": "v" for i in range(MAX_ARGUMENTS + 1)},
        })

        assert rule is None, (
            "trimming the list would arm a rule that calls the actuator with "
            "different arguments than the file says"
        )

    def test_an_oversized_argument_value_is_refused_not_shortened(self):
        rule = TriggerRule.from_dict({
            "name": "r", "sense": "hearing", "match_mode": MATCH_KEYWORDS,
            "keywords": ["loud"], "actuator": "notify",
            "arguments": {"summary": "x" * (MAX_ARGUMENT_VALUE_CHARS + 1)},
        })

        assert rule is None

    def test_a_sanctioned_rule_round_trips_unchanged(self):
        original, problem = build_rule(
            name="r", sense="hearing", match_mode=MATCH_KEYWORDS,
            keywords=["loud"], actuator="notify", arguments={"summary": "x"},
            skills=_HANDLER_FNS,
        )
        assert original is not None, problem

        loaded = TriggerRule.from_dict(original.to_dict())

        assert loaded is not None
        assert loaded.to_dict() == original.to_dict(), (
            "the bounds must not disturb a rule that was armed legitimately"
        )


class TestAnActuatorThatNeverRanIsNotASuccess:
    """`execute_tool` returns prose and drops `ran`; `execute_tool_outcome`
    returns both. The engines were using the first one."""

    def test_a_tool_that_never_ran_is_not_reported_as_fired(self, tmp_path):
        engine = TriggerEngine(
            config_factory=FakeConfig,
            dispatch=lambda *a, **kw: ToolOutcome(
                text="Tool 'notify' failed: exit=126",
                verdict=verification.Verdict.UNVERIFIED, ran=False,
            ),
        )
        engine.store().add(_rule())
        rule = engine.store().all()[0]

        (result,) = engine.evaluate(_percept("that was the doorbell"), now=1000.0)

        assert result.fired is False
        assert rule.last_fired_at is None, (
            "a tool that never executed must not start the cooldown clock"
        )

    def test_a_tool_with_nothing_to_check_against_is_still_a_success(self, tmp_path):
        """The negative control, and the reason UNVERIFIED is not a failure.

        Treating UNVERIFIED as a failure was proposed, and would be wrong: on
        the default seam every successful dispatch in this codebase is
        UNVERIFIED, so it would park every legitimate rule after five firings.
        """
        engine = TriggerEngine(
            config_factory=FakeConfig,
            dispatch=lambda *a, **kw: ToolOutcome(
                text="Notification sent.", verdict=verification.Verdict.UNVERIFIED, ran=True,
            ),
        )
        engine.store().add(_rule())
        rule = engine.store().all()[0]

        (result,) = engine.evaluate(_percept("that was the doorbell"), now=1000.0)

        assert result.fired is True
        assert result.verdict is verification.Verdict.UNVERIFIED
        assert rule.last_fired_at == 1000.0
        assert rule.due(1001.0) is False, "cooldown must still apply"

    def test_the_default_seam_is_the_one_that_reports_ran(self, tmp_path):
        """Otherwise the check above can never fire in production.

        Asserted on the *bound* attribute rather than on the source, and for
        both engines, because a source substring test stops at the first failing
        assert and would report one engine as checked when only the other was.
        """
        from shani_chronoa.tools import execute_tool, execute_tool_outcome
        from shani_chronoa.triggers import EventEngine, EventRuleStore

        percept = TriggerEngine(store=RuleStore(tmp_path / "r.json"))
        event = EventEngine(store=EventRuleStore(tmp_path / "e.json"))

        assert percept._dispatch is execute_tool_outcome, (
            "the percept engine still dispatches through the prose-only seam, so "
            "`ran` is invisible and a tool that never ran reads as a success"
        )
        assert event._dispatch is execute_tool_outcome
        assert execute_tool is not execute_tool_outcome, (
            "control: the two seams must actually differ, or this test is "
            "comparing a thing to itself"
        )
