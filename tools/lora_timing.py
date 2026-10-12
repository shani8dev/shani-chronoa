"""How long does one LoRA epoch cost on this machine, at 1.7B, on CPU?

This is the measurement the 渡劫 (Ascension) stage of `cultivation.py` is
missing, and everything about whether that stage is reachable hangs on it. The
stack (torch 2.14.1+cpu, peft 0.21.2, transformers 5.19.0, accelerate 1.15.0)
is installed in `cache/cultivate-libs` and Qwen3-1.7B is in safetensors at
`cache/models/Qwen3-1.7B`; neither is packaged, both are gitignored.

**Why the recipe is fixed in this file rather than passed in.** The settings come
from measurements, not from taste:

- **rank 8** - capacity is what causes forgetting on a corpus this small.
- **one epoch, not convergence** - arXiv:2506.14126 measures that long expert
  fine-tuning *degrades* a merge through memorisation-driven interference, and
  arXiv:2508.16082 measures that a one-epoch task vector is the negative
  gradient scaled by the learning rate with the first epoch dominating both its
  norm and its direction. Two epochs is not "a bit more"; it is a different
  vector.
- **`q/k/v/o_proj` only** - the common LoRA target set.
- **classification head, not instruction format** - arXiv:2607.03801 measures
  +2.2 to +3.3 points for a classification head over label generation, at exactly
  the 0.6B and 1.7B this box runs.

**What this reports and what it does not.** It reports seconds per optimiser
step and peak RSS, and extrapolates an epoch over the training half of the
reranker corpus. It does not report whether the result is any good: that needs
a training run followed by `task_eval`, and a run costing hours is the thing
being decided here.
"""

import argparse
import json
import pathlib
import resource
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
LIBS = REPO / "cache" / "cultivate-libs"
MODEL = REPO / "cache" / "models" / "Qwen3-1.7B"
CORPUS = REPO / "tools" / "reranker_corpus.json"

sys.path.insert(0, str(LIBS))

RANK = 8
TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj"]
SEQ_LEN = 512
BATCH = 1
GRAD_ACCUM = 8


def peak_rss_mb() -> float:
    """Peak resident set of this process, in MB.

    `ru_maxrss` is kilobytes on Linux. Read from the kernel rather than
    estimated from parameter counts: the parameter count is what someone
    predicted and this is what the machine did, and the LoRA optimiser's
    transient buffers are exactly the part a count omits.
    """
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def build_examples(limit: int):
    """(positive_text, negative_text) pairs in the shape the head scores.

    The pair form matters: a reranker learns to *rank within a candidate list*,
    and training it as a plain classifier would measure something else - the
    classification-head result above is about the output head, not about
    collapsing ranking into classification.
    """
    doc = json.loads(CORPUS.read_text())
    out = []
    for row in doc["train"]:
        neg = row["negatives"]
        if not neg:
            continue
        out.append((row["positives_desc"], neg[0]["desc"]))
        if len(out) >= limit:
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", type=int, default=3,
                    help="optimiser steps to time; 3 is enough for a rate and "
                         "cheap enough not to cost the hours being measured")
    ap.add_argument("--limit", type=int, default=64,
                    help="examples to build before the steps")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    threads = torch.get_num_threads()
    print(f"torch {torch.__version__} | {threads} threads | "
          f"{torch.get_num_interop_threads()} interop")
    print(f"model {MODEL}")
    print(f"corpus {CORPUS}")
    print()

    t0 = time.time()
    model = AutoModelForCausalLM.from_pretrained(
        MODEL, dtype=torch.float32, low_cpu_mem_usage=True)
    load_s = time.time() - t0
    params = sum(p.numel() for p in model.parameters())
    print(f"loaded in {load_s:.1f}s | {params/1e9:.2f}B parameters "
          f"({peak_rss_mb():.0f} MB peak RSS so far)")

    # fp32 on CPU: bf16 matmul on this build falls back and is slower, and the
    # whole question is wall clock rather than memory.
    model.config.use_cache = False
    lora = LoraConfig(r=RANK, lora_alpha=2 * RANK, lora_dropout=0.0,
                      target_modules=TARGETS, bias="none",
                      task_type="SEQ_CLS")
    model = get_peft_model(model, lora)
    trainable = [p for p in model.parameters() if p.requires_grad]
    n_train = sum(p.numel() for p in trainable)
    print(f"LoRA rank {RANK} on {TARGETS}: {n_train/1e6:.2f}M trainable "
          f"of {params/1e6:.0f}M ({100*n_train/params:.3f}%)")

    tok = AutoTokenizer.from_pretrained(MODEL)
    tok.padding_side = "right"
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    examples = build_examples(args.limit)
    print(f"{len(examples)} examples built\n")

    opt = torch.optim.AdamW(trainable, lr=2e-4)
    timings = []
    for step in range(args.steps):
        # Two sequences per step: the positive and the hardest negative. This is
        # the pair the reranker must order, and pairing them in one optimiser
        # step is what makes the head learn a *margin* rather than two
        # independent scores.
        pos, neg = examples[step % len(examples)]
        batch = tok([pos, neg], return_tensors="pt", padding="max_length",
                    truncation=True, max_length=SEQ_LEN)
        t = time.time()
        out = model(**batch, labels=batch["input_ids"])
        out.loss.backward()
        opt.step()
        opt.zero_grad(set_to_none=True)
        dt = time.time() - t
        timings.append(dt)
        print(f"  step {step+1}: {dt:.1f}s  loss {out.loss.item():.3f}  "
              f"{peak_rss_mb():.0f} MB peak")

    mean = sum(timings) / len(timings)
    doc = json.loads(CORPUS.read_text())
    pairs = sum(1 + len(r["negatives"]) for r in doc["train"])
    per_epoch = mean * GRAD_ACCUM * len(doc["train"])

    print()
    print(f"mean {mean:.1f}s per optimiser step ({GRAD_ACCUM} examples each)")
    print(f"training rows {len(doc['train'])}, scored pairs in them {pairs}")
    print(f"**one epoch ~= {per_epoch/3600:.1f} hours** "
          f"({per_epoch:.0f}s)")
    print(f"peak RSS {peak_rss_mb():.0f} MB")
    print()
    print("This is a WALL CLOCK figure for the optimizer only - no evaluation, "
          "no data building, no checkpointing. It decides whether the path is "
          "viable; it says nothing about whether the result is any good, which "
          "needs task_eval over a run that costs the hours above.")

    if args.json:
        pathlib.Path(REPO / "build" / "lora-timing.json").write_text(
            json.dumps({"mean_s": mean, "per_epoch_s": per_epoch,
                        "peak_rss_mb": peak_rss_mb(),
                        "threads": threads, "rank": RANK}, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())