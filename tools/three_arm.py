#!/usr/bin/env python3
"""The three-arm decoder run: does the grammar do the work, or does the request?

**The experiment `AGENTS.md` prescribed and that was never run.** The entry
"Constrained decoding: the available lever nobody here has used" recommended a
grammar arm and said, in its own words, that without a third arm "any gain
would be attributed to the grammar when the paper says the register may be doing
it, and a check that cannot tell two causes apart is not check".

The three arms, on one server, one model, one case list, temperature 0:

| arm | config                  | what differs                                     |
|-----|-------------------------|--------------------------------------------------|
| 1   | `+greedy`               | nothing - llama-server's own tool calling       |
| 2   | `+greedy+constrained`   | two-step prompt, **grammar enforced**            |
| 3   | `+greedy+request`       | the same two-step prompt, **no grammar**         |

**The single variable is the grammar.** Arms 2 and 3 send the same instruction,
the same tool menu and the same schema *shape* - as prose in arm 3, as
`response_format` in arm 2 - so the model reads the same thing either way and
differs only in whether a machine can contradict it. Same two steps, same
parse, same `recover_tool_calls`, same scorer. If arm 3 matches arm 2 the
grammar bought nothing and the request did all of it, which is what
arXiv:2607.18476 found across 44 models.

**Why the report separates form from choice.** The enforcement probe measured
that a grammar whose schema allows exactly one value makes the model return that
value *even when it has already decided against it*. So the constraint moves
**whether a tool was called** and leaves **which tool** where it was. One
combined number cannot show that, and the two readings imply opposite decisions:
"the grammar did nothing" argues against adopting it, while "it made the answer
well-formed without making it right" argues adopting it is safe and pointless.

**One process per arm, each writing its own JSON the moment it finishes.** The
first version passed all three arms to one `task_eval` invocation, which writes
its JSON once at the end - so a run cut off during the third arm produced
nothing at all, including the two arms already finished. Measured here: arm 1
takes about an hour, arms 2 and 3 issue two requests a case and take about two.
Splitting costs nothing (one server is still loaded once) and makes the failure
mode impossible.

`--dry-run` prints the argv so the numbers can be checked before anything starts.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
for extra in ("tools", "cache/cultivate-libs"):
    _p = str(REPO / extra)
    if _p not in sys.path:
        sys.path.insert(0, _p)

MODELS = REPO / "cache" / "data-home" / "shani-chronoa" / "llm"
BASE = MODELS / "Qwen3-1.7B-Q4_K_M.gguf"

#: The same five ids the recorded ladder used, so a number here is comparable
#: with the one it is being checked against rather than with a fresh sample.
#: **Only three of these exist** - `volume` is not a case in the file - which is
#: why the population is loaded and counted rather than taken from this string.
CASES = "time,weather,volume,set_timer,reminder"

ARMS = ("+greedy", "+greedy+constrained", "+greedy+request")


def cases_of(spec: str) -> "list[str]":
    return [c for c in spec.split(",") if c]


def _wait(port: int, seconds: int = 180) -> bool:
    for _ in range(seconds):
        time.sleep(1.0)
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/health" % port, timeout=2).read()
            return True
        except Exception:
            continue
    return False


def score_arm(rows: "list[dict]") -> "tuple[int, int, int, int]":
    """(acted, right tool, right tool and arguments, scorable).

    **`acted` is the form half and `tool` is the choice half**, reported
    separately because that split *is* the finding.

    Rows the service failed are excluded from every denominator rather than
    counted as misses, and the count is printed - a 429 is not a wrong answer.
    """
    scorable = [r for r in rows if not r.get("unavailable")]
    acted = sum(1 for r in scorable if r.get("seen") != "no tool")
    return (acted,
            sum(1 for r in scorable if r.get("tool")),
            sum(1 for r in scorable if r.get("args")),
            len(scorable))


def report(results: dict, arms: "list[str]", n: int, dropped: int = 0) -> None:
    """The form/choice table, as its own function so a partial run prints it."""
    print("")
    print("%d cases, temperature 0, one server" % n)
    if dropped:
        print("(%d row(s) the service failed are excluded from every denominator, "
              "not counted as misses)" % dropped)
    print("")
    print("%-22s %-13s %-15s %s" % ("arm", "acted (form)", "right tool", "tool+args"))
    tallies = {}
    for arm in arms:
        rows = results.get(arm, {}).get("rows", [])
        tallies[arm] = score_arm(rows)
        d = tallies[arm][3]
        print("%-22s %4d/%-8d %5d/%-9d %5d/%-7d"
              % (arm, tallies[arm][0], d, tallies[arm][1], d, tallies[arm][2], d))
    print("")
    print("acted = a tool was called at all (form).  right tool = the right one (choice).")
    base = arms[0]
    for arm in arms[1:]:
        print("  %s vs %s: form %+d, choice %+d"
              % (arm, base, tallies[arm][0] - tallies[base][0],
                 tallies[arm][1] - tallies[base][1]))
    if len(arms) == 3:
        print("")
        print("  The third line is the experiment: same request, same two steps, the")
        print("  grammar is the only difference.  A tie means the request did the work;")
        print("  form up with choice flat means the grammar made answers well-formed")
        print("  and no more likely to be right.")
    if len(arms) < 3:
        print("")
        print("  !! only %d of %d arms ran - this is not a three-arm result"
              % (len(arms), len(ARMS)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--cases", default=CASES,
                    help="comma-separated case ids; **empty runs every case**. The "
                         "default names five ids of which only three exist, so "
                         "'--cases ' with nothing after it is how you get 220 - and "
                         "a five-case run that reads like a full one is the failure "
                         "this default invites.")
    ap.add_argument("--threads", default="4")
    ap.add_argument("--ctx", type=int, default=2048)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--json", default="")
    args = ap.parse_args(argv)

    # **Say the population out loud before starting a server.** The first version
    # of this tool counted the *strings* it had been handed, so it printed "5
    # cases" against a 225-case file - and a summary that reads the same for 5 and
    # for 220 is a reporting defect, not a formatting choice.
    here = json.loads((REPO / "tools" / "eval_cases.json").read_text())["cases"]
    chosen = [c for c in here if not args.cases or c["id"] in cases_of(args.cases)]
    print("%d case(s) of %d in the file" % (len(chosen), len(here)))
    if not chosen:
        print("no case matched --cases=%r" % args.cases)
        return 2

    server = ["llama-server", "--model", str(BASE), "--port", str(args.port),
              "--threads", args.threads, "--ctx-size", str(args.ctx)]
    ids = ",".join(c["id"] for c in chosen)
    stem = pathlib.Path(args.json).stem if args.json else None

    print("$ " + " ".join(server))
    for arm in ARMS:
        cmd = [sys.executable, str(REPO / "tools" / "task_eval.py"),
               "--configs", arm, "--cases", ids,
               "--port", str(args.port), "--repeat", str(args.repeat)]
        if stem:
            cmd += ["--json", str(pathlib.Path(args.json).with_name(
                "%s-%s.json" % (stem, arm.replace("+", ""))))]
        print("$ " + " ".join(cmd))
    if args.dry_run:
        print("")
        print("(dry run: no server started)")
        return 0

    proc = subprocess.Popen(server, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not _wait(args.port):
            print("the server never came up")
            return 2
        print("")
        print("=" * 78)
        merged, order = {}, []
        for arm in ARMS:
            cmd = [sys.executable, str(REPO / "tools" / "task_eval.py"),
                   "--configs", arm, "--cases", ids,
                   "--port", str(args.port), "--repeat", str(args.repeat)]
            target = None
            if stem:
                target = pathlib.Path(args.json).with_name(
                    "%s-%s.json" % (stem, arm.replace("+", "")))
                cmd += ["--json", str(target)]
            began = time.time()
            code = subprocess.run(cmd).returncode
            took = time.time() - began
            rows = (json.loads(target.read_text()).get(arm, {}).get("rows", [])
                    if target else [])
            acted, tool, args_ok, n = score_arm(rows)
            print("  %-22s acted %d/%-4d right tool %d/%-4d tool+args %d/%-4d  (%.0fs, exit %d)"
                  % (arm, acted, n, tool, n, args_ok, n, took, code))
            if target:
                merged[arm] = {"rows": rows}
                order.append(arm)
            if code != 0:
                print("  !! %s exited %d - stopping so the arms that did finish are kept"
                      % (arm, code))
                break

        print("")
        print("=" * 78)
        print("arms with results: %d of %d" % (len(order), len(ARMS)))
        if not order:
            return 2
        if stem:
            pathlib.Path(args.json).write_text(json.dumps(merged, indent=1))
        report(merged, order, len(chosen),
               sum(1 for arm in order for r in merged[arm]["rows"]
                   if r.get("unavailable")))
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())