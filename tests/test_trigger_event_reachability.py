"""The six event types are reachable from a turn, and evaluated by a real loop.

Every other test of `triggers.py` proves the engine is *correct*. This file
proves it is *reached*, which is the thing 81 green tests did not: until
2026-09-30 `build_event_rule` was called from nowhere outside its own module
and nothing in the tree called `EventEngine.poll()`, so the six event types
could be armed by no one and fired by nothing. `AGENTS.md` names this exact
failure four times over.

The reachability claims are asserted on the *source* of the two files that
carry the wiring, because "a caller exists" is a property of the tree and a
test that constructs an engine in isolation cannot see it. The behaviour
claims then drive the same objects those files construct.
"""

import ast
import json
import os
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from shani_chronoa.config import (
    _EVENT_CONSENT_KEYS,
    _EVENT_DEFAULT_ENABLED,
    _SENSE_DEFAULT_ENABLED,
    ChronoaConfig,
)
from shani_chronoa.senses.scheduler import AmbientScheduler
from shani_chronoa.skills import manage_triggers
from shani_chronoa.triggers import (
    MIN_COOLDOWN_SECONDS,
    TRIGGER_CONTROL_KEY,
    EventEngine,
    EventRuleStore,
)
import shani_chronoa.triggers as triggers

_REPO = Path(__file__).resolve().parents[1]
_SKILL = _REPO / "usr/lib/shani-chronoa/shani_chronoa/skills/manage_triggers.py"
_SCHEDULER = _REPO / "usr/lib/shani-chronoa/shani_chronoa/senses/scheduler.py"
_CLI = _REPO / "usr/bin/shani-chronoa-sense"
GSCHEMA_XML = _REPO / "usr/share/glib-2.0/schemas/org.shani.chronoa.gschema.xml"


def _names(path: Path) -> "set[str]":
    """Every name the parsed module mentions, so prose cannot pass for a call."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            found.update(a.name.rsplit(".", 1)[-1] for a in node.names)
    return found


# --- reachability, asserted on the tree ---------------------------------------

class TestTheWiringExistsInTheRunningTree:
    """The before/after grep, as a test. Reverting either file turns these red."""

    def test_the_skill_reaches_build_event_rule(self):
        assert "build_event_rule" in _names(_SKILL), (
            "manage_triggers no longer mentions build_event_rule, so an LLM turn "
            "cannot arm an event rule again"
        )

    def test_the_skill_reaches_both_stores(self):
        names = _names(_SKILL)
        assert "build_rule" in names, "the original percept path must survive"
        assert "EventRuleStore" in names, "the event store is no longer reachable"

    def test_the_scheduler_evaluates_event_rules_on_its_own_tick(self):
        names = _names(_SCHEDULER)
        assert "run_due" in names, (
            "the ambient loop no longer dispatches debounced event runs"
        )

    def test_the_scheduler_does_not_import_triggers(self):
        """Injected, not imported - the isolation the AST test upstream asserts."""
        assert "EventEngine" not in _names(_SCHEDULER), (
            "scheduler.py must not import triggers; the engine is injected so this "
            "module keeps no dependency on it"
        )

    def test_the_shipped_cli_supplies_an_event_engine(self):
        """An actual call, not merely the name.

        The first version of this asserted `"EventEngine" in <names>` and a
        negative control proved it worthless: setting `engine = None` in
        `_ambient_scheduler` left every test green, because the name survives in
        the import and the annotation. A control that cannot fail is worse than
        no control, so the assertion is on a `Call` node.
        """
        calls = [
            node for node in ast.walk(ast.parse(_CLI.read_text(encoding="utf-8")))
            if isinstance(node, ast.Call)
            and ((isinstance(node.func, ast.Name) and node.func.id == "EventEngine")
                 or (isinstance(node.func, ast.Attribute)
                     and node.func.attr == "EventEngine"))
        ]
        assert calls, (
            "shani-chronoa-sense no longer constructs an EventEngine, so the "
            "ambient loop has nothing to evaluate event rules with. The name "
            "still being imported is not the same claim."
        )


# --- harness -------------------------------------------------------------------

class _NullStore:
    """A `PerceptStore` that keeps nothing. The senses are not what is under test."""

    def add(self, percept):  # pragma: no cover - never reached with senses={}
        return None


@pytest.fixture
def consent(tmp_path, monkeypatch, gsettings_env):
    """Both consents on, and every store redirected into `tmp_path`.

    `gsettings_env` is the repo's own: `conftest.py` already isolates HOME and
    compiles the schema, and an earlier draft of this fixture re-created both by
    hand and collided with that autouse one. Consent is granted through
    `ChronoaConfig.set` and never the `gsettings` CLI, because under
    `GSETTINGS_BACKEND=keyfile` the CLI writes a *different* store than
    `ChronoaConfig` reads - a control that silently does nothing.
    """
    monkeypatch.setattr(triggers, "RULES_FILE", tmp_path / "rules.json")
    monkeypatch.setattr(triggers, "EVENT_RULES_FILE", tmp_path / "event_rules.json")
    monkeypatch.setattr(triggers, "FINGERPRINTS_FILE", tmp_path / "fingerprints.json")

    config = ChronoaConfig()
    config.set("privacy-mode", "false")
    config.set(TRIGGER_CONTROL_KEY, "true")
    config.set("git-sense-enabled", "true")
    return config


def _shut(key: str) -> None:
    ChronoaConfig().set(key, "false")


def _grant(key: str) -> None:
    ChronoaConfig().set(key, "true")


def _rule(name: str):
    """The armed rule called `name`, read back from the store on disk."""
    for rule in EventRuleStore().all():
        if rule.name == name:
            return rule
    raise AssertionError(f"no armed rule called {name!r}")


def _fire_denial(rule) -> str:
    """What the *dispatch* gate says about `rule` - '' when it may act.

    `EventEngine._consent` is the same call `poll()` and `run_due()` make
    before anything happens, reached here directly so a fire-time assertion
    does not also have to manufacture the underlying signal.

    `or ""` because the gate has spelled "may act" both ways - `""` and
    `None`, both optional-strings - and a test that asserted `== ""` would
    report a spelling change as a consent failure.
    """
    return EventEngine(store=EventRuleStore())._consent(ChronoaConfig(), rule) or ""


def _git_repo() -> Path:
    """A real repository, *inside* `$HOME`.

    Inside, because `read_git_state` confines its source to the user's home and
    reports anything outside as `SIGNAL_UNAVAILABLE` - a repo at `tmp_path/repo`
    sits outside the `tmp_path/home` that `conftest.py`'s autouse fixture sets,
    and every firing assertion then fails for a reason that has nothing to do
    with the wiring.
    """
    repo = Path(os.environ["HOME"]) / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    env = _git_env()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, env=env)
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "first"], check=True, env=env)
    return repo


def _git_env() -> dict:
    return {
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "PATH": "/usr/bin:/bin", "HOME": os.environ["HOME"],
    }


def _commit(repo: Path, message: str) -> None:
    env = _git_env()
    (repo / "a.txt").write_text(message + "\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", message], check=True, env=env)


def _arm(repo: Path, **overrides) -> str:
    # `cooldown_seconds` is 1s, the minimum `MIN_COOLDOWN_SECONDS` allows, and
    # used to be 0. That was not incidental: `build_event_rule` reached
    # `_validate_rule_fields`, which returned early for the default
    # `match_mode=any` and so never ran the cooldown bounds at all, so "0" was
    # accepted and every rule armed here had a cooldown that suppressed nothing.
    # With the bounds live, 0 is refused with a message naming the limit - the
    # same refusal a user now gets - so the fixture has to ask for a legal
    # value. `debounce_seconds` stays 0 because 0 *is* legal there
    # (`MIN_DEBOUNCE_SECONDS`), and `test_a_debounce_of_zero_is_not_silently_
    # become_five_seconds` depends on it.
    args = {
        "action": "add", "name": "repo-changed", "event_type": "git",
        "source": str(repo), "actuator": "notify",
        "arguments": {"summary": "changed"}, "cooldown_seconds": MIN_COOLDOWN_SECONDS,
        "debounce_seconds": 0,
    }
    args.update(overrides)
    return manage_triggers._run(args)


_CALLS: list = []


def _recorder(actuator, arguments, **kwargs):
    _CALLS.append((actuator, dict(arguments), kwargs.get("origin")))
    from shani_chronoa import verification
    return verification.Result(verification.Verdict.VERIFIED, "it happened")


@pytest.fixture(autouse=True)
def _clear_calls():
    """A module-level call list that survives between tests would make every
    `len(_CALLS) == 1` assertion pass for the wrong reason after the first."""
    _CALLS.clear()
    yield
    _CALLS.clear()


def _engine() -> EventEngine:
    return EventEngine(store=EventRuleStore(), dispatch=_recorder)


def _scheduler(engine=None, **kwargs) -> AmbientScheduler:
    """A scheduler with the *event* interval at 0, so two `poll_due()` calls in
    one test both poll.

    The shipped default is 15s and is asserted separately - a test that simply
    called `poll_due()` twice would see the second call correctly decline to
    poll and read that as "the wiring does not work".
    """
    kwargs.setdefault("senses", {})
    kwargs.setdefault("store", _NullStore())
    kwargs.setdefault("event_engine", _engine())
    kwargs.setdefault("event_poll_seconds", 0.0)
    return AmbientScheduler(**kwargs)


# --- creation path -------------------------------------------------------------

class TestAnEventRuleCanBeArmedFromATurn:
    def test_a_turn_arms_a_rule_the_app_can_then_read(self, consent, tmp_path):
        repo = _git_repo()
        assert "Armed 'repo-changed'" in _arm(repo)

        (rule,) = EventRuleStore().all()
        assert rule.name == "repo-changed"
        assert rule.event_type == "git"
        assert rule.source == str(repo)
        assert rule.actuator == "notify"

    def test_the_armed_rule_survives_as_json_on_disk(self, consent, tmp_path):
        repo = _git_repo()
        _arm(repo)
        stored = json.loads(Path(triggers.EVENT_RULES_FILE).read_text())
        assert stored[0]["event_type"] == "git"
        assert stored[0]["source"] == str(repo)

    def test_listing_shows_an_event_rule_and_needs_no_permission(self, consent, tmp_path):
        repo = _git_repo()
        _arm(repo)
        _shut(TRIGGER_CONTROL_KEY)
        listing = manage_triggers._run({"action": "list"})
        assert "Refusing" not in listing
        assert "repo-changed" in listing
        assert "event rule(s) armed" in listing

    def test_remove_takes_the_event_rule_out_of_both_stores(self, consent, tmp_path):
        repo = _git_repo()
        _arm(repo)
        assert "Removed the event rule" in manage_triggers._run(
            {"action": "remove", "name": "repo-changed"})
        assert EventRuleStore().all() == []

    def test_a_debounce_of_zero_is_not_silently_become_five_seconds(
            self, consent, tmp_path):
        """`0 or 5.0` is 5.0. Caught by reading the rule back, not the reply."""
        repo = _git_repo()
        _arm(repo)
        assert EventRuleStore().all()[0].debounce_seconds == 0.0

    def test_a_destructive_actuator_is_not_reachable_from_a_turn(self, consent, tmp_path):
        out = _arm(tmp_path, actuator="kill_process", arguments={"pid": "1"})
        assert "Refusing to arm" in out
        assert "destructive" in out
        assert EventRuleStore().all() == []

    def test_allow_destructive_is_not_a_parameter_the_model_can_set(self, consent, tmp_path):
        assert "allow_destructive" not in json.dumps(manage_triggers.SCHEMA), (
            "allow_destructive in the schema would let one argument switch off both "
            "the arm-time and the dispatch-time destructive guard"
        )

    def test_an_expiry_rule_may_only_notify(self, consent, tmp_path):
        out = _arm(tmp_path, event_type="expiry", actuator="control_service",
                   arguments={"action": "restart"})
        assert "may only notify" in out
        assert EventRuleStore().all() == []

    def test_an_unknown_event_type_is_refused(self, consent, tmp_path):
        out = _arm(tmp_path, event_type="moonphase")
        assert "event_type must be one of" in out
        assert EventRuleStore().all() == []


# --- the consent gate ----------------------------------------------------------

class TestTheConsentGate:
    def test_a_shut_gate_refuses_to_arm_and_names_the_key(self, consent, tmp_path):
        repo = _git_repo()
        _shut(TRIGGER_CONTROL_KEY)
        out = _arm(repo)
        assert "Refusing to change armed rules" in out
        assert TRIGGER_CONTROL_KEY in out, (
            "the refusal must name the key, or a user cannot act on it"
        )
        assert EventRuleStore().all() == []

    def test_revoking_the_arming_key_stops_an_already_armed_rule(
            self, consent, tmp_path):
        """The gate that was missing: arming was gated, firing was not.

        A user who armed a rule and then turned the key off had revoked
        permission to *change* the rules and nothing else. The only way to stop
        the rule was to disarm it - which the same switched-off key refused.
        """
        repo = _git_repo()
        _arm(repo)
        engine = _engine()
        engine.poll()  # baseline
        _commit(repo, "second")
        _shut(TRIGGER_CONTROL_KEY)

        results = engine.poll()
        assert _CALLS == [], "a rule fired with the arming key switched off"
        assert results and all(r.censored for r in results)
        assert any(TRIGGER_CONTROL_KEY in r.reason for r in results), (
            "the refusal must name the key"
        )

    def test_the_per_type_consent_key_is_still_required(self, consent, tmp_path):
        repo = _git_repo()
        _arm(repo)
        engine = _engine()
        engine.poll()
        _commit(repo, "second")
        _shut("git-sense-enabled")

        results = engine.poll()
        assert _CALLS == []
        assert any("git-sense-enabled" in r.reason for r in results)


class TestEveryEventTypeIsArmableOnceItsKeyIsGranted:
    """The five types that could never act, and now can - one at a time.

    Replaces an assertion that pinned the defect. `config._SENSE_CONSENT_KEYS`
    declared a key for `git` and for nothing else, so `EventRule.sense`
    (which returns the *event type*) resolved to no key at all for the other
    five: `sense_allowed()` returned False, `TriggerEngine._consent` refused,
    and `EventEngine._consent` refused again at dispatch. Five event types
    shipped that could be stored, listed, and reported as armed, and could
    never fire. The old test asserted that, which made a broken gate read as a
    working one.

    Every assertion here goes through `manage_triggers._run` or the real
    `EventEngine`, never through a private helper, because the arm-time refusal
    and the fire-time refusal are two different code paths in two different
    files and a test of only one of them proves half the claim.
    """

    #: The five that shipped inert. Spelled out rather than derived so that a
    #: sixth type added to `triggers.EVENT_TYPES` fails
    #: `test_the_six_event_types_are_exactly_the_six` below instead of
    #: quietly escaping every gate assertion in this class.
    WAS_INERT = ("fswatch", "failure", "expiry", "containerrun", "unithealth")

    #: The cooldown every `_arm` call below passes explicitly.
    #:
    #: `_arm`'s own default is `cooldown_seconds: 0`, which `triggers.py` began
    #: refusing once `MIN_COOLDOWN_SECONDS` became 1.0. None of these tests
    #: care about cooldown - they arm and read back a consent verdict - so
    #: naming it here keeps them from inheriting whatever the current floor is,
    #: which is the difference between a consent test and a cooldown test.
    COOLDOWN = 1

    #: One source per type that `build_event_rule` accepts without the
    #: source existing - the readers report an unavailable signal rather than
    #: refusing the rule, which is what these tests are checking. `expiry`
    #: additionally drives a real deadline file through the engine below.
    SOURCES = {
        "git": "repo",
        "fswatch": "/tmp",
        "failure": "pacman-database-outdated",
        "expiry": "probe",
        "containerrun": "probe",
        "unithealth": "probe.service",
    }

    @pytest.mark.parametrize("event_type", WAS_INERT)
    def test_it_has_a_consent_key_and_the_schema_knows_it(self, event_type):
        """Fail-closed all the way down: name -> key -> declared boolean."""
        from shani_chronoa.config import _consent_key_for

        key = _consent_key_for(event_type)
        assert key == f"{event_type}-sense-enabled", (
            f"{event_type} resolves to {key!r}; a refusal that names a switch "
            f"which does not exist is as useless as no key at all"
        )
        # Read from the XML rather than from `ChronoaConfig`, so this fails on
        # the registration defect itself instead of on a consequence of it.
        assert key in _schema_keys(), (
            f"{key} is absent from {GSCHEMA_XML.name}, so `get_bool` returns "
            f"its default and no grant can ever open it"
        )

    def test_the_six_event_types_are_exactly_the_six(self):
        """Anti-vacuity for every `parametrize` above.

        `triggers.EVENT_TYPES` is the real set. If it grows, this fails, and
        the five type-specific tests are known not to cover the new one.
        """
        assert sorted(triggers.EVENT_TYPES) == sorted(
            ("git",) + self.WAS_INERT
        ), (
            "triggers.EVENT_TYPES changed; WAS_INERT and the params below are "
            "written out by hand and this is where that gets caught"
        )

    def test_git_keeps_exactly_one_key(self):
        """`git` is both a sense and an event type, and gets one key.

        A second key for the same word would let a user permit a git *sense*
        and refuse a git *rule* with no way to say why. It is gated by
        `git-sense-enabled`, which already worked and must keep working.
        """
        from shani_chronoa.config import _SENSE_CONSENT_KEYS, _consent_key_for

        assert _consent_key_for("git") == "git-sense-enabled"
        assert _SENSE_CONSENT_KEYS["git"] == "git-sense-enabled"
        assert "git" not in _EVENT_CONSENT_KEYS

    @pytest.mark.parametrize("event_type", WAS_INERT)
    def test_it_has_a_label_a_person_can_read(self, event_type):
        """A gate label is the only prose a user ever gets for the key.

        Without one, `Capability.gate_label()` falls back to the bare key name,
        so a refusal or a help row would say `fswatch-sense-enabled` - a
        string nobody has ever read. The shape assertions below are what the
        label must not be: the bare event type (`fswatch`, one word) or the key
        (`fswatch-sense-enabled`, hyphenated). Substring matching is
        deliberately not used - "system failures" contains "failure", and a
        check that rejected that would be wrong.
        """
        from shani_chronoa.capabilities import GATE_NAMES

        key = f"{event_type}-sense-enabled"
        label = GATE_NAMES.get(key)
        assert label, (
            f"{key} has no entry in capabilities.GATE_NAMES, so it falls back "
            f"to printing the raw key to a user"
        )
        assert label.startswith("Let Chronoa"), label
        assert "-" not in label, (
            f"{key}'s label is the key itself rather than a request: {label!r}"
        )
        assert len(label.split()) >= 3, (
            f"{key}'s label is the bare event type rather than a request: "
            f"{label!r}"
        )

    def test_the_five_are_not_mapped_onto_a_skill(self):
        """`GATED` is one key per tool; these gate a rule, not a skill.

        Putting any of them in `GATED` would bind them to `manage_triggers`
        and tell a user that switching one on lets them arm every type.
        """
        from shani_chronoa.capabilities import GATED

        keys = {f"{event_type}-sense-enabled" for event_type in self.WAS_INERT}
        assert not keys & set(GATED.values()), (
            f"a per-event-type gate is bound to a skill: "
            f"{sorted(keys & set(GATED.values()))}"
        )

    def test_the_five_are_absent_from_every_default_enabled_set(self):
        """A fresh install arms nothing.

        This is the assertion that keeps the fix from becoming the bug in the
        other direction. `_SENSE_DEFAULT_ENABLED` turns a dozen senses on for
        a new user; the same treatment for event types would arm unattended
        rules nobody asked for, on a machine where the switches have never
        been touched.
        """
        for event_type in self.WAS_INERT:
            assert event_type not in _SENSE_DEFAULT_ENABLED, (
                f"{event_type} is default-on; a fresh install may not arm an "
                f"unattended rule of any type"
            )
            assert event_type not in _EVENT_DEFAULT_ENABLED, (
                f"{event_type} is default-on in the event table"
            )
            key = f"{event_type}-sense-enabled"
            assert _schema_key_defaults()[key] == "false", (
                f"{key} defaults to {_schema_key_defaults().get(key)!r} in the "
                f"schema, not false"
            )

    @pytest.mark.parametrize("event_type", WAS_INERT)
    def test_a_fresh_install_refuses_every_one_of_them(
        self, event_type, gsettings_env
    ):
        """The behaviour, not just the table: nothing granted, nothing armed.

        `_hermetic_env` gives every test a throwaway `XDG_CONFIG_HOME` and the
        `gsettings_env` fixture a throwaway keyfile backend, so this config has
        read no user value at all - it is what a user's first launch sees.
        """
        config = ChronoaConfig()
        for other in self.WAS_INERT:
            assert config.sense_allowed(other) is False, (
                f"{other} is permitted with nothing granted"
            )
            assert f"{other}-sense-enabled" in config.sense_allowed_reason(other), (
                f"{other}'s refusal does not name its key: "
                f"{config.sense_allowed_reason(other)!r}"
            )

    @pytest.mark.parametrize("event_type", WAS_INERT)
    def test_an_older_schema_that_lacks_the_key_denies_rather_than_permits(
        self, gsettings_env, monkeypatch, event_type
    ):
        """The fallback `get_bool` takes, which is otherwise never read.

        While the shipped schema compiles, every one of these keys resolves
        through it and `_default_on()` is never consulted - so a test that
        only exercises the normal path cannot tell a correct fallback from a
        broken one. This is the case that reaches it: an install whose schema
        predates these keys, where the only thing standing between "the switch
        is missing" and "the machine may watch your files" is the fallback
        value. It is the same reason `test_skill_consent_registration.py` uses
        a deliberate `True` poison value.
        """
        config = ChronoaConfig()
        monkeypatch.setattr(
            config, "_valid_keys",
            {k for k in config._valid_keys if k != f"{event_type}-sense-enabled"},
        )
        assert config.sense_allowed(event_type) is False, (
            f"{event_type} is permitted by an install whose schema has no "
            f"{event_type}-sense-enabled; the fallback must be false"
        )

    @pytest.mark.parametrize("event_type", WAS_INERT)
    def test_it_is_refused_while_the_key_is_off_and_fires_once_granted(
        self, consent, tmp_path, event_type
    ):
        """The claim itself, both halves, through the real skill path.

        Order matters: the refusal is proved *first*, on a config that has
        granted this key nothing, because "permitted once granted" is also
        true of a type with no gate at all - which is the state this test
        file was written to catch.
        """
        key = f"{event_type}-sense-enabled"
        _shut(key)

        refused = _arm(tmp_path, name=f"off-{event_type}", event_type=event_type,
                       source=self.SOURCES[event_type], cooldown_seconds=self.COOLDOWN)
        assert "It would NOT fire right now" in refused, (
            f"{event_type} armed with {key} off: {refused!r}"
        )
        assert key in refused, (
            f"the refusal must name {key}; a user who cannot act on a refusal "
            f"concludes the assistant is broken"
        )

        _grant(key)
        granted = _arm(tmp_path, name=f"on-{event_type}", event_type=event_type,
                       source=self.SOURCES[event_type], cooldown_seconds=self.COOLDOWN)
        assert "It would NOT fire right now" not in granted, (
            f"{event_type} refused with {key} granted: {granted!r}"
        )
        rule = _rule(f"on-{event_type}")
        assert _fire_denial(rule) == "", (
            f"{event_type} is still refused at dispatch with {key} on"
        )

    @pytest.mark.parametrize("event_type", WAS_INERT)
    def test_revoking_the_key_stops_a_rule_that_was_already_armed(
        self, consent, tmp_path, event_type
    ):
        """The negative control, run backwards: on, armed, then off.

        Without this the previous test would also pass if the gate read the key
        once and cached it, which is the shape of bug `EventEngine._consent`
        was fixed for on the `trigger-control-enabled` side.
        """
        key = f"{event_type}-sense-enabled"
        _grant(key)
        _arm(tmp_path, name=f"revoke-{event_type}", event_type=event_type,
              source=self.SOURCES[event_type], cooldown_seconds=self.COOLDOWN)
        assert _fire_denial(_rule(f"revoke-{event_type}")) == ""

        _shut(key)
        denial = _fire_denial(_rule(f"revoke-{event_type}"))
        assert denial, f"{event_type} kept firing after {key} was switched off"
        assert key in denial, f"the refusal must name {key}: {denial!r}"

    def test_an_expiry_rule_really_dispatches_through_the_engine(
        self, consent, tmp_path
    ):
        """Not only does the string change - a rule of a formerly-inert type
        reaches the actuator.

        Every other test in this class asserts on a consent verdict. This one
        arms through `manage_triggers`, gives the reader a real deadline file,
        and polls the real `EventEngine`, so the thing under test is the whole
        path a user experiences rather than the wording of a refusal.

        The file is rewritten between polls, and that is load-bearing rather
        than incidental: `feed()` treats the first sighting of a state as a
        baseline and refuses an unchanged fingerprint, so polling twice
        without a real transition would assert `len(_CALLS) == 1` on the
        strength of a *dedupe* suppression and prove nothing about consent.
        Moving a deadline inside its threshold is a genuine new state - the
        reader's own docstring calls a renewed deadline "a new key".
        """
        _grant("expiry-sense-enabled")
        deadlines = tmp_path / "deadlines"
        deadlines.mkdir()

        def _write_deadline(expires_at: float) -> None:
            (deadlines / "probe.json").write_text(
                json.dumps({"expires_at": expires_at, "label": "probe"}),
                encoding="utf-8",
            )

        _write_deadline(time.time() + 30 * 86400.0)   # far off: no event yet
        out = _arm(tmp_path, name="real-expiry", event_type="expiry",
                   source="probe", params={"directory": str(deadlines)},
                   cooldown_seconds=self.COOLDOWN, debounce_seconds=0)
        assert "It would NOT fire right now" not in out, out

        _engine().poll()
        assert _CALLS == [], "a deadline 30 days out must not dispatch"

        _write_deadline(time.time() - 10.0)          # now inside the 1d threshold
        _engine().poll()
        assert len(_CALLS) == 1, (
            f"an armed expiry rule reached its threshold and dispatched "
            f"{_CALLS}"
        )
        actuator, arguments, origin = _CALLS[0]
        assert actuator == "notify"
        assert origin == triggers._ORIGIN, (
            "an unattended action that does not record itself as unattended is "
            "indistinguishable from one the user asked for"
        )

        _shut("expiry-sense-enabled")
        _write_deadline(time.time() - 20.0)          # a further new state
        _engine().poll()
        assert len(_CALLS) == 1, (
            f"an expiry rule dispatched again after its key was revoked: "
            f"{_CALLS}"
        )


def _schema_keys() -> "set[str]":
    """Key names as parsed from the shipped XML.

    The XML rather than `_valid_keys`, deliberately: the registration defect
    is "the switch is not there", and asserting on the compiled store would
    report its symptom.
    """
    node = ET.parse(GSCHEMA_XML).getroot()
    return {
        key.get("name") for key in node.iter("key")
        if key.get("name")
    }


def _schema_key_defaults() -> dict:
    """`{key name: <default> text}` from the shipped XML."""
    node = ET.parse(GSCHEMA_XML).getroot()
    return {
        key.get("name"): key.findtext("default")
        for key in node.iter("key")
        if key.get("name")
    }


# --- the evaluation path -------------------------------------------------------

class TestTheRealLoopEvaluatesIt:
    def test_a_scheduled_poll_reads_the_signal_and_dispatches(self, consent, tmp_path):
        repo = _git_repo()
        _arm(repo)
        scheduler = _scheduler()
        scheduler.poll_due()  # baseline: nothing has changed yet
        assert _CALLS == []

        _commit(repo, "second")
        scheduler.poll_due()

        assert len(_CALLS) == 1, f"expected exactly one dispatch, got {_CALLS}"
        actuator, arguments, origin = _CALLS[0]
        assert actuator == "notify"
        assert arguments == {"summary": "changed"}
        assert origin == triggers._ORIGIN, (
            "an unattended actuation that does not record itself as unattended is "
            "indistinguishable from one the user asked for"
        )

    def test_one_transition_dispatches_exactly_once(self, consent, tmp_path):
        """`poll()` then `run_due()` on the same tick, which is what the loop does.

        `feed()` submits to the debounce window unconditionally and then, when
        the delay has already elapsed, dispatches directly - leaving the window
        armed for `run_due()` to fire the same event a second time. Found by
        running the real CLI against a real repository: two `notify` calls in
        `tool_calls.log` for one commit.
        """
        repo = _git_repo()
        _arm(repo)
        scheduler = _scheduler()
        scheduler.poll_due()
        _commit(repo, "second")
        scheduler.poll_due()
        assert len(_CALLS) == 1, f"one commit dispatched {_CALLS}"

    def test_a_positive_debounce_dispatches_once_when_it_comes_due(
            self, consent, tmp_path):
        repo = _git_repo()
        _arm(repo, debounce_seconds=0.05)
        engine = _engine()
        engine.poll()
        _commit(repo, "second")
        assert engine.poll() and not _CALLS, "debounced: nothing may act yet"
        import time as _time
        _time.sleep(0.06)
        engine.run_due()
        assert len(_CALLS) == 1

    def test_an_unchanged_source_does_not_fire_again(self, consent, tmp_path):
        repo = _git_repo()
        _arm(repo)
        scheduler = _scheduler()
        scheduler.poll_due()
        _commit(repo, "second")
        scheduler.poll_due()
        assert len(_CALLS) == 1
        for _ in range(3):
            scheduler.poll_due()
        assert len(_CALLS) == 1, f"re-fired on an unchanged repo: {_CALLS}"

    def test_a_scheduler_with_no_engine_reads_no_event_rules(self, consent, tmp_path):
        """The default must stay inert, or every existing construction site
        starts touching the user's rules file."""
        repo = _git_repo()
        _arm(repo)
        scheduler = _scheduler(event_engine=None)
        scheduler.poll_due()
        _commit(repo, "second")
        scheduler.poll_due()
        assert _CALLS == []
        assert scheduler.summary()["event_triggers"]["enabled"] is False

    def test_a_raising_event_engine_does_not_stop_the_senses_being_polled(
            self, consent, tmp_path):
        """The containment lives in the loop, not in the engine.

        A corrupt `event_rules.json` is refused at *construction* - `RuleStore`
        loads eagerly, so `EventEngine()` raises, which is the fail-closed
        behaviour it documents. What must not happen is a failure *mid-poll*
        taking the sense loop down with it, so that is what is driven here.
        """
        class _Exploding:
            def __init__(self):
                self.reads = 0

            def poll(self):
                self.reads += 1
                raise RuntimeError("the rules file is corrupt")

            def run_due(self):
                raise AssertionError("run_due must not be reached")

            def store(self):
                class _S:
                    @staticmethod
                    def all():
                        return []
                return _S()

        engine = _Exploding()
        scheduler = _scheduler(event_engine=engine)
        scheduler.poll_due()  # must not raise
        assert engine.reads == 1
        assert scheduler.summary()["event_triggers"]["polls"] == 0
        scheduler.poll_due()  # still contained, still polling
        assert engine.reads == 2

    def test_a_corrupt_rules_file_is_refused_rather_than_silently_emptied(
            self, consent, tmp_path):
        """Fail-closed at construction, which is the only point the store can see it."""
        Path(triggers.EVENT_RULES_FILE).write_text("{not json", encoding="utf-8")
        with pytest.raises(triggers.RuleStoreError):
            triggers.EventRuleStore()

    def test_the_loop_wakes_for_the_event_interval_with_no_ambient_senses(
            self, consent, tmp_path):
        """Otherwise it falls back to the one-second idle tick and re-runs
        `git status` every second for the life of the thread."""
        scheduler = _scheduler(event_poll_seconds=15.0)
        assert scheduler.ambient_senses() == {}
        # Due immediately on the first tick, as a sense is.
        assert scheduler.seconds_until_next_due() == 0.0
        scheduler.poll_due()
        # And then it backs off to its own interval rather than the idle tick:
        # `git status` on a large tree and `systemctl show` on a busy machine
        # are both unbounded in the kernel, and `triggers._SIGNAL_TIMEOUT_
        # SECONDS` exists because of it.
        assert scheduler.seconds_until_next_due() > 1.0
        assert scheduler.summary()["event_triggers"]["interval_seconds"] == 15.0

    def test_the_summary_reports_the_armed_rules(self, consent, tmp_path):
        repo = _git_repo()
        _arm(repo)
        scheduler = _scheduler()
        scheduler.poll_due()
        events = scheduler.summary()["event_triggers"]
        assert events["rules"] == ["repo-changed"]
        assert events["polls"] == 1
        assert events["fired"] == 0
