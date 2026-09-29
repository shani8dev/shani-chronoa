"""The assistant had 68 skills and no way to ask a question.

Speech is ambiguous in a way a text box is not. "Delete the old one" has no
referent on a screen nobody can see, and in a conversation there is no list in
front of the user to pick from. So every ambiguous request could only be guessed
at, refused, or - worst - silently resolved to the wrong file.

What these tests hold onto is not that the question gets asked. It is that
**failing to ask never becomes guessing**, which is the way this feature could
quietly do real damage: a headless session or a dismissed prompt that returned
option 1 would make the assistant confidently delete the wrong thing while
looking helpful.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import ask_bridge  # noqa: E402
from shani_chronoa.tools import _LOCAL_TOOLS, execute_tool  # noqa: E402

QUESTION = {"question": "Which file should I open?", "options": ["a.txt", "b.txt"]}


@pytest.fixture(autouse=True)
def no_presenter():
    ask_bridge.set_presenter(None)
    yield
    ask_bridge.set_presenter(None)


def _answering(answer: str, delay: float = 0.05):
    def presenter(question, options):
        done, resolve = ask_bridge.make_event()
        threading.Thread(target=lambda: (time.sleep(delay), resolve(answer)),
                         daemon=True).start()
        return done
    return presenter


def _dismiss(delay: float = 0.05):
    def presenter(question, options):
        done, _ = ask_bridge.make_event()
        threading.Thread(target=lambda: (time.sleep(delay), done.set()),
                         daemon=True).start()
        return done
    return presenter


class TestNobodyToAsk:
    """A missing presenter must be a refusal, never a default choice."""

    def test_headless_says_nobody_is_there(self):
        out = execute_tool("ask_user", QUESTION)
        assert "nobody" in out.lower()
        assert "a.txt" not in out and "b.txt" not in out, (
            "a headless refusal mentioned an option, so the model may read it "
            "as a choice"
        )

    def test_a_broken_presenter_is_not_a_choice(self):
        def broken(question, options):
            raise RuntimeError("the window went away")
        ask_bridge.set_presenter(broken)
        out = execute_tool("ask_user", QUESTION)
        assert "a.txt" not in out and "b.txt" not in out

    def test_a_presenter_returning_nothing_is_not_a_choice(self):
        ask_bridge.set_presenter(lambda q, o: None)
        out = execute_tool("ask_user", QUESTION)
        assert "a.txt" not in out and "b.txt" not in out


class TestDismissalIsNotAChoice:
    def test_dismissed_reports_unanswered(self):
        ask_bridge.set_presenter(_dismiss())
        out = execute_tool("ask_user", QUESTION)
        assert "no option was chosen" in out
        assert "a.txt" not in out, (
            "a dismissed prompt came back as an option - the failure this whole "
            "feature could cause"
        )

    def test_a_timeout_reports_unanswered(self):
        ask_bridge.set_presenter(_dismiss(delay=5.0))
        out = ask_bridge.ask("Which?", ["a", "b"], timeout=0.2)
        assert out == "", "a timeout produced something other than no answer"


class TestARealAnswer:
    def test_the_chosen_option_comes_back(self):
        ask_bridge.set_presenter(_answering("b.txt"))
        out = execute_tool("ask_user", QUESTION)
        assert "b.txt" in out
        assert "a.txt" not in out

    def test_the_answer_crosses_the_thread_boundary(self):
        ask_bridge.set_presenter(_answering("b.txt"))
        assert ask_bridge.ask("Which?", ["a.txt", "b.txt"], timeout=5) == "b.txt"

    def test_the_options_reach_the_presenter_unchanged(self):
        seen = {}

        def presenter(question, options):
            seen["q"] = question
            seen["o"] = list(options)
            done, resolve = ask_bridge.make_event()
            resolve("a.txt")
            return done

        ask_bridge.set_presenter(presenter)
        execute_tool("ask_user", {"question": "Pick one",
                                  "options": ["alpha", "beta", "gamma"]})
        assert seen["o"] == ["alpha", "beta", "gamma"]
        assert seen["q"] == "Pick one"


class TestMalformedCalls:
    def test_one_option_is_refused(self):
        out = execute_tool("ask_user", {"question": "Which?", "options": ["only"]})
        assert "at least 2" in out

    def test_no_question_is_refused(self):
        """The skill refuses a missing question on its own.

        Called in-process, deliberately. Through `execute_tool` this is now
        caught earlier by the guardrail, which is the better place for it - but
        "the dispatcher noticed" must not quietly become "the skill stopped
        checking". A skill that relies on being called correctly is one schema
        edit away from crashing, and the gate has to be the skill's own.
        """
        from shani_chronoa.skills.ask_user import _run
        assert "no question" in _run({"options": ["a", "b"]})

    def test_the_dispatcher_also_catches_it_before_dispatch(self):
        """Belt and braces, and the outer layer is the one that reports first."""
        out = execute_tool("ask_user", {"options": ["a", "b"]})
        assert "required argument 'question'" in out
        assert "It accepts: options, question." in out, (
            "the refusal should tell the model what the tool does accept"
        )

    def test_empty_options_are_refused(self):
        out = execute_tool("ask_user", {"question": "Which?", "options": []})
        assert "at least 2" in out


class TestItIsALocalTool:
    def test_ask_user_runs_in_process(self):
        assert "ask_user" in _LOCAL_TOOLS

    def test_the_privilege_list_stays_short(self):
        """A local tool is unsandboxed. This list is the whole attack surface."""
        assert len(_LOCAL_TOOLS) <= 3, (
            f"{len(_LOCAL_TOOLS)} tools bypass the sandbox: "
            f"{sorted(_LOCAL_TOOLS)}. Each one needs a reason it cannot be "
            f"subprocessed."
        )

    def test_other_skills_are_still_sandboxed(self):
        assert "get_datetime" not in _LOCAL_TOOLS
        assert execute_tool("get_datetime", {}).strip(), "a normal skill stopped working"
