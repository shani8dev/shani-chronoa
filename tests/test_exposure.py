"""What can be reached from outside the process, and how little of it there is.

Asked after a reviewer raised the risk that harnesses accepting commands over a
channel have been the source of bad evenings. This audits the answer rather than
asserting it, because "we are local-first" is a claim about configuration and
configuration drifts.

Four things can be a door, and each is checked from the source rather than from
a memory of it:

- **a listening socket** - every `--host` Chronoa passes to a server binary has
  to be a loopback address, because a model server on `0.0.0.0` is a model server
  anyone on the network can use;
- **an exported bus name** - the session bus is the one thing a channel *can*
  use, so what is exported there is the whole inbound surface;
- **a stdio or socket MCP server** - the tools are the most dangerous thing in
  the tree, so what the MCP transport accepts matters more than what the skills
  do;
- **a gateway** - the new one, and the narrowest of them: one method.

The point of the test is that adding a second door would fail here rather than in
somebody's threat model.
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

PACKAGE = Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa/shani_chronoa"


def _sources():
    for path in sorted(PACKAGE.rglob("*.py")):
        if "tests" in path.parts:
            continue
        yield path, path.read_text(encoding="utf-8")


def test_no_server_is_asked_to_bind_beyond_loopback():
    """Every `--host` a model server is started with.

    A model server bound to `0.0.0.0` is a model server anyone on the network
    can use, and it is the single most common way a "local" assistant stops
    being local.
    """
    offenders = []
    for path, source in _sources():
        for match in re.finditer(r'"--host",\s*([^\)\n]+)', source):
            value = match.group(1)
            # A named constant is fine as long as the constant is loopback;
            # `HOST` and `model_service.HOST` are checked below.
            offenders.append((path.name, value.strip()))
    assert not offenders or all(
        "HOST" in value for _name, value in offenders
    ), f"a literal --host that is not a constant: {offenders}"


def test_every_host_constant_is_loopback():
    """`local_llm.HOST`, `stt_server.HOST`, `model_service.HOST` and friends."""
    import shani_chronoa.local_llm as llm
    import shani_chronoa.model_service as ms
    import shani_chronoa.stt_server as ss

    for name, module in (("local_llm", llm), ("model_service", ms),
                        ("stt_server", ss)):
        host = getattr(module, "HOST", "127.0.0.1")
        assert host in ("127.0.0.1", "localhost", "::1"), (
            f"{name}.HOST is {host!r}: a model server on anything else is "
            "reachable from the network")


def test_the_stt_server_binds_loopback_too():
    """The one that is easy to forget, because it is started by a skill."""
    # Matched as a tuple too: it is written `HOST, PORT = "127.0.0.1", 8766`, and a
    # regex for the bare form silently matched nothing - which is a test that
    # passes while checking nothing.
    source = (PACKAGE / "stt_server.py").read_text(encoding="utf-8")
    assert re.search(r'^HOST(?:\s*,\s*PORT)?\s*=\s*"127\.0\.0\.1"', source, re.M), (
        "stt_server.py starts a whisper.cpp server; it must bind loopback")


def test_the_mcp_server_is_stdio_only():
    """The tools are the dangerous part of the tree, so the transport matters.

    MCP servers are normally stdio: the host launches them as a subprocess and
    speaks JSON-RPC over its pipes. A socket here would be a skill server
    reachable from the network, which is a different and much larger decision.
    """
    source = (PACKAGE / "mcp.py").read_text(encoding="utf-8")
    for forbidden in ("HTTPServer", "socketserver", "AF_INET", "listen("):
        assert forbidden not in source, f"mcp.py contains {forbidden}"
    assert "stdio" in source.lower(), (
        "the module should still say which transport it is, so a future change "
        "has to be deliberate")


def test_the_only_bus_names_exported_are_the_ones_we_know():
    """The session bus is the one thing a channel can use, so this is the whole
    inbound surface, and it should be a short list."""
    exported = set()
    for path, source in _sources():
        for match in re.finditer(r'bus_own_name\w*\([^,]*,\s*"([\w.]+)"', source):
            exported.add(match.group(1))
        for match in re.finditer(r'bus_own_name_on_connection\([^,]+,\s*([A-Z_]+),',
                                 source):
            exported.add(f"<constant {match.group(1)}>")
    assert exported <= {"dev.shani.chronoa.SearchProvider",
                        "<constant BUS_NAME>"}, (
        f"a new bus name is being exported: {sorted(exported)}")


def test_the_gateway_exposes_exactly_one_method():
    """One method, `Submit`. A second one is a second door."""
    source = (PACKAGE / "gateway.py").read_text(encoding="utf-8")
    methods = re.findall(r"<method name=\"(\w+)\"", source)
    assert methods == ["Submit"], f"the gateway interface grew: {methods}"
    # And the class behind it has one public verb plus Introspect.
    from shani_chronoa import gateway
    public = [name for name in dir(gateway._Service)
              if not name.startswith("_")]
    assert public == ["Introspect", "Submit"], public


def test_a_gateway_cannot_reach_a_tool():
    """The whole argument, as an assertion rather than a comment."""
    # **Parsed, not grepped.** The first version of this searched the file's
    # text and failed on the module's own docstring, which explains that a
    # submission still goes through `tools.execute_tool_outcome` - the sentence
    # is the argument, and the grep could not tell it from a call. So the
    # identifiers are collected from the AST, where a docstring is not a name.
    import ast

    from shani_chronoa import gateway
    tree = ast.parse((PACKAGE / "gateway.py").read_text(encoding="utf-8"))
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            called.add(node.attr)
        elif isinstance(node, ast.Name):
            called.add(node.id)
    for forbidden in ("execute_tool", "execute_tool_outcome", "run_skill",
                      "subprocess", "system", "popen", "Popen", "eval"):
        assert forbidden not in called, f"gateway.py reaches for {forbidden}"
    # And the registry has no method that runs anything.
    assert not any("exec" in name.lower() or "tool" in name.lower()
                   for name in dir(gateway.Registry)), (
        "the registry grew something that sounds like execution")


def test_a_gateway_turn_goes_through_the_windows_own_submit():
    """So it meets the same whitelist, the same consent keys and the same log.

    If a channel had its own path into the assistant, every gate the window
    respects would be optional for whoever found the other path - which is how a
    consent key stops meaning anything.
    """
    from shani_chronoa.app import application
    source = Path(application.__file__).read_text(encoding="utf-8")
    assert "self._submit(text)" in source, (
        "a gateway submission must go through the same _submit the window uses")
    assert "_submit_gateway_text" in source


def test_the_gateway_is_off_unless_asked_for():
    """A machine with no gateway configured must have nothing listening."""
    from shani_chronoa import gateway
    from shani_chronoa.app import application
    source = Path(application.__file__).read_text(encoding="utf-8")
    assert "if not self._gateways.names():" in source, (
        "exporting unconditionally would put an inbound interface on every "
        "installation, including the ones that never asked for one")
    # And the module itself refuses to be created for a name that would break
    # the bus method naming.
    with pytest.raises(ValueError):
        gateway.Registry(lambda text: "").register("bad/name")
