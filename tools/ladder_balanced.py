"""The balanced-adapter ladder, run against a real server with the confound removed.

**The ladder this exists to re-run.** The recorded three-adapter result was
**0/5 at lambda=1.0** and **2/5 at lambda=0.35**, read as evidence that
orthogonality does not give semantic composition. Then `tools/adapter_norms.py`
measured what that sum actually was: total delta norms **0.0365 / 3.0265 /
4.0636**, a **111x spread** with all three declaring `alpha/rank` of 2.0. So the
0/5 was measured on a sum in which one adapter was **0.5%** of the other two - a
rounding error - and the confound is arithmetic rather than a claim about
orthogonal bases.

That makes the experiment narrow, and `--lora-scaled` makes it cheap: it takes
`FNAME:SCALE,...`, so the balanced arms need **no merge and no re-quantisation**.
The scales are the reciprocal of each adapter's delta norm, normalised so the
*combined* magnitude is preserved rather than inflated - the weaker adapter is
still multiplied by 65x, which is the honest cost and is printed rather than
buried.

**Why combined magnitude is preserved rather than setting the strongest to 1.**
Setting the largest to 1 inflates the sum overall, so any gain cannot be
separated from the volume. Mean-normalising keeps the total where it was and
changes only the *composition*, which is the variable under test.

`--dry-run` prints the argv and the scale factors without starting anything, so
the numbers can be checked before a server is launched.
"""

import argparse
import pathlib
import subprocess
import sys
import time
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
for extra in ("tools", "cache/cultivate-libs"):
    p = str(REPO / extra)
    if p not in sys.path:
        sys.path.insert(0, p)

MODELS = REPO / "cache" / "data-home" / "shani-chronoa" / "llm"
BASE = MODELS / "Qwen3-1.7B-Q4_K_M.gguf"
ADAPTERS = ["lumen-mouth-lora.gguf", "lumen-cortex-lora.gguf",
            "lumen-executor-lora.gguf"]

#: The case subset the recorded ladder used, so a number here is comparable with
#: the one it is being checked against rather than with a fresh sample.
CASES = "time,weather,volume,set_timer,reminder"


def delta_norms():
    """(name, total delta norm) for the three adapters, measured."""
    import adapter_norms
    return [(n, adapter_norms.read_adapter(MODELS / n)[1]) for n in ADAPTERS]


def balanced_scales(norms):
    """Reciprocal-of-norm scales, normalised to preserve combined magnitude.

    `target` is the mean of the incoming norms, so `sum(w_i * norm_i)` is
    unchanged before and after and only the *composition* moves.
    """
    target = sum(n for _n, n in norms) / len(norms)
    return [target / n for _n, n in norms]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--cases", default=CASES)
    ap.add_argument("--greedy", action="store_true")
    ap.add_argument("--config", default=None,
                   help="task_eval config; the default is select")

    args = ap.parse_args(argv)

    norms = delta_norms()
    scales = balanced_scales(norms)
    print("measured total delta norms:")
    for (name, n), s in zip(norms, scales):
        print(f"  {name:<26} {n:8.4f}   balanced scale {s:8.3f}x")

    pairs = ",".join(f"{MODELS / n}:{s:.4f}" for (n, _v), s in
                     zip(norms, scales))
    argv = ["llama-server", "--model", str(BASE), "--port", str(args.port),
            "--threads", "4", "--ctx-size", "2048",
            "--lora-scaled", pairs]
    print("\n$ " + " ".join(argv))

    if args.dry_run:
        print("\n(dry run: no server started)")
        return 0

    proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        up = False
        for _ in range(180):
            time.sleep(1.0)
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{args.port}/health", timeout=2).read()
                up = True
                break
            except Exception:
                continue
        if not up:
            print("the server never came up")
            return 2
        cmd = [sys.executable, str(REPO / "tools" / "task_eval.py"),
               "--configs", args.config or ("+greedy" if args.greedy else "select"),
               "--cases", args.cases, "--port", str(args.port)]
        print("\n$ " + " ".join(cmd))
        return subprocess.run(cmd).returncode
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
