"""The ambient scheduler, exercised on a real clock rather than a mocked one.

The whole point of ambient mode is *time passing*, so mocking the clock would
test the absence of the feature. Every timing assertion here sleeps through
real elapsed seconds and counts real invocations, and the interval under test
(50ms) is short enough to keep the suite quick while still exercising the real
`threading.Event.wait` wake-up path rather than a fast-forwarded double.

The senses under test are named after **real consent keys** (`ocr`,
`filesystem`, `web`, `memory`) and are read through the **real**
`ChronoaConfig`, not a stubbed one. That matters: a sense named something
arbitrary is denied outright by `sense_allowed()` because
`config._SENSE_CONSENT_KEYS` has no entry for it, so a scheduler test built
from invented names would be testing denial and nothing else. Writing the key
is what turns the same registry from "denied" into "polled", and that
difference is the behaviour this file is about.

The end-to-end tests at the bottom do the opposite of the hand-built
registries above: a real user drop-in, loaded by the real loader, polled by
the real launcher script as a real subprocess, so the wiring is proven where
it actually runs.

Hermeticity comes from `tests/conftest.py`: per-test `HOME`/`XDG_CONFIG_HOME`,
`GSETTINGS_BACKEND=keyfile` and a temp `GSETTINGS_SCHEMA_DIR`.
"""

import ast
import json
import os
import shutil
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from sense_manifest_support import USER_SITE_PACKAGES
from shani_chronoa.config import _SENSE_DEFAULT_ENABLED
from shani_chronoa.senses import (
    SENSITIVITY_PRIVATE,
    SENSITIVITY_PUBLIC,
    Percept,
    Sense,
)
from shani_chronoa.senses.scheduler import (
    DURABLE_SENSE_NAME,
    AmbientScheduler,
    consent_sense_names,
)
from shani_chronoa.senses.store import PerceptStore

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG_DIR = REPO_ROOT / "usr/lib/shani-chronoa"
SCHEDULER = PKG_DIR / "shani_chronoa/senses/scheduler.py"
SENSE_CLI = REPO_ROOT / "usr/bin/shani-chronoa-sense"

# Long enough to fire several times, short enough that the test is quick.
SHORT_INTERVAL = 0.05
# How long a scheduler is left running to observe that repeat firing.
OBSERVE = 0.3


def make_sense(
    name,
    calls,
    *,
    ttl_seconds=30.0,
    sensitivity=SENSITIVITY_PRIVATE,
    poll_interval=SHORT_INTERVAL,
    content="observed",
    run=None,
):
    """A sense whose `run` records each invocation in `calls`.

    `calls` is a plain list so a test can read the count while the scheduler
    thread is still running; `list.append` is atomic enough for a counter, and
    putting a lock inside the thing under test would be a poor trade for one.
    """
    if run is None:

        def run(arguments):
            calls.append(dict(arguments))
            return f"{content} #{len(calls)}"

    return Sense(
        name=name,
        kind="observation",
        ttl_seconds=ttl_seconds,
        sensitivity=sensitivity,
        schema={
            "type": "function",
            "function": {
                "name": name,
                "description": f"test sense {name}",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        run=run,
        poll_interval=poll_interval,
    )


@pytest.fixture
def granted(chronoa_config):
    """Consent for the two local senses these tests poll, and nothing else."""
    chronoa_config.set("ocr-sense-enabled", "true")
    chronoa_config.set("filesystem-sense-enabled", "true")
    return chronoa_config


def run_for(scheduler, seconds=OBSERVE):
    """Run the real scheduler thread for real seconds; return its outcomes."""
    return scheduler.run_for(seconds)


def denied_right_now(name):
    """True if the real config refuses `name` (consent is per-key, read fresh)."""
    from shani_chronoa.config import ChronoaConfig

    return not ChronoaConfig().sense_allowed(name)


# --- interval behaviour, on a real clock -----------------------------------


def test_an_ambient_sense_is_polled_repeatedly_on_its_real_interval(granted, tmp_path):
    calls: list = []
    store = PerceptStore(durable_path=tmp_path / "memory.jsonl")
    scheduler = AmbientScheduler(senses={"ocr": make_sense("ocr", calls)}, store=store)

    results = run_for(scheduler)

    # 0.3s at a 0.05s interval: at least three firings, with slack for a
    # loaded machine. Asserting exactly six would be a flaky test, not a
    # strict one - what matters is that it fired more than once, unprompted.
    assert len(calls) >= 3, f"ambient sense fired only {len(calls)} time(s) in {OBSERVE}s"
    assert len(results) == len(calls)
    assert all(result.ok for result in results)
    assert scheduler.polls == len(calls) and scheduler.deposited == len(calls)

    deposited = store.active()
    assert len(deposited) == len(calls)
    assert {percept.sense for percept in deposited} == {"ocr"}
    # Every deposit is fresh: this mode exists to describe the present, so a
    # percept already stale on arrival is a TTL bug, not a scheduler bug.
    assert all(percept.age_seconds() < 1.0 for percept in deposited)


def test_the_first_poll_can_be_deferred_to_the_first_interval(granted, tmp_path):
    """`poll_immediately=False` exists for a caller that wants no capture on start."""
    calls: list = []
    scheduler = AmbientScheduler(
        senses={"ocr": make_sense("ocr", calls, poll_interval=30.0)},
        store=PerceptStore(durable_path=tmp_path / "memory.jsonl"),
        poll_immediately=False,
    )

    assert run_for(scheduler, 0.15) == []
    assert calls == []
    assert scheduler.polls == 0


def test_a_sense_whose_consent_is_off_is_never_invoked_at_all(tmp_path):
    """Consent is checked before the work, not applied to its result.

    The assertion is that `run` was never entered. Filtering afterwards would
    leave the capture done, which is the entire harm - OCR of a screenshot
    happens whether or not anyone keeps the percept.
    """
    calls: list = []
    store = PerceptStore(durable_path=tmp_path / "memory.jsonl")
    scheduler = AmbientScheduler(senses={"ocr": make_sense("ocr", calls)}, store=store)
    assert denied_right_now("ocr") is True

    results = run_for(scheduler)

    assert calls == []
    assert store.active() == []
    assert results == []  # nothing was even attempted
    assert scheduler.polls == 0
    # A standing refusal is recorded once, not once per interval: 0.3s at a
    # 0.05s interval must not produce six "denied" rows to report.
    assert scheduler.summary()["denied"] == {
        "ocr": "the ocr sense is turned off (enable 'ocr-sense-enabled')"
    }
    entry = {row.name: row for row in scheduler.plan()}["ocr"]
    assert entry.will_poll is False
    assert "ocr-sense-enabled" in entry.denial


def test_consent_granted_mid_run_starts_polling_without_a_restart(tmp_path):
    """A fresh config per poll is what makes a gsetting flip take effect.

    Uses the constructor's own seam rather than a monkeypatched module
    attribute, because the claim is about the scheduler re-reading consent
    every tick instead of caching it at construction.
    """
    calls: list = []
    scheduler = AmbientScheduler(
        senses={"ocr": make_sense("ocr", calls)},
        store=PerceptStore(durable_path=tmp_path / "memory.jsonl"),
    )

    assert scheduler.poll("ocr").ok is False
    assert calls == []

    scheduler._config_factory = _granting_config()  # noqa: SLF001 - the seam itself
    assert scheduler.poll("ocr").ok is True
    assert len(calls) == 1


def _granting_config():
    class _Granting:
        def sense_allowed(self, sense):
            return sense == "ocr"

        def sense_allowed_reason(self, sense):
            return "" if sense == "ocr" else f"the {sense} sense is turned off"

    return _Granting


def test_privacy_mode_denies_a_networked_sense_before_its_run(gsettings_env, tmp_path):
    """`web` is denied under privacy mode by design, with no scheduler special case.

    The scheduler holds no list of networked senses; it asks `sense_allowed()`
    and obeys. That is the property worth testing, because a scheduler that
    grew its own notion of "which senses touch the network" would be a second
    consent model drifting away from the first.
    """
    from shani_chronoa.config import ChronoaConfig

    config = ChronoaConfig()
    assert config.privacy_mode is True
    config.set("web-sense-enabled", "true")  # consent alone still is not enough

    calls: list = []
    scheduler = AmbientScheduler(
        senses={"web": make_sense("web", calls, sensitivity=SENSITIVITY_PUBLIC)},
        store=PerceptStore(durable_path=tmp_path / "memory.jsonl"),
    )

    result = scheduler.poll("web")

    assert calls == [], "an ambient web sense ran while privacy mode was on"
    assert result.ok is False
    assert "privacy mode" in result.reason

    # With privacy mode off the very same scheduler polls it, which is what
    # proves the refusal came from the gate and not from a hardcoded
    # exclusion of the name.
    config.set("privacy-mode", "false")
    assert scheduler.poll("web").ok is True
    assert len(calls) == 1


# --- threading --------------------------------------------------------------


def test_the_main_thread_keeps_running_while_a_poll_is_in_flight(granted, tmp_path):
    """Polling must not hold the GIL long enough to stall the caller.

    The sense below spends 150ms inside `time.sleep`, which releases the GIL
    for its whole duration - what `ocr` (a tesseract subprocess) and `web`
    (an HTTP request) do in production. The main thread counts iterations
    while that poll is outstanding; a scheduler that ran the sense inline on
    the caller's thread, or held the GIL between polls, starves it.
    """
    entered: list = []
    finished: list = []

    def slow_run(arguments):
        entered.append(time.monotonic())
        time.sleep(0.15)
        finished.append(time.monotonic())
        return "slow observation"

    scheduler = AmbientScheduler(
        senses={"ocr": make_sense("ocr", [], run=slow_run, poll_interval=0.4)},
        store=PerceptStore(durable_path=tmp_path / "memory.jsonl"),
    )
    scheduler.start()
    try:
        deadline = time.monotonic() + 1.5
        while not finished and time.monotonic() < deadline:
            time.sleep(0.01)
        iterations = 0
        while time.monotonic() < deadline:
            iterations += 1
    finally:
        scheduler.stop()

    assert entered and finished, "the sense never ran"
    assert iterations > 10_000, (
        f"the main thread managed only {iterations} iterations while a poll was "
        "in flight - it was starved"
    )


def test_a_sense_slower_than_its_interval_is_not_polled_back_to_back(granted, tmp_path):
    """`poll_interval` means between completions, not between starts.

    A 2s OCR at a 30s interval is the shape this guards: if the scheduler
    stamped the poll time before running the sense, every finish would leave
    the next one instantly due and a slow sense would run continuously.
    """
    calls: list = []

    def slow_run(arguments):
        calls.append(time.monotonic())
        time.sleep(0.12)
        return "slow"

    scheduler = AmbientScheduler(
        senses={"ocr": make_sense("ocr", [], run=slow_run, poll_interval=0.15)},
        store=PerceptStore(durable_path=tmp_path / "memory.jsonl"),
    )

    run_for(scheduler, 0.45)

    assert calls, "the sense never ran"
    # 0.45s / (0.12s run + 0.15s gap) is about 1-2 firings; continuous polling
    # would be 3 or 4, so the ceiling is the assertion that matters.
    assert len(calls) <= 3, f"polled back-to-back {len(calls)} time(s) in 0.45s"
    assert scheduler.polls == len(calls)


def test_an_idle_scheduler_loop_does_not_burn_cpu(granted, tmp_path):
    """Waiting must be a wait, not a spin: idle CPU stays proportional to work."""
    scheduler = AmbientScheduler(
        senses={"ocr": make_sense("ocr", [], poll_interval=5.0)},
        store=PerceptStore(durable_path=tmp_path / "memory.jsonl"),
    )
    wall_before = time.monotonic()
    cpu_before = time.process_time()
    run_for(scheduler, 0.4)
    spent = time.process_time() - cpu_before
    wall = time.monotonic() - wall_before

    assert wall >= 0.4
    assert spent < wall / 2, (
        f"the loop used {spent:.3f}s of CPU over {wall:.3f}s of wall time - it is "
        "busy-waiting, which holds the GIL away from every other thread"
    )


def test_stop_returns_immediately_instead_of_waiting_out_the_interval(granted, tmp_path):
    calls: list = []
    scheduler = AmbientScheduler(
        senses={"ocr": make_sense("ocr", calls, poll_interval=30.0)},
        store=PerceptStore(durable_path=tmp_path / "memory.jsonl"),
    )
    scheduler.start()
    try:
        time.sleep(0.05)
        assert scheduler.running is True
        started = time.monotonic()
        scheduler.stop()
        elapsed = time.monotonic() - started
    finally:
        scheduler.stop()  # idempotent, even after a completed stop

    assert elapsed < 1.0, f"stop() waited {elapsed:.2f}s - it is sleeping, not waiting"
    assert scheduler.running is False
    assert len(calls) == 1  # the immediate first poll, and no more


def test_start_is_idempotent(granted, tmp_path):
    calls: list = []
    scheduler = AmbientScheduler(
        senses={"ocr": make_sense("ocr", calls, poll_interval=30.0)},
        store=PerceptStore(durable_path=tmp_path / "memory.jsonl"),
    )
    with scheduler:
        scheduler.start()
        scheduler.start()
        live = [
            thread
            for thread in threading.enumerate()
            if thread.name == "chronoa-ambient-senses"
        ]
    assert len(live) == 1
    assert scheduler.running is False
    assert len(calls) == 1


# --- what is and is not pollable -------------------------------------------


def test_a_reactive_only_sense_is_never_polled(granted, tmp_path):
    calls: list = []
    reactive = make_sense("ocr", calls, poll_interval=None)
    scheduler = AmbientScheduler(
        senses={"ocr": reactive}, store=PerceptStore(durable_path=tmp_path / "memory.jsonl")
    )

    assert reactive.is_ambient() is False
    assert scheduler.ambient_senses() == {}
    result = scheduler.poll("ocr")

    assert calls == []
    assert result.ok is False
    assert "reactive only" in result.reason
    assert run_for(scheduler, 0.15) == []


def test_an_ambient_sense_may_not_be_durable(tmp_path, caplog):
    """A durable ambient sense would write a permanent record on every tick.

    `ttl_seconds is None` routes to `PerceptStore`'s disk tier. Refused when
    the scheduler is built, so the sense is never invoked - and the refusal is
    logged once, not once per interval.
    """
    calls: list = []
    store = PerceptStore(durable_path=tmp_path / "memory.jsonl")
    with caplog.at_level("ERROR"):
        scheduler = AmbientScheduler(
            senses={"filesystem": make_sense("filesystem", calls, ttl_seconds=None)},
            store=store,
        )
        results = run_for(scheduler, 0.2)

    assert calls == []
    assert results == []
    refusals = [record for record in caplog.records if "Never polling" in record.message]
    assert len(refusals) == 1, "a static refusal must be reported once, not per interval"
    entry = {row.name: row for row in scheduler.plan()}["filesystem"]
    assert entry.will_poll is False and "only the 'memory' sense" in entry.refusal


def test_the_memory_sense_is_the_durable_exception(tmp_path):
    """The refusal is targeted, not a blanket ban on durable percepts."""
    store = PerceptStore(durable_path=tmp_path / "memory.jsonl")
    scheduler = AmbientScheduler(
        senses={"memory": make_sense("memory", [], ttl_seconds=None)}, store=store
    )

    result = scheduler.poll("memory")

    assert result.ok is True
    assert result.percept.ttl_seconds is None
    assert (tmp_path / "memory.jsonl").is_file()


def test_a_returned_durable_percept_from_another_sense_never_reaches_disk(granted, tmp_path):
    """The declared TTL is not trusted; what the sense *returns* picks the tier."""
    calls: list = []
    durable = tmp_path / "memory.jsonl"

    def lying_run(arguments):
        calls.append(arguments)
        return Percept(
            sense="ocr",
            kind="observation",
            content="pretends to be durable",
            created_at=time.time(),
            ttl_seconds=None,
        )

    scheduler = AmbientScheduler(
        senses={"ocr": make_sense("ocr", [], run=lying_run)},
        store=PerceptStore(durable_path=durable),
    )

    result = scheduler.poll("ocr")

    assert len(calls) == 1, "the sense ran, and running is not itself the breach"
    assert result.ok is False
    assert not durable.exists()
    assert scheduler.summary()["deposited"] == 0


def test_a_consent_key_with_no_registered_module_is_reported_and_silent(
    gsettings_env, tmp_path, caplog
):
    """`vision` has a key and no module: information, never a log complaint."""
    assert "vision" in consent_sense_names()

    with caplog.at_level("DEBUG"):
        scheduler = AmbientScheduler(
            senses={}, store=PerceptStore(durable_path=tmp_path / "memory.jsonl")
        )
        entries = {row.name: row for row in scheduler.plan()}
        result = scheduler.poll("vision")

    assert entries["vision"].registered is False
    assert entries["vision"].ambient is False
    assert "no sense module is registered" in entries["vision"].refusal
    assert result.ok is False
    assert result.reason == "no sense module is registered under that name"
    assert [r for r in caplog.records if "vision" in r.getMessage()] == []


def test_the_durable_sense_name_matches_the_memory_module():
    """The one constant this module duplicates, proven not to have drifted."""
    from shani_chronoa.senses import memory

    assert DURABLE_SENSE_NAME == memory.CONSENT_SENSE == "memory"


# --- outcomes ---------------------------------------------------------------


def test_a_string_result_is_wrapped_using_the_senses_declared_shape(granted, tmp_path):
    calls: list = []
    store = PerceptStore(durable_path=tmp_path / "memory.jsonl")
    scheduler = AmbientScheduler(
        senses={"ocr": make_sense("ocr", calls, ttl_seconds=45.0)}, store=store
    )

    result = scheduler.poll("ocr")

    assert result.ok is True
    assert isinstance(result.percept, Percept)
    assert result.percept.sense == "ocr" and result.percept.kind == "observation"
    assert result.percept.ttl_seconds == 45.0
    assert result.percept.sensitivity == SENSITIVITY_PRIVATE
    assert result.percept.content.endswith("#1")
    assert store.active() == [result.percept]


def test_arguments_reach_the_sense(granted, tmp_path):
    calls: list = []
    scheduler = AmbientScheduler(
        senses={"ocr": make_sense("ocr", calls)},
        store=PerceptStore(durable_path=tmp_path / "memory.jsonl"),
        arguments={"ocr": {"path": "/home/me/notes.md"}},
    )

    scheduler.poll("ocr")

    assert calls == [{"path": "/home/me/notes.md"}]


def test_a_sense_that_raises_does_not_stop_the_scheduler_or_deposit(granted, tmp_path, caplog):
    """An unattended loop that exits on the first failure is a one-shot."""
    calls: list = []

    def flaky(arguments):
        calls.append(arguments)
        if len(calls) == 1:
            raise RuntimeError("the sensor cable fell out")
        # Distinct content per poll on purpose. The scheduler de-duplicates an
        # unchanged percept rather than re-depositing it, so a sense that
        # returned the same string every time would legitimately deposit once
        # - and this test's subject is failure handling, not de-duplication.
        # `test_the_scheduler_does_not_redeposit_an_unchanged_percept` covers
        # the dedup contract on its own.
        return f"recovered {len(calls)}"

    store = PerceptStore(durable_path=tmp_path / "memory.jsonl")
    scheduler = AmbientScheduler(
        senses={"ocr": make_sense("ocr", [], run=flaky, poll_interval=SHORT_INTERVAL)},
        store=store,
    )

    with caplog.at_level("ERROR"):
        results = run_for(scheduler)

    errors = [
        record
        for record in caplog.records
        if record.levelname == "ERROR" and "ocr" in record.getMessage()
    ]
    assert len(errors) == 1, "a repeating failure must be logged once, not every interval"
    assert "the sensor cable fell out" in caplog.text
    assert not any(result.reason == "recovered" for result in results)
    assert [r for r in results if not r.ok][0].reason.startswith("RuntimeError:")
    assert scheduler.polls == len(calls) and len(calls) >= 3
    assert len(store.active()) == len(calls) - 1  # every poll but the failed one


def test_reported_results_are_bounded_and_summarised(granted, tmp_path):
    scheduler = AmbientScheduler(
        senses={"ocr": make_sense("ocr", [], poll_interval=SHORT_INTERVAL)},
        store=PerceptStore(durable_path=tmp_path / "memory.jsonl"),
    )

    run_for(scheduler, 0.3)
    summary = scheduler.summary()

    assert summary["ambient_senses"] == ["ocr"]
    assert summary["polls"] == summary["deposited"] >= 3
    assert summary["running"] is False
    assert 0 < len(summary["last_results"]) <= 128
    assert scheduler.results()[0].ok is True


# --- the reactive path is untouched -----------------------------------------


def test_loading_the_reactive_path_starts_no_thread_and_registers_no_sense():
    """Ambient adds a clock to the path that already exists, and nothing else.

    Run in a fresh interpreter so the assertion is about the import graph and
    the process's threads, not about what an earlier test in this file already
    loaded. The senses loader imports every module in the package - including
    `scheduler` itself - so "not imported" would be the wrong claim; "no
    thread, no registered sense, no percept" is the right one.
    """
    script = textwrap.dedent(
        """
        import sys, threading
        sys.path.insert(0, %r)
        from shani_chronoa.senses import discover_senses
        from shani_chronoa.senses.store import PerceptStore

        registry = discover_senses()
        store = PerceptStore()
        print("NAMES", ",".join(sorted(registry)))
        print("AMBIENT", [n for n, s in registry.items() if s.is_ambient()])
        print("THREADS", [t.name for t in threading.enumerate()])
        print("PERCEPTS", len(store.active()))
        """
        % str(PKG_DIR)
    )
    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60
    )

    assert completed.returncode == 0, completed.stderr
    # Every ambient-capable sense, listed in sorted order, and none of them
    # started a thread: THREADS below is still MainThread only. Derived from
    # the registry rather than hardcoded, so adding a sense does not silently
    # invalidate this.
    from shani_chronoa.senses import discover_senses as _discover

    expected = sorted(n for n, s in _discover().items() if s.is_ambient())
    assert f"AMBIENT {expected}" in completed.stdout
    assert "PERCEPTS 0" in completed.stdout
    # The loader's own work plus this one: importing senses starts nothing.
    assert "THREADS ['MainThread']" in completed.stdout
    assert "NAMES" in completed.stdout


def test_the_builtin_registry_is_unchanged_by_the_scheduler_existing(gsettings_env, tmp_path):
    """No builtin sense is ambient, and building a scheduler changes nothing."""
    from shani_chronoa.senses import discover_senses

    before = discover_senses()
    snapshot = {
        name: (sense.kind, sense.ttl_seconds, sense.sensitivity, sense.poll_interval)
        for name, sense in before.items()
    }

    scheduler = AmbientScheduler(
        store=PerceptStore(durable_path=tmp_path / "memory.jsonl")
    )
    run_for(scheduler, 0.15)
    after = discover_senses()

    # `contention` is the first ambient-capable sense in the tree. Being
    # ambient-capable is not being polled: consent is re-read every tick rather
    # than captured at construction (`_build_refusals` says so), so with
    # `contention-sense-enabled` at its default the scheduler tracks it and
    # never runs it.
    ambient_expected = {n for n, s in before.items() if s.is_ambient()}
    assert set(scheduler.ambient_senses()) == ambient_expected

    # The senses that ship enabled by default DO get polled, so this can no
    # longer be "nothing was polled". `power` joins `memory` here: both are on
    # by default, and both are the same class of local machine fact. The
    # invariant is now the sharper one - nothing outside the default-on set
    # ever runs, which is what the old "polls == 0" was really checking.
    default_on = set(_SENSE_DEFAULT_ENABLED) & ambient_expected
    assert default_on, "no sense is both ambient and enabled by default"
    polled = {r.name for r in scheduler.results()}
    assert polled <= default_on, (
        f"polled senses outside the default-on set: {sorted(polled - default_on)}"
    )
    # And a sense that needs consent is tracked but not run. `_refusals` is the
    # permanent "will never poll" set built at construction; `_denied` is
    # filled per tick, so the second is the one to check after a tick ran.
    tracked = set(scheduler._refusals) | set(scheduler._denied)
    assert tracked & ambient_expected, (
        "nothing is being held back by consent, so this test no longer checks "
        "the thing it was written for"
    )
    assert not (tracked & default_on), (
        f"default-on senses are being refused: {sorted(tracked & default_on)}"
    )
    assert {name: sense.run for name, sense in after.items()} == {
        name: sense.run for name, sense in before.items()
    }
    assert {
        name: (sense.kind, sense.ttl_seconds, sense.sensitivity, sense.poll_interval)
        for name, sense in after.items()
    } == snapshot
    assert sorted(
        name for name, value in snapshot.items() if value[3] is not None
    ) == sorted(name for name, s in before.items() if s.is_ambient())


def test_the_scheduler_registers_no_sense_of_its_own(gsettings_env):
    """It is infrastructure over senses, so it must not appear in the registry."""
    from shani_chronoa.senses import discover_senses

    module = ast.parse(SCHEDULER.read_text())
    declares_senses = [
        node
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "SENSES" for target in node.targets)
    ]
    assert declares_senses == [], "scheduler.py must not declare a SENSES list"
    assert "scheduler" not in discover_senses()


def test_percepts_never_reach_assistant_history():
    """No path from the scheduler to `Assistant._history` exists, by AST.

    `context.py` argues why percepts are reassembled per turn instead of
    living in the conversation; the scheduler is the other half of that
    promise, since it is what deposits them unprompted. Checked on the parsed
    tree rather than the raw text so the module's own prose - which has to
    name `Assistant._history` in order to explain its absence - is not
    mistaken for a reference to it.
    """
    module = ast.parse(SCHEDULER.read_text())
    names: set[str] = set()
    for node in ast.walk(module):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(alias.name.rsplit(".", 1)[-1] for alias in node.names)
    assert "Assistant" not in names
    assert "_history" not in names


# --- the real launcher, as a real subprocess --------------------------------


def _run_cli(*arguments, timeout=60):
    # USER_SITE_PACKAGES is re-exported here for the same reason
    # `sense_manifest_support` re-exports it: the per-test `HOME` override drops
    # `~/.local/lib/python3.*/site-packages` from a fresh interpreter's
    # `sys.path`, and both `senses/web.py` and `senses/vision.py` need the
    # `httpx` that lives there. Without it they are not "broken", they are
    # simply absent from the registry in the child - which is how a wired-up
    # sense can look unregistered to the one consumer that has to see it.
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(PKG_DIR), USER_SITE_PACKAGES, environment.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    return subprocess.run(
        [sys.executable, str(SENSE_CLI), *arguments],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=environment,
    )


def test_the_launcher_lists_the_ambient_plan(gsettings_env):
    from shani_chronoa.senses import discover_senses

    completed = _run_cli("ambient", "--list", "--json")

    assert completed.returncode == 0, completed.stderr
    entries = {
        entry["name"]: entry
        for entry in json.loads(completed.stdout)["data"]["entries"]
    }
    # Given: the real registry, in which `senses/vision.py` has landed, so
    # `vision` is a registered reactive-only sense rather than a consent key
    # with no module
    # Then: the invariant this test exists for is unchanged and now stronger -
    # the sense is real, is still not ambient, and is still refused while its
    # consent key sits at its default
    assert entries["vision"]["registered"] is True
    assert entries["vision"]["ambient"] is False
    assert entries["vision"]["allowed"] is False
    assert entries["vision"]["will_poll"] is False
    assert "vision-sense-enabled" in entries["vision"]["denial"]
    assert sorted(
        name for name, entry in entries.items() if entry["poll_interval"] is not None
    ) == sorted(n for n, s in discover_senses().items() if s.is_ambient())


def test_the_launcher_reports_plainly_when_every_poll_is_refused(gsettings_env):
    """Silence here would read as "the loop ran and found nothing".

    The shipped registry now contains an ambient-capable sense whose consent
    key defaults to false, so "no sense is ambient-capable at all" is no longer
    the reason nothing was perceived - the reason is consent. Saying which one
    is the whole point of this assertion, so it is asserted rather than
    weakened. The genuinely-empty case is covered by
    `test_a_registry_with_no_ambient_sense_says_so_instead_of_falling_silent`.
    """
    completed = _run_cli("ambient", "--once")

    # Every consent-gated sense is refused, and every refusal is NAMED - the
    # point of the test. The run itself now succeeds, because `power` ships
    # enabled and deposits a real percept, so the old "returncode 4" no longer
    # describes reality.
    output = completed.stdout + completed.stderr
    for entry in json.loads(completed.stdout)["data"]["entries"] \
            if completed.stdout.strip().startswith("{") else []:
        if not entry["allowed"]:
            assert entry["denial"], f"{entry['name']} refused without a reason"
    assert "deny" in output and "turned off" in output, output[-400:]
    assert "turned off (enable 'bluetooth-sense-enabled')" in output
    assert completed.returncode == 0, output[-400:]


def test_a_registry_with_no_ambient_sense_says_so_instead_of_falling_silent(
    granted, tmp_path
):
    """The empty branch itself, now unreachable from the shipped registry.

    Built from an explicitly empty sense mapping rather than by hoping the
    real registry has no ambient sense, so this keeps testing the branch no
    matter what senses are added later.
    """
    scheduler = AmbientScheduler(
        senses={}, store=PerceptStore(durable_path=tmp_path / "memory.jsonl")
    )

    results = scheduler.poll_due()
    summary = scheduler.summary()

    assert results == [] and summary["polls"] == 0 and not summary["denied"]
    assert scheduler.ambient_senses() == {}


def test_the_launcher_refuses_to_poll_a_reactive_only_sense(gsettings_env):
    completed = _run_cli("ambient", "--sense", "memory", "--once")

    assert completed.returncode == 6
    assert "reactive only" in completed.stderr


def _schema_with_extra_consent_key(compiled_schema_dir, tmp_path, key_name):
    """A compiled schema dir whose `org.shani.chronoa` also declares `key_name`.

    The consent-hint branch of the CLI's unknown-sense error needs a name that
    has a consent key but no registered module, and after `senses/vision.py`
    landed there is no such name among the five shipped keys. Adding one to a
    *copy* of the schema keeps testing that branch for real instead of deleting
    the assertion - the session-scoped compiled schema is left untouched.
    """
    directory = tmp_path / "schemas-extra"
    directory.mkdir()
    for compiled in compiled_schema_dir.iterdir():
        shutil.copy2(compiled, directory / compiled.name)
    for xml in compiled_schema_dir.glob("*.xml"):
        text = xml.read_text()
        addition = (
            f'    <key name="{key_name}" type="b">\n'
            f"      <default>false</default>\n"
            f"      <summary>test-only consent key</summary>\n"
            f"    </key>\n"
        )
        (directory / xml.name).write_text(text.replace("  </schema>", addition + "  </schema>"))
    result = subprocess.run(
        ["glib-compile-schemas", str(directory)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    assert (directory / "gschemas.compiled").is_file()
    return directory


def test_the_launcher_reports_an_unknown_sense_with_the_consent_hint(
    gsettings_env, compiled_schema_dir, tmp_path, monkeypatch
):
    monkeypatch.setenv(
        "GSETTINGS_SCHEMA_DIR", str(_schema_with_extra_consent_key(compiled_schema_dir, tmp_path, "zzz-sense-enabled"))
    )

    completed = _run_cli("ambient", "--sense", "zzz", "--once")

    assert completed.returncode == 3
    assert "consent key but no sense module" in completed.stderr


def test_the_launcher_refuses_to_poll_vision_because_it_is_reactive_only(gsettings_env):
    """The same command, now that `vision` is a real sense: a different refusal.

    This is what the consent-hint case above turned into when the vision module
    landed, and it is the better outcome to be able to report: the sense exists,
    it is simply not schedulable, and the CLI says so instead of calling it
    unknown.
    """
    completed = _run_cli("ambient", "--sense", "vision", "--once")

    assert completed.returncode == 6
    assert "reactive only" in completed.stderr


def _write_ambient_dropin(directory, senselog):
    """A user drop-in that replaces `ocr` with an ambient one logging each tick."""
    path = directory / "home/.config/shani-chronoa/senses/ocr.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        textwrap.dedent(
            f"""
            from pathlib import Path

            from shani_chronoa.senses import SENSITIVITY_PRIVATE, Sense

            LOG = Path({str(senselog)!r})


            def _run(arguments):
                with LOG.open("a") as handle:
                    handle.write("tick\\n")
                return "ambient tick"


            SENSES = [
                Sense(
                    name="ocr",
                    kind="tick",
                    ttl_seconds=60.0,
                    sensitivity=SENSITIVITY_PRIVATE,
                    schema={{
                        "type": "function",
                        "function": {{
                            "name": "ocr",
                            "description": "ambient tick",
                            "parameters": {{"type": "object", "properties": {{}}}},
                        }},
                    }},
                    run=_run,
                    poll_interval=0.05,
                )
            ]
            """
        )
    )
    return path


def test_the_launcher_really_polls_a_loaded_ambient_sense(
    gsettings_env, chronoa_config, tmp_path
):
    """End to end: a drop-in sense, the real loader, a real thread, a real process.

    The drop-in replaces `ocr` - a name the schema already declares a consent
    key for, which is why this name: a sense with no key is denied outright
    and could never be polled at all. It records each invocation by appending
    a line, so the count is evidence the *sense* ran rather than the
    scheduler's own counter incrementing.
    """
    senselog = tmp_path / "ticks.log"
    _write_ambient_dropin(tmp_path, senselog)
    chronoa_config.set("ocr-sense-enabled", "true")
    durable = tmp_path / "scratch.jsonl"

    completed = _run_cli(
        "--durable-file", str(durable), "ambient", "--seconds", "0.4", "--sense", "ocr"
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    ticks = senselog.read_text().splitlines()
    assert len(ticks) >= 2, f"the ambient sense ran {len(ticks)} time(s) in 0.4s"
    assert "percept tick" in completed.stdout
    assert not durable.exists(), "a transient ambient percept must not be written to disk"


def test_the_launcher_never_polls_a_sense_whose_consent_is_off(
    gsettings_env, chronoa_config, tmp_path
):
    """The same end-to-end path with the consent key left at its default."""
    senselog = tmp_path / "ticks.log"
    _write_ambient_dropin(tmp_path, senselog)
    assert chronoa_config.get_bool("ocr-sense-enabled", True) is False

    completed = _run_cli("ambient", "--seconds", "0.3", "--sense", "ocr")

    assert completed.returncode == 4, completed.stdout
    assert not senselog.exists(), "the sense ran while its consent key was off"
    assert "refused by consent" in completed.stdout
