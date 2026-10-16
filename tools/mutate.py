#!/usr/bin/env python3
"""Run a mutation set: break the code, watch the tests fail, put it back.

**oh-my-openagent's `script/qa/desktop/linux/expect-sabotage.ts`, generalised.**
That file verifies a *single* deliberately-failing scenario and its own
comment states the rule this whole tool exists to enforce:

> A failing run is not a successful negative control unless the intended opt-in
> check failed **and** both the process and fixture directory were cleaned up.

Chronoa's own `AGENTS.md` carries the same rule from the other side - *"a
negative control that cannot fail is not a control"* - and for two years the
enforcement has been a harness written into `/tmp`, run by hand, and lost. Eight
of them existed in one session on 2026-10-11; two had already been wiped by a
restart when this was written. **Every commit that says "N of N mutations
confirmed to fail" was citing evidence that no longer existed.**

So the harness lives in the repository, beside the code whose claims it backs.

Usage:

    python3 tools/mutate.py                    # every set in tools/mutations/
    python3 tools/mutate.py edit_file          # one set by name
    python3 tools/mutate.py --list

**What a set is.** A Python module in `tools/mutations/` exporting `MUTATIONS`, a
list of dicts:

    {"file":  "usr/lib/shani-chronoa/.../mod.py",   # relative to the repo root
     "old":   "the exact text to replace",           # str, or a list for a pair
     "new":   "what to replace it with",
     "label": "what the mutation breaks",            # shown in the report
     "expect": ["test_name", ...]}                   # tests that must fail

Six rules the runner enforces, each of which cost a green run against unmutated
code before it was written down:

1. **Every anchor must occur exactly once.** A `str.replace` that silently
   matched nothing has twice produced a mutation reported as a survivor. The
   count is asserted before the write, and a *pair* of anchors is applied in
   order with the same check - two loops that are not adjacent and must change
   together, where mutating one alone is a no-op.
2. **The file is restored in a `finally`.** A crash or a timeout leaves the tree
   exactly as it was found.
3. **The verdict is read from the summary line, never the return code.** This box
   segfaults at GTK teardown *after* the summary is written, so a return code of
   -11 is a crash on the way out and not a test result.
4. **A mutation is "caught" only when one of the named tests fails.** A whole-file
   failure that happens for another reason is reported as caught *and* as
   mis-named, so the witness list stays honest.
5. **A set that raises does not stop the run** - it is measured, reported, and
   the rest still run. `--stop-on-first-error` brings the old behaviour back for
   when a broken tree is the thing being diagnosed and nine more sets' worth of
   time would only bury it. *Measured 2026-12:* the first full inventory hit an
   unmeasurable mutation in `sense_budget` and, on the default, stopped - **9 of
   43 sets never ran** and the tally still read `273 mutations, 261 caught`, a
   complete-looking number for a third of a run. So the tally now says how many
   sets it reached out of how many existed, and an incomplete one is not allowed
   to read like a clean one.
6. **Nothing here is packaged.** `tools/` is the repo's maintenance surface, the
   same as `cli_matrix.py` and `task_eval.py`.
7. **One run at a time, and a killed run says so.** Rules 2 and 7 are both about
   leaving the tree alone, and both were measured the hard way - see `_Lock` and
   `_JOURNAL` below for what two overlapping runs did to this repository's own
   source.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

#: The repository the tool is operating on. **The working directory wins**, so the
#: self-test can run this tool inside a temp tree shaped like the repository; the
#: runner's own location is the fallback for an invocation from anywhere else.
ROOT = Path.cwd() if (Path.cwd() / "tools" / "mutations").is_dir() \
    else Path(__file__).resolve().parent.parent
#: Where the mutation sets live, and the order they are looked for.
#:
#: **The working directory comes first, and that is not a convenience.** The
#: harness's own self-test (`tests/test_mutation_harness_can_fail.py`) runs this
#: tool inside a temp directory shaped like the repository, so a runner that
#: resolves its sets from its own location reads the real repo's sets and the
#; self-test silently tests production data instead of its fixture. Falling back
#: to the runner's own directory is what keeps `python3 /path/to/tools/mutate.py`
#: working from anywhere.
MUTATIONS = None


def _mutation_dir() -> Path:
    local = Path.cwd() / "tools" / "mutations"
    if local.is_dir():
        return local
    return Path(__file__).resolve().parent / "mutations"


def sets() -> "dict[str, Path]":
    """Every mutation set, by the name a person types."""
    directory = _mutation_dir()
    return {path.stem: path for path in sorted(directory.glob("*.py"))
            if not path.stem.startswith("_")}


#: `build/` is already gitignored for the XDG redirections, and it is the only
#: writable place here, so the lock and the journal live beside each other.
STATE_DIR = ROOT / "build" / "mutate"

#: Set on the pytest **this** module spawns, so `tests/conftest.py` can tell it
#: apart from a person's run. It has to be a name rather than a literal in two
#: places: the guard refuses to collect while `run.lock` is held, and without the
#: marker it would refuse every mutation run - the runner holds the lock on
#: purpose, so the guard and the runner disagree by construction unless one of them
#: says which process it is. A test holds the two spellings together.
MUTATION_MARKER_NAME = "CHRONOA_MUTATION_RUN"


class _Busy(Exception):
    """Another run holds the tree."""


class MutationDidNotParse(Exception):
    """A mutation produced code that cannot be collected.

    Raised rather than returned as "no summary", so it is reported as an
    **error** and not as a survivor - see `_failures` for the measurement.
    """


class MalformedMutation(Exception):
    """This mutation's own anchor and replacement do not make the file it claims.

    **A statement about one mutation, not about the tree**, which is why it is a
    separate class from `MutationDidNotParse` and is caught beside it per
    mutation. A rotated anchor (`count == 1`) stays a bare `AssertionError` and
    still stops the set, because that *is* a statement about the file every
    remaining mutation is aimed at.

    The distinction was measured: the first version of the parse guard raised
    `AssertionError`, so one mutation whose anchor matched a **prefix** of a
    multi-line statement landed its insertion inside an open parenthesis, and the
    whole set aborted with **`0 mutations, 0 caught`** over a run that had
    already measured two. That is the exact failure the per-mutation
    `MutationDidNotParse` handling exists to prevent, reached through the guard
    that was added to prevent a different one.
    """


class _Lock:
    """One mutation run at a time, and a stale lock says whose.

    **This exists because two runs corrupted this repository's own source, and
    the harness is supposed to be the thing that cannot do that.** A `finally`
    restores a file; two runs each holding a *different* copy of the original
    means the second restore overwrites the first run's mutation with pre-run
    text, or the first restore lands while the second run is mid-mutation and
    silently reverts a live edit. Measured here: a run started before a session
    boundary was still alive when the next one started, and killing both left
    **two mutations stuck applied** to `fm_radio.py` and `fmdsp.py` - the phase
    wrap deleted and the Nyquist zero dropped - which read as a defect somebody
    had just introduced.

    The lock is `O_EXCL` on a file holding the owner's pid, so a crashed run is
    distinguishable from a live one rather than deadlocking the next attempt.
    """

    def __init__(self, timeout: float = 0.0):
        self.timeout = timeout
        self.path = STATE_DIR / "run.lock"
        self.held = False

    def _stale(self) -> bool:
        """True when the lock's owner is gone, so the next run can take it."""
        try:
            pid = int(self.path.read_text(encoding="utf-8").split()[0])
        except (OSError, IndexError, ValueError):
            return True
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        return False

    def __enter__(self) -> "_Lock":
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + self.timeout
        while True:
            try:
                handle = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                                 0o600)
            except FileExistsError:
                if self._stale():
                    # A crashed run left this. Say so rather than deleting it
                    # quietly - the file records who died holding the tree.
                    print(f"  NOTE: taking over a stale lock from pid "
                          f"{self.path.read_text(encoding='utf-8').strip()}",
                          file=sys.stderr)
                    self.path.unlink(missing_ok=True)
                    continue
                if time.time() >= deadline:
                    owner = self.path.read_text(encoding="utf-8").strip()
                    raise _Busy(
                        f"another mutation run holds the tree ({owner}). Wait "
                        f"for it, or stop it by that pid. Two runs at once "
                        f"overwrite each other's restore.")
                time.sleep(1.0)
                continue
            with os.fdopen(handle, "w") as out:
                out.write(f"{os.getpid()}\n")
            self.held = True
            return self

    def __exit__(self, *exc) -> None:
        if self.held:
            self.path.unlink(missing_ok=True)
            self.held = False


#: Which files are currently mutated, and what they were before.
#:
#: A `finally` cannot run on `SIGKILL`, so a killed run leaves the tree mutated
#: and the next run - or the next person - has no way to tell a mutation from an
#: edit. This writes each file's pre-run text *before* the first mutation, and
#: removes the entry in the same `finally` that restores. A journal left behind
#: is therefore a **claim that the tree is not in its committed state**, which is
#: exactly what `restore_pending()` reports.
JOURNAL = STATE_DIR / "mutated.json"


def _journal_write(originals: "dict[Path, str]") -> None:
    import json
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {str(path.relative_to(ROOT)): text
               for path, text in originals.items()}
    JOURNAL.write_text(json.dumps(payload), encoding="utf-8")


def _journal_clear() -> None:
    JOURNAL.unlink(missing_ok=True)


def restore_pending() -> "list[str]":
    """Files a previous run mutated and did not put back."""
    import json
    if not JOURNAL.exists():
        return []
    try:
        return sorted(json.loads(JOURNAL.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return ["(the journal itself is unreadable)"]


def _recover_stale_journal() -> "list[str]":
    """Put back what a killed run left mutated, and say what it did.

    Called under the lock, so nothing else is holding a copy of these files.
    """
    recovered = []
    for name in restore_pending():
        path = ROOT / name
        if not path.is_file():
            recovered.append(f"{name} (no longer on disk; skipped)")
            continue
        import json
        try:
            original = json.loads(JOURNAL.read_text(encoding="utf-8"))[name]
        except (KeyError, OSError, ValueError):
            recovered.append(f"{name} (no entry in the journal; skipped)")
            continue
        path.write_text(original, encoding="utf-8")
        recovered.append(name)
    _journal_clear()
    return recovered

#: How long one test run may take before it is declared a timeout rather than a
#: result. **This was 900 s and that was a bug in the other direction**: a
#: mutation that turns a bounded wait into an unbounded one does not fail, it
#: hangs, and 900 s per mutation turns an eight-mutation set into two hours of
#: nothing. The real cost was paid finding that out — a `harness` mutation
#: replacing the lock's deadline test with `if False:` spun until it was killed.
#: A mutation run is only ever against one test file; the slowest here
#: (`fm_radio`, which synthesises 200 kS/s of signal per mutation) takes about
#: 40 s, so 300 s is a ceiling four times the worst case rather than a budget.
RUN_TIMEOUT = 300

#: The environment a test run needs. `PYTHONDONTWRITEBYTECODE` is here because
#: this repo records two packaging tests failing on bytecode a probe left behind;
#: the XDG_* redirections are here because a suite run without them writes into
#: the developer's real home.
RUN_ENV = {
    "XDG_STATE_HOME": None,
    "XDG_CONFIG_HOME": None,
    "XDG_DATA_HOME": None,
    "PYTHONDONTWRITEBYTECODE": "1",
}


def _test_env() -> dict:
    env = dict(os.environ)
    for key, value in RUN_ENV.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    # A redirected state dir has to exist; mkdir is cheap and the alternative is
    # a test that reads a path that is not there and reports it as a failure.
    scratch = ROOT / "build" / "mutate"
    for name in ("state", "config", "data"):
        (scratch / name).mkdir(parents=True, exist_ok=True)
    env["XDG_STATE_HOME"] = str(scratch / "state")
    env["XDG_CONFIG_HOME"] = str(scratch / "config")
    env["XDG_DATA_HOME"] = str(scratch / "data")
    env.setdefault("HOME", os.environ.get("HOME", ""))
    # **This pytest is the mutation runner's own, and says so.**
    # `tests/conftest.py` refuses to collect while `run.lock` exists, because a
    # mutation run rewrites source in place and an ad-hoc run beside it reads
    # mutated code. Without this marker the runner would be refused by the guard
    # meant to protect against it - the runner *is* a pytest holding the lock on
    # purpose. The value is the runner's pid, so the exemption is a fact about a
    # process rather than a blanket flag anybody could set to get a quiet run.
    env[MUTATION_MARKER_NAME] = str(os.getpid())
    return env


def _run_capped(cmd: list, timeout: int, **kwargs) -> "subprocess.CompletedProcess":
    """`subprocess.run` with a timeout that also ends what the child started.

    **`subprocess.run(timeout=...)` kills the direct child and raises; a
    grandchild it spawned survives.** Measured 2026-10-16 on this very set: its
    first mutation makes a test's second `mutate.py` **take** the lock rather
    than refuse it, so the pytest run exceeded `RUN_TIMEOUT`,
    `subprocess.run` killed pytest, and the `mutate.py --quiet` grandchild
    carried on applying mutations with no parent and no lock. That is the
    "two overlapping runs left two mutations stuck applied" incident
    `harness.py`'s own docstring records as re-established **by hand** - and
    the mechanism that produced it is this file's own timeout.

    `start_new_session=True` puts the child in its own process group, so a
    single `os.killpg` ends the child *and* everything it started. SIGKILL
    rather than SIGTERM because the thing being cleaned up is a process that has
    already had its whole budget: giving it a chance to tidy up is the wrong
    trade for a runner whose contract is that the tree is restored afterwards.

    **`proc.pid` is the group id**, which is exactly what `start_new_session`
    guarantees and the reason the pid is read before the group is killed.
    """
    proc = subprocess.Popen(cmd, start_new_session=True, **kwargs)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        out, err = proc.communicate()
        raise subprocess.TimeoutExpired(
            cmd, timeout, output=out, stderr=err) from None
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def _failures(test_file: str) -> tuple:
    """(the FAILED test ids, the summary line)."""
    out = _run_capped(
        [sys.executable, "-m", "pytest", test_file, "-q",
         "-p", "no:cacheprovider", "--tb=no", "-rfE"],
          # **`-rfE`, not `-rf`.** Measured 2026-12 from the first complete
          # inventory: `wizard_mark`'s "the name collision, restored" was
          # reported a plain SURVIVOR, and its own summary line read
          # **`28 passed, 8 warnings, 101 errors in 48.98s`** - 101 errors,
          # of which the first two are the mutation's OWN named witnesses.
          #
          # **pytest's short summary is per-category and `-rf` reports failures
          # only.** A fixture that raises turns every test using it into an
          # ERROR, `-rf` prints none of them, `failed` comes back empty, and
          # the verdict is a bare "nothing caught this and nothing else
          # failed" for a mutation that broke a hundred and one things. With
          # `-rfE` the same run yields 101 error lines, the witnesses are found,
          # and the mutation is **caught** - so this was the harness
          # under-reporting coverage, and the survivor it invented was not a
          # coverage gap at all.
          #
          # The selector is last-one-wins, which is why the `-rs` note below is
          # not about `-rs`: `-rfE -rs` reports **neither**. Measured across the
          # whole flag space on a file with one failure and one fixture error:
          # `-rf` 1 failed / 0 error, `-rE` 0 / 1, `-rfE` 1 / 1, `-ra` 1 / 1,
          # `-rfE -rs` 0 / 0. `test_the_pytest_flags_the_runner_passes_emit_failures`
          # holds that table, so the next person to add a selector is told what
          # it costs before they add it.
          # **Do not add `-rs` here.** Measured 2026-12, the hard way: passing
          # `-rf -rs` together makes pytest emit the SKIPPED short summary and
          # **no FAILED lines at all** (`-q --tb=no -rf -rs` on a probe with one
          # failing test prints one `SKIPPED` line and zero `FAILED`). Every
          # witness then looks absent, `hit` is empty, and **every mutation in
          # every set is reported as a survivor** - the harness becomes a stamp
          # that says nothing was caught. The skip count is read from the summary
          # line instead, which already carries it and costs no flag.
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=_test_env(),
        cwd=str(ROOT), timeout=RUN_TIMEOUT,
    )
    text = out.stdout + out.stderr
    # **Errors are failures.** A run that comes back `27 passed, 101 errors`
    # and no FAILED lines is a run that could not measure anything, and the
    # first read of it was "survivor" - "the tests did not catch this", when the
    # truth is "the tests could not run". pytest prints `ERROR <path>::<name>` for
    # a fixture that raised during setup, which is exactly what a broken import
    # produces. Same class as `MutationDidNotParse`: a check that could not run
    # is not a check that found nothing.
    failed = set()
    for line in text.splitlines():
        if not line.startswith(("FAILED", "ERROR")):
            continue
        # **A collection error names a FILE, not a test id.** pytest prints
        # `ERROR tests/test_selfcheck.py` for a module that will not import, and
        # `split("::")[-1].split(" ")[0]` on that line yields the literal word
        # `"ERROR"` - so a mutation that broke every test in a file reported
        # `failed for another reason: {'ERROR'}`, which names nothing a reader
        # could act on. Take the path when there is no `::`, so the same line
        # names the file that could not be collected.
        if "::" in line:
            failed.add(line.split("::")[-1].split(" ")[0])
        else:
            failed.add(line.split()[-1])
    # **Anchored at the start, and "error" is in the vocabulary.** Two
    # defects, both found by running: a run with *only* errors prints
    # `1 error in 0.01s`, which contains neither " passed" nor " failed",
    # so the summary list came back empty and the mutation was reported
    # UNMEASURABLE - when its witness was in the error list all along. And
    # matching the words anywhere in a line would instead pick up
    # short-summary entries like `ERROR tests/x.py::t - the error occurred`,
    # so once `-rfE` was added `summary[-1]` depended on which line pytest
    # happened to print last. pytest's summary always begins with a count,
    # so anchor there and name the whole vocabulary.
    summary = [l for l in text.splitlines() if _SUMMARY.match(l)]

    # **A collection error is not a survivor, and it is not "no summary" either.**
    # Measured here: a mutation whose replacement was indented one level too far
    # produced `IndentationError: unexpected indent`, pytest aborted with
    # "1 error during collection" and exit 2 - and this function's parser looked
    # only for " passed" / " failed", neither of which appears in that sentence.
    # So the run reported the mutation as a **survivor**, which is the worst
    # possible answer: it reads as "the tests did not catch this", when the truth
    # is "this mutation did not parse and nothing was measured at all".
    #
    # That distinction is exactly OmO's #8850 in the other direction - a checker
    # that could not run is not a checker that found nothing, and the only way to
    # tell them apart is to notice the case and say so.
    # How many tests did the run SKIP? Read from the summary line, which already
    # reads `1 failed, 1 passed, 1 skipped in 0.00s`. This cannot say *which*
    # witness skipped - the wording at the call site says "may" for exactly that
    # reason - but it is enough to tell a measurement in which some witnesses
    # could not run apart from one where they ran and passed.
    skipped = 0
    if not summary:
        errored = [l for l in text.splitlines()
                   if "error during collection" in l or " error in " in l
                   or "errors" in l and "passed" not in l]
        if errored:
            raise MutationDidNotParse(errored[-1].strip())
        raise MutationDidNotParse(
            "pytest produced no summary - the run neither passed nor failed, so "
            "nothing was measured")
    counts = re.search(r"(\d+) skipped", summary[-1])
    if counts:
        skipped = int(counts.group(1))
    return failed, summary[-1], skipped


def _collected_ids(test_file: str) -> set:
    """Every test id `test_file` actually has.

    **This is what makes `expect` a claim that can be checked.** A witness that
    names no test is a witness that cannot fail, and until now the only signal was
    the runner's "NOT caught by" line - true information, read by nobody on the way
    past. Measured 2026-12 over all 44 sets via one complete inventory: **42
    witnesses were reported NOT-caught and 41 of them exist** - so the class is
    narrow, but the two it found are real and both sit in one mutation's expect
    list: `test_the_reasons_are_not_about_a_lock_file` exists **nowhere**, and
    `TestTheHookActuallyFires` is a **class**, not a test id.

    Collection only, once per set, no test is run - `--collect-only` costs a
    fraction of a second, against a mutation that costs one full test-file run
    each.
    """
    out = _run_capped(
        [sys.executable, "-m", "pytest", test_file, "--collect-only", "-q",
         "-p", "no:cacheprovider"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=_test_env(),
        cwd=str(ROOT), timeout=RUN_TIMEOUT,
    )
    # **The bare method name, because that is what `_failures` stores.** The
    # verdict set holds `line.split("::")[-1]`, so `expect` entries are bare names
    # like `test_a_stale_lock_is_not` while `--collect-only` prints full ids like
    # `tests/x.py::TestClass::test_a_stale_lock_is_not`. Comparing the two across
    # that boundary marks every witness in the collection as stale - which is what
    # the first version did, and it is the same "two representations of one thing
    # compared as strings" error recorded in AGENTS.md for `discovered_modules`.
    return {line.strip().rsplit("::", 1)[-1]
            for line in out.stdout.splitlines()
            if line.strip() and "::" in line
            and not line.startswith(("ERROR", "FAILED", "="))}


def _witness_hit(expect: str, failed: set) -> bool:
    """Does `expect` name one of the tests in `failed`?

    **A prefix matches any parametrization.** `pytest` spells a parametrized id
    out in full - `test_a_short_needle_is_never_retried[x = 1\\n]` - so a witness
    that names the base id would never match, and the first version of this set
    reported a survivor with five tests failing for exactly the right reason.
    Spelling out every parametrization is the alternative and it rots the moment
    somebody adds a case.

    The `[` guard is what stops `test_foo` from matching `test_foo_bar` - a
    witness that matches more than it names is a witness that cannot fail.
    """
    return expect in failed or any(
        name.startswith(expect + "[") for name in failed)


#: pytest's own summary line: `28 passed, 8 warnings, 101 errors in 48.98s`,
#: `1 error in 0.01s`, `4 passed, 5 skipped in 0.44s`. It always
#: begins with a count - which is what makes anchoring on it safe.
_SUMMARY = re.compile(r"^\s*\d+ (passed|failed|error|errors|skipped)")


def _unexplained(failed: set, expect: "list[str]") -> set:
    """The tests that failed when no named witness did.

    **This is the line that keeps a witness list honest.** A mutation whose whole
    suite goes red for an unrelated reason - an import error, an earlier test
    that shares state - is reported as a survivor *and* names what failed, so it
    cannot be read as "the code is fine". The first run of this harness found
    exactly that: a witness named the base id while the suite's ids were
    parametrized, and the mutation was reported a survivor with five tests
    failing for the right reason.
    """
    return {name for name in failed
            if not any(_witness_hit(t, {name}) for t in expect)}


def _apply(path: Path, pairs) -> None:
    text = path.read_text(encoding="utf-8")
    for old, new in pairs:
        assert text.count(old) == 1, (
            "the anchor %r appears %d times in %s, not once - a mutation whose "
            "anchor is not unique is either aimed at the wrong place or does not "
            "exist, and either way it reports a survivor against unmutated code"
            % (old[:60], text.count(old), path.name))
        # **`count == 1` is necessary and not sufficient.** `str.replace(old, old)`
        # matches exactly once and rewrites the file with itself, so the run
        # proceeds to report this mutation as a SURVIVOR - a claim that nothing
        # covers it, measured against a file it never changed. `assert s.count(old)
        # == 1` passes on it; the runner's own docstring records this as *the*
        # str.replace trap in the one costume that gets past the guard written
        # for it. Refused here, before the write, so the tree stays untouched.
        assert new != old, (
            "the mutation anchored on %r replaces it with itself in %s - it "
            "changes nothing, so the run would report a survivor against "
            "unmutated code"
            % (old[:60], path.name))
        text = text.replace(old, new)
    # **Once, after every pair, before the write - and unconditional.**
    #
    # *Why not per pair.* `pairs` is `[old, new] = zip(list, list)`, so one
    # mutation may be **several edits that must land together** for a reason
    # that has nothing to do with parsing - two loops that are not adjacent and
    # have to change together (`mutate.py`'s own docstring). Per-pair parsing
    # would demand something stricter than the shape promises: that each half is
    # independently valid Python. A half that *opens* a construct is legal in a
    # pair and unparseable alone. **Measured on this tree: 4 pair-anchor
    # mutations across 3 sets, and 0 halves that fail to parse alone** - so this
    # is a property of the format, not an observed need, and it is recorded as
    # such rather than dressed up as a finding.
    #
    # *What once-at-the-end buys instead.* It validates the **combination**,
    # which no per-pair parse can: two edits that are each fine and jointly are
    # not is a class the two-sided check below is the only thing that sees.
    #
    # *Why unconditional.* Not gated on a `.py` suffix, because
    # `usr/bin/shani-chronoa` and `usr/bin/shani-chronoa-overlay` are Python with
    # no extension and four mutations target them - and `ast.parse` does not care
    # what the file is called.
    #
    # Downstream, `MutationDidNotParse` already refuses to count an unparseable
    # run as caught *or* as a survivor; it reports UNMEASURABLE. That is correct
    # and it is **late**: by then the malformed file is on disk, pytest cannot
    # even collect it, and a set that used to reach every mutation reports none.
    # Caught at the source instead, the file is never written, so the tree stays
    # byte-identical and the run's other verdicts stay meaningful. The class this
    # exists for is not hypothetical - `goal_edits` and `sink_shape` each carry a
    # comment about an anchor that ended mid-line and left a dangling
    # continuation.
    try:
        ast.parse(text)
    except SyntaxError as exc:
        raise MalformedMutation(
            "the spliced text of %s does not parse (%s at line %s) - this "
            "mutation is malformed, not uncovered; the file was not written"
            % (path.name, exc.msg, exc.lineno)) from exc
    path.write_text(text, encoding="utf-8")


def run_set(name: str, path: Path, verbose: bool = True) -> dict:
    """Run one mutation set. Returns the tally, and leaves the tree as it found it."""
    namespace: dict = {}
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)
    mutations = namespace.get("MUTATIONS") or []
    if not mutations:
        return {"set": name, "count": 0, "caught": 0, "survivors": [], "empty": True}

    test_file = mutations[0].get("tests") or mutations[0]["expect"] and None
    # A set may name its test file explicitly; otherwise it is derived from the
    # module that defines the mutations.
    test_file = namespace.get("TEST_FILE")
    if not test_file:
        raise SystemExit(f"{name}: the set must define TEST_FILE")

    caught = 0
    survivors = []
    equivalent = []
    unmeasurable = []
    stale_witnesses = []
    originals = {}
    try:
        collected = _collected_ids(test_file)
    except Exception as exc:  # noqa: BLE001 - a set whose collection fails is
        # reported, not fatal: the mutations it holds are still measurable
        # because they run the file, a *reader* of it, not a collector of it.
        print(f"  WARNING: could not collect {test_file}: {exc}", file=sys.stderr)
        collected = set()
    # **Once per set, not per mutation.** Every `expect` entry is checked against
    # the ids the test file actually has, so a witness that names nothing is an
    # error rather than a line in the log nobody reads. `hit` can only tell you a
    # witness did not fire; it can never tell you it *could* not.
    for entry in mutations:
        for witness in entry.get("expect") or []:
            if not _witness_hit(witness, collected):
                stale_witnesses.append((witness, entry["label"]))
    for entry in mutations:
        target = ROOT / entry["file"]
        originals.setdefault(target, target.read_text(encoding="utf-8"))
        old, new = entry["old"], entry["new"]
        pairs = list(zip(old, new)) if isinstance(old, list) else [(old, new)]
        try:
            _journal_write(originals)
            _apply(target, pairs)
            if verbose:
                print(f"  MUTATED: {entry['label']}")
            failed, summary, skipped = _failures(test_file)
        except (MutationDidNotParse, MalformedMutation) as unmeasurable_run:
            # **One mutation pytest could not measure is a result about that
            # mutation, not a reason to abandon the rest of the set.** Measured
            # 2026-12: `sense_budget`'s "the worker catches Exception instead of
            # BaseException" produced no summary at all, and the raise cost that
            # set every remaining mutation - while `reached` still counted it, so
            # the tally would read `43 of 43 sets` over a mutation count that
            # silently excluded them. Counting sets is not enough; a partial
            # inventory has to be able to say it is a fraction of a mutation too.
            #
            # **Exactly two things are caught, and the split is the point.**
            # `MutationDidNotParse` is about the run and `MalformedMutation` is
            # about *this mutation's own splice* - both are statements about one
            # mutation, so both land in `unmeasurable` and the set continues.
            # `_apply`'s `count == 1` refusal still propagates and still stops the
            # set, because a rotated anchor is a statement about **the file every
            # remaining mutation is aimed at**, not about one run: the other nine
            # entries in this set would each report a survivor measured against a
            # file none of their anchors is in.
            #
            # A bare `AssertionError` out of `_apply` used to be the third member
            # of this tuple by accident rather than by decision, and it cost a
            # whole set - see `MalformedMutation`.
            unmeasurable.append((entry["label"], unmeasurable_run))
            if verbose:
                print(f"    -> UNMEASURABLE  ({unmeasurable_run})")
            continue
        finally:
            for path2, text in originals.items():
                path2.write_text(text, encoding="utf-8")
            # **Only once the files are back.** Clearing first would leave a
            # window where the tree is restored and the journal still claims
            # otherwise - and a run killed in that window recovers nothing,
            # which is the exact failure this journal exists to catch.
            _journal_clear()

        # **`entry.get("expect")`, not `entry["expect"]`.** The documented
        # contract is "every mutation either names a witness *or* carries
        # `equivalent: True` with a `why`" - so an equivalent mutation is
        # *allowed* to have no `expect` at all. This read it unconditionally,
        # before the `equivalent` branch below, so such a mutation raised
        # `KeyError: 'expect'`, which aborted the set and left every mutation
        # after it unmeasured **while the tally still printed a summary** -
        # the same class the `MutationDidNotParse` handling above exists to
        # prevent, reached from the branch that is supposed to be a safe
        # exit. Measured on `palette_modes` 2026-10-13: five mutations
        # reported, then `0 mutations, 0 caught, 0 survivors` over a run that
        # had measured five. All 24 other equivalents in this tree happen to
        # carry `expect` as well, so nothing else had ever reached it.
        expect = entry.get("expect") or []
        hit = [t for t in expect if _witness_hit(t, failed)]
        missed = [t for t in expect if t not in hit]
        if hit:
            caught += 1
            if verbose:
                extra = (f"  (also: {sorted(_unexplained(failed, expect))})"
                         if _unexplained(failed, expect) else "")
                print(f"    -> caught by {', '.join(hit)}{extra}")
                if missed:
                    print(f"    -> NOT caught by {', '.join(missed)}")
            continue

        # **An equivalent mutant is kept and reported, never counted as coverage
        # and never hidden.** A mutation no test can reach is not evidence that
        # the code is guarded - it is a statement that the guard has no witness.
        # This repo's own practice is to record those with the reason, and a
        # runner that only has "caught" and "survivor" forces the author to
        # choose between lying and deleting, which is how a documented equivalent
        # mutant becomes an undocumented missing one.
        if entry.get("equivalent"):
            equivalent.append((entry["label"], entry.get("why", "")))
            if verbose:
                print(f"    -> equivalent mutant, kept: {entry.get('why', '')}")
            continue

        unexplained = _unexplained(failed, expect)
        measured = summary
        if skipped:
            # **Say the run skipped tests rather than implying it covered
            # them.** "may" because `-rs` names a location and not a test id,
            # so this cannot claim *which* witness skipped - only that a
            # measurement in which some of them could not run is not the same
            # sentence as one where they ran and passed.
            measured = (f"{summary}, {skipped} skipped - a witness that skips "
                        f"cannot fail, so this may be unmeasurable on this "
                        f"machine rather than uncovered")
        survivors.append((entry["label"], measured, unexplained))
        if verbose:
            print(f"    -> SURVIVOR  ({summary})")
            if unexplained:
                print(f"       failed for another reason: {unexplained}")
    return {"set": name, "count": len(mutations), "caught": caught,
            "survivors": survivors, "equivalent": equivalent,
            "unmeasurable": unmeasurable,
            "stale_witnesses": stale_witnesses}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Break the code, watch the tests fail, put it back.")
    parser.add_argument("names", nargs="*",
                        help="mutation set names (default: all of them)")
    parser.add_argument("--list", action="store_true",
                        help="list the mutation sets and exit")
    parser.add_argument("--quiet", action="store_true",
                        help="only the final tally")
    parser.add_argument("--stop-on-first-error", action="store_true",
                        help="abandon the remaining sets when one raises; "
                             "the default runs them all and reports each "
                             "failure, so one bad anchor cannot hide the "
                             "rest")
    parser.add_argument("--wait", type=float, default=0.0, metavar="SECONDS",
                        help="wait this long for another run to finish instead of "
                             "refusing (default: refuse immediately)")
    parser.add_argument("--check-witnesses", action="store_true",
                        help="check every mutation's named witnesses resolve to "
                             "a real test id, without running any mutation")
    parser.add_argument("--check-anchors", action="store_true",
                        help="check every mutation's anchor still matches the code, "
                             "without running a single test. This is the cheap "
                             "half of a run (see rule 2) and the half that can "
                             "be done every time: it reads the files, so it "
                             "takes the same lock a real run does.")
    args = parser.parse_args(argv)

    available = sets()
    if args.list:
        for name, path in available.items():
            namespace = {}
            exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)
            print(f"  {name:<24} {len(namespace.get('MUTATIONS') or [])} mutations, "
                  f"{namespace.get('TEST_FILE', '?')}")
        return 0

    wanted = args.names or list(available)
    unknown = [n for n in wanted if n not in available]
    if unknown:
        print(f"no mutation set named {unknown}; --list shows them", file=sys.stderr)
        return 2

    started = time.time()
    totals = {"count": 0, "caught": 0}
    reached = 0
    all_survivors = []
    all_equivalent = []
    all_unmeasurable = []
    all_stale_witnesses = []
    failures = []
    try:
        lock = _Lock(timeout=args.wait)
        lock.__enter__()
    except _Busy as busy:
        print(str(busy), file=sys.stderr)
        return 2
    try:
        if args.check_witnesses:
            return _check_witnesses(wanted, available)
        if args.check_anchors:
            return _check_anchors(wanted, available)
        # **A killed run's leftovers are put back here, before anything is
        # mutated again** - so a tree that is not in its committed state is
        # repaired by the next run rather than being read as somebody's edit.
        recovered = _recover_stale_journal()
        if recovered:
            print("  RECOVERED a previous run's mutations that were never put "
                  "back (it was killed): " + ", ".join(recovered))
        for name in wanted:
            reached += 1
            if not args.quiet:
                print(f"=== {name} ===")
            try:
                result = run_set(name, available[name], verbose=not args.quiet)
            except Exception as exc:  # noqa: BLE001 - one bad set must lose neither
                failures.append((name, exc))
                print(f"  ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
                if args.stop_on_first_error:
                    # **The tally is still printed.** A set that raises on a
                    # rotated anchor is a result, not an abort: the other sets'
                    # answers are still true, and a run that prints nothing reads
                    # as "nothing ran".
                    print()
                    _report(totals, all_survivors, failures, started,
                            all_equivalent, (reached, len(wanted)),
                            all_unmeasurable, all_stale_witnesses)
                    return 1
                continue
            if result.get("empty"):
                print(f"  {name} has no mutations")
                continue
            totals["count"] += result["count"]
            totals["caught"] += result["caught"]
            all_survivors.extend((name,) + s for s in result["survivors"])
            all_equivalent.extend((name,) + e for e in result.get("equivalent", []))
            all_unmeasurable.extend((name,) + u
                                    for u in result.get("unmeasurable", []))
            all_stale_witnesses.extend((name,) + r
                                       for r in result.get("stale_witnesses", []))
    finally:
        lock.__exit__(None, None, None)

    if not args.quiet:
        print()
    _report(totals, all_survivors, failures, started, all_equivalent,
            (reached, len(wanted)), all_unmeasurable, all_stale_witnesses)
    return 1 if (all_survivors or failures or all_unmeasurable
                 or all_stale_witnesses) else 0


def _check_witnesses(wanted, available) -> int:
    """Every mutation's `expect` list resolves to a real test id.

    **The cheap half of the witness gate.** A witness that cannot fire was a line
    in the middle of a mutation run that nobody read; the per-set check closes it
    and this makes it *runnable every time*, beside `--check-anchors`, because
    collection is not a run. 44 sets collected is a fraction of the anchor check's
    own file reads.

    **It takes the lock for the reason the anchor check does**: this reads source
    a mutation run may be rewriting, and a check that reads what a run is mutating
    cannot tell a mutation from an edit. That was measured here already, on the
    cheap half, and it is why this is not exempt.
    """
    stale = []
    checked = 0
    collected = {}
    for name in wanted:
        namespace = {}
        exec(compile(available[name].read_text(encoding="utf-8"),
                     str(available[name]), "exec"), namespace)
        test_file = namespace.get("TEST_FILE")
        if not test_file:
            continue
        if test_file not in collected:
            try:
                collected[test_file] = _collected_ids(test_file)
            except Exception as exc:  # noqa: BLE001 - reported, not fatal
                collected[test_file] = set()
                print(f"  could not collect {test_file}: {exc}", file=sys.stderr)
        ids = collected[test_file]
        for entry in namespace.get("MUTATIONS") or []:
            for witness in entry.get("expect") or []:
                checked += 1
                if not _witness_hit(witness, ids):
                    stale.append(f"{name}: {witness!r} - named on the mutation "
                                 f"{entry['label']!r}, and no test in "
                                 f"{test_file} has that id")
    if stale:
        print(f"{len(stale)} witness(es) could never fire. The mutation is still "
              f"reported when another witness catches it, so this is a hole in "
              f"the measurement and not a red tally:\n" + "\n".join(stale))
        return 1
    print(f"{checked} witness(es) across {len(wanted)} set(s), all resolve to a "
          f"real test id")
    return 0


def _check_anchors(wanted, available) -> int:
    """Every mutation's anchor still matches the code. No tests are run.

    **This is the cheap half of a run, and it is the half that can happen every
    time.** The expensive half - every mutation still caught - took under two
    minutes with four sets and over ten with eleven, so leaving it in the default
    suite put it past its own timeout: the guarantee went missing and the test
    reported a failure about something else entirely.

    It reuses `_apply`'s own rule rather than restating it. A second copy of
    "occurs exactly once" is a second thing to be wrong, and the two would
    disagree about a tree that is fine.
    """
    rotted = []
    silent = []
    checked = 0
    declared = 0
    for name in wanted:
        namespace = {}
        exec(compile(available[name].read_text(encoding="utf-8"),
                     str(available[name]), "exec"), namespace)
        mutations = namespace.get("MUTATIONS")
        if not mutations:
            # **A set with nothing in it is a set that was not checked, and this
            # half used to report it as one that was.** `MUTATIONS = []` - or no
            # `MUTATIONS` at all - contributes zero anchors and then still appears
            # in the set count, so "43 sets, 359 anchors, all still match the
            # code" reads as a complete inventory of 43 things. The full run says
            # "N has no mutations"; the cheap half said nothing at all.
            silent.append(f"{name} declares "
                          f"{'MUTATIONS = []' if mutations is not None else 'no MUTATIONS'}")
            continue
        for entry in mutations:
            declared += 1
            target = ROOT / entry["file"]
            if not target.is_file():
                rotted.append(f"{name}: {entry['label']} -> "
                              f"{entry['file']} does not exist")
                continue
            text = target.read_text(encoding="utf-8")
            old, new = entry["old"], entry["new"]
            if isinstance(old, list):
                # **`zip` truncates to the shorter, silently.** A pair-anchor
                # written with two `old` and one `new` checks one anchor and
                # applies one edit, and the anchor count simply comes out lower -
                # with no error, and `_apply` truncating the same way. So the
                # mismatch is reported rather than absorbed.
                if len(old) != len(new):
                    rotted.append(
                        f"{name}: {entry['label']} - {len(old)} anchor(s) but "
                        f"{len(new)} replacement(s); zip() would drop the rest "
                        f"and check less than the set declares")
                    continue
                pairs = list(zip(old, new))
            else:
                pairs = [(old, new)]
            for i, (before, _after) in enumerate(pairs, 1):
                checked += 1
                count = text.count(before)
                if count != 1:
                    rotted.append(
                        f"{name}: {entry['label']} - anchor {i} of {len(pairs)} "
                        f"occurs {count} times in {entry['file']}, not once")
    if rotted:
        print("a mutation set's anchor has rotted. Fix the set or the code it "
              "aimed at - do not delete the set:\n" + "\n".join(rotted))
        return 1
    # **Both numbers are printed and neither is asserted against the other.**
    # They are anchors and mutations, and a *pair* of anchors for one mutation -
    # which this harness supports on purpose, for the two loops that are not
    # adjacent and must change together - makes them differ **on correct code**.
    #
    # Measured 2026-12 on this tree: 366 mutations, 368 anchors, the difference
    # being `edit_file.py`'s "a single line is treated as a quote" and
    # `file_locks.py`'s "the device is read as decimal instead of hex", two
    # anchors each. An earlier note here claimed the two numbers were equal and
    # that no set used a pair-anchor; that was a reading error, and it would have
    # made this look like a vacuous gate rather than an actively wrong one.
    # Printing both is what lets a reader notice a set that lost a mutation; a
    # `checked != declared` gate would fire the day a set legitimately grew a
    # pair-anchor, which is the shape this harness exists partly to support.
    #
    # **Count these two numbers by the same method before comparing them.** An
    # independent probe that skipped `tools/mutations/harness.py` - itself a real
    # set of 7 - reported 359 and 361 against this function's 366 and 368, and
    # looked like a disagreement between two measurements until the omission was
    # found. Two methods that count different things do not cross-check anything.
    if silent:
        print(f"{len(wanted)} set(s), {checked} anchors of {declared} declared "
              f"mutations, all still match the code - BUT {len(silent)} set(s) "
              f"declared nothing and were NOT checked:")
        for line in silent:
            print(f"    {line}")
        return 1
    print(f"{len(wanted)} set(s), {checked} anchors of {declared} declared "
          f"mutations, all still match the code")
    return 0


def _report(totals, survivors, failures, started, equivalent=(), sets=None,
             unmeasurable=(), stale_witnesses=()) -> None:
    """The tally.

    **`sets` is `(reached, available)`, and it is not decoration.** A run that
    stopped early printed the same line as one that finished - `273 mutations, 261
    caught` either way - with nothing in the output to say which. That number is
    the thing people quote, so a number that cannot tell a full inventory from a
    third of one is worse than no number at all.

    **`unmeasurable` is the same defect one level down.** A set that reached every
    mutation and lost one to a run pytest could not measure was still counted as
    *reached*, so `43 of 43 sets` would have read complete over a mutation count
    that quietly excluded it. Together the two make the arithmetic checkable:
    **caught + survivors + equivalent + unmeasurable == count**, so a reader who
    adds the columns up knows whether the run measured what it claims to.
    """
    reached, available = sets if sets else (None, None)
    line = (f"{totals['count']} mutations, {totals['caught']} caught, "
            f"{len(survivors)} survivors, {len(equivalent)} equivalent, "
            f"{len(unmeasurable)} unmeasurable, "
            f"{len(stale_witnesses)} stale witness(es)")
    if available:
        line += f", {reached} of {available} sets"
    print(f"{line}  ({time.time() - started:.0f}s)")
    if available and (reached < available or unmeasurable or stale_witnesses):
        # **Say it in the tally, not only on stderr.** The stderr line is the first
        # thing a raising run prints; the tally is the thing that survives into a
        # commit message. A partial run must not be quotable as a clean one.
        print(f"  INCOMPLETE: the {totals['count']} above is a partial "
              f"inventory and NOT the full one - "
              + (f"{available - reached} of {available} sets were never run, so "
                 f"re-run without --stop-on-first-error to get them; "
                 if available - reached else "")
              + (f"{len(unmeasurable)} mutation(s) could not be measured at all "
                 f"and are listed below; " if unmeasurable else "")
              + (f"{len(stale_witnesses)} witness(es) named a test that does not "
                 f"exist, so they could never have fired; "
                 if stale_witnesses else "")
              + "nothing was measured for them either way")
    for name, label, summary, failed in survivors:
        print(f"  SURVIVOR [{name}] {label}")
        print(f"          {summary}")
        if failed:
            print(f"          failed for another reason: {failed}")
    for name, label, why in equivalent:
        print(f"  EQUIVALENT [{name}] {label}")
        if why:
            print(f"          {why}")
    for name, label, exc in unmeasurable:
        print(f"  UNMEASURABLE [{name}] {label}")
        print(f"          {type(exc).__name__}: {exc}")
    for name, witness, label in stale_witnesses:
        print(f"  STALE WITNESS [{name}] {witness}")
        print(f"          named on the mutation {label!r}, and nothing in that "
              f"set's test file has that id - so it could never have fired")
    for name, exc in failures:
        print(f"  ERROR [{name}] {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
