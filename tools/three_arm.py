#!/usr/bin/env python3
"""The three-arm decoder run: does the grammar do the work, or does the request?

**The experiment `AGENTS.md` prescribed and that was never run.** The entry
"Constrained decoding: the available lever nobody here has used" recommended a
grammar arm and said, in its own words, that without a third arm "any gain
would be attributed to the grammar when the paper says the register may be doing
it, and a check that cannot tell two causes apart is not check".

The three arms, on one server, one model, one case list, temperature 0:

| arm | config              | what differs                                    |
|-----|---------------------|-------------------------------------------------|
| 1   | `+greedy`           | nothing - llama-server's own tool calling      |
| 2   | `+greedy+constrained` | same two-step prompt, **grammar enforced**     |
| 3   | `+greedy+request`   | same two-step prompt, schema in prose, **no grammar** |

**The single variable is the grammar.** Arms 2 and 3 send the same instruction,
the same tool menu and the same schema *shape* - as prose in arm 3, as
`response_format` in arm 2 - so the model reads the same thing either way and
differs only in whether a machine can contradict it. Same two steps, same
parse, same `recover_tool_calls`, same scorer. If arm 3 matches arm 2 the
grammar bought nothing and the request did all of it, which is what
arXiv:2607.18476 found across 44 models.

**Why the server is started here and not assumed.** A second server, a different
`-t`, a second model or a non-zero temperature would each be a fourth
explanation, and none of them is the grammar. `--dry-run` prints the argv so the
numbers can be checked before anything is launched.

`--json` writes the raw rows so a later reading can be re-deriven without
re-running the model.
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

#: The same five cases the recorded ladder used, so a number here is comparable
#: with the 4/5 it is being checked against rather than with a fresh sample.
#: The adapters are deliberately **not** loaded: this measures the decoder, and
#: the ladder's merged adapters already answered no (0/3, 0/5).
CASES = "time,weather,volume,set_timer,reminder"

ARMS = ("+greedy", "+greedy+constrained", "+greedy+request")

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
    separately because that split *is* the finding. Measured directly: given a
    schema whose only legal value was `get_datetime` and a question about the
    weather, the constrained model answered `get_datetime` - the one value it was
    allowed - having already decided the question was not about the time. So the
    grammar moves `acted` and leaves `tool` where it was.

    A single combined number cannot show that. "Same score" would read as "the
    grammar did nothing" when the honest reading is "the grammar made the answer
    well-formed and no more likely to be right", and those call for opposite
    decisions about adopting it.

    Rows the service failed are excluded from every denominator rather than
    counted as misses, and the count is printed - a 429 is not a wrong answer.
    """
    scorable = [r for r in rows if not r.get("unavailable")]
    acted = sum(1 for r in scorable if r.get("seen") != "no tool")
    return (acted,
            sum(1 for r in scorable if r.get("tool")),
            sum(1 for r in scorable if r.get("args")),
            len(scorable))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--cases", default=CASES,
                    help="comma-separated case ids; **empty runs every case**. The "
                         "default is the five the recorded ladder used, so "
                         "'--cases ' with nothing after it is how you get 220 and "
                         "not 5 - and a five-case run that looks like a full one is "
                         "the failure this default invites.")
    ap.add_argument("--threads", default="4")
    ap.add_argument("--ctx", type=int, default=2048)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--json", default="")
    args = ap.parse_args(argv)

    # **Say the population out loud before starting a server.** The first full
    # run of this tool printed "5 cases" in its own summary and nobody read it,
    # because the summary looks the same whether 5 or 220 cases ran and the cost
    # of the wrong number is a result that cannot be compared with anything.
    here = json.loads((REPO / "tools" / "eval_cases.json").read_text())["cases"]
    chosen = [c for c in here if not args.cases or c["id"] in cases_of(args.cases)]
    print("%d case(s) of %d in the file" % (len(chosen), len(here)))
    if not chosen:
        print("no case matched --cases=%r" % args.cases)
        return 2

    server = ["llama-server", "--model", str(BASE), "--port", str(args.port),
              "--threads", args.threads, "--ctx-size", str(args.ctx)]
    cmd = [sys.executable, str(REPO / "tools" / "task_eval.py"),
           "--configs", ",".join(ARMS), "--cases", ",".join(c["id"] for c in chosen),
           "--port", str(args.port), "--repeat", str(args.repeat)]
    if args.json:
        cmd += ["--json", args.json]
    print("$ " + " ".join(server))
    print("$ " + " ".join(cmd))
    if args.dry_run:
        print("\n(dry run: no server started)")
        return 0

    proc = subprocess.Popen(server, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not _wait(args.port):
            print("the server never came up")
            return 2
        print("\n" + "=" * 78)
        started = time.time()
        code = subprocess.run(cmd).returncode
        elapsed = time.time() - started

        if not args.json:
            return code
        results = json.loads(pathlib.Path(args.json).read_text())
        n = len(chosen)
        print("\n" + "=" * 78)
        print("%d cases, temperature 0, one server, %.0fs (%.1fs a case a arm)"
              % (n, elapsed, elapsed / max(1, n * len(ARMS))))
        dropped = sum(1 for arm in ARMS
                      for r in results.get(arm, {}).get("rows", []) if r.get("unavailable"))
        if dropped:
            print("(%d row(s) the service failed are excluded from every "
                  "denominator, not counted as misses)" % dropped)
        print()
        print("%-22s %-10s %-12s %s" % ("arm", "acted", "right tool", "tool+args"))
        tallies = {}
        for arm in ARMS:
            rows = results.get(arm, {}).get("rows", [])
            tallies[arm] = score_arm(rows)
            d = tallies[arm][3]
            print("%-22s %4d/%-5d %5d/%-6d %5d/%-6d" % (
                arm, tallies[arm][0], d, tallies[arm][1], d, tallies[arm][2], d))
        print()
        print("  acted = a tool was called at all (form).  right tool = the right "
              "one (choice).")
        print("  %s vs %s: form %+d, choice %+d"
              % (ARMS[1], ARMS[0], tallies[ARMS[1]][0] - tallies[ARMS[0]][0],
                 tallies[ARMS[1]][1] - tallies[ARMS[0]][1]))
        print("  %s vs %s: form %+d, choice %+d  <- same request, grammar is the "
              "only difference"
              % (ARMS[1], ARMS[2], tallies[ARMS[1]][0] - tallies[ARMS[2]][0],
                 tallies[ARMS[1]][1] - tallies[ARMS[2]][1]))
        print()
        print("  form up and choice flat is the grammar working exactly as far as")
        print("  it can: well-formed, no more likely to be right.")
        return code
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()


def cases_of(spec: str) -> "list[str]":
    return [c for c in spec.split(",") if c]


if __name__ == "__main__":
    raise SystemExit(main())