"""The cloud context window is the model's own reported length, not a guess.

Before this, `context_window` matched the model name against a static fragment
table and fell to a modest 32k default for anything it did not recognise - so a
router like `kilo-auto/free` and any model the table never heard of were
mis-gauged (measured live: Nemotron's real 1M window read as 32k, which
summarised every long turn away early). Now each model's `/models` row carries
its context length, captured as the provider is listed and read here first.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import cloud_llm  # noqa: E402


def _clear():
    cloud_llm._MODEL_CONTEXT.clear()


def test_a_reported_length_is_read_from_the_models_row():
    for row, expected in (
        ({"id": "x", "context_length": 256000}, 256000),       # kilo-style
        ({"id": "x", "context_window": 128000}, 128000),       # context_window
        ({"id": "x", "max_model_len": 131072}, 131072),        # vLLM
        ({"id": "x", "top_provider": {"context_length": 1048576}}, 1048576),  # OpenRouter nested
        ({"id": "x"}, 0),                                       # nothing reported
        ({"id": "x", "context_length": 512}, 0),               # too small to be a window
        ({"id": "x", "context_length": "not a number"}, 0),    # junk
    ):
        assert cloud_llm._row_context(row) == expected, row


def test_the_dynamic_length_wins_over_the_name_table_and_default():
    _clear()
    # Unknown to the name table -> default, before anything is listed.
    assert cloud_llm.context_window("nvidia/nemotron-3.5-super-vl:free") == cloud_llm.DEFAULT_CLOUD_CONTEXT
    # Once the provider reported it, the real window is used.
    cloud_llm._MODEL_CONTEXT["nvidia/nemotron-3.5-super-vl:free"] = 1_000_000
    assert cloud_llm.context_window("nvidia/nemotron-3.5-super-vl:free") == 1_000_000
    # And it wins even over a name-table fragment match.
    cloud_llm._MODEL_CONTEXT["qwen-special"] = 999_999
    assert cloud_llm.context_window("qwen-special") == 999_999
    _clear()


def test_the_table_and_default_are_the_fallbacks_when_nothing_was_reported():
    _clear()
    assert cloud_llm.context_window("claude-3-5-sonnet") == 200_000       # table
    assert cloud_llm.context_window("some-model-nobody-knows") == cloud_llm.DEFAULT_CLOUD_CONTEXT


def test_listing_captures_the_lengths(monkeypatch):
    """list_models fills the context cache from each row, keyed by model id."""
    _clear()
    body = {"data": [
        {"id": "a/big", "context_length": 1_000_000},
        {"id": "b/mid", "context_window": 128_000},
        {"id": "c/none"},
    ]}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            import json
            return json.dumps(body).encode()

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    ids, why = cloud_llm.list_models("kilo", api_key="")
    assert ids == ["a/big", "b/mid", "c/none"] and why == ""
    assert cloud_llm.context_window("a/big") == 1_000_000
    assert cloud_llm.context_window("b/mid") == 128_000
    # c/none reported nothing, so it falls back - not captured at a wrong value.
    assert "c/none" not in cloud_llm._MODEL_CONTEXT
    _clear()
