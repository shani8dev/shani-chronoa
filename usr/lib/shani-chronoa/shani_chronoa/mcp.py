"""A real MCP (Model Context Protocol) server exposing Chronoa's skills.

This replaces an earlier version of this module that only *looked* like MCP
support: it spoke a hand-rolled `/tools/list` + `/tools/call` REST protocol
of its own invention, was never actually the Model Context Protocol, and was
never wired into anything (instantiated in `app.py` and never touched
again). See AGENTS.md's "Cross-repo impact" section for that history.

Chronoa's actual value here is being an MCP *server*, not a client: it
already has a whitelisted set of local system skills
(`shani_chronoa/skills/`) that any MCP-speaking tool (Claude Desktop, Claude
Code, Cursor, etc.) can call once this is registered as one of that tool's
MCP servers. Each skill's existing Ollama-style tool schema
(`{"type": "function", "function": {...}}`) is translated into a real MCP
tool by dynamically building a matching Python function signature - MCP's
`add_tool()` derives a tool's input schema by inspecting the Python function
you hand it (it has no path for "just take this JSON schema as-is"),
confirmed by trying the obvious shortcut first: a generic
`def _wrapper(**kwargs)` produces a broken schema with one literal field
named "kwargs" instead of the skill's real parameters. Setting
`__signature__` to a real `inspect.Signature` built from the skill's schema
is what actually works - verified end to end against every built-in skill
(zero-arg, required, optional-with-default, int, string, and bool
parameters all round-tripped correctly through a real `tools/call`).

Run as a standalone process (`shani-chronoa-mcp`), separate from the GTK
app - that's how MCP servers are normally wired into a host's config
(`command`/`args` in a JSON config, not embedded in another app's event
loop), and it means this works even when the GTK app itself isn't running.

## Trust model

This server is **stdio-only**: it is launched as a subprocess by an MCP
host (Claude Desktop, Claude Code, Cursor, ...) and speaks JSON-RPC over
that process's stdin/stdout. There is deliberately no SSE or HTTP
transport. A stdio client is a **same-user trusted process** - anything
that can launch this server can already run arbitrary code as the same
user, so no authentication is performed or needed. The skills it exposes
are arbitrary same-user Python: built-in skills ship with the package, and
user skills dropped into `~/.config/shani-chronoa/skills/` are imported
and executed with the user's full privileges. Treat this server as
equivalent to running a local script, not as a network service.
"""

import inspect
import logging
from typing import Optional

from shani_chronoa.skills import is_valid_schema
from shani_chronoa.tools import TOOLS, execute_tool

logger = logging.getLogger(__name__)

try:
    from mcp.server.mcpserver import MCPServer
except Exception as e:  # pragma: no cover - exercised only when the optional dep is missing
    MCPServer = None  # type: ignore
    logger.debug(f"mcp package not available: {e}")

_JSON_TYPE_MAP = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}

_SERVER_INSTRUCTIONS = (
    "Local system skills exposed by the Shani Chronoa voice assistant: "
    "open applications, adjust volume, set timers, check battery, and "
    "search the web on this machine. "
    "This server runs over stdio only and is intended for same-user trusted "
    "clients; the skills it exposes are arbitrary Python code running with "
    "the invoking user's privileges."
)


def is_available() -> bool:
    """Check whether the `mcp` package is installed."""
    return MCPServer is not None


def _build_tool_function(schema: dict):
    """Build a Python function whose signature matches a skill's JSON schema.

    `MCPServer.add_tool()` infers a tool's input schema from the Python
    function's signature (type hints, required-vs-defaulted params) rather
    than accepting a pre-built JSON schema directly, so this reconstructs an
    equivalent signature from the skill's existing Ollama-style schema
    instead of hand-writing one wrapper per skill (which would silently
    stop covering new skills dropped into ~/.config/shani-chronoa/skills/).
    Returns None for a malformed schema so the caller can skip it
    deterministically instead of crashing the whole server.
    """
    if not is_valid_schema(schema):
        logger.warning(f"Skipping malformed tool schema in MCP server: {schema!r}")
        return None
    function_schema = schema["function"]
    name = function_schema.get("name", "tool")
    params_schema = function_schema.get("parameters", {}) or {}
    properties = params_schema.get("properties", {}) or {}
    required = set(params_schema.get("required", []) or [])

    parameters = []
    for prop_name, prop_schema in properties.items():
        py_type = _JSON_TYPE_MAP.get(prop_schema.get("type"), str)
        if prop_name in required:
            default = inspect.Parameter.empty
        else:
            default = None
            py_type = Optional[py_type]
        parameters.append(
            inspect.Parameter(prop_name, inspect.Parameter.POSITIONAL_OR_KEYWORD, default=default, annotation=py_type)
        )

    def _wrapper(**kwargs) -> str:
        # Drop unfilled optional args rather than passing literal Nones
        # through to the skill's own default handling.
        call_args = {k: v for k, v in kwargs.items() if v is not None}
        return execute_tool(name, call_args)

    _wrapper.__name__ = name
    _wrapper.__doc__ = function_schema.get("description", "")
    _wrapper.__signature__ = inspect.Signature(parameters)
    return _wrapper


def build_server() -> "MCPServer":
    """Build an MCP server exposing every currently-loaded Chronoa skill."""
    if not is_available():
        raise RuntimeError("mcp package is not installed")

    server = MCPServer(
        name="shani-chronoa",
        instructions=_SERVER_INSTRUCTIONS,
    )
    for schema in TOOLS:
        fn = _build_tool_function(schema)
        if fn is None:
            continue
        function_schema = schema["function"]
        server.add_tool(fn, name=function_schema.get("name"), description=function_schema.get("description"))
    return server


def main() -> None:
    """Entry point for the standalone `shani-chronoa-mcp` server process."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    if not is_available():
        logger.error("mcp package is not installed - install it to run shani-chronoa-mcp")
        raise SystemExit(1)
    server = build_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
