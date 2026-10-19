"""Use tools from other MCP servers - the client half `mcp.py` never had.

`mcp.py` is a **server**: it offers Chronoa's skills to Claude Desktop, an IDE,
any MCP client. Nothing let Chronoa itself use a tool someone else serves - a
database, a ticket tracker, a company's internal API - which every other agent
can. This is that half, and it is small on purpose: the stdio transport is
newline-delimited JSON-RPC 2.0, and three methods carry the whole of tool use
(`initialize`, `tools/list`, `tools/call`). No SDK dependency.

**Configuration** is the `mcp-servers` setting, in the same shape Claude
Desktop's `mcpServers` uses, so an existing config pastes in unchanged::

    {"files": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "~/notes"]},
     "tickets": {"command": "/usr/bin/tickets-mcp", "env": {"TOKEN": "..."}}}

**Each server's tools become Chronoa tools** named `mcp_<server>_<tool>`, with
the server's own description and input schema, and the consent key
`mcp-client-enabled` in the description (which is how `capabilities.gated_by`
gates them - one switch for every external tool, asked per call while off).

**Every call is its own server process**: started, initialised, asked, closed.
Slower than a pooled connection and much simpler to reason about - no session
state survives between calls, and a server that hangs costs one call's timeout
rather than the app's.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SETTING = "mcp-servers"
CONSENT_KEY = "mcp-client-enabled"
PREFIX = "mcp_"
PROTOCOL_VERSION = "2025-06-18"
LIST_TIMEOUT = 20
CALL_TIMEOUT = 120
#: The argument the dispatcher adds so the child knows which server and tool.
ROUTE_ARG = "__mcp"

_lock = threading.Lock()
#: tool name -> (server, remote tool name), filled by `discover()`.
_routes: Dict[str, Tuple[str, str]] = {}
#: server -> what discovery found: a tool count, or the error.
_status: Dict[str, str] = {}


def servers(raw: Optional[str] = None) -> Dict[str, dict]:
    """The configured servers, `{name: {"command", "args", "env"}}`.

    A malformed setting yields `{}` and is logged - a typo in someone's JSON
    must not stop the app from starting.
    """
    if raw is None:
        try:
            from shani_chronoa.config import ChronoaConfig
            raw = ChronoaConfig().get(SETTING, "")
        except Exception:  # noqa: BLE001
            raw = ""
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except ValueError as exc:
        logger.warning("mcp-servers is not valid JSON: %s", exc)
        return {}
    if isinstance(data, dict) and isinstance(data.get("mcpServers"), dict):
        data = data["mcpServers"]       # a whole Claude Desktop config, pasted
    out = {}
    for name, spec in (data.items() if isinstance(data, dict) else ()):
        if isinstance(spec, dict) and isinstance(spec.get("command"), str):
            out[str(name)] = spec
    return out


def tool_name(server: str, tool: str) -> str:
    """`mcp_<server>_<tool>`, reduced to what a tool name may contain."""
    raw = f"{PREFIX}{server}_{tool}"
    return re.sub(r"[^A-Za-z0-9_-]", "_", raw)[:64]


class _Session:
    """One server process for one exchange."""

    def __init__(self, spec: dict, timeout: float) -> None:
        argv = [os.path.expanduser(spec["command"]),
                *[os.path.expanduser(str(a)) for a in spec.get("args") or []]]
        env = dict(os.environ)
        env.update({str(k): str(v) for k, v in (spec.get("env") or {}).items()})
        self._timeout = timeout
        self._next = 0
        self._proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, env=env, bufsize=1)

    def request(self, method: str, params: dict) -> dict:
        self._next += 1
        ident = self._next
        self._send({"jsonrpc": "2.0", "id": ident, "method": method, "params": params})
        result: dict = {}
        done = threading.Event()

        def read() -> None:
            for line in self._proc.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue        # a server's stray log line on stdout
                if message.get("id") == ident:
                    result.update(message)
                    break
            done.set()

        threading.Thread(target=read, daemon=True).start()
        if not done.wait(self._timeout):
            raise TimeoutError(f"no answer to {method} within {self._timeout:.0f}s")
        if "error" in result:
            error = result["error"]
            raise RuntimeError(error.get("message") if isinstance(error, dict) else str(error))
        if "result" not in result:
            raise RuntimeError(f"the server closed without answering {method}")
        return result["result"]

    def notify(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method})

    def _send(self, message: dict) -> None:
        self._proc.stdin.write(json.dumps(message) + "\n")
        self._proc.stdin.flush()

    def __enter__(self) -> "_Session":
        self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "shani-chronoa", "version": "0.1"}})
        self.notify("notifications/initialized")
        return self

    def __exit__(self, *_exc) -> None:
        try:
            self._proc.stdin.close()
        except OSError:
            pass
        try:
            self._proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._proc.kill()


def list_tools(spec: dict, timeout: float = LIST_TIMEOUT) -> List[dict]:
    with _Session(spec, timeout) as session:
        return list(session.request("tools/list", {}).get("tools") or [])


def call_tool(spec: dict, tool: str, arguments: dict, timeout: float = CALL_TIMEOUT) -> str:
    with _Session(spec, timeout) as session:
        result = session.request("tools/call", {"name": tool, "arguments": arguments})
    parts = []
    for item in result.get("content") or []:
        if item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
        else:
            parts.append(f"[{item.get('type')} content]")
    text = "\n".join(parts).strip() or "(the tool returned nothing)"
    return ("The tool reported an error: " + text) if result.get("isError") else text


def schema_for(server: str, tool: dict) -> dict:
    """A Chronoa tool schema for one remote tool, gated by the client key."""
    description = str(tool.get("description") or "").strip()
    parameters = tool.get("inputSchema")
    if not isinstance(parameters, dict) or parameters.get("type") != "object":
        parameters = {"type": "object", "properties": {}}
    return {"type": "function", "function": {
        "name": tool_name(server, str(tool.get("name") or "")),
        "description": (f"[MCP server '{server}'] {description} Requires the "
                        f"'{CONSENT_KEY}' consent key."),
        "parameters": parameters,
    }}


def discover(registry: list, handlers: dict) -> Dict[str, str]:
    """List every configured server's tools and add them to `registry`.

    `registry`/`handlers` are `tools.TOOLS` and its handler table, mutated in
    place - the assistant holds a reference to the same list. Tools from a
    previous discovery are removed first, so a server taken out of the setting
    takes its tools with it. Returns `{server: what happened}`.
    """
    found: Dict[str, str] = {}
    fresh, routes = [], {}
    for server, spec in servers().items():
        try:
            tools = list_tools(spec)
        except Exception as exc:  # noqa: BLE001 - one broken server is one line
            found[server] = f"not reachable: {exc}"
            continue
        for tool in tools:
            schema = schema_for(server, tool)
            fresh.append(schema)
            routes[schema["function"]["name"]] = (server, str(tool.get("name") or ""))
        found[server] = f"{len(tools)} tool(s)"
    with _lock:
        registry[:] = [t for t in registry
                       if not str(t.get("function", {}).get("name", "")).startswith(PREFIX)]
        for name in list(handlers):
            if name.startswith(PREFIX):
                del handlers[name]
        registry.extend(fresh)
        for name in routes:
            handlers[name] = run
        _routes.clear()
        _routes.update(routes)
        _status.clear()
        _status.update(found)
    return found


def route(name: str) -> Optional[Tuple[str, str]]:
    with _lock:
        return _routes.get(name)


def status() -> Dict[str, str]:
    with _lock:
        return dict(_status)


def run(arguments: dict) -> str:
    """The handler every MCP tool shares; runs in the skill's child process.

    The dispatcher adds `ROUTE_ARG` = `[server, tool]`, because a child process
    has none of this module's discovery state.
    """
    arguments = dict(arguments or {})
    target = arguments.pop(ROUTE_ARG, None)
    if not (isinstance(target, list) and len(target) == 2):
        return "Not run: this MCP tool was called without its server."
    server, tool = str(target[0]), str(target[1])
    spec = servers().get(server)
    if spec is None:
        return f"Not run: no MCP server named '{server}' is configured any more."
    try:
        return call_tool(spec, tool, arguments)
    except Exception as exc:  # noqa: BLE001 - the model needs the reason
        return f"The MCP server '{server}' could not run {tool}: {exc}"
