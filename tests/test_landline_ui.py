"""The landline's call cue offers the call tools to the assistant.

The Phone page's dialer tests moved to shani-link (tests/test_app_landline_ui.py)
with the page on 2026-11-04; what stays is the assistant's half - that "call
Amma" brings the call tools into the model's tool list.
"""


def test_the_call_cue_offers_the_call_tools():
    from shani_chronoa import tool_select as ts
    tools = [{"function": {"name": n}} for n in ("landline_call", "bluetooth_call", "web_search")]
    offered = {t["function"]["name"] for t in ts.select_tools("call Amma", tools)}
    assert "landline_call" in offered and "bluetooth_call" in offered
