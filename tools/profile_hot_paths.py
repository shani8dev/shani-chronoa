"""What actually costs time and memory on the paths every turn touches.

Run as a driver it spawns **one subprocess per path** and tabulates wall time and
peak RSS. Subprocess-per-item is the whole design: `ru_maxrss` is a high-water
mark, so measuring ten things in one process attributes the largest allocation to
whichever ran last. This is the "a probe that measures the fixture" trap one level
up - a number that cannot be attributed to the thing it is about is not a number.

    python3 tools/profile_hot_paths.py            # the table
    python3 tools/profile_hot_paths.py learning_load -v   # one, verbosely

**Space is reported as peak RSS of the whole process, not as the size of one
object.** A reader that returns a 4 MB list built inside a 400 MB peak is a
different problem from one that returns it in a 6 MB peak, and only the second
number is what the machine has to hold.

Not packaged: `tools/` is the maintenance surface. Nothing here changes behaviour;
it measures.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "usr" / "lib" / "shani-chronoa"
#: A **copy** of this machine's real call log, all chunks. Profiling a reader
#: against a synthetic file measures the reader on nothing - 40-byte fixtures are
#: why the rotation bug hid for a day.
LOG_DIR = pathlib.Path(os.environ.get("CHRONOA_PROFILE_LOG_DIR",
                                      "/tmp/opencode/prof"))
LOG = LOG_DIR / "tool_calls.log"


def _log_path():
    return LOG


# ---------------------------------------------------------------- one path each
def p_discover_skills(_v):
    """The registry, read once per process at startup."""
    from shani_chronoa import skills
    schemas, handlers = skills.discover_skills()
    return "%d schemas, %d handlers" % (len(schemas), len(handlers))


def p_select_tools(_v):
    """Per request: narrow 256 schemas to the handful worth sending."""
    from shani_chronoa import skills, tool_select
    schemas, _ = skills.discover_skills()
    cases = json.loads((ROOT / "tools" / "eval_cases.json").read_text())["cases"]
    total = 0
    for case in cases:
        # **No `learned` kwarg.** It is `select_tools(request, tools, limit,
        # in_use)` - the learned weights reach `ranked()`, not this. Passing it
        # was the shape-filter lie this repo keeps paying for.
        total += len(tool_select.select_tools(case["say"], schemas))
    return "%d requests, %d schemas offered in total" % (len(cases), total)


def p_guardrail_check(_v):
    """Per dispatch: the shape check, now including the enum gate."""
    from shani_chronoa import guardrail, skills
    schemas, _ = skills.discover_skills()
    fn = lambda s: s.get("function") or {}
    out = 0
    for schema in schemas:
        props = ((fn(schema).get("parameters") or {}).get("properties") or {})
        args = {}
        for key, node in props.items():
            args[key] = (node.get("enum") or ["x"])[0] if isinstance(node, dict) else "x"
        r = guardrail.check(fn(schema)["name"], args, fn(schema).get("parameters") or {})
        out += int(r.should_run)
    return "%d well-formed calls checked, %d passed" % (len(schemas), out)


def p_learning_load(_v):
    """The training reader: the whole history, across every chunk."""
    from shani_chronoa import learning
    rows = learning.load(_log_path())
    return "%d examples" % len(rows)


def p_learning_examples(_v):
    """load() plus feature construction - what a training run actually costs."""
    from shani_chronoa import learning
    # **There is no `learning.example`, and `features()` does not take an
    # `Example`.** `load()` already returns `Example`s whose `.active` *is* the
    # sparse vector, so feature construction happens inside `load()` - asking
    # this probe to build them again would have measured nothing twice.
    rows = learning.load(_log_path())
    return "%d examples, %.1f non-zeros each" % (
        len(rows), sum(len(r.active) for r in rows) / max(len(rows), 1))


def p_active_vector(_v):
    """What one training record costs held, sparse against dense.

    **Both sides measured with `sys.getsizeof`, then extrapolated.** The
    arithmetic a reader would do says a 16,384-wide list of floats is 64 KB. It
    is not: every slot holds a pointer *and* a distinct float object, so the real
    cost is several times that - and that gap is the entire reason the sparse
    form exists. Measured here rather than asserted, because the number in the
    `Example` docstring is the whole argument for it.
    """
    import sys
    from shani_chronoa import learning
    rows = learning.load(_log_path())
    if not rows:
        return "no examples"
    wide = learning._N_FEATURES
    one_dense = sys.getsizeof([0.0] * wide) + wide * sys.getsizeof(0.0)
    one_sparse = (sys.getsizeof(rows[0].active)
                  + sum(sys.getsizeof(k) + sys.getsizeof(v) for k, v in rows[0].active))
    return ("%d records: dense %.2f GB vs sparse %.2f MB (%.0fx less)"
            % (len(rows), len(rows) * one_dense / 1e9,
               len(rows) * one_sparse / 1e6, one_dense / max(one_sparse, 1)))


def p_learning_entries(_v):
    """The raw-record reader behind `unknown_tool_examples` and `lessons`."""
    from shani_chronoa import learning
    entries = learning._entries(_log_path())
    return "%d records" % len(entries)


def p_durable_records(_v):
    """The hot-path advisory read: a seek to the tail, capped."""
    from shani_chronoa import learning
    # `_durable_records(tracker)` takes a **tracker** and returns `Optional` -
    # not a path and not a list. `tracker.log_file` is a **property returning a
    # Path**: a callable stand-in got a `TypeError` about `__fspath__` receiving
    # a function. The real tracker is not what this measures, the read is.
    class _T:
        log_file = _log_path()
    records = learning._durable_records(_T) or []
    return "%d records from the tail" % len(records)


def p_dream_read_log(_v):
    """The dream pass's reader: newest-first across the whole history."""
    from shani_chronoa import dream
    rows = dream.read_log(_log_path())
    return "%d records" % len(rows)


def p_activity_log_tail(_v):
    """The Tool Activity panel's reader."""
    from shani_chronoa.settings_window import activity
    tail = activity._log_tail(_log_path())
    return "%d rows, %d bytes reported" % (len(tail.lines), tail.size)


def p_dispatch_cheap(_v):
    """A real dispatch end to end, gate + sandbox + audit origin included."""
    from shani_chronoa import tools
    out = tools.execute_tool_outcome("get_datetime", {})
    return "ran=%s verdict=%s" % (out.ran, out.verdict)


PATHS = {
    "discover_skills": p_discover_skills,
    "select_tools": p_select_tools,
    "guardrail_check": p_guardrail_check,
    "dispatch_cheap": p_dispatch_cheap,
    "learning_load": p_learning_load,
    "learning_examples": p_learning_examples,
    "active_vector": p_active_vector,
    "learning_entries": p_learning_entries,
    "durable_records": p_durable_records,
    "dream_read_log": p_dream_read_log,
    "activity_log_tail": p_activity_log_tail,
}

#: What each path costs, judged against. **A time with no scale is not a
#: finding** - "0.2s" means nothing without knowing whether it is per turn, per
#: request or once at startup.
SCALE = {
    "discover_skills": "once per process",
    "select_tools": "**per request**",
    "guardrail_check": "**per dispatch**",
    "dispatch_cheap": "**per dispatch**",
    "learning_load": "per training run",
    "learning_examples": "per training run",
    "active_vector": "per training run",
    "learning_entries": "per panel refresh",
    "durable_records": "**per request** (advisory weights)",
    "dream_read_log": "per background pass",
    "activity_log_tail": "per panel refresh",
}


def measure(name: str, repeats: int, verbose: bool) -> dict:
    """One path, in its own process, `repeats` times."""
    code = (
        "import sys, time, resource, json\n"
        "sys.path.insert(0, %r)\n"
        "sys.argv = ['x']\n"
        "import importlib.util as u\n"
        "spec = u.spec_from_file_location('prof', %r)\n"
        "m = u.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "fn = m.PATHS[%r]\n"
        "ts = []\n"
        "t = time.perf_counter(); note = fn(%s); cold = time.perf_counter()-t\n"
        "for _ in range(%d):\n"
        "    t = time.perf_counter(); fn(%s); ts.append(time.perf_counter()-t)\n"
        "print(json.dumps({'note': note, 'cold': cold, 'best': min(ts), "
        "'median': sorted(ts)[len(ts)//2], 'rss_mb': resource.getrusage("
        "resource.RUSAGE_SELF).ru_maxrss/1024.0, 'runs': ts}))\n"
        % (str(PKG), str(pathlib.Path(__file__).resolve()), name,
           "True" if verbose else "False", repeats,
           "True" if verbose else "False")
    )
    started = time.time()
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, env={**os.environ,
                                          "PYTHONDONTWRITEBYTECODE": "1"})
    if proc.returncode != 0 or not proc.stdout.strip():
        return {"name": name, "error": (proc.stderr.strip().splitlines() or
                                         ["no output"])[-1][:160]}
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    out["name"] = name
    out["scale"] = SCALE.get(name, "")
    out["total"] = time.time() - started
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("path", nargs="?", choices=sorted(PATHS),
                    help="one path; omit for the table")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if args.path:
        one = measure(args.path, args.repeats, True)
        if "error" in one:
            print("%-20s ERROR %s" % (one["name"], one["error"]))
            return 1
        print("%-20s cold %.4fs  warm median %.4fs  peak RSS %.0f MB  | %s"
              % (one["name"], one["cold"], one["median"], one["rss_mb"],
                 one["note"]))
        if args.verbose:
            print("   per-run:", ["%.4f" % t for t in one["runs"]])
        return 0

    if not LOG.exists():
        print("No log at %s. Set CHRONOA_PROFILE_LOG_DIR to a directory holding"
              " tool_calls.log* - profiling a reader on a synthetic file measures"
              " it on nothing." % LOG)
        return 2

    rows = [measure(name, args.repeats, False) for name in PATHS]
    # Sorted by **cold**, because a warm number is a cache reading itself back
    # and ranking a table by it would put the cheapest path first for the wrong
    # reason. `learning_load` measured 0.0001s warm while its cold read is the
    # most expensive in the set.
    rows.sort(key=lambda r: r.get("cold", r.get("median", 0)), reverse=True)
    print("hot paths, %d repeats each, one subprocess apiece"
          % args.repeats)
    print("log: %s (%d bytes across %d chunk(s))"
          % (LOG, sum(p.stat().st_size for p in LOG_DIR.glob("tool_calls.log*")),
             len(list(LOG_DIR.glob("tool_calls.log*")))))
    print()
    print("%-20s %9s %9s %12s  %s" % ("path", "cold", "warm", "peak RSS", "scale"))
    for row in rows:
        if "error" in row:
            print("%-20s %9s %9s %12s  %s" % (row["name"], "ERROR", "",
                                               "", row["error"]))
            continue
        print("%-20s %8.4fs %8.4fs %10.0f MB  %s"
              % (row["name"], row["cold"], row["median"], row["rss_mb"],
                 row["scale"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
