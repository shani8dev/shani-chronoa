"""A reranker corpus built from the 220 cases and the REAL candidate lists.

**Why this is a reranker corpus and not a selection corpus.** The first
framing was wrong and the data says so. Of 220 cases over 204 tools, **192
tools appear exactly once** - so "hold out a request" also holds out its tool,
and a classifier trained on the rest can never learn a description it has only
one example of. Selection cannot generalise here.

Ranking can. Every tool is a piece of natural language - name, description,
parameters - so a model that scores *(request, tool description)* transfers to
a tool it has never seen. That is the only framing the 220 cases support, and
it is also the one the measurements demand: offered-set recall is already
**94.5%**, and the remaining misses sit at **rank 8-41** - the right tool is
retrieved and then crowded out. Retrieval is solved; ranking is not.

**Why negatives are drawn from `ranked()` and not at random.** The confusers
that crowd out the answer are the ones the current scorer already rates highly.
Sampling negatives uniformly would build a corpus the model passes trivially
and would not touch the defect it is meant to fix.

**And why `ranked()` is called with `learned={}`.** `ranked()` reads this
machine's live tool-call log when its `learned` argument is `None`
(`learned_weights()` -> `weights_from_tracker(_tracker())`), so the corpus would
be a function of **whoever ran the builder and what they had asked for since**.
Measured: two builds of the same commit, minutes apart with no edit between them,
reported **212** and **211** positives inside the offered set, because a tool call
had happened in between. A training artefact that moves with the operator's
history cannot be named by a commit, cannot be reproduced on another machine,
and quietly carries the machine's own log into the training set. An explicit
empty dict is the point at which "no learned weights" is said out loud.

Each case yields one positive and up to `NEGATIVES` hard negatives, so 220
requests become thousands of scored pairs - which is what a reranker needs.
"""

import json
import pathlib
import random
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "usr" / "lib" / "shani-chronoa"))

CASES = REPO / "tools" / "eval_cases.json"
OUT = REPO / "tools" / "reranker_corpus.json"

SEED = 20261013
NEGATIVES = 12
HOLDOUT = 0.25


def _description(tool: dict) -> str:
    fn = tool.get("function", tool)
    desc = (fn.get("description") or "").strip()
    name = fn.get("name", "")
    props = list(((fn.get("parameters") or {}).get("properties") or {}))
    return f"{name}: {desc} (arguments: {', '.join(props)})" if props else f"{name}: {desc}"


def build():
    from shani_chronoa import tools as T
    from shani_chronoa import tool_select as S

    cases = json.loads(CASES.read_text())["cases"]
    usable = [c for c in cases
              if c.get("expect") and c["expect"][0].get("tool")]
    rows = []
    stats = {"offered": 0, "not_offered": 0}

    for case in sorted(usable, key=lambda c: c["say"]):
        say = case["say"]
        want = case["expect"][0]["tool"]
        # `learned={}` on purpose - see the module docstring. `None` would read
        # this machine's live call log and make the file drift as the operator
        # uses the assistant.
        scored = S.ranked(say, T.TOOLS, learned={})
        offered = [n for _s, n, _h in scored[:S.DEFAULT_LIMIT]]
        descs = {S._name(t): _description(t) for t in T.TOOLS}

        if want in offered:
            stats["offered"] += 1
        else:
            # A recall failure is still a corpus row: the positive is scored
            # against the same distractors the selector produced, which is the
            # only place a reranker can recover it. Dropping these would remove
            # every case where retrieval is the problem - the population a
            # reranker exists for - and report a corpus of only the cases the
            # selector already got right.
            stats["not_offered"] += 1

        rank = {n: i for i, (_s, n, _h) in enumerate(scored)}
        negatives = [n for n in offered if n != want]
        # **Rank first, then truncate.** The first version shuffled, took
        # `NEGATIVES` of the shuffle and *then* sorted - so the kept subset was
        # random and the comment claiming otherwise was false. The hard
        # negatives are the entire reason this corpus exists; keeping twelve at
        # random is twelve irrelevant distractors plus a claim about them.
        negatives = sorted(negatives, key=lambda n: rank.get(n, 10 ** 6))
        negatives = negatives[:NEGATIVES]

        rows.append({
            "say": say,
            "positive": want,
            "positives_desc": descs.get(want, want),
            "negatives": [{"tool": n, "desc": descs.get(n, n)} for n in negatives],
            "offered": offered,
            "recall_hit": want in offered,
        })
    return rows, stats


def _shown(path: pathlib.Path, base: pathlib.Path) -> str:
    """The path as written when it is inside the repo, absolute when it is not.

    `relative_to` raises rather than answering, and a builder that crashes on
    the way to printing its own output is a builder whose one useful line is the
    line it cannot print.
    """
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def main() -> int:
    rows, stats = build()
    rng = random.Random(SEED + 1)
    rng.shuffle(rows)
    cut = int(len(rows) * (1 - HOLDOUT))
    train, held = rows[:cut], rows[cut:]

    pairs = sum(1 + len(r["negatives"]) for r in rows)
    doc = {
        "about": ("Reranker corpus: score (request, tool description) pairs. "
                  "Generated by tools/build_reranker_corpus.py, seed "
                  f"{SEED}. Negatives come from the selector's own top-"
                  f"{NEGATIVES}, not from uniform sampling - the tools that "
                  "crowd out the answer are the ones it already rates highest."),
        "limit": ("No case carries ARGUMENTS, so this can evidence tool "
                  "RANKING only. `ascension_gate` scores right tool AND right "
                  "arguments; nothing in this corpus can satisfy that."),
        "seed": SEED,
        "counts": {"requests": len(rows), "pairs": pairs,
                   "train": len(train), "held_out": len(held)},
        "recall": stats,
        "train": train,
        "held_out": held,
    }
    OUT.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"{len(rows)} requests -> {pairs} scored pairs "
          f"({len(train)} train / {len(held)} held out)")
    print(f"  positives the selector already ranks inside its offer: "
          f"{stats['offered']}, outside it: {stats['not_offered']}")
    print(f"  wrote {_shown(OUT, REPO)}")
    print("\nLIMIT: no arguments in any case - this evidences RANKING only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
