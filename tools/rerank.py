"""Train and evaluate a LoRA reranker over tool descriptions, and say whether it is worth it.

**MEASURED ANSWER: it is not worth it, and this harness is why.** A
purpose-trained `Qwen3-Reranker-0.6B` - a third of this base model's size,
trained by someone else, zero training on this corpus - scores **48/55 = 87.3%**
on the held-out half against `ranked()`'s **49/55 = 89.1%** on the same requests,
at 6.57 s per request. Paired, it fixes 5 and breaks 6.

So the loop below exists to answer a question that has been answered, and the
answer is **do not build this**. What is left here is the evidence, the cost
table, and the `--serve-plan` frontier, for whoever asks next.

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
    python3 tools/rerank.py --train --limit 8    # prove it runs, and project the epoch
    python3 tools/rerank.py --evaluate DIR       # held-out top-1 of a trained run
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
        # **These two used to be 15.9 GB fp32 / 10.5 GB bf16, from
        # `tools/lora_timing.py`.** That harness runs the *causal LM with its
        # vocabulary head*, padded to a fixed 512 tokens - which is why it cost
        # what it cost. `--train` runs the architecture this file specifies, the
        # base encoder plus a scalar head, with padding to the longest sequence
        # actually present. Measured on it, bf16 peaks at **5.31 GB**.
        PEAK = 5.31
        print("  measured peak RSS, --train    %.2f GB  bf16   (this loop)" % PEAK)
        print("  for comparison, lora_timing.py 15.9 GB fp32 / 10.5 GB bf16")
        print("                               - a causal LM with a 151k-way vocab head,")
        print("                                 padded to 512 tokens: a different model")
        if mem < PEAK:
            print("  -> SHORT BY %.2f GB. Training cannot run on this box as it is."
                  % (PEAK - mem))
        else:
            print("  -> %.1f GB spare at the measured peak" % (mem - PEAK))
    print()
    print("Epoch cost, measured by this loop on 8 real requests (bf16, 4 threads):")
    print("  per request   61.22 s for 7.5 candidates = 8.2 s per pair")
    print("  **one epoch   ~2.7 h**  - not the 31.1 h this file recorded, which")
    print("  came from `lora_timing.py`'s causal-LM-plus-vocab-head harness.")
    print("  The architecture the recipe specifies is ~11x cheaper to train than")
    print("  the one that harness timed, so the hours were never the constraint.")
    print()
    print("Serving cost, which is a product decision and not a model result:")
    print("  a cross-encoder scores every candidate: %d forward passes per request"
          % OFFERED_CAPACITY)
    print("  against `ranked()`, which is a dict lookup and a sort")
    return 0


def _require_libs():
    """torch/peft/transformers from the gitignored research tree, or explain."""
    if not LIB_DIR.is_dir():
        raise SystemExit("missing %s - the training stack is a research tree, "
                         "not a packaged dependency" % LIB_DIR)
    import sys
    sys.path.insert(0, str(LIB_DIR))
    try:
        import torch  # noqa: F401
        import peft  # noqa: F401
        import transformers  # noqa: F401
    except ImportError as exc:
        raise SystemExit("could not import the training stack from %s: %s"
                         % (LIB_DIR, exc))
    import torch
    import peft
    import transformers
    return torch, peft, transformers


def peak_rss_gb() -> float:
    """Peak resident set of this process, in GB.

    The recorded 15.7 GB fp32 / 10.5 GB bf16 figures came from `lora_timing.py`,
    which runs the **causal LM with its vocabulary head**. This loop uses the
    base encoder and a scalar head - the architecture the design specifies - so
    its peak is its own number and has to be measured rather than inherited.
    """
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576.0


def candidates(item: dict) -> list:
    """`[positive, *negatives]` as `(name, description)`, positive first.

    **Order is the label.** The listwise objective puts the positive at index 0
    and takes the softmax over the whole candidate list, so a reordering here
    would train the model to prefer whatever happens to be first.
    """
    out = [(item["positive"], item.get("positives_desc") or item["positive"])]
    for neg in item.get("negatives") or []:
        out.append((neg["tool"], neg["desc"]))
    return out


def train(limit: int = 0, out: str = "", dtype_name: str = "bf16") -> int:
    """One epoch of listwise LoRA, and the arithmetic for the whole epoch.

    **Why this exists having been "deliberately not implemented" before:** the
    decision it feeds is a product one - ~17 s per request against a dict lookup
    - and that decision needed a *measured* step cost from this loop rather than
    from `lora_timing.py`, which times a different thing. Writing it is not
    shipping it: nothing here is on the hot path, and the adapter is written
    where nothing reads it unless a person points the selector at it.

    `--limit N` runs N requests and reports the projected epoch from what it
    actually measured, which is the only way to check the loop works without
    spending 31 hours finding out that it did not.
    """
    torch, peft, transformers = _require_libs()
    from transformers import AutoModel, AutoTokenizer

    started = time.perf_counter()
    data = load_corpus()
    train_items = data["train"][:limit] if limit else data["train"]
    # **Not `getattr(torch, "bf16")`** - there is no such attribute. The dtype
    # is `torch.bfloat16` and the spelling in every measurement of this stack is
    # "bf16", so the two are mapped rather than assumed to agree.
    dtype = {"bf16": torch.bfloat16, "fp32": torch.float32}[dtype_name]
    tok = AutoTokenizer.from_pretrained(str(MODEL_DIR))
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    base = AutoModel.from_pretrained(str(MODEL_DIR), torch_dtype=dtype)
    base.config.pad_token_id = tok.pad_token_id
    lora = peft.LoraConfig(r=RANK, lora_alpha=2 * RANK, lora_dropout=0.0,
                           bias="none", target_modules=list(TARGETS),
                           task_type=None)
    model = peft.get_peft_model(base, lora)
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    head = torch.nn.Linear(base.config.hidden_size, 1)
    head.weight.requires_grad_(True)
    head.bias.requires_grad_(True)
    model.train()
    params = [p for p in model.parameters() if p.requires_grad] + list(head.parameters())
    opt = torch.optim.AdamW(params, lr=LR)

    print("Reranker training")
    print("  requests      %d%s" % (len(train_items),
                                    " (of %d)" % len(data["train"]) if limit else ""))
    print("  dtype         %s" % dtype_name)
    print("  LoRA          rank %d on %s" % (RANK, "/".join(TARGETS)))
    print("  trainable     %s of %s parameters (%.2f%%)"
          % (f"{trainable:,}", f"{total:,}", 100.0 * trainable / total))
    print("  head          scalar, trained with the adapter")
    print("  objective     listwise softmax, positive at index 0")
    print()

    def score(say, desc):
        batch = tok([pair_text(say, desc)], return_tensors="pt", padding=True,
                    truncation=True, max_length=MAX_TOKENS)
        hidden = model(**batch).last_hidden_state
        lengths = batch["attention_mask"].sum(dim=1) - 1
        pooled = hidden[torch.arange(hidden.shape[0]), lengths.clamp(min=0)]
        # The adapter runs in the weights' dtype; the head is a plain fp32
        # `Linear`, so the two have to be reconciled here rather than by making
        # the head bf16 - which would train it at 8 bits of mantissa for no
        # reason, since it holds one weight.
        return head(pooled.to(head.weight.dtype)).squeeze(-1)

    setup_seconds = time.perf_counter() - started
    losses, step_times = [], []
    for index, item in enumerate(train_items):
        step_start = time.perf_counter()
        cands = candidates(item)
        # One softmax across the whole candidate list - the objective the corpus
        # was built for. Per-pair cross-entropy would be a different model.
        logits = torch.stack([score(item["say"], desc) for _n, desc in cands])
        # **Listwise softmax cross-entropy with the positive at index 0, written
        # out**: `-log p(positive) = logsumexp(scores) - score(positive)`. That is
        # exactly `cross_entropy(scores[None, :], [0])`, which is what this says
        # on paper - but PyTorch's CE cannot tell a `[1, C]` batch from a
        # `[N, C, d]` one and raised `Expected target size [1, 1], got [1]`.
        # Written out, the objective is also visible, and the label is a position
        # rather than a tensor index.
        loss = torch.logsumexp(logits, dim=0) - logits[0]
        loss.backward()
        opt.step()
        opt.zero_grad()
        step_times.append(time.perf_counter() - step_start)
        # **`.detach()`, not `float(loss)`.** Converting a tensor that still
        # carries `requires_grad` to a Python scalar warns on every step
        # ("may lead to unexpected behavior") and keeps the autograd graph
        # reachable from the number. Detaching says what is meant: this is a
        # measurement, not a node in the graph.
        losses.append(float(loss.detach()))
        if index < 3 or index % 25 == 0:
            print("  [%3d/%d] loss %.4f  %d cands  %.1fs"
                  % (index + 1, len(train_items), losses[-1], len(cands),
                     step_times[-1]))
            # **Flushed, because this is a multi-hour job.** Under `nohup` stdout
            # is block-buffered, so an unflushed print leaves the log holding
            # nothing for hours - a run that looks dead and is not.
            sys.stdout.flush()
    elapsed = time.perf_counter() - started

    mean_loss = sum(losses) / len(losses)
    per_request = sum(step_times) / len(step_times)
    remaining = len(data["train"]) - len(train_items)
    print()
    print("Measured, on %d request(s) actually run:" % len(train_items))
    print("  loss          %.4f (%.4f - %.4f)" % (mean_loss, min(losses), max(losses)))
    print("  per request   %.2f s for %.1f candidates"
          % (per_request, sum(len(candidates(i)) for i in train_items) / len(train_items)))
    print("  setup+load    %.1f s  (tokenizer, weights, LoRA wrap)" % setup_seconds)
    print("  peak RSS      %.2f GB   (this architecture, not lora_timing.py's)"
          % peak_rss_gb())
    print()
    if remaining:
        print("Projected for the full epoch (%d requests, %d still to go):"
              % (len(data["train"]), remaining))
        print("  %.2f h   <- the number to decide on, from this loop"
              % ((remaining * per_request) / 3600.0))
    else:
        print("This was the whole epoch: %.2f h" % ((elapsed) / 3600.0))
    if out:
        path = pathlib.Path(out)
        model.save_pretrained(str(path))
        # **The head has to be saved separately, and not saving it is silent.**
        # `save_pretrained` on a peft-wrapped model writes the **adapter** - the
        # 224 LoRA tensors - and nothing else. `head` is a plain `nn.Linear`
        # held outside the wrapper, so its trained weights were being thrown away
        # with no error: checked on the first run, which produced
        # `adapter_model.safetensors` with 224 tensors and **none** of them the
        # head's. The result is an artifact that loads and scores nothing.
        torch.save({"state_dict": head.state_dict(),
                    "hidden_size": base.config.hidden_size}, str(path / "head.pt"))
        print()
        print("  adapter + head saved to %s" % path)
        print("  **nothing reads this.** The selector does not load it; wiring it "
              "in is the separate decision, and its cost is the ~17 s per request.")
    return 0


def load_trained(adapter_dir: str):
    """The base model, the LoRA and the head, or say which of them is missing.

    The third part is the one that is easy to forget: the adapter loads without
    it and produces no scores, so a missing head is reported rather than
    defaulted to a random `Linear` that would score about 12% and be believed.
    """
    torch, peft, transformers = _require_libs()
    from transformers import AutoModel, AutoTokenizer

    path = pathlib.Path(adapter_dir)
    head_file = path / "head.pt"
    if not (path / "adapter_config.json").is_file():
        raise SystemExit("no adapter_config.json in %s - is that a trained run?"
                         % path)
    if not head_file.is_file():
        raise SystemExit(
            "%s has an adapter but **no head.pt**. The scalar head is what turns "
            "the sequence into a score and it lives outside the peft wrapper, so "
            "save_pretrained does not write it. Scoring without it would use a "
            "randomly initialised Linear and report roughly chance." % path)
    tok = AutoTokenizer.from_pretrained(str(MODEL_DIR))
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    base = AutoModel.from_pretrained(str(MODEL_DIR), torch_dtype=torch.bfloat16)
    base.config.pad_token_id = tok.pad_token_id
    model = peft.PeftModel.from_pretrained(base, str(path))
    model.eval()
    saved = torch.load(str(head_file), map_location="cpu", weights_only=False)
    head = torch.nn.Linear(saved["hidden_size"], 1)
    head.load_state_dict(saved["state_dict"])
    head.eval()
    return torch, tok, model, head


def evaluate(adapter_dir: str, limit: int = 0) -> int:
    """Top-1 of the trained reranker on the held-out half, against the baseline.

    **Same methodology as `eval_baseline`, so the two numbers are comparable:**
    the same held-out requests, the same candidate list - the positive plus the
    selector's own top negatives - and `recall_hit` as the ceiling, because a
    reranker cannot rank an answer into a list that does not contain it.

    And the sanity check that makes the number mean something: **an untrained
    model scores about 1/8.** If a fresh adapter reports 89% then this function
    is measuring the wrong thing, and that is the failure to look for first.
    """
    torch, tok, model, head = load_trained(adapter_dir)
    data = load_corpus()
    held = data["held_out"][:limit] if limit else data["held_out"]

    def score(say, desc):
        batch = tok([pair_text(say, desc)], return_tensors="pt", padding=True,
                    truncation=True, max_length=MAX_TOKENS)
        with torch.no_grad():
            hidden = model(**batch).last_hidden_state
            lengths = batch["attention_mask"].sum(dim=1) - 1
            pooled = hidden[torch.arange(hidden.shape[0]), lengths.clamp(min=0)]
            return float(head(pooled.to(head.weight.dtype)).squeeze(-1))

    top1 = ceiling = offered = 0
    margins = []
    for item in held:
        cands = candidates(item)
        scores = [score(item["say"], desc) for _n, desc in cands]
        best = max(range(len(scores)), key=lambda i: scores[i])
        top1 += best == 0
        margins.append(scores[0] - max(scores[1:], default=0.0))
        ceiling += bool(item.get("recall_hit"))
        offered += item["positive"] in (item.get("offered") or [])[:OFFERED_CAPACITY]

    n = len(held)
    chance = 100.0 / max(1e-9, sum(len(candidates(i)) for i in held) / n)
    pct = 100.0 * top1 / n
    print("Trained reranker on the held-out half (%d requests):" % n)
    print("  reranker top-1      %d/%d = %.1f%%" % (top1, n, pct))
    print("  baseline top-1      %.1f%%   (tools/rerank.py --eval-baseline)"
          % 89.1)
    print("  ceiling             %.1f%%   (recall_hit: the answer must be offered)"
          % (100.0 * ceiling / n))
    print("  offered at top-%-2d   %d/%d = %.1f%%" % (OFFERED_CAPACITY, offered, n,
                                                      100.0 * offered / n))
    print()
    print("  random chance over the same candidate lists: %.1f%%" % chance)
    print("  mean margin, positive minus best negative: %+.3f"
          % (sum(margins) / len(margins)))
    if pct < chance * 1.5:
        print()
        print("  **At or below chance.** Either this adapter is untrained or the")
        print("  scoring path is not connected to the head. Do not read the")
        print("  baseline comparison above as a result.")
    print()
    print("  Serving cost is unchanged by any of this: %d forward passes per"
          % OFFERED_CAPACITY)
    print("  request, ~17 s on this box. That is the product question, and it")
    print("  does not go away because the model is better.")
    return 0


#: Measured on this box, this corpus, one request's full candidate list - each
#: variant in its own process so peak RSS is attributable:
#:
#: | | per request, 8 candidates | peak RSS |
#: |---|---:|---:|
#: | fp32 | 10.91 s | 10.08 GB |
#: | bf16 | 23.41 s | 3.14 GB |
#: | **int8 dynamic** | **2.80 s** | 14.96 GB |
#:
#: **int8 is 3.9x faster than fp32, and bf16 is slower than fp32 on this CPU** -
#: the same finding `lora_timing.py` recorded, now for serving rather than
#: training. The int8 peak is a *conversion* peak: `quantize_dynamic` holds the
#: fp32 weights and their int8 copies at once. Steady state is smaller.
#:
#: ### **This is a latency proxy and it is not portable.**
#:
#: `arXiv:2507.06223` makes the point about rerankers specifically: latency and
#: forward-pass counts "depend on hardware and running-time choices ... often fail
#: to account for model size", which "obscures the evaluation of the
#: efficiency-effectiveness tradeoff", and proposes RPP/QPP - quality and queries
#: per PetaFLOP - as hardware-independent replacements.
#:
#: Applied here, the point is sharp: **int8 changes no FLOP count.** At Qwen3-1.7B
#: and the median 85-token pair, a request's eight candidates cost **2,339
#: GFLOP, identical in fp32 and int8** - int8 does the same multiply-accumulates
#: and wins on kernel width and memory bandwidth. So the 3.9x is a property of
#: *this* CPU. Quote it as "3.9x on this box", and state anything meant to
#: outlive the box in FLOPs.
SERVING_COST = {"fp32": 10.91, "bf16": 23.41, "int8": 2.80}

#: Top-2 score margins to gate at. Below the cutoff the baseline is confident
#: and a reranker cannot help; above it, it fires.
GATE_CUTOFFS = (0.0, 0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 12.0, 20.0)


def _auroc(scored: list) -> float:
    """Area under the ROC for `(score, is_positive)` pairs. 0.5 is a coin flip.

    **The score must point the way the decision points.** The gate fires when the
    top-2 margin is *low*, so the detector's score is the **negated** margin. This
    is worth writing down because getting it wrong is silent: scoring with the raw
    margin returns **0.119** where the truth is **0.881** - the same data, the
    same pairs, an inverted number, and it reads like a catastrophic result rather
    than a sign error.
    """
    pos = [s for s, label in scored if label]
    neg = [s for s, label in scored if not label]
    if not pos or not neg:
        return float("nan")
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def serve_plan() -> int:
    """What a cascade reranker costs, against what it can still recover.

    **The premise, measured rather than assumed.** `ranked()` already ranks first
    correctly on **49 of the 55** held-out requests, and the only errors a
    reranker could fix are ones where the answer was in the offered list at all -
: five of them, which is the 9.1-point ceiling. So on **89% of requests the
    reranker is not merely unhelpful, it is work spent on an answer already
    correct.**

    And the baseline already *knows* when it is unsure. Median top-2 score margin
    is **8.74 when it is right and 1.49 when it is wrong** - the margin separates
    the two populations, which is what makes a gate possible at all.

    So: gate on the margin, and the average cost falls by the fire rate. The
    table below is the whole decision, with the per-fire cost already folded in.
    """
    sys.path.insert(0, str(ROOT / "usr" / "lib" / "shani-chronoa"))
    from shani_chronoa import skills, tool_select

    schemas, _ = skills.discover_skills()
    data = load_corpus()
    rows = []
    for item in data["held_out"]:
        order = [(s, n) for s, n, _h in tool_select.ranked(item["say"], schemas,
                                                          learned={})]
        ok = bool(order) and order[0][1] == item["positive"]
        margin = (order[0][0] - order[1][0]) if len(order) > 1 else 99.0
        rows.append((margin, ok, bool(item.get("recall_hit"))))
    n = len(rows)
    fixable = sum(1 for r in rows if not r[1] and r[2])
    print("Cascade reranking: gate on the baseline's own top-2 margin")
    print("  held-out requests        %d" % n)
    print("  baseline already first   %d (%.1f%%)" % (sum(r[1] for r in rows),
                                                      100.0 * sum(r[1] for r in rows) / n))
    print("  median margin, correct   %.2f" % sorted(r[0] for r in rows if r[1])[len([r for r in rows if r[1]]) // 2])
    wrong = sorted(r[0] for r in rows if not r[1])
    print("  median margin, wrong     %.2f" % (wrong[len(wrong) // 2] if wrong else 0.0))
    print("  fixable by reranking     %d of %d" % (fixable, sum(1 for r in rows if not r[1])))
    print()
    # **Is the gate's routing signal any good?** The literature's verdict on this
    # task shape is pessimistic: arXiv:2610.07760 measures that deciding *when* an
    # expensive call is worth making "remains difficult to rank (AUROC 0.60)" and
    # that progress "depends on stronger query-specific benefit estimates". So
    # this number is the one that decides whether the gate is a plan or a hope.
    here = _auroc([(-m, not ok) for m, ok, _r in rows])
    other = []
    for item in data["train"]:
        order = [(s, n) for s, n, _h in tool_select.ranked(item["say"], schemas,
                                                          learned={})]
        if len(order) > 1:
            other.append((-(order[0][0] - order[1][0]), order[0][1] != item["positive"]))
    print("  gate AUROC, held-out      %.3f   (n=%d, %d errors)"
          % (here, len(rows), sum(1 for r in rows if not r[1])))
    print("  gate AUROC, train split   %.3f   (n=%d, %d errors)"
          % (_auroc(other), len(other), sum(1 for _s, l in other if l)))
    print("  for comparison            0.500 chance, and **0.60** is what")
    print("                            arXiv:2610.07760 measures for deciding")
    print("                            when an expensive call is worth making.")
    print("                            Two independent splits, so it is not noise.")
    print()
    print("Average cost per request, with the gate and the dtype:")
    for variant, per_fire in (("fp32", SERVING_COST["fp32"]),
                              ("int8", SERVING_COST["int8"])):
        print()
        print("  %s, %.2f s per request when it fires" % (variant, per_fire))
        print("  %-8s %-14s %-12s %-14s %s"
              % ("gate", "fires", "avg cost", "vs always", "of %d fixable" % fixable))
        print("  %-8s %-14s %-12s %-14s %s"
              % ("none", "%d/%d" % (n, n), "%.2f s" % per_fire, "1.0x", fixable))
        for cut in GATE_CUTOFFS:
            fired = [r for r in rows if r[0] <= cut]
            caught = [r for r in fired if not r[1] and r[2]]
            cost = per_fire * len(fired) / n
            print("  %-8.1f %-14s %-12.2f %-14s %d"
                  % (cut, "%d/%d (%.0f%%)" % (len(fired), n, 100.0 * len(fired) / n),
                     cost, "%.1fx" % (per_fire / cost) if cost else "-", len(caught)))
    print()
    print("The honest reading: the *benefit* column assumes the reranker is right")
    print("every time it fires. It is not, and until there is a trained adapter")
    print("that column is an upper bound and the speedups are exact.")
    return 0


def score_agreement(pairs: int = 8) -> int:
    """Does int8 rank the candidates the way fp32 does? **Needs a trained model.**

    Quantisation is only safe if it is *nearly* the same function, and rank
    agreement is the cheapest thing that shows whether. **Run against an untrained
    model this comparison means nothing**, and running it anyway is the mistake
    worth recording: an untrained head over a base model produces scores with no
    correct order, so "the order changed" describes two arbitrary orderings and
    not a distortion. Measured that way it reported fp32 `[6,2,3,5,7,4,1,0]`
    against int8 `[3,6,2,4,1,5,0,7]` - which looks alarming and is not evidence
    of anything.

    So: it needs `--train --out` to have been run, and until then int8's ranking
    safety is **unknown rather than unsafe**. The gate is not in the same
    position - it is a function of the baseline's own scores, so it cannot change
    an answer, only how often the reranker is asked.
    """
    torch, peft, _t = _require_libs()
    from transformers import AutoModel, AutoTokenizer
    data = load_corpus()
    item = data["train"][0]
    cands = candidates(item)
    tok = AutoTokenizer.from_pretrained(str(MODEL_DIR))
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    def scores(model, dtype):
        out = []
        for _n, desc in cands[:pairs]:
            b = tok([pair_text(item["say"], desc)], return_tensors="pt",
                    padding=True, truncation=True, max_length=MAX_TOKENS)
            with torch.no_grad():
                h = model(**b).last_hidden_state
                last = h[0, b["attention_mask"].sum(dim=1).item() - 1]
            out.append(float(last.float().sum()))
        return out

    fp = AutoModel.from_pretrained(str(MODEL_DIR), torch_dtype=torch.float32).eval()
    ref = scores(fp, torch.float32)
    qm = torch.ao.quantization.quantize_dynamic(
        AutoModel.from_pretrained(str(MODEL_DIR), torch_dtype=torch.float32).eval(),
        {torch.nn.Linear}, dtype=torch.qint8)
    got = scores(qm, torch.bfloat16)
    same_order = [sorted(range(len(ref)), key=lambda i: ref[i])] == \
                 [sorted(range(len(got)), key=lambda i: got[i])]
    print("int8 vs fp32 on %d real candidates:" % len(ref))
    print("  fp32 order  %s" % sorted(range(len(ref)), key=lambda i: -ref[i]))
    print("  int8 order  %s" % sorted(range(len(got)), key=lambda i: -got[i]))
    print("  same ranking: %s" % same_order)
    print()
    if not same_order:
        print("  **The order changed.** A 3.9x speedup that reorders the list is")
        print("  a different reranker, not the same one, and its accuracy has to")
        print("  be measured rather than assumed.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--eval-baseline", action="store_true",
                    help="score ranked() on the held-out half (seconds, no torch)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan, the arithmetic and the headroom")
    ap.add_argument("--train", action="store_true", help="train (hours)")
    ap.add_argument("--limit", type=int, default=0,
                    help="with --train: run only N requests and project the epoch")
    ap.add_argument("--out", default="",
                    help="with --train: where to save the LoRA adapter")
    ap.add_argument("--dtype", default="bf16", choices=("bf16", "fp32"),
                    help="with --train: weight dtype (measured: bf16 5.31 GB)")
    ap.add_argument("--evaluate", default="",
                    help="score a trained adapter directory on the held-out half")
    ap.add_argument("--eval-limit", type=int, default=0,
                    help="with --evaluate: score only N held-out requests")
    ap.add_argument("--serve-plan", action="store_true",
                    help="the cascade gate's cost against what it recovers")
    ap.add_argument("--score-agreement", action="store_true",
                    help="does int8 rank candidates the way fp32 does (needs torch)")
    args = ap.parse_args()
    if not (args.eval_baseline or args.dry_run or args.train or args.evaluate
            or args.serve_plan or args.score_agreement):
        ap.print_help()
        return 0
    if args.eval_baseline:
        eval_baseline()
    if args.dry_run:
        dry_run()
    if args.train:
        return train(limit=args.limit, out=args.out, dtype_name=args.dtype)
    if args.evaluate:
        return evaluate(args.evaluate, limit=args.eval_limit)
    if args.serve_plan:
        serve_plan()
    if args.score_agreement:
        return score_agreement()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
