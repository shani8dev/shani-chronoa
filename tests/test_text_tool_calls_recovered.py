"""Tool calls a model writes as text are run, on the cloud path too.

Seen in a real Chronoa run on 2026-10-08: a free Kilo-routed model answered
with Qwen3-Coder's XML form (`<tool_call><function=browse><parameter=url>...`)
instead of `tool_calls`, and Chronoa showed the markup to the person as its
reply. Two gaps: `recover_tool_calls` read only JSON inside `<tool_call>`, and
the cloud client never called it (its `postprocess` returned the message as is).
"""

import json

from shani_chronoa import cloud_llm
from shani_chronoa.local_llm import recover_tool_calls

TOOLS = [{"type": "function", "function": {"name": "browse", "parameters": {
    "type": "object", "properties": {"action": {"type": "string"}, "url": {"type": "string"},
                                     "timeout": {"type": "number"}, "visible": {"type": "boolean"}}}}}]

# The two tools the balanced-adapter ladder found the `function` spelling on.
# Without them in the offer, `recover_tool_calls` refuses the call - which is the
# offered-set rule working, and is why the first version of these tests failed
# against a fixture offering only `browse`.
TOOLS += [
    {"type": "function", "function": {"name": "reminders", "parameters": {
        "type": "object", "properties": {"number": {"type": "integer"},
                                         "text": {"type": "string"},
                                         "due": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "get_weather", "parameters": {
        "type": "object", "properties": {"place": {"type": "string"}}}}},
]

XML = ("I'll open the site.\n<tool_call>\n<function=browse>\n<parameter=action>\nnavigate\n</parameter>\n"
       "<parameter=url>\nhttps://blazedemo.com/\n</parameter>\n<parameter=timeout>\n30\n</parameter>\n"
       "<parameter=visible>\ntrue\n</parameter>\n</function>\n</tool_call>")


def test_the_xml_form_becomes_a_real_call_with_typed_arguments():
    out = recover_tool_calls({"role": "assistant", "content": XML}, TOOLS)
    # The prose survives. This assertion said `content == ""` until 2026-10-11,
    # so it pinned the defect rather than the claim: the fixture's own first line
    # is "I'll open the site.", and a person watching a browser open never saw
    # it, because recovery overwrote the whole reply. Shape from edge0's
    # `_split`, which cuts only the spans that became calls.
    assert out["content"] == "I'll open the site."
    assert len(out["tool_calls"]) == 1
    call = out["tool_calls"][0]["function"]
    assert call["name"] == "browse"
    assert json.loads(call["arguments"]) == {"action": "navigate", "url": "https://blazedemo.com/",
                                             "timeout": 30, "visible": True}


def test_a_tool_that_was_not_offered_stays_text():
    text = "<function=delete_everything><parameter=path>/</parameter></function>"
    out = recover_tool_calls({"role": "assistant", "content": text}, TOOLS)
    assert out == {"role": "assistant", "content": text}


def test_a_value_that_is_not_its_type_is_kept_as_text():
    text = "<function=browse><parameter=timeout>soon</parameter></function>"
    call = recover_tool_calls({"role": "assistant", "content": text}, TOOLS)["tool_calls"][0]
    assert json.loads(call["function"]["arguments"]) == {"timeout": "soon"}


def test_the_cloud_client_recovers_them_too():
    backend_cls = next(c for c in vars(cloud_llm).values()
                       if isinstance(c, type) and "postprocess" in vars(c))
    backend = backend_cls.__new__(backend_cls)
    out = backend.postprocess({"role": "assistant", "content": XML}, TOOLS)
    assert out.get("tool_calls"), "the cloud path showed the markup instead of running the call"


def test_a_reply_that_is_only_a_json_call_is_run():
    """Seen live: `{"name": "browse", "parameters": {...}}` ended a booking after four steps."""
    text = '{"name": "browse", "parameters": {"action": "click", "label": "Find Flights"}}'
    out = recover_tool_calls({"role": "assistant", "content": text}, TOOLS)
    assert out["tool_calls"][0]["function"]["name"] == "browse"
    assert json.loads(out["tool_calls"][0]["function"]["arguments"]) == {"action": "click", "label": "Find Flights"}


def test_an_answer_that_quotes_json_is_left_alone():
    text = 'The site sent back {"name": "browse", "parameters": {}} which is odd.'
    assert recover_tool_calls({"role": "assistant", "content": text}, TOOLS)["content"] == text


#: One tool, one string parameter. A named constant rather than the nested
#: literal repeated per test: written out three times it was wrong once, and a
#: `SyntaxError` in the guard is the least of what a hand-kept copy costs.
TZ_TOOLS = [{"type": "function", "function": {
    "name": "get_datetime",
    "parameters": {"type": "object", "properties": {"tz": {"type": "string"}}}}}]


class TestRecoveryKeepsWhatItDidNotConsume:
    """**A recovered call must not delete the assistant's own words.**

    Measured 2026-10-11 against the shape the fixture above already carried:
    every one of these lost its prose entirely and logged nothing, because the
    only copy was the string the old code overwrote with `""`.

        "Let me check that for you." + call + "That is the current time."
          -> content "" , the tool runs, two sentences vanish

    The person watches a browser open and hears nothing, which is
    indistinguishable from the assistant going quiet mid-sentence.
    """

    def test_prose_before_and_after_the_call_survives(self):
        text = ("Let me check that for you.\n"
                "<tool_call>\n<function=get_datetime>\n"
                "<parameter=tz>\nEurope/Madrid\n</parameter>\n</function>\n</tool_call>\n"
                "That is the current time.")
        out = recover_tool_calls({"role": "assistant", "content": text}, TZ_TOOLS)
        assert len(out["tool_calls"]) == 1
        assert out["content"] == "Let me check that for you.\n\nThat is the current time."

    def test_the_empty_wrapper_is_not_left_behind(self):
        """The complaint this file opens with, one layer down.

        Cutting only the `<function>` span left `"<tool_call>\\n\\n</tool_call>"`
        in the reply - markup shown to the person as a reply, which is the exact
        thing `XML`'s own header says this recovery exists to stop.
        """
        out = recover_tool_calls({"role": "assistant", "content": XML}, TOOLS)
        assert "tool_call" not in out["content"]
        assert "<function" not in out["content"]

    def test_a_reply_that_is_only_the_call_is_still_empty(self):
        out = recover_tool_calls({"role": "assistant", "content":
                                  "<tool_call>\n<function=get_datetime>\n"
                                  "<parameter=tz>\nUTC\n</parameter>\n</function>\n</tool_call>"},
                                 TZ_TOOLS)
        assert len(out["tool_calls"]) == 1
        assert out["content"] == ""

    def test_fenced_json_keeps_the_prose_around_it(self):
        text = ("Here it is:\n```json\n"
                '{"name": "get_datetime", "arguments": {"tz": "UTC"}}\n```\nAnything else?')
        out = recover_tool_calls({"role": "assistant", "content": text}, TZ_TOOLS)
        assert len(out["tool_calls"]) == 1
        assert out["content"] == "Here it is:\n\nAnything else?"
        assert "```" not in out["content"]

    def test_json_inside_a_tool_call_wrapper_keeps_its_prose(self):
        """The JSON path with a wrapper around it, which is the real shape.

        **This does not make `_widen` load-bearing, and the mutation run proved
        it.** The first version of this docstring said it did. `_TOOL_TAG` and
        `_JSON_FENCE` both match the *whole* `<tool_call>...</tool_call>`
        wrapper, so the span they hand over already contains the tags and
        widening it is a no-op; every consumed span on the JSON path is either
        already-widened or `(0, len(content))`. `_widen` earns its keep only in
        the XML branch, where `_XML_FUNCTION` matches the inner
        `<function=...>` alone - and removing it there fails five tests.

        The call is kept on both paths so the invariant reads the same in both,
        and this test stays because the prose surviving is a real claim whatever
        makes it true.
        """
        text = ("One moment.\n<tool_call>\n"
                '{"name": "get_datetime", "arguments": {"tz": "UTC"}}\n'
                "</tool_call>\nThat is it.")
        out = recover_tool_calls({"role": "assistant", "content": text}, TZ_TOOLS)
        assert len(out["tool_calls"]) == 1
        assert out["content"] == "One moment.\n\nThat is it."

    def test_two_calls_and_the_prose_between_them(self):
        text = ("One moment.\n<tool_call>\n<function=get_datetime>\n"
                "<parameter=tz>\nUTC\n</parameter>\n</function>\n</tool_call>\n"
                "And the other one.\n<tool_call>\n<function=get_datetime>\n"
                "<parameter=tz>\nEurope/Madrid\n</parameter>\n</function>\n</tool_call>")
        out = recover_tool_calls({"role": "assistant", "content": text}, TZ_TOOLS)
        assert len(out["tool_calls"]) == 2
        assert out["content"] == "One moment.\n\nAnd the other one."

    def test_a_reply_with_no_call_is_untouched(self):
        text = "It is about four in the afternoon."
        assert recover_tool_calls({"role": "assistant", "content": text},
                                 TZ_TOOLS) == {"role": "assistant", "content": text}


class TestAFunctionBlockMustParseWhole:
    """**A partial parse is a confident wrong answer.**

    `_XML_PARAMETER.findall` took the pairs that matched and ignored the rest, so
    a block carrying one usable argument and one line of prose produced a *real
    tool call* with the prose silently discarded. Nothing downstream can tell
    that apart from a clean parse. edge0's `_parse_arguments` refuses instead,
    and the refusal here means the text stays put - which is also what
    `text_tool_call()` reads to tell a person the model wrote something odd.
    """

    def test_stray_text_between_parameters_is_refused(self):
        text = ("<tool_call>\n<function=get_datetime>\n"
                "<parameter=tz>\nEurope/Madrid\n</parameter>\n"
                "the user is in Spain\n</function>\n</tool_call>")
        out = recover_tool_calls({"role": "assistant", "content": text},
                                TZ_TOOLS)
        assert not out.get("tool_calls"), (
            "a block with prose between its parameters was acted on anyway - "
            "the model gets called with the arguments that happened to parse "
            "and never learns the rest was dropped")
        assert "the user is in Spain" in out["content"]

    def test_a_block_that_is_whole_still_works(self):
        """**The control.** Without this the refusal above cannot fail."""
        text = ("<tool_call>\n<function=get_datetime>\n"
                "<parameter=tz>\nEurope/Madrid\n</parameter>\n</function>\n</tool_call>")
        out = recover_tool_calls({"role": "assistant", "content": text},
                                TZ_TOOLS)
        assert len(out["tool_calls"]) == 1
        assert json.loads(out["tool_calls"][0]["function"]["arguments"]) == {"tz": "Europe/Madrid"}

    def test_stray_text_IN_THE_MIDDLE_is_refused_too(self):
        """The case the trailing check cannot see, so this is what makes the
        *between*-pairs check load-bearing rather than redundant.

        The refusal above is two checks - stray text before the first pair or
        between pairs, and stray text after the last. A fixture whose junk is
        only trailing is caught by the second, so removing the first leaves it
        green; that is exactly what the mutation run found.
        """
        text = ("<tool_call>\n<function=get_datetime>\n"
                "<parameter=tz>\nUTC\n</parameter>\n"
                "the user is in Spain\n"
                "<parameter=tz>\nEurope/Madrid\n</parameter>\n"
                "</function>\n</tool_call>")
        out = recover_tool_calls({"role": "assistant", "content": text}, TZ_TOOLS)
        assert not out.get("tool_calls"), (
            "junk between two valid parameters was ignored and the call ran - "
            "the model receives a parameter it never wrote in that order")

    def test_whitespace_between_parameters_is_not_stray_text(self):
        text = ("<tool_call>\n<function=get_datetime>\n"
                "<parameter=tz>\nUTC\n</parameter>\n\n"
                "<parameter=tz>\nEurope/Madrid\n</parameter>\n</function>\n</tool_call>")
        out = recover_tool_calls({"role": "assistant", "content": text},
                                TZ_TOOLS)
        assert len(out["tool_calls"]) == 1, (
            "a blank line between parameters was treated as stray prose - the "
            "real template emits them, which is why the check is on stripped text")


# ---------------------------------------------------------------------------
# `function` as a third spelling of the name field, found by the balanced-adapter
# ladder rather than invented.
# ---------------------------------------------------------------------------

FLAT = '{"arguments": {"number": 1, "text": "Call mom"}, "function": "reminders"}'
NESTED = '{"function": {"name": "get_weather", "arguments": {"place": "Pune"}}}'


def test_the_flat_function_key_names_the_tool():
    """**Measured, not guessed.** The balanced-adapter ladder - three lumen
    adapters with their 111x delta-norm spread removed - answered "make a
    reminder" with exactly this object: right tool, right arguments, and the call
    left as text because only `name` was read. `function` is OpenAI's own wrapper
    key, so it is a spelling a small model converges on and not a private one.
    """
    out = recover_tool_calls({"role": "assistant", "content": FLAT}, TOOLS)
    calls = out.get("tool_calls") or []
    assert calls, "the flat `function` spelling was left as text"
    assert calls[0]["function"]["name"] == "reminders"
    assert json.loads(calls[0]["function"]["arguments"]) == {
        "number": 1, "text": "Call mom"}


def test_the_nested_openai_wrapper_is_unwrapped():
    """`{"function": {"name": ...}}` is the same call with its name nested.

    Handling the flat form and not the nested one would recover half of the
    spellings a model actually emits, which reads as coverage while missing the
    case that has a `name` inside.
    """
    out = recover_tool_calls({"role": "assistant", "content": NESTED}, TOOLS)
    calls = out.get("tool_calls") or []
    assert calls, "the nested wrapper was left as text"
    assert calls[0]["function"]["name"] == "get_weather"


def test_a_function_key_naming_a_tool_that_was_not_offered_stays_text():
    """The offered-set rule is the safety property and it is unchanged.

    A new key must not become a new way to call a tool nobody offered - which is
    the whole reason this function gates on `offered` at all.
    """
    text = '{"arguments": {"path": "/"}, "function": "delete_everything"}'
    assert recover_tool_calls({"role": "assistant", "content": text}, TOOLS) \
        == {"role": "assistant", "content": text}


def test_a_function_key_that_is_not_a_tool_name_stays_text():
    """`{"function": "get_datetime"}` names a tool; `{"function": 7}` does not.

    The nested branch reads `name` out of a dict, so the flat branch must refuse
    a non-string too - a dict, a list and a number are all things a malformed
    reply carries.
    """
    for bad in ('{"arguments": {}, "function": 7}',
                '{"arguments": {}, "function": ["get_weather"]}',
                '{"arguments": {}, "function": {"nested": "get_weather"}}',
                '{"arguments": {}, "function": null}'):
        out = recover_tool_calls({"role": "assistant", "content": bad}, TOOLS)
        assert "tool_calls" not in out, f"{bad} was turned into a call"
