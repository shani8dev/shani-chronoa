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
2. **The file is restored in a `finally`.** A crash, a timeout or a kill leaves
   the tree exactly as it was found.
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

#: How long one test run may take before it is declared a timeout rather than a
#: result. A mutation run is only ever against one test file, so a generous
#: ceiling is a safety net and not a budget anybody tunes.
RUN_TIMEOUT = 900

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
    failed = {line.split("::")[-1].split(" ")[0]
              for line in text.splitlines() if line.startswith("FAILED")}
    summary = [l for l in text.splitlines() if " passed" in l or " failed" in l]
    return failed, (summary[-1] if summary else "(no summary)")


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
    originals = {}
    for entry in mutations:
        target = ROOT / entry["file"]
        originals.setdefault(target, target.read_text(encoding="utf-8"))
        old, new = entry["old"], entry["new"]
        pairs = list(zip(old, new)) if isinstance(old, list) else [(old, new)]
        try:
            _apply(target, pairs)
            if verbose:
                print(f"  MUTATED: {entry['label']}")
            failed, summary = _failures(test_file)
        finally:
            for path2, text in originals.items():
                path2.write_text(text, encoding="utf-8")

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
        else:
            survivors.append((entry["label"], summary,
                              sorted(_unexplained(failed, entry["expect"]))))
            if verbose:
                print(f"    -> SURVIVOR  ({summary})")
                unexplained = _unexplained(failed, entry["expect"])
                if unexplained:
                    print(f"       failed for another reason: {unexplained}")
    return {"set": name, "count": len(mutations), "caught": caught,
            "survivors": survivors}


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
    failures = []
    for name in wanted:
        if not args.quiet:
            print(f"=== {name} ===")
        try:
            result = run_set(name, available[name], verbose=not args.quiet)
        except Exception as exc:  # noqa: BLE001 - one bad set must not lose the rest
            failures.append((name, exc))
            print(f"  ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
            if not args.keep_going:
                # **The tally is still printed.** A set that raises on a rotated
                # anchor is a result, not an abort: the other sets' answers are
                # still true and a run that prints nothing reads as "nothing ran".
                print()
                _report(totals, all_survivors, failures, started)
                return 1
            continue
        if result.get("empty"):
            print(f"  {name} has no mutations")
            continue
        totals["count"] += result["count"]
        totals["caught"] += result["caught"]
        all_survivors.extend((name,) + s for s in result["survivors"])

    if not args.quiet:
        print()
    _report(totals, all_survivors, failures, started)
    return 1 if (all_survivors or failures) else 0


def _report(totals, survivors, failures, started) -> None:
    print(f"{totals['count']} mutations, {totals['caught']} caught, "
          f"{len(survivors)} survivors  ({time.time() - started:.0f}s)")
    for name, label, summary, failed in survivors:
        print(f"  SURVIVOR [{name}] {label}")
        print(f"          {summary}")
        if failed:
            print(f"          failed for another reason: {failed}")
    for name, exc in failures:
        print(f"  ERROR [{name}] {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
