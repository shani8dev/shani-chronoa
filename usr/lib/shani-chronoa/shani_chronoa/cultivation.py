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

**Corrected 2026-10-12: 渡劫 is no longer "has no code".** That line was stale
the moment `adapter.py` landed (`e0c2b7c`). It now carries `is_adapter`,
`mismatches`, `check` and `server_args`, and the check is the point: a LoRA
adapter names a base it was trained for and is refused against any other, so the
fit is verified before llama-server is asked to load it. `TERMS["ascension"]`
and the `trial` row are written against that reality.

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

**The three axes.** `STAGES` is *where you are*, `RESOURCES` is *what you
have*, and `TERMS` is the **genre vocabulary those two are written in** - kept
because a term naming nothing here would be decoration, so each one either
names the function that decides it or is refused with the reason. The
progression is the genre's own shape (a ladder with prerequisites, bounded
resources, and a trial at each advance) used honestly: the trial really refuses,
and "immortal" really means attributable and reversible, which is the only
part of the metaphor that survives contact with a machine.

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


#: The cultivation-novel vocabulary, in the sense the genre uses it.
#:
#: **This table exists under the same rule as `RESOURCES`: a term that names
#: nothing here is a decorative flourish, so every entry names the *function*
#: that decides it, or is declared `NOT_BUILT` with the reason.** That is the
#: line between using the genre as a shared metaphor (which it genuinely is -
#: a progression with prerequisites and resource limits) and pasting on Chinese
#: for flavour. A refusal here ("no real analogue, and a fake one would be a
#: lie") is more useful than an invented mapping.
#:
#: The terms are **the genre's shared lexicon** - the ones that appear across
#: essentially every cultivation story, so they are the load-bearing ones -
#: not a quotation from any single novel, and deliberately **not** names of
#: specific characters, sects or artefacts. Specific names from specific novels
#: would be decoration pretending to be structure.
#:
#: Each row is `{is, from, measured}` shaped like a resource so one accessor can
#: serve both tables, and the test imports every `from`. `from` is the module
#: function name when one exists; otherwise it is the literal `"NOT_BUILT"` and
#: `not_built` carries the reason.
TERMS: Dict[str, Dict[str, str]] = {
    "qi": {  # 灵气 qi - the ambient energy that is gathered, not created
        "is": "the raw signal available to train from: a machine's own day's "
              "observations, verdicts and calls",
        "from": "distill.harvest_rows",
        "measured": "the same measured figure as the qi resource: what a day "
                    "of use on this machine actually yields",
    },
    "foundation": {  # 筑基 zhǔjī - the base built before anything else
        "is": "the pinned base checkpoint every later stage is measured "
              "against, verified before use rather than assumed",
        "from": "local_llm.verify",
        "measured": "size + sha256 must match the pin; an unverified base "
                    "makes every later number meaningless",
    },
    "perception": {  # 感知 gǎnzhī - perceiving the world's state
        "is": "bounded observations of the machine and the day, each carrying "
              "how sure it is and where it came from",
        "from": "senses.discover_senses",
        "measured": "a fixed registry of senses, each consent-gated, because a "
                    "perception nobody can turn off is surveillance",
    },
    "refinement": {  # 淬炼 cuìliàn - refining what was gathered
        "is": "the distilled router: raw qi gathered and refined into a "
              "component that narrows and reorders what is already offered",
        "from": "distill.train_router",
        "measured": "trained only on human-agreed rows, and it must beat the "
                    "majority-tool baseline or there is no student",
    },
    "attunement": {  # 同化 tonghua - fusing separate things into one
        "is": "the weight merge: sibling checkpoints interpolated into one, "
              "which must measure better than every parent to count",
        "from": "merge.gate",
        "measured": "three verdicts, and only `better` ships - a tie is not a "
                    "win, it is a no-better refusal",
    },
    "ascension": {  # 渡劫 du jie - surviving the trial to advance
        "is": "the adapter loaded *alongside* the base, so the trial does not "
              "rewrite what already works",
        "from": "adapter.check",
        "measured": "REFUSED on the honest instrument. `adapter.check` passes - "
                    "lumen-mouth-lora FITS Qwen3-1.7B-Q4_K_M and is refused "
                    "against the 0.6B with 168/196 mismatches - and *fitting* is "
                    "not *earning*. Measured on task_eval, 5 cases, select "
                    "config: base alone 4/5 at 4.65 s/case, base + adapter 4/5 "
                    "at 7.65 s/case, zero refusals either way, and both miss "
                    "the same one. Same accuracy, 65% slower: the adapter adds "
                    "nothing it has been shown to earn, so Ascension stays "
                    "locked. It is reversible - dropping the file undoes it - "
                    "which is exactly why trying it was cheap",
    },
    "immortality": {  # 长生 changsheng - lasting by not rotting
        "is": "a signed, provenance-bearing record: every change attributable "
              "and reversible, so a degradation can be traced and undone",
        "from": "learning.verify_model",
        "measured": "digest verifies and the prior version still exists; "
                    "immortal here means attributable + reversible, not eternal",
    },
    "furnace": {  # 丹炉 danlu - the thing that transforms under heat
        "is": "the quantiser: same source weights, re-quantised with an "
              "importance matrix telling it which bits matter",
        "from": "local_llm.calibrated_quantize",
        "measured": "the calibrated build beside the naive one, so the "
                    "difference is an actual measurement and not a claim",
    },
    "elixir": {  # 丹药 danyao - a consumed product whose effect is checked
        "is": "the refined artifact itself - the outcome model - and the "
              "measured difference it made against the log it came from",
        "from": "learning.train_and_save",
        "measured": "cross-fit ECE and NLL on held-out calls; a model that "
                    "loses to a constant is not an elixir, it is a bottle",
    },
    "core": {  # 妖丹 yaodan - the kernel taken from a trained thing
        "is": "the distilled component carried out of the log it was fitted "
              "to: a router, a bandit arm, an outcome model",
        "from": "learning.cross_validate",
        "measured": "scored by cross-validation, so a core is judged on data "
                    "it was not forged on",
    },
    "stone": {  # 灵石 lingshi - the unit things are priced and weighed in
        "is": "the budget a thing must fit inside: memory, bandwidth, context",
        "from": "local_llm.effective_bandwidth",
        "measured": "measured bandwidth, not a spec sheet, so what the machine "
                    "can actually do is what the ladder plans against",
    },
    "seclusion": {  # 闭关 biguan - the closed-door pass that consolidates
        "is": "the offline consolidation over the day's activity, away from "
              "any live request",
        "from": "consolidation.consolidate",
        "measured": "run on a schedule or on demand, never on the reply path",
    },
    "heart_demon": {  # 心魔 xinmo - the self-corruption a closed loop invites
        "is": "the failure this whole design refuses: training on your own "
              "output until the model collapses into a fixed point",
        "from": "distill.MIN_AGREEMENT",
        "measured": "the human-agreement floor is what makes self-collapse "
                    "visible; without an outside label there is nothing to see",
    },
    "deviation": {  # 走火入魔 zouhuo rumo - losing the path mid-cultivation
        "is": "an artifact that measured well on the wrong instrument and is "
              "worse at the thing it exists for",
        "from": "merge.perplexity",
        "judged_by": "tools/task_eval.py",
        "measured": "perplexity can be improved while tool-selection gets "
                    "worse - measured here, which is why the gate is a floor",
    },
    "kinship": {  # 血脉 xuemai - the lineage an artefact inherits
        "is": "provenance: which parents, which licences, which digests, and "
              "the signed record tying them together",
        "from": "learning.sign_model",
        "measured": "a model that cannot say what went into it cannot be "
                    "traced when it degrades nor rolled back",
    },
    "reversal": {  # 逆转 nizhuan - undoing an advancement
        "is": "rollback: the prior version exists and swapping back is a "
              "move, not a rebuild",
        "from": "local_llm.use",
        "measured": "the design choice that put ascension before attunement: "
                    "a reversible thing is tried before an irreversible one",
    },
    "technique": {  # 功法 gongfa - the method, separate from the result
        "is": "the harness that decides how a turn is offered to the model: "
              "tool selection, not every schema at once",
        "from": "tool_select.select_tools",
        "measured": "selecting the relevant tools is what lets a small model "
                    "answer at all - the harness, not the weights",
    },
    "perception_gate": {  # 明察 mingcha - seeing, but not overstepping
        "is": "consent: a perception is refused unless its switch is on, and "
              "withheld again if consent is withdrawn",
        "from": "permissions.decide",
        "measured": "a gate that fails closed - a permission nobody can grant "
                    "is a permission that is always off, which is safe",
    },
    "artifact": {  # 器物 qiwu - a thing that carries its own meaning
        "is": "a model described by what is inside it - identity, licence, "
              "digest, source - read off the file itself",
        "from": "model_registry.describe",
        "measured": "described from the GGUF header, so the description is a "
                    "fact about the bytes and not a claim about them",
    },
    "transmission": {  # 传承 chuancheng - passing it on without losing it
        "is": "portable knowledge: what a fresh machine cannot otherwise get - "
              "arms, model, router - exported and re-imported with digests",
        "from": "learning.export_knowledge",
        "measured": "exported knowledge verifies on import; a bundle that "
                    "does not verify is refused rather than adopted",
    },
    "counter": {  # 灵契 lingqi - the contract bound to a task
        "is": "the boundary fence around untrusted content, so a tool result "
              "or a fetched page cannot speak as the system",
        "from": "provenance.fence",
        "measured": "fenced by a per-turn nonce, so content cannot forge its "
                    "own terminator",
    },
    "price": {  # 天机 tianji - what a thing costs to use
        "is": "the per-call cost of spending a model: tokens against a known "
              "price, so use is accountable",
        "from": "usage.price",
        "measured": "an unknown model prices as unknown, never as zero - a "
                    "free number is a fabricated one",
    },
    "chance": {  # 机缘 jiyuan - an opportunity, taken or missed
        "is": "the bandit over tools: an arm is pulled on measured worth and "
              "the estimate is kept honest against what it returned",
        "from": "learning.evaluate_bandit",
        "measured": "reported with whether it ranks better than chance; on "
                    "this machine it does not, and the tool says so",
    },
    "trial": {  # 天劫 tianjie - the catastrophe the advance must survive
        "is": "the gate itself: a held-out measurement that can refuse the "
              "advancement",
        "from": "merge.gate",
        "measured": "a stage that does not measure is not unlocked; the "
                    "refusal is a first-class outcome, not an oversight",
    },
    # --- terms the genre has and this codebase deliberately does not --------
    #
    # These are the honest refusals. Each has no real analogue here, and a
    # fabricated mapping would be a lie wearing a table - so the reason is
    # recorded instead of a function.
    "sworn_oath": {
        "is": "a vow that binds fate - the genre's mystical contracts between "
              "people, enforced by the story rather than by a mechanism",
        "from": "NOT_BUILT",
        "not_built": "no analogue: nothing here binds a person by magic, and "
                     "the real commitments (consent, permissions) are code, "
                     "not vows",
    },
    "sect": {
        "is": "a lineage or school that transmits a technique across "
              "generations, with its own hierarchy and rules",
        "from": "NOT_BUILT",
        "not_built": "deliberately not a here-is-how to form a guild model; "
                     "the whitelist is a fixed set of tools, not a lineage "
                     "you join",
    },
    "technique_tier": {
        "is": "a graded ladder of named techniques, from entry spell to the "
              "artefact a master carries",
        "from": "NOT_BUILT",
        "not_built": "the real ladder is the measured model ladder (see the "
                     "stone resource), and it moves on evidence, not on names",
    },
}


#: The cosmological register, kept **separate from `TERMS` on purpose**.
#:
#: `TERMS` is the vocabulary of *progress* - where you are and what you have.
#: `ELEMENTS` is the vocabulary of *the world the progress happens in*:
#: emptiness, time, undifferentiated chaos, the elemental cycle, weather, and
#: the light/dark duality. Mixing them would blur the very distinction the two
#: tables exist to hold, so they are two tables under one rule.
#:
#: **Same discipline as everywhere else: every entry names a function that
#: imports, or is refused as NOT_BUILT with the reason.** This matters more for
#: this register than for the progression one, because cosmological terms are
#: the most tempting to fake - they sound profound and map onto anything if you
#: squint. 混沌 in particular is the word a writer reaches for when the design
#: has not been thought through, so it gets the strictest treatment here: it is
#: refused, with the reason, rather than decorated onto whatever was already
#: built.
#:
#: Genre lexicon again, not any one novel's proper nouns.
ELEMENTS: Dict[str, Dict[str, str]] = {
    "void": {  # 虚空 xūkōng - emptiness; the space things move through
        "is": "instant retrieval across everything already stored: every "
              "conversation answerable by meaning without walking a history",
        "from": "conversation_store.search",
        "measured": "hybrid meaning + FTS5 search over the whole store, so "
                    "reaching a year-old turn costs a query, not a traversal",
    },
    "time": {  # 时光 shíguāng - the record of what was, read back
        "is": "reading the past: the tool-call log re-read for what it teaches, "
              "and a goal run's own age used to decide if it is stale",
        "from": "goals.reconcile_stale_runs",
        "measured": "a run whose last activity is old is demoted to paused and "
                    "stamped, rather than shown as 'in progress' forever",
    },
    "chaos": {  # 混沌 húndùn - the undifferentiated; what is before form
        "from": "NOT_BUILT",
        "is": "the pre-differentiated state, from which order is supposed to "
              "arise",
        "not_built": "deliberately refused: 'chaos' is the word a design reaches "
                     "for when it has not been thought through, and mapping it "
                     "onto the raw log would dignify undifferentiated input "
                     "that this repo exists to refine. qi is the real input; "
                     "chaos is its absence of structure, and structure is the "
                     "job, not a resource",
    },
    "five_elements": {  # 五行 wǔxíng - the elemental cycle
        "is": "the closed vocabularies of state a thing may be in: installed / "
              "not installed / running / not running / could-not-ask - each "
              "real, mutually exclusive, and closed so a row may not invent a "
              "fourth",
        "from": "gui.surfaces.desktop.EVIDENCE_WORDS",
        "measured": "five evidence words and a closed set; collapsing them "
                    "would let 'could not ask' read as 'not installed', which is "
                    "the confident wrong answer this repo keeps catching",
    },
    "wind": {  # 风 fēng - the ambient, always-moving periphery
        "is": "the senses: the machine's own background readings, polled on "
              "their intervals, each consent-gated",
        "from": "senses.discover_senses",
        "measured": "a fixed registry of senses, polled on schedule; the "
                    "ambient input, distinguished from a deliberate request",
    },
    "lightning": {  # 雷电 léidiàn - sudden external events striking the system
        "is": "the trigger event types: screen lock, power state, USB, network, "
              "schedule - transitions the system did not ask for",
        "from": "triggers.EVENT_TYPES",
        "measured": "a closed set of event types that fires on a *transition*; "
                    "a rule armed on a state that never changes does nothing",
    },
    "yin": {  # 阴 yīn - the dark half; what must not act
        "is": "the read-only half of the tool split: what the assistant may "
              "observe but not change",
        "from": "capabilities.READ_ONLY_TOOLS",
        "measured": "a declared read-only set; explore mode refuses anything "
                    "outside it, so the dark half is a boundary, not a mood",
    },
    "yang": {  # 阳 yáng - the light half; what may act
        "is": "the mutating half: the tools that change the machine, each "
              "reaching its own consent gate",
        "from": "capabilities.MUTATING_TOOLS",
        "measured": "a declared mutating set; every one of these meets a "
                    "consent key before it runs, so the light half is gated",
    },
}


def element_status() -> Dict[str, str]:
    """`{element: what is actually known about it}` - the cosmological register,
    same no-empty-answers contract as `term_status` / `resource_status`."""
    out: Dict[str, str] = {}
    for name, entry in ELEMENTS.items():
        if entry.get("from") == "NOT_BUILT":
            out[name] = "NOT BUILT: " + entry.get("not_built", "no analogue")
        else:
            out[name] = entry.get("measured", "not measured")
    return out


def term_status() -> Dict[str, str]:
    """`{term: what is actually known about it}` - same shape as
    `resource_status`, over the genre vocabulary.

    Mirrors the resource contract: no empty answers. A term with no analogue
    says `NOT BUILT` and why; a term with an analogue quotes the measurement.
    """
    out: Dict[str, str] = {}
    for name, entry in TERMS.items():
        if entry.get("from") == "NOT_BUILT":
            out[name] = "NOT BUILT: " + entry.get("not_built", "no analogue")
        else:
            out[name] = entry.get("measured", "not measured")
    return out


#: The realm ladder, reversed: the later a stage, the fewer paths remain open.
#: `next_stage` walks it forward; this says what each boundary *costs*.
#:
#: Deliberately not a flat "harder -> harder" list. Each entry names the thing
#: that becomes irreversible, because that is the property the ordering rests
#: on and the reason ascension sits before attunement.
BOUNDARIES: Dict[str, str] = {
    "foundation": "before this: nothing exists to measure. After: every later "
                  "number is relative to a verified base.",
    "perception": "after this the system can see; that is also the point at "
                  "which seeing must become consent-gated, because an "
                  "observation nobody can switch off is surveillance.",
    "refinement": "after this the system reorders what is on offer. It gains "
                  "no new power - a router that could authorise a call would "
                  "be a compromised host, not a router.",
    "ascension": "the first reversible advance: a 20 MB adapter loaded "
                 "alongside the base, dropped without a rewrite. Tried before "
                 "attunement precisely because it can be undone.",
    "attunement": "the first irreversible advance: the whole model again, at "
                  "1.1 GB, behind a metadata round-trip. Earned after "
                  "ascension has shown a cheap advance can be measured at all.",
    "immortality": "after this a change is attributable and reversible. That "
                   "is the whole meaning: not that it lives forever, but that "
                   "every change can be traced and undone.",
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
