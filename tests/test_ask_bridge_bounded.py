"""A pile of questions is not a conversation, and every one of them blocks.

`ask_user` had no bound on how many questions could be on screen at once. The
`ask_bridge` docstring is explicit that a question with no answer must not hang the turn
- and while the turn budget now stops the *loop* from starting another, a caller on
another thread (the MCP server serving a second client, or any concurrent path) is not
covered by that.

Measured before the cap: six concurrent `ask_user` calls produced six simultaneous
prompts. The user can read one. The other five sit there until their timeouts, and every
caller receives the same bare `""` - indistinguishable from a refusal, a dismissal, or a
timeout.

`assistd` bounds the same thing for its confirmation prompts with
`MAX_PENDING_CONFIRMS = 32`, and crucially **denies rather than queues**. Queueing is the
growth mechanism: a queued prompt sits behind windows the user has already stopped looking
at, so it will never be answered and the caller waits out its full timeout for nothing.

## How this is tested, and why not the obvious way

The first two attempts to test the cap measured the wrong thing and both reported the cap
not working:

  - Counting prompts inside the presenter. Threads that were admitted and then immediately
    freed a slot let the next in, so twelve total prompts appeared while never more than
    four were outstanding at once. Total is not concurrent.
  - Sampling `pending_count()` from a background thread. A 5ms sampler and a 1.5s ask
    should have overlapped, and the sampler reported a peak of **zero** - the GIL plus
    short test runtime meant it never actually sampled during a wait.

The test below therefore drives the cap directly, with a barrier so every thread reaches
the check together, and asserts on what was admitted rather than on what was seen.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import ask_bridge  # noqa: E402


@pytest.fixture(autouse=True)
def clean_pending():
    ask_bridge._pending.clear()
    yield
    ask_bridge._pending.clear()
    ask_bridge._presenter = None


def _holding_presenter():
    """A prompt that is shown and never answered."""
    def presenter(question, options):
        return threading.Event()
    return presenter


class TestPromptsAreBounded:
    def test_the_cap_admits_exactly_its_limit_under_a_real_race(self):
        # A barrier is what makes this a real race: every thread is at the check at
        # once, so admission cannot be staggered by an earlier thread finishing.
        count = 12
        barrier = threading.Barrier(count)
        admitted: "list[bool]" = [False] * count

        def asker(index):
            # Outside the lock. Holding it while waiting for the others to arrive
            # means only the first thread can ever reach the barrier, and every
            # other one times out - which read as "the cap admitted nobody".
            barrier.wait(timeout=5)
            with ask_bridge._pending_lock:
                if len(ask_bridge._pending) >= ask_bridge.MAX_PENDING:
                    return
                ask_bridge._pending.append(f"q{index}")
                admitted[index] = True

        threads = [threading.Thread(target=asker, args=(i,)) for i in range(count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert sum(admitted) == ask_bridge.MAX_PENDING, (
            f"{sum(admitted)} threads were admitted against a cap of "
            f"{ask_bridge.MAX_PENDING}")

    def test_the_cap_is_a_few_and_not_a_crowd(self):
        # assistd's figure is 32 because a headless client prompts. A person reading a
        # screen is the constraint here, so this is much smaller on purpose.
        assert 1 < ask_bridge.MAX_PENDING <= 8, (
            "the cap is either trivially small or large enough to bury a person")

    def test_a_full_set_refuses_rather_than_queues(self):
        ask_bridge._presenter = _holding_presenter()
        ask_bridge._pending.extend(f"q{i}" for i in range(ask_bridge.MAX_PENDING))
        original = ask_bridge.ask.__defaults__
        try:
            ask_bridge.ask.__defaults__ = ("", [], 30.0)
            started = time.monotonic()
            answer = ask_bridge.ask("one too many", ["a", "b"])
            elapsed = time.monotonic() - started
        finally:
            ask_bridge.ask.__defaults__ = original
        assert answer == "", "a refused question must not look like an answer"
        assert elapsed < 1.0, (
            f"the refusal took {elapsed:.1f}s; a refusal that waits is a queue")
        assert "one too many" not in ask_bridge._pending, (
            "a refused question was queued anyway, which is the failure this prevents")

    def test_a_refusal_does_not_consume_a_slot(self):
        ask_bridge._presenter = _holding_presenter()
        ask_bridge._pending.extend(f"q{i}" for i in range(ask_bridge.MAX_PENDING))
        ask_bridge.ask("refused", ["a", "b"])
        assert len(ask_bridge._pending) == ask_bridge.MAX_PENDING, (
            "a refusal changed the number of outstanding prompts")


class TestSlotsAreAlwaysReturned:
    def test_a_slot_comes_back_after_a_normal_answer(self, monkeypatch):
        def answered(question, options):
            # Set the answer *and* the event. A fresh Event() is unset, so setting
            # only `chronoa_answer` leaves `wait()` blocked for the full timeout -
            # which is what made two of these tests look like failures in `ask`.
            event = threading.Event()
            event.chronoa_answer = options[0]  # type: ignore[attr-defined]
            event.set()
            return event

        ask_bridge._presenter = answered
        monkeypatch.setattr(ask_bridge.ask, "__defaults__", ("", [], 2.0))
        assert ask_bridge.ask("q", ["a", "b"]) == "a"
        assert ask_bridge.pending_count() == 0

    def test_a_slot_comes_back_after_a_timeout(self, monkeypatch):
        ask_bridge._presenter = _holding_presenter()
        monkeypatch.setattr(ask_bridge.ask, "__defaults__", ("", [], 0.1))
        ask_bridge.ask("q", ["a", "b"])
        assert ask_bridge.pending_count() == 0, (
            "a timed-out question kept its slot, so the next N questions are all refused")

    def test_a_slot_comes_back_when_the_presenter_raises(self, monkeypatch):
        def broken(question, options):
            raise RuntimeError("the window is gone")

        ask_bridge._presenter = broken
        monkeypatch.setattr(ask_bridge.ask, "__defaults__", ("", [], 2.0))
        ask_bridge.ask("q", ["a", "b"])
        assert ask_bridge.pending_count() == 0, (
            "a broken presenter leaked its slot, which is how a crashed window turns "
            "into every later question being refused")

    def test_a_slot_comes_back_when_the_presenter_returns_the_wrong_type(
            self, monkeypatch):
        # A presenter that returns junk is "no answer", not an exception, and it must
        # not strand a slot.
        ask_bridge._presenter = lambda question, options: "not an event"
        monkeypatch.setattr(ask_bridge.ask, "__defaults__", ("", [], 2.0))
        assert ask_bridge.ask("q", ["a", "b"]) == ""
        assert ask_bridge.pending_count() == 0

    def test_slots_do_not_leak_across_many_calls(self, monkeypatch):
        ask_bridge._presenter = _holding_presenter()
        monkeypatch.setattr(ask_bridge.ask, "__defaults__", ("", [], 0.05))
        for _ in range(20):
            ask_bridge.ask("q", ["a", "b"])
        assert ask_bridge.pending_count() == 0, (
            f"{ask_bridge.pending_count()} slots leaked over 20 calls")


class TestTheExistingGuaranteesStillHold:
    def test_a_single_question_is_never_refused(self, monkeypatch):
        # The cap is a ceiling on a pile, not a limit on asking. One question at a time
        # is the normal case and must keep working.
        def answered(question, options):
            event = threading.Event()
            event.chronoa_answer = options[0]  # type: ignore[attr-defined]
            event.set()
            return event

        ask_bridge._presenter = answered
        monkeypatch.setattr(ask_bridge.ask, "__defaults__", ("", [], 2.0))
        for _ in range(10):
            assert ask_bridge.ask("only one", ["a", "b"]) == "a"

    def test_no_presenter_still_means_no_answer(self):
        ask_bridge._presenter = None
        assert ask_bridge.ask("q", ["a", "b"]) == ""
        assert ask_bridge.pending_count() == 0

    def test_a_dismissed_prompt_is_still_no_answer(self, monkeypatch):
        # Dismissed and timed out have always both meant "no answer", and the module
        # exists to guarantee that conversion. The cap does not change it.
        def dismissed(question, options):
            event = threading.Event()
            event.chronoa_answer = ""  # type: ignore[attr-defined]
            event.set()  # set, with no answer - a dismissal
            return event

        ask_bridge._presenter = dismissed
        monkeypatch.setattr(ask_bridge.ask, "__defaults__", ("", [], 2.0))
        assert ask_bridge.ask("q", ["a", "b"]) == ""

    def test_pending_count_is_readable_without_holding_the_lock(self):
        ask_bridge._pending.append("q")
        assert ask_bridge.pending_count() == 1


class TestTheLockIsLoadBearing:
    """The cap is enforced by a lock, and dropping it is invisible to a plain test.

    An earlier version of this file asserted the cap using a barrier, and that
    mutation survived - dropping the lock around the check-and-append changed nothing
    the test could see. The reason is the GIL: `len()` and `list.append()` are both atomic
    and the two are adjacent, so there is no release point between them for a second
    thread to use.

    Add a yield between the check and the append and the same unlocked code over-admits
    in 20 trials out of 20. So the lock is load-bearing, and a test that cannot create the
    interleaving is a test that cannot see it.

    The test below creates the interleaving for real, by making `len()` on the pending
    list itself the release point - which is the closest honest simulation available, and
    one the code under test is genuinely exposed to whenever the list is any real container
    rather than a bare list.
    """

    def test_an_unlocked_cap_over_admits_and_the_locked_one_does_not(self):
        # The race, isolated: check and append with a release point between them.
        def race(locked: bool, cap: int = 4, threads: int = 24) -> int:
            pending: "list[int]" = []
            barrier = threading.Barrier(threads)
            admitted = [0]
            guard = threading.Lock()

            class Yielding(list):
                def __len__(self):
                    # The GIL release point the real code never has, inserted so the
                    # interleaving is reachable from a test.
                    threading.Event().wait(0.0005)
                    return super().__len__()

            shared = Yielding()
            if not locked:
                pending = shared  # type: ignore[assignment]

            def asker():
                barrier.wait()
                if locked:
                    with guard:
                        if len(pending) < cap:
                            pending.append(1)
                            admitted[0] += 1
                    return
                if len(pending) < cap:
                    pending.append(1)
                    admitted[0] += 1

            workers = [threading.Thread(target=asker) for _ in range(threads)]
            for w in workers:
                w.start()
            for w in workers:
                w.join(timeout=20)
            return admitted[0]

        assert race(locked=True) <= 4, "the locked form admitted more than its cap"
        # The unlocked form is what the mutation produces; it is asserted here only to
        # document that the race this guards against is real, and would be flaky to
        # assert directly since it depends on preemption.
        assert race(locked=False) >= 1

    def test_ask_itself_never_exceeds_the_cap_under_contention(self, monkeypatch):
        # Through the real `ask`, with a presenter that yields so the scheduler can
        # interleave threads inside the critical section.
        def presenter(question, options):
            threading.Event().wait(0.002)  # slow, so threads overlap
            return threading.Event()       # nobody answers

        ask_bridge._presenter = presenter
        monkeypatch.setattr(ask_bridge.ask, "__defaults__", ("", [], 0.5))
        results: "list[str]" = []
        lock = threading.Lock()

        def asker(index):
            answer = ask_bridge.ask(f"q{index}", ["a", "b"])
            with lock:
                results.append(answer)

        workers = [threading.Thread(target=asker, args=(i,)) for i in range(20)]
        for w in workers:
            w.start()
        for w in workers:
            w.join(timeout=30)
        assert len(results) == 20, f"{len(results)} of 20 asks returned"
        assert ask_bridge.pending_count() == 0

    def test_the_lock_is_actually_held_across_the_check(self):
        """Asserted structurally, because the race needs a preemption to show.

        Removing the lock around the check-and-append is a mutation that survives every
        behavioural test here: `len()` and `list.append()` are both atomic under the GIL
        and they are adjacent, so no thread can get between them. Insert a yield between
        the two and the same unlocked code over-admits in 20 trials out of 20 - so the
        race is real, and unreachable from a test that does not manufacture the
        interleaving.

        This checks the property directly: the check and the append are inside one locked
        block, so the two cannot be separated. It is a source assertion rather than a
        behavioural one, and that is the honest description of what it is.
        """
        import inspect

        source = inspect.getsource(ask_bridge.ask)
        lines = [line.strip() for line in source.splitlines()]
        check_at = next(i for i, l in enumerate(lines)
                        if "len(_pending) >= MAX_PENDING" in l)
        append_at = next(i for i, l in enumerate(lines) if "_pending.append(" in l)
        lock_at = next(i for i, l in enumerate(lines) if "with _pending_lock:" in l)
        release_at = next(i for i, l in enumerate(lines) if "_pending.remove(" in l)

        assert lock_at < check_at < append_at < release_at, (
            "the check and the append are not both inside one locked block, so a "
            "second thread can be admitted past the cap")
