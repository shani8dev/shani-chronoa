"""Build mode keeps going until the steps it wrote this turn are closed.

Driven through a real `Assistant.handle` with a scripted model and the real
`todo_list` store. What must hold:

- a reply that stops with this turn's steps open is continued, and one whose
  steps are closed is not;
- a model that never closes its steps ends after `MAX_CONTINUATIONS`, not never;
- steps already on the list never keep a turn running, and outside Build mode
  a turn is never continued;
- the rules (plan, prove, be honest) are in Build mode's own prompt clause.
"""
import asyncio
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import assistant as assistant_mod, buildmode, tools  # noqa: E402
from shani_chronoa.assistant import Assistant  # noqa: E402
from shani_chronoa.skills import todo_list  # noqa: E402


class _On:
    def get_bool(self, key, default=False):
        return True


def _call(name, args, ident):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": ident, "type": "function",
         "function": {"name": name, "arguments": json.dumps(args)}}]}


class _Model:
    """Plays a list of moves; each move is a tool call or a final answer.

    `closes` is how many continuation prompts it takes before the model closes
    step 2 - `None` never closes it.
    """

    def __init__(self, closes=1, plan=True):
        self.closes = closes
        self.plan = plan
        self.prompts = []       # every user message the model was shown last
        self.offered = []       # tool names offered on each call
        self.n = 0
        self.seen_users = 0

    async def chat_message(self, messages, tools=None):
        self.n += 1
        self.offered.append({t["function"]["name"] for t in (tools or [])})
        users = [m for m in messages if m.get("role") == "user"]
        last_user = users[-1]["content"]
        if len(users) > self.seen_users:  # a new prompt, even one worded the same
            self.seen_users = len(users)
            self.prompts.append(last_user)
        since = messages[messages.index(users[-1]) + 1:]
        done = sum(1 for m in since if m.get("role") == "tool")
        continuations = len(self.prompts) - 1
        if continuations == 0 and self.plan:
            moves = [("todo_list", {"action": "add", "content": "fix the bug"}),
                     ("todo_list", {"action": "add", "content": "run the tests"}),
                     ("todo_list", {"action": "update", "id": self.first, "status": "completed"})]
        elif self.closes is not None and continuations >= self.closes:
            moves = [("todo_list", {"action": "update", "id": self.first + 1, "status": "completed"})]
        else:
            moves = []
        if done < len(moves):
            name, args = moves[done]
            return _call(name, args, f"c{self.n}")
        return {"role": "assistant", "content": f"reply {continuations}"}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.delenv("CHRONOA_PROJECT", raising=False)
    monkeypatch.setattr(todo_list, "ChronoaConfig", lambda: _On())

    def execute_tool(name, arguments, by_reference=False, origin=None, **_):
        if name == "todo_list":
            return todo_list._run(arguments)
        return "ok"

    monkeypatch.setattr(assistant_mod, "execute_tool", execute_tool)
    monkeypatch.setattr(buildmode, "project", lambda: "/home/x/shop")
    yield
    tools.clear_current_turn()
    tools.set_current_channel("")


def _turn(model, text):
    model.first = max(buildmode.snapshot() or {0}) + 1
    return asyncio.run(Assistant(model).handle(text))


def test_an_open_step_is_continued_and_a_closed_list_ends_the_turn(store):
    model = _Model(closes=1)
    reply = _turn(model, "fix the discount bug")
    assert len(model.prompts) == 2, model.prompts
    assert "1 step(s) still open: run the tests" in model.prompts[1]
    assert reply == "reply 1"


def test_a_model_that_never_closes_its_steps_still_ends(store):
    model = _Model(closes=None)
    _turn(model, "fix the discount bug")
    assert len(model.prompts) == 1 + buildmode.MAX_CONTINUATIONS


def test_old_steps_never_keep_a_turn_running(store):
    todo_list._run({"action": "add", "content": "buy milk"})
    model = _Model(plan=False)
    _turn(model, "tidy the project")
    assert len(model.prompts) == 1, "a step from before the turn kept it running"


def test_outside_build_mode_a_turn_is_never_continued(store, monkeypatch):
    monkeypatch.setattr(buildmode, "project", lambda: "")
    model = _Model(closes=None)
    _turn(model, "fix the discount bug")
    assert len(model.prompts) == 1, "a turn outside Build mode was continued"


def test_the_rules_are_in_build_modes_clause(store):
    clause = buildmode.prompt_clause()
    assert "seen it work" in clause and "blocked" in clause and "still open" in clause
