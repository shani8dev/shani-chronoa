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

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUNNER = REPO / "tools" / "mutate.py"

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


def _run(*args, cwd=REPO):
    return subprocess.run(
        [sys.executable, str(RUNNER), *args],
        capture_output=True, text=True, cwd=str(cwd), timeout=600,
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
        out = _run("--list")
        assert out.returncode == 0, out.stdout + out.stderr
        # The real repository's sets, checked in beside the code they back.
        for name in ("edit_file", "location", "finite", "stale"):
            assert name in out.stdout, (
                f"{name} is not a checked-in mutation set, so a commit citing it "
                f"cites evidence that is not in the repository:\n{out.stdout}")


class TestNoSetIsLeftBehind:
    """Every checked-in set must still run.

    A mutation set whose anchor rots - because the code it aimed at was refactored
    - asserts on the write and errors out, which is the *right* behaviour and also
    a signal: the set needs updating, not deleting. This test is what says so
    loudly instead of the tool being quietly unused.
    """

    def test_every_set_runs_clean(self):
        out = _run("--quiet")
        assert out.returncode == 0, (
            "a checked-in mutation set reports a survivor or an error. Fix the "
            "set or the code it aimed at - do not delete the set:\n" + out.stdout)
        assert "0 survivors" in out.stdout, out.stdout
