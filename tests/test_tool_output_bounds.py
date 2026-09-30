"""An elision the model cannot act on is not a compression, it is a loss.

The six agent frameworks surveyed for this (`openai-agents-python`,
`pydantic-ai`, `autogen`, `agno`, `smolagents`, `langgraph`) do not truncate
tool output at all - full results go into the next request in every one. The
two that do, goose and codex, do it the same way and in the same order:

- goose `crates/goose-cli/src/session/streaming_buffer.rs:69` - a 50-line cap
  and a 20-line preview, spilling the full text to a temp file and printing the
  path. Its `parse_positive_lines` rejects `0` so a bad env var cannot hide
  everything behind a temp file.
- codex `codex-rs/tui/src/tool_output.rs:19` - `PREVIEW_LINES = 3`,
  `MAX_PREVIEW_LINE_BYTES = 16 * 1024`, the byte cap applied *before* wrapping,
  with the reason in the source: "a single tool-result line can be megabytes
  long".

Both report the elided count honestly rather than dropping it silently, and
both bound the input before the expensive transform.

Measured on the code as it stood, through `Assistant.build_messages()`, on the
one shape these two rules exist for - `printf 'a%.0s' $(seq 1 200000)`, a
single 200,000-character line:

    before: the model received a 1,235-character elision whose entire advice was
    "call the tool again or narrow the request", reported the loss in characters
    only, and carried no parallel record of what it replaced. The 199,000
    omitted bytes were on disk nowhere and reachable by no means at all.

That last part is the defect the tests below are about. For a deterministic
result the advice cannot work: `read_text_file` on the same path returns the
same bytes and is elided identically, every turn, forever.

Nothing here is on by default. `Assistant.build_messages()` calls
`compression.compress(messages)` with no summarizer, so summarization is
opt-in and the elision path is byte-identical to the one these tests were first
written against.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import compression  # noqa: E402


def _tool(content: str, call_id: str = "1") -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def _user(content: str) -> dict:
    return {"role": "user", "content": content}


def _aged(result: str, call_id: str = "big") -> "list[dict]":
    """`result` old enough to be eligible, so the window is not what is tested."""
    return [_tool(result, call_id)] + [_user(f"q{i}") for i in range(
        compression.KEEP_RECENT_MESSAGES + 2)]


def _old_results(count: int, size: int = 5000) -> "list[dict]":
    """`count` oversized tool results, all of them *past* the protected window.

    The filler turns are load-bearing, not decoration. With only the results in
    the list, `cutoff` is zero and nothing is eligible, so a fixture written that
    way passes whatever `summarize_tool_results` does - including nothing at all.
    That is the "a test that cannot fail" trap, and it is why every summarization
    assertion here is paired with an assertion that a summary was produced.
    """
    rows = [_tool("r" * size, str(i)) for i in range(count)]
    rows += [_user(f"q{i}") for i in range(compression.KEEP_RECENT_MESSAGES + 2)]
    return rows


def _record(messages: "list[dict]", index: int = 0) -> dict:
    return messages[index][compression.COMPRESSION_FIELD]


class _FakeSummarizer:
    """Records what it was asked, returns something shorter and recognisable."""

    def __init__(self, reply: str = "SUMMARY: kept the numbers and the ids") -> None:
        self.calls: "list[tuple[str, str]]" = []
        self.reply = reply

    def __call__(self, prompt: str, text: str) -> str:
        self.calls.append((prompt, text))
        return self.reply


class _ExplodingSummarizer:
    def __call__(self, prompt: str, text: str) -> str:
        raise RuntimeError("ollama is not reachable")


# ---------------------------------------------------------------------------
# TASK 1 - bound the input before the transform
# ---------------------------------------------------------------------------


class TestBytesPerLineIsCapped:
    """codex: a single tool-result line can be megabytes long.

    The cap has to be applied to the pieces, before head and foot are joined.
    Applied to the joined string instead, the assembly has already done the work
    the cap exists to avoid.
    """

    def test_a_line_longer_than_the_cap_is_cut(self):
        text = "x" * (compression.MAX_LINE_CHARS * 3)
        capped, hidden = compression._cap_lines(text)
        assert len(capped.split("\n")[0]) < len(text)
        assert hidden == 1

    def test_a_partially_shown_line_counts_as_hidden(self):
        """codex's rule. A line shown in part is a line not given.

        Counting only fully-dropped lines is what makes a truncation read as
        completeness, which is the failure this file is about.
        """
        text = "x" * (compression.MAX_LINE_CHARS + 10) + "\nshort"
        _, hidden = compression._cap_lines(text)
        assert hidden == 1, "the shortened line was not counted as hidden"

    def test_a_result_within_the_cap_is_untouched(self):
        text = "short line\nanother"
        capped, hidden = compression._cap_lines(text)
        assert (capped, hidden) == (text, 0)

    def test_the_cap_is_applied_before_the_result_is_assembled(self, monkeypatch):
        # `HEAD_CHARS` is 600, well under `MAX_LINE_CHARS`, so the cap cannot
        # bite on today's numbers - which means this is the only way to see
        # that it is applied to the pieces rather than after the join. Raising
        # the retention budget is the realistic way to reach it: a caller who
        # wants more context will raise `HEAD_CHARS`.
        monkeypatch.setattr(compression, "HEAD_CHARS", compression.MAX_LINE_CHARS * 4)
        body = compression._elide("x" * (compression.MAX_LINE_CHARS * 4))
        lines = [line for line in body.split("\n") if line]
        assert max(len(line) for line in lines) < compression.MAX_LINE_CHARS + 200, (
            "a retained line came out longer than the per-line cap, so the cap "
            "is being applied after the pieces are joined"
        )
        assert "more characters on this line" in body


class TestTheUntruncatedTextIsOnDisk:
    """goose: keep the preview, spill the rest, print the path."""

    def test_the_spilled_file_is_byte_identical_to_the_original(self, tmp_path,
                                                                monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        original = "HEADER: read /var/log/app.log\n" + "log line\n" * 900 \
            + "TAIL-ERROR: permission denied on /etc/shadow\n"
        out = compression.compress(_aged(original))
        path = _record(out)["spill_path"]
        assert Path(path).read_text() == original, (
            "the spill is not the original text, so it recovers nothing"
        )

    def test_the_path_is_in_the_text_the_model_reads(self, tmp_path, monkeypatch):
        """Not just in the parallel record. The record is provenance; the note is
        what the model is actually reading when it decides whether to give up."""
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        out = compression.compress(_aged("x" * 200_000))
        assert _record(out)["spill_path"] in out[0]["content"]

    def test_the_spill_follows_XDG_STATE_HOME(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        out = compression.compress(_aged("x" * 200_000))
        assert Path(_record(out)["spill_path"]).is_relative_to(tmp_path), (
            "the spill escaped the state directory, so a test run - or any "
            "process with XDG_STATE_HOME set - writes into a real home"
        )

    def test_the_same_result_is_not_written_twice(self, tmp_path, monkeypatch):
        """A content-addressed name means a result elided on every turn of a long
        conversation costs one file, not one file per turn."""
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        rows = _aged("x" * 200_000)
        for _ in range(3):
            compression.compress(rows)
        spills = list((tmp_path / "shani-chronoa" / compression.SPILL_SUBDIR).iterdir())
        assert len(spills) == 1, f"wrote {len(spills)} copies of one result"

    def test_a_failed_spill_is_not_fatal_and_says_so(self, tmp_path, monkeypatch):
        """`compress()` is on the path of every request. A read-only state
        directory must degrade to an elision with no path, not raise."""
        blocked = tmp_path / "blocked"
        blocked.mkdir()
        blocked.chmod(0o500)
        monkeypatch.setenv("XDG_STATE_HOME", str(blocked))
        try:
            out = compression.compress(_aged("x" * 200_000))
            assert "elided" in out[0]["content"]
            assert "spill_path" not in _record(out)
            assert "untruncated text is at" not in out[0]["content"]
        finally:
            blocked.chmod(0o700)

    def test_an_absurdly_large_result_is_declined_rather_than_written(self,
                                                                     tmp_path,
                                                                     monkeypatch):
        # Trading a bounded context problem for an unbounded disk problem is not
        # a fix. The limit is a degradation, not a silent cap.
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        monkeypatch.setattr(compression, "MAX_SPILL_CHARS", 100)
        assert compression._spill("x" * 1000) is None

    def test_no_half_written_file_is_left_behind(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        compression._spill("x" * 200_000)
        leftovers = [p.name for p in
                     (tmp_path / "shani-chronoa" / compression.SPILL_SUBDIR).iterdir()
                     if p.name.endswith(".tmp") or p.name.startswith(".")]
        assert not leftovers, f"a reader could see a partial file: {leftovers}"


class TestTheElidedCountIsDisclosed:
    """goose's `... (N more lines)` and codex's `omitted` counter."""

    def test_a_single_enormous_line_reports_one_omitted_line(self, tmp_path,
                                                              monkeypatch):
        # Before this existed, the note said "1-line result" and reported only a
        # character count, so the single case where almost everything was lost
        # was the one that read as though nothing was.
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        out = compression.compress(_aged("a" * 200_000))
        assert "1 line not shown in full" in out[0]["content"]
        assert _record(out)["omitted_lines"] == 1

    def test_it_does_not_double_count_one_line_cut_at_both_ends(self):
        """The head and the foot of a result shorter than `HEAD_CHARS +
        FOOT_CHARS` come out of the *same* logical line. Counting both cut
        points reports two lines for a one-line result."""
        assert compression._omitted_line_count("a" * 200_000, "a" * 600,
                                               "a" * 400) == 1

    def test_many_dropped_lines_are_counted(self):
        # Asserted against the arithmetic rather than a magic range: the lines
        # the head and foot span are lines the model still has, so they are not
        # omitted however many characters of them survive.
        content = "\n".join(f"line {i}" for i in range(900))
        head = content[:compression.HEAD_CHARS]
        foot = content[-compression.FOOT_CHARS:]
        spanned = (content.count("\n", 0, compression.HEAD_CHARS) + 1
                   + content.count("\n", len(content) - compression.FOOT_CHARS,
                                   len(content)) + 1)
        assert compression._omitted_line_count(content, head, foot) == \
            900 - spanned + 2, "the 2 partial cut-through lines are not counted"

    def test_a_result_within_budget_reports_nothing_dropped(self):
        assert compression._omitted_line_count("a\nb\nc", "a\nb\nc", "a\nb\nc") == 0


class TestTheParallelRecord:
    """agno: compression is lossless, inspectable and reversible.

    Chronoa cannot keep the original *on the wire* beside the summary - the
    message dicts go to the provider verbatim - so the record carries provenance
    instead. This is the property that makes the reduction reversible: the
    digest has to actually identify the bytes the spill file holds.
    """

    def test_every_reduced_result_carries_a_record(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        out = compression.compress(_aged("x" * 200_000))
        assert compression.COMPRESSION_FIELD in out[0]

    def test_the_record_reports_the_original_size_and_line_count(self, tmp_path,
                                                                 monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        original = "HEADER\n" + "body line\n" * 900 + "TAIL\n"
        out = compression.compress(_aged(original))
        record = _record(out)
        assert record["original_chars"] == len(original)
        assert record["original_lines"] == original.count("\n") + 1

    def test_the_digest_identifies_the_bytes_the_spill_holds(self, tmp_path,
                                                            monkeypatch):
        """This is the reversibility claim, and it is checkable rather than
        aspirational: open the file the note names, hash it, and it must be the
        bytes the record says were replaced."""
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        original = "x" * 200_000 + "\nTRAILER"
        out = compression.compress(_aged(original))
        record = _record(out)
        on_disk = Path(record["spill_path"]).read_text()
        assert hashlib.sha256(on_disk.encode()).hexdigest()[:16] == record["sha256"]
        assert record["sha256"] != hashlib.sha256(out[0]["content"].encode()).hexdigest()[:16], \
            "the digest identifies the reduced text, not the original"

    def test_the_record_is_absent_from_an_untouched_result(self):
        rows = [_tool("x" * 1999, str(i)) for i in range(2)] + [_user("hi")]
        for message in compression.compress(rows):
            assert compression.COMPRESSION_FIELD not in message

    def test_the_record_costs_far_less_than_what_it_saves(self, tmp_path,
                                                          monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        original = "x" * 200_000
        out = compression.compress(_aged(original))
        overhead = len(repr(_record(out)))
        assert overhead < len(original) / 100, (
            f"the record costs {overhead} characters to save {len(original)}; "
            "provenance is not worth that"
        )

    def test_recompressing_a_compressed_result_does_not_lose_the_record(self,
                                                                       tmp_path,
                                                                       monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        once = compression.compress(_aged("x" * 200_000))
        twice = compression.compress(once)
        assert _record(twice)["kind"] == "elision"


# ---------------------------------------------------------------------------
# TASK 2 - compression into a parallel field, never overwriting
# ---------------------------------------------------------------------------


class TestTheDualTrigger:
    """agno `manager.py:69-103`: token-based OR count-based, default 3."""

    def _results(self, count: int, size: int = 5000) -> "list[dict]":
        """Results large enough to be worth compressing, few enough that the size
        trigger has to be checked separately from the count trigger.

        3 x 5,000 is under `TOTAL_TOOL_BUDGET_CHARS`, so the fixture below is
        provably exercising the count trigger alone - which is the point of
        having two triggers.
        """
        return [_tool("r" * size, str(i)) for i in range(count)] + [_user("hi")]

    def test_the_count_trigger_fires_at_three_uncompressed_results(self):
        assert not compression.should_summarize(self._results(2))
        assert compression.should_summarize(self._results(3))

    def test_small_results_do_not_accumulate_toward_the_count_trigger(self):
        """Three trivial results are not three results worth summarizing; agno's
        count is of *uncompressed* results, which is only meaningful if the
        threshold that makes a result worth compressing is applied first."""
        assert not compression.should_summarize(self._results(20, 10))

    def test_the_size_trigger_fires_independently_of_the_count(self):
        one = [_tool("r" * 5000, "1"), _user("hi")]
        assert not compression.should_summarize(one), \
            "the size trigger fired on a single in-budget result"
        huge = [_tool("r" * 40_000, "1"), _user("hi")]
        assert compression.should_summarize(huge), \
            "one enormous result did not fire the size trigger"

    def test_a_result_already_compressed_is_not_counted_again(self, tmp_path,
                                                              monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        rows = _old_results(3)
        once = compression.summarize_tool_results(rows, _FakeSummarizer())
        assert any(m.get(compression.COMPRESSION_FIELD) for m in once), \
            "nothing was summarized, so this trigger was never exercised"
        assert compression.should_summarize(once) is False, (
            "already-summarized results kept tripping the count trigger, so "
            "every turn would pay for another summarization pass"
        )


class TestTheRubric:
    def test_it_preserves_the_facts_a_summary_must_never_drop(self):
        prompt = compression.SUMMARIZE_PROMPT
        for required in ("numbers", "dates", "Entities", "Identifiers"):
            assert required in prompt, f"the rubric does not mention {required}"

    def test_it_removes_what_is_safe_to_remove(self):
        prompt = compression.SUMMARIZE_PROMPT
        for forbidden in ("Hedging", "Meta-commentary", "Formatting artifacts"):
            assert forbidden in prompt

    def test_it_is_not_just_summarize_this(self):
        assert len(compression.SUMMARIZE_PROMPT) > 500

    def test_the_rubric_is_what_the_summarizer_is_actually_given(self, tmp_path,
                                                                  monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        summarizer = _FakeSummarizer()
        compression.summarize_tool_results(_old_results(3), summarizer)
        assert summarizer.calls, "no summarization was attempted"
        assert summarizer.calls[0][0] == compression.SUMMARIZE_PROMPT


class TestSummarizationNeverOverwritesTheRecord:
    def test_the_input_list_is_untouched(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        rows = _old_results(3)
        compression.summarize_tool_results(rows, _FakeSummarizer())
        assert rows[0]["content"] == "r" * 5000

    def test_the_reduced_result_still_carries_the_original_size(self, tmp_path,
                                                                monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        rows = _old_results(3)
        out = compression.summarize_tool_results(rows, _FakeSummarizer())
        assert _record(out)["original_chars"] == 5000

    def test_the_summary_is_marked_as_a_summary_not_an_elision(self, tmp_path,
                                                               monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        out = compression.summarize_tool_results(_old_results(3), _FakeSummarizer())
        assert _record(out)["kind"] == "summary"

    def test_the_protected_window_is_never_summarized(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        summarizer = _FakeSummarizer()
        rows = [_user("read it")] + [_tool("r" * 5000, str(i)) for i in range(8)]
        out = compression.summarize_tool_results(rows, summarizer)
        keep = compression.KEEP_RECENT_MESSAGES
        assert rows[-keep:] == out[-keep:], \
            "summarization reached into the results the model is reading now"

    def test_only_tool_output_is_summarized(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        rows = [{"role": "user", "content": "u" * 5000},
                {"role": "assistant", "content": "a" * 5000}]
        rows += _old_results(3)
        out = compression.summarize_tool_results(rows, _FakeSummarizer())
        # The control for the two assertions below: a summarizer ran at all.
        assert any(m.get(compression.COMPRESSION_FIELD) for m in out), \
            "nothing was summarized, so the role filter was never exercised"
        assert out[0]["content"] == "u" * 5000
        assert out[1]["content"] == "a" * 5000


class TestAFailedSummarizationIsNotFatal:
    """agno `manager.py:138-140`: return the original, degrade to no-compression."""

    def test_an_exploding_summarizer_does_not_raise(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        out = compression.summarize_tool_results(_old_results(3),
                                                 _ExplodingSummarizer())
        assert out[0]["content"] == "r" * 5000

    def test_a_failed_summary_leaves_no_record_claiming_one_happened(self,
                                                                     tmp_path,
                                                                     monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        out = compression.summarize_tool_results(_old_results(3),
                                                 _ExplodingSummarizer())
        assert compression.COMPRESSION_FIELD not in out[0]

    def test_an_empty_reply_counts_as_a_failure(self, tmp_path, monkeypatch):
        """A model that returns nothing has not summarized anything. Recording it
        as a summary would replace 5,000 characters of output with a blank."""
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        out = compression.summarize_tool_results(_old_results(3),
                                                 _FakeSummarizer("   "))
        assert out[0]["content"] == "r" * 5000

    def test_the_elision_path_still_runs_when_summarization_fails(self, tmp_path,
                                                                  monkeypatch):
        """Degrading to *no* compression is the promise; degrading to nothing at
        all would leave an oversized result on the wire."""
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        out = compression.compress(_aged("x" * 200_000),
                                   summarizer=_ExplodingSummarizer())
        assert "elided" in out[0]["content"]


# ---------------------------------------------------------------------------
# TASK 3 - depth-bounded recursive summarization
# ---------------------------------------------------------------------------


def _conversation(turns: int = 12, size: int = 1500) -> "list[dict]":
    """Alternating user/assistant turns, the shape `_trim_history` produces.

    `size` is kept well under half the budget used below. That is not
    convenience: the tail is accumulated backwards while it fits in half the
    budget, so a message larger than half the budget can never be part of a
    preserved tail and the algorithm correctly collapses to a flat summary
    instead - which is aider's behaviour, not a Chronoa bug, but it makes a
    fixture that claims to test "the tail survives" prove nothing.
    """
    rows = [_user("what is the state of " + "x" * size)]
    for i in range(turns):
        rows.append({"role": "assistant", "content": f"answer {i} " + "y" * size})
        rows.append(_user(f"and then {i}? " + "z" * size))
    return rows


#: A 128k window puts the budget at its 8,192 ceiling (aider's clamp), so half
#: of it is 4,096 and a 1,500-character message fits in the tail twice over.
_WINDOW = 131_072


class TestTheBudgetIsARatio:
    """aider `models.py:358`: `min(max(max_input_tokens / 16, 1024), 8192)`.

    A history worth summarizing on a 32k model is a rounding error on a 128k
    one, so the budget is a ratio of the window rather than a constant that is
    wrong for one model or another.
    """

    def test_it_scales_with_the_window(self):
        assert compression.summary_budget_chars(32_000) < \
            compression.summary_budget_chars(128_000)

    def test_it_is_clamped_at_both_ends(self):
        assert compression.summary_budget_chars(4_000) == compression.SUMMARY_BUDGET_MIN_CHARS
        assert compression.summary_budget_chars(10_000_000) == \
            compression.SUMMARY_BUDGET_MAX_CHARS

    def test_the_ratio_is_the_documented_one(self):
        assert compression.summary_budget_chars(64_000) == 4000


class TestRecursiveSummarization:
    def test_a_history_inside_the_budget_is_returned_untouched(self):
        summarizer = _FakeSummarizer()
        rows = [_user("hello")]
        out = compression.summarize_history(rows, summarizer, max_input_tokens=32_000)
        assert out == rows
        assert not summarizer.calls, "an in-budget history was summarized anyway"

    def test_the_recent_tail_survives_verbatim(self):
        """aider accumulates the tail backwards to half the budget so the newest
        turns are never paraphrased. The most recent turns are the ones the model
        is actually reasoning about."""
        rows = _conversation()
        summarizer = _FakeSummarizer()
        out = compression.summarize_history(rows, summarizer, max_input_tokens=_WINDOW)
        assert out[-1]["content"] == rows[-1]["content"]
        assert out[-2]["content"] == rows[-2]["content"], (
            "the tail was accumulated to one message when two fit in half the "
            "budget, so the walk backwards stopped early"
        )

    def test_the_summary_is_not_a_summary_of_the_whole_conversation(self):
        """The cut happens: if the head were summarized and the tail dropped, the
        result would be one message and nothing would be preserved."""
        rows = _conversation()
        out = compression.summarize_history(rows, _FakeSummarizer(),
                                           max_input_tokens=_WINDOW)
        assert len(out) > 1, "the whole conversation collapsed into one message"

    def test_the_result_fits_the_budget_when_the_summarizer_shrinks(self):
        rows = _conversation()
        out = compression.summarize_history(rows, _FakeSummarizer(),
                                           max_input_tokens=_WINDOW)
        assert compression.context_size(out) <= \
            compression.summary_budget_chars(_WINDOW)

    def test_it_recurses_when_one_summary_still_does_not_fit(self):
        """The convergence property elision does not have: if the result still
        does not fit, the loop runs again rather than stopping and logging."""
        class Shrinking:
            def __init__(self):
                self.rounds = 0

            def __call__(self, prompt, text):
                self.rounds += 1
                return "s" * max(200, len(text) // 3)

        summarizer = Shrinking()
        rows = _conversation(turns=20)
        compression.summarize_history(rows, summarizer, max_input_tokens=_WINDOW)
        assert summarizer.rounds > 1, (
            "one summarization round was accepted even though the result still "
            "did not fit the budget"
        )

    def test_an_unreducible_input_is_reported_not_silently_returned(self, caplog):
        """Recursion converges only if the summarizer shrinks. A model that
        returns its input back exhausts the depth bound, and the caller has to be
        told - the same discipline `compress()` applies when its floor is missed.
        """
        class NeverShrinks:
            def __call__(self, prompt, text):
                return "s" * (len(text) - 10)

        rows = _conversation(turns=20)
        with caplog.at_level("WARNING"):
            out = compression.summarize_history(rows, NeverShrinks(),
                                               max_input_tokens=_WINDOW)
        assert compression.context_size(out) > compression.summary_budget_chars(_WINDOW), \
            "this fixture was supposed to be irreducibly over budget"
        assert any("still over the" in r.message for r in caplog.records), \
            "the budget was missed with no diagnostic at all"

    def test_it_collapses_to_a_flat_summary_past_the_depth_limit(self):
        """aider `depth > 3`. Past that the head is already a summary, and
        re-splitting it is how a summary starts losing the numbers it was
        written to preserve."""
        class NeverShrinks:
            def __init__(self):
                self.rounds = 0

            def __call__(self, prompt, text):
                self.rounds += 1
                return "s" * (len(text) - 10)

        summarizer = NeverShrinks()
        rows = _conversation(turns=20)
        compression.summarize_history(rows, summarizer, max_input_tokens=_WINDOW,
                                     max_depth=compression.MAX_SUMMARY_DEPTH)
        assert summarizer.rounds <= compression.MAX_SUMMARY_DEPTH + 2, (
            f"recursed {summarizer.rounds} times: the depth bound is not a bound"
        )

    def test_the_head_is_cut_on_an_assistant_boundary(self):
        """aider moves the split point back until the head ends on an assistant
        message.

        Cut mid-turn, the head carries a user's question without its answer, and
        the "summary" is of a fragment. Observable from outside because the
        correction moves the split one message further back than the raw
        backwards accumulation would: with 1,500-character messages and half the
        budget at 4,096, the raw walk stops with a 2-message tail, and the
        boundary correction gives a 3-message tail. One summary plus three tail
        messages is four; without the correction it is three.
        """
        rows = _conversation()
        out = compression.summarize_history(rows, _FakeSummarizer(),
                                           max_input_tokens=_WINDOW)
        assert out[-3:] == rows[-3:], \
            "the preserved tail is not the last three messages verbatim"
        assert len(out) == 4, (
            f"expected one summary plus the 3-message boundary-corrected tail, "
            f"got {len(out)} messages - the assistant-boundary walk looks absent"
        )
        # The head therefore ended on rows[-4], which is the assistant's answer
        # to rows[-5]'s question rather than the question on its own.
        assert rows[-4]["role"] == "assistant"

    def test_a_failed_summarization_keeps_the_prose_verbatim(self, tmp_path,
                                                              monkeypatch):
        """A degraded summary is still a summary. A lossy one nobody asked for is
        not, so the untransformed transcript is what survives."""
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        rows = _conversation()
        out = compression.summarize_history(rows, _ExplodingSummarizer(),
                                           max_input_tokens=_WINDOW)
        assert rows[-1]["content"] in "\n".join(
            m.get("content") or "" for m in out)


class TestTheStalenessGuard:
    """aider `base_coder.py:1031-1032`: discard, do not apply.

    Applying a summary computed over a list that has since changed would
    silently drop turns. Not hypothetical here either: `compress()` is called
    with a fresh copy from `build_messages()`, and a summarizer built on this
    repo's `AsyncBridge` runs on another thread and returns through
    `GLib.idle_add`, so the assistant's history can grow mid-summarization.
    """

    _ARRIVED = "a turn that arrived mid-summarization"

    def _mutating(self, rows: "list[dict]"):
        arrived = self._ARRIVED

        class Mutating:
            def __call__(self, prompt, text):
                rows.append(_user(arrived))
                return "SUMMARY"
        return Mutating()

    def test_a_summary_computed_over_a_changed_list_is_discarded(self, tmp_path,
                                                                 monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        rows = _conversation()
        out = compression.summarize_history(rows, self._mutating(rows),
                                           max_input_tokens=_WINDOW)
        assert not any(m.get("content") == "SUMMARY" for m in out), (
            "a summary computed over a list that changed underneath it was applied"
        )

    def test_the_turn_that_arrived_mid_summarization_is_not_dropped(self, tmp_path,
                                                                   monkeypatch):
        """The reason aider discards rather than applies. Applying a summary
        computed over a stale list silently drops turns, and the user never finds
        out which ones."""
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        rows = _conversation()
        out = compression.summarize_history(rows, self._mutating(rows),
                                           max_input_tokens=_WINDOW)
        assert self._ARRIVED in [m.get("content") for m in out], (
            "the turn that arrived while the summary was being computed is not in "
            "the result at all"
        )

    def test_an_unchanged_list_does_apply_its_summary(self, tmp_path, monkeypatch):
        """The control for the two above: a guard that always discards is a
        correct-looking implementation of nothing."""
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        rows = _conversation()
        out = compression.summarize_history(rows, _FakeSummarizer(),
                                           max_input_tokens=_WINDOW)
        assert any(m.get("content") == "SUMMARY: kept the numbers and the ids"
                   for m in out)

    def test_the_fallback_is_the_deterministic_path_not_an_exception(self, tmp_path,
                                                                     monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        rows = _conversation()
        out = compression.summarize_history(rows, self._mutating(rows),
                                           max_input_tokens=32_000)
        assert isinstance(out, list) and out


class TestTheDefaultPathIsUnchanged:
    """`assistant.py:185` calls `compress(messages)` with no summarizer."""

    def test_compress_without_a_summarizer_never_summarizes(self, tmp_path,
                                                            monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        rows = [_tool("r" * 5000, "1"), _user("hi")]
        out = compression.compress(rows)
        assert out[0]["content"] == "r" * 5000

    def test_an_ordinary_result_is_still_a_no_op(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        rows = [{"role": "user", "content": "what time is it?"},
                _tool("It is 18:30."),
                {"role": "assistant", "content": "It is half past six."}]
        assert compression.compress(rows) == rows

    def test_compress_writes_nothing_when_it_reduces_nothing(self, tmp_path,
                                                             monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        rows = [{"role": "user", "content": "hi"},
                _tool("x" * 500, "1")]
        compression.compress(rows)
        assert not (tmp_path / "shani-chronoa").exists(), (
            "the spill directory was created for a conversation that reduced "
            "nothing"
        )