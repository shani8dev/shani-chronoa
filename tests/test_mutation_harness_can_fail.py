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

import ast
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
        # **The mutation now parses, and that is the fix.** It used to replace a
        # docstring line with `"""A fixture.` - an unterminated string literal, so
        # the fixture was a SyntaxError and pytest aborted with "1 error during
        # collection". The run reported SURVIVOR for *that*, and this control
        # asserted "SURVIVOR" appeared - so it passed without ever exercising what
        # it claims to: that a mutation nothing covers is reported as one. A
        # control that cannot fail for its own reason is the same defect as a test
        # that cannot fail.
        #
        # `TOTAL` is the one constant the fixture asserts nothing about - the
        # function it tests does not read it - so changing it breaks nothing
        # observable and *is* an uncovered mutation.
        name = _write_fixture_set(tmp_path, [{
            "file": "usr/lib/fixture_module.py",
            "old": "TOTAL = 3\n",
            "new": "TOTAL = 4\n",
            "label": "a constant no test reads",
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
        _run(name, cwd=tmp_path)
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
        out = _run("--quiet", timeout=3600)
        assert out.returncode == 0, (
            "a checked-in mutation set reports a survivor or an error. Fix the "
            "set or the code it aimed at - do not delete the set:\n" + out.stdout)
        assert "0 survivors" in out.stdout, out.stdout


class TestEveryMutationEitherHasAWitnessOrSaysWhyItCannotHaveOne:
    """A mutation that names no test must say it is an equivalent mutant.

    **Found by running the full inventory** (2026-12), which reported three
    survivors. One was `kv_parity`'s witnesses skipping for want of an
    environment variable, and the other two were *documented equivalents* that
    explained themselves in a **comment** where `tools/mutate.py` has supported an
    `equivalent` key with a `why` all along - ten sets use it, `history.py`
    among them.

    So the runner was right about the tally and wrong about what it meant: a
    SURVIVOR line cannot distinguish a guard nobody wrote a test for from a guard
    that is provably untestable, and only one of those is a finding.

    The prose was not lost - both reasons are already written out in full, which
    is what makes this mechanical - it was simply in a place no tool reads.
    """

    @staticmethod
    def _mutations():
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        for path in sorted((root / "tools" / "mutations").glob("*.py")):
            if path.name == "harness.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Dict) and node.keys):
                    continue
                entry = {}
                for key, value in zip(node.keys, node.values):
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        entry[key.value] = value
                if "label" in entry and "file" in entry:
                    yield path.name, entry

    @staticmethod
    def _witnesses(entry):
        expect = entry.get("expect")
        if not isinstance(expect, ast.List):
            return []
        return [e.value for e in expect.elts if isinstance(e, ast.Constant)]

    def test_no_mutation_is_neither_witnessed_nor_flagged_equivalent(self):
        unwitnessed = [
            f"{name}: {entry['label'].value}"
            for name, entry in self._mutations()
            if not self._witnesses(entry) and not entry.get("equivalent")
        ]
        assert not unwitnessed, (
            f"{len(unwitnessed)} mutation(s) name no test and are not flagged "
            f"equivalent, so the inventory reports them as SURVIVORS - "
            f"indistinguishable from a guard with no witness at all:\n  "
            + "\n  ".join(unwitnessed)
            + "\n\nEither name a test that fails without it, or set "
            '"equivalent": True with a "why" - the runner has supported that key '
            "since it learned to tell the two apart, and a reason written in a "
            "comment is one no tool reads."
        )

    def test_an_equivalent_mutant_states_its_reason(self):
        """A flag with no reason is the claim this file exists to distrust.

        `tools/mutate.py` prints `EQUIVALENT [set] label` and then the `why`. An
        empty one leaves a reader with an assertion and no argument, which is the
        shape of a promise nobody can check.
        """
        bare = [
            f"{name}: {entry['label'].value}"
            for name, entry in self._mutations()
            if entry.get("equivalent")
            and not (isinstance(entry.get("why"), ast.Constant)
                     and entry["why"].value.strip())
        ]
        assert not bare, (
            f"{len(bare)} equivalent mutant(s) carry no reason:\n  "
            + "\n  ".join(bare)
        )

    def test_the_check_can_be_made_to_fail(self, tmp_path):
        """The control. Without it this is a gate nobody has ever seen refuse."""
        import textwrap
        probe = tmp_path / "mutations"
        probe.mkdir()
        (probe / "probe.py").write_text(textwrap.dedent('''
            MUTATIONS = [
                {
                    "file": "x.py",
                    "label": "a mutation nobody witnessed and nobody explained",
                    "expect": [],
                },
            ]
        ''').strip(), encoding="utf-8")
        offending = []
        for path in sorted(probe.glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not (isinstance(node, ast.Dict) and node.keys):
                    continue
                entry = {}
                for key, value in zip(node.keys, node.values):
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        entry[key.value] = value
                if "label" in entry and "file" in entry:
                    expect = entry.get("expect")
                    names = ([e.value for e in expect.elts
                              if isinstance(e, ast.Constant)]
                             if isinstance(expect, ast.List) else [])
                    if not names and not entry.get("equivalent"):
                        offending.append(entry["label"].value)
        assert offending, (
            "an unwitnessed, unflagged mutation was NOT reported, so this gate "
            "would pass over exactly the defect it exists to catch"
        )


#: One failing test and one skipped test. The pair is the whole point of the class
#: below: a harness that reports the skip and loses the failure is worse than one
#: that reports neither, because the tally still looks like a measurement.
MIXED_TEST = '''from __future__ import annotations

import pytest


def test_a_real_failure():
    assert 1 == 2


def test_a_skipped_witness():
    """The shape every real-GGUF witness in this repo takes without its model."""
    pytest.skip("CHRONOA_TEST_MODEL is not set")
'''


class TestTheTallyCannotBeQuietlyWrong:
    """Two ways this harness produced a number that read as a measurement.

    **One of them was mine, made while fixing the other.** Reporting the first full
    inventory (2026-12), `kv_parity`'s one survivor had both named witnesses
    *skipped*, so a real-GGUF condition was being reported as an uncovered guard.
    The fix was to report the skip count - and the way I took it, adding `-rs` to
    the pytest call, **silently deleted every `FAILED` line**: `-rf -rs` together
    emit the skip summary and no failures, so `hit` was empty for every mutation in
    every set and the whole harness reported "nothing is caught". A live run read
    `18 mutations, 0 caught, 17 survivors`, which is loud, but only because the
    failure happened to be in the loud direction.

    The skip count now comes from the summary line, which already carries it, so
    there is no flag that can take the failures with it.
    """

    def _runner(self):
        namespace = {"__file__": str(RUNNER)}
        exec(compile(RUNNER.read_text(encoding="utf-8"), str(RUNNER), "exec"),
             namespace)
        return namespace

    def test_a_run_reports_its_failures_and_its_skips_together(self, tmp_path):
        """The regression guard. Both numbers, from the same run."""
        ns = self._runner()
        probe = tmp_path / "test__mixed.py"
        probe.write_text(MIXED_TEST, encoding="utf-8")
        ns["STATE_DIR"] = tmp_path / "build" / "mutate"
        ns["ROOT"] = tmp_path
        failed, summary, skipped = ns["_failures"](str(probe))
        names = {name.split("::")[-1].split(" ")[0] for name in failed}
        assert "test_a_real_failure" in names, (
            f"the failing test was not reported as failing - the summary line says "
            f"{summary!r}, and a harness that cannot see a failure cannot tell a "
            f"caught mutation from an uncovered one")
        assert skipped == 1, (
            f"the skipped test was not counted either - summary was {summary!r}")

    def test_the_pytest_flags_the_runner_passes_emit_failures(self, tmp_path):
        """**The control, and the reason the fix is worded the way it is.**

        This asserts the trap is real rather than folklore, so the comment above it
        can be trusted later: it runs pytest both ways on the same file and
        requires the `-rs` form to lose the `FAILED` lines. If a future pytest
        honours both flags, this test fails and the guard can be simplified.
        """
        probe = tmp_path / "test__mixed.py"
        probe.write_text(MIXED_TEST, encoding="utf-8")
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")

        def run(*flags):
            return subprocess.run(
                [sys.executable, "-m", "pytest", str(probe), "-q",
                 "-p", "no:cacheprovider", "--tb=no", *flags],
                capture_output=True, text=True, env=env, cwd=str(tmp_path))

        plain, with_skips = run("-rf"), run("-rf", "-rs")
        assert "FAILED" in plain.stdout, plain.stdout
        assert "FAILED" not in with_skips.stdout, (
            "pytest now honours -rf together with -rs, so the note in "
            "tools/mutate.py warning that -rs deletes the FAILED lines is stale "
            "and the skip count can move back to the short summary")

    def test_a_survivor_says_when_its_witnesses_skipped(self, tmp_path):
        """A witness that skips cannot fail, and the report has to say which kind
        of survivor this is."""
        (tmp_path / "tools" / "mutations").mkdir(parents=True)
        (tmp_path / "tests").mkdir(parents=True)
        (tmp_path / "usr" / "lib").mkdir(parents=True)
        (tmp_path / "usr" / "lib" / "fixture_module.py").write_text(
            FIXTURE, encoding="utf-8")
        (tmp_path / "tests" / "test__mutation_selfcheck.py").write_text(
            MIXED_TEST, encoding="utf-8")
        name = _write_fixture_set(tmp_path, [{
            "file": "usr/lib/fixture_module.py",
            "old": "TOTAL = 3\n",
            "new": "TOTAL = 4\n",
            "label": "a constant only a skipped test could have covered",
            "expect": ["test_a_skipped_witness"],
        }])
        out = _run(name, cwd=tmp_path)
        assert "SURVIVOR" in out.stdout, out.stdout + out.stderr
        assert "skipped" in out.stdout and "unmeasurable" in out.stdout, (
            "the survivor does not mention that a test skipped, so a condition "
            "that was simply absent from this machine reads exactly like a guard "
            "nobody wrote a test for:\n" + out.stdout)
        assert "1 skipped" in out.stdout, out.stdout

    def test_a_survivor_in_a_clean_run_is_not_annotated(self, tmp_path):
        """**The control for the test above.** The annotation must come from the
        run, not from the wording being unconditional - otherwise it says nothing
        about any particular survivor."""
        (tmp_path / "tools" / "mutations").mkdir(parents=True)
        (tmp_path / "tests").mkdir(parents=True)
        (tmp_path / "usr" / "lib").mkdir(parents=True)
        (tmp_path / "usr" / "lib" / "fixture_module.py").write_text(
            FIXTURE, encoding="utf-8")
        (tmp_path / "tests" / "test__mutation_selfcheck.py").write_text(
            "def test_nothing_skips():\n    assert True\n", encoding="utf-8")
        name = _write_fixture_set(tmp_path, [{
            "file": "usr/lib/fixture_module.py",
            "old": "TOTAL = 3\n",
            "new": "TOTAL = 4\n",
            "label": "a constant no test reads",
            "expect": ["test_a_test_that_does_not_exist"],
        }])
        out = _run(name, cwd=tmp_path)
        assert "SURVIVOR" in out.stdout, out.stdout + out.stderr
        assert "unmeasurable on this machine" not in out.stdout, (
            "a survivor was annotated as possibly-unmeasurable in a run where "
            "nothing skipped at all:\n" + out.stdout)

    def _tree(self, tmp_path, test_source=None):
        (tmp_path / "tools" / "mutations").mkdir(parents=True)
        (tmp_path / "tests").mkdir(parents=True)
        (tmp_path / "usr" / "lib").mkdir(parents=True)
        (tmp_path / "usr" / "lib" / "fixture_module.py").write_text(
            FIXTURE, encoding="utf-8")
        (tmp_path / "tests" / "test__mutation_selfcheck.py").write_text(
            test_source or FIXTURE_TEST, encoding="utf-8")

    def _named_set(self, tmp_path, name, mutations):
        path = tmp_path / "tools" / "mutations" / f"{name}.py"
        body = ['TEST_FILE = "tests/test__mutation_selfcheck.py"', "",
                "MUTATIONS = ["]
        body += [f"    {entry!r}," for entry in mutations]
        path.write_text("\n".join(body) + "]\n", encoding="utf-8")

    # An anchor that no longer exists. `_apply` refuses it rather than editing
    # blind, the set raises, and that is the case the default has to survive: it is
    # how a mutation set rots when its module is edited underneath it.
    _ROTATED = {
        "file": "usr/lib/fixture_module.py",
        "old": "    return sum(items) + 99\n",
        "new": "    return 0\n",
        "label": "an anchor that no longer exists",
        "expect": ["test_whatever"],
    }
    _FINE = {
        "file": "usr/lib/fixture_module.py",
        "old": "    return sum(items)\n",
        "new": "    return sum(items) + 1\n",
        "label": "the fixture's total is off by one",
        "expect": ["test_the_total_is_what_the_docstring_says"],
    }

    def test_a_set_that_raises_does_not_hide_the_others(self, tmp_path):
        """**Measured on the real inventory, 2026-12.** `sense_budget` hit a
        mutation that could not be measured and the run stopped - 9 of 43 sets
        never executed, and the tally read `273 mutations, 261 caught`, which is a
        finished-looking number for a third of a run. A set whose anchor rots is a
        result to fix; it is not a reason to throw away the other answers.
        """
        self._tree(tmp_path)
        self._named_set(tmp_path, "aaa_rots", [self._ROTATED])
        self._named_set(tmp_path, "zzz_runs", [self._FINE])
        out = _run("aaa_rots", "zzz_runs", cwd=tmp_path)
        assert "2 of 2 sets" in out.stdout, (
            "the run stopped at the first set that raised, so the other one was "
            "never measured:\n" + out.stdout + out.stderr)
        assert "ERROR [aaa_rots]" in out.stdout, out.stdout + out.stderr
        assert "caught by test_the_total" in out.stdout, (
            "the set after the failing one did not run:\n" + out.stdout)

    def test_a_partial_tally_says_it_is_partial(self, tmp_path):
        """The other half. Stopping early is defensible when the tree is what is
        being diagnosed; printing a clean number afterwards is not."""
        self._tree(tmp_path)
        self._named_set(tmp_path, "aaa_rots", [self._ROTATED])
        self._named_set(tmp_path, "zzz_runs", [self._FINE])
        out = _run("--stop-on-first-error", "aaa_rots", "zzz_runs",
                   cwd=tmp_path)
        assert "INCOMPLETE" in out.stdout, (
            "a run that stopped early printed a tally that reads exactly like a "
            "finished one, so a partial inventory can be quoted as the full one:\n"
            + out.stdout)
        assert "1 of 2 sets" in out.stdout, out.stdout

    def test_a_finished_tally_carries_no_incomplete_warning(self, tmp_path):
        """**The control.** A warning that fires on every run warns about nothing."""
        self._tree(tmp_path)
        self._named_set(tmp_path, "aaa_rots", [self._ROTATED])
        self._named_set(tmp_path, "zzz_runs", [self._FINE])
        out = _run("aaa_rots", "zzz_runs", cwd=tmp_path)
        assert "INCOMPLETE" not in out.stdout, (
            "a run that reached both sets still claimed to be partial:\n"
            + out.stdout)
        assert "2 of 2 sets" in out.stdout, out.stdout

    def test_the_flag_is_the_only_way_to_stop_early(self, tmp_path):
        """A default nobody can discover is a default nobody can rely on either
        way, so the opt-out has to exist *and* the default has to be the safe one."""
        self._tree(tmp_path)
        self._named_set(tmp_path, "aaa_rots", [self._ROTATED])
        self._named_set(tmp_path, "zzz_runs", [self._FINE])
        stopped = _run("--stop-on-first-error", "aaa_rots", "zzz_runs",
                       cwd=tmp_path)
        default = _run("aaa_rots", "zzz_runs", cwd=tmp_path)
        assert "INCOMPLETE" in stopped.stdout
        assert "INCOMPLETE" not in default.stdout
        assert ("2 of 2" in default.stdout) != ("2 of 2" in stopped.stdout), (
            "--stop-on-first-error and the default reached the same number of "
            "sets, so the flag does not do what its help says")

    #: A replacement that leaves the file unterminated, so pytest aborts with
    #: "1 error during collection" and the runner cannot measure the mutation.
    #: This is the real historical case, not a contrived one: a mutation set whose
    #: replacement drops a quote produced exactly that.
    _UNPARSEABLE = {
        "file": "usr/lib/fixture_module.py",
        "old": '    """A sum that is checked against a known total."""\n',
        "new": '    """A sum that is checked against a known total.\n',
        "label": "the mutation leaves the file unterminated",
        "expect": ["test_the_total_is_what_the_docstring_says"],
    }

    def test_an_unmeasurable_mutation_does_not_cost_the_rest_of_its_set(self,
                                                                       tmp_path):
        """The defect this closes, one level below the one above.

        `sense_budget`'s "the worker catches Exception instead of BaseException"
        produced no summary at all. `MutationDidNotParse` propagated out of
        `run_set`, so that mutation **and every mutation after it in the same
        set** were never measured - while `reached` still counted the set, which
        would have printed `43 of 43 sets` over a mutation count that quietly
        excluded them. One unmeasurable mutation is a result about that mutation.
        """
        self._tree(tmp_path)
        self._named_set(tmp_path, "aaa_partial", [self._UNPARSEABLE, self._FINE])
        out = _run("aaa_partial", cwd=tmp_path)
        assert "UNMEASURABLE [aaa_partial]" in out.stdout, (
            "the mutation pytest could not measure was not reported as such:\n"
            + out.stdout + out.stderr)
        assert "caught by test_the_total" in out.stdout, (
            "the mutation AFTER the unmeasurable one never ran, so one bad "
            "measurement cost the whole set:\n" + out.stdout)
        assert "2 mutations," in out.stdout, out.stdout

    def test_the_tally_columns_add_up_to_the_mutation_count(self, tmp_path):
        """**The invariant that makes a partial inventory visible by arithmetic.**

        A count nobody can add up is a count nobody can audit, and the four
        verdicts a mutation can get are exhaustive: a named witness failed, none
        did, it is a documented equivalent, or pytest could not measure it.
        """
        self._tree(tmp_path)
        self._named_set(tmp_path, "aaa_partial", [self._UNPARSEABLE, self._FINE])
        out = _run("aaa_partial", cwd=tmp_path)
        tally = [l for l in out.stdout.splitlines() if " mutations," in l]
        assert tally, out.stdout
        # "N mutations, C caught, S survivors, E equivalent, U unmeasurable"
        words = tally[-1].split(", ")
        count = int(words[0].split()[0])
        columns = [int(w.split()[0]) for w in words[1:5]]
        assert sum(columns) == count, (
            f"{count} mutations but caught+survivors+equivalent+unmeasurable "
            f"= {sum(columns)}, so at least one mutation has no verdict recorded "
            f"and the tally is not auditable:\n{tally[-1]}")

    def test_an_unmeasurable_mutation_makes_the_tally_incomplete(self, tmp_path):
        """The set *was* reached - every mutation was attempted - and the run is
        still not a complete inventory. Those are different sentences and the
        tally has to be able to say both."""
        self._tree(tmp_path)
        self._named_set(tmp_path, "aaa_partial", [self._UNPARSEABLE, self._FINE])
        out = _run("aaa_partial", cwd=tmp_path)
        assert "1 of 1 sets" in out.stdout, out.stdout
        assert "INCOMPLETE" in out.stdout, (
            "a run that lost a mutation to an unmeasurable pytest printed a "
            "complete-looking tally:\n" + out.stdout)
        assert "could not be measured" in out.stdout, out.stdout

    def test_a_finished_tally_is_not_marked_incomplete_by_the_column_alone(self,
                                                                        tmp_path):
        """**The control.** A column that is always printed must not always warn,
        or the warning says nothing about any particular run."""
        self._tree(tmp_path)
        self._named_set(tmp_path, "aaa_fine", [self._FINE])
        out = _run("aaa_fine", cwd=tmp_path)
        assert "0 unmeasurable" in out.stdout, out.stdout
        assert "INCOMPLETE" not in out.stdout, out.stdout

    def test_an_unmeasurable_mutation_is_a_failure_exit(self, tmp_path):
        """A run that could not measure something is not a clean run, and the
        exit code is what a script reads."""
        self._tree(tmp_path)
        self._named_set(tmp_path, "aaa_partial", [self._UNPARSEABLE, self._FINE])
        out = _run("aaa_partial", cwd=tmp_path)
        assert out.returncode != 0, (
            "a run that could not measure one of its mutations exited 0, so a "
            "script would read it as clean:\n" + out.stdout)

    def test_a_rotated_anchor_still_stops_its_set(self, tmp_path):
        """**The over-fix guard.** The `except` catches `MutationDidNotParse`
        only; a rotted anchor raises something else and must still stop the set,
        because that is a statement about the file the remaining mutations are
        aimed at rather than about one run."""
        self._tree(tmp_path)
        self._named_set(tmp_path, "aaa_rots", [self._ROTATED, self._FINE])
        out = _run("aaa_rots", cwd=tmp_path)
        assert "ERROR [aaa_rots]" in out.stdout, out.stdout
        assert "caught by test_the_total" not in out.stdout, (
            "the second mutation ran after the anchor rotted, so the except has "
            "widened past MutationDidNotParse and a stale file is now measured "
            "against code that no longer has the anchor:\n" + out.stdout)
        # Nothing is *missing* here - the set was reached and its outcome is in
        # the ERROR line - so the tally is not INCOMPLETE. That is the difference
        # between "could not be measured" and "was never attempted", and the
        # unmeasurable test above is the other side of it.
        assert "INCOMPLETE" not in out.stdout, (
            "a set that was reached and reported an ERROR is not an incomplete "
            "inventory:\n" + out.stdout)


class TestTheCheapHalfCannotClaimWhatItDidNotCheck:
    """`--check-anchors` is the half that runs every time, so it is the half whose
    output is quoted most.

    It prints **"43 set(s), 359 anchors, all still match the code"**, and two
    things could make that a complete-looking sentence about an incomplete check:

    - **A set with nothing in it.** `MUTATIONS = []` - or no `MUTATIONS` key at
      all - contributed zero anchors and then still appeared in the set count, so
      the line read as an inventory of 43 things. The *full* run says "N has no
      mutations"; the cheap half said nothing. An absence presented as a pass.
    - **`zip` truncating to the shorter of two lists.** A pair-anchor written
      with two `old` strings and one `new` checks one anchor and applies one
      edit, and the anchor count simply comes out lower - with no error anywhere,
      because `_apply` truncates the same way.

    **Both are latent today**, which is why this is a gate rather than a fix: 43
    sets, 359 mutations declared, 359 anchors checked, none empty, none
    mismatched. That measurement is also the cross-check the old line could not
    make for itself, so the count and the total are printed together now.
    """

    def _bare(self, tmp_path, body):
        """A mutation-set file written verbatim, for shapes `_write_fixture_set`
        cannot express."""
        path = tmp_path / "tools" / "mutations" / "probe.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return "probe"

    def test_a_set_declaring_nothing_is_reported_not_counted_as_checked(self,
                                                                    tmp_path):
        (tmp_path / "tools" / "mutations").mkdir(parents=True)
        (tmp_path / "usr" / "lib").mkdir(parents=True)
        (tmp_path / "usr" / "lib" / "fixture_module.py").write_text(
            FIXTURE, encoding="utf-8")
        self._bare(tmp_path, 'TEST_FILE = "tests/x.py"\nMUTATIONS = []\n')
        out = _run("--check-anchors", "probe", cwd=tmp_path)
        assert out.returncode != 0, (
            "a set with an empty MUTATIONS list passed the anchor check:\n"
            + out.stdout)
        assert "NOT checked" in out.stdout, out.stdout
        assert "MUTATIONS = []" in out.stdout, (
            "the report does not say *why* the set was skipped, so a reader "
            "cannot tell an empty set from a missing key:\n" + out.stdout)

    def test_a_set_with_no_mutations_key_at_all_is_reported_too(self, tmp_path):
        """**The other shape.** Absent and empty are different facts and the
        message distinguishes them, because a set whose `MUTATIONS` line was
        deleted is a different accident from one that was emptied."""
        (tmp_path / "tools" / "mutations").mkdir(parents=True)
        (tmp_path / "usr" / "lib").mkdir(parents=True)
        (tmp_path / "usr" / "lib" / "fixture_module.py").write_text(
            FIXTURE, encoding="utf-8")
        self._bare(tmp_path, 'TEST_FILE = "tests/x.py"\nSOMETHING_ELSE = 1\n')
        out = _run("--check-anchors", "probe", cwd=tmp_path)
        assert out.returncode != 0, out.stdout
        assert "no MUTATIONS" in out.stdout, out.stdout

    def test_a_mismatched_pair_anchor_is_reported_rather_than_truncated(self,
                                                                     tmp_path):
        """Two anchors and one replacement. `zip` drops the second silently."""
        (tmp_path / "tools" / "mutations").mkdir(parents=True)
        (tmp_path / "usr" / "lib").mkdir(parents=True)
        (tmp_path / "usr" / "lib" / "fixture_module.py").write_text(
            FIXTURE, encoding="utf-8")
        self._bare(tmp_path, '''TEST_FILE = "tests/x.py"
MUTATIONS = [
    {"file": "usr/lib/fixture_module.py",
     "old": ["TOTAL = 3\\n", "    return sum(items)\\n"],
     "new": ["TOTAL = 4\\n"],
     "label": "two anchors, one replacement",
     "expect": ["test_nothing"]},
]
''')
        out = _run("--check-anchors", "probe", cwd=tmp_path)
        assert out.returncode != 0, (
            "a pair-anchor with mismatched lengths passed, which means zip() "
            "checked one anchor and reported the set clean:\\n" + out.stdout)
        assert "2 anchor(s) but 1 replacement(s)" in out.stdout, out.stdout

    def test_a_matched_pair_anchor_is_accepted(self, tmp_path):
        """**The control for the test above.** A pair-anchor of *equal* length is
        the supported shape - two loops that are not adjacent and must change
        together - so the new check must not refuse it."""
        (tmp_path / "tools" / "mutations").mkdir(parents=True)
        (tmp_path / "usr" / "lib").mkdir(parents=True)
        (tmp_path / "usr" / "lib" / "fixture_module.py").write_text(
            FIXTURE, encoding="utf-8")
        self._bare(tmp_path, '''TEST_FILE = "tests/x.py"
MUTATIONS = [
    {"file": "usr/lib/fixture_module.py",
     "old": ["TOTAL = 3\\n", "    return sum(items)\\n"],
     "new": ["TOTAL = 4\\n", "    return sum(items) + 1\\n"],
     "label": "two adjacent loops, two anchors",
     "expect": ["test_the_total_is_what_the_docstring_says"]},
]
''')
        out = _run("--check-anchors", "probe", cwd=tmp_path)
        assert out.returncode == 0, out.stdout
        assert "2 anchors of 1 declared mutations" in out.stdout, out.stdout

    def test_a_real_set_still_passes_and_both_numbers_are_printed(self, tmp_path):
        """**The control for the whole class.** Without this, every test above
        would also pass against a check that refuses everything."""
        (tmp_path / "tools" / "mutations").mkdir(parents=True)
        (tmp_path / "usr" / "lib").mkdir(parents=True)
        (tmp_path / "usr" / "lib" / "fixture_module.py").write_text(
            FIXTURE, encoding="utf-8")
        name = _write_fixture_set(tmp_path, [{
            "file": "usr/lib/fixture_module.py",
            "old": "    return sum(items)\n",
            "new": "    return sum(items) + 1\n",
            "label": "the fixture's total is off by one",
            "expect": ["test_the_total_is_what_the_docstring_says"],
        }])
        out = _run("--check-anchors", name, cwd=tmp_path)
        assert out.returncode == 0, out.stdout
        assert "1 anchors of 1 declared mutations" in out.stdout, (
            "the count and the total are not printed together, so a reader "
            "cannot cross-check them:\n" + out.stdout)

    def test_a_rotted_anchor_is_still_reported(self, tmp_path):
        """**And the pre-existing guarantee survives the additions.** These
        checks were added on top of rot detection, not in place of it."""
        (tmp_path / "tools" / "mutations").mkdir(parents=True)
        (tmp_path / "usr" / "lib").mkdir(parents=True)
        (tmp_path / "usr" / "lib" / "fixture_module.py").write_text(
            FIXTURE, encoding="utf-8")
        self._bare(tmp_path, '''TEST_FILE = "tests/x.py"
MUTATIONS = [
    {"file": "usr/lib/fixture_module.py",
     "old": "    return sum(items) + 99\\n",
     "new": "    return 0\\n",
     "label": "an anchor that no longer exists",
     "expect": ["test_nothing"]},
]
''')
        out = _run("--check-anchors", "probe", cwd=tmp_path)
        assert out.returncode != 0, out.stdout
        assert "occurs 0 times" in out.stdout, out.stdout
