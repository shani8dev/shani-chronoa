"""The mutation harness, and whether it can fail.

oh-my-openagent's `script/qa/desktop/linux/expect-sabotage.ts` verifies a
deliberately-failing run and its own comment states the rule:

> A failing run is not a successful negative control unless the intended opt-in
> check failed **and** both the process and fixture directory were cleaned up.

`tools/mutate.py` is that rule as a tool, so it is itself subject to it. A harness
that reports "caught" for every mutation - including mutations aimed at nothing,
mutations whose witnesses do not exist, and mutations of a file with no tests
covering them - is the same failure as a test that always passes, and it is worse
because it reads as evidence. **Every commit in this repo that says "N of N
mutations confirmed to fail" cites this tool now.**

Three things are asserted here, and each is a different way the harness could lie:

1. **It reports caught** for a mutation a test really covers, and survivor for
   one nothing covers.
2. **It refuses a mutation whose anchor does not exist** - the count assertion.
   A `str.replace` that silently matched nothing has twice produced a survivor
   reported against unmutated code; asserting the count before the write is what
   makes that impossible rather than merely unlikely.
3. **It leaves the tree exactly as it found it** - the cleanup half of the
   sabotage rule, for a harness whose fixture is a source file.

Everything here runs against a *fixture* file written into a temp directory, so no
test touches the real package.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUNNER = REPO / "tools" / "mutate.py"
_SET_DIR = REPO / "tools" / "mutations"

#: A fixture file with a real invariant, and a test that covers it.
FIXTURE = '''"""A fixture with one invariant for the harness to break."""

TOTAL = 3


def total(items):
    """A sum that is checked against a known total."""
    return sum(items)
'''

FIXTURE_TEST = '''from __future__ import annotations

import sys
from pathlib import Path

# The fixture tree is shaped like the repository, so the module under test is
# two directories down in `usr/lib` - the same layout this repo uses.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "usr" / "lib"))

import fixture_module  # noqa: E402


def test_the_total_is_what_the_docstring_says():
    assert fixture_module.total([1, 2]) == 3
'''


def _run(*args, cwd=REPO, timeout=600):
    return subprocess.run(
        [sys.executable, str(RUNNER), *args],
        capture_output=True, text=True, cwd=str(cwd), timeout=timeout,
    )


def _write_fixture_set(directory: Path, mutations):
    path = directory / "tools" / "mutations" / "selfcheck.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = [
        'TEST_FILE = "tests/test__mutation_selfcheck.py"',
        "",
        "MUTATIONS = [",
    ]
    for entry in mutations:
        body.append(f"    {entry!r},")
    body.append("]")
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    return "selfcheck"


class TestTheHarnessCanFail:
    """A runner that cannot fail is not a runner."""

    def _fixture(self, tmp_path):
        (tmp_path / "tools" / "mutations").mkdir(parents=True)
        (tmp_path / "tools").mkdir(parents=True, exist_ok=True)
        (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
        (tmp_path / "usr" / "lib").mkdir(parents=True, exist_ok=True)
        (tmp_path / "usr" / "lib" / "fixture_module.py").write_text(
            FIXTURE, encoding="utf-8")
        (tmp_path / "tests" / "test__mutation_selfcheck.py").write_text(
            FIXTURE_TEST, encoding="utf-8")
        return tmp_path

    def test_a_covered_mutation_is_reported_caught(self, tmp_path):
        self._fixture(tmp_path)
        name = _write_fixture_set(tmp_path, [{
            "file": "usr/lib/fixture_module.py",
            "old": "    return sum(items)\n",
            "new": "    return sum(items) + 1\n",
            "label": "the fixture's total is off by one",
            "expect": ["test_the_total_is_what_the_docstring_says"],
        }])
        out = _run(name, cwd=tmp_path)
        assert out.returncode == 0, out.stdout + out.stderr
        assert "1 mutations, 1 caught, 0 survivors" in out.stdout, out.stdout
        assert "caught by test_the_total" in out.stdout, out.stdout

    def test_an_uncovered_mutation_is_a_survivor(self, tmp_path):
        """**The control.** A mutation nothing covers must be reported as one,
        or "all mutations caught" means nothing at all."""
        self._fixture(tmp_path)
        name = _write_fixture_set(tmp_path, [{
            "file": "usr/lib/fixture_module.py",
            "old": '"""A fixture with one invariant for the harness to break."""\n',
            "new": '"""A fixture.\n',
            "label": "a docstring nobody tests",
            "expect": ["test_a_test_that_does_not_exist"],
        }])
        out = _run(name, cwd=tmp_path)
        assert out.returncode == 1, out.stdout + out.stderr
        assert "0 survivors" not in out.stdout, (
            f"an uncovered mutation was reported caught:\n{out.stdout}")
        assert "SURVIVOR" in out.stdout, out.stdout

    def test_a_witness_that_matches_nothing_is_reported(self, tmp_path):
        """A mutation that breaks the code but names the wrong test.

        **This is the failure mode that produced a false "all caught" once**: the
        witness named a base id while the suite's ids were parametrized, so five
        tests were failing for exactly the right reason and the run said
        "survivor". The runner reports both halves now.
        """
        self._fixture(tmp_path)
        name = _write_fixture_set(tmp_path, [{
            "file": "usr/lib/fixture_module.py",
            "old": "    return sum(items)\n",
            "new": "    return sum(items) + 1\n",
            "label": "the fixture's total is off by one",
            "expect": ["test_some_other_test"],
        }])
        out = _run(name, cwd=tmp_path)
        assert out.returncode == 1, out.stdout + out.stderr
        assert "SURVIVOR" in out.stdout, out.stdout
        assert "failed for another reason" in out.stdout, (
            "the runner hid which tests actually failed:\n" + out.stdout)

    def test_an_absent_anchor_is_refused_not_skipped(self, tmp_path):
        """**The count assertion.** A `str.replace` that matched nothing is a
        no-op, and a no-op mutation looks exactly like a caught one."""
        self._fixture(tmp_path)
        name = _write_fixture_set(tmp_path, [{
            "file": "usr/lib/fixture_module.py",
            "old": "    return sum(nothing_here)\n",
            "new": "    return sum(items)\n",
            "label": "aimed at text that is not there",
            "expect": ["test_the_total_is_what_the_docstring_says"],
        }])
        out = _run(name, cwd=tmp_path)
        assert out.returncode == 1, out.stdout + out.stderr
        assert "not once" in out.stderr + out.stdout, out.stdout + out.stderr
        assert "0 caught" in out.stdout or "mutations, 0 caught" in out.stdout, (
            f"an absent anchor was reported as a result:\n{out.stdout}")

    def test_an_anchor_occurring_twice_is_refused(self, tmp_path):
        """**The other half of "exactly once", and it had no witness.**

        `test_an_absent_anchor_is_refused_not_skipped` covers the count-0 case.
        The count-2 case is a different mistake - an anchor that matches the same
        text in two places, where `str.replace` would rewrite **both**, so the
        mutation would be twice as destructive than its label says - and
        accepting it was caught only by a constant witness in `harness`, which
        says nothing about this particular rule.
        """
        self._fixture(tmp_path)
        doubled = tmp_path / "usr" / "lib" / "fixture_module.py"
        doubled.write_text(FIXTURE + FIXTURE, encoding="utf-8")
        name = _write_fixture_set(tmp_path, [{
            "file": "usr/lib/fixture_module.py",
            "old": "    return sum(items)\n",
            "new": "    return sum(items) + 1\n",
            "label": "aimed at text that appears twice",
            "expect": ["test_the_total_is_what_the_docstring_says"],
        }])
        out = _run(name, cwd=tmp_path)
        assert out.returncode == 1, out.stdout + out.stderr
        assert "not once" in out.stderr + out.stdout, (
            "an anchor matching twice was applied to both sites:\n"
            + out.stdout + out.stderr)

    def test_the_journal_brackets_the_test_run(self, tmp_path):
        """**The ordering, on the real `run_set`, not on a sequence typed here.**

        Three claims were unwitnessed because a constant rot-check witness
        carried them: that `run_set` writes the journal at all, that it writes
        it *before* the mutation, and that it clears it only after the file is
        back. Each matters, because a kill in the gaps loses exactly what the
        journal was added to protect.

        Driven by instrumenting the runner's own functions, so the claim is about
        the order `run_set` performs them in - not about an order this test
        wrote itself and then checked.
        """
        self._fixture(tmp_path)
        _write_fixture_set(tmp_path, [{
            "file": "usr/lib/fixture_module.py",
            "old": "    return sum(items)\n",
            "new": "    return sum(items) + 1\n",
            "label": "covered",
            "expect": ["test_the_total_is_what_the_docstring_says"],
        }])
        namespace = {"__file__": str(RUNNER)}
        exec(compile(RUNNER.read_text(encoding="utf-8"), str(RUNNER), "exec"),
             namespace)
        namespace["ROOT"] = tmp_path
        order = []

        def instrument(key):
            original = namespace[key]

            def wrapper(*a, **kw):
                order.append(key)
                return original(*a, **kw)

            namespace[key] = wrapper

        for key in ("_journal_write", "_journal_clear", "_apply", "_failures"):
            instrument(key)

        namespace["run_set"](
            "selfcheck", tmp_path / "tools" / "mutations" / "selfcheck.py",
            verbose=False)

        assert order, "nothing was instrumented, so this control proves nothing"
        assert order[0] == "_journal_write", (
            "the journal is not written before the mutation, so a kill in "
            f"between leaves the tree mutated with no record: {order}")
        assert order[-1] == "_journal_clear", (
            "the journal is not cleared last, so a kill before it recovers a "
            f"tree that is already restored: {order}")
        assert order.index("_apply") < order.index("_failures"), (
            "the mutation is applied after the tests are run")
        assert order.index("_apply") < order.index("_journal_clear"), (
            "the journal is cleared before the mutation is put back")

    def test_the_tree_is_restored(self, tmp_path):
        """The cleanup half of the sabotage rule: the fixture is a source file, so
        it must be byte-identical after the run - including on the failing path.
        """
        self._fixture(tmp_path)
        target = tmp_path / "usr" / "lib" / "fixture_module.py"
        before = target.read_bytes()
        name = _write_fixture_set(tmp_path, [
            {"file": "usr/lib/fixture_module.py",
             "old": "    return sum(items)\n",
             "new": "    return sum(items) + 1\n",
             "label": "covered",
             "expect": ["test_the_total_is_what_the_docstring_says"]},
            {"file": "usr/lib/fixture_module.py",
             "old": "    return sum(items) + 1\n",     # absent once the first ran
             "new": "    return 0\n",
             "label": "an anchor that no longer exists",
             "expect": ["test_whatever"]},
        ])
        _run("--keep-going", name, cwd=tmp_path)
        assert target.read_bytes() == before, (
            "the harness left the fixture mutated; a second run would then be "
            "measuring a tree that nobody asked for")

    def test_a_witness_matches_its_parametrization(self):
        """`test_foo` must not match `test_foo_bar`, and `test_foo` must match
        `test_foo[case]` - the `[` guard is the difference between a witness that
        names one test and one that names everything.

        The runner is imported by `exec` with `__file__` supplied, because a bare
        `exec` of a module that resolves its own path raises `NameError` - which
        the first version of this test did, and which made the harness look like
        it could not be imported at all.
        """
        namespace = {"__file__": str(RUNNER)}
        exec(compile(RUNNER.read_text(encoding="utf-8"), str(RUNNER), "exec"),
             namespace)
        hit = namespace["_witness_hit"]
        assert hit("test_a", {"test_a", "test_b"}) is True
        assert hit("test_a", {"test_a[case]"}) is True
        assert hit("test_a", {"test_apple"}) is False
        assert hit("test_a", {"test_b"}) is False

    def test_the_runner_lists_its_sets(self):
        """Every set on disk is listed, and every listed set has a test file.

        **This used to name four sets in a tuple**, which is how the five added
        later (`clip`, `goal_edits`, `maps`, `ir_remote`, `fm_radio`) sat in the
        repository for a session with the test claiming to be the inventory and
        not mentioning them. A hand-kept list of the thing a directory already
        knows is a list that rots, and the docstring above this class says a set
        must not rot quietly into being unused.

        So the names are read off the directory rather than typed, which means
        adding a set cannot make this test stale, and *this* set cannot be
        removed without this failing.
        """
        out = _run("--list")
        assert out.returncode == 0, out.stdout + out.stderr

        on_disk = sorted(p.stem for p in _SET_DIR.glob("*.py")
                         if p.stem != "__init__")
        assert on_disk, f"no mutation sets found in {_SET_DIR}"
        for name in on_disk:
            assert name in out.stdout, (
                f"{name}.py is checked in beside the code it backs but the "
                f"runner does not list it, so it never runs:\n{out.stdout}")

        # And the reverse direction: a set that loads and names no test file
        # cannot be run at all, and `--list` printing "?" is easy to skim past.
        # **The test file is the last whitespace-separated token**, which is the
        # shape `--list` prints. The first version split on the run's own column
        # padding and read `4 mutations, tests/foo.py` as the path, so it
        # reported the first real line as a missing test file.
        for line in out.stdout.splitlines():
            if not line.strip():
                continue
            assert "?" not in line, (
                f"a set does not declare TEST_FILE, so there is nothing to run "
                f"it against:\n{line}")
            test_file = line.split()[-1]
            assert test_file.endswith(".py"), f"unreadable --list line: {line!r}"
            assert (REPO / test_file).is_file(), (
                f"{test_file} does not exist, so this set reports against a file "
                f"nothing will ever run:\n{line}")


class TestTheTreeIsOnlyEverHeldByOneRun:
    """**Two rules added because this harness itself corrupted the repository.**

    A `finally` restores a file, which covers a crash and a timeout. It does not
    cover `SIGKILL`, and it does not cover **two runs at once**: each holds its
    own copy of the original, so one run's restore overwrites the other run's
    mutation, or lands while the other is mid-mutation and silently reverts a
    live edit.

    That is not theoretical. A run started before a session boundary was still
    alive when the next one began; killing both left **two mutations stuck
    applied** to `fm_radio.py` and `fmdsp.py` - the discriminator's phase wrap
    deleted, the lowpass's Nyquist zero dropped - which read as a defect somebody
    had just introduced, in files this harness is responsible for.

    The rule at the top of this module now says "one run at a time, and a killed
    run says so". These are the controls that can tell it.
    """

    def _runner(self):
        namespace = {"__file__": str(RUNNER)}
        exec(compile(RUNNER.read_text(encoding="utf-8"), str(RUNNER), "exec"),
             namespace)
        return namespace

    def test_a_second_run_is_refused_and_says_whose(self, tmp_path):
        ns = self._runner()
        ns["STATE_DIR"] = tmp_path / "build" / "mutate"
        first = ns["_Lock"]()
        first.__enter__()
        try:
            second = ns["_Lock"]()
            with pytest.raises(ns["_Busy"]) as refusal:
                second.__enter__()
            assert str(os.getpid()) in str(refusal.value), (
                "the refusal does not name the holder, so a person cannot tell "
                "which run to wait for or stop:\n" + str(refusal.value))
        finally:
            first.__exit__(None, None, None)
        assert not (ns["STATE_DIR"] / "run.lock").exists(), (
            "the lock outlived the run that took it, so the next run is refused "
            "forever")

    def test_a_stale_lock_is_taken_over_and_says_so(self, tmp_path):
        """A crashed run must not deadlock the next attempt - and taking its lock
        silently would be the same class as silently deleting a rotated anchor.
        """
        ns = self._runner()
        state = tmp_path / "build" / "mutate"
        state.mkdir(parents=True)
        # **This line was missing in the first version, and the test passed
        # anyway.** `_Lock.__init__` resolves `STATE_DIR` when the object is
        # built, so without it the lock was taken in the *repository's* build
        # directory and the stale file written here was never read - the test
        # was asserting that a fresh lock names its own pid, and calling it a
        # takeover. It is in both this test and the one above now, and the
        # assertion below is what noticed.
        ns["STATE_DIR"] = state
        # A pid that cannot be running: `os.kill(pid, 0)` is how `_stale`
        # decides, and this one answers ESRCH.
        (state / "run.lock").write_text("4194303\n", encoding="utf-8")
        lock = ns["_Lock"]()
        assert lock.path == state / "run.lock", (
            f"the lock is not looking at the fixture: {lock.path}")
        lock.__enter__()
        try:
            assert lock.held is True, "a dead owner's lock was not taken over"
            assert (state / "run.lock").read_text(encoding="utf-8").strip() \
                == str(os.getpid())
        finally:
            lock.__exit__(None, None, None)

    def test_a_killed_runs_mutations_are_put_back_by_the_next_run(self, tmp_path):
        """**The half a `finally` cannot do.**

        The tree is left mutated with nothing recording it, and it reads as an
        edit - which is exactly what happened to `fm_radio.py` and `fmdsp.py`.
        So the pre-run text is journalled before the first mutation, and the next
        run restores it and says which files it touched.
        """
        ns = self._runner()
        tree = tmp_path / "usr" / "lib"
        tree.mkdir(parents=True)
        target = tree / "fixture_module.py"
        original = FIXTURE
        target.write_text(original, encoding="utf-8")
        ns["STATE_DIR"] = tmp_path / "build" / "mutate"
        ns["ROOT"] = tmp_path

        # What a killed run leaves: the file mutated, and the journal still
        # holding what it was.
        target.write_text(original + "\n# STUCK\n", encoding="utf-8")
        ns["_journal_write"]({target: original})

        assert ns["restore_pending"]() == ["usr/lib/fixture_module.py"], (
            "a mutated tree is indistinguishable from an edited one, which is "
            "the whole failure")
        recovered = ns["_recover_stale_journal"]()
        assert recovered == ["usr/lib/fixture_module.py"], recovered
        assert target.read_text(encoding="utf-8") == original, (
            "the recovery did not put the file back")
        assert ns["restore_pending"]() == [], (
            "the journal survived the recovery, so the next run would repeat it")

    def test_the_journal_is_cleared_only_after_the_files_are_back(self, tmp_path):
        """The ordering, which is a real window and not a style preference.

        Clearing the journal *first* leaves a gap in which the tree is restored
        and the journal still says otherwise - and a run killed in that gap
        recovers nothing, which is the failure the journal exists to catch.
        """
        ns = self._runner()
        tree = tmp_path / "usr" / "lib"
        tree.mkdir(parents=True)
        target = tree / "fixture_module.py"
        target.write_text(FIXTURE, encoding="utf-8")
        ns["STATE_DIR"] = tmp_path / "build" / "mutate"
        ns["ROOT"] = tmp_path
        order = []
        original_write = type(target).write_text

        def record(self, *a, **kw):
            order.append("write")
            return original_write(self, *a, **kw)

        original_clear = ns["_journal_clear"]
        type(target).write_text = record
        try:
            ns["_journal_write"]({target: FIXTURE})
            order.clear()
            for path, text in {target: FIXTURE}.items():
                path.write_text(text, encoding="utf-8")
            ns["_journal_clear"]()
        finally:
            type(target).write_text = original_write
            ns["_journal_clear"] = original_clear
        assert order == ["write"], (
            "nothing was written, so this control proves nothing - it must fail "
            "if the restore stops happening")


class TestNoSetIsLeftBehind:
    """Every checked-in set must still run.

    A mutation set whose anchor rots - because the code it aimed at was refactored
    - asserts on the write and errors out, which is the *right* behaviour and also
    a signal: the set needs updating, not deleting. This test is what says so
    loudly instead of the tool being quietly unused.
    """

    @pytest.mark.slow
    def test_no_anchor_has_rot(self):
        """**The cheap half, and it is the half that can run every time.**

        This is the failure the class above is named for: a set whose anchor no
        longer matches the code. It costs a file read per mutation and finishes
        in well under a second, because `_apply`'s count assertion is the whole
        of it - no test suite is executed.

        The expensive half (every mutation still caught) is
        `test_every_set_runs_clean` below and **cannot** run every time: the
        inventory was under two minutes with four sets and is over ten with
        eleven, `fm_radio` alone synthesising 200 kS/s of signal per mutation.
        Leaving it in the default suite put it past `_run`'s 600 s cap, so the
        test failed on a **timeout** - the guarantee gone, and reported as a
        failure about something else entirely.

        **The first version of this walked the sets itself**, and two things came
        out of that, both worth keeping:

        - It reported four anchors as rotted in sets that had run green minutes
          earlier. The reason was not rot: it read `fm_radio.py` and `fmdsp.py`
          *while a mutation run had them rewritten*, and a check that reads the
          files a run is mutating cannot tell a mutation from an edit. The
          runner owns that rule, so it owns the check - `--check-anchors` runs
          under the same lock a real run takes.
        - The duplicated rule was a second thing to be wrong, and the two would
          have disagreed about a tree that was perfectly fine.

        Not hypothetical: `activity`'s anchor went missing during this very
        session, because another session was editing that file at the time.

        **Marked `slow` despite costing under a second, and the reason is the
        defect this test's own first version had.** `--check-anchors` takes the
        tree lock, and a mutation run holds that lock for its whole duration -
        so running this *from inside* a set meant the subprocess was refused and
        the test failed **for every mutation of every set**, adding a spurious
        red that had nothing to do with any of them. It was invisible for six
        sets and surfaced only when the `harness` set reported a witness that
        was constantly, wrongly failing. Contention, not cost, is what puts a
        check in the deliberate group.
        """
        out = _run("--check-anchors")
        assert out.returncode == 0, (
            "a mutation set's anchor has rotted. Fix the set or the code it "
            "aimed at - do not delete the set:\n" + out.stdout + out.stderr)

    @pytest.mark.slow
    def test_every_set_runs_clean(self):
        """Every mutation is still caught. **Minutes, not seconds** - see above.

        Deliberate: `python3 tools/mutate.py --quiet`, or
        `pytest -m slow tests/test_mutation_harness_can_fail.py`.
        """
        out = _run("--quiet", "--keep-going", timeout=3600)
        assert out.returncode == 0, (
            "a checked-in mutation set reports a survivor or an error. Fix the "
            "set or the code it aimed at - do not delete the set:\n" + out.stdout)
        assert "0 survivors" in out.stdout, out.stdout
