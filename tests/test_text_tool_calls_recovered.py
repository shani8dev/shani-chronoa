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

XML = ("I'll open the site.\n<tool_call>\n<function=browse>\n<parameter=action>\nnavigate\n</parameter>\n"
       "<parameter=url>\nhttps://blazedemo.com/\n</parameter>\n<parameter=timeout>\n30\n</parameter>\n"
       "<parameter=visible>\ntrue\n</parameter>\n</function>\n</tool_call>")


def test_the_xml_form_becomes_a_real_call_with_typed_arguments():
    out = recover_tool_calls({"role": "assistant", "content": XML}, TOOLS)
    assert out["content"] == "" and len(out["tool_calls"]) == 1
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
