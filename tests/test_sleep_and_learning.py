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
