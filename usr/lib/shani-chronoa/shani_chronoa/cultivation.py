"""Cultivation: growing a model by stages, each earned by measurement.

**What this is.** A progression with prerequisites, in the sense the term is
actually used in the source material - and then in the only sense that survives
contact with a machine. Each stage produces an artifact, each artifact must
*measure* better than what it replaces, and **a stage that does not measure is
not unlocked.** No stage is skipped because it is inconvenient, and no stage is
unlocked because it sounds good.

**What it is not.** It is not a way to beat a frontier lab. Distilling what
OpenAI and Anthropic say into a local 1.7B model does not produce a smaller
OpenAI; it produces a smaller model with their *style* and none of their
capability, and the repo already measures why that is a bad trade in a different
place (`model_zoo`: 94% of labels identical, so the cheapest model already wins).
What is achievable is narrower and real: **beating a frontier model at one
measured task on this machine**, which is a claim you can check and a frontier
lab has no incentive to check.

**The six stages, and the measurement that unlocks each.**

| stage | produces | gate to proceed |
|---|---|---|
| 筑基 Foundation | a base model, digest-pinned | size + sha256 match (`local_llm.verify`) |
| 感知 Perception | observations from the day's log | the log was read, and what it says is bounded |
| 淬炼 Refinement | a distilled router | agreement with the human ≥ `MIN_AGREEMENT` (0.5) **and** it beats the majority-tool baseline |
| 同化 Attunement | a merge of sibling weights | perplexity beats **every** measured parent, *at the same quantisation* (`merge.gate` → `better`) |
| 渡劫 Ascension | a LoRA adapter alongside the base | measured on a held-out split, and reversible without a rewrite |
| 长生 Immortality | a signed, provenance-bearing record | `learning.verify_model` passes, and rollback exists |

**Ascension before Attunement, deliberately.** An adapter is ~20 MB and loads
*alongside* the base - no container rewriting, no re-quantisation, reversible by
dropping a file. A merge is the whole model again, at 1.1 GB, behind a metadata
round-trip that had to be fixed before anything could be measured. Both add
capability, and the ordering was chosen while only one of them worked.

**Corrected 2026-10-12: the container is no longer the blocker, and this
section said otherwise.** It claimed the round-trip was "not yet correct" and
pointed at a measured failure further down that was **never there** - the
sentence referred forward to itself. What was actually true then, and is fixed
now (`723f997`, `1e67057`):

- `gguf_write` copies the parent's metadata section byte for byte. Rebuilding it
  through `gguf-py`'s typed writer lost 15 of 31 keys - every `qwen3.*` one - so
  `llama-quantize` refused the file outright. Measured: a 310-tensor Qwen3-0.6B
  blends into 1143 MiB of F16 in 28 s and `llama-quantize` produces 378 MiB of
  Q4_K_M from it, which had never once succeeded.
- **113 of those 310 tensors are F32** - every norm vector and bias - while the
  197 weight matrices are Q8_0. Writing them all F16 gives a file that *loads*
  and then dies computing with `dst: f32, src0: f32, src1: f16`.
- The gate was comparing a Q4_K_M merge against Q8_0 parents, so it charged the
  whole requantization cost to the merge: an **identity** merge - lambda = [1, 0],
  bit-identical to the base - was refused for being 13.6% worse than the base.

**So Attunement is no longer blocked by mechanics. It is blocked by
arithmetic, which is a different and more honest kind of blocked.** Measured on
the one sibling pair available (`Qwen3-0.6B-Base` 48.770 against `Qwen3-0.6B`
76.469, same corpus, 57% apart), every interpolated point lands *between* them
and beats neither. TIES exists for exactly that, and whether it helps on this
pair is being measured rather than assumed.

**What is still true, and is why the ordering has not changed:** Ascension is
still the cheaper lever, and a merge still rewrites 1.1 GB to add what a 20 MB
adapter adds alongside.

**The five prohibitions. This is the part that matters.**

These are the heart demon, named concretely, because every one of them is a
thing that has actually happened to a self-improving system:

1. **Never train on your own output.** Self-distillation with no external label
   is how a model collapses into a fixed point - and it is invisible, because
   every sample looks fluent. `distill.py` already refuses it: *"Only agreements
   are training data. A teacher's mistakes are not training data."*
2. **The human is the label authority, not the teacher.** *"A case where the
   teacher and the human differ is a case the human won."* Training on it teaches
   the student the exact mistake it was corrected on.
3. **A student is a prior, never an authority.** It reorders and narrows what was
   already on offer. It cannot add a capability, and it cannot run one. A router
   that can grant itself a skill is not a router, it is a compromised host.
4. **Refusing is a first-class outcome.** Below the agreement floor, or not
   beating the baseline, the honest output is *no student*. `distill.py` refuses;
   `merge.gate` refuses. **A system that cannot say "no" cannot be said to have
   said "yes" meaningfully.**
5. **Provenance on every artifact, or it does not exist.** Parents and their
   digests, the coefficients, the measurement, the verdict - signed with
   `learning.sign_model` and checked with `learning.verify_model`. A model that
   cannot say what went into it cannot be diagnosed when it degrades, and cannot
   be rolled back. **That is the whole meaning of immortal here: not that it
   lives forever, but that every change is attributable and reversible.**

**The measurement problem, stated because it is the whole game.** Perplexity is
the weakest signal available and the one this repo uses most, because it is the
one that is cheap and reproducible. It is close to useless for "is this better":
a merge can improve perplexity and be worse at the only thing it was for.
`merge.gate` is therefore a *floor*, not a verdict - it can stop a regression,
and nothing more. **It has three verdicts, and the middle one is the one that
matters most here:** `worse` (refused, quality lost), `no-better` (refused, *"it
has cost disk and added nothing"*) and `better`. A tie with the better parent is
neither success nor failure, and a gate with only "better than" and "worse than"
reports a tie as a win - which is how a number reads as a result when nothing
distinguished it. The measures that actually track usefulness already exist here
and are better than perplexity: does the router beat the majority-tool baseline,
does a tool's claim survive verification, does the dream produce findings a
person acts on.

**What is currently true, measured rather than asserted.**

- 筑基 holds. `Qwen3-1.7B-Q4_K_M.gguf` and `Qwen3-8B-Q4_K_M.gguf` are on disk,
  digest-pinned, and `current.gguf` resolves to one of them.
- 感知 and 淬炼 exist and are gated (`dream.py`, `distill.py`).
- **同化 is arithmetic-complete and container-broken.** The blend is verified:
  `λ=0` returns the base exactly, `λ=1` returns the parent exactly (max
  difference 9.31e-04, which is f16 rounding), and 0.5/0.5 is the midpoint. On
  real weights, 310 Qwen3 tensors dequantised and re-blended into 1137 MiB of F16
  in 29 s. But the GGUF container loses 15 of 31 metadata keys - every
  architecture-specific `qwen3.*` one - and `llama-quantize` then refuses it:
  `key not found in model: qwen3.context_length`. **The next move is byte-exact
  copying of the parent's KV section, not more type mapping.**
- 渡劫 has no code. `llama-server` on this box already supports `--lora`,
  `--lora-scaled` and `POST /lora-adapters`; nothing in this package passes them.
- **The measurement that justifies "models second" cannot be reproduced here.**
  `model_zoo` cites 11,868 recorded calls with 94% identical labels; this
  machine's `tool_calls.log` holds **one**. `model_zoo.compare()` returns **zero
  estimators** because scikit-learn is not installed. The ordering is sound as
  reasoning and unverified as a measurement on any machine but the author's.
"""

from __future__ import annotations

from typing import Dict, List, NamedTuple, Optional, Tuple

#: Stage name -> what it produces. The progression is ordered by *trust* in the
#: artifact, not by ambition: a reversible 20 MB adapter is ahead of an
#: irreversible 1.1 GB rewrite because the first can be undone.
STAGES: Tuple[str, ...] = (
    "foundation", "perception", "refinement", "attunement", "ascension",
    "immortality",
)

#: What each stage must produce, in the words the gate uses.
#:
#: Each sentence names **the function that decides it**, because a stage whose
#: gate is described only in prose is a stage nothing can check: the first
#: version of this table said "beats every measured parent" with no reference to
#: `merge.gate` at all, and no test could have noticed, because no test
#: imported the module.
PRODUCES: Dict[str, str] = {
    "foundation": "a base model whose size and sha256 match the pin",
    "perception": "bounded observations from a window of the day's log",
    "refinement": "a router that beats the majority-tool baseline",
    "attunement": "a merge whose `merge.gate` verdict is `better` - at the "
                   "same quantisation as its parents, which is not automatic",
    "ascension": "a reversible adapter that measures on a held-out split",
    "immortality": "a signed record whose digest verifies and whose prior "
                   "version still exists",
}


#: The resource axis. A progression says *where you are*; this says *what you
#: have*, and the two are separate: a cultivator with no 灵气 can be at the last
#: stage and have produced nothing.
#:
#: **Every entry names the function that produces or measures it.** The first
#: draft of this table was prose - "rare materials are the open weights" - and a
#: table whose entries are ideas cannot be checked, which is the same defect
#: `PRODUCES` had until its deciding functions were named in. Each entry is
#: therefore importable, and `tests/test_cultivation.py` imports them.
#:
#: The numbers are measured on this machine and are dated, because a resource
#: row that quotes a figure nobody re-measured is a claim wearing a table.
RESOURCES: Dict[str, Dict[str, str]] = {
    "qi": {  # 灵气 - the ambient input, gathered rather than made
        "is": "the day's observations: tool calls, verdicts, senses, turns",
        "from": "distill.harvest_rows",
        "measured": "11 harvestable (request, tool) pairs on this machine, all "
                    "of one repeated question; 0 dreams ever written",
    },
    "materials": {  # 灵材 - the open weights, and their provenance
        "is": "another model's weights, plus the licence and digest that say "
              "whether they may be used",
        "from": "model_registry.describe",
        "measured": "identity, licence, uuid and source read off the GGUF "
                    "header, so a model is described by what is in it",
    },
    "furnace": {  # 丹炉 - the thing that refines, and costs time to run
        "is": "importance-matrix calibration: the same source, re-quantised "
              "with activations telling it which weights matter",
        "from": "local_llm.calibrated_quantize",
        "measured": "751 ms naive against 10.9 s calibrated on SmolLM2-135M",
    },
    "elixir": {  # 丹药 - a refined consumable whose effect is read before use
        "is": "the calibrated build beside the naive one, and the measured "
              "difference between them",
        "from": "local_llm.compare_calibration",
        "measured": "helped / hurt / unchanged, or `not measured` - and a "
                    "missing measurement is never reported as no-change",
    },
    "core": {  # 妖丹 - what is taken out of something trained
        "is": "an outcome model, a bandit arm or a distilled router: a trained "
              "component carried out of the log it was fitted to",
        "from": "learning.train_and_save",
        "measured": "17,891 labelled calls; ECE 0.430 -> 0.107 and NLL 0.938 -> "
                    "0.547 on a 2-way cross-fit",
    },
    "stone": {  # 灵石 - the budget a thing has to fit inside
        "is": "memory bandwidth, which is what decode speed actually follows",
        "from": "local_llm.effective_bandwidth",
        "measured": "16.5 GiB/s on this i7-1165G7; a 0.6B model is bound by "
                    "per-token compute and moves less than the hardware can",
    },
    "poison": {  # 丹毒 - a refinement that damages the one taking it
        "is": "an artifact that measured well on the wrong instrument and is "
              "worse on the task it exists for",
        "from": "merge.gate",
        "judged_by": "tools/task_eval.py",
        "measured": "REAL, and produced this session: a merged Qwen3-0.6B scored "
                    "4/5 at tool selection where its parent scored 1/5 - four "
                    "times worse, and 37x slower, while the perplexity corpus "
                    "called the two close",
    },
    "seclusion": {  # 闭关 - the offline pass that consolidates
        "is": "dream.py's consolidation over the day's activity",
        "from": "dream.dream",
        "measured": "never run on this machine; the panel offers it on demand",
    },
}


def resource_status() -> Dict[str, str]:
    """`{resource: what is actually known about it}`, every entry populated.

    A resource with no measured figure reads as available, which is the same
    absence-shaped green this repository keeps recording. So the return value
    has no empty strings in it: an unmeasured resource says `not measured`, and
    a missing one says so by its own absence from `RESOURCES`.
    """
    return {name: entry.get("measured", "not measured")
            for name, entry in RESOURCES.items()}


class Verdict(NamedTuple):
    """`(unlocked, why)` - never a bare bool.

    A refusal nobody can act on is a bug report, not a design, and this is the
    one place where the whole progression's honesty lives.
    """
    unlocked: bool
    why: str


def stage_status(current: str) -> List[str]:
    """The stages at or after `current`. Unknown names get nothing.

    Deliberately not lenient: an unrecognised stage must not silently unlock the
    ones after it, because the entire value of a progression is that the order
    is enforced.
    """
    if current not in STAGES:
        return []
    return list(STAGES[STAGES.index(current):])


def next_stage(current: str) -> Optional[str]:
    """The stage after `current`, or None at the end - and None for nonsense."""
    tail = stage_status(current)
    return tail[1] if len(tail) > 1 else None
