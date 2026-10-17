"""One tool result bigger than the whole window is clipped, not sent to fail.

Measured 2026-10-15 on an 8192-token llama-server: `read_text_file` of a 28 KB
file was 11,894 tokens on its own; every older turn was dropped, the newest
was kept whole, and the server answered 400 "exceeds the available context
size". With the clip the same request was answered.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import local_llm  # noqa: E402


def _turn(result):
    call = {"id": "1", "type": "function", "function": {"name": "read_text_file", "arguments": "{}"}}
    return [{"role": "system", "content": "s" * 2000},
            {"role": "user", "content": "read it"},
            {"role": "assistant", "content": "", "tool_calls": [call]},
            {"role": "tool", "tool_call_id": "1", "content": result}]


def _budget(n_ctx):
    return int((n_ctx - local_llm.REPLY_RESERVE) * local_llm.CHARS_PER_TOKEN)


def _size(messages):
    return sum(len(str(m.get("content") or "")) for m in messages)


def test_an_oversized_result_is_clipped_to_fit_and_says_so():
    big = "head line\n" + "x" * 40000 + "\nKEY: the end of the file\n"
    out = local_llm.fit_to_context(_turn(big), 8192)
    result = out[-1]["content"]
    assert _size(out) <= _budget(8192)
    assert result.startswith("head line") and "KEY: the end of the file" in result
    assert "left out to fit the model's memory" in result and "start_line" in result


def test_a_result_that_fits_is_untouched():
    small = "x" * 3000
    assert local_llm.fit_to_context(_turn(small), 8192)[-1]["content"] == small


def test_the_person_and_the_model_are_never_clipped():
    """Only tool output is shortened: a long user message is the person talking."""
    msgs = _turn("y" * 30000)
    msgs[1] = {"role": "user", "content": "please " * 3000}
    out = local_llm.fit_to_context(msgs, 8192)
    assert out[1]["content"] == msgs[1]["content"]
