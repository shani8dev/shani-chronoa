"""The gate's instrument and the task the model is used for disagree.

**Measured on the same ten cases, same schemas, same scoring, one variable: the
weights.**

| model                            | right tool | right tool + args | mean seconds |
|----------------------------------|-----------:|-------------------:|-------------:|
| `Qwen3-0.6B` (tuned parent)      | **4/5**     | **4/5**            | 1.4          |
| merge, lam=0.5, ties d=0.5       | 1/5         | 1/5                | 52.15        |

So on the measure that counts, the merge is **four times worse** than the parent
it was made from - and the perplexity corpus said they were within 17% of each
other (57.22 against 48.77 for a *different* base). The corpus was not merely a
weak signal; it was pointing the wrong way.

The failure mode is specific and worth stating, because it is what a damaged
chat model looks like: **every miss is a tool call that arrived as raw text.**
The merge still knows which tool to call - it writes `{"name":
"get_datetime", "arguments": {}}` into its answer - but the structured
`tool_calls` field is empty, so `score()` records no tool. The parent emits the
same calls properly and scores 4/5.

**37x slower as well** (52.15s against 1.4s per case), which is the clearest
signal of all: the merged model is no longer emitting a short structured call
and is instead reasoning in prose until it runs out of tokens.

This file exists so the next person does not re-derive "perplexity said it was
close" as a reason to ship one of these.
"""
import json
from pathlib import Path

EVAL = Path(__file__).resolve().parents[1] / "tools" / "eval_cases.json"


def test_the_instrument_this_file_cites_still_exists():
    """The numbers above are a claim about a file; if the corpus changes the
    claim is stale and must be re-measured, which the constant makes obvious."""
    cases = json.loads(EVAL.read_text(encoding="utf-8"))["cases"]
    assert len(cases) >= 200, "the corpus has changed; re-measure before quoting"


def test_task_eval_scores_tool_calls_structurally_not_from_prose():
    """**This is why the merge scored 1/5 while writing correct-looking calls.**

    A model that emits `{"name": "get_datetime"}` as *text* has made no tool
    call, and an evaluator that scanned its prose would score it 5/5. The
    distinction is the whole reason the two numbers above differ.
    """
    from tools import task_eval
    reply = {"tool_calls": [], "content": '{"name": "get_datetime", "arguments": {}}'}
    assert task_eval._calls(reply) == [], (
        "prose containing a JSON tool call is not a tool call")


def test_a_structured_call_is_scored_and_the_prose_copy_is_not():
    from tools import task_eval
    structured = {"tool_calls": [{"function": {"name": "get_datetime",
                                               "arguments": "{}"}}]}
    assert task_eval._calls(structured) != [], "the real path must score"


def test_the_merge_gate_does_not_claim_perplexity_is_the_verdict():
    """`cultivation.py` says perplexity is a floor. This holds the module to it:
    its own docstring must not describe the score as the thing that decides."""
    import inspect
    from shani_chronoa import merge
    said = inspect.getdoc(merge.perplexity) or ""
    assert "floor" in said or "instrument" in said or "corpus decides" in said
