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
| 同化 Attunement | a merge of sibling weights | perplexity beats **every** measured parent (`merge.gate`) |
| 渡劫 Ascension | a LoRA adapter alongside the base | measured on a held-out split, and reversible without a rewrite |
| 长生 Immortality | a signed, provenance-bearing record | `learning.verify_model` passes, and rollback exists |

**Ascension before Attunement, deliberately.** An adapter is ~20 MB and loads
*alongside* the base - no container rewriting, no re-quantisation, reversible by
dropping a file. A merge is the whole model again, at 1.1 GB, behind a metadata
round-trip that is not yet correct (see the measured failure below). Both add
capability; only one of them is currently trustworthy, so the progression starts
where the trustworthy thing is.

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
and nothing more. The measures that actually track usefulness already exist here
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
PRODUCES: Dict[str, str] = {
    "foundation": "a base model whose size and sha256 match the pin",
    "perception": "bounded observations from a window of the day's log",
    "refinement": "a router that beats the majority-tool baseline",
    "attunement": "a merge that beats every measured parent",
    "ascension": "a reversible adapter that measures on a held-out split",
    "immortality": "a signed record whose digest verifies and whose prior "
                   "version still exists",
}


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
