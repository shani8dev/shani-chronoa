"""Taking an answer back and asking again.

A small model gets a question wrong in a way a person can see and a bigger one
would not have got wrong: the tool was the wrong one, or the arguments were
half-guessed. Alpaca's per-message "regenerate" is the cheapest possible fix for
that - no editing, no new conversation, no retyping the question - and it is
also how a person compares the local model against a cloud one.

Three things have to be true, and each has a way of being quietly wrong:

- the question is re-asked **without the user retyping it**, which means the
  answer has to be taken back out of history first;
- **the saved transcript is rewritten**, not appended to, or the next process
  restores the answer the user just threw away;
- the **tool results** that turn left behind go too. A `tool` message with no
  question above it is not a conversation, and it is exactly what
  `history_repair` exists to clean up after a crash - so regenerate must not
  create the same damage deliberately.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import conversation_store  # noqa: E402
from shani_chronoa.assistant import Assistant  # noqa: E402


class _FakeLLM:
    async def chat_message(self, messages, tools=None):
        return {"role": "assistant", "content": "stub"}


@pytest.fixture
def transcript(tmp_path):
    return tmp_path / "current.jsonl"


def _answered(transcript, question="what is on my disk?", answer="these files"):
    assistant = Assistant(_FakeLLM(), session_path=transcript)
    assistant._record({"role": "user", "content": question})
    assistant._record({"role": "assistant", "content": answer})
    return assistant


class TestRewrite:
    def test_the_file_holds_exactly_what_was_kept(self, transcript):
        conversation_store.append({"role": "user", "content": "one"}, transcript)
        conversation_store.append({"role": "assistant", "content": "two"}, transcript)
        assert conversation_store.rewrite([{"role": "user", "content": "one"}], transcript)
        assert conversation_store.load(transcript) == [{"role": "user", "content": "one"}]

    def test_an_empty_transcript_is_a_real_state(self, transcript):
        """Regenerating the first turn of a conversation leaves nothing, and that
        has to be a file the next process can load without complaint."""
        conversation_store.append({"role": "user", "content": "one"}, transcript)
        assert conversation_store.rewrite([], transcript)
        assert conversation_store.load(transcript) == []

    def test_the_permissions_do_not_lose_the_tightening(self, transcript):
        """A transcript is a private record of everything the user said; the
        temp-file-and-rename must not leave it world-readable."""
        conversation_store.rewrite([{"role": "user", "content": "one"}], transcript)
        assert transcript.stat().st_mode & 0o777 == 0o600

    def test_a_message_the_format_does_not_know_is_refused(self, transcript):
        conversation_store.append({"role": "user", "content": "one"}, transcript)
        before = transcript.read_text()
        assert not conversation_store.rewrite([{"role": "narrator", "content": "x"}], transcript)
        assert transcript.read_text() == before

    def test_nothing_is_left_behind_on_failure(self, tmp_path):
        """The temp file is named per-pid, so a failed rewrite in a directory
        with an index must not leave a stray `.jsonl.1234.tmp` for the next
        process to trip over."""
        root = tmp_path / "sessions"
        root.mkdir()
        path = root / "20260101-000000-abcd.jsonl"
        conversation_store.rewrite([{"role": "user", "content": "one"}], path)
        conversation_store.rewrite([{"role": "narrator", "content": "x"}], path)
        assert [p.name for p in root.iterdir()] == [path.name]

    def test_a_partly_written_file_never_replaces_a_good_one(self, transcript, monkeypatch):
        conversation_store.append({"role": "user", "content": "one"}, transcript)
        before = transcript.read_text()

        def _boom(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(conversation_store.os, "write", _boom)
        assert not conversation_store.rewrite([{"role": "user", "content": "two"}], transcript)
        assert transcript.read_text() == before


class TestDropLastReply:
    def test_it_returns_the_question_and_the_answer_it_took_back(self, transcript):
        assistant = _answered(transcript, "how much room is left?", "38G free")
        assert assistant.drop_last_reply() == ("how much room is left?", "38G free")

    def test_the_answer_is_gone_from_the_history(self, transcript):
        assistant = _answered(transcript)
        assistant.drop_last_reply()
        assert assistant.visible_turns() == []

    def test_earlier_turns_are_kept(self, transcript):
        assistant = _answered(transcript)
        assistant._record({"role": "user", "content": "and the temperature?"})
        assistant._record({"role": "assistant", "content": "warm"})
        question, _previous = assistant.drop_last_reply()
        assert question == "and the temperature?"
        assert [text for _role, text in assistant.visible_turns()] == \
            ["what is on my disk?", "these files"]

    def test_the_tool_results_of_that_turn_go_with_it(self, transcript):
        """A tool message left above the new answer is a message with no
        question above it - the damage `history_repair` cleans up after a crash,
        which regenerate must not do on purpose."""
        assistant = _answered(transcript)
        assistant._record({"role": "tool", "tool_call_id": "1", "content": "/home/x/a.txt"})
        assistant._record({"role": "assistant", "content": "a.txt"})
        assistant.drop_last_reply()
        assert [m["role"] for m in assistant._history] == ["system"]

    def test_the_saved_transcript_is_rewritten_not_appended_to(self, transcript):
        """Otherwise the next process starts by restoring the discarded answer."""
        assistant = _answered(transcript)
        assistant.drop_last_reply()
        assert conversation_store.load(transcript) == []
        restored = Assistant(_FakeLLM(), session_path=transcript)
        assert restored.visible_turns() == []

    def test_a_second_ask_records_one_copy_of_the_question(self, transcript):
        """The question is dropped along with the answer so `handle()` records it
        once; left in place, every later turn would see it asked again with no
        answer above it."""
        assistant = _answered(transcript)
        question, _previous = assistant.drop_last_reply()
        assistant._record({"role": "user", "content": question})
        assistant._record({"role": "assistant", "content": "a different answer"})
        contents = [m.get("content") for m in conversation_store.load(transcript)]
        assert contents == ["what is on my disk?", "a different answer"]

    def test_nothing_to_take_back_is_said_plainly(self, transcript):
        assistant = Assistant(_FakeLLM(), session_path=transcript)
        assert assistant.drop_last_reply() == ("", "")

    def test_a_history_with_no_question_is_not_truncated_to_the_system_prompt(self):
        """A history that somehow holds an assistant message with no user turn
        must not be emptied by a button - it reports nothing to re-ask."""
        assistant = Assistant(_FakeLLM())
        assistant._history.append({"role": "assistant", "content": "orphan"})
        assert assistant.drop_last_reply() == ("", "")
        assert len(assistant._history) == 2

    def test_the_tool_result_of_an_earlier_turn_survives(self, transcript):
        assistant = _answered(transcript)
        assistant._record({"role": "tool", "tool_call_id": "1", "content": "/home/x/a.txt"})
        assistant._record({"role": "user", "content": "how big is it?"})
        assistant._record({"role": "assistant", "content": "4K"})
        assistant.drop_last_reply()
        roles = [m["role"] for m in assistant._history]
        assert roles == ["system", "user", "assistant", "tool"]


class TestTheTranscriptControl:
    def _buttons(self, view):
        found, stack = [], [view]
        while stack:
            node = stack.pop(0)
            if type(node).__name__ == "Button":
                found.append(node)
            child = node.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
        return {b.get_tooltip_text(): b for b in found}

    @staticmethod
    def _gtk():
        gi = pytest.importorskip("gi")
        gi.require_version("Gtk", "4.0")
        gi.require_version("Gio", "2.0")

    def test_no_handler_means_no_control(self):
        gi = pytest.importorskip("gi")
        gi.require_version("Gtk", "4.0")
        from shani_chronoa.gui.widgets import TranscriptView

        view = TranscriptView()
        view.add_assistant_turn("38G free")
        assert "Ask that again" not in self._buttons(view)

    def test_the_control_calls_the_handler_when_clicked(self):
        gi = pytest.importorskip("gi")
        gi.require_version("Gtk", "4.0")
        from shani_chronoa.gui.widgets import TranscriptView

        asked = []
        view = TranscriptView(on_regenerate=lambda: asked.append(True))
        view.add_assistant_turn("38G free")
        self._buttons(view)["Ask that again"].emit("clicked")
        assert asked == [True]


class _CountingLLM(_FakeLLM):
    """Records every question it was asked, so the re-ask is observable."""

    def __init__(self):
        self.asked: list = []

    async def chat_message(self, messages, tools=None):
        self.asked.append(messages[-1].get("content"))
        return {"role": "assistant", "content": f"answer {len(self.asked)}"}


def _buttons(widget):
    found, stack = [], [widget]
    while stack:
        node = stack.pop(0)
        if type(node).__name__ == "Button":
            found.append(node)
        child = node.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return found


def _labels(widget):
    """Every string a real label under `widget` is showing."""
    found, stack = [], [widget]
    while stack:
        node = stack.pop(0)
        if type(node).__name__ in ("Label", "Text"):
            text = node.get_label() if hasattr(node, "get_label") else node.get_text()
            found.append(text)
        child = node.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return "\n".join(found)


class TestTheApplicationPath:
    """The whole chain, from the button in a real window to a real Assistant.

    Three objects each have to be wired to the next one, and a wiring that is
    missing anywhere leaves the control doing nothing that looks like work. So
    this clicks the real button and checks the two things that can only come
    from the far end: what the model was asked, and what the saved transcript
    holds afterwards.

    `_submit` is replaced with a recorder rather than driven: it starts the
    speech queue and the async bridge, and what it does with the text is covered
    by the send-path tests. What is under test here is that regenerate reaches
    it with the right text.
    """

    def _app(self, transcript):
        gi = pytest.importorskip("gi")
        gi.require_version("Gtk", "4.0")
        gi.require_version("Gio", "2.0")

        from shani_chronoa.app import ChronoaApplication
        from shani_chronoa.gui import ChronoaWindow

        llm = _CountingLLM()
        application = ChronoaApplication()
        application.llm = llm
        application.assistant = Assistant(llm, session_path=transcript)
        application.window = ChronoaWindow(application)
        application.attach_regenerate()
        application.submitted = []
        application._submit = lambda text: application.submitted.append(text)
        return application, llm

    def test_the_button_re_asks_the_same_question(self, transcript):
        application, llm = self._app(transcript)
        window = application.window

        window.add_user_turn("what is on my disk?")
        window.set_response("these files")
        llm.asked.append("what is on my disk?")
        assistant = application.assistant
        assistant._record({"role": "user", "content": "what is on my disk?"})
        assistant._record({"role": "assistant", "content": "these files"})

        regenerate = [b for b in _buttons(window._transcript)
                      if b.get_tooltip_text() == "Ask that again"]
        assert regenerate, "no regenerate control on the assistant's reply"
        regenerate[0].emit("clicked")

        assert application.submitted == ["what is on my disk?"]
        assert [m["role"] for m in assistant._history] == ["system"]
        shown = _labels(window._transcript)
        assert "what is on my disk?" in shown
        assert "these files" not in shown, "the discarded answer is still on screen"

    def test_the_question_is_on_screen_while_the_new_answer_is_computed(self, transcript):
        """The history drops the question so it is recorded once, so something
        else has to put it back on screen or the transcript blinks empty."""
        application, _llm = self._app(transcript)
        window = application.window
        window.add_user_turn("how much room is left?")
        window.set_response("38G free")
        assistant = application.assistant
        assistant._record({"role": "user", "content": "how much room is left?"})
        assistant._record({"role": "assistant", "content": "38G free"})

        application._regenerate()

        shown = _labels(window._transcript)
        assert shown.count("how much room is left?") == 1
        assert "38G free" not in shown

    def test_asking_again_with_nothing_to_ask_again_says_so(self, transcript):
        application, _llm = self._app(transcript)
        application._regenerate()
        assert "no answer" in application.window._detail_label.get_label()
        assert application.submitted == []

    def test_no_assistant_means_no_control_at_all(self, transcript):
        """A button that reports "there is no answer to ask again" on every
        conversation is worse than not offering it."""
        application, _llm = self._app(transcript)
        application.assistant = None
        application.attach_regenerate()
        application.window.set_response("an answer")
        assert [b for b in _buttons(application.window._transcript)
                if b.get_tooltip_text() == "Ask that again"] == []
