"""Quick Ask: one question, one answer, and nothing kept unless asked for.

The window is driven as a person drives it - text into the field, the Ask button
pressed, the answer read off the real label - because the parts that go wrong
here are wiring, not logic: a checkbox that is never read, an answer that never
reaches the label, a kept turn that never reaches the conversation. Each of
those reads as correct in the source.

The three promises in the module's own docstring are the assertions:
nothing is written to the conversation, nothing is spoken, and the microphone
is never opened.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Gio", "2.0")

from shani_chronoa.assistant import Assistant  # noqa: E402
from shani_chronoa.gui import QuickAskWindow  # noqa: E402


class _FakeLLM:
    def __init__(self):
        self.asked: list = []

    def is_available(self):
        return True

    async def chat_message(self, messages, tools=None):
        self.asked.append(messages[-1].get("content"))
        return {"role": "assistant", "content": f"answer to {messages[-1].get('content')}"}


def _pump_main_context(limit_s: float = 5.0) -> None:
    """Drain the idle callbacks the bridge uses to deliver its result."""
    import time

    from gi.repository import GLib

    context = GLib.MainContext.default()
    deadline = time.time() + limit_s
    while time.time() < deadline and context.pending():
        context.iteration(False)
        time.sleep(0.01)


def _window(**kwargs):
    """A Quick Ask whose asker answers immediately, unless one is given."""
    asked, kept = [], []

    def on_ask(text, done):
        asked.append(text)
        done(kwargs.pop("answer", "Lisbon is 14:32"))

    return QuickAskWindow(on_ask=on_ask, on_keep=lambda q, a: kept.append((q, a)))


def _button(window, label):
    found, stack = [], [window]
    while stack:
        node = stack.pop(0)
        if type(node).__name__ == "Button" and node.get_label() == label:
            found.append(node)
        child = node.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return found[0] if found else None


class TestAsking:
    def test_pressing_ask_shows_the_answer(self):
        window = _window()
        window._entry.set_text("what time is it in Lisbon")
        _button(window, "Ask").emit("clicked")
        assert window._answer.get_text() == "Lisbon is 14:32"
        assert window._answer.get_visible()

    def test_return_in_the_field_asks_too(self):
        """The field is the obvious way to finish a question, and a window that
        only answers on a button click is a small annoyance every time."""
        window = _window()
        window._entry.set_text("is the disk full")
        window._entry.emit("activate")
        assert window._answer.get_text() == "Lisbon is 14:32"

    def test_an_empty_question_is_not_sent(self):
        asked = []
        window = QuickAskWindow(on_ask=lambda text, done: asked.append(text))
        window._entry.set_text("   ")
        _button(window, "Ask").emit("clicked")
        assert asked == []

    def test_a_failure_is_shown_and_not_left_looking_like_an_answer(self):
        """The previous answer staying on screen under an error reads as \"it
        answered the same thing again\"."""

        def on_ask(_text, done):
            done(RuntimeError("the model went away"))

        window = QuickAskWindow(on_ask=on_ask)
        window._entry.set_text("hello")
        _button(window, "Ask").emit("clicked")
        assert "the model went away" in window._answer.get_text()

    def test_asking_again_replaces_the_answer(self):
        window = _window()
        window._entry.set_text("first")
        _button(window, "Ask").emit("clicked")
        window._entry.set_text("second")
        _button(window, "Ask").emit("clicked")
        assert window._answer.get_text() == "Lisbon is 14:32"

    def test_a_second_press_while_busy_is_refused_rather_than_raced(self):
        """Two answers racing for one label: the user reads whichever finished
        second, which is not necessarily the question they last asked."""
        pending = []
        window = QuickAskWindow(on_ask=lambda text, done: pending.append((text, done)))
        window._entry.set_text("first")
        _button(window, "Ask").emit("clicked")
        window._entry.set_text("second")
        _button(window, "Ask").emit("clicked")
        assert [text for text, _done in pending] == ["first"]
        pending[0][1]("the first answer")
        assert window._answer.get_text() == "the first answer"

    def test_the_button_works_again_after_the_answer(self):
        window = _window()
        window._entry.set_text("hello")
        button = _button(window, "Ask")
        button.emit("clicked")
        assert button.get_sensitive()
        button.emit("clicked")


class TestKeeping:
    def test_nothing_is_kept_by_default(self):
        kept = []
        window = QuickAskWindow(on_ask=lambda t, done: done("x"),
                                on_keep=lambda q, a: kept.append((q, a)))
        window._entry.set_text("is the disk full")
        _button(window, "Ask").emit("clicked")
        assert kept == [], "an unasked-for question was written into the conversation"

    def test_the_box_off_means_not_kept_even_after_a_kept_one(self):
        """A checkbox that latches on is a privacy setting that silently changed
        its own answer."""
        kept = []
        window = QuickAskWindow(on_ask=lambda t, done: done("x"),
                                on_keep=lambda q, a: kept.append((q, a)))
        window._keep.set_active(True)
        window._entry.set_text("first")
        _button(window, "Ask").emit("clicked")
        window._keep.set_active(False)
        window._entry.set_text("second")
        _button(window, "Ask").emit("clicked")
        assert [q for q, _a in kept] == ["first"]

    def test_a_failure_is_not_kept(self):
        kept = []
        window = QuickAskWindow(on_ask=lambda t, done: done(RuntimeError("nope")),
                                on_keep=lambda q, a: kept.append((q, a)))
        window._keep.set_active(True)
        window._entry.set_text("hello")
        _button(window, "Ask").emit("clicked")
        assert kept == []


def _code_without_docstrings(path: Path) -> str:
    """The module's code, with every docstring removed.

    A source grep for "speak" matches the sentence explaining why this window
    does not speak, which is how a check like that ends up asserting nothing.
    """
    import ast

    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


class TestTheNoAudioPromises:
    def test_the_window_has_no_speak_path_and_no_microphone(self):
        """The whole reason this surface is safe to pop up over someone's work
        is that it is silent and does not listen. Neither is reachable by
        clicking anything, so this is checked against the code - docstrings
        stripped, or the words explaining the rule would match the rule's own
        keywords."""
        code = _code_without_docstrings(_REPO / "usr/lib/shani-chronoa/shani_chronoa/gui/quick_ask.py")
        for forbidden in ("tts", "synthesize", "speak", "pw-record", "pw-play",
                          "arecord", "aplay", "listening", "AudioRecorder", "record"):
            assert forbidden not in code, forbidden

    def test_nothing_audio_can_come_through_the_constructor_arguments(self):
        """A caller cannot hand this window a speaker or a microphone either,
        which is the same promise from the other side."""
        import inspect

        from shani_chronoa.gui.quick_ask import QuickAskWindow

        parameters = set(inspect.signature(QuickAskWindow.__init__).parameters)
        assert parameters == {"self", "application", "on_ask", "on_keep"}


class TestTheApplicationWiring:
    """The window asks through the real assistant, and keeps nothing by default.

    The conversation is checked before and after rather than through the UI,
    because that is the promise: a Quick Ask question must not be findable in
    the saved transcript afterwards, whatever the window says.
    """

    def _app(self, tmp_path):
        from shani_chronoa.app import ChronoaApplication
        from shani_chronoa.gui import ChronoaWindow

        llm = _FakeLLM()
        app = ChronoaApplication()
        app.llm = llm
        app.assistant = Assistant(llm, session_path=tmp_path / "current.jsonl")
        app.window = ChronoaWindow(app)
        return app, llm

    def test_an_ask_never_reaches_the_saved_conversation(self, tmp_path):
        from shani_chronoa import conversation_store

        app, llm = self._app(tmp_path)
        done = []
        # The bridge answers on the GTK thread, so the answer only arrives once
        # the main loop has run - asserting straight after the call would pass
        # for a window that never answers at all.
        future = app._quick_ask_run("what time is it in Lisbon", done.append)
        future.result(timeout=10)
        _pump_main_context()
        assert llm.asked == ["what time is it in Lisbon"]
        assert done == ["answer to what time is it in Lisbon"]
        assert conversation_store.load(tmp_path / "current.jsonl") == []
        assert app.assistant.visible_turns() == []

    def test_keeping_writes_it_as_a_normal_turn(self, tmp_path):
        from shani_chronoa import conversation_store

        app, _llm = self._app(tmp_path)
        app._quick_ask_keep("what time is it in Lisbon", "14:32")
        assert app.assistant.visible_turns() == [
            ("user", "what time is it in Lisbon"), ("assistant", "14:32")]
        assert len(conversation_store.load(tmp_path / "current.jsonl")) == 2

    def test_no_model_says_so_rather_than_asking_nothing(self, tmp_path):
        app, _llm = self._app(tmp_path)
        app.llm = None
        done = []
        app._quick_ask_run("hello", done.append)
        assert done == ["No model is available to answer with."]

    def test_the_action_opens_one_window_and_reuses_it(self, tmp_path):
        app, _llm = self._app(tmp_path)
        app.quick_ask()
        first = app._quick_ask_window
        assert first is not None
        app.quick_ask()
        assert app._quick_ask_window is first, "a second popup for one shortcut press"
        first.close()

    def test_the_shortcut_is_registered(self, tmp_path):
        app, _llm = self._app(tmp_path)
        app._create_actions()
        # GTK normalises the accelerator itself ("<Shift><Control>a"), so the
        # modifiers are compared as a set rather than as one exact string.
        accels = app.get_accels_for_action("app.quick-ask")
        assert len(accels) == 1, accels
        modifiers = set(re.findall(r"<([A-Za-z]+)>", accels[0]))
        assert accels[0].split(">")[-1] == "a", accels[0]
        assert {"Shift", "Control"} <= modifiers, accels[0]

    def test_the_action_is_wired_to_the_window(self, tmp_path):
        """A registered action nobody handles is a shortcut that does nothing -
        the same class of bug as a button with no handler."""
        app, _llm = self._app(tmp_path)
        app._create_actions()
        action = app.lookup_action("quick-ask")
        assert action is not None and action.get_enabled()
        action.activate(None)
        assert app._quick_ask_window is not None, "the action did not open the popup"
        app._quick_ask_window.close()