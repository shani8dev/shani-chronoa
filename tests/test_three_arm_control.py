"""The three-arm decoder run's control arm: the grammar is the only variable.

**What was measured before this file existed.** `AGENTS.md` prescribed a
grammar arm and said that without a third arm "any gain would be attributed to
the grammar when the paper says the register may be doing it, and a check that
cannot tell two causes apart is not check". arXiv:2607.18476 found across 44
models that asking for JSON *already* compresses the answer distribution, and
that `response_format` compressed no further than the request (-0.03 bits).

So the control arm (`+greedy+request`) sends the same instruction, the same
tool menu and the same schema shape, and differs only in whether
`response_format` reaches llama-server. These tests hold that claim to the
letters - they are the reason the arms are comparable, and the first version of
the control was a prompt that differed in more than the grammar, which would
have made the whole comparison worthless.
"""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
import sys

if str(ROOT / "usr" / "lib" / "shani-chronoa") not in sys.path:
    sys.path.insert(0, str(ROOT / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import local_llm


def offered_tools():
    from shani_chronoa.tools import TOOLS
    keep = {"get_datetime", "get_weather", "set_timer", "ask_user"}
    return [t for t in TOOLS if t["function"]["name"] in keep]


@pytest.fixture
def offered():
    from shani_chronoa.tools import TOOLS
    keep = {"get_datetime", "get_weather", "set_timer", "ask_user"}
    return [t for t in TOOLS if t["function"]["name"] in keep]


class TestTheShapeRequestSaysWhatTheGrammarWouldHaveSaid:
    """Without enforcement the prompt is the only channel, so it must carry the
    shape. A vague "reply with JSON" would not be the same experiment."""

    def test_it_names_every_field_and_marks_the_optional_ones(self, offered):
        from shani_chronoa.local_llm import _shape_request, argument_schema
        weather = next(t for t in offered if t["function"]["name"] == "get_weather")
        text = _shape_request(argument_schema(weather))
        params = (weather["function"].get("parameters") or {})
        for key in params.get("properties") or {}:
            assert key in text, f"{key} is not in the request {text!r}"
        for key in params.get("required") or []:
            assert key in text

    def test_the_tool_choice_lists_the_offered_names_and_none(self, offered):
        from shani_chronoa.local_llm import _shape_request, pick_schema
        text = _shape_request(pick_schema(offered))
        for tool in offered:
            assert tool["function"]["name"] in text, (
                f"{tool['function']['name']} is not offered in the request")
        assert "none" in text, "the arm that answers in words is not in the request"

    def test_it_asks_for_json_and_nothing_else(self, offered):
        from shani_chronoa.local_llm import _shape_request, pick_schema
        text = _shape_request(pick_schema(offered))
        assert "JSON" in text
        assert "no other text" in text


class TestThePromptsArePinnedToTheirExactText:
    """Golden strings, because a refactor here would move both arms together.

    `_arm_prompt`, `_decision_prompt` and `_argument_prompt` were lifted out of
    `_constrained` after a 220-case run had already been launched against the
    inline version. **Both arms would have moved together**, so every score in
    that run stays comparable to the refactored code - or none of them do, and
    there is no way to tell from the numbers. Verified once against the inline
    code across 40 combinations (5 tools x 2 heads x 2 arms x 2 steps): all
    byte-identical. These literals make that a standing property rather than a
    one-off claim.
    """

    def test_the_decision_prompt(self, offered):
        schema = local_llm.pick_schema(offered)
        assert local_llm._decision_prompt("", offered, schema, enforce=True) == (
            "\n\nDecide what to do with the user's last message. If one of these tools does it, choose that "
            "tool. If it is a question you can answer yourself, or chat, choose none.\nTools:\n"
            + local_llm.tool_menu(offered))

    def test_the_argument_prompt(self, offered):
        weather = next(t for t in offered if t["function"]["name"] == "get_weather")
        schema = local_llm.argument_schema(weather)
        assert local_llm._argument_prompt("", weather, schema, enforce=True) == (
            "\n\nCall get_weather: " + weather["function"].get("description", "")
            + "\nGive its arguments for the user's last message.")

    def test_the_head_is_prepended_verbatim(self, offered):
        schema = local_llm.pick_schema(offered)
        for head in ("", "You are Chronoa. ", "x" * 4000):
            for enforce in (True, False):
                assert local_llm._decision_prompt(head, offered, schema, enforce).startswith(head)


class TestTheControlArmIsTheGrammarArmPlusASuffix:
    """**The single variable.** If this fails the two arms are not comparable
    and a difference between them cannot be read as the grammar's."""

    def test_the_decision_prompt_differs_only_by_the_shape(self, offered):
        schema = local_llm.pick_schema(offered)
        grammar = local_llm._decision_prompt("HEAD", offered, schema, enforce=True)
        control = local_llm._decision_prompt("HEAD", offered, schema, enforce=False)
        assert control.startswith(grammar), (
            "the control arm does not contain the grammar arm's prompt as a "
            "prefix, so something other than the shape differs")
        assert control[len(grammar):] == local_llm._shape_request(schema), (
            "the control arm adds something other than the shape")

    def test_the_argument_prompt_differs_only_by_the_shape(self, offered):
        weather = next(t for t in offered if t["function"]["name"] == "get_weather")
        schema = local_llm.argument_schema(weather)
        grammar = local_llm._argument_prompt("HEAD", weather, schema, enforce=True)
        control = local_llm._argument_prompt("HEAD", weather, schema, enforce=False)
        assert control.startswith(grammar)
        assert control[len(grammar):] == local_llm._shape_request(schema)

    def test_the_grammar_arm_carries_no_prose_shape(self, offered):
        """Otherwise the grammar arm is quietly two arms at once."""
        schema = local_llm.pick_schema(offered)
        grammar = local_llm._decision_prompt("HEAD", offered, schema, enforce=True)
        assert local_llm._shape_request(schema) not in grammar, (
            "the enforced arm also spells the schema out, so the two arms differ "
            "in two places and the grammar is not the only variable")


class TestEnforcementIsTheOnlyPayloadDifference:
    """`enforce=False` must send the identical request with no
    `response_format`. llama-server builds the grammar from the schema, so this
    switch *is* the grammar."""

    def _drive(self, monkeypatch, **kwargs):
        """Call the real `_json` and record the payload it would send.

        Driven against the **real** `_Local` instance rather than a reading of
        the source, because a test that reads its own subject cannot fail - and
        the first version of this file did exactly that.
        """
        import asyncio
        from shani_chronoa import cloud_llm
        sent = []

        async def fake_chat(self, messages, tools=None, stream=False):
            sent.append(dict(self.extra_payload))
            return {"role": "assistant", "content": '{"tool": "get_datetime"}'}

        monkeypatch.setattr(cloud_llm.OpenAICompatibleLLM, "chat_message", fake_chat)
        llm = local_llm.LocalLLM()
        got = asyncio.run(llm._json([{"role": "user", "content": "what time is it?"}],
                                    {"type": "object"}, **kwargs))
        return sent, got

    def test_enforced_sends_a_json_schema_response_format(self, monkeypatch):
        sent, _ = self._drive(monkeypatch, enforce=True)
        assert sent, "nothing was sent at all"
        fmt = sent[0].get("response_format")
        assert fmt and fmt["type"] == "json_schema", (
            "the enforced arm did not ask for a grammar: %r" % (sent[0],))
        assert "schema" in fmt["json_schema"]

    def test_unenforced_sends_nothing_at_all(self, monkeypatch):
        sent, _ = self._drive(monkeypatch, enforce=False)
        assert sent, "nothing was sent at all"
        assert "response_format" not in sent[0], (
            "the control arm still sent a response_format, so it was not a control")

    def test_the_default_is_enforced(self, monkeypatch):
        """The control is opt-in, so an unedited path keeps today's behaviour."""
        sent, _ = self._drive(monkeypatch)
        assert "response_format" in sent[0], (
            "calling `_json` without `enforce` no longer constrains the output, "
            "so every existing caller silently became the control arm")

    def test_both_arms_return_the_same_json(self, monkeypatch):
        """The stub answers the same either way; the parse must not depend on
        the grammar being there."""
        _, enforced = self._drive(monkeypatch, enforce=True)
        _, free = self._drive(monkeypatch, enforce=False)
        assert enforced == free == {"tool": "get_datetime"}


class TestTheTwoArmsAreNotBothOn:
    """`+greedy+constrained` must stay a pure grammar run. Both flags on at once
    would produce an arm that is neither."""

    def _flags(self, config):
        """(CONSTRAINED, REQUEST_ONLY) after `build_llm` has set them.

        **Two wrong calls in a row, both hidden by exception handling.** The
        first called `make_llm`, which does not exist, and swallowed the
        `AttributeError`; the second called `build_llm` with one argument when it
        takes two, and reclassified the `TypeError` as "a provider may refuse to
        build". Both times the flags read as their defaults and
        `test_plain_greedy_has_neither` passed **vacuously** - the one test that
        could have caught it asserts an absence, and absent is what a broken
        harness produces.
        """
        import importlib.util
        spec = importlib.util.spec_from_file_location("task_eval", ROOT / "tools" / "task_eval.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert hasattr(module, "build_llm"), "task_eval no longer has build_llm"
        # **No try/except.** The second version of this wrapped the call and
        # reclassified the failure, and the call was wrong - `build_llm` takes
        # `(config, cloud)` - so a `TypeError` slipped through the guard and
        # both arms were read as their defaults. `LocalLLM()` needs no server, so
        # any exception here is a real defect and belongs in the failure message.
        module.build_llm(config, "")
        return local_llm.CONSTRAINED, local_llm.REQUEST_ONLY

    def test_the_grammar_arm_does_not_also_ask_in_prose(self):
        constrained, request = self._flags("+greedy+constrained")
        assert constrained is True, "the grammar arm was not a grammar arm"
        assert request is False, "the grammar arm also ran the control, so it was two arms"

    def test_the_control_arm_does_not_also_enforce(self):
        constrained, request = self._flags("+greedy+request")
        assert request is True, "the control arm was not run"
        assert constrained is False, "the control arm was also enforced, so it was not a control"

    def test_plain_greedy_has_neither(self):
        """Meaningful only because the two tests above prove a flag can be True."""
        constrained, request = self._flags("+greedy")
        assert constrained is False and request is False, (
            "a plain greedy run turned an arm on")


class TestTheRequestArmReachesTheWireWithoutAGrammar:
    """The flags being right is not enough - the **dispatch** has to pass them
    on. This is the seam `chat_message` uses, driven end to end against the real
    `_Local`, so a grammar that leaks into the control arm shows up here rather
    than only in a 220-case number nobody would suspect."""

    def _run(self, monkeypatch, **flags):
        import asyncio
        from shani_chronoa import cloud_llm
        sent = []

        async def fake_chat(self, messages, tools=None, stream=False):
            sent.append(dict(self.extra_payload))
            return {"role": "assistant", "content": '{"tool": "get_datetime"}'}

        monkeypatch.setattr(cloud_llm.OpenAICompatibleLLM, "chat_message", fake_chat)
        for name, value in flags.items():
            monkeypatch.setattr(local_llm, name, value)
        llm = local_llm.LocalLLM()
        reply = asyncio.run(llm.chat_message(
            [{"role": "system", "content": "You are Chronoa."},
             {"role": "user", "content": "what time is it?"}], tools=offered_tools()))
        return sent, reply

    def test_the_request_arm_sends_no_grammar_and_still_makes_a_call(self, monkeypatch):
        sent, reply = self._run(monkeypatch, CONSTRAINED=False, REQUEST_ONLY=True)
        assert sent, "the request arm sent nothing"
        assert not any("response_format" in p for p in sent), (
            "the request arm reached the wire with a grammar on it: %r" % sent)
        calls = reply.get("tool_calls") or []
        assert calls and calls[0]["function"]["name"] == "get_datetime", (
            "the request arm made no tool call, so it is not comparable to the "
            "grammar arm: %r" % reply)

    def test_the_grammar_arm_sends_a_grammar_on_both_steps(self, monkeypatch):
        sent, reply = self._run(monkeypatch, CONSTRAINED=True, REQUEST_ONLY=False)
        assert len(sent) == 2, "the grammar arm is two steps, got %d" % len(sent)
        assert all("response_format" in p for p in sent), (
            "a grammar step went out unconstrained: %r" % sent)


class TestTheArmsAreDeclared:
    """A config that no test names is a config that rots."""

    def test_all_three_arms_are_in_the_configs_tuple(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("task_eval", ROOT / "tools" / "task_eval.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for arm in ("+greedy", "+greedy+constrained", "+greedy+request"):
            assert arm in module.CONFIGS, f"{arm} is not runnable"

    def test_the_docstring_documents_the_control(self):
        text = (ROOT / "tools" / "task_eval.py").read_text(encoding="utf-8")
        assert "+greedy+request" in text.split("def load_cases")[0], (
            "the control arm is undocumented in the header")