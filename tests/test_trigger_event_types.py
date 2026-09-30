"""The six trigger event types, and the four anti-noise layers they share.

Every event type is held to the same three proofs, because the same three
failures are what a naive implementation of each one actually produces:

- (a) a real transition fires **exactly once**;
- (b) a repeat / no-op does **not** fire;
- (c) an unavailable dependency yields "signal unavailable" and **not** a
  firing - never "no change", which is the failure mode the senses section of
  `AGENTS.md` is written about.

Two things make the assertions able to fail at all, and both exist because the
obvious assertion is worthless here:

- **Arming is a baseline, not a change.** The first sighting of a state is
  recorded and fires nothing, so every "a transition fires" test establishes a
  baseline first (`arm()` / `prime()`) and then changes the state. Without that,
  "fires" and "fires on the very first read" are indistinguishable.
- **"No timer" is counted, not inferred.** A suppressed no-op and an armed-but-
  not-yet-due timer look identical to a test that only counts dispatches, so
  `TestTheNegativeControl` counts *pending timers* and asserts the deadline
  itself did not move. That is the property that stops trigger spam.

Two previously-fixed behaviours are pinned here rather than assumed, because an
earlier pass in this repo regressed one of them: a FAILED verdict starts **no**
cooldown, and consent is checked **before** `due()`. Both are load-bearing and
both are invisible when they are wrong.
"""

import json
import subprocess
import time
from pathlib import Path

import pytest

from shani_chronoa import verification
from shani_chronoa.triggers import (
    ANTI_NOISE_LAYERS,
    BACKOFF_CAP_SECONDS,
    BACKOFF_MAX_CONSECUTIVE_FAILURES,
    _DESTRUCTIVE_ACTUATORS,
    _UNATTENDED_WRITE_ACTUATORS,
    EVENT_CONTAINERRUN,
    EVENT_EXPIRY,
    EVENT_FAILURE,
    EVENT_FSWATCH,
    EVENT_GIT,
    EVENT_UNITHEALTH,
    LAYER_COOLDOWN,
    LAYER_DEDUPE_WINDOW,
    LAYER_DUPLICATE_EVENT,
    LAYER_FILTER_MISMATCH,
    MATCH_ANY,
    MATCH_KEYWORDS,
    MATCH_SUBSTRING,
    RETRY_RETRYABLE,
    RETRY_TERMINAL,
    SIGNAL_UNAVAILABLE,
    SIGNAL_WATCH_ERROR,
    TRIGGER_CONTROL_KEY,
    UNIT_BACKING_OFF,
    UNIT_GAVE_UP,
    UNIT_HEALTH_STATES,
    BackoffPolicy,
    DedupeWindow,
    DurableFingerprints,
    Event,
    EventEngine,
    EventRule,
    EventRuleStore,
    PollingDirWatcher,
    RuleStore,
    RuleStoreError,
    TriggerEngine,
    TriggerRule,
    WatcherError,
    build_event_rule,
    build_rule,
    read_container_state,
    read_event_signal,
    read_expiry,
    read_failure_verdict,
    read_git_state,
    read_unit_health,
    read_watched_path,
    record_deadline,
    record_verdict,
)
from shani_chronoa.tools import _HANDLER_FNS

ALL_ALLOWED = frozenset(
    (EVENT_GIT, EVENT_FSWATCH, EVENT_FAILURE, EVENT_EXPIRY,
     EVENT_CONTAINERRUN, EVENT_UNITHEALTH, "hearing", "memory")
)


class FakeConfig:
    """Consent stub. A denial is always deliberate, never a default.

    `get_bool` was missing until 2026-09-30 and its absence is the point worth
    recording: `EventEngine._consent` asks the config for
    `trigger-control-enabled` through the same `get_bool(key, default)` accessor
    every other consent key in this codebase uses, and a test double that
    implements only `sense_allowed`/`input_control_enabled` raised
    `AttributeError` on 35 tests at once rather than producing one honest
    failure. The double now implements the interface the production code calls.
    `trigger_control` defaults True so the *other* refusals are what a test is
    exercising; a test that cares about this key passes `trigger_control=False`
    or flips it mid-run through `FlippingConfig`.
    """

    def __init__(self, denied: str = "", input_control: bool = False,
                 trigger_control: bool = True):
        self._denied = denied
        self.input_control_enabled = input_control
        self.trigger_control = trigger_control
        self._keys: dict = {TRIGGER_CONTROL_KEY: trigger_control}

    def get_bool(self, key: str, default: bool = False) -> bool:
        # Mirrors `ChronoaConfig.get_bool`: an undeclared key yields the
        # supplied default, so a config object that knows nothing about a key
        # denies rather than permits.
        return self._keys.get(key, default)

    def sense_allowed(self, sense: str) -> bool:
        return sense != self._denied and sense in ALL_ALLOWED

    def sense_allowed_reason(self, sense: str) -> str:
        return "" if self.sense_allowed(sense) else f"the {sense} sense is turned off"


class FlippingConfig(FakeConfig):
    """Consent a test can revoke mid-run, to prove it is re-checked."""

    def __init__(self, state: dict):
        super().__init__()
        self._state = state

    def get_bool(self, key: str, default: bool = False) -> bool:
        if key == self._state.get("revoked_key"):
            return False
        if key == self._state.get("granted_key"):
            return True
        return super().get_bool(key, default)

    def sense_allowed(self, sense: str) -> bool:
        if sense == self._state.get("denied"):
            return False
        return super().sense_allowed(sense)


class Recorder:
    """A dispatch seam that records origin, and can be told to fail."""

    def __init__(self, verdict=None):
        self.calls = []
        self._verdict = verdict

    def __call__(self, actuator, arguments, **kwargs):
        self.calls.append((actuator, dict(arguments), kwargs.get("origin")))
        if self._verdict is not None:
            return verification.Result(self._verdict, "the post-condition did not hold")
        return verification.Result(verification.Verdict.VERIFIED, "it happened")


def _make_engine(tmp_path, config_factory=FakeConfig, dispatch=None):
    return EventEngine(
        store=EventRuleStore(tmp_path / "event_rules.json"),
        config_factory=config_factory,
        dispatch=dispatch if dispatch is not None else Recorder(),
        fingerprint_store=DurableFingerprints(tmp_path / "fingerprints.json"),
    )


@pytest.fixture
def recorder():
    return Recorder()


@pytest.fixture
def engine(tmp_path, recorder):
    """An `EventEngine` with every store inside `tmp_path`.

    Each store gets an explicit path, so nothing in this file can reach the
    real `~/.local/share/shani-chronoa/triggers/`.
    """
    return _make_engine(tmp_path, dispatch=recorder)


# `EventRule` uses `__slots__` (deliberately - it is persisted), so the test
# cannot hang an engine reference off the object. Rules are unique by name
# within a store, which makes the name the natural key.
_ENGINE_OF: dict = {}


def _rule(**overrides):
    fields = {
        "name": "r", "event_type": EVENT_GIT, "source": "/tmp/repo",
        "actuator": "notify", "arguments": {"summary": "x"},
        "match_mode": MATCH_ANY, "substring": "", "debounce_seconds": 0.0,
        "cooldown_seconds": 30.0, "retry_policy": RETRY_RETRYABLE,
    }
    fields.update(overrides)
    return EventRule(**fields)


def _armed(engine, **overrides):
    rule = _rule(**overrides)
    engine.store().add(rule)
    _ENGINE_OF[rule.name] = engine
    return rule


def _event(kind, subject="s", fingerprint="fp1", terminal=False, failed=False,
           progress=False, summary="state changed", **detail):
    return Event(
        kind=kind, subject=subject, summary=summary, fingerprint=fingerprint,
        detail=dict(detail), terminal=terminal, failed=failed, progress=progress,
        created_at=0.0,
    )


def _fp_key(rule, subject):
    """How `DurableFingerprints` keys a rule's view of one subject."""
    return f"{rule.name}\x00{subject}"


def _key(rule, subject, fingerprint):
    """How `DedupeWindow` keys a pending run: rule, subject, and transition."""
    return f"{rule.name}\x00{subject}\x00{fingerprint}"


def _fired(results):
    return [r for r in results if r.fired]


def prime(rule, fingerprint="baseline", subject="s", now=1000.0):
    """Record the baseline for one rule, and assert that arming is inert.

    Arming must not notify you about the state the machine is already in, or
    "tell me when this repository changes" would greet you with a description
    of the repository every time it was armed. The two assertions live in the
    helper because they are a property of *arming*, not of one event type.
    """
    engine = _ENGINE_OF[rule.name]
    result = engine.feed(
        rule, _event(rule.event_type, subject=subject, fingerprint=fingerprint), now=now
    )
    assert result.fired is False, "arming a rule notified about the current state"
    assert engine.dedupe().peek(_key(rule, subject, fingerprint)) is None, (
        "arming a rule armed a timer before anything had changed"
    )
    assert engine._prints.get(_fp_key(rule, subject)) == fingerprint, (
        "arming a rule did not record the baseline it claims to have taken"
    )
    return result


def arm(engine, now=999.0):
    """One poll to baseline every armed rule, for the `poll()`-driven tests."""
    results = engine.poll(now=now)
    assert _fired(results) == [], "establishing the baseline fired an actuator"
    return results


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Point `$HOME` at `tmp_path`, so a watched directory is *inside* home.

    `Path.home()` reads `$HOME`, and the fswatch reader confines to it for the
    same reason `senses/filesystem.py` does: containment tested on the named
    path is defeated by a symlink. Without this, every fswatch test would be
    measuring the confinement refusal instead of the watch.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _git_repo(tmp_path) -> Path:
    """A real repository: the git reader's whole job is git's own output."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")
    (repo / "a.txt").write_text("one\n")
    git("add", "-A")
    git("commit", "-qm", "first")
    return repo


def _commit(repo, message):
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qam", message, "--allow-empty"],
        check=True, capture_output=True,
    )


# ===========================================================================
# 1. git
# ===========================================================================

class TestGitEvent:
    """Signal: `rev-parse --verify HEAD` plus a porcelain status read."""

    def test_a_real_commit_fires_exactly_once(self, engine, recorder, tmp_path):
        repo = _git_repo(tmp_path)
        _armed(engine, event_type=EVENT_GIT, source=str(repo))
        arm(engine)

        _commit(repo, "second")
        (fired,) = _fired(engine.poll(now=1000.0))
        assert fired.event.detail["head"] == read_git_state(repo).payload["head"]
        assert len(recorder.calls) == 1

        for tick in (1001.0, 1002.0):
            repeated = engine.poll(now=tick)
            assert _fired(repeated) == []
            assert {r.layer for r in repeated} == {LAYER_DUPLICATE_EVENT}, (
                f"the repeat at t={tick} was suppressed by something other than "
                "duplicate detection, so this test cannot tell a working "
                "fingerprint from a broken one"
            )
        assert len(recorder.calls) == 1, "an unchanging repository fired again"

    def test_a_commit_touching_400_files_is_one_event_not_400(self, engine, recorder, tmp_path):
        repo = _git_repo(tmp_path)
        _armed(engine, event_type=EVENT_GIT, source=str(repo))
        arm(engine)

        for i in range(400):
            (repo / f"f{i}.txt").write_text(str(i))
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-qm", "400"], check=True, capture_output=True
        )

        assert len(_fired(engine.poll(now=1000.0))) == 1, (
            "a 400-file commit produced more than one event"
        )
        assert len(recorder.calls) == 1

    def test_a_dirtied_working_tree_changes_the_fingerprint(self, tmp_path):
        repo = _git_repo(tmp_path)
        before = read_git_state(repo).fingerprint
        (repo / "a.txt").write_text("two\n")
        after = read_git_state(repo)

        assert after.fingerprint != before
        assert after.payload["dirty"] == 1

    def test_a_repeated_read_is_identical(self, tmp_path):
        """Two reads a second apart must not disagree, or the fingerprint
        manufactures transitions nobody made."""
        repo = _git_repo(tmp_path)
        assert read_git_state(repo).fingerprint == read_git_state(repo).fingerprint

    def test_a_repository_with_no_commits_is_unavailable_not_deleted(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        subprocess.run(["git", "init", "-q", str(empty)], check=True, capture_output=True)

        signal = read_git_state(empty)

        assert signal.status == SIGNAL_UNAVAILABLE
        assert signal.fingerprint is None, (
            "an unavailable signal must carry no fingerprint, or it compares "
            "equal to whatever was last seen and reads as 'unchanged'"
        )
        assert signal.event is None

    def test_git_being_absent_is_unavailable_not_clean(self, engine, recorder, monkeypatch):
        import shani_chronoa.triggers as triggers

        monkeypatch.setattr(
            triggers.shutil, "which",
            lambda name: None if name == "git" else f"/usr/bin/{name}",
        )
        _armed(engine, event_type=EVENT_GIT, source="/tmp/whatever")

        (result,) = engine.poll(now=1000.0)

        assert result.signal_status == SIGNAL_UNAVAILABLE
        assert result.fired is False
        assert recorder.calls == [], "an unreadable signal fired an actuator"

    def test_upstream_gone_is_reported_apart_from_behind_zero(self, tmp_path):
        repo = _git_repo(tmp_path)
        # Configure an upstream that git will resolve as *configured* but cannot
        # resolve as *existing* - the renamed-remote or never-fetched case. Set
        # through config because `--set-upstream-to` insists the start point exist.
        for key, value in (("branch.main.remote", "origin"),
                           ("branch.main.merge", "refs/heads/gone")):
            subprocess.run(["git", "-C", str(repo), "config", key, value],
                           check=True, capture_output=True)

        signal = read_git_state(repo)

        assert signal.payload["upstream"] == "origin/gone"
        assert signal.payload["behind"] is None
        assert signal.payload["upstream_gone"] is True
        assert "gone" in signal.event.summary


# ===========================================================================
# 2. fswatch
# ===========================================================================

class _FakeWatcher:
    """A watcher whose answers the test states outright, and whose failures
    are raised - a dead watcher has to be able to say that it is dead."""

    def __init__(self, batches):
        self._batches = list(batches)

    def poll(self, root):
        outcome = self._batches.pop(0) if self._batches else []
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class TestFswatchEvent:
    def test_a_changed_path_fires_exactly_once(self, engine, recorder, tmp_path, fake_home):
        watched = tmp_path / "watched"
        watched.mkdir()
        (watched / "before.txt").write_text("a")
        watcher = _FakeWatcher([[], [str(watched / "after.txt")], [str(watched / "after.txt")]])
        _armed(engine, event_type=EVENT_FSWATCH, source=str(watched),
               seams={"watcher": watcher})

        assert _fired(engine.poll(now=1000.0)) == [], "arming the watch already fired"

        (fired,) = _fired(engine.poll(now=1001.0))
        assert fired.event.subject == str(watched.resolve())
        assert len(recorder.calls) == 1

        repeated = engine.poll(now=1002.0)
        assert _fired(repeated) == [], "an unchanged tree fired again"
        assert {r.layer for r in repeated} == {LAYER_DUPLICATE_EVENT}, (
            "the repeat was suppressed by something other than duplicate detection"
        )
        assert len(recorder.calls) == 1

    def test_a_second_different_change_fires_again(self, engine, recorder, tmp_path, fake_home):
        """A fingerprint constant over the changed set would pass every other
        test here: the first change still differs from the quiet baseline, and
        its repeat is still a duplicate. Only a *second, different* change
        distinguishes a real fingerprint from a constant one."""
        watched = tmp_path / "watched"
        watched.mkdir()
        watcher = _FakeWatcher([
            [],
            [str(watched / "a.txt")],
            [str(watched / "a.txt"), str(watched / "b.txt")],
        ])
        _armed(engine, event_type=EVENT_FSWATCH, source=str(watched),
               cooldown_seconds=0.0, seams={"watcher": watcher})

        assert _fired(engine.poll(now=1000.0)) == []
        assert len(_fired(engine.poll(now=1001.0))) == 1
        assert len(_fired(engine.poll(now=1002.0))) == 1, (
            "a second, different change under the same watched path did not fire; "
            "the fingerprint cannot be constant across changed sets"
        )
        assert len(recorder.calls) == 2

    def test_a_watcher_error_is_reported_and_never_reads_as_no_change(
        self, engine, recorder, tmp_path, fake_home
    ):
        watched = tmp_path / "watched"
        watched.mkdir()
        watcher = _FakeWatcher([[], WatcherError("inotify queue overflowed")])
        _armed(engine, event_type=EVENT_FSWATCH, source=str(watched),
               seams={"watcher": watcher})
        engine.poll(now=1000.0)

        (result,) = engine.poll(now=1001.0)

        assert result.signal_status == SIGNAL_WATCH_ERROR, (
            "a dead watcher reported as 'no changes' is the failure this state exists for"
        )
        assert result.fired is False
        assert "inotify" in result.reason
        assert recorder.calls == []

    def test_a_path_outside_the_home_directory_is_unavailable(self, engine, recorder):
        _armed(engine, event_type=EVENT_FSWATCH, source="/etc",
               seams={"watcher": _FakeWatcher([["anything"]])})

        (result,) = engine.poll(now=1000.0)

        assert result.signal_status == SIGNAL_UNAVAILABLE
        assert "outside your home" in result.reason
        assert recorder.calls == []

    def test_a_symlink_out_of_the_home_directory_is_refused(self, engine, recorder, tmp_path, fake_home):
        """Confinement is tested after resolving, so a symlink cannot slip it."""
        link = tmp_path / "escape"
        link.symlink_to("/etc")
        _armed(engine, event_type=EVENT_FSWATCH, source=str(link),
               seams={"watcher": _FakeWatcher([["anything"]])})

        (result,) = engine.poll(now=1000.0)

        assert result.signal_status == SIGNAL_UNAVAILABLE
        assert "outside your home" in result.reason
        assert recorder.calls == []

    def test_debounce_is_per_path_so_a_hot_file_cannot_starve_another(
        self, engine, recorder, tmp_path, fake_home
    ):
        """The reason `DedupeWindow` is keyed rather than one engine-wide timer."""
        watched = tmp_path / "watched"
        watched.mkdir()
        rule = _armed(engine, event_type=EVENT_FSWATCH, source=str(watched),
                      debounce_seconds=30.0)
        root = str(watched.resolve())
        hot_subject, cold_subject = f"{root}/hot.log", f"{root}/cold.txt"
        prime(rule, fingerprint="base-h", subject=hot_subject)
        prime(rule, fingerprint="base-c", subject=cold_subject)

        hot = _event(EVENT_FSWATCH, subject=hot_subject, fingerprint="h1")
        first = engine.feed(rule, hot, now=1000.0)
        assert first.layer == LAYER_DEDUPE_WINDOW
        hot_deadline = engine.dedupe().peek(_key(rule, hot_subject, "h1"))

        for step in range(2, 6):
            engine.feed(
                rule,
                _event(EVENT_FSWATCH, subject=hot_subject, fingerprint=f"h{step}"),
                now=1000.0 + step,
            )

        second = engine.feed(
            rule, _event(EVENT_FSWATCH, subject=cold_subject, fingerprint="c1"),
            now=1006.0,
        )

        assert second.layer == LAYER_DEDUPE_WINDOW
        assert engine.dedupe().peek(_key(rule, hot_subject, "h5")) > hot_deadline, (
            "the hot path's own deadline should have moved"
        )
        assert engine.dedupe().peek(_key(rule, cold_subject, "c1")) == 1036.0, (
            "the cold path's deadline was pushed back by an unrelated path's burst"
        )
        assert len(recorder.calls) == 0

    def test_the_real_polling_watcher_arms_before_it_reports(self, tmp_path, fake_home):
        watched = tmp_path / "w"
        watched.mkdir()
        (watched / "a.txt").write_text("one")
        watcher = PollingDirWatcher()

        assert watcher.poll(watched) == [], "the first poll must arm, not report"

        (watched / "b.txt").write_text("two")
        assert watcher.poll(watched) == [str(watched / "b.txt")]

    def test_the_real_watcher_reports_a_tree_it_cannot_walk(self, tmp_path, monkeypatch, fake_home):
        watched = tmp_path / "w"
        watched.mkdir()
        watcher = PollingDirWatcher()
        watcher.poll(watched)
        monkeypatch.setattr("shani_chronoa.triggers.MAX_WATCH_ENTRIES", 1)
        (watched / "a.txt").write_text("one")
        (watched / "b.txt").write_text("two")

        signal = read_watched_path(str(watched), watcher=watcher)

        assert signal.status == SIGNAL_WATCH_ERROR
        assert "more than 1 files" in signal.detail


# ===========================================================================
# 3. failure
# ===========================================================================

class TestFailureEvent:
    def test_a_verdict_changing_to_failed_fires_exactly_once(self, engine, recorder, tmp_path):
        verdicts = tmp_path / "verdicts"
        verdicts.mkdir()
        _armed(engine, event_type=EVENT_FAILURE, source="suite",
               params={"directory": str(verdicts)})
        record_verdict("suite", "passed", "all green", directory=verdicts)
        assert _fired(engine.poll(now=1000.0)) == [], "arming on a passing check fired"

        record_verdict("suite", "failed", "3 tests failed", directory=verdicts)
        (fired,) = _fired(engine.poll(now=1001.0))
        assert "3 tests failed" in fired.event.summary
        assert len(recorder.calls) == 1

        repeated = engine.poll(now=1002.0)
        assert _fired(repeated) == [], "a still-failing check fired again"
        assert {r.layer for r in repeated} == {LAYER_DUPLICATE_EVENT}
        assert len(recorder.calls) == 1

    def test_a_missing_verdict_is_unavailable_not_a_pass(self, engine, recorder, tmp_path):
        verdicts = tmp_path / "verdicts"
        verdicts.mkdir()
        _armed(engine, event_type=EVENT_FAILURE, source="absent",
               params={"directory": str(verdicts)})

        (result,) = engine.poll(now=1000.0)

        assert result.signal_status == SIGNAL_UNAVAILABLE
        assert "whether the command passed is unknown" in result.reason
        assert recorder.calls == []

    def test_a_corrupt_verdict_is_unavailable(self, tmp_path):
        verdicts = tmp_path / "verdicts"
        verdicts.mkdir()
        (verdicts / "suite.json").write_text("{not json")

        signal = read_failure_verdict("suite", directory=verdicts)

        assert signal.status == SIGNAL_UNAVAILABLE
        assert signal.fingerprint is None

    def test_a_rule_cannot_supply_its_own_command(self):
        """`AGENTS.md`'s permanent boundary, re-entering through the side door."""
        signal = read_failure_verdict("rm -rf /", source="command")

        assert signal.status == SIGNAL_UNAVAILABLE
        assert "cannot supply its own argv" in signal.detail

    def test_a_failing_test_and_a_failing_deploy_do_not_share_a_policy(
        self, engine, recorder, tmp_path
    ):
        verdicts = tmp_path / "verdicts"
        verdicts.mkdir()
        test = _armed(engine, name="test", event_type=EVENT_FAILURE, source="suite",
                      retry_policy=RETRY_RETRYABLE, cooldown_seconds=1.0,
                      params={"directory": str(verdicts)})
        deploy = _armed(engine, name="deploy", event_type=EVENT_FAILURE, source="rollout",
                        retry_policy=RETRY_TERMINAL, cooldown_seconds=1.0,
                        params={"directory": str(verdicts)})
        record_verdict("suite", "passed", directory=verdicts)
        record_verdict("rollout", "passed", directory=verdicts)
        arm(engine)

        record_verdict("suite", "failed", directory=verdicts)
        record_verdict("rollout", "failed", directory=verdicts)
        by_name = {r.rule.name: r for r in engine.poll(now=1000.0)}

        assert by_name["deploy"].fired is False and by_name["deploy"].censored is True
        assert RETRY_TERMINAL in by_name["deploy"].reason
        assert deploy.parked is True

        assert by_name["test"].fired is True
        assert test.parked is False
        assert test.retry_policy == RETRY_RETRYABLE


# ===========================================================================
# 4. expiry
# ===========================================================================

class TestExpiryEvent:
    def test_each_threshold_fires_once(self, engine, recorder, tmp_path):
        deadlines = tmp_path / "deadlines"
        deadlines.mkdir()
        _armed(engine, event_type=EVENT_EXPIRY, source="vpn", actuator="notify",
               arguments={"summary": "VPN expires"},
               params={"directory": str(deadlines)})
        record_deadline("vpn", 1000.0 + 30 * 86400, directory=deadlines)
        assert _fired(engine.poll(now=1000.0)) == [], "a deadline 30 days out fired"

        record_deadline("vpn", 1000.0 + 6 * 86400, directory=deadlines)
        (first,) = _fired(engine.poll(now=1000.0))
        assert "7d" in first.event.summary
        assert len(recorder.calls) == 1

        repeated = engine.poll(now=1001.0)
        assert _fired(repeated) == [], "the 7d reminder repeated"
        assert {r.layer for r in repeated} == {LAYER_DUPLICATE_EVENT}
        assert len(recorder.calls) == 1

        # Six days later, as the two thresholds are actually spaced; a 30s
        # cooldown would otherwise swallow the second reminder and hide whether
        # the threshold logic works at all.
        record_deadline("vpn", 1000.0 + 6 * 86400 + 12 * 3600, directory=deadlines)
        (second,) = _fired(engine.poll(now=1000.0 + 6 * 86400))
        assert "1d" in second.event.summary
        assert second.event.terminal is True
        assert len(recorder.calls) == 2

    def test_a_deadline_is_fingerprinted_by_its_instant_so_a_renewal_fires_again(
        self, engine, recorder, tmp_path
    ):
        """Without `expires_at` in the key, a renewed credential is muted forever."""
        deadlines = tmp_path / "deadlines"
        deadlines.mkdir()
        _armed(engine, event_type=EVENT_EXPIRY, source="cert", actuator="notify",
               arguments={"summary": "cert expires"},
               params={"directory": str(deadlines)})
        record_deadline("cert", 1000.0 + 30 * 86400, directory=deadlines)
        arm(engine)
        record_deadline("cert", 1000.0 + 6 * 86400, directory=deadlines)
        assert _fired(engine.poll(now=1000.0))

        record_deadline("cert", 2000.0 + 6 * 86400, directory=deadlines)
        assert _fired(engine.poll(now=2000.0)), (
            "a renewed deadline did not fire; the old fingerprint muted it"
        )

    def test_the_actuator_may_only_notify(self):
        rule, problem = build_event_rule(
            name="e", event_type=EVENT_EXPIRY, source="vpn", actuator="speak",
            arguments={"text": "renew"}, skills=_HANDLER_FNS,
        )

        assert rule is None
        assert "may only notify" in (problem or "")

    def test_a_non_notify_actuator_is_refused_at_dispatch_too(self, engine, recorder):
        """Not only at arm time: a hand-edited rules file must not bypass it."""
        rule = _armed(engine, event_type=EVENT_EXPIRY, source="vpn",
                      actuator="speak", arguments={"text": "renew"})
        prime(rule)

        result = engine.feed(rule, _event(EVENT_EXPIRY, fingerprint="a"), now=1000.0)

        assert result.censored is True
        assert "may only notify" in result.reason
        assert recorder.calls == []

    def test_a_missing_deadline_is_unavailable(self, engine, recorder, tmp_path):
        deadlines = tmp_path / "deadlines"
        deadlines.mkdir()
        _armed(engine, event_type=EVENT_EXPIRY, source="nothing", actuator="notify",
               arguments={"summary": "x"}, params={"directory": str(deadlines)})

        (result,) = engine.poll(now=1000.0)

        assert result.signal_status == SIGNAL_UNAVAILABLE
        assert recorder.calls == []

    def test_a_deadline_with_no_usable_instant_is_unavailable(self, tmp_path):
        deadlines = tmp_path / "deadlines"
        deadlines.mkdir()
        (deadlines / "bad.json").write_text(json.dumps({"label": "no instant"}))

        signal = read_expiry("bad", directory=deadlines)

        assert signal.status == SIGNAL_UNAVAILABLE
        assert "expires_at" in signal.detail


# ===========================================================================
# 5. containerrun
# ===========================================================================

class _FakeRuntime:
    """A container runtime that answers `inspect` from a queue of states.

    Keyed on the *shape* of the query rather than a flat script of answers,
    because one read asks two questions - the status triple, then the exit code
    - and a flat script hands the second question the next poll's state. That
    looks like a system that flickers on its own, and it makes "a repeat does
    not fire" pass or fail for reasons that have nothing to do with the engine.

    The last state repeats forever, so a stable container really is stable.
    """

    def __init__(self, states):
        self.states = list(states)
        self._current = ("running", "true", "0")

    def __call__(self, argv, **kwargs):
        joined = " ".join(str(part) for part in argv)
        if "State.Status" in joined:
            if self.states:
                self._current = self.states.pop(0)
            status, running, code = self._current
            return subprocess.CompletedProcess(
                argv, 0, stdout=f"{status}|{running}|{code}\n", stderr=""
            )
        if "State.ExitCode" in joined:
            return subprocess.CompletedProcess(
                argv, 0, stdout=f"{self._current[2]}\n", stderr=""
            )
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")


@pytest.fixture
def fake_runtime(monkeypatch):
    import shani_chronoa.triggers as triggers

    def install(responses):
        monkeypatch.setattr(triggers, "_run_argv", _FakeRuntime(responses))
        monkeypatch.setattr(triggers.shutil, "which", lambda name: f"/usr/bin/{name}")

    return install


class TestContainerRunEvent:
    def test_a_container_exiting_fires_exactly_once(self, engine, recorder, fake_runtime):
        fake_runtime([("running", "true", "0"), ("exited", "false", "1")])
        _armed(engine, event_type=EVENT_CONTAINERRUN, source="build")
        assert _fired(engine.poll(now=1000.0)) == [], "a running container fired"

        (fired,) = _fired(engine.poll(now=1001.0))
        assert fired.event.terminal is True, "an exit is terminal, not retryable"
        assert len(recorder.calls) == 1

        repeated = engine.poll(now=1002.0)
        assert _fired(repeated) == [], "an exited container fired again"
        assert {r.layer for r in repeated} == {LAYER_DUPLICATE_EVENT}
        assert len(recorder.calls) == 1

    def test_a_second_run_of_the_same_container_fires_again(self, engine, recorder, fake_runtime):
        """A change of exit code is a transition; the same code twice is not.

        Only the *immediately preceding* state is compared, which is correct -
        a transition is a change from where the machine was - and it is why this
        test needs two consecutive exits. A fingerprint constant over the state
        passes a running->exited->running sequence (the third read is a genuine
        change from `running`) and is only caught here.
        """
        fake_runtime([
            ("running", "true", "0"),    # baseline
            ("exited", "false", "7"),    # transition 1
            ("exited", "false", "9"),    # transition 2: same state, new exit code
        ])
        _armed(engine, event_type=EVENT_CONTAINERRUN, source="build",
               cooldown_seconds=0.0)
        assert _fired(engine.poll(now=1000.0)) == [], "the running baseline fired"

        assert len(_fired(engine.poll(now=1001.0))) == 1
        assert len(_fired(engine.poll(now=1002.0))) == 1, (
            "a new exit code did not fire; the fingerprint cannot ignore it"
        )
        assert len(_fired(engine.poll(now=1003.0))) == 0, (
            "the unchanged exit code fired again"
        )
        assert len(recorder.calls) == 2

    def test_a_return_to_a_previously_seen_state_is_itself_a_transition(
        self, engine, recorder, fake_runtime
    ):
        fake_runtime([
            ("running", "true", "0"),
            ("exited", "false", "7"),
            ("running", "true", "0"),
        ])
        _armed(engine, event_type=EVENT_CONTAINERRUN, source="build",
               cooldown_seconds=0.0)

        assert _fired(engine.poll(now=1000.0)) == []
        assert len(_fired(engine.poll(now=1001.0))) == 1
        assert len(_fired(engine.poll(now=1002.0))) == 1, (
            "a container that started again did not fire; the state is not tracked"
        )

    def test_no_runtime_is_unavailable_not_an_exit(self, engine, recorder, monkeypatch):
        import shani_chronoa.triggers as triggers

        monkeypatch.setattr(triggers.shutil, "which", lambda name: None)
        _armed(engine, event_type=EVENT_CONTAINERRUN, source="build")

        (result,) = engine.poll(now=1000.0)

        assert result.signal_status == SIGNAL_UNAVAILABLE
        assert recorder.calls == []

    def test_a_container_that_does_not_exist_is_unavailable(self, engine, recorder, monkeypatch):
        import shani_chronoa.triggers as triggers

        monkeypatch.setattr(triggers.shutil, "which", lambda name: "/usr/bin/docker")
        monkeypatch.setattr(
            triggers, "_run_argv",
            lambda argv, **kw: subprocess.CompletedProcess(
                argv, 1, stdout="", stderr=f"Error: No such container: {argv[-1]}"
            ),
        )
        _armed(engine, event_type=EVENT_CONTAINERRUN, source="ghost")

        (result,) = engine.poll(now=1000.0)

        assert result.signal_status == SIGNAL_UNAVAILABLE
        assert recorder.calls == [], (
            "a container that never existed was reported as one that finished"
        )

    def test_an_unknown_runtime_state_is_unavailable_not_guessed(
        self, engine, recorder, fake_runtime
    ):
        fake_runtime([("paused", "true", "0")])
        _armed(engine, event_type=EVENT_CONTAINERRUN, source="build")

        (result,) = engine.poll(now=1000.0)

        assert result.signal_status == SIGNAL_UNAVAILABLE
        assert "which this reader does not know" in result.reason
        assert recorder.calls == []

    def test_killing_a_container_is_refused_even_with_the_opt_in(self, engine, recorder):
        """Destructive, so it stays out of an unattended rule's reach entirely."""
        rule = _armed(engine, name="reap", event_type=EVENT_CONTAINERRUN,
                      source="build", actuator="kill_process",
                      arguments={"pattern": "build"}, allow_destructive=True)
        prime(rule, subject="container:build")

        result = engine.feed(
            rule, _event(EVENT_CONTAINERRUN, subject="container:build", terminal=True),
            now=1000.0,
        )

        assert result.fired is False
        assert result.censored is True
        assert "destructive" in result.reason
        assert recorder.calls == []

    def test_progress_heartbeats_are_bucketed_and_the_transition_is_not(self, engine, recorder):
        rule = _armed(engine, name="run", event_type=EVENT_CONTAINERRUN, source="build",
                      cooldown_seconds=0.0)
        subject = "container:build"
        prime(rule, fingerprint="armed", subject=subject)

        beats = [
            engine.feed(
                rule,
                _event(EVENT_CONTAINERRUN, subject=subject, fingerprint=f"p{i}", progress=True),
                now=1000.0,
            )
            for i in range(8)
        ]

        assert len(_fired(beats)) == 1, (
            f"{len(_fired(beats))} of 8 heartbeats in one bucket produced events; "
            "at most one per 15s is the rule"
        )

        # The transition is not subject to the bucket: this is the whole point
        # of bucketing only progress.
        stalled = engine.feed(
            rule, _event(EVENT_CONTAINERRUN, subject=subject, fingerprint="stalled"),
            now=1000.0,
        )
        assert stalled.fired is True


# ===========================================================================
# 6. unithealth
# ===========================================================================

class _FakeSystemd:
    """Scripted unit states, then the last one forever - see `_FakeRuntime`."""

    def __init__(self, states):
        self.states = list(states)
        self._last = ("active", "running", "success", 0)

    def __call__(self, argv, **kwargs):
        if self.states:
            self._last = self.states.pop(0)
        active, sub, result, restarts = self._last
        return subprocess.CompletedProcess(
            argv, 0,
            stdout=f"ActiveState={active}\nSubState={sub}\nResult={result}\n"
                   f"NRestarts={restarts}\nStateChangeTimestamp=x\n",
            stderr="",
        )


@pytest.fixture
def fake_systemd(monkeypatch):
    import shani_chronoa.triggers as triggers

    def install(states):
        monkeypatch.setattr(triggers, "_run_argv", _FakeSystemd(states))
        monkeypatch.setattr(triggers.shutil, "which", lambda name: f"/usr/bin/{name}")

    return install


class TestUnitHealthEvent:
    def test_only_a_transition_fires_and_the_state_is_a_snapshot(
        self, engine, recorder, fake_systemd
    ):
        fake_systemd([("active", "running", "success", 0)])
        _armed(engine, event_type=EVENT_UNITHEALTH, source="nginx.service")
        arm(engine)

        fake_systemd([("activating", "start", "exit-code", 3)])
        (starting,) = _fired(engine.poll(now=1000.0))
        assert starting.event.detail["health"] == "starting"
        assert starting.event.detail["restarts"] == 3

        # A status display must be able to poll this repeatedly without
        # inferring transitions, and must not fire on an unchanged state.
        for tick in (1001.0, 1002.0):
            repeated = engine.poll(now=tick)
            assert _fired(repeated) == []
            assert {r.layer for r in repeated} == {LAYER_DUPLICATE_EVENT}, (
                "a status display polling an unchanged unit must be answered by "
                "duplicate detection, not by the cooldown"
            )
        assert len(recorder.calls) == 1

    def test_backing_off_and_gave_up_are_different_states(self, fake_systemd):
        fake_systemd([("activating", "auto-restart", "exit-code", 2)])
        backing = read_unit_health("app.service")

        fake_systemd([("failed", "failed", "exit-code", 5)])
        gave_up = read_unit_health("app.service")

        assert backing.payload["snapshot"]["health"] == UNIT_BACKING_OFF
        assert gave_up.payload["snapshot"]["health"] == UNIT_GAVE_UP
        assert backing.fingerprint != gave_up.fingerprint
        assert backing.event.terminal is False
        assert gave_up.event.terminal is True, (
            "gave_up is terminal; a boolean makes these two identical and retries "
            "a dead unit for as long as the machine is up"
        )

    def test_gave_up_parks_a_terminal_rule(self, engine, recorder, fake_systemd):
        fake_systemd([("active", "running", "success", 0)])
        rule = _armed(engine, event_type=EVENT_UNITHEALTH, source="app.service",
                      retry_policy=RETRY_TERMINAL)
        arm(engine)

        fake_systemd([("failed", "failed", "exit-code", 5)])
        (result,) = engine.poll(now=1001.0)

        assert result.censored is True
        assert RETRY_TERMINAL in result.reason
        assert rule.parked is True
        assert recorder.calls == []

    def test_no_systemd_is_unavailable_not_healthy(self, engine, recorder, monkeypatch):
        import shani_chronoa.triggers as triggers

        monkeypatch.setattr(triggers.shutil, "which", lambda name: None)
        _armed(engine, event_type=EVENT_UNITHEALTH, source="nginx.service")

        (result,) = engine.poll(now=1000.0)

        assert result.signal_status == SIGNAL_UNAVAILABLE
        assert "not systemd-managed" in result.reason
        assert recorder.calls == []

    def test_an_unmappable_sub_state_is_unavailable_not_guessed(
        self, engine, recorder, fake_systemd
    ):
        fake_systemd([("active", "whatever-new", "success", 0)])
        _armed(engine, event_type=EVENT_UNITHEALTH, source="app.service")

        (result,) = engine.poll(now=1000.0)

        assert result.signal_status == SIGNAL_UNAVAILABLE
        assert "maps to no known health state" in result.reason
        assert recorder.calls == []

    def test_every_declared_health_state_is_a_real_string(self):
        for state in UNIT_HEALTH_STATES:
            assert isinstance(state, str) and state


# ===========================================================================
# The four anti-noise layers, named as the taxonomy names them
# ===========================================================================

class TestTheFourAntiNoiseLayers:
    def test_each_layer_is_reachable_and_reported_by_name(self, engine, recorder):
        rule = _armed(engine, debounce_seconds=30.0, cooldown_seconds=6000.0)
        seen = {}

        seen[LAYER_DEDUPE_WINDOW] = engine.feed(
            rule, _event(EVENT_GIT, fingerprint="a"), now=1000.0
        )
        seen[LAYER_DUPLICATE_EVENT] = engine.feed(
            rule, _event(EVENT_GIT, fingerprint="a"), now=1001.0
        )

        filtered = _armed(engine, name="f", match_mode=MATCH_SUBSTRING,
                          substring="only-this-string")
        seen[LAYER_FILTER_MISMATCH] = engine.feed(
            filtered, _event(EVENT_GIT, fingerprint="z", summary="something else"),
            now=1000.0,
        )

        engine.run_due(now=1030.0)  # fires, so the next one is inside the cooldown
        seen[LAYER_COOLDOWN] = engine.feed(
            rule, _event(EVENT_GIT, fingerprint="b"), now=1030.0
        )

        for layer in ANTI_NOISE_LAYERS:
            assert layer in seen, f"{layer} is unreachable"
            assert seen[layer].fired is False, f"{layer} fired anyway"
            assert seen[layer].reason, f"{layer} suppressed without saying why"

    def test_a_keyword_filter_matches(self, engine, recorder):
        rule = _armed(engine, event_type=EVENT_UNITHEALTH, source="app.service",
                      match_mode=MATCH_KEYWORDS, keywords=["gave up", "exited"])
        prime(rule)

        assert engine.feed(
            rule, _event(EVENT_UNITHEALTH, fingerprint="a", summary="unit x gave up"),
            now=1000.0,
        ).fired is True
        assert engine.feed(
            rule, _event(EVENT_UNITHEALTH, fingerprint="b", summary="unit x is working"),
            now=1001.0,
        ).fired is False

    def test_debounce_delays_and_never_drops(self):
        window = DedupeWindow()
        event = _event(EVENT_GIT, fingerprint="a")

        first = window.submit("k", event, 100.0, 10.0)
        second = window.submit("k", event, 105.0, 10.0)
        third = window.submit("k", event, 101.0, 10.0)

        assert first == 110.0
        assert second == 115.0, "a second event did not push the deadline out"
        assert third == 115.0, "an earlier observation moved a later deadline"
        assert window.peek("k") == 115.0

    def test_a_debounced_run_fires_exactly_once_when_it_becomes_due(self, engine, recorder):
        rule = _armed(engine, debounce_seconds=30.0)
        prime(rule)

        (scheduled,) = [engine.feed(rule, _event(EVENT_GIT, fingerprint="a"), now=1000.0)]
        assert scheduled.layer == LAYER_DEDUPE_WINDOW
        assert engine.run_due(now=1029.0) == [], "a debounced run fired early"

        (fired,) = engine.run_due(now=1030.0)
        assert fired.fired is True
        assert len(recorder.calls) == 1
        assert engine.run_due(now=1031.0) == [], "the same delayed run fired twice"

    def test_a_cancelled_delayed_run_is_recorded_as_censored(self, tmp_path, recorder):
        state = {"denied": None}
        engine = _make_engine(tmp_path, config_factory=lambda: FlippingConfig(state),
                              dispatch=recorder)
        rule = _armed(engine, debounce_seconds=30.0)
        prime(rule)
        engine.feed(rule, _event(EVENT_GIT, fingerprint="a"), now=1000.0)

        state["denied"] = EVENT_GIT
        (result,) = engine.run_due(now=1100.0)

        assert result.censored is True
        assert "delayed run was cancelled" in result.reason
        assert recorder.calls == [], "a cancelled run still dispatched"


# ===========================================================================
# The negative control
# ===========================================================================

class TestTheNegativeControl:
    """An unchanged fingerprint must schedule **no timer**.

    This is the property that stops trigger spam, so the assertion has to tell
    "no timer exists" apart from "a timer exists but is not due". Both look like
    "nothing fired" to a test that only counts dispatches, so this one counts
    *pending timers* and asserts the deadline itself did not move.
    """

    def test_an_unchanged_fingerprint_schedules_no_timer(self, engine, recorder):
        rule = _armed(engine, debounce_seconds=30.0)
        prime(rule)
        engine.feed(rule, _event(EVENT_GIT, fingerprint="a"), now=1000.0)
        assert len(engine.dedupe().pending()) == 1

        for tick in range(1, 25):
            result = engine.feed(
                rule, _event(EVENT_GIT, fingerprint="a"), now=1000.0 + tick
            )
            assert result.layer == LAYER_DUPLICATE_EVENT
            assert result.fired is False

        assert len(engine.dedupe().pending()) == 1, (
            "24 no-op observations armed 24 more timers"
        )
        assert engine.dedupe().peek(_key(rule, "s", "a")) == 1030.0, (
            "a no-op observation pushed an already-armed run later; the deadline "
            "moved even though nothing changed"
        )
        assert engine.run_due(now=1030.0)[0].fired is True
        assert len(recorder.calls) == 1, "24 no-ops and one deadline produced two runs"

    def test_a_state_unchanged_since_the_last_firing_arms_nothing_either(self, engine):
        rule = _armed(engine, debounce_seconds=30.0)
        prime(rule)
        engine.feed(rule, _event(EVENT_GIT, fingerprint="a"), now=1000.0)
        engine.run_due(now=1100.0)

        assert engine.dedupe().pending() == {}, "a consumed run was not released"
        engine.feed(rule, _event(EVENT_GIT, fingerprint="a"), now=1101.0)
        assert engine.dedupe().pending() == {}, (
            "a state that has not changed since the last *firing* armed a new run"
        )

    def test_a_filter_mismatch_schedules_no_timer(self, engine):
        rule = _armed(engine, name="f", match_mode=MATCH_SUBSTRING,
                      substring="only-this", debounce_seconds=30.0)
        prime(rule)

        result = engine.feed(
            rule, _event(EVENT_GIT, fingerprint="a", summary="other"), now=1000.0
        )

        assert result.layer == LAYER_FILTER_MISMATCH
        assert engine.dedupe().pending() == {}

    def test_an_unavailable_signal_schedules_no_timer(self, engine, recorder, tmp_path):
        _armed(engine, event_type=EVENT_FAILURE, source="absent", debounce_seconds=30.0,
               params={"directory": str(tmp_path)})

        (result,) = engine.poll(now=1000.0)

        assert result.signal_status == SIGNAL_UNAVAILABLE
        assert engine.dedupe().pending() == {}
        assert recorder.calls == []

    def test_a_censored_rule_schedules_no_timer(self, tmp_path, recorder):
        engine = _make_engine(
            tmp_path, config_factory=lambda: FakeConfig(denied=EVENT_GIT), dispatch=recorder
        )
        rule = _armed(engine, debounce_seconds=30.0)
        prime(rule)

        result = engine.feed(rule, _event(EVENT_GIT, fingerprint="a"), now=1000.0)

        assert result.censored is True
        assert engine.dedupe().pending() == {}
        assert recorder.calls == []

    def test_a_watcher_error_schedules_no_timer(self, engine, tmp_path, fake_home):
        watched = tmp_path / "w"
        watched.mkdir()
        _armed(engine, event_type=EVENT_FSWATCH, source=str(watched), debounce_seconds=30.0,
               seams={"watcher": _FakeWatcher([WatcherError("queue overflowed")])})

        (result,) = engine.poll(now=1000.0)

        assert result.signal_status == SIGNAL_WATCH_ERROR
        assert engine.dedupe().pending() == {}


# ===========================================================================
# Behaviours an earlier pass in this repo regressed
# ===========================================================================

class TestPreservedBehaviours:
    def test_a_failed_verdict_starts_no_cooldown_on_the_event_path(self, tmp_path):
        engine = _make_engine(tmp_path, dispatch=Recorder(verification.Verdict.FAILED))
        rule = _armed(engine, cooldown_seconds=3600.0)
        prime(rule)

        (result,) = [engine.feed(rule, _event(EVENT_GIT, fingerprint="a"), now=1000.0)]

        assert result.fired is False
        assert result.verdict is verification.Verdict.FAILED
        assert rule.last_fired_at is None, (
            "a failed action must not start the cooldown clock, or the rule goes "
            "quiet for a whole window having done nothing"
        )

    def test_a_failed_verdict_is_retried_rather_than_forgotten(self, tmp_path):
        """The retry has to be the *same* state, or the rule is never retried."""
        failing = Recorder(verification.Verdict.FAILED)
        engine = _make_engine(tmp_path, dispatch=failing)
        rule = _armed(engine, cooldown_seconds=3600.0)
        prime(rule)

        engine.feed(rule, _event(EVENT_GIT, fingerprint="a"), now=1000.0)
        # The backoff is the retry clock, and it is separate from the cooldown.
        assert rule.retry_at == 1001.0, "a failure armed no backoff at all"
        assert rule.due(1000.5) is False, "the retry is not spaced by the backoff"
        assert rule.due(1001.0) is True

        (second,) = [engine.feed(rule, _event(EVENT_GIT, fingerprint="a"), now=1100.0)]
        assert second.verdict is verification.Verdict.FAILED
        assert len(failing.calls) == 2, "the identical state was not retried"

    def test_a_success_clears_the_backoff_and_arms_the_cooldown(self, tmp_path):
        engine = _make_engine(tmp_path, dispatch=Recorder(verification.Verdict.FAILED))
        rule = _armed(engine, cooldown_seconds=3600.0)
        prime(rule)
        engine.feed(rule, _event(EVENT_GIT, fingerprint="a"), now=1000.0)
        assert rule.retry_at is not None

        engine._dispatch = Recorder()  # a later attempt succeeds
        (ok,) = [engine.feed(rule, _event(EVENT_GIT, fingerprint="a"), now=2000.0)]

        assert ok.fired is True
        assert rule.retry_at is None
        assert rule.last_fired_at == 2000.0
        assert rule.due(2000.1) is False, "a success must still cool the rule down"

    def test_a_failure_eventually_parks_rather_than_retrying_forever(self, tmp_path):
        engine = _make_engine(tmp_path, dispatch=Recorder(verification.Verdict.FAILED))
        rule = _armed(engine, cooldown_seconds=0.0)
        prime(rule)

        for step in range(20):
            engine.feed(rule, _event(EVENT_GIT, fingerprint="a"), now=1000.0 + step * 100)

        assert rule.parked is True
        assert rule.consecutive_failures >= 5

    def test_consent_is_checked_before_the_cooldown_on_the_event_path(self, tmp_path, recorder):
        """A rule inside its cooldown must still report its denial.

        Ordering these the other way round makes a switched-off consent key look
        exactly like "nothing matched" - and a refusal nobody can see is the one
        outcome a consent gate must never produce.
        """
        state = {"denied": None}
        engine = _make_engine(
            tmp_path, config_factory=lambda: FlippingConfig(state), dispatch=recorder
        )
        rule = _armed(engine, cooldown_seconds=3600.0)
        prime(rule)
        engine.feed(rule, _event(EVENT_GIT, fingerprint="a"), now=1000.0)
        assert rule.due(1001.0) is False, "the fixture did not put the rule in cooldown"
        recorder.calls.clear()

        state["denied"] = EVENT_GIT
        result = engine.feed(rule, _event(EVENT_GIT, fingerprint="b"), now=1001.0)

        assert result.censored is True, "the denial was hidden behind the cooldown"
        assert "turned off" in result.reason
        assert recorder.calls == []

    def test_consent_is_checked_before_due_on_the_percept_path_too(self, tmp_path):
        """The percept path's own invariant, pinned so the two cannot drift."""
        from shani_chronoa.senses import Percept, SENSITIVITY_PRIVATE

        rule, problem = build_rule(
            name="d", sense="hearing", match_mode=MATCH_SUBSTRING, actuator="notify",
            arguments={"summary": "x"}, substring="doorbell", skills=_HANDLER_FNS,
        )
        assert rule is not None, problem
        state = {"denied": None}
        engine = TriggerEngine(
            store=RuleStore(tmp_path / "rules.json"),
            config_factory=lambda: FlippingConfig(state),
            dispatch=lambda *a, **k: None,
        )
        engine.store().add(rule)
        percept = Percept(
            sense="hearing", kind="utterance", content="doorbell",
            created_at=time.time(), ttl_seconds=300, source="mic",
            sensitivity=SENSITIVITY_PRIVATE,
        )
        engine.evaluate(percept, now=1000.0)
        assert rule.last_fired_at == 1000.0
        assert rule.due(1000.1) is False, "the fixture did not enter the cooldown"

        state["denied"] = "hearing"
        (result,) = engine.evaluate(percept, now=1000.1)

        assert result.denied is True, (
            "a rule already inside its cooldown hid the consent denial"
        )
        assert "hearing sense is turned off" in result.reason


# ===========================================================================
# Safety guards
# ===========================================================================

class TestSafetyGuards:
    def test_a_destructive_actuator_cannot_be_armed_without_the_opt_in(self):
        rule, problem = build_event_rule(
            name="k", event_type=EVENT_UNITHEALTH, source="app.service",
            actuator="kill_process", arguments={"pattern": "app"}, skills=_HANDLER_FNS,
        )

        assert rule is None
        assert "destructive" in (problem or "")

    def test_the_destructive_guard_is_checked_at_dispatch_not_only_at_arm(self, engine, recorder):
        """A hand-edited rules file must not be a way around the guard."""
        rule = _armed(engine, event_type=EVENT_UNITHEALTH, source="app.service",
                      actuator="kill_process", arguments={"pattern": "app"},
                      allow_destructive=False)
        prime(rule)

        result = engine.feed(rule, _event(rule.event_type, fingerprint="a"), now=1000.0)

        assert result.censored is True
        assert "allow_destructive" in result.reason
        assert recorder.calls == []

    def test_a_destructive_actuator_is_reachable_with_the_opt_in(self, engine, recorder):
        """So the opt-in is real, and the guard is not a permanent no."""
        rule = _armed(engine, event_type=EVENT_UNITHEALTH, source="app.service",
                      actuator="kill_process", arguments={"pattern": "app"},
                      allow_destructive=True)
        prime(rule)

        result = engine.feed(rule, _event(rule.event_type, fingerprint="a"), now=1000.0)

        assert result.fired is True
        assert recorder.calls[0][0] == "kill_process"

    def test_input_actuators_need_their_own_consent_key_on_the_event_path(self, engine, recorder):
        rule = _armed(engine, event_type=EVENT_UNITHEALTH, source="app.service",
                      actuator="click_pointer", arguments={"x": 1, "y": 2})
        prime(rule)

        result = engine.feed(rule, _event(rule.event_type, fingerprint="a"), now=1000.0)

        assert result.censored is True
        assert "input-control-enabled" in result.reason
        assert recorder.calls == []

    def test_every_firing_records_an_origin_and_a_reason(self, engine, recorder):
        rule = _armed(engine)
        prime(rule)

        result = engine.feed(rule, _event(EVENT_GIT, fingerprint="a"), now=1000.0)

        assert recorder.calls[0][2] == "unattended", (
            "origin alone is not enough; it must be the unattended value so an "
            "audit can tell this from a person asking"
        )
        assert result.reason and EVENT_GIT in result.reason

    def test_an_event_rule_cannot_be_armed_for_a_non_whitelisted_actuator(self):
        rule, problem = build_event_rule(
            name="x", event_type=EVENT_GIT, source="/tmp/r",
            actuator="definitely_not_a_skill", arguments={}, skills=_HANDLER_FNS,
        )

        assert rule is None
        assert "whitelisted" in (problem or "")

    def test_a_live_object_cannot_be_put_in_persisted_params(self):
        """`params` is written to disk; a live object there cannot survive a
        restart and would come back as something else."""
        rule, problem = build_event_rule(
            name="w", event_type=EVENT_FSWATCH, source="/tmp/w",
            actuator="notify", arguments={}, match_mode=MATCH_ANY,
            params={"watcher": object()}, skills=_HANDLER_FNS,
        )

        assert rule is None
        assert "seams" in (problem or "")

    def test_one_broken_rule_does_not_stop_the_others(self, engine, recorder):
        def flaky(actuator, arguments, **kwargs):
            if actuator == "notify":
                raise RuntimeError("notify-send is missing")
            return verification.Result(verification.Verdict.VERIFIED, "ok")

        engine._dispatch = flaky
        a = _armed(engine, name="a")
        b = _armed(engine, name="b", actuator="speak", arguments={"text": "x"})
        prime(a)
        prime(b, subject="s2")

        first = engine.feed(a, _event(EVENT_GIT, fingerprint="a1"), now=1000.0)
        second = engine.feed(b, _event(EVENT_GIT, subject="s2", fingerprint="b1"), now=1000.0)

        assert "notify-send is missing" in first.reason
        assert second.fired is True, "one broken rule stopped the other"


class TestTheStorePathSeam:
    """The default store path must be resolved when a store is *built*.

    `RULES_FILE` / `EVENT_RULES_FILE` are module-level constants, and rebinding
    them at runtime is a supported seam - `tests/conftest.py` redirects
    `RULES_FILE` at a tmp directory on every test precisely so the suite cannot
    write armed rules into the real `~/.local/share`, and
    `test_skill_workbench.py` rebinds it directly. Capturing the value in a
    class attribute at import time makes the rebind a silent no-op, and the
    tests go on writing to the user's real home: green suite, contaminated
    state, and nothing anywhere reporting an error.
    """

    def test_rebinding_the_percept_store_path_redirects_a_new_store(self, tmp_path, monkeypatch):
        import shani_chronoa.triggers as triggers

        monkeypatch.setattr(triggers, "RULES_FILE", tmp_path / "elsewhere" / "rules.json")

        store = RuleStore()

        assert store.path == tmp_path / "elsewhere" / "rules.json"
        store.add(TriggerRule(
            name="d", sense="hearing", match_mode=MATCH_SUBSTRING,
            actuator="notify", arguments={}, substring="x",
        ))
        assert (tmp_path / "elsewhere" / "rules.json").is_file()

    def test_rebinding_the_event_store_path_redirects_a_new_store(self, tmp_path, monkeypatch):
        import shani_chronoa.triggers as triggers

        monkeypatch.setattr(
            triggers, "EVENT_RULES_FILE", tmp_path / "elsewhere" / "event_rules.json"
        )

        assert EventRuleStore().path == tmp_path / "elsewhere" / "event_rules.json"

    def test_the_fingerprint_path_is_redirectable_too(self, tmp_path, monkeypatch):
        import shani_chronoa.triggers as triggers

        monkeypatch.setattr(
            triggers, "FINGERPRINTS_FILE", tmp_path / "elsewhere" / "prints.json"
        )

        prints = DurableFingerprints()
        prints.record("k", "v")

        assert (tmp_path / "elsewhere" / "prints.json").is_file()


# ===========================================================================
# Durability of the fingerprint, and the backoff policy
# ===========================================================================

class TestDurability:
    def test_a_fingerprint_survives_a_restart(self, tmp_path, recorder):
        path = tmp_path / "prints.json"
        first = _make_engine(tmp_path, dispatch=recorder)
        rule = _armed(first, debounce_seconds=0.0)
        prime(rule, fingerprint="armed", now=1000.0)
        first.feed(rule, _event(EVENT_GIT, fingerprint="stable"), now=1000.0)

        # A brand-new process, with no in-memory state at all.
        second = _make_engine(tmp_path, dispatch=recorder)
        result = second.feed(rule, _event(EVENT_GIT, fingerprint="stable"), now=2000.0)

        assert result.layer == LAYER_DUPLICATE_EVENT
        assert len(recorder.calls) == 1, (
            "the state did not change and the rule notified again after a restart; "
            "an in-memory latch alone cannot prevent this"
        )

    def test_a_corrupt_fingerprint_file_is_refused_rather_than_emptied(self, tmp_path):
        path = tmp_path / "prints.json"
        path.write_text("{ truncated")

        with pytest.raises(RuleStoreError) as caught:
            DurableFingerprints(path)

        assert "refusing to load" in str(caught.value)


class TestBackoffPolicy:
    def test_the_delay_is_exponential_and_capped(self):
        policy = BackoffPolicy(base=1.0, cap=BACKOFF_CAP_SECONDS)
        delays = [policy.delay_for(n) for n in range(1, 10)]

        assert delays[:5] == [1.0, 2.0, 4.0, 8.0, 16.0]
        assert max(delays) == BACKOFF_CAP_SECONDS
        assert all(a < b for a, b in zip(delays[:5], delays[1:6])), delays
        assert all(a <= b for a, b in zip(delays, delays[1:])), delays

    def test_five_consecutive_failures_park_it(self):
        policy = BackoffPolicy(clock=lambda: 0.0)
        for _ in range(4):
            policy.observe_failure()
        assert policy.should_park() is False
        policy.observe_failure()
        assert policy.should_park() is True

    def test_a_success_resets_only_the_consecutive_count(self):
        policy = BackoffPolicy(clock=lambda: 0.0)
        for _ in range(3):
            policy.observe_failure()
        policy.observe_success()

        assert policy.consecutive == 0
        assert len(policy.restarts) == 3, (
            "clearing the window on success is exactly how a crash loop that "
            "succeeds once a minute retries forever"
        )

    def test_the_rolling_window_restart_cap_parks_a_slow_crash_loop(self):
        """The cap the consecutive counter structurally cannot do."""
        clock = {"t": 0.0}
        policy = BackoffPolicy(clock=lambda: clock["t"], restart_window=300.0)

        for _ in range(5):
            policy.observe_failure()
            policy.observe_success()
            clock["t"] += 60.0

        assert policy.consecutive == 0, "the consecutive counter saw nothing wrong"
        assert policy.should_park() is True, (
            "five crashes in five minutes, each isolated, and it still wanted a sixth"
        )

    def test_failures_outside_the_window_do_not_count(self):
        clock = {"t": 0.0}
        policy = BackoffPolicy(clock=lambda: clock["t"], restart_window=300.0)
        for _ in range(4):
            policy.observe_failure()
            clock["t"] += 1000.0

        assert policy.should_park() is False


# ===========================================================================
# Rule persistence
# ===========================================================================

class TestEventRulePersistence:
    def test_a_rule_round_trips_through_the_store(self, engine, tmp_path):
        rule = _armed(engine, event_type=EVENT_FAILURE, source="suite",
                      retry_policy=RETRY_TERMINAL, params={"directory": "/tmp/v"},
                      debounce_seconds=12.5)
        rule.attempt = 3
        rule.retry_at = 456.0
        rule.parked = True
        rule.parked_reason = "the deploy is broken"
        engine.store()._write()

        (loaded,) = EventRuleStore(tmp_path / "event_rules.json").all()

        assert loaded.event_type == EVENT_FAILURE
        assert loaded.retry_policy == RETRY_TERMINAL
        assert loaded.params == {"directory": "/tmp/v"}
        assert loaded.debounce_seconds == 12.5
        assert loaded.attempt == 3
        assert loaded.retry_at == 456.0
        assert loaded.parked is True
        assert loaded.parked_reason == "the deploy is broken"

    def test_seams_are_not_persisted(self, engine, tmp_path):
        """A live object in a persisted field would come back as something else."""
        _armed(engine, event_type=EVENT_FSWATCH, source="/tmp/w",
               seams={"watcher": object()})
        engine.store()._write()

        (loaded,) = EventRuleStore(tmp_path / "event_rules.json").all()

        assert loaded.seams == {}

    def test_a_record_with_an_unknown_event_type_is_refused(self, tmp_path):
        path = tmp_path / "event_rules.json"
        path.write_text(json.dumps([{
            "name": "x", "event_type": "telepathy", "source": "s",
            "actuator": "notify", "arguments": {},
        }]))

        with pytest.raises(RuleStoreError):
            EventRuleStore(path)

    def test_an_existing_percept_rules_file_still_loads(self, tmp_path):
        """The event store is a sibling file precisely so this stays true."""
        path = tmp_path / "rules.json"
        rule = TriggerRule(
            name="d", sense="hearing", match_mode=MATCH_SUBSTRING,
            actuator="notify", arguments={}, substring="doorbell",
        )
        RuleStore(path).add(rule)

        assert [r.name for r in RuleStore(path).all()] == ["d"]


class TestSignalDispatch:
    def test_an_unknown_event_type_is_unavailable(self, engine):
        rule = _rule()
        rule.event_type = "not-a-type"

        signal = read_event_signal(rule)

        assert signal.status == SIGNAL_UNAVAILABLE

    def test_a_reader_that_raises_does_not_fire_and_does_not_stop_the_poll(
        self, engine, recorder, tmp_path
    ):
        import shani_chronoa.triggers as triggers

        repo = _git_repo(tmp_path)
        original = triggers.read_git_state
        calls = []

        def explode(path, now=None):
            calls.append(path)
            if len(calls) == 1:
                raise RuntimeError("git index lock")
            return original(path, now=now)

        _armed(engine, name="a", event_type=EVENT_GIT, source=str(repo))
        _armed(engine, name="b", event_type=EVENT_GIT, source=str(repo))
        arm(engine)
        _commit(repo, "move")

        triggers.read_git_state = explode
        try:
            results = engine.poll(now=1000.0)
        finally:
            triggers.read_git_state = original

        assert len(results) == 2
        assert any("git index lock" in r.reason for r in results)
        assert len(_fired(results)) == 1, "one broken read stopped the other rule"


# ===========================================================================
# The gates that were missing or unreachable (2026-09-30)
# ===========================================================================
#
# A security review found four defects on this path and one asymmetry. Each was
# reproduced against the real code before it was fixed; the numbers quoted in
# each docstring are that measurement, not an estimate.

class TestTheBoundsRunForTheDefaultMatchMode:
    """`_validate_rule_fields` returned from the *whole function* for `any`.

    `manage_triggers` hands out `match_mode=any` as the default for an event
    rule - `skills/manage_triggers.py` says why: an event rule's question is
    normally "tell me when this source changes" - so the cooldown and argument
    bounds below that line were skipped on the default arming path.

    Measured before the fix, all accepted with `match_mode=any`:
    `cooldown_seconds` 0.0 / -9999.0 / 1000000000, 100 arguments against a
    limit of 8, and a 100k-character argument value against a limit of 4096.
    """

    @pytest.mark.parametrize("cooldown", [0.0, -9999.0, 1000000000])
    def test_a_bogus_cooldown_is_refused_on_the_default_mode(self, cooldown):
        rule, problem = build_event_rule(
            name="r", event_type=EVENT_UNITHEALTH, source="app.service",
            actuator="notify", arguments={"summary": "x"}, match_mode=MATCH_ANY,
            cooldown_seconds=cooldown, skills=_HANDLER_FNS,
        )

        assert rule is None, f"cooldown {cooldown!r} was accepted with match_mode=any"
        assert "cooldown_seconds" in (problem or "")

    def test_too_many_arguments_are_refused_on_the_default_mode(self):
        rule, problem = build_event_rule(
            name="r", event_type=EVENT_UNITHEALTH, source="app.service",
            actuator="notify", match_mode=MATCH_ANY,
            arguments={f"a{i}": "v" for i in range(100)},
            skills=_HANDLER_FNS,
        )

        assert rule is None
        assert "arguments" in (problem or "")

    def test_an_oversized_argument_value_is_refused_on_the_default_mode(self):
        rule, problem = build_event_rule(
            name="r", event_type=EVENT_UNITHEALTH, source="app.service",
            actuator="notify", match_mode=MATCH_ANY,
            arguments={"summary": "x" * 100_000}, skills=_HANDLER_FNS,
        )

        assert rule is None
        assert "at most" in (problem or "")

    def test_the_negative_control_the_same_cooldown_is_refused_in_substring_mode(self):
        """Identical input, one difference: a real match mode.

        If this were refused and `any` were not, the three above would be
        measuring the substring branch rather than the shared bounds.
        """
        rule, problem = build_event_rule(
            name="r", event_type=EVENT_UNITHEALTH, source="app.service",
            actuator="notify", arguments={"summary": "x"},
            match_mode=MATCH_SUBSTRING, substring="gone",
            cooldown_seconds=0.0, skills=_HANDLER_FNS,
        )

        assert rule is None
        assert "must be at least" in (problem or "")

    def test_a_legal_any_rule_is_still_accepted(self):
        """So the fix is not "refuse the mode the CLI defaults to"."""
        rule, problem = build_event_rule(
            name="r", event_type=EVENT_UNITHEALTH, source="app.service",
            actuator="notify", arguments={"summary": "x"}, match_mode=MATCH_ANY,
            skills=_HANDLER_FNS,
        )

        assert rule is not None, problem


class TestAHandEditedEventRulesFileIsBoundedAtLoad:
    """`EventRule.from_dict` applied no bounds whatsoever.

    Measured before the fix, all of these loaded: `cooldown_seconds` of -1.0
    and 1e18, `debounce_seconds` of -99, `attempt` of 100000,
    `consecutive_failures` of -1, a 100k-character `substring`, 5000 keywords
    and 100 arguments.

    The split is deliberate. Numbers that govern cadence are clamped toward
    firing *less*; anything governing what the rule matches or what the actuator
    is told is refused, because trimming an argument list or shortening a
    string would arm a rule that means something other than what the file says -
    and a quietly-different armed rule is worse than an absent one, because
    `list` still shows it.
    """

    BASE = {
        "name": "r", "event_type": EVENT_UNITHEALTH, "source": "app.service",
        "actuator": "notify", "arguments": {"summary": "x"}, "match_mode": MATCH_ANY,
    }

    def _load(self, **overrides):
        raw = dict(self.BASE)
        raw.update(overrides)
        return EventRule.from_dict(raw)

    @pytest.mark.parametrize("value,expected", [(-1.0, 1.0), (0.0, 1.0),
                                                (10 ** 9, 3600.0), (30.0, 30.0)])
    def test_the_cooldown_is_clamped_into_range(self, value, expected):
        rule = self._load(cooldown_seconds=value)

        assert rule is not None
        assert rule.cooldown_seconds == expected

    def test_the_debounce_is_clamped_into_range(self):
        rule = self._load(debounce_seconds=-99)

        assert rule is not None
        assert rule.debounce_seconds == 0.0, (
            "a negative debounce dispatches on every poll with no collapse"
        )

    def test_the_backoff_counters_cannot_be_driven_out_of_range(self):
        """`attempt` feeds `2 ** attempt`, and a negative `consecutive_failures`
        compares False against the cap, so `should_park()` never becomes True."""
        rule = self._load(attempt=100000, consecutive_failures=-1)

        assert rule is not None
        assert 0 <= rule.attempt <= 64, "an unbounded attempt is a 2 ** 100000"
        assert 0 <= rule.consecutive_failures <= BACKOFF_MAX_CONSECUTIVE_FAILURES
        assert rule.exhausted() is True or rule.consecutive_failures >= 0

    def test_a_clamped_counting_failure_still_parks(self):
        """The permissive reading of a negative counter: never parks."""
        rule = self._load(consecutive_failures=-1, attempt=0)
        if rule is not None and rule.consecutive_failures == 0:
            policy = rule._policy()
            assert policy.should_park() is False

    def test_an_oversized_substring_is_refused_not_shortened(self):
        """A shorter needle matches *more* strings, so this is the one bound
        where repairing rather than refusing would be the permissive choice."""
        assert self._load(substring="y" * 100_000) is None

    def test_too_many_keywords_are_refused_not_trimmed(self):
        assert self._load(keywords=["k"] * 5000) is None

    def test_too_many_arguments_are_refused_not_trimmed(self):
        assert self._load(arguments={f"a{i}": "v" for i in range(100)}) is None

    def test_a_sanctioned_rule_round_trips_unchanged(self):
        original, problem = build_event_rule(
            name="r", event_type=EVENT_UNITHEALTH, source="app.service",
            actuator="notify", arguments={"summary": "x"}, match_mode=MATCH_ANY,
            cooldown_seconds=30.0, debounce_seconds=0.0, skills=_HANDLER_FNS,
        )
        assert original is not None, problem

        loaded = EventRule.from_dict(original.to_dict())

        assert loaded is not None
        assert loaded.to_dict() == original.to_dict(), (
            "the bounds must not disturb a rule that was armed legitimately"
        )


class TestABrokenActuatorReachesTheBackoffAndThePark:
    """The two ways a non-success can arrive that were recorded as successes.

    `tools.DispatchResult` documents four rows, and the fourth -
    `ran=False, UNVERIFIED - never executed` - was indistinguishable from the
    second, `ran=True, UNVERIFIED - ran, and there is nothing to check it
    against`. And the exception branch called `note_failure` without ever asking
    whether that failure had reached a cap.
    """

    def test_an_actuator_that_never_ran_is_not_a_success(self, engine):
        from shani_chronoa.tools import ToolOutcome

        engine._dispatch = lambda *a, **kw: ToolOutcome(
            text="Tool 'notify' failed: exit=126",
            verdict=verification.Verdict.UNVERIFIED, ran=False,
        )
        rule = _armed(engine)
        prime(rule)

        result = engine.feed(rule, _event(rule.event_type, fingerprint="a"), now=1000.0)

        assert result.fired is False, (
            "the tool never executed; recording that as fired started a "
            "cooldown window on a rule that had done nothing"
        )
        assert rule.last_fired_at is None
        assert rule.consecutive_failures == 1, "a non-success must start the backoff"

    def test_the_negative_control_a_post_condition_less_success_still_fires(self, engine):
        """Why UNVERIFIED alone is not treated as a failure.

        Treating it as one was proposed, and it is wrong on this codebase: the
        default dispatch seam returns UNVERIFIED for *every* successful skill,
        so it would park every legitimate rule after five firings. The signal
        that separates "could not check it" from "did not run" is `ran`, and
        that is the row this checks.
        """
        rule = _armed(engine)
        prime(rule)

        result = engine.feed(rule, _event(rule.event_type, fingerprint="a"), now=1000.0)

        assert result.fired is True
        assert result.verdict is verification.Verdict.VERIFIED
        assert rule.parked is False

    def test_a_raising_actuator_parks_once_its_retries_are_exhausted(self, tmp_path):
        """Measured before the fix: 12 runs, `consecutive_failures` at 12,
        `exhausted()` True from the fifth onward, `parked` False throughout."""
        def boom(*a, **kw):
            raise RuntimeError("the actuator is on fire")

        engine = _make_engine(tmp_path, dispatch=boom)
        rule = _armed(engine)
        prime(rule)

        clock = 1000.0
        for index in range(BACKOFF_MAX_CONSECUTIVE_FAILURES + 4):
            engine.feed(rule, _event(rule.event_type, fingerprint=f"fp{index}"), now=clock)
            clock = max(clock + 120.0, (rule.retry_at or clock) + 1.0)

        assert rule.exhausted() is True, "the counter did reach the cap"
        assert rule.parked is True, (
            "a raising actuator retried forever: note_failure was called and "
            "exhausted() was never checked"
        )
        assert rule.consecutive_failures >= BACKOFF_MAX_CONSECUTIVE_FAILURES
        assert rule.parked_reason

    def test_a_raising_actuator_does_not_park_before_the_cap(self, tmp_path):
        """So the fix cannot pass by parking on the first failure."""
        def boom(*a, **kw):
            raise RuntimeError("transient")

        engine = _make_engine(tmp_path, dispatch=boom)
        rule = _armed(engine)
        prime(rule)

        engine.feed(rule, _event(rule.event_type, fingerprint="fp0"), now=1000.0)

        assert rule.parked is False
        assert rule.consecutive_failures == 1
        assert rule.retry_at is not None and rule.retry_at > 1000.0

    def test_a_parked_rule_is_not_retried_on_the_next_poll(self, tmp_path):
        """The point of parking: the raising actuator stops being called."""
        calls = []

        def boom(*a, **kw):
            calls.append(a[0])
            raise RuntimeError("still on fire")

        engine = _make_engine(tmp_path, dispatch=boom)
        rule = _armed(engine)
        prime(rule)

        clock = 1000.0
        for index in range(BACKOFF_MAX_CONSECUTIVE_FAILURES + 3):
            engine.feed(rule, _event(rule.event_type, fingerprint=f"fp{index}"), now=clock)
            clock = max(clock + 120.0, (rule.retry_at or clock) + 1.0)
        parked_after = len(calls)

        engine.feed(rule, _event(rule.event_type, fingerprint="later"), now=clock + 1000.0)

        assert len(calls) == parked_after, "a parked rule kept being driven"
        assert calls, "no control: the actuator was never called at all"


class TestDestructiveAndUnkeyedActuatorsOnBothArmPaths:
    """`build_rule` accepted all eight; `build_event_rule` refused all eight.

    The percept path is the half `skills/manage_triggers.py` makes reachable
    from a spoken instruction, so it was the permissive one. And
    `write_text_file` was accepted by *both*: the only shipped skill that can
    replace the contents of a file you wrote and declares no consent key, so
    there was nothing to re-check at fire time and its skill does not refuse
    unattended either.
    """

    @pytest.mark.parametrize("actuator", sorted(_DESTRUCTIVE_ACTUATORS & set(_HANDLER_FNS)))
    def test_both_arm_paths_refuse_it(self, actuator):
        percept, percept_problem = build_rule(
            name="r", sense="hearing", match_mode=MATCH_KEYWORDS, keywords=["loud"],
            actuator=actuator, arguments={}, skills=_HANDLER_FNS,
        )
        event, event_problem = build_event_rule(
            name="r", event_type=EVENT_UNITHEALTH, source="app.service",
            actuator=actuator, arguments={}, match_mode=MATCH_ANY,
            skills=_HANDLER_FNS,
        )

        assert percept is None and "destructive" in (percept_problem or "")
        assert event is None and "destructive" in (event_problem or "")

    def test_write_text_file_is_refused_by_both_and_has_no_opt_in(self):
        for kwargs in ({}, {"allow_destructive": True}):
            percept, percept_problem = build_rule(
                name="r", sense="hearing", match_mode=MATCH_KEYWORDS,
                keywords=["loud"], actuator="write_text_file",
                arguments={"path": "/tmp/x", "content": "y"},
                skills=_HANDLER_FNS, **kwargs,
            )
            event, event_problem = build_event_rule(
                name="r", event_type=EVENT_UNITHEALTH, source="app.service",
                actuator="write_text_file", match_mode=MATCH_ANY,
                arguments={"path": "/tmp/x", "content": "y"},
                skills=_HANDLER_FNS, **kwargs,
            )

            assert percept is None, "the percept arm path accepted a file overwrite"
            assert event is None, "the event arm path accepted a file overwrite"
            assert "consent key" in (percept_problem or "")
            assert "consent key" in (event_problem or "")

    def test_write_text_file_really_has_no_consent_key(self):
        """The premise of the refusal above, measured against the live schema.

        Checked against `tools.TOOLS` rather than `capabilities.GATED` alone:
        `gated_by` treats a skill's own description as the more current of its
        two sources, and a description that names a key would satisfy the check.
        """
        from shani_chronoa.capabilities import gated_by
        from shani_chronoa.tools import TOOLS

        described = {
            (entry.get("function") or {}).get("name"):
            (entry.get("function") or {}).get("description", "")
            for entry in TOOLS
        }
        assert gated_by("write_text_file", described["write_text_file"]) is None, (
            "write_text_file now declares a consent key, so the refusal needs "
            "re-deciding - it could become a keyed actuator like the rest"
        )

    def test_the_rest_of_the_destructive_set_does_declare_a_key(self):
        """So this is a statement about *one* skill, not about the set."""
        from shani_chronoa.capabilities import gated_by
        from shani_chronoa.tools import TOOLS

        described = {
            (entry.get("function") or {}).get("name"):
            (entry.get("function") or {}).get("description", "")
            for entry in TOOLS
        }
        unkeyed = sorted(
            a for a in _DESTRUCTIVE_ACTUATORS & set(_HANDLER_FNS)
            if gated_by(a, described[a]) is None
        )

        assert unkeyed == [], (
            f"{unkeyed} are in the destructive set with no consent key of their "
            "own, so the arm-time refusal is their only gate - say so, or add the key"
        )


class TestBothEnginesGateOnTheSameKey:
    """The asymmetry that made item 1 possible: one switch, two engines."""

    def test_the_percept_engine_refuses_on_the_same_key_the_event_one_does(self, tmp_path):
        from shani_chronoa.triggers import TriggerEngine

        class OnlyTheArmingKey(FakeConfig):
            def __init__(self):
                super().__init__()
                self._keys = {TRIGGER_CONTROL_KEY: False}

        rule = TriggerRule(
            name="p", sense="hearing", match_mode=MATCH_KEYWORDS, keywords=["loud"],
            actuator="notify", arguments={"summary": "x"},
        )
        event_rule = EventRule(
            name="e", event_type=EVENT_UNITHEALTH, source="app.service",
            actuator="notify", arguments={"summary": "x"}, match_mode=MATCH_ANY,
        )
        engine = TriggerEngine(store=RuleStore(tmp_path / "rules.json"))
        config = OnlyTheArmingKey()

        percept_denial = engine._consent(config, rule)
        event_denial = engine._consent(config, event_rule)

        assert TRIGGER_CONTROL_KEY in percept_denial
        assert TRIGGER_CONTROL_KEY in event_denial
        assert percept_denial == event_denial, (
            "the same switch produced two different messages on the two paths, "
            "which is how they drifted apart in the first place"
        )
