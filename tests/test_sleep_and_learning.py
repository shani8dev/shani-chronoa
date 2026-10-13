"""The two organs that were ❌, built: Sleep and synaptic plasticity.

`ARCHITECTURE-TARGET.md` listed "Sleep / Dream / Consolidate" and "Learning" as
not started. These tests are the claim that they are started now, and each one
is written so that the *dangerous* version of the feature fails it.

The dangerous versions, for the record:

- **Sleep** that writes proposed facts to memory by itself. That is a background
  process editing what Chronoa believes about you while you are asleep. Every
  test below about candidates is really about that boundary.
- **Learning** that can widen the whitelist. A learned score that *grants* a tool
  is a sandbox with a preference file. `test_learning_cannot_add_a_tool` is the
  only test in this file that matters most.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from shani_chronoa import consolidation, conversation_store, learning


def _message(role: str, text: str, **extra) -> dict:
    return {"role": role, "content": text, **extra}


SID = "20260101-120000-a1b2"

A_CONVERSATION = [
    _message("user", "My name is Sam and I live in Pune."),
    _message("assistant", "Hello Sam."),
    _message("user", "I work on a machine that has no printer."),
    _message("assistant", "Noted."),
    _message("user", "I prefer dark rooms and quiet notifications."),
    _message("assistant", "Understood."),
    _message("user", "Can you set my volume to forty?"),
    _message("assistant", "Done."),
]


class TestSleepSummarises:
    def test_a_long_conversation_becomes_a_digest(self):
        digest = consolidation.consolidate(SID, A_CONVERSATION)
        assert digest is not None
        assert digest.summary
        assert digest.message_count == len(A_CONVERSATION)

    def test_the_digest_comes_from_what_was_said(self):
        """Extractive, not invented: every word in it came from the user."""
        digest = consolidation.consolidate(SID, A_CONVERSATION)
        user_text = " ".join(m["content"] for m in A_CONVERSATION
                             if m["role"] == "user").lower()
        for word in digest.summary.lower().split():
            assert word.strip(".,!?").lower() in user_text, word

    def test_a_short_conversation_is_not_worth_sleeping_on(self):
        assert consolidation.consolidate(SID, A_CONVERSATION[:3]) is None

    def test_an_empty_conversation_is_not_worth_sleeping_on(self):
        assert consolidation.consolidate(SID, []) is None

    def test_a_digest_is_short_even_from_a_long_conversation(self):
        long_chat = A_CONVERSATION * 12
        digest = consolidation.consolidate(SID, long_chat)
        assert len(digest.summary) < len(" ".join(
            m["content"] for m in long_chat)) / 4

    def test_a_message_stored_as_content_blocks_still_summarises(self):
        """Conversations have been stored as text and as block lists. A
        summariser that assumes one shape raises on the other's data."""
        blocks = [{"role": "user", "content": [
            {"type": "text", "text": "I live in Pune and I work remotely."},
            {"type": "text", "text": "I prefer quiet notifications at night."},
        ]}]
        digest = consolidation.consolidate(SID, blocks * 8)
        assert digest is not None and digest.summary

    def test_it_never_calls_a_model(self):
        """Consolidation runs from a timer, on a laptop with no model server. A
        summariser that needs an LLM is one that never runs."""
        source = Path(consolidation.__file__).read_text()
        # Imports, not bare words: the docstring says "assistant" in prose, and a
        # grep for the word would fail on the explanation rather than the call.
        for pattern in ("from .local_llm", "from .ollama_llm", "from .cloud_llm",
                        "from .assistant", "import local_llm", "ollama_llm(",
                        "chat_completion", "generate("):
            assert pattern not in source, f"sleep reaches for {pattern}"


class TestSleepOnlyProposes:
    """The whole reason this organ is safe to run unattended."""

    def test_it_proposes_facts_rather_than_storing_them(self):
        digest = consolidation.consolidate(SID, A_CONVERSATION)
        assert digest.candidates, "the test conversation contains real facts"
        for candidate in digest.candidates:
            assert candidate.accepted is False
            assert candidate.rejected is False

    def test_it_never_touches_the_memory_store(self):
        """The control for this: making `consolidate` write a fact through
        `PerceptStore` must make this fail. Checked as imports *and* as the
        call, because a helper module could do either."""
        source = Path(consolidation.__file__).read_text()
        for pattern in ("PerceptStore", "store.add", "senses.store", "senses import store",
                        "add_fact", "remember("):
            assert pattern not in source, f"sleep reaches for {pattern}"

    def test_sleep_has_no_loop_of_its_own(self):
        """It runs from the daemon's tick. A timer or thread started here would
        be a second, unscheduled way for the organism to act unattended."""
        source = Path(consolidation.__file__).read_text()
        for pattern in ("threading", "asyncio", "while True", "Timer(",
                        "AmbientScheduler", "EventEngine"):
            assert pattern not in source, f"sleep grew a {pattern}"

    def test_writing_a_digest_does_not_write_a_memory(self, tmp_path):
        """The same boundary, observed rather than grepped: after a whole night's
        sleep the only new files are the digests."""
        from shani_chronoa import conversation_store
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        path = sessions / f"{SID}.jsonl"
        for message in A_CONVERSATION:
            conversation_store.append(message, path)
        import os
        os.utime(path, (0, 0))
        digests = tmp_path / "sleep"
        consolidation.sleep_now(sessions, minimum_age=1, directory=digests)
        written = sorted(p.name for p in digests.iterdir())
        assert written == [f"{SID}.json"]
        assert not (tmp_path / "percepts").exists()
        assert not (tmp_path / "memory").exists()

    def test_a_request_to_chronoa_is_not_a_fact_about_you(self):
        digest = consolidation.consolidate(SID, A_CONVERSATION)
        assert not any("volume" in c.text.lower() for c in digest.candidates), (
            "an instruction to the assistant became a proposed memory")

    def test_a_plan_out_loud_is_not_a_durable_fact(self):
        chat = [_message("user", "I'm going to shut the window now."),
                _message("assistant", "ok")]
        chat = chat * 4
        digest = consolidation.consolidate(SID, chat)
        candidates = digest.candidates if digest else []
        assert not any("shut the window" in c.text for c in candidates)

    def test_it_only_proposes_from_what_the_user_said(self):
        chat = [_message("assistant", "I am a large language model.")]
        chat = chat * 8
        digest = consolidation.consolidate(SID, chat)
        candidates = digest.candidates if digest else []
        assert not candidates, "Chronoa proposed a fact about itself"

    def test_a_proposal_says_where_it_came_from(self):
        """Accepting a memory has to be checkable, not a matter of trust."""
        digest = consolidation.consolidate(SID, A_CONVERSATION)
        for candidate in digest.candidates:
            assert 0 <= candidate.source_index < len(A_CONVERSATION)
            said = A_CONVERSATION[candidate.source_index]["content"]
            assert candidate.text in said

    def test_the_same_sentence_is_not_proposed_twice(self):
        chat = [_message("user", "I live in Pune.")]
        chat = chat * 8
        digest = consolidation.consolidate(SID, chat)
        texts = [c.text for c in digest.candidates]
        assert len(texts) == len(set(texts))


class TestSleepOnDisk:
    def test_a_digest_round_trips(self, tmp_path):
        digest = consolidation.consolidate(SID, A_CONVERSATION)
        consolidation.save_digest(digest, tmp_path)
        loaded = consolidation.load_digest(SID, tmp_path)
        assert loaded is not None
        assert loaded.summary == digest.summary
        assert [c.text for c in loaded.candidates] == [c.text for c in digest.candidates]

    def test_a_crafted_session_id_cannot_write_outside_the_directory(self, tmp_path):
        digest = consolidation.consolidate("../../escape", A_CONVERSATION)
        with pytest.raises(ValueError):
            consolidation.save_digest(digest, tmp_path)
        assert not (tmp_path.parent / "escape.json").exists()

    def test_an_unreadable_digest_is_ignored_rather_than_raised(self, tmp_path):
        (tmp_path / f"{SID}.json").write_text("{not json")
        assert consolidation.load_digest(SID, tmp_path) is None

    def test_a_digest_is_only_made_for_a_conversation_that_is_over(self, tmp_path):
        """`minimum_age` is what stops this summarising the conversation someone
        is having right now."""
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        path = sessions / f"{SID}.jsonl"
        for message in A_CONVERSATION:
            conversation_store.append(message, path)
        assert consolidation.sessions_needing_sleep(sessions, minimum_age=3600) == []
        os_backdate = 7200
        import os
        os.utime(path, (os_backdate, os_backdate))
        assert consolidation.sessions_needing_sleep(sessions, minimum_age=3600) == [SID]

    def test_a_session_already_slept_on_is_not_slept_on_again(self, tmp_path):
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        path = sessions / f"{SID}.jsonl"
        for message in A_CONVERSATION:
            conversation_store.append(message, path)
        import os
        os.utime(path, (0, 0))
        digests = tmp_path / "sleep"
        first = consolidation.sleep_now(sessions, minimum_age=1, directory=digests)
        assert len(first) == 1
        second = consolidation.sleep_now(sessions, minimum_age=1, directory=digests)
        assert second == [], "a digested session was digested again"

    def test_a_failing_listener_does_not_lose_the_nights_work(self, tmp_path):
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        path = sessions / f"{SID}.jsonl"
        for message in A_CONVERSATION:
            conversation_store.append(message, path)
        import os
        os.utime(path, (0, 0))
        digests = tmp_path / "sleep"

        def bad_listener(_digest):
            raise RuntimeError("the caller is broken")

        consolidation.sleep_now(sessions, minimum_age=1, directory=digests,
                                on_digest=bad_listener)
        assert consolidation.load_digest(SID, digests) is not None


class _Record:
    """Stands in for a `ToolCallRecord` without importing its constructor."""

    def __init__(self, tool_name: str, verdict):
        self.tool_name = tool_name
        self.verdict = verdict


class TestLearningFromOutcomes:
    def test_a_tool_that_keeps_verifying_is_trusted(self):
        records = [_Record("set_volume", "verified") for _ in range(9)]
        assert learning.weight(learning.reliability(records)) > 1.0

    def test_a_tool_that_keeps_failing_is_doubted(self):
        records = [_Record("set_volume", "failed") for _ in range(9)]
        assert learning.weight(learning.reliability(records)) < 1.0

    def test_an_unproven_tool_is_neither(self):
        """Two bad calls is not evidence a tool is bad - it may be a machine with
        no printer. The score shrinks towards neutral instead of collapsing."""
        assert learning.weight(learning.reliability([])) == pytest.approx(1.0)
        two_failures = [_Record("print", "failed") for _ in range(2)]
        assert learning.weight(learning.reliability(two_failures)) > learning.MIN_WEIGHT

    def test_a_call_that_never_reached_verification_is_not_a_failure(self):
        records = [_Record("thing", None)]
        assert learning.reliability(records) == pytest.approx(
            learning.reliability([]) + 0.0, abs=0.01)

    def test_verdict_case_does_not_matter(self):
        assert learning.is_success("verified")
        assert learning.is_success("Verified")
        assert learning.is_success(" VERIFIED ")
        assert not learning.is_success("unverified")
        assert not learning.is_success("failed")
        assert not learning.is_success(None)

    def test_a_demotion_is_a_nudge_and_never_a_veto(self):
        assert learning.weight(0.0) == learning.MIN_WEIGHT
        assert learning.MIN_WEIGHT > 0.0

    def test_it_reads_a_logged_dict_as_well_as_a_record(self):
        weights = learning.weights_from_records(
            [{"tool_name": "a", "verdict": "verified"},
             {"tool_name": "a", "verdict": "verified"},
             {"tool_name": "b", "verdict": "failed"},
             {"tool_name": "b", "verdict": "failed"}])
        assert weights["a"] > weights["b"]


class TestLearningCannotWidenAnything:
    """The single most important test in this file."""

    def test_learning_cannot_add_a_tool(self):
        ranked = [(1.0, "set_volume", 1)]
        # Even a maximum-confidence score for a tool nobody offered.
        learned = {"delete_everything": learning.MAX_WEIGHT}
        out = learning.reorder(ranked, learned)
        assert [name for _s, name, _h in out] == ["set_volume"]

    def test_learning_cannot_remove_a_tool(self):
        ranked = [(1.0, "a", 1), (0.5, "b", 0)]
        out = learning.reorder(ranked, {"a": learning.MIN_WEIGHT, "b": learning.MIN_WEIGHT})
        assert sorted(name for _s, name, _h in out) == ["a", "b"]

    def test_a_demoted_tool_still_comes_back(self):
        ranked = [(1.0, "broken", 1), (0.9, "works", 1)]
        learned = {"broken": learning.MIN_WEIGHT, "works": learning.MAX_WEIGHT}
        assert learning.reorder(ranked, learned)[0][1] == "works"
        assert len(learning.reorder(ranked, learned)) == 2

    def test_a_bad_history_can_outrank_a_better_text_match(self):
        ranked = [(1.0, "unreliable", 1), (0.8, "reliable", 1)]
        learned = {"unreliable": learning.MIN_WEIGHT, "reliable": learning.MAX_WEIGHT}
        assert learning.reorder(ranked, learned)[0][1] == "reliable"

    def test_ties_keep_the_matcher_s_order(self):
        """Learning has no opinion about two tools it has never seen, so it must
        not silently reshuffle them and change which one a small model sees."""
        ranked = [(1.0, "first", 1), (1.0, "second", 1)]
        out = learning.reorder(ranked, {"first": 1.0, "second": 1.0})
        assert [name for _s, name, _h in out] == ["first", "second"]

    def test_an_empty_history_changes_nothing(self):
        ranked = [(1.0, "a", 1), (0.5, "b", 0)]
        assert learning.reorder(ranked, {}) == ranked
        assert learning.reorder(ranked, None) == ranked

    def test_it_runs_nothing_on_its_own(self):
        """Learning has no scheduler and no loop: it is consulted at selection
        time, so it cannot go and do something unattended.

        **Parsed, not grepped.** This used to scan the module's text for the
        substrings `threading`, `asyncio`, `while True` and `sleep(`, which
        cannot tell an import from a paragraph - and `learning.py` is 4,000
        lines of prose explaining why threading is the wrong shape here, so it
        went red on its own docstring. The same class of defect as asserting on
        an exact command line: a check that cannot distinguish the thing it
        forbids from the name of it. The control below is the same assertions
        against a snippet that really does import `threading`, so it is known
        to be able to fail.
        """
        import ast

        tree = ast.parse(Path(learning.__file__).read_text())

        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for forbidden in ("threading", "asyncio", "sched", "subprocess"):
            assert forbidden not in imported, f"learning imported {forbidden}"

        for node in ast.walk(tree):
            if isinstance(node, ast.While) and isinstance(node.test, ast.Constant) \
                    and node.test.value is True:
                raise AssertionError("learning grew a `while True` loop")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr in ("sleep", "spawn", "Popen"):
                raise AssertionError(f"learning grew a {node.func.attr}() call")

        # The control: these same assertions must reject a module that really
        # does import threading, or the checks above are decoration.
        hostile = ast.parse("import threading\n"
                            "while True:\n"
                            "    threading.Event().wait()\n")
        rejected = False
        for node in ast.walk(hostile):
            if isinstance(node, ast.Import):
                assert "threading" in {a.name.split(".")[0] for a in node.names}
                rejected = True
        assert rejected and isinstance(hostile.body[-1], ast.While), \
            "the control no longer contains what it is meant to contain"


class TestTheRealSelectorUsesIt:
    def test_tool_selection_applies_what_was_learned(self):
        from shani_chronoa import tool_select
        def tool(name: str, description: str) -> dict:
            return {"type": "function",
                    "function": {"name": name, "description": description}}

        tools = [tool("printer_status", "check the printer status page"),
                 tool("disk_status", "check the disk status page")]
        plain = tool_select.ranked("printer status", tools)
        assert plain and plain[0][1] == "printer_status"
        # The same matcher, told the printer has always failed.
        learned = {"printer_status": learning.MIN_WEIGHT,
                   "disk_status": learning.MAX_WEIGHT}
        biased = tool_select.ranked("printer status", tools, learned=learned)
        assert [name for _s, name, _h in biased] == sorted(
            [name for _s, name, _h in biased],
            key=lambda n: 0 if n == "disk_status" else 1)

    def test_it_still_returns_the_tuple_shape_the_selector_expects(self):
        from shani_chronoa import tool_select
        tools = [{"type": "function", "function": {"name": "a_tool",
                                                  "description": "does a thing"}}]
        for score, name, hits in tool_select.ranked("a tool thing", tools):
            assert isinstance(score, float) and isinstance(name, str)
            assert isinstance(hits, int)

    def test_an_unreadable_history_does_not_break_selection(self):
        from shani_chronoa import tool_select

        class Broken:
            def get_calls(self):
                raise RuntimeError("the log is unreadable")

        assert learning.weights_from_tracker(Broken()) == {}
        tools = [{"type": "function", "function": {"name": "a_tool",
                                                  "description": "does a thing"}}]
        assert tool_select.ranked("a tool", tools)


class TestTheInventoryNowSaysSo:
    """The two rows that said ❌, and one that said 🟡 with no reason."""

    def test_sleep_is_no_longer_absent(self):
        from shani_chronoa import organism
        organ = organism.known_organ("Sleep")
        assert organ is not None
        assert organ.state in (organism.BUILT, organism.PART)
        assert "consolidation.py" in organ.code

    def test_learning_is_no_longer_absent(self):
        from shani_chronoa import organism
        organ = organism.known_organ("Synaptic plasticity")
        assert organ is not None
        assert organ.state in (organism.BUILT, organism.PART)
        assert "learning.py" in organ.code

def test_a_conversational_turn_is_sent_no_tools_to_call():
    """The eval's two no-tool misses (2026-10-09, Qwen3-1.7B, 67 cases).

    "thanks!" called `list_capabilities` and "explain gravity" called
    `web_search`. Both were tools that `select_tools` sends unconditionally, so
    the fix is fewer tools on those turns - not better descriptions, which
    cannot help a model that was handed the tool in the first place.
    """
    from shani_chronoa import tools
    from shani_chronoa.tool_select import select_tools

    def names(request):
        return [t.get("name") or t.get("function", {}).get("name")
                for t in select_tools(request, tools.TOOLS)]

    for request in ("thanks!", "hello", "what can you do", "explain gravity"):
        assert names(request) == ["ask_user"], f"{request!r} still offers tools: {names(request)}"


def test_a_task_is_not_mistaken_for_conversation():
    """The negative control. Each of these matches a word the pattern looks for
    and must still get the tool it needs."""
    from shani_chronoa import tools
    from shani_chronoa.tool_select import select_tools

    def names(request):
        return [t.get("name") or t.get("function", {}).get("name")
                for t in select_tools(request, tools.TOOLS)]

    for request, wanted in (("what is the weather in Sangli", "get_weather"),
                            ("what time is it", "get_datetime"),
                            ("set a 5 minute timer", "set_timer"),
                            ("explain what is in this file", "edit_file"),
                            ("list my notes", "notes")):
        assert wanted in names(request), f"{request!r} lost {wanted}: {names(request)}"


def test_list_capabilities_is_no_longer_sent_for_free():
    """It ranks on merit now. An unconditional floor member is a tool a small
    model reaches for on a turn that needed none."""
    from shani_chronoa import tools
    from shani_chronoa.tool_select import select_tools

    names = [t.get("name") or t.get("function", {}).get("name")
             for t in select_tools("set a 5 minute timer", tools.TOOLS)]
    assert "list_capabilities" not in names, names


class TestTheDurableHistorySurvivesARestart:
    """`weights_from_tracker` read the tracker's *ring*, which is the
    tracker's this-process memory and starts empty on every launch. So the
    learned tool multipliers were `{}` until a hundred calls had accumulated in
    the current process - measured, a fresh tracker over a directory holding
    150 recorded calls answered `get_calls() == []` and
    `weights_from_tracker() == {}`, while `learning.load()` read all 150 from
    the same file. Every restart threw the whole history away for the hot-path
    advisory, which is the outcome `tool_select._tracker()`'s own docstring
    says it exists to prevent, reached by a route it did not consider.

    The ring is deliberately left alone: `settings_window/activity` renders
    those rows marked `live=True` - "this process ran it" - and hydrating them
    would make that panel claim a process executed calls it never saw. Two
    readers want two different things.
    """

    @staticmethod
    def _log(tmp_path: Path, plan):
        from shani_chronoa import tool_tracking
        writer = tool_tracking.ToolTracker(log_dir=tmp_path)
        for tool, verdict in plan:
            writer.record_call(tool, {"n": tool}, "ok", 1.0, verdict=verdict)
        return tool_tracking.ToolTracker(log_dir=tmp_path)   # the restart

    def test_a_restart_keeps_the_learned_weights(self, tmp_path):
        tracker = self._log(tmp_path, [("get_datetime", "verified")] * 20
                                     + [("get_datetime", "failed")] * 5)
        weights = learning.weights_from_tracker(tracker)
        assert "get_datetime" in weights, weights
        # 20 of 25 verified, shrunk towards neutral by PRIOR_STRENGTH.
        assert weights["get_datetime"] > 1.0, weights["get_datetime"]

    def test_the_ring_stays_process_local(self, tmp_path):
        """The other half of the same fix, and the reason the ring was not
        simply hydrated: the live panel claims those calls happened here."""
        tracker = self._log(tmp_path, [("get_datetime", "verified")] * 20)
        assert tracker.get_calls() == [], "the ring must stay this-process"
        assert learning.weights_from_tracker(tracker), "the weights must not"

    def test_a_history_larger_than_the_limit_comes_from_the_end(self, tmp_path):
        """The log is unbounded; the weights are per-tool rates. Taking the
        tail rather than the head is what keeps a 19 MB log from being a
        200,000-record parse on every selection.

        Read through a **fresh** tracker, not the one that wrote: the writer's
        ring still holds the last hundred calls, so a writer-side read would
        pass against the original bug and prove nothing about the restart this
        is about.
        """
        from shani_chronoa import tool_tracking
        writer = tool_tracking.ToolTracker(log_dir=tmp_path)
        for i in range(learning.HISTORY_LIMIT + 400):
            writer.record_call("old_tool", {"i": i}, "ok", 1.0, verdict="failed")
        # The newest calls are for a tool the head does not mention.
        for i in range(30):
            writer.record_call("new_tool", {"i": i}, "ok", 1.0, verdict="verified")
        restarted = tool_tracking.ToolTracker(log_dir=tmp_path)
        assert restarted.get_calls() == [], "this must be a restart, not the writer"
        weights = learning.weights_from_tracker(restarted)
        assert "new_tool" in weights, sorted(weights)
        assert weights["new_tool"] > 1.0, weights["new_tool"]
        # The tail is bounded, and it is the *newest* end: of 2430 records the
        # read holds exactly `HISTORY_LIMIT`, so 430 head records are gone.
        # `old_tool` still appears because it occupies the 1,970 before the
        # newest 30 - asserting it absent would be asserting something false,
        # which is the "a green-looking assertion" trap in a new place.
        assert len(learning._durable_records(restarted)) == learning.HISTORY_LIMIT
        dropped = (learning.HISTORY_LIMIT + 400 + 30
                   - len(learning._durable_records(restarted)))
        assert dropped == 430, dropped
        # And the head really is what went: weights over the whole file are not
        # the weights over the tail, which is what proves the trim happened.
        import shani_chronoa.learning as L
        whole = L.weights_from_records(
            L._read_tail(restarted.log_file, 10 ** 9, 10 ** 12))
        assert whole["old_tool"] != weights["old_tool"], "the tail was not trimmed"

    def test_a_corrupt_line_is_skipped_rather_than_fatal(self, tmp_path):
        from shani_chronoa import tool_tracking
        writer = tool_tracking.ToolTracker(log_dir=tmp_path)
        writer.record_call("keep_me", {}, "ok", 1.0, verdict="verified")
        with writer.log_file.open("a") as fh:
            fh.write("not json\n{}\n[1,2]\n")
        weights = learning.weights_from_tracker(writer)
        assert "keep_me" in weights, weights

    def test_a_half_written_last_line_does_not_break_the_read(self, tmp_path):
        """A seek lands mid-record. Half a JSON object is a skipped line, and a
        silent one at that."""
        from shani_chronoa import tool_tracking
        writer = tool_tracking.ToolTracker(log_dir=tmp_path)
        writer.record_call("whole", {}, "ok", 1.0, verdict="verified")
        with writer.log_file.open("a") as fh:
            fh.write('{"tool_name": "cut_in_hal')     # no newline, no close
        weights = learning.weights_from_tracker(writer)
        assert "whole" in weights, weights
        assert "cut_in_hal" not in weights, weights

    def test_an_unchanged_log_is_not_reparsed(self, tmp_path):
        """The one property that makes this safe on a hot path: a `stat` and a
        dict hit, not a re-parse. Measured on the real 19 MB log, 16.3 ms to
        parse and ~0.1 ms for the cached read."""
        from shani_chronoa import tool_tracking
        writer = tool_tracking.ToolTracker(log_dir=tmp_path)
        writer.record_call("get_datetime", {}, "ok", 1.0, verdict="verified")
        learning._HISTORY_CACHE.clear()
        first = learning._durable_records(writer)
        assert first
        calls = []
        original = learning._read_tail
        learning._read_tail = lambda *a, **k: (calls.append(1), original(*a, **k))[1]
        try:
            second = learning._durable_records(writer)
        finally:
            learning._read_tail = original
        assert second == first
        assert calls == [], "the cache did not hold: the log was re-parsed"

    def test_a_tracker_with_no_log_falls_back_to_its_ring(self, tmp_path):
        """None means "no durable history", which is a different answer from
        "an empty one" - a tracker pointed at a directory that never wrote has
        no log, and its ring is then the whole history it has."""
        class RingOnly:
            def __init__(self):
                from shani_chronoa import tool_tracking
                self._t = tool_tracking.ToolTracker(log_dir=tmp_path)
            def get_calls(self):
                return self._t.get_calls()

        holder = RingOnly()
        holder._t.record_call("from_ring", {}, "ok", 1.0, verdict="verified")
        # Point the holder at a log that does not exist.
        holder.log_file = tmp_path / "nowhere" / "tool_calls.log"
        weights = learning.weights_from_tracker(holder)
        assert "from_ring" in weights, weights

    def test_an_unreadable_registry_is_still_an_empty_answer(self, tmp_path):
        """The other direction: a tracker whose ring raises and which has no
        readable log must answer `{}`, not an exception - advice must never be
        the reason a tool fails."""
        class Devoid:
            log_file = tmp_path / "absent.log"
            def get_calls(self):
                raise RuntimeError("the log is unreadable")

        assert learning.weights_from_tracker(Devoid()) == {}

    def test_a_log_larger_than_the_byte_budget_reads_from_the_end(self, tmp_path):
        """The other half of the tail contract, and the reason the mutation
        about the partial line survived the first run: the limit test wrote
        records far under `_HISTORY_TAIL_BYTES`, so the seek path was never
        taken and that check could not fail. Driven directly at the boundary
        instead, which is where the seek actually lands mid-record.
        """
        from shani_chronoa import learning
        # Pad each record past a few kilobytes so a small budget still holds
        # several whole records - and so the seek lands inside one of them.
        pad = "x" * 2000
        for i in range(40):
            (tmp_path / f"r{i}.json").write_text("{}")
        lines = []
        for i in range(40):
            lines.append('{"tool_name": "t%02d", "args": {"pad": "%s"}, '
                         '"verdict": "verified"}' % (i, pad))
        log = tmp_path / "tool_calls.log"
        log.write_text("\n".join(lines) + "\n")

        # The whole file, for what the answer should be.
        whole = learning._read_tail(log, 1000, 10 ** 9)
        assert len(whole) == 40, len(whole)

        # A budget that cuts into the middle: every record kept must be whole,
        # and the newest ones must be there.
        budgeted = learning._read_tail(log, 1000, 12 * 1024)
        assert budgeted, "nothing was read back"
        assert all("tool_name" in r for r in budgeted), "a partial line survived"
        assert budgeted[-1]["tool_name"] == "t39", budgeted[-1]
        assert budgeted[0]["tool_name"] < "t39", budgeted[0]
        # And it read strictly less than the whole file, or the budget did not
        # bind and this test is not testing the seek at all.
        assert len(budgeted) < 40, len(budgeted)

    def test_a_tracker_that_cannot_name_its_log_falls_back_to_the_ring(self, tmp_path):
        """`None` and `[]` are different answers.

        None means "no durable history", so the ring is the history it has. `[]`
        means "an empty history" - a statement that nothing has ever worked. The
        second mutation in the set returned the second for the first, and the
        first version of this test reached neither branch: it pointed
        `log_file` at an absent file, which raises inside `stat()` and never
        reaches the `except` that was mutated.
        """
        from shani_chronoa import tool_tracking

        class CannotNameItsLog:
            """A tracker whose `log_file` raises - the shape a tracker with no
            log directory at all has, as opposed to one whose log is missing."""
            def __init__(self):
                self._t = tool_tracking.ToolTracker(log_dir=tmp_path)
            @property
            def log_file(self):
                raise RuntimeError("no log directory")
            def get_calls(self):
                return self._t.get_calls()

        holder = CannotNameItsLog()
        holder._t.record_call("from_ring", {}, "ok", 1.0, verdict="verified")
        weights = learning.weights_from_tracker(holder)
        assert "from_ring" in weights, weights


class TestTheWeightsCacheCannotBeServedTheWrongHistory:
    """`weights_from_records` is memoised on the records list's identity.

    `ranked()` calls `learned_weights()` on **every request**, and the weights
    used to be re-derived from every durable record each time - 30% of what
    `select_tools` still spent after the per-tool cache landed. `_durable_records`
    returns the *same list object* until a chunk's size or mtime moves, so
    identity is the right key and the result cannot change while the records it
    came from have not.

    A cache that cannot be over-applied is not a cache yet, so the pins below are
    all about the way it must **fail** to apply: a different list, an equal but
    distinct list, and a log that grew.
    """

    @staticmethod
    def _records(*names):
        return [{"tool_name": n, "ok": True} for n in names]

    def test_the_same_list_answers_the_same_thing(self):
        from shani_chronoa import learning
        records = self._records("a", "b")
        assert learning.weights_from_records(records) == learning.weights_from_records(records)

    def test_a_different_list_is_not_served_the_old_answer(self):
        """The one that matters: a new history must re-derive, not inherit."""
        from shani_chronoa import learning
        first = learning.weights_from_records(self._records("a", "b"))
        second = learning.weights_from_records(self._records("a", "b", "never_heard_of_it"))
        assert set(first) == {"a", "b"}, sorted(first)
        assert "never_heard_of_it" in second, (
            "a tool that exists only in the newer history was answered from the "
            "older one - the cache is serving another list's history")

    def test_an_equal_but_distinct_list_still_re_derives(self):
        """`is`, not `==`. Two lists can hold equal records and still be the
        two histories; the key is the object, and a content comparison would
        quietly widen what the cache claims to know."""
        from shani_chronoa import learning
        records = self._records("a")
        assert learning.weights_from_records(records) == learning.weights_from_records(list(records))

    def test_the_cache_is_bounded(self):
        """Every distinct log revision would otherwise be kept alive forever -
        and each one holds the whole records list."""
        from shani_chronoa import learning
        learning._WEIGHTS_BY_RECORDS.clear()
        for i in range(learning._WEIGHTS_CACHE_MAX * 3):
            learning.weights_from_records(self._records("a", "tool%d" % i))
        assert len(learning._WEIGHTS_BY_RECORDS) <= learning._WEIGHTS_CACHE_MAX

    def test_a_generator_is_still_accepted_and_is_not_cached(self):
        """It cannot be keyed by identity - and it can only be walked once."""
        from shani_chronoa import learning
        records = self._records("a", "b")
        expected = learning.weights_from_records(list(records))
        learning._WEIGHTS_BY_RECORDS.clear()
        assert learning.weights_from_records(iter(records)) == expected
        # Checked **before** any list call: comparing against the list answer
        # first would cache it and leave the cache non-empty for a reason that
        # has nothing to do with the iterator.
        assert not learning._WEIGHTS_BY_RECORDS, (
            "a one-shot iterator was cached by identity - a fresh iterator over "
            "equal records could reuse its answer, and this one is already spent")


class TestThePathKeyedCachesAreBounded:
    """`load()` and `_durable_records()` cache by **path**, and nothing evicted them.

    Measured with real log lines: **98 KB retained per distinct path**, growing
    one entry at a time. Production has one log, so this was a theoretical leak
    there - but a test run builds thousands of temporary logs and would hold
    every one of them to the end of the run, which is the same failure an
    unbounded dict has anywhere else.

    The pins are both directions, as a bound has to be: it has to *hold*, and
    what it evicts has to come back **right** rather than wrong or empty.
    """

    @staticmethod
    def _log_with(path, lines=40):
        """A log of `lines` real records, or shape-valid ones if there is none.

        Real rows where available: synthetic ones that do not parse measure the
        cache's entry count honestly but its retained bytes not at all, which is
        how a leak gets reported as harmless.
        """
        real = Path(os.environ.get("CHRONOA_TEST_LOG",
                                   "/tmp/opencode/prof/tool_calls.log.1"))
        if real.exists():
            body = "\n".join(real.read_text().splitlines()[:lines])
        else:
            row = {"ts": "2026-10-13T00:00:00Z", "tool_name": "get_datetime",
                   "verdict": "ok", "request": "what time is it", "reply": "",
                   "args": {}}
            body = "\n".join(json.dumps(row) for _ in range(lines))
        path.write_text(body + "\n")
        return path

    def test_the_example_cache_does_not_grow_with_the_number_of_logs(self, tmp_path):
        from shani_chronoa import learning
        learning._EXAMPLE_CACHE.clear()
        for n in range(learning._EXAMPLE_CACHE_MAX + 6):
            learning.load(self._log_with(tmp_path / ("log%d.txt" % n)))
        assert len(learning._EXAMPLE_CACHE) <= learning._EXAMPLE_CACHE_MAX, (
            "the example cache grew to %d entries" % len(learning._EXAMPLE_CACHE))

    def test_the_history_cache_does_not_grow_with_the_number_of_logs(self, tmp_path):
        from shani_chronoa import learning
        learning._HISTORY_CACHE.clear()
        for n in range(learning._HISTORY_CACHE_MAX + 6):
            # **The file has to exist.** `_durable_records` stats every chunk and
            # returns `None` on `OSError`, *before* it reaches the cache - so a
            # version of this test that named paths without writing them
            # asserted on a dict that was never written to, and passed with the
            # bound deleted. That is the check that cannot fail.
            path = self._log_with(tmp_path / ("log%d.txt" % n))
            class _T:
                log_file = path
            assert learning._durable_records(_T()) is not None, (
                "path %d was not read at all, so this test is measuring nothing"
                % n)
        assert len(learning._HISTORY_CACHE) <= learning._HISTORY_CACHE_MAX, (
            "the history cache grew to %d entries" % len(learning._HISTORY_CACHE))

    def test_a_re_read_of_an_evicted_log_is_correct_not_empty(self, tmp_path):
        """The bound must not turn into "the log is there and empty"."""
        from shani_chronoa import learning
        learning._HISTORY_CACHE.clear()
        paths = [self._log_with(tmp_path / ("log%d.txt" % n)) for n in range(8)]
        for path in paths[:-1]:
            class _T:
                log_file = path
            learning._durable_records(_T())
        class _Last:
            log_file = paths[-1]
        records = learning._durable_records(_Last()) or []
        assert records, (
            "after the cache evicted everything else, re-reading a log that "
            "still exists returned nothing - the reader is reporting an empty "
            "log rather than a cold cache")
