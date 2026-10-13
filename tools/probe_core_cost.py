"""Does the unconditional CORE floor earn its 40% of the tool budget?

**The measurement, because the number was recorded as dilution and never
attributed.** Across the 220 scorable cases, `select_tools` offers **1,390**
schemas and **553 of them (40%) score no word match at all**. They are not a
ranking artefact - they are `CORE`, unioned in unconditionally:
`get_datetime` on 212 requests, `ask_user` on 177, `web_search` on 163.

`CORE`'s own comment says why it exists and records the cost from the other
direction: a small model handed a tool list "treats every turn as an instruction
to use one", which is why `list_capabilities` had to stop being free. So the
floor is deliberate and the tension is documented - and **which way it nets out
on the real model was never measured.**

The two arms are the same eval, the same server, the same cases: `select` as it
ships, and `select` with CORE admitted only when it *ranks*. Nothing else
differs, so the delta is the floor and not the run.
"""

import argparse
import asyncio
import json
import pathlib
import subprocess
import sys
import time
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
for extra in ("usr/lib/shani-chronoa", "tools"):
    p = str(REPO / extra)
    if p not in sys.path:
        sys.path.insert(0, p)

MODELS = REPO / "cache" / "data-home" / "shani-chronoa" / "llm"
BASE = MODELS / "Qwen3-1.7B-Q4_K_M.gguf"
#: **The population where the floor should matter is the one its own comment
#: describes.** `tool_select.CORE`'s docstring records that a small model handed
#: a tool list "treats every turn as an instruction to use one" - which is a claim
#: about *conversational* turns, where the right answer is to just reply. So the
#: ensemble is the 5 `none: true` cases plus a few ordinary ones as a control, and
#: not the ids a probe author invents. (`volume` and `set_timer` were the first
#: version's guess and are not in the corpus at all, so they were silently
#: dropped - a case list named rather than derived is a case list nobody ran.)
CASE_IDS = [c["id"] for c in json.loads(
    (REPO / "tools" / "eval_cases.json").read_text())["cases"]
    if c.get("none")] + ["time", "weather", "reminder"]


def up(port):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2).read()
        return True
    except Exception:
        return False


def start(port):
    argv = ["llama-server", "--model", str(BASE), "--port", str(port),
            "--threads", "4", "--ctx-size", "2048"]
    proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    for _ in range(180):
        if up(port):
            return proc
        time.sleep(1.0)
    proc.kill()
    return None


async def one_arm(label, port):
    """Run the eval with this process's `select_tools` as patched."""
    from shani_chronoa import tool_select as S
    import task_eval

    # `task_eval` imports `select_tools` **inside** its function body, so the
    # seam is the module attribute, not a name on `task_eval` - the first version
    # of this probe patched the latter and raised `AttributeError` at the first
    # arm.
    real = S.select_tools

    def patched(say, tools):
        sent = real(say, tools)
        if not CONDITIONAL:
            return sent
        # CORE only when it ranks on merit.
        scored = {n for _s, n, _h in S.ranked(say, tools, learned={})}
        keep = [t for t in sent
                if S._name(t) not in set(S.CORE) or S._name(t) in scored]
        return keep or sent  # never empty: an empty list asks for nothing

    S.select_tools = patched
    try:
        report = await task_eval.run(("select",), CASES, "", 1)
    finally:
        S.select_tools = real
    # `run()` returns {config: rows}, not {rows: [...]}; the first
    # version of this probe read a key that is not there.
    rows = report["select"]["rows"]
    return label, rows


#: The cases `task_eval.run` wants are **dicts**, not the ids named on the command
#: line - the first version of this probe passed `CASES.split(",")` and every case
#: raised `string indices must be integers` inside a frame nobody could read as
#: "I passed the wrong shape".
CASES = [c for c in json.loads(
    (REPO / "tools" / "eval_cases.json").read_text())["cases"]
    if c["id"] in CASE_IDS]

CONDITIONAL = False


async def main_async(port):
    global CONDITIONAL
    out = []
    for label, conditional in (("CORE unconditional (as shipped)", False),
                               ("CORE only when it ranks", True)):
        CONDITIONAL = conditional
        _label, rows = await one_arm(label, port)
        right = sum(1 for r in rows if r["args"])
        out.append((label, right, len(rows), rows))
        print(f"  {label:<34} {right}/{len(rows)}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--reuse", action="store_true",
                    help="a server is already running on --port")
    args = ap.parse_args()

    proc = None
    if not args.reuse:
        proc = start(args.port)
        if proc is None:
            print("the server never came up")
            return 2
    try:
        print(f"{'arm':<34} score")
        asyncio.run(main_async(args.port))
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
