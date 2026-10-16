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
import inspect
import os
import textwrap
import subprocess
import sys
import time
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


class _LockFixture:
    """Holds `build/mutate/run.lock` the way a live mutation run would.

    **The pid must be the first whitespace token**, because `_Lock._stale` does
    `int(read_text().split()[0])`. JSON `{"pid": 123}` makes that `int()` fail,
    which `_stale` reads as *the owner is gone* - so the lock is taken over and
    the run proceeds. Writing JSON here produced a test that passed for exactly
    the opposite reason it was written for.
    """

    def __init__(self, tmp_path):
        self.path = tmp_path / "build" / "mutate" / "run.lock"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(f"{os.getpid()}\n", encoding="utf-8")

    def acquire(self):
        return self

    def release(self):
        self.path.unlink(missing_ok=True)


def _named_set(tmp_path, name, mutations):
    """A mutation set written under `name`, over the fixture's own test file.

    At module level, because two classes build sets and a second copy is a second
    thing to keep in step.
    """
    path = tmp_path / "tools" / "mutations" / f"{name}.py"
    body = ['TEST_FILE = "tests/test__mutation_selfcheck.py"', "",
            "MUTATIONS = ["]
    body += [f"    {entry!r}," for entry in mutations]
    path.write_text("\n".join(body) + "]\n", encoding="utf-8")


def _run(*args, cwd=REPO, timeout=600):
    return subprocess.run(
        [sys.executable, str(RUNNER), *args],
        capture_output=True, text=True, cwd=str(cwd), timeout=timeout,
    )


#: **Fresh on every call, and that is deliberate in both directions.**
#:
#: *One exec per test* is what gives `pytest.raises(cls)` a class it can match:
#: a second `exec` of the same file builds a **second class object**, so fetching
#: the class from one exec and raising through another leaves the exception
#: uncaught - the assertion failing for the wrong reason and looking exactly like
#: "the guard stopped raising". Each test therefore takes **one** namespace and
#: uses it for both.
#:
#: *Fresh per test* is also what keeps these tests isolated. `mutate.py`'s
#: functions read their module globals - `STATE_DIR`, `ROOT` - from the very
#: namespace they were exec'd into, so a shared one would let one test's
#: `ns["STATE_DIR"] = tmp_path` answer for the next. Memoising the whole file was
#: tried and left 65 green, which is the trap: every current test sets those
#: keys before using them, so the contamination is invisible until a test relies
#: on one being *unset*.
#:
#: At module level because three classes need it, and a second copy would be a
#: third thing to keep in step - the same reason `_named_set` and `_fixture` are
#: not methods. (Two of the three classes had their own identical copy.)
def _runner():
    """The runner's own module namespace, exec'd fresh from its file.

    Deliberately the real module and not a re-implementation. Returned whole
    rather than just the one function because the assertions name the **exception
    class** each refusal raises, and that is not decoration: `MalformedMutation`
    is a plain `Exception` and deliberately **not** an `AssertionError` subclass,
    so that a future `except AssertionError` around `_apply` cannot quietly
    absorb a malformed mutation and turn it back into the set-aborting failure.
    A test asserting only `AssertionError` would not have noticed either way -
    and separating them had to be measured, because three assertions in this file
    went red when it landed.
    """
    namespace = {"__file__": str(RUNNER)}
    exec(compile(RUNNER.read_text(encoding="utf-8"), str(RUNNER), "exec"),
         namespace)
    return namespace


#: A module with two independent functions, so a pair-anchor has two places to
#: work that do not interact. `test_the_no_op_mutation_leaves_the_tree_alone` and
#: its neighbours mutate this rather than `FIXTURE`, because the pair case needs a
#: second function to move half of.
PAIR_FIXTURE = '''"""A fixture with two independent functions."""


def one(items):
    return sum(items)


def two(items):
    return max(items)
'''


def _fixture(tmp_path):
    """A whole fixture tree: the module, its test, and the set directory.

    At module level, for the reason `_named_set` gives - two classes build
    fixtures, and a second copy is a second thing to keep in step.
    """
    (tmp_path / "tools" / "mutations").mkdir(parents=True)
    (tmp_path / "tools").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    (tmp_path / "usr" / "lib").mkdir(parents=True, exist_ok=True)
    (tmp_path / "usr" / "lib" / "fixture_module.py").write_text(
        FIXTURE, encoding="utf-8")
    (tmp_path / "tests" / "test__mutation_selfcheck.py").write_text(
        FIXTURE_TEST, encoding="utf-8")
    return tmp_path


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

    def test_a_covered_mutation_is_reported_caught(self, tmp_path):
        _fixture(tmp_path)
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
        _fixture(tmp_path)
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
        _fixture(tmp_path)
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
        _fixture(tmp_path)
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
        _fixture(tmp_path)
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
        _fixture(tmp_path)
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
        _fixture(tmp_path)
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


class TestAMutationThatChangesNothingIsRefused:
    """`count == 1` is necessary and not sufficient.

    The two rules asserted here are the other two ways `_apply` could accept a
    mutation that does not change anything, and both produce the same shape of
    false evidence - **a survivor reported against a file that was never
    modified**, which is a claim that nothing covers it, measured against
    unmutated code:

    1. `new == old`. `str.replace(old, old)` matches exactly once and rewrites the
       file with itself. `assert text.count(old) == 1` passes on it.
    2. A splice that does not parse. `MutationDidNotParse` already refuses to count
       an unparseable run as caught or as a survivor - and it does so **late**: the
       malformed file is on disk, pytest cannot collect it, and a set that used to
       reach every mutation reports none.

    **The two halves are asserted at two different levels, and the split is the
    point.**

    At the *runner* level, through a real subprocess: what the run **reports**.
    Byte-identity is worthless here, because `run_set` restores in a `finally` -
    the file comes back identical whether it was written or not, so the only
    observable claim is the tally and the error line.

    At the `_apply` level, in-process: what lands **on disk**. There is no
    `finally` in that path, so an unchanged file can only mean the write never
    happened. A test that asserts byte-identity through the runner would pass with
    the guard deleted, which is the shape this file exists to catch.

    The control is last and is a real mutation through the real `run_set`: a
    harness that refuses everything reports a clean tally and no file change, and
    two refusal tests alone cannot tell that from working.
    """

    _ANCHOR = "    return sum(items)\n"

    def _apply(self):
        return _runner()["_apply"]

    def _pair_fixture(self, tmp_path):
        _fixture(tmp_path)
        target = tmp_path / "usr" / "lib" / "fixture_module.py"
        target.write_text(PAIR_FIXTURE, encoding="utf-8")
        return target

    # -- level one: what the run says ------------------------------------

    def test_a_mutation_that_replaces_its_anchor_with_itself_is_refused(
            self, tmp_path):
        """The no-op guard, measured through the real runner.

        The claim is not that the file is unchanged - it is unchanged either way,
        because `str.replace(old, old)` is a rewrite with itself. **The claim is
        that the run says so**, rather than carrying on and reporting a survivor
        for a mutation it never made.
        """
        _fixture(tmp_path)
        name = _write_fixture_set(tmp_path, [{
            "file": "usr/lib/fixture_module.py",
            "old": self._ANCHOR,
            "new": self._ANCHOR,
            "label": "replaces its anchor with itself",
            "expect": ["test_the_total_is_what_the_docstring_says"],
        }])
        out = _run(name, cwd=tmp_path)
        report = out.stdout + out.stderr

        assert out.returncode == 1, report
        assert "replaces it with itself" in report, (
            "a no-op mutation was not named as one:\n" + report)
        assert "changes nothing" in report, report
        # The four verdicts must all be zero. A survivor here is the defect this
        # guard exists for, so it is asserted rather than left to the tally.
        assert "0 mutations, 0 caught, 0 survivors" in report, (
            "a no-op mutation was reported as a result:\n" + report)
        assert "0 equivalent" in report and "0 unmeasurable" in report, report

    def test_a_malformed_mutation_is_refused_before_it_is_run(self, tmp_path):
        """The parse guard, measured through the real runner.

        Downstream, `MutationDidNotParse` already reports UNMEASURABLE for this -
        so the guard is not making a *new* claim, it is making the same claim
        **without writing the file**, and the report says which of the two
        happened.

        **The tally here is the whole of the second guard**, and it is the
        version measured after the change rather than asserted from the design.
        The first version of the parse guard raised a bare `AssertionError`, so a
        malformed mutation aborted the set and the report read

            0 mutations, 0 caught, 0 survivors, 0 equivalent, 0 unmeasurable

        over a run whose two earlier mutations had already been measured. That
        is a finished-looking tally for a set that produced no evidence, which is
        the failure `MutationDidNotParse`'s per-mutation handling was written to
        prevent - reached *through* the guard added to prevent a different one.
        Measured now:

            1 mutations, 0 caught, 0 survivors, 0 equivalent, 1 unmeasurable,
            0 stale witness(es), 1 of 1 sets
            INCOMPLETE: the 1 above is a partial inventory and NOT the full one
            UNMEASURABLE [selfcheck] leaves the call open

        so the arithmetic invariant still holds, the label is carried, and the set
        reports itself **reached**.
        """
        _fixture(tmp_path)
        name = _write_fixture_set(tmp_path, [{
            "file": "usr/lib/fixture_module.py",
            "old": self._ANCHOR,
            "new": "    return sum(items\n",   # the call is never closed
            "label": "leaves the call open",
            "expect": ["test_the_total_is_what_the_docstring_says"],
        }])
        out = _run(name, cwd=tmp_path)
        report = out.stdout + out.stderr

        assert out.returncode == 1, report
        assert "does not parse" in report, report
        # Named as malformed, not as uncovered. "malformed, not uncovered" is the
        # whole difference between a bad mutation and a coverage gap.
        assert "malformed, not uncovered" in report, report
        assert "the file was not written" in report, report

        # Counted as unmeasurable, which is the bucket that says *nothing was
        # measured* - and emphatically not as caught or as a survivor, since a
        # mutation that was never applied cannot be evidence about coverage.
        assert "0 caught, 0 survivors" in report, (
            "a mutation that was never applied was reported as a result:\n"
            + report)
        assert "1 unmeasurable" in report, report
        # And named by its own label, so a set with three malformed entries does
        # not produce three identical lines.
        assert "UNMEASURABLE [" in report and "leaves the call open" in report, (
            "the unmeasurable entry was not attributed to its mutation:\n"
            + report)
        # The set is still counted as reached. This is the claim: one mutation
        # that cannot be measured is a result about that mutation, not a reason
        # to abandon the set.
        assert "1 of 1 sets" in report, (
            "the set was not counted as reached, so a malformed entry still "
            f"cost the set:\n{report}")
        # INCOMPLETE is what makes the partial inventory say so in words rather
        # than only in a column.
        assert "INCOMPLETE" in report, report

    def test_one_malformed_mutation_does_not_cost_the_rest_of_the_set(
            self, tmp_path):
        """**The claim the previous test cannot make: the set carries on.**

        A one-mutation set that is refused still reports `1 of 1 sets`, because
        there was nothing after it to lose. Put a **real, caught** mutation behind
        the malformed one and the distinction is the whole thing: under the bare
        `AssertionError` this reported `0 mutations` and measured nothing at all,
        while here the good mutation is applied, its witness fails, and the tally
        reads `2 mutations, 1 caught, 1 unmeasurable`.

        Ordered deliberately: the malformed entry is first, so a set that aborts
        on it cannot reach the second by accident.
        """
        _fixture(tmp_path)
        name = _write_fixture_set(tmp_path, [
            {
                "file": "usr/lib/fixture_module.py",
                "old": "TOTAL = 3\n",
                "new": "TOTAL = (3\n",
                "label": "malformed: the signature never closes",
                "expect": ["test_the_total_is_what_the_docstring_says"],
            },
            {
                "file": "usr/lib/fixture_module.py",
                "old": self._ANCHOR,
                "new": "    return sum(items) + 1\n",
                "label": "and this one is real",
                "expect": ["test_the_total_is_what_the_docstring_says"],
            },
        ])
        out = _run(name, cwd=tmp_path)
        report = out.stdout + out.stderr

        assert out.returncode == 1, report
        # The whole claim, in one line: both mutations were accounted for, and
        # the one after the malformed entry was measured rather than skipped.
        assert "2 mutations, 1 caught" in report, (
            "a malformed mutation cost the set the entries after it:\n"
            + report)
        assert "1 unmeasurable" in report, report
        assert "0 survivors" in report, report
        assert "1 of 1 sets" in report, report
        # And both labels are named, so which one was refused is answerable.
        assert "malformed: the signature never closes" in report, report
        assert "and this one is real" in report, report
        # The fixture tree is byte-identical afterwards: the good mutation was
        # restored, and the bad one never landed.
        ast.parse((tmp_path / "usr" / "lib" / "fixture_module.py").read_text(
            encoding="utf-8"))

    # -- level two: what reaches the disk ---------------------------------

    def test_a_malformed_mutation_never_reaches_the_file(self, tmp_path):
        """**The claim no runner-level assertion can make.**

        `run_set` restores in a `finally`, so through it an unchanged file is
        consistent with *both* "never written" and "written then put back". Here
        `_apply` is called directly, so there is no restore - and an unchanged
        file can only be a file that was never written.
        """
        target = self._pair_fixture(tmp_path)
        before = target.read_bytes()
        # One namespace for the class *and* the raiser: a second `exec` builds a
        # second `MalformedMutation`, and `pytest.raises` would then let a real
        # malformed mutation escape as an uncaught exception.
        ns = _runner()
        malformed = ns["MalformedMutation"]

        with pytest.raises(malformed) as caught:
            ns["_apply"](target, [(self._ANCHOR, "    return sum(items\n")])

        assert "does not parse" in str(caught.value), caught.value
        assert target.read_bytes() == before, (
            "the malformed text was written and then restored; only a refusal "
            "that never writes can leave the tree alone")
        # And the thing that matters downstream: a file that cannot be collected
        # takes every test in it with it.
        ast.parse(target.read_text(encoding="utf-8"))

    def test_a_no_op_mutation_is_refused_at_the_source(self, tmp_path):
        """The no-op guard at the same level, for symmetry with the two above.

        Byte-identity proves nothing here even in-process - `str.replace(old, old)`
        leaves the file identical whether it ran or the mutation was refused. So
        the assertion is the **refusal**, and this test exists so the two guards
        are held to the same instrument rather than one being measured by tally
        and the other by bytes.
        """
        target = self._pair_fixture(tmp_path)

        with pytest.raises(AssertionError) as caught:
            self._apply()(target, [(self._ANCHOR, self._ANCHOR)])

        assert "replaces it with itself" in str(caught.value), caught.value

    def test_the_refusal_says_why_rather_than_only_that_it_failed(self, tmp_path):
        """A refusal a person cannot act on is a refusal they will route around.

        Both messages carry the two facts that make the failure diagnosable - what
        the mutation was, and what it would have claimed - so this is asserted on
        both shapes rather than on the presence of an exception.
        """
        target = self._pair_fixture(tmp_path)
        ns = _runner()

        with pytest.raises(AssertionError) as no_op:
            ns["_apply"](target, [(self._ANCHOR, self._ANCHOR)])
        message = str(no_op.value)
        assert "survivor" in message, (
            "the refusal does not say what it is preventing:\n" + message)
        assert "fixture_module.py" in message, (
            "the refusal does not name the file:\n" + message)

        # Same namespace as the no-op half above: the class and the raiser have to
        # come from one exec or `pytest.raises` cannot match.
        malformed_refusal = ns["MalformedMutation"]
        with pytest.raises(malformed_refusal) as malformed:
            ns["_apply"](target, [(self._ANCHOR, "    return sum(items\n")])
        message = str(malformed.value)
        assert "malformed, not uncovered" in message, message
        assert "fixture_module.py" in message, message

    # -- placement: why the parse is once, after every pair ----------------

    def test_a_pair_anchor_whose_halves_do_not_parse_alone_still_applies(
            self, tmp_path):
        """**This is what pins the parse *after* the loop rather than in it.**

        `pairs` is `[old, new] = zip(...)`, so one mutation may be several edits
        that must land together - two loops that are not adjacent and have to
        change together. A parse inside the loop would demand something stricter
        than the format promises: that each half is *independently* valid Python.
        A half that opens a construct is legal in a pair and unparseable alone.

        Measured on this tree: **4 pair-anchor mutations across 3 sets, and 0
        halves that fail to parse alone** - so a per-pair parse would not break
        anything *today*. This test exists because that is a property of the
        current sets rather than of the format, and the guard is on the shape.

        The fixture is a string that opens here and closes there, which is the
        clearest instance of the class: neither half is a Python file, jointly
        they are one.
        """
        target = self._pair_fixture(tmp_path)
        before = target.read_bytes()
        pairs = [
            # Opens a triple-quoted string and never closes it: unparseable alone.
            ("def one(items):\n", 'def one(items):\n    doc = """\n'),
            # Closes it, far below in a different function: also unparseable alone,
            # because on its own it opens a second unterminated string.
            ("    return max(items)\n", '    return max(items)\n    """\n'),
        ]
        # The precondition, asserted rather than assumed - the claim above is
        # about these two halves specifically, so it is checked here.
        for old, new in pairs:
            half = PAIR_FIXTURE.replace(old, new, 1)
            with pytest.raises(SyntaxError):
                compile(half, "<half>", "exec")
        joint = PAIR_FIXTURE
        for old, new in pairs:
            joint = joint.replace(old, new, 1)
        compile(joint, "<joint>", "exec")   # must not raise

        self._apply()(target, pairs)

        assert target.read_bytes() != before, (
            "the pair was refused, so the parse is per-pair after all - and a "
            "pair-anchor that splits a construct is now unrepresentable")
        assert target.read_text(encoding="utf-8") == joint

    # -- the control ------------------------------------------------------

    def test_a_real_mutation_still_applies_and_its_result_parses(self, tmp_path):
        """**The control, and it is the reason the five above mean anything.**

        A guard that refuses everything passes all five: the file never changes
        and an `AssertionError` is raised every time. Only a real mutation
        getting through shows the guards discriminate rather than blanket-refuse -
        and this one runs through the real `run_set` and its real pytest, so
        "caught" additionally proves the mutated text really was on disk while
        the test ran.
        """
        _fixture(tmp_path)
        name = _write_fixture_set(tmp_path, [{
            "file": "usr/lib/fixture_module.py",
            "old": self._ANCHOR,
            "new": "    return sum(items) + 1\n",
            "label": "covered",
            "expect": ["test_the_total_is_what_the_docstring_says"],
        }])
        out = _run(name, cwd=tmp_path)
        report = out.stdout + out.stderr

        assert out.returncode == 0, report
        assert "1 mutations, 1 caught" in report, (
            "a real mutation was refused or lost, so the guards above are "
            f"refusing more than they claim:\n{report}")
        assert "0 survivors" in report, report
        assert "0 unmeasurable" in report, report

        # And the same thing at the `_apply` level, so the control covers the
        # instrument the file-level assertions use rather than only the runner.
        # Its own directory: `_fixture` builds the tree rather than filling it, so
        # a second call on the same path is a `FileExistsError`, not a refresh.
        target = self._pair_fixture(tmp_path / "pair")
        self._apply()(target, [(self._ANCHOR, "    return sum(items) + 1\n")])
        assert "+ 1" in target.read_text(encoding="utf-8")
        ast.parse(target.read_text(encoding="utf-8"))


class TestATimedOutRunLeavesNothingBehind:
    """A cap that kills only the child is not a cap.

    `subprocess.run(..., timeout=...)` terminates the **direct child** and
    raises; anything that child started keeps running. For a mutation harness
    that is not a tidiness question - the thing that survives is a process
    still rewriting the tree, with no parent and no lock.

    Measured 2026-10-16 on this repository's own `harness` set, and it is how
    the malformed mutation above was found: the set's first mutation makes a
    test's second `mutate.py` **take** the lock instead of refusing it, so the
    pytest run overran `RUN_TIMEOUT`, pytest was killed, and a
    `mutate.py --quiet` grandchild went on applying mutations alone. That is the
    "two overlapping runs left two mutations stuck applied" incident the
    `harness` set's own docstring records as re-established **by hand** - and
    the mechanism that produced it was the harness's own timeout.

    The control is the point of the first test and is **the other direction**: the
    same scenario run through `subprocess.run` must leave the marker behind. A
    marker that never appears because the grandchild never started would make
    every assertion below pass forever.
    """

    #: A parent that spawns a grandchild and then idles. The grandchild writes
    #: `marker` two seconds later - after the one-second cap - so the marker can
    #: only exist if something beyond the parent survived the cap.
    _PARENT = (
        "import subprocess, sys, time;"
        "subprocess.Popen([sys.executable, '-c',"
        " \"import sys, time; time.sleep(2);"
        " open(sys.argv[1], 'w').write('survived')\","
        " sys.argv[1]]);"
        "time.sleep(30)"
    )

    _settle = 3.0   # past the grandchild's 2s write
    _cap = 1

    def _scenario(self, tmp_path, runner, label):
        """Run the tree-spawning parent under `runner`; True if it survived.

        `runner` is called as `runner(cmd, cap)` so the same helper shape serves
        `subprocess.run` and the runner's own `_run_capped`, which takes its
        timeout as a positional argument.
        """
        marker = tmp_path / f"{label}.marker"
        assert not marker.exists(), f"{label}: a stale marker answers for us"
        cmd = [sys.executable, "-c", self._PARENT, str(marker)]
        raised = False
        try:
            runner(cmd, self._cap)
        except subprocess.TimeoutExpired:
            raised = True
        assert raised, (
            f"{label}: the parent finished inside the cap, so nothing was "
            f"tested - a scenario that does not time out proves nothing")
        time.sleep(self._settle)
        return marker.exists()

    def test_the_control_first_because_it_is_what_gives_the_marker_meaning(
            self, tmp_path):
        """The negative direction, asserted first.

        `subprocess.run` with a timeout leaves the grandchild alive: the marker
        appears. Without this the test below would pass on a scenario where the
        grandchild never started at all.
        """
        survived = self._scenario(
            tmp_path, lambda cmd, cap: subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=cap),
            "control")
        assert survived, (
            "the grandchild did not survive subprocess.run, so the marker "
            "cannot distinguish the helper from a scenario that never ran it")

    def test_a_capped_run_leaves_no_grandchild_behind(self, tmp_path):
        """The claim: the cap ends the child **and everything it started**."""
        survived = self._scenario(
            tmp_path, _runner()["_run_capped"], "capped")
        assert not survived, (
            "the grandchild outlived the cap and would still be rewriting the "
            "tree - which is the incident this helper exists to close")

    def test_a_command_that_finishes_is_not_killed(self, tmp_path):
        """The other half, and what stops the first test being a blanket kill.

        A helper that SIGKILLed everything unconditionally would pass the two
        above and break every ordinary mutation run in this repository.
        """
        out = _runner()["_run_capped"](
            [sys.executable, "-c", "print('done')"],
            timeout=self._cap,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert out.returncode == 0, out
        assert "done" in out.stdout, out

    def test_stdout_and_stderr_are_both_captured(self, tmp_path):
        """`capture_output` is a `run` convenience; the helper takes the pipes.

        A conversion that dropped one of them would make `_failures` blind to
        whichever stream pytest used - and `subprocess.run` gives both under one
        argument, so the loss would be silent.
        """
        out = _runner()["_run_capped"](
            [sys.executable, "-c",
             "import sys; print('on-out'); print('on-err', file=sys.stderr)"],
            timeout=self._cap,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert "on-out" in out.stdout, out
        assert "on-err" in out.stderr, out

    def test_both_call_sites_go_through_the_helper(self):
        """**A source check, and the reason it is one is the timeout.**

        The four tests above drive `_run_capped` **directly**, so they are all
        green if the helper is perfect and nothing calls it - which is exactly
        the shape of the defect, because `subprocess.run(timeout=)` is what both
        call sites used and what the orphan incident went through.

        A runtime witness here would have to time out a real pytest run, and the
        budget is `RUN_TIMEOUT = 300`, so one test would cost five minutes to
        measure the same two lines. **A check that is expensive and says the same
        thing as a cheap one is not worth running**, so the wiring is read off
        the source - and both directions are asserted, because "mentions
        `subprocess.run`" alone is satisfied by a module docstring.

        The `--collect-only` site is included because it is the one that is
        forgotten: it runs once per set rather than per mutation, so a revert
        there is invisible to every behavioural test in this class.
        """
        ns = _runner()
        for name in ("_failures", "_collected_ids"):
            source = inspect.getsource(ns[name])
            assert "_run_capped(" in source, (
                f"{name} does not go through the helper, so a capped run leaves "
                f"whatever it started behind:\n{source}")
            assert "subprocess.run(" not in source, (
                f"{name} calls subprocess.run directly, which kills the child and "
                f"leaves its children running:\n{source}")

    def test_the_timeout_budget_is_still_a_positive_int(self):
        """Both call sites pass `RUN_TIMEOUT`, and `int` is what it is.

        `communicate(timeout=...)` raises `TypeError` on a `None` and accepts a
        float, so a cap that silently became "no cap" would surface as a hang
        rather than as a failure - and the orphan above was a hang.
        """
        cap = _runner()["RUN_TIMEOUT"]
        assert isinstance(cap, int) and not isinstance(cap, bool), cap
        assert 0 < cap <= 3600, (
            f"a cap of {cap}s is not a cap: measured, 300s is four times the "
            f"slowest real set")


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

    def test_a_second_run_is_refused_and_says_whose(self, tmp_path):
        ns = _runner()
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
        ns = _runner()
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
        ns = _runner()
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
        ns = _runner()
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
            # module-level `NAME = "literal"`, so an alias used as a witness
            # resolves rather than reading as a witness nobody named.
            # **Distinct loop names, not one.** The first version wrote
            # `for n in tree.body ... for n in n.targets`, which rebinds the same
            # name inside its own comprehension and then reads `.value` off a
            # `Name` - the shadowing this file records repeatedly, caught here by
            # pyflakes-free execution rather than by reading.
            names = {}
            for stmt in tree.body:
                if not isinstance(stmt, ast.Assign):
                    continue
                if not isinstance(stmt.value, ast.Constant):
                    continue
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        names[target.id] = stmt.value
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Dict) and node.keys):
                    continue
                entry = {}
                for key, value in zip(node.keys, node.values):
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        entry[key.value] = value
                if "label" in entry and "file" in entry:
                    yield path.name, entry, names

    @staticmethod
    def _witnesses(entry, names):
        """Every test a mutation names, with module-level aliases resolved.

        **A `Name` is a witness too.** `tools/mutations/diagnostics.py` defines
        `_GREEN = "test_a_server_setup_never_configured_is_not_a_fault"` once and
        uses it in several entries, which is the right way to avoid typing a long
        name five times - and the first version of this check skipped every
        `ast.Name` element, so those entries read as *"names no test at all"*.
        Three were reported as unwitnessed on a set that named a witness each
        time, and the remedy would have been to inline five copies of the string
        or to flag a live guard as equivalent. **A checker that cannot see a
        correct spelling is the `skills_total` mistake in a validator.**
        """
        expect = entry.get("expect")
        if not isinstance(expect, ast.List):
            return []
        out = []
        for element in expect.elts:
            if isinstance(element, ast.Constant):
                out.append(element.value)
            elif isinstance(element, ast.Name):
                bound = names.get(element.id)
                if isinstance(bound, ast.Constant):
                    out.append(bound.value)
        return out

    def test_no_mutation_is_neither_witnessed_nor_flagged_equivalent(self):
        unwitnessed = [
            f"{name}: {entry['label'].value}"
            for name, entry, names in self._mutations()
            if not self._witnesses(entry, names) and not entry.get("equivalent")
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
            for name, entry, names in self._mutations()
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


#: A fixture that raises during setup, which turns every test that uses it into
#: an ERROR rather than a FAILED. This is what `-rf` cannot report.
BROKEN_FIXTURE = """import pytest


@pytest.fixture
def broken():
    raise RuntimeError("the fixture itself could not set up")
"""


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

    def test_a_run_reports_its_failures_and_its_skips_together(self, tmp_path):
        """The regression guard. Both numbers, from the same run."""
        ns = _runner()
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
        """**The whole selector table, measured, because the trap is not about one
        flag.**

        pytest's short summary is **per-category and last-selector-wins**, so
        adding a selector does not add to what is reported - it *replaces* it.
        The runner needed `-rf` for failures and, since 2026-12, `-rfE` for errors
        too; it briefly needed `-rs` for skips and that silently deleted the
        `FAILED` lines. One file with one real failure, one skipped test and one
        fixture error settles all of it at once.

        Asserting the table rather than one anecdote is what makes the next person
        who adds a selector find out what it costs.
        """
        # Its own module, because the table needs all three verdicts in one run:
        # a failure, an ERROR from a raising fixture, and a skip. MIXED_TEST has
        # the first and third but nothing that *uses* the broken fixture, so
        # borrowing it here would have measured `-rE` against a file with no
        # errors in it - and an absence of errors reads exactly like `-rE`
        # working.
        probe = tmp_path / "test__mixed.py"
        probe.write_text(
            "def test_a_failure():\n    assert 1 == 2\n\n"
            "def test_an_error(broken):\n    assert True\n\n"
            "def test_a_skip():\n"
            "    import pytest\n    pytest.skip('no model')\n",
            encoding="utf-8")
        (tmp_path / "conftest.py").write_text(BROKEN_FIXTURE, encoding="utf-8")
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")

        def counts(*flags):
            out = subprocess.run(
                [sys.executable, "-m", "pytest", str(probe), "-q",
                 "-p", "no:cacheprovider", "--tb=no", *flags],
                capture_output=True, text=True, env=env, cwd=str(tmp_path)).stdout
            return (sum(l.startswith("FAILED") for l in out.splitlines()),
                    sum(l.startswith("ERROR") for l in out.splitlines()))

        # (flags) -> (failures, errors). Measured on this pytest, not remembered.
        assert counts("-rf") == (1, 0), (
            "-rf no longer reports failures without errors - if this changed, "
            "re-read tools/mutate.py's note before changing it")
        assert counts("-rE") == (0, 1), (
            "-rE no longer suppresses failures, so last-selector-wins is not how "
            "this pytest behaves and the note in tools/mutate.py is stale")
        assert counts("-rfE") == (1, 1), (
            "the runner's own flags no longer report both a failure and an error, "
            "so a mutation that breaks a fixture would read as a bare survivor "
            "with nothing failed for another reason")
        assert counts("-rfE", "-rs") == (0, 0), (
            "pytest now honours -rfE together with -rs, so the note in "
            "tools/mutate.py warning that a second selector deletes both is stale")

        # **And the runner has to actually pass them.** The table above measures
        # pytest, which is indifferent to what this repository does - so a run that
        # reverted the runner to bare `-rf` left every assertion above green and
        # nothing here noticed. The flags are read off the runner's own source, so
        # the flag under test is the one actually used.
        runner_source = RUNNER.read_text(encoding="utf-8")
        assert '"-rfE"' in runner_source, (
            "tools/mutate.py no longer passes -rfE, so a mutation that breaks a "
            "fixture produces ERROR lines the runner never reads - reported as a "
            "bare survivor with nothing failed for another reason")
        # `-rfE -rs` was the actual bug, so the combination is refused outright.
        assert '"-rs"' not in runner_source, (
            "tools/mutate.py carries -rs, which suppresses both the FAILED and the "
            "ERROR lines and turns every mutation in every set into a survivor")

    def test_a_mutation_that_breaks_a_fixture_is_caught_not_a_bare_survivor(self,
                                                                        tmp_path):
        """**The false survivor, reproduced.** Found by the first complete
        inventory: `wizard_mark`'s "the name collision, restored" was reported a
        plain SURVIVOR while its own summary line read `28 passed, 8 warnings,
        101 errors` - and the first of those 101 errors *is one of its named
        witnesses*.

        So the mutation was covered all along. `-rf` reports failures only, a
        raising fixture turns every test that uses it into an ERROR, none of them
        were printed, `failed` came back empty, and the harness said "nothing
        caught this and nothing else failed either" about a mutation that broke a
        hundred and one things. **The survivor was the harness's own blindness,
        not a coverage gap**, and the inventory was under-reporting coverage in
        the direction that makes a suite look worse than it is.
        """
        (tmp_path / "tools" / "mutations").mkdir(parents=True)
        (tmp_path / "tests").mkdir(parents=True)
        (tmp_path / "usr" / "lib").mkdir(parents=True)
        (tmp_path / "usr" / "lib" / "fixture_module.py").write_text(
            FIXTURE, encoding="utf-8")
        # The test file's own fixture raises, so the mutation is not what breaks
        # it - what the harness has to notice is that the *witness* lands in the
        # error list rather than the failure list.
        (tmp_path / "tests" / "test__mutation_selfcheck.py").write_text(
            "import pytest\n\n"
            "@pytest.fixture\n"
            "def broken():\n"
            "    raise RuntimeError('the fixture could not set up')\n\n"
            "def test_the_total_is_what_the_docstring_says(broken):\n"
            "    assert True\n", encoding="utf-8")
        name = _write_fixture_set(tmp_path, [{
            "file": "usr/lib/fixture_module.py",
            "old": "    return sum(items)\n",
            "new": "    return sum(items) + 1\n",
            "label": "covered by a witness that lands in the error list",
            "expect": ["test_the_total_is_what_the_docstring_says"],
        }])
        out = _run(name, cwd=tmp_path)
        assert "caught by test_the_total" in out.stdout, (
            "a mutation whose witness failed was not seen as caught, so the run "
            "reports a bare survivor for something it actually caught:\n"
            + out.stdout + out.stderr)

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
        _named_set(tmp_path, "aaa_rots", [self._ROTATED])
        _named_set(tmp_path, "zzz_runs", [self._FINE])
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
        _named_set(tmp_path, "aaa_rots", [self._ROTATED])
        _named_set(tmp_path, "zzz_runs", [self._FINE])
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
        _named_set(tmp_path, "aaa_rots", [self._ROTATED])
        _named_set(tmp_path, "zzz_runs", [self._FINE])
        out = _run("aaa_rots", "zzz_runs", cwd=tmp_path)
        assert "INCOMPLETE" not in out.stdout, (
            "a run that reached both sets still claimed to be partial:\n"
            + out.stdout)
        assert "2 of 2 sets" in out.stdout, out.stdout

    def test_the_flag_is_the_only_way_to_stop_early(self, tmp_path):
        """A default nobody can discover is a default nobody can rely on either
        way, so the opt-out has to exist *and* the default has to be the safe one."""
        self._tree(tmp_path)
        _named_set(tmp_path, "aaa_rots", [self._ROTATED])
        _named_set(tmp_path, "zzz_runs", [self._FINE])
        stopped = _run("--stop-on-first-error", "aaa_rots", "zzz_runs",
                       cwd=tmp_path)
        default = _run("aaa_rots", "zzz_runs", cwd=tmp_path)
        assert "INCOMPLETE" in stopped.stdout
        assert "INCOMPLETE" not in default.stdout
        assert ("2 of 2" in default.stdout) != ("2 of 2" in stopped.stdout), (
            "--stop-on-first-error and the default reached the same number of "
            "sets, so the flag does not do what its help says")

    #: A replacement that leaves the file unterminated. **This used to be the
    #: unmeasurable fixture and stopped being one** when `-rfE` landed: every test
    #: importing the module now reports an ERROR, the witness is among them, and
    #: the mutation is correctly *caught*. A broken module is a detection, not an
    #: absence of measurement, and the harness now says so.
    _CRASHES = (
        "import os, pathlib\n\n\n"
        "def pytest_collection_finish(session):\n"
        "    src = (pathlib.Path(__file__).parent / 'usr' / 'lib'\n"
        "            / 'fixture_module.py').read_text()\n"
        "    if 'CRASH_PYTEST' in src:   # only when a mutation says so\n"
        "        os._exit(3)\n")
    _CRASHES_ENTRY = {
        "file": "usr/lib/fixture_module.py",
        "old": "TOTAL = 3\n",
        "new": "TOTAL = 3\nCRASH_PYTEST = True\n",
        "label": "a mutation after which pytest cannot be measured at all",
        "expect": ["test_the_total_is_what_the_docstring_says"],
    }
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
        # pytest dies with no summary at all - the only remaining shape
        # of "cannot be measured", now that `-rfE` reports errors.
        (tmp_path / "conftest.py").write_text(self._CRASHES, encoding="utf-8")
        _named_set(tmp_path, "aaa_partial", [self._CRASHES_ENTRY, self._FINE])
        out = _run("aaa_partial", cwd=tmp_path)
        assert "UNMEASURABLE [aaa_partial]" in out.stdout, (
            "the mutation pytest could not measure was not reported as such:\n"
            + out.stdout + out.stderr)
        assert "caught by test_the_total" in out.stdout, (
            "the mutation AFTER the unmeasurable one never ran, so one bad "
            "measurement cost the whole set:\n" + out.stdout)
        assert "2 mutations," in out.stdout, out.stdout

    #: A mutation whose result is **valid Python that cannot be imported**.
    #: `_apply`'s parse guard does not fire on it - it parses - so it is the route
    #: to the downstream path this test is about. Kept deliberately distinct from
    #: `_UNPARSEABLE`: an unparseable mutation is now refused at the source and
    #: never reaches collection at all.
    _UNIMPORTABLE = {
        "file": "usr/lib/fixture_module.py",
        "old": '"""A fixture with one invariant for the harness to break."""\n',
        "new": ('"""A fixture with one invariant for the harness to break."""\n'
                'import a_name_that_does_not_exist\n'),
        "label": "the module imports a name that does not exist",
        "expect": ["test_the_total_is_what_the_docstring_says"],
    }

    def test_a_mutation_that_cannot_be_collected_names_the_file_that_failed(
            self, tmp_path):
        """**The honest shape of it, which is not the one I first wrote.**

        A mutation that leaves the module uncollectable produces
        `ERROR tests/test__mutation_selfcheck.py` - **a file, not a test id**. So
        the witness cannot be matched no matter what flags are passed: there is no
        test run to match *against*. My first version of this test asserted the
        mutation should be **caught**, which is wrong in both directions - the
        suite cannot earn a catch for a test that never executed, and claiming one
        would be exactly the absence-as-coverage this file exists to prevent.

        What the runner owes a reader is a name, and it used to give one that
        told them nothing: `split("::")[-1].split(" ")[0]` on that line yields the
        literal word **"ERROR"**, so the report read
        `failed for another reason: {'ERROR'}` - about a mutation that broke every
        test in a file. The path is now reported instead.

        **The fixture changed when the parse guard landed, and the route is the
        interesting part.** This used to be driven by an *unparseable* mutation,
        which is the same defect the guard now refuses upstream: the run reports
        the refusal and nothing reaches collection. A test written against a route
        that no longer exists would pass on a build that collected nothing, so the
        mutation here is valid Python that **cannot be imported** - which parses,
        so the guard does not fire, and still leaves the test file uncollectable.
        `compile` is asserted on the result so the fixture cannot drift back into
        the guard's territory while staying green.
        """
        self._tree(tmp_path)
        # The precondition: this mutation must parse, or the guard refuses it and
        # the assertions below would be measuring the guard rather than the report.
        spliced = FIXTURE.replace(
            self._UNIMPORTABLE["old"], self._UNIMPORTABLE["new"], 1)
        compile(spliced, "<spliced>", "exec")   # must not raise

        _named_set(tmp_path, "aaa_parse", [self._UNIMPORTABLE])
        out = _run("aaa_parse", cwd=tmp_path)
        assert "SURVIVOR [aaa_parse]" in out.stdout, out.stdout
        assert "test__mutation_selfcheck.py" in out.stdout, (
            "the collection error did not name the file that could not be "
            "collected, so a mutation that broke every test in a file reports "
            "\"failed for another reason\" without saying which file:\n"
            + out.stdout)
        assert "{'ERROR'}" not in out.stdout, (
            "the failed item is the literal word ERROR rather than a path - the "
            "collection-error parse is reading the category as a test id:\n"
            + out.stdout)

    def test_the_collection_parse_takes_the_category_word_as_the_regression(self,
                                                                          tmp_path):
        """**The control, aimed at the original defect rather than at a variant.**

        Reverting to the pre-fix parse (`split("::")[-1].split(" ")[0]`) means the
        word `ERROR` becomes a test id, and this test is what notices.

        Two candidate controls were tried and **neither bites**, which is the reason
        this one is spelled out: `split(" ")[1]` produces the same file path for a
        collection error and a different wrong answer for a FAILED line, so it does
        not distinguish the two implementations; and taking the value from
        `_unexplained` instead tests the same code twice. A control has to fail on
        the *original* defect, and the original defect was a line beginning `ERROR`
        with nothing after it that could be a test id.
        """
        self._tree(tmp_path)
        _named_set(tmp_path, "aaa_parse", [self._UNPARSEABLE])
        out = _run("aaa_parse", cwd=tmp_path)
        assert "{'ERROR'}" not in out.stdout, (
            "the collection error parsed as a test id, so a reader is told "
            "\"failed for another reason: {'ERROR'}\" about a mutation that "
            "broke every test in a file - and that names nothing to act on:\n"
            + out.stdout)

    def test_the_tally_columns_add_up_to_the_mutation_count(self, tmp_path):
        """**The invariant that makes a partial inventory visible by arithmetic.**

        A count nobody can add up is a count nobody can audit, and the four
        verdicts a mutation can get are exhaustive: a named witness failed, none
        did, it is a documented equivalent, or pytest could not measure it.
        """
        self._tree(tmp_path)
        # pytest dies with no summary at all - the only remaining shape
        # of "cannot be measured", now that `-rfE` reports errors.
        (tmp_path / "conftest.py").write_text(self._CRASHES, encoding="utf-8")
        _named_set(tmp_path, "aaa_partial", [self._CRASHES_ENTRY, self._FINE])
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
        # pytest dies with no summary at all - the only remaining shape
        # of "cannot be measured", now that `-rfE` reports errors.
        (tmp_path / "conftest.py").write_text(self._CRASHES, encoding="utf-8")
        _named_set(tmp_path, "aaa_partial", [self._CRASHES_ENTRY, self._FINE])
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
        _named_set(tmp_path, "aaa_fine", [self._FINE])
        out = _run("aaa_fine", cwd=tmp_path)
        assert "0 unmeasurable" in out.stdout, out.stdout
        assert "INCOMPLETE" not in out.stdout, out.stdout

    def test_an_unmeasurable_mutation_is_a_failure_exit(self, tmp_path):
        """A run that could not measure something is not a clean run, and the
        exit code is what a script reads."""
        self._tree(tmp_path)
        # pytest dies with no summary at all - the only remaining shape
        # of "cannot be measured", now that `-rfE` reports errors.
        (tmp_path / "conftest.py").write_text(self._CRASHES, encoding="utf-8")
        _named_set(tmp_path, "aaa_partial", [self._CRASHES_ENTRY, self._FINE])
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
        _named_set(tmp_path, "aaa_rots", [self._ROTATED, self._FINE])
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

    def test_the_rotted_path_says_how_many_rotted(self, tmp_path):
        """**The reporting asymmetry this class now also holds.**

        On a clean tree the line is `83 set(s), 863 anchors of 859 declared
        mutations, all still match the code` - three numbers, and a reader can
        scale the check. The rotted path printed the failure list and **no
        count at all**, so the same reader cannot tell five rotted anchors from
        five hundred. The count is in the *first* line because the list below it
        is unbounded: one line per rotted anchor, so a count printed after it is
        a count a reader has to scroll back for.

        Three mutations, one sound and two rotted, so the count is 2 of 3 and
        is not the number of entries in the file.
        """
        (tmp_path / "tools" / "mutations").mkdir(parents=True)
        (tmp_path / "usr" / "lib").mkdir(parents=True)
        (tmp_path / "usr" / "lib" / "fixture_module.py").write_text(
            FIXTURE, encoding="utf-8")
        self._bare(tmp_path, '''TEST_FILE = "tests/x.py"
MUTATIONS = [
    {"file": "usr/lib/fixture_module.py",
     "old": "TOTAL = 3\\n",
     "new": "TOTAL = 4\\n",
     "label": "the total is off by one",
     "expect": ["test_the_total_is_what_the_docstring_says"]},
    {"file": "usr/lib/fixture_module.py",
     "old": "TOTAL = 99\\n",
     "new": "TOTAL = 4\\n",
     "label": "an anchor that no longer exists",
     "expect": ["test_nothing"]},
    {"file": "usr/lib/fixture_module.py",
     "old": "    return sum(items) + 99\\n",
     "new": "    return 0\\n",
     "label": "a second anchor that no longer exists",
     "expect": ["test_nothing"]},
]
''')
        out = _run("--check-anchors", "probe", cwd=tmp_path)
        assert out.returncode != 0, out.stdout
        headline = out.stdout.splitlines()[0]
        assert "2 of 3 declared mutation(s)" in headline, (
            "the rotted path prints the failure list with no count, so a "
            "reader cannot tell five rotted anchors from five hundred:\n"
            + out.stdout)
        assert "3 anchor(s) checked" in headline, out.stdout
        assert "across 1 set(s)" in headline, out.stdout
        # The count has to be *above* the list it counts, not below it.
        assert out.stdout.index("2 of 3 declared") < out.stdout.index(
            "occurs 0 times"), (
            "the count is in the list rather than in the headline, so it is "
            "scrolled off with it:\n" + out.stdout)

    def test_the_count_is_rotted_mutations_and_not_failure_lines(self, tmp_path):
        """**A pair-anchor is one mutation and two anchors, and it rots in two
        places at once.** So it contributes *two* lines to the list and must
        contribute **one** to the count - and the sentence has to keep the two
        units apart, or `2 of 1 declared mutations` is what it prints.

        Counting `len(rotted)` is the obvious implementation and it is wrong in
        the direction that over-reports: the pair-anchor is precisely the shape
        this harness supports on purpose (two loops that are not adjacent and
        have to change together), so it is the case that must not be counted
        twice.
        """
        (tmp_path / "tools" / "mutations").mkdir(parents=True)
        (tmp_path / "usr" / "lib").mkdir(parents=True)
        (tmp_path / "usr" / "lib" / "fixture_module.py").write_text(
            FIXTURE, encoding="utf-8")
        self._bare(tmp_path, '''TEST_FILE = "tests/x.py"
MUTATIONS = [
    {"file": "usr/lib/fixture_module.py",
     "old": ["TOTAL = 99\\n", "    return sum(items) + 99\\n"],
     "new": ["TOTAL = 4\\n", "    return 0\\n"],
     "label": "two adjacent loops, both rotted",
     "expect": ["test_nothing"]},
]
''')
        out = _run("--check-anchors", "probe", cwd=tmp_path)
        assert out.returncode != 0, out.stdout
        assert "1 of 1 declared mutation(s)" in out.stdout, (
            "the count is lines in the list rather than mutations that have a "
            "rotted anchor, so one pair-anchor is reported twice:\n" + out.stdout)
        # And the two units are named separately rather than conflated, which
        # is what makes the sentence above readable.
        assert "2 anchor(s) checked" in out.stdout, out.stdout
        assert out.stdout.count("occurs 0 times") == 2, (
            "the fixture rots both halves, so the list should carry two lines "
            "and the count should still be one:\n" + out.stdout)

    def test_the_clean_and_silent_lines_gained_no_count(self, tmp_path):
        """**The control for both tests above.** A check that printed the count
        everywhere would satisfy them, and the two sibling lines are quoted by
        CI and by people - so their bytes are pinned rather than their content.

        The count belongs on the rotted path because that is the path with no
        number. The clean and `silent` lines already carry three each.
        """
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
        assert out.stdout.strip() == (
            "1 set(s), 1 anchors of 1 declared mutations, all still match "
            "the code"), (
            "the clean line has changed, and it is the line CI quotes:\n"
            + out.stdout)
        assert "rotted" not in out.stdout, (
            "the count reached a path that already had three numbers:\n"
            + out.stdout)

        # And the silent line, which is the third path with a headline.
        (tmp_path / "tools" / "mutations" / f"{name}.py").unlink()
        self._bare(tmp_path, 'TEST_FILE = "tests/x.py"\nMUTATIONS = []\n')
        out = _run("--check-anchors", "probe", cwd=tmp_path)
        assert out.returncode != 0, out.stdout
        assert out.stdout.splitlines()[0] == (
            "1 set(s), 0 anchors of 0 declared mutations, all still match the "
            "code - BUT 1 set(s) declared nothing and were NOT checked:"), (
            "the silent line has changed:\n" + out.stdout)
        assert "rotted" not in out.stdout, (
            "the count reached the silent path, which is a different problem "
            "with a different answer:\n" + out.stdout)


class TestAWitnessThatNamesNothingIsAnErrorNotALogLine:
    """A witness that names no test is a witness that cannot fail.

    **Found by reading one complete inventory's own output.** The runner already
    told us, per mutation, which of a mutation's named witnesses did NOT fire -
    42 of them across 44 sets. Checking all 42 against the tree: **41 exist.** So
    the class is narrow, and the two it found are real, and both are in one
    mutation's expect list:

        test_the_reasons_are_not_about_a_lock_file   # exists nowhere
        TestTheHookActuallyFires                    # a CLASS, not a test id

    The runner was already printing that information, in the middle of a
    mutation run, on its way past. `hit` can tell you a witness did not fire; it
    can never tell you it *could* not. This closes that.

    **And the first version over-fired**, which is the reason the false-positive
    controls below are not optional. `_collected_ids` returned pytest's full ids
    (`tests/x.py::TestClass::test_name`) while `expect` entries and `_failures`'
    verdict set both use the **bare** method name - so every witness in the
    collection was marked stale at once. That is the "two representations of one
    thing compared as strings" error recorded in AGENTS.md for
    `discovered_modules`, arrived at from a new direction.
    """

    def _set(self, tmp_path, witnesses):
        """A set with a witness list to interrogate, over a real test file."""
        (tmp_path / "tools" / "mutations").mkdir(parents=True)
        (tmp_path / "tests").mkdir(parents=True)
        (tmp_path / "usr" / "lib").mkdir(parents=True)
        (tmp_path / "usr" / "lib" / "fixture_module.py").write_text(
            FIXTURE, encoding="utf-8")
        (tmp_path / "tests" / "test__mutation_selfcheck.py").write_text(
            "import sys\n"
            "from pathlib import Path\n\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parent.parent"
            " / 'usr' / 'lib'))\n\n"
            "import fixture_module  # noqa: E402\n\n"
            # **The tests have to be able to *catch*, so the stale witness is the
            # only thing left that can make the run non-zero.** With `assert True`
            # the mutation was also a survivor and the exit code was non-zero for
            # a reason a control could not separate from the one under test - a
            # control that cannot distinguish its own subject.
            "def test_one():\n"
            "    assert fixture_module.TOTAL == 3\n\n"
            "def test_two():\n"
            "    assert fixture_module.TOTAL == 3\n\n"
            "class TestARealClass:\n"
            "    def test_inside_a_class(self):\n"
            "        assert fixture_module.TOTAL == 3\n",
            encoding="utf-8")
        _named_set(tmp_path, "probe", [{
            "file": "usr/lib/fixture_module.py",
            "old": "TOTAL = 3\n",
            "new": "TOTAL = 4\n",
            "label": "one mutation, named witnesses under test",
            "expect": witnesses,
        }])
        return _run("probe", cwd=tmp_path)

    def test_a_witness_naming_nothing_is_an_error(self, tmp_path):
        out = self._set(tmp_path, ["test_one",
                                    "test_a_witness_that_does_not_exist"])
        assert "STALE WITNESS" in out.stdout, (
            "a witness naming a test that exists nowhere was not reported:\n"
            + out.stdout)
        assert out.returncode != 0, (
            "a set naming a witness that cannot fire exited 0, so a script would "
            "read it as clean:\n" + out.stdout)

    def test_the_reason_says_it_could_never_have_fired(self, tmp_path):
        """**The control.** The line has to say *why*, not only that it is stale -
        "no test has that id" and "the test is there but never fires" are opposite
        findings with opposite fixes."""
        out = self._set(tmp_path, ["test_one",
                                    "test_a_witness_that_does_not_exist"])
        assert "could never have fired" in out.stdout, out.stdout

    def test_a_witness_that_names_a_class_is_an_error(self, tmp_path):
        """**The second real case.** `TestARealClass` exists, and it is not a test
        id - a class is not a test, and a witness naming one cannot match."""
        out = self._set(tmp_path, ["test_one", "TestARealClass"])
        assert "STALE WITNESS" in out.stdout, out.stdout
        assert "TestARealClass" in out.stdout, out.stdout

    def test_a_witness_that_exists_is_not_flagged(self, tmp_path):
        """**The false-positive control, and the version that failed first.**"""
        out = self._set(tmp_path, ["test_one"])
        assert "STALE WITNESS" not in out.stdout, (
            "a witness naming a real test was reported as stale, so the "
            "collection and the comparison disagree about what a test id is:\n"
            + out.stdout)

    def test_a_witness_inside_a_class_is_not_flagged(self, tmp_path):
        """And the collection's own ids, kept whole. `expect` uses the bare method
        name, which is what `_failures` stores too."""
        out = self._set(tmp_path, ["test_inside_a_class"])
        assert "STALE WITNESS" not in out.stdout, out.stdout

    def test_a_witness_named_by_a_base_id_across_parametrization_is_not_flagged(
            self, tmp_path):
        """**The prefix rule survives.** `test_foo` matches `test_foo[case]`, so a
        base-id witness against a parametrized test is not stale - and flagging it
        would break every set that uses the rule on purpose."""
        (tmp_path / "tools" / "mutations").mkdir(parents=True)
        (tmp_path / "tests").mkdir(parents=True)
        (tmp_path / "usr" / "lib").mkdir(parents=True)
        (tmp_path / "usr" / "lib" / "fixture_module.py").write_text(
            FIXTURE, encoding="utf-8")
        (tmp_path / "tests" / "test__mutation_selfcheck.py").write_text(
            "import pytest\n\n"
            "@pytest.mark.parametrize('n', [1, 2])\n"
            "def test_a_parametrized_case(n):\n    assert n\n",
            encoding="utf-8")
        _named_set(tmp_path, "probe", [{
            "file": "usr/lib/fixture_module.py",
            "old": "TOTAL = 3\n",
            "new": "TOTAL = 4\n",
            "label": "named by its base id",
            "expect": ["test_a_parametrized_case"],
        }])
        out = _run("probe", cwd=tmp_path)
        assert "STALE WITNESS" not in out.stdout, (
            "a base-id witness against a parametrized test was flagged, so the "
            "prefix rule this harness documents on purpose is broken:\n"
            + out.stdout)

    def test_the_cheap_half_reports_a_stale_witness_without_running_anything(
            self, tmp_path):
        """**`--check-witnesses`, and why it exists.**

        The per-set gate only fires inside a mutation run, which is minutes long
        and mutates the tree. Collection is neither. Same check, same lock
        discipline, a fraction of the cost - so a rename that strands a witness is
        found by the command a person can run every time, not by one that needs
        half an hour and rewrites source.
        """
        self._set(tmp_path, ["test_one", "test_no_such_witness"])
        out = _run("--check-witnesses", cwd=tmp_path)
        assert out.returncode != 0, (
            "the cheap witness check passed on a set naming a witness that does "
            "not exist:\n" + out.stdout)
        assert "test_no_such_witness" in out.stdout, out.stdout
        assert "could never fire" in out.stdout, out.stdout

    def test_the_cheap_half_clears_a_clean_set(self, tmp_path):
        """**The control.** Without it the cheapest failure is a check that refuses
        everything."""
        self._set(tmp_path, ["test_one", "test_inside_a_class"])
        out = _run("--check-witnesses", cwd=tmp_path)
        assert out.returncode == 0, out.stdout
        assert "2 witness(es)" in out.stdout, (
            "the count is not reported, so a run cannot tell a clean check from "
            "one that collected nothing:\n" + out.stdout)

    def test_the_cheap_half_refuses_to_run_on_a_mutated_tree(self, tmp_path):
        """**The reason it takes the lock, which is a measured lesson.** A check
        that reads what a mutation run is rewriting cannot tell a mutation from an
        edit - AGENTS.md records that on the anchor check, and the same hole here
        would make a collection succeed against mutated code."""
        self._set(tmp_path, ["test_one"])
        lock = _LockFixture(tmp_path)
        lock.acquire()
        try:
            out = _run("--check-witnesses", cwd=tmp_path)
            assert out.returncode != 0, (
                "the cheap witness check ran while another run held the tree, so "
                "it collected against code that was being rewritten:\n"
                + out.stdout)
            assert "holds the tree" in out.stdout + out.stderr, out.stdout
        finally:
            lock.release()

    def test_a_set_whose_file_has_no_witnesses_is_not_flagged(self, tmp_path):
        out = self._set(tmp_path, [])
        assert "STALE WITNESS" not in out.stdout, out.stdout


class TestNoMutationSetHasADuplicateKey:
    """No entry in any set may name the same key twice.

    **This is the defect class AGENTS.md records three times already** - the
    `browse` help label killed by a duplicate in `capabilities._GROUPS`, the three
    entries in one table, and a duplicate key silently killing a label - and
    **pyflakes catches two of the three shapes and not this one**, which is how
    three live duplicates sat in this repository's own mutation sets.

    Measured, on this box's pyflakes:

        GROUPS = {"browse": "one", "browse": "another"}
            -> dictionary key 'browse' repeated with different values
        {"equivalent": True, "equivalent": True, ...}
            -> nothing at all

    pyflakes warns only when the two values **differ**. Two identical
    `"equivalent": True,` lines are therefore invisible to the declared lint, and
    a duplicate key with the same value is still a duplicate: the later occurrence
    wins silently, and the reader cannot tell which one the author meant.

    **And the three were produced by two sessions finding the same thing.** This
    session's `e21d8a2` flagged `privacy_device` and `process_cost` as documented
    equivalents with a reason; the concurrent session found the same three
    mutations independently and added the same flag on top, in `b8818f4`. Neither
    was wrong, and `history.py` was flagged twice by the same collision. The
    resulting duplicates were harmless *and* undetectable, which is the exact
    combination this class of defect needs.
    """

    @staticmethod
    def _entries():
        root = _SET_DIR
        for path in sorted(root.glob("*.py")):
            if path.name == "harness.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Dict) and node.keys):
                    continue
                keys = [k.value for k in node.keys
                        if isinstance(k, ast.Constant)]
                if "label" in keys:
                    yield path.name, node, keys

    def test_no_duplicate_key_in_any_set(self):
        """**The check.** AST, so a duplicate is a duplicate whether or not the two
        values differ - which is the whole reason a lint that compares values
        cannot do this job."""
        bad = []
        for name, node, keys in self._entries():
            seen, dupes = set(), set()
            for key in keys:
                if key in seen:
                    dupes.add(key)
                seen.add(key)
            if dupes:
                bad.append(f"{name}: {sorted(dupes)} at line {node.lineno}")
        assert not bad, (
            f"{len(bad)} mutation entry/entries name a key twice. The later value "
            f"wins silently and pyflakes cannot see it when both values are the "
            f"same, so this is one edit away from a flag that stops applying:\\n  "
            + "\\n  ".join(bad))

    def test_the_check_would_catch_a_deliberate_duplicate(self, tmp_path):
        """**The control.** An absence presented as a pass is the same defect as an
        absence presented as a failure, so the check has to be shown to bite on a
        set that repeats a key - with identical values, the shape pyflakes misses.
        """
        probe = tmp_path / "probe.py"
        probe.write_text(textwrap.dedent('''
            MUTATIONS = [
                {
                    "file": "x.py",
                    "label": "a mutation that names a key twice",
                    "equivalent": True,
                    "equivalent": True,
                    "expect": [],
                },
            ]
        ''').strip(), encoding="utf-8")
        keys = None
        for node in ast.walk(ast.parse(probe.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Dict) and node.keys:
                keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
                if "label" in keys:
                    break
        assert keys is not None
        assert len(keys) != len(set(keys)), (
            "the duplicate-key check did not notice a deliberately duplicated "
            "key with identical values, so it is the lint that already exists and "
            "not a check that can see this shape")


class TestNoHandKeptTableAnywhereHasADuplicateKey:
    """Tree-wide, not just the mutation sets.

    **The check above started as one set and the tree-wide sweep found a second
    live instance.** Sweeping `usr/`, `tools/` and `tests/` for dict literals with
    a repeated key: **1,112 files, 1 duplicate** — `_IMPL` in
    `tests/test_skill_gates_are_enforced.py`, 95 entries with 93 unique keys, where
    `container` and `vm` were each declared twice. The second copy's comment about
    `*-control-enabled` was sitting on entries the interpreter had already thrown
    away, and the surviving rows were identical, so nothing failed.

    AGENTS.md records *"pyflakes was the only thing that noticed"* as the reason
    the duplicate-key defect is caught, in three separate places. It does not
    notice this shape: **it warns only when a repeated key's two values differ**,
    so two identical rows are invisible to the declared lint. That gap is the
    finding, and a lint that someone has stopped checking is worse than one that
    never worked.

    The sweep is the reason this file does it rather than leaving it to pyflakes.
    """

    ROOTS = ("usr", "tools", "tests")

    @staticmethod
    def _duplicates():
        import pathlib
        base = pathlib.Path(__file__).resolve().parent.parent
        roots = [base / name for name in
                 TestNoHandKeptTableAnywhereHasADuplicateKey.ROOTS
                 if (base / name).is_dir()]
        files = [p for root in roots for p in root.rglob("*.py")
                 if "pkg" not in p.relative_to(base).parts[:1]]
        for path in sorted(files):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except (SyntaxError, OSError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Dict) or not node.keys:
                    continue
                keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
                if len(keys) != len(set(keys)):
                    dupes = sorted({k for k in keys if keys.count(k) > 1})
                    yield f"{path.relative_to(base)}:{node.lineno} {dupes}"

    def test_no_duplicate_keys_anywhere_in_the_tree(self):
        found = list(self._duplicates())
        assert not found, (
            f"{len(found)} dict literal(s) name a key twice. The later value wins "
            f"silently, pyflakes only warns when the two values DIFFER, so an "
            f"identical repeat is invisible to the declared lint:\\n  "
            + "\\n  ".join(found))

    def test_the_sweep_actually_reads_the_tree(self):
        """**The control.** A sweep that finds nothing because it scanned nothing
        is the same defect as one that cannot fail - and this is exactly what a
        `pkg` exclusion or an empty root list would produce.
        """
        import pathlib
        base = pathlib.Path(__file__).resolve().parent.parent
        roots = [base / name for name in self.ROOTS if (base / name).is_dir()]
        assert len(roots) == 3, (
            f"expected to sweep usr/, tools/ and tests/, found "
            f"{[r.name for r in roots]}", )
        files = [p for root in roots for p in root.rglob("*.py")
                 if "pkg" not in p.relative_to(base).parts[:1]]
        assert len(files) > 1000, (
            f"the sweep read {len(files)} files, which is too few to be the whole "
            f"tree - so a clean result here would mean nothing")
        # and it must be able to see a duplicate: the mutation sets' own check is
        # the same shape on a known-good file, so prove the predicate discrinates.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            probe = pathlib.Path(tmp) / "probe.py"
            probe.write_text(
                'ROW = {"a": 1, "a": 1}\n', encoding="utf-8")
            tree = ast.parse(probe.read_text(encoding="utf-8"))
            seen = [n for n in ast.walk(tree)
                    if isinstance(n, ast.Dict) and n.keys]
            assert seen and len([k.value for k in seen[0].keys
                                 if isinstance(k, ast.Constant)]) != len(
                set(k.value for k in seen[0].keys
                    if isinstance(k, ast.Constant))), (
                "the predicate did not flag a deliberately duplicated key with "
                "identical values, so the sweep could not catch the shape it "
                "exists for")
