"""Perplexity is scored on prose, and prose is the wrong question.

`merge.perplexity` falls back to `local_llm._CORPUS` when no corpus is given,
and that corpus is **this project's own documentation**. A base language model
predicts prose better than a chat model does - measured on Qwen3-0.6B:
`Qwen3-0.6B-Base` 48.770 against `Qwen3-0.6B` 76.469 - so a gate scored on it is
answering "which model is better at prose" and reporting the answer as quality.

That does not make the corpus wrong. Two models must be scored on identical
bytes for the numbers to be comparable, and a corpus that could go missing from
a package would turn a gate into a no-op - both real, and both reasons the
corpus is fixed in code rather than sampled.

The defect is that it is the **only** instrument, and it is `cultivation.py`'s
own point made concrete: *"perplexity is a floor, not a verdict - it can stop a
regression, and nothing more."*
"""
from shani_chronoa import local_llm, merge


def test_the_default_corpus_is_the_projects_own_prose():
    assert local_llm._CORPUS, "an empty corpus would make every score None"
    lowered = local_llm._CORPUS.lower()
    # It reads like documentation, which is the finding: it is not a
    # representation of what the assistant is asked to do.
    assert any(w in lowered for w in
               ("perception", "quantization", "consensus", "assistant"))


def test_the_gate_accepts_a_different_corpus_so_the_floor_is_not_the_ceiling():
    """The escape hatch has to exist and has to be reachable, or "perplexity is
    a floor" is advice with no mechanism behind it."""
    import inspect
    signature = inspect.signature(merge.perplexity)
    assert "corpus" in signature.parameters
    source = inspect.getsource(merge.perplexity)
    assert "_CORPUS" in source, "the fallback is the documented default"
    assert "corpus" in source, "and it must be overridable"


def test_the_tool_corpus_the_assistant_is_actually_for_exists():
    """`tools/eval_cases.json` is 226 real requests with their correct tool
    calls, and it is not packaged - which is why perplexity cannot reach it and
    why a merge has never been scored on it."""
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "tools" / "eval_cases.json"
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    assert len(cases) >= 200
    assert all("say" in c for c in cases)
    # The case shapes are three, and a corpus that assumed one would mislabel
    # the other two: a tool call, a deliberate no-call, and an ask.
    assert any("expect" in c for c in cases)
    assert any(c.get("none") for c in cases), (
        "cases expecting no tool call are real cases; dropping them would bias "
        "a corpus toward always calling something")
