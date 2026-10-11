"""A sense that never answers must not stop the ones that do.

`poll()` called `sense.run()` directly, guarded only by `except Exception`.
That catches a sense that *raises* and does nothing whatever about one that
*hangs*. Measured: a single sense whose `run` blocked on
`threading.Event().wait()` left `poll_due()` blocked forever, and the healthy
sense registered next to it was never polled - one hung sense stopped the whole
ambient layer for the life of the process, which is every sense in it.

The budget is the sense's own `poll_interval`, floored and capped, because the
scheduler already defines a sense slower than its own interval as one that is
too slow (`poll_due`'s comment on stamping `_last_poll` after the run). A
constant would silently decide which of a 2s audio sense and a 10-minute
snapshot check is allowed to be slow.

ECC's `observe.sh` solves the same problem with `signal.alarm(8)` -
"self-terminate before the async hook 10s timeout can orphan us". That does not
port: `signal.alarm` can only be armed on the **main** thread, and the
scheduler is not on it. The honest limit of this fix is that the sense's
**thread is abandoned, not killed**; what stops is everything downstream of it.

The budgets are patched to a quarter second rather than sleeping through the
real 30s floor - the same shape as `speech_gate.wait()` taking its clock, so the
tests prove the *timeout* instead of measuring how long a wait is.
"""

import threading

import pytest

from shani_chronoa.senses import Sense
from shani_chronoa.senses.scheduler import AmbientScheduler
from shani_chronoa.senses.store import PerceptStore
# --- a sense that never answers ---------------------------------------------
#
# `poll()` called `sense.run()` directly, guarded only by `except Exception`.
# That catches a sense that *raises* and does nothing whatever about one that
# *hangs*: measured, a single sense whose `run` blocked on
# `threading.Event().wait()` left `poll_due()` blocked forever, and the healthy
# sense registered next to it was never polled at all. One hung sense stopped
# the whole ambient layer for the life of the process.
#
# The budget is the sense's own `poll_interval`, floored and capped, so the tests
# patch those two constants rather than sleeping through a real 30s floor. That
# is the same shape as `speech_gate.wait()` taking its clock: it proves the
# *timeout* instead of measuring how long a wait is.


@pytest.fixture
def small_budgets(monkeypatch):
    """A sub-second budget, so a timeout test is fast and honest."""
    from shani_chronoa.senses import scheduler as S
    monkeypatch.setattr(S, "SENSE_BUDGET_FLOOR", 0.25)
    monkeypatch.setattr(S, "SENSE_BUDGET_CEILING", 0.25)
    return S


#: A sense that does not answer. Bounded rather than infinite **on purpose**:
#: an infinite wait proves the point but makes a *broken* build hang until the
#: mutation runner kills it, so the mutation it is meant to catch is reported as
#: a timeout instead of a failure. 45s is far past any budget here (0.25s in
#: these tests, 120s in production) and keeps a regressed run finite.
_HANG_SECONDS = 45.0


def _hang(arguments):
    threading.Event().wait(_HANG_SECONDS)
    return "never"


def _fast(arguments):
    return "done"


def _make(name, fn, interval=100.0):
    return make_sense(name, [], run=fn, poll_interval=interval)


class TestAHungSenseDoesNotStopTheOthers:
    def test_the_healthy_sense_is_still_polled(self, granted, small_budgets, tmp_path):
        """The whole defect, in one assertion."""
        store = PerceptStore(durable_path=tmp_path / "memory.jsonl")
        scheduler = AmbientScheduler(
            senses={"ocr": _make("ocr", _hang), "filesystem": _make("filesystem", _fast)},
            store=store)
        results = {}
        thread = threading.Thread(
            target=lambda: results.update(out=scheduler.poll_due()), daemon=True)
        thread.start()
        thread.join(10.0)
        assert not thread.is_alive(), (
            "poll_due() is still blocked: one hung sense stopped the loop")
        ran = {r.name: r for r in results["out"]}
        assert "filesystem" in ran, f"the healthy sense was never polled: {list(ran)}"
        assert ran["filesystem"].ok, ran["filesystem"].reason

    def test_the_hung_sense_reports_a_timeout_rather_than_a_crash(self, granted,
                                                                 small_budgets, tmp_path):
        store = PerceptStore(durable_path=tmp_path / "memory.jsonl")
        scheduler = AmbientScheduler(senses={"ocr": _make("ocr", _hang)}, store=store)
        (result,) = scheduler.poll_due()
        assert not result.ok, result.percept
        assert "timed out" in result.reason, result.reason
        # The result is dropped, not stored late. A percept arriving after the
        # scheduler had moved on would carry a timestamp from before it existed.
        assert result.percept is None

    def test_a_sense_that_raises_still_behaves_exactly_as_before(self, granted,
                                                                small_budgets, tmp_path):
        """The budget must not change the failure path it was added beside."""
        def boom(arguments):
            raise ValueError("the sense broke")

        store = PerceptStore(durable_path=tmp_path / "memory.jsonl")
        scheduler = AmbientScheduler(senses={"ocr": _make("ocr", boom)}, store=store)
        (result,) = scheduler.poll_due()
        assert not result.ok
        assert "ValueError" in result.reason, result.reason
        assert "the sense broke" in result.reason, result.reason

    def test_a_fast_sense_is_never_interrupted_by_its_budget(self, granted,
                                                             small_budgets, tmp_path):
        """The other direction, so the budget is not simply always firing."""
        store = PerceptStore(durable_path=tmp_path / "memory.jsonl")
        scheduler = AmbientScheduler(senses={"ocr": _make("ocr", _fast)}, store=store)
        (result,) = scheduler.poll_due()
        assert result.ok, result.reason

    def test_the_failure_is_counted_like_any_other(self, granted, small_budgets, tmp_path):
        """A timeout goes through `_note_failure`, so the senses' own failure
        tracking sees it rather than a hang being invisible in the numbers."""
        store = PerceptStore(durable_path=tmp_path / "memory.jsonl")
        scheduler = AmbientScheduler(senses={"ocr": _make("ocr", _hang)}, store=store)
        scheduler.poll_due()
        assert scheduler._failures.get("ocr") == 1, scheduler._failures

    def test_the_budget_is_the_senses_own_interval(self, granted):
        """Not a number chosen here. The scheduler already defines a sense slower
        than its own interval as a sense that is too slow - see `poll_due`'s
        comment on stamping `_last_poll` after the run."""
        from shani_chronoa.senses import scheduler as S
        # Restore the real constants; the fixture patched them for this module.
        assert S.budget_for(_make("ocr", _fast, interval=45.0)) == 45.0
        assert S.budget_for(_make("ocr", _fast, interval=2.0)) == S.SENSE_BUDGET_FLOOR
        assert S.budget_for(_make("ocr", _fast, interval=10_000.0)) == S.SENSE_BUDGET_CEILING

    def test_a_sub_second_budget_does_not_read_as_zero(self, granted, small_budgets,
                                                       tmp_path):
        """`:g`, not `:.0f` - "timed out after 0s" is a wrong number that reads as
        a rounding error rather than the bound the sense was given."""
        store = PerceptStore(durable_path=tmp_path / "memory.jsonl")
        scheduler = AmbientScheduler(senses={"ocr": _make("ocr", _hang)}, store=store)
        (result,) = scheduler.poll_due()
        assert "0.25s" in result.reason, result.reason


def make_sense(name, calls, run=None, poll_interval=100.0):
    """A sense with the real consent key, so `sense_allowed` says yes.

    Copied rather than imported from `test_sense_scheduler` on purpose: a test
    module importing another test module's helper couples two files that are
    supposed to be runnable apart, and `conftest` is the place for shared
    fixtures, not a sibling.
    """
    def _run(arguments):
        if calls is not None:
            calls.append(arguments)
        return run(arguments) if run else "ok"

    return Sense(
        name=name,
        kind="machine",
        ttl_seconds=60.0,
        sensitivity="public",
        schema={"type": "object", "properties": {}},
        run=_run,
        poll_interval=poll_interval,
    )


@pytest.fixture
def granted(chronoa_config):
    """Consent for the two local senses these tests poll, and nothing else."""
    chronoa_config.set("ocr-sense-enabled", "true")
    chronoa_config.set("filesystem-sense-enabled", "true")
    return chronoa_config


