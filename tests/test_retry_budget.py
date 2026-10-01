"""A per-rule retry budget, and the three ways a persisted one used to vanish.

The budget was per-rule in storage (`consecutive_failures` and `restart_times`
are both on `EventRule`) but `EventRule._policy()` built `BackoffPolicy()` with
no arguments, so every rule got the module default of five. Worse, `from_dict`
clamped `consecutive_failures` to that same module default - so a rule set to
twelve would reload as five and `should_park()` would flip to False on restart.
That is a *silent* un-park: the rule comes back from disk looking armed and
starts retrying a broken actuator it had already given up on, with nothing
anywhere saying the budget changed.

Three things are asserted here:

- **It round-trips.** `to_dict` -> `from_dict` preserves a budget of twelve, and
  a rule parked at twelve stays parked across a restart.
- **A hostile value is not adopted.** `from_dict` validates before the instance
  exists, so a hand-edited `"max_consecutive_failures": 10**9` would otherwise
  be honoured - a budget of a billion consecutive attempts is not a config, it
  is a permanently disabled backoff.
- **The rolling-window cap is not silently tightened.** Raising the budget must
  not make a rule *less* likely to retry: `should_park()` is an OR of the two
  caps, so a budget above `BACKOFF_RESTART_LIMIT` with the restart limit left
  alone would still park after five.
"""

import json

import pytest

from shani_chronoa import verification
from shani_chronoa.triggers import (
    BACKOFF_MAX_CONSECUTIVE_FAILURES,
    BACKOFF_RESTART_LIMIT,
    EVENT_GIT,
    MATCH_ANY,
    RETRY_RETRYABLE,
    TRIGGER_CONTROL_KEY,
    DurableFingerprints,
    Event,
    EventEngine,
    EventRule,
    EventRuleStore,
)

#: The ceiling on a *persisted* per-rule budget. See the module docstring of
#: `triggers.py` and `test_a_hand_edited_budget_above_the_ceiling_is_not_adopted`.
CEILING = 20

BASE = {
    "name": "r", "event_type": EVENT_GIT, "source": "/tmp/repo",
    "actuator": "notify", "arguments": {"summary": "x"},
}


def _load(**overrides):
    raw = dict(BASE)
    raw.update(overrides)
    return EventRule.from_dict(raw)


class FakeConfig:
    def __init__(self):
        self._keys = {TRIGGER_CONTROL_KEY: True}

    def get_bool(self, key, default=False):
        return self._keys.get(key, default)

    def sense_allowed(self, sense):
        return True

    def sense_allowed_reason(self, sense):
        return ""


def _engine(tmp_path, dispatch):
    return EventEngine(
        store=EventRuleStore(tmp_path / "event_rules.json"),
        config_factory=FakeConfig,
        dispatch=dispatch,
        fingerprint_store=DurableFingerprints(tmp_path / "prints.json"),
    )


def _event(fingerprint):
    return Event(
        kind=EVENT_GIT, subject="s", summary="HEAD moved",
        fingerprint=fingerprint, detail={}, created_at=0.0,
    )


def _failed_dispatch(*args, **kwargs):
    return verification.Result(verification.Verdict.FAILED, "the display never changed")


# --- the default is unchanged ----------------------------------------------

def test_a_rule_with_no_persisted_budget_gets_the_module_default():
    """`test_five_consecutive_failures_park_it` pins the policy default, and a
    rule that has to construct the same policy by hand would drift from it."""
    rule = _load()

    assert rule.max_consecutive_failures == BACKOFF_MAX_CONSECUTIVE_FAILURES
    assert rule._policy().max_consecutive_failures == BACKOFF_MAX_CONSECUTIVE_FAILURES


def test_the_default_still_parks_at_five():
    rule = _load(consecutive_failures=BACKOFF_MAX_CONSECUTIVE_FAILURES)

    assert rule.exhausted() is True


# --- it round-trips --------------------------------------------------------

def test_a_budget_of_twelve_round_trips_as_twelve_not_five():
    """The defect: `from_dict` clamped `consecutive_failures` to the module
    default, so a twelve-failure rule came back as a five-failure rule."""
    rule = _load(max_consecutive_failures=12)
    assert rule is not None
    assert rule.max_consecutive_failures == 12

    reloaded = EventRule.from_dict(rule.to_dict())

    assert reloaded.max_consecutive_failures == 12, (
        "the per-rule budget did not survive persistence; it silently reset to "
        f"the module default of {BACKOFF_MAX_CONSECUTIVE_FAILURES}"
    )


def test_a_rule_parked_at_twelve_is_still_parked_after_a_restart(tmp_path):
    """The reason the clamp was a bug and not a tidiness issue: losing the
    budget also loses `should_park()`, so a parked rule un-parks on restart."""
    engine = _engine(tmp_path, _failed_dispatch)
    rule = EventRule(
        name="r", event_type=EVENT_GIT, source="/tmp/repo", actuator="notify",
        arguments={"summary": "x"}, match_mode=MATCH_ANY, debounce_seconds=0.0,
        cooldown_seconds=0.0, retry_policy=RETRY_RETRYABLE,
        max_consecutive_failures=12,
    )
    engine.store().add(rule)
    engine.feed(rule, _event("baseline"), now=1000.0)

    clock = 1000.0
    for index in range(12):
        engine.feed(rule, _event(f"fp{index}"), now=clock)
        clock = max(clock + 120.0, (rule.retry_at or clock) + 1.0)

    assert rule.parked is True
    assert rule.consecutive_failures >= 12, rule.consecutive_failures

    engine.store()._write()
    (loaded,) = EventRuleStore(tmp_path / "event_rules.json").all()

    assert loaded.max_consecutive_failures == 12
    assert loaded.consecutive_failures >= 12
    assert loaded.exhausted() is True, (
        "the rule un-parked on restart: the budget came back lower than the "
        "counter it is compared against, so should_park() flipped to False"
    )


def test_the_budget_reaches_the_policy_that_actually_parks(tmp_path):
    """`_policy()` constructing `BackoffPolicy()` with no arguments is the
    other half: a budget in storage that never reaches the policy is decorative.
    """
    generous = _load(max_consecutive_failures=12, consecutive_failures=8)
    strict = _load(max_consecutive_failures=3, consecutive_failures=8)

    assert generous.exhausted() is False, "a budget of 12 parked at 8 failures"
    assert strict.exhausted() is True, "a budget of 3 did not park at 8 failures"


def test_a_persisted_counter_above_the_rules_own_budget_still_parks():
    """The clamp on `consecutive_failures` has to move with the budget, or a
    twelve-failure rule comes back claiming to have fewer than five."""
    rule = _load(max_consecutive_failures=12, consecutive_failures=12)

    assert rule.consecutive_failures == 12, (
        "the persisted counter was clamped back to the module default, so the "
        "rule cannot represent its own state"
    )
    assert rule.exhausted() is True


# --- the rolling-window cap stays honest -----------------------------------

def test_a_budget_above_the_restart_limit_does_not_silently_re_tighten():
    """`should_park()` is an OR of two caps. Raising only `consecutive_failures`
    and leaving `restart_limit` at five means a rule with a budget of twelve
    still parks after five failures inside the window - the budget is a lie."""
    rule = _load(max_consecutive_failures=12, restart_times=[0.0] * 5)

    assert rule.exhausted() is False, (
        "raising the per-rule budget must not leave the rolling-window cap to "
        "park the rule at BACKOFF_RESTART_LIMIT anyway"
    )


def test_the_restart_limit_tracks_the_budget_but_never_below_the_module_default():
    at_default = _load(max_consecutive_failures=BACKOFF_MAX_CONSECUTIVE_FAILURES)
    raised = _load(max_consecutive_failures=12)

    assert at_default._policy().restart_limit == BACKOFF_RESTART_LIMIT
    assert raised._policy().restart_limit >= raised.max_consecutive_failures


def test_a_restart_list_longer_than_the_module_limit_still_loads():
    """`:2474` refuses a `restart_times` list over `BACKOFF_RESTART_LIMIT`, and
    a refusal means `from_dict` returns None - so a rule that legitimately
    accumulated a longer window becomes permanently unloadable, and an
    unreadable rule refuses the *whole* file. A per-rule budget above five has
    to widen this bound too."""
    rule = _load(max_consecutive_failures=12, restart_times=[0.0] * 12)

    assert rule is not None, (
        "a rule that failed twelve times could not be loaded again; the store "
        "refuses the entire file when one rule is unreadable"
    )
    assert len(rule.restart_times) == 12


# --- hostile input ---------------------------------------------------------

@pytest.mark.parametrize("persisted", [10 ** 9, -1, 0, 3.5, "twelve", None, True])
def test_a_hand_edited_budget_is_capped_or_refused_never_adopted(persisted):
    """`from_dict` validates before the instance exists, so a hand-edited rules
    file is the only thing standing between a user and a billion retries."""
    rule = _load(max_consecutive_failures=persisted)

    if rule is None:
        return
    assert 1 <= rule.max_consecutive_failures <= CEILING, (
        f"a persisted budget of {persisted!r} was adopted as "
        f"{rule.max_consecutive_failures}"
    )


def test_a_hand_edited_budget_above_the_ceiling_is_not_adopted():
    rule = _load(max_consecutive_failures=10 ** 9)

    assert rule is not None
    assert rule.max_consecutive_failures == CEILING
    assert rule.exhausted() is False, (
        "a budget of a billion would make should_park() unreachable, which is a "
        "permanently disabled backoff wearing a config value"
    )


def test_the_ceiling_bounds_both_caps_together():
    """A ceiling that only bounds `consecutive_failures` leaves the restart
    list growing to it, which is the `restart_times` refusal above."""
    rule = _load(max_consecutive_failures=CEILING)

    assert rule._policy().restart_limit == CEILING


def test_a_budget_the_rules_own_counter_cannot_reach_is_not_widened_forever():
    rule = _load(max_consecutive_failures=CEILING)

    assert rule._policy().max_consecutive_failures == CEILING


# --- the field is actually in all four places ------------------------------

def test_the_field_is_in_slots_init_to_dict_and_from_dict():
    """`__slots__` means a field missing from it is an `AttributeError` at
    runtime, not at import - and a field in `to_dict` but not `from_dict` is a
    value that silently reverts."""
    assert "max_consecutive_failures" in EventRule.__slots__

    rule = EventRule(
        name="r", event_type=EVENT_GIT, source="/s", actuator="notify",
        arguments={}, max_consecutive_failures=9,
    )
    assert rule.max_consecutive_failures == 9
    assert rule.to_dict()["max_consecutive_failures"] == 9

    reloaded = EventRule.from_dict(rule.to_dict())
    assert reloaded.max_consecutive_failures == 9

    reloaded.max_consecutive_failures = 11
    with pytest.raises(AttributeError):
        reloaded.not_a_real_field = 1


def test_a_rule_without_the_key_still_loads(tmp_path):
    """An existing rules file written before this field existed must not become
    unreadable: `RuleStore._load` refuses the *whole* file on one bad rule."""
    path = tmp_path / "event_rules.json"
    path.write_text(json.dumps([dict(BASE)]))

    (loaded,) = EventRuleStore(path).all()

    assert loaded.max_consecutive_failures == BACKOFF_MAX_CONSECUTIVE_FAILURES
