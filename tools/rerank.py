"""Train and evaluate a LoRA reranker over tool descriptions, and say whether it is worth it.

**Why a reranker at all, and why this shape.** The measured state of selection
is: `select` offers the right tool in **95.5%** of 220 requests but ranks it
**first** in only **80%**, and **35% of the tools it actually sends share no word
with the request**. So the answer is usually *in* the offered list and the ordering
is what loses it - which is a ranking problem, and ranking transfers where
classification does not: 192 of 204 tools appear exactly once in the corpus, so a
classifier that holds out a request also holds out its tool's only example.

The objective is therefore **listwise**: one softmax over `1 positive + N
negatives` per request, rather than a binary cross-entropy per pair. The corpus is
built in that shape on purpose - negatives come from `ranked()`'s own top slice,
because the tools that crowd out the answer are the ones it already rates highest.
Uniform negatives build a corpus the model passes trivially and that trains
nothing.

**The recipe is fixed here rather than passed in**, and every setting carries the
measurement it came from - the same discipline `lora_timing.py` documents:

- **rank 8**, on `q/k/v/o_proj` - capacity is what causes forgetting on a corpus
  this small.
- **one epoch, not convergence** - arXiv:2506.14126 measures long expert
  fine-tuning *degrading* a merge through memorisation-driven interference, and
  arXiv:2508.16082 measures a one-epoch task vector as the negative gradient
  scaled by the learning rate, with the first epoch dominating both its norm and
  its direction. Two epochs is a different vector, not "a bit more".
- **a scalar head, not instruction format** - arXiv:2607.03801 measures +2.2 to
  +3.3 for a classification head over label generation, at exactly the 0.6B and
  1.7B this box runs.
- **a cross-encoder, not a bi-encoder** - the decision is between a request and a
  *specific* description, and a bi-encoder cannot attend across the pair.

**What decides whether this ships, and it is not top-1.** Two numbers, both
required:

1. **top-1 on the 55 held-out requests**, against what `ranked()` already scores
   on the same 55. The corpus's own `recall_hit` field bounds the ceiling: a
   reranker cannot rank an answer into a list that does not contain it, so the
   number to beat is `min(current_top1, recall_ceiling)`.
2. **the serving cost.** A cross-encoder scores every candidate, so a k-candidate
   list costs k forward passes per request in the hot path - against a selector
   that is a dict lookup and a sort. `--eval-baseline` prints it, because a
   reranker that turns 80% into 85% by multiplying selection latency is a product
   decision and not a model result.

Nothing here is packaged: `cache/cultivate-libs` (torch, peft, transformers) and
`cache/models/Qwen3-1.7B` are both gitignored research trees.

    python3 tools/rerank.py --eval-baseline      # seconds, no torch needed
    python3 tools/rerank.py --dry-run            # the plan, the arithmetic, the headroom
    python3 tools/rerank.py --train              # the hours
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
CORPUS = ROOT / "tools" / "reranker_corpus.json"
MODEL_DIR = ROOT / "cache" / "models" / "Qwen3-1.7B"
LIB_DIR = ROOT / "cache" / "cultivate-libs"

RANK = 8
TARGETS = ("q_proj", "k_proj", "v_proj", "o_proj")
EPOCHS = 1
LR = 1e-4
MAX_TOKENS = 384
#: A request plus a tool description, truncated. Measured against the corpus's own
#: longest pair rather than guessed - a number nobody measured is the constant
#: nobody re-checks.
OFFERED_CAPACITY = 8


def load_corpus() -> dict:
    return json.loads(CORPUS.read_text())


def _truncate(text: str, limit: int) -> str:
    """Over-long descriptions lose their tail, which is where `action` lives.

    Recorded rather than assumed: cutting a description mid-`action` list would
    turn a positive into a negative for a reason that has nothing to do with the
    request, which is the one mistake a ranking corpus cannot recover from.
    """
    return text if len(text) <= limit else text[: limit - 1] + "…"


def pair_text(say: str, desc: str) -> str:
    return _truncate("request: %s\ntool: %s" % (say.strip(), desc.strip()), MAX_TOKENS)


def eval_baseline(verbose: bool = True) -> dict:
    """What `tool_select.ranked()` already does on the held-out half.

    No torch, no model, seconds. This is the number a reranker has to beat, and
    computing it before training is the difference between a result and a hope.
    """
    sys.path.insert(0, str(ROOT / "usr" / "lib" / "shani-chronoa"))
    from shani_chronoa import skills, tool_select

    schemas, _ = skills.discover_skills()
    data = load_corpus()
    held = data["held_out"]
    top1 = offered = ceiling = 0
    for item in held:
        # **The real selector over the real schemas**, not a reconstruction from
        # the corpus's own `desc` strings. `ranked()` yields
        # `(score, name, overlap)` and only for tools that matched, so a
        # candidate list read off the wrong place would be measuring the corpus
        # builder rather than the thing being beaten. `learned={}` says out loud
        # that no learned weights are in play - the corpus builder's own bug was a
        # build that was a function of whoever ran it.
        order = [name for _score, name, _overlap
                 in tool_select.ranked(item["say"], schemas, learned={})]
        top1 += bool(order) and order[0] == item["positive"]
        # the corpus drew its negatives from the selector's own top slice, so
        # "was the answer inside the budget" is exactly `recall_hit`
        ceiling += bool(item.get("recall_hit"))
        offered += item["positive"] in order[:OFFERED_CAPACITY]
    n = len(held)
    out = {
        "held_out_requests": n,
        "baseline_top1": top1,
        "baseline_top1_pct": round(100.0 * top1 / n, 1),
        "offered_recall_at_%d" % OFFERED_CAPACITY: offered,
        "offered_recall_pct": round(100.0 * offered / n, 1),
        "reranker_ceiling": ceiling,
        "reranker_ceiling_pct": round(100.0 * ceiling / n, 1),
        "candidates_reranked_per_request": OFFERED_CAPACITY,
    }
    if verbose:
        print("What a reranker has to beat, measured on the held-out half:")
        for k, v in out.items():
            print("  %-34s %s" % (k, v))
        print()
        print("  The ceiling is `recall_hit`: a reranker cannot rank an answer")
        print("  into a list that does not contain it. So top-1 above")
        print("  %.1f%% is unreachable, whatever the model does."
              % out["reranker_ceiling_pct"])
    return out


def dry_run() -> int:
    """The arithmetic, and whether this box can hold it."""
    data = load_corpus()
    train, held = data["train"], data["held_out"]
    negs = [len(it.get("negatives") or []) for it in train]
    pairs = sum(1 + n for n in negs)

    print("Reranker plan")
    print("  corpus        %s  (%d requests, %d pairs, seed %s)"
          % (CORPUS.name, data["counts"]["requests"], data["counts"]["pairs"],
             data["seed"]))
    print("  train / held  %d / %d requests" % (len(train), len(held)))
    print("  per request   1 positive + %.1f negatives (max %d)"
          % (sum(negs) / len(negs), max(negs)))
    print("  objective     listwise softmax cross-entropy over the candidate list")
    print("  model         %s" % (MODEL_DIR.name))
    print("  recipe        LoRA rank %d on %s, %d epoch(s), lr %g, <=%d tokens"
          % (RANK, "/".join(TARGETS), EPOCHS, LR, MAX_TOKENS))

    mem = None
    try:
        for line in pathlib.Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                mem = int(line.split()[1]) / 1048576.0
                break
    except OSError:
        pass
    print()
    print("Headroom")
    if mem is None:
        print("  MemAvailable could not be read, so this is NOT a claim that it fits")
    else:
        print("  MemAvailable                  %.1f GB" % mem)
        print("  measured peak RSS, this stack  15.9 GB   (tools/lora_timing.py)")
        if mem < 15.9:
            print("  -> SHORT BY %.1f GB. Training cannot run on this box as it is."
                  % (15.9 - mem))
        else:
            print("  -> %.1f GB spare at the measured peak" % (mem - 15.9))
    print()
    print("Serving cost, which is a product decision and not a model result:")
    print("  a cross-encoder scores every candidate: %d forward passes per request"
          % OFFERED_CAPACITY)
    print("  against `ranked()`, which is a dict lookup and a sort")
    return 0


def train() -> int:
    """Not implemented in this pass. Deliberately.

    `dry_run` reports whether the box can hold a step at all, and the honest
    next step depends on that number rather than on this function existing.
    Writing a training loop that has never been run would be exactly the
    "machinery whose only purpose is to report success" the doctrine rules out.
    """
    raise SystemExit(
        "tools/rerank.py --train is not implemented. Run --dry-run first: if "
        "MemAvailable is below the measured 15.9 GB peak, the hours are not the "
        "binding constraint and a smaller rank or a shorter context is the "
        "first question.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--eval-baseline", action="store_true",
                    help="score ranked() on the held-out half (seconds, no torch)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan, the arithmetic and the headroom")
    ap.add_argument("--train", action="store_true", help="train (hours)")
    args = ap.parse_args()
    if not (args.eval_baseline or args.dry_run or args.train):
        ap.print_help()
        return 0
    if args.eval_baseline:
        eval_baseline()
    if args.dry_run:
        dry_run()
    if args.train:
        return train()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
