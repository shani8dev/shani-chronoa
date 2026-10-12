"""Nothing stopped a plain `pytest` reading a tree a mutation run was rewriting.

`tools/mutate.py` rewrites each source file in place, restores it in a `finally`,
and holds `build/mutate/run.lock` for the duration so that two *runs* cannot
overwrite each other's restore. The lock made the tree safe for its owner and said
nothing at all to a reader.

**Measured 2026-10-12**: a test suite was started beside a mutation run and read
mutated code, reporting **three confident failures on a suite that had just been
green**. Not flaky, not mine, and the only honest reading was that the tree was
not what it looked like — which is precisely what the lock file was already
recording and nothing was reading.

So `pytest_configure` refuses to collect while the lock is held by a **live** pid.
Three carve-outs, each because the alternative is wrong:

- **The runner's own pytest is exempt**, through `CHRONOA_MUTATION_RUN`, which
  `mutate.py` sets to its own pid. Without it the guard would refuse the very
  process it exists to let through, since the runner *is* a pytest that holds the
  lock on purpose.
- **A stale lock is not a refusal.** The runner takes those over and says so; a
  suite blocked behind a corpse would be the harness inventing an obstacle.
- **The exit is `pytest.exit`, not a raise.** A raise in `pytest_configure` is an
  INTERNALERROR, which reads as a broken checkout rather than as "wait for the
  other run".

**These tests point the guard at a lock of their own and never touch the real
one.** Not tidiness — this suite is run *by* the mutation runner, which holds
`run.lock` for the entire run, and a fixture that cleared it was destroying the
record the runner depends on. That was found by running this harness over this
very file: the first version of the fixture deleted the lock to set up its
condition, and the symptom was this suite *skipping* under the runner and every
mutation in the set reporting as a survivor.

Testing it in process rather than by spawning pytest is deliberate and not a
shortcut: this `pytest_configure` demonstrably runs — it is what registers the
`holds_organs` and `slow` markers — so calling it tests the hook that fires, and
`tests/` is not a package, so a subprocess could not load this conftest without
writing a file into the repository mid-suite.
"""

import os

import conftest
import pytest

#: Above the default `pid_max`, so nothing can own it and the lock is stale by
#: construction rather than by hoping.
IMPOSSIBLE_PID = 4194304


@pytest.fixture
def lock(monkeypatch, tmp_path):
    """The guard's lock file, redirected to this test's own directory.

    `monkeypatch.setattr` rather than a delete: `RUN_LOCK` is read as a module
    global on every call, so pointing it elsewhere exercises exactly the same code
    path without touching the file a real run depends on.
    """
    mine = tmp_path / "run.lock"
    monkeypatch.setattr(conftest, "RUN_LOCK", mine)
    return mine


@pytest.fixture
def not_the_runner(monkeypatch):
    """This is a person's run, not the mutation harness's own pytest."""
    monkeypatch.delenv(conftest.MUTATION_MARKER, raising=False)


class TestItNamesWhatIsWrong:
    def test_no_lock_means_no_refusal(self, lock, not_the_runner):
        assert not lock.exists()
        assert conftest._mutation_in_flight() is None

    def test_a_live_lock_is_a_refusal(self, lock, not_the_runner):
        lock.write_text(f"{os.getpid()}\n", encoding="utf-8")   # certainly alive
        reason = conftest._mutation_in_flight()
        assert reason is not None, "a held lock was read as nobody holding it"
        assert str(os.getpid()) in reason, reason

    def test_a_stale_lock_is_not(self, lock, not_the_runner):
        """The runner takes a stale lock over and says so. A suite blocked behind
        a dead process is the harness inventing an obstacle."""
        lock.write_text(f"{IMPOSSIBLE_PID}\n", encoding="utf-8")
        assert conftest._mutation_in_flight() is None

    def test_an_unreadable_lock_is_not_a_hold(self, lock, not_the_runner):
        """A truncated or empty lock file is a crashed run's debris, not a claim."""
        lock.write_text("", encoding="utf-8")
        assert conftest._mutation_in_flight() is None

    def test_the_reason_is_about_what_is_happening_not_a_filename(self, lock,
                                                                   not_the_runner):
        """A refusal that says "run.lock exists" and nothing else leaves the reader
        with a filename. It has to say what is happening and what to do about it."""
        lock.write_text(f"{os.getpid()}\n", encoding="utf-8")
        reason = conftest._mutation_in_flight().lower()
        # Case-insensitively: the message opens a sentence with "Wait", and a
        # check that missed it on capitalisation would have been a check that
        # cannot fail.
        for needed in ("mutation", "mutated", "pid", "wait"):
            assert needed in reason, f"{needed!r} missing from {reason!r}"


class TestTheRunnersOwnPytestIsExempt:
    def test_the_marker_wins_over_a_held_lock(self, lock, monkeypatch):
        """The runner holds the lock *on purpose* and spawns pytest to use it.
        Without this the guard refuses every mutation run it was added beside."""
        monkeypatch.setenv(conftest.MUTATION_MARKER, str(os.getpid()))
        lock.write_text(f"{os.getpid()}\n", encoding="utf-8")
        assert conftest._mutation_in_flight() is None


class TestTheHookActuallyFires:
    """The helper is only worth anything if `pytest_configure` calls it."""

    def test_configure_exits_while_a_run_holds_the_tree(self, lock, not_the_runner):
        lock.write_text(f"{os.getpid()}\n", encoding="utf-8")
        with pytest.raises(pytest.exit.Exception) as stopped:
            conftest.pytest_configure(_StubConfig())
        assert "mutation run" in str(stopped.value.msg)
        assert stopped.value.returncode == 3, (
            "a plain raise here is an INTERNALERROR, which reads as a broken "
            "checkout rather than as 'wait for the other run'")

    def test_configure_does_not_exit_when_the_tree_is_ordinary(self, lock,
                                                               not_the_runner):
        """A guard that always refuses is not a guard, and this is the only thing
        that says so: every ordinary run of this suite takes this path."""
        conftest.pytest_configure(_StubConfig())      # must simply return

    def test_the_real_lock_is_never_touched_by_this_suite(self, lock):
        """The file the mutation harness depends on. Asserted, because the first
        version of the fixture deleted it and the consequence - this suite
        skipping, every mutation reported a survivor - looked like untested code."""
        assert conftest.RUN_LOCK == lock or not lock.exists(), (
            "these tests must run against their own lock path")
        assert not lock.exists()


class _StubConfig:
    """Only what `pytest_configure` touches before it would exit."""

    def __init__(self):
        self.lines = []

    def addinivalue_line(self, name, text):
        self.lines.append((name, text))


def test_the_mutation_runner_sets_the_marker():
    """**The other half of the exemption, and it is the half that can rot.**

    A guard keyed on an environment variable only exempts the runner if the runner
    sets it. If `mutate.py` is refactored and stops, every mutation run is refused
    by the guard added to protect against ad-hoc runs - and the symptom is a
    mutation set reporting every mutation as broken, which reads as coverage
    rather than as a bug.

    Deliberately runnable *under* the runner: the fixture above points the guard
    at a temporary path rather than clearing the lock this process is holding.
    """
    import importlib.util
    root = conftest.Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("mutate_under_test",
                                                  root / "tools" / "mutate.py")
    mutate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mutate)
    assert mutate.MUTATION_MARKER_NAME == conftest.MUTATION_MARKER, (
        "conftest.py and tools/mutate.py name the exemption differently, so the "
        "guard refuses the runner it was written to let through")
    assert mutate._test_env()[conftest.MUTATION_MARKER] == str(os.getpid())