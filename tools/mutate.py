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
5. **`--keep-going` runs every set even when one raises**, so one broken anchor
   does not hide the rest.
6. **Nothing here is packaged.** `tools/` is the repo's maintenance surface, the
   same as `cli_matrix.py` and `task_eval.py`.
7. **One run at a time, and a killed run says so.** Rules 2 and 7 are both about
   leaving the tree alone, and both were measured the hard way - see `_Lock` and
   `_JOURNAL` below for what two overlapping runs did to this repository's own
   source.
"""

from __future__ import annotations

import argparse
import os
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


def _failures(test_file: str) -> tuple:
    """(the FAILED test ids, the summary line)."""
    out = subprocess.run(
        [sys.executable, "-m", "pytest", test_file, "-q",
         "-p", "no:cacheprovider", "--tb=no", "-rf"],
        capture_output=True, text=True, env=_test_env(),
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
    failed = {line.split("::")[-1].split(" ")[0]
              for line in text.splitlines()
              if line.startswith(("FAILED", "ERROR"))}
    summary = [l for l in text.splitlines() if " passed" in l or " failed" in l]

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
    if not summary:
        errored = [l for l in text.splitlines()
                   if "error during collection" in l or " error in " in l
                   or "errors" in l and "passed" not in l]
        if errored:
            raise MutationDidNotParse(errored[-1].strip())
        raise MutationDidNotParse(
            "pytest produced no summary - the run neither passed nor failed, so "
            "nothing was measured")
    return failed, summary[-1]


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
        text = text.replace(old, new)
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
    originals = {}
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
            failed, summary = _failures(test_file)
        finally:
            for path2, text in originals.items():
                path2.write_text(text, encoding="utf-8")
            # **Only once the files are back.** Clearing first would leave a
            # window where the tree is restored and the journal still claims
            # otherwise - and a run killed in that window recovers nothing,
            # which is the exact failure this journal exists to catch.
            _journal_clear()

        hit = [t for t in entry["expect"] if _witness_hit(t, failed)]
        missed = [t for t in entry["expect"] if t not in hit]
        if hit:
            caught += 1
            if verbose:
                extra = (f"  (also: {sorted(_unexplained(failed, entry['expect']))})"
                         if _unexplained(failed, entry["expect"]) else "")
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

        unexplained = _unexplained(failed, entry["expect"])
        survivors.append((entry["label"], summary, unexplained))
        if verbose:
            print(f"    -> SURVIVOR  ({summary})")
            if unexplained:
                print(f"       failed for another reason: {unexplained}")
    return {"set": name, "count": len(mutations), "caught": caught,
            "survivors": survivors, "equivalent": equivalent}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Break the code, watch the tests fail, put it back.")
    parser.add_argument("names", nargs="*",
                        help="mutation set names (default: all of them)")
    parser.add_argument("--list", action="store_true",
                        help="list the mutation sets and exit")
    parser.add_argument("--quiet", action="store_true",
                        help="only the final tally")
    parser.add_argument("--keep-going", action="store_true",
                        help="run every set even when one raises")
    parser.add_argument("--wait", type=float, default=0.0, metavar="SECONDS",
                        help="wait this long for another run to finish instead of "
                             "refusing (default: refuse immediately)")
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
    all_survivors = []
    all_equivalent = []
    failures = []
    try:
        lock = _Lock(timeout=args.wait)
        lock.__enter__()
    except _Busy as busy:
        print(str(busy), file=sys.stderr)
        return 2
    try:
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
            if not args.quiet:
                print(f"=== {name} ===")
            try:
                result = run_set(name, available[name], verbose=not args.quiet)
            except Exception as exc:  # noqa: BLE001 - one bad set must lose neither
                failures.append((name, exc))
                print(f"  ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
                if not args.keep_going:
                    # **The tally is still printed.** A set that raises on a
                    # rotated anchor is a result, not an abort: the other sets'
                    # answers are still true, and a run that prints nothing reads
                    # as "nothing ran".
                    print()
                    _report(totals, all_survivors, failures, started, all_equivalent)
                    return 1
                continue
            if result.get("empty"):
                print(f"  {name} has no mutations")
                continue
            totals["count"] += result["count"]
            totals["caught"] += result["caught"]
            all_survivors.extend((name,) + s for s in result["survivors"])
            all_equivalent.extend((name,) + e for e in result.get("equivalent", []))
    finally:
        lock.__exit__(None, None, None)

    if not args.quiet:
        print()
    _report(totals, all_survivors, failures, started, all_equivalent)
    return 1 if (all_survivors or failures) else 0


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
    checked = 0
    for name in wanted:
        namespace = {}
        exec(compile(available[name].read_text(encoding="utf-8"),
                     str(available[name]), "exec"), namespace)
        for entry in namespace.get("MUTATIONS") or []:
            target = ROOT / entry["file"]
            if not target.is_file():
                rotted.append(f"{name}: {entry['label']} -> "
                              f"{entry['file']} does not exist")
                continue
            text = target.read_text(encoding="utf-8")
            old, new = entry["old"], entry["new"]
            pairs = list(zip(old, new)) if isinstance(old, list) else [(old, new)]
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
    print(f"{len(wanted)} set(s), {checked} anchors, all still match the code")
    return 0


def _report(totals, survivors, failures, started, equivalent=()) -> None:
    print(f"{totals['count']} mutations, {totals['caught']} caught, "
          f"{len(survivors)} survivors, {len(equivalent)} equivalent  "
          f"({time.time() - started:.0f}s)")
    for name, label, summary, failed in survivors:
        print(f"  SURVIVOR [{name}] {label}")
        print(f"          {summary}")
        if failed:
            print(f"          failed for another reason: {failed}")
    for name, label, why in equivalent:
        print(f"  EQUIVALENT [{name}] {label}")
        if why:
            print(f"          {why}")
    for name, exc in failures:
        print(f"  ERROR [{name}] {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
