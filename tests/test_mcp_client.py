"""Chronoa as an MCP *client*: other servers' tools, used from Chronoa.

Driven against a real stdio server (written below, run as a subprocess), so the
JSON-RPC exchange - initialize, notifications/initialized, tools/list,
tools/call - is the one a real server sees, not a mocked transport.
"""
import json
import pathlib
import sys
import textwrap

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import mcp_client  # noqa: E402

SERVER = textwrap.dedent('''
    import json, sys
    for line in sys.stdin:
        msg = json.loads(line)
        ident = msg.get("id")
        if ident is None:
            continue
        m = msg["method"]
        if m == "initialize":
            r = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}}
        elif m == "tools/list":
            r = {"tools": [{"name": "shout", "description": "Upper-case text.",
                            "inputSchema": {"type": "object",
                                            "properties": {"text": {"type": "string"}}}},
                           {"name": "fail", "description": "Always errors."}]}
        elif m == "tools/call":
            if msg["params"]["name"] == "fail":
                r = {"isError": True, "content": [{"type": "text", "text": "nope"}]}
            else:
                r = {"content": [{"type": "text",
                                  "text": msg["params"]["arguments"]["text"].upper()}]}
        print(json.dumps({"jsonrpc": "2.0", "id": ident, "result": r}), flush=True)
''')


@pytest.fixture
def spec(tmp_path):
    path = tmp_path / "server.py"
    path.write_text(SERVER)
    return {"command": sys.executable, "args": [str(path)]}


def test_a_whole_claude_desktop_config_can_be_pasted():
    raw = json.dumps({"mcpServers": {"a": {"command": "x"}, "bad": {"args": []}}})
    assert list(mcp_client.servers(raw)) == ["a"], "a server with no command must be dropped"


def test_malformed_json_is_no_servers_not_a_crash():
    assert mcp_client.servers("{not json") == {}


def test_tools_are_listed_and_called(spec):
    names = [t["name"] for t in mcp_client.list_tools(spec)]
    assert names == ["shout", "fail"]
    assert mcp_client.call_tool(spec, "shout", {"text": "hi"}) == "HI"
    assert mcp_client.call_tool(spec, "fail", {}).startswith("The tool reported an error")


def test_discovery_registers_gated_tools_and_forgets_removed_servers(spec, monkeypatch):
    registry, handlers = [{"function": {"name": "own_skill"}}], {"own_skill": object()}
    monkeypatch.setattr(mcp_client, "servers", lambda raw=None: {"echo": spec})
    found = mcp_client.discover(registry, handlers)
    assert found == {"echo": "2 tool(s)"}
    names = [t["function"]["name"] for t in registry]
    assert names == ["own_skill", "mcp_echo_shout", "mcp_echo_fail"]
    shout = next(t for t in registry if t["function"]["name"] == "mcp_echo_shout")
    from shani_chronoa import capabilities
    assert capabilities.gated_by("mcp_echo_shout", shout["function"]["description"]) == \
        mcp_client.CONSENT_KEY, "an external tool must sit behind the client switch"
    assert mcp_client.route("mcp_echo_shout") == ("echo", "shout")
    # The server is removed from the setting: its tools go, Chronoa's stay.
    monkeypatch.setattr(mcp_client, "servers", lambda raw=None: {})
    mcp_client.discover(registry, handlers)
    assert [t["function"]["name"] for t in registry] == ["own_skill"]
    assert "own_skill" in handlers and "mcp_echo_shout" not in handlers


def test_an_unreachable_server_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(mcp_client, "servers",
                        lambda raw=None: {"gone": {"command": "/nonexistent-mcp-server"}})
    found = mcp_client.discover([], {})
    assert found["gone"].startswith("not reachable"), found


def test_the_shared_handler_needs_its_route(spec, monkeypatch):
    monkeypatch.setattr(mcp_client, "servers", lambda raw=None: {"echo": spec})
    assert mcp_client.run({"text": "x"}).startswith("Not run")
    assert mcp_client.run({"text": "yo", mcp_client.ROUTE_ARG: ["echo", "shout"]}) == "YO"
