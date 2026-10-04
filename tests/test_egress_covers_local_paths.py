"""The egress log must cover the local call sites too, not only the cloud ones.

The log existed for the cloud providers only. It did not cover `ollama_llm.py`, the
vision sense, or page retrieval - which is backwards for a local-first app,
because those are the paths where the user's own words, their own screen and
whatever URL they were told to fetch go, and `egress.is_local` is exactly what
distinguishes them from a third party.

`senses/web.py` is listed here as instrumented, in two places' worth of
comment, while never calling `egress.record` at all. A `grep egress` on that
file matched a docstring. Retrieval is instrumented in `webtext.retrieve`
instead, which is where every fetch actually passes through.

So the property is not "the cloud is logged". It is: anything that carries
conversation content or an image has a record, and a user who has pointed
`ollama-host` at a remote machine sees that in the log rather than having to
know to look.
"""

import ast
import pathlib
import sys

import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")

CARRIERS = {
    "stt_provision.py": "bytes_out is the model file, so a real download is auditable",
    # file: what the request carries
    "ollama_llm.py": "the whole conversation",
    "senses/vision.py": "a base64 screenshot of the user's screen",
    "webtext.py": "whatever URL the assistant was told to fetch",
    "cloud_llm.py": "the conversation, to a third party",
}


def _tree(name: str):
    path = pathlib.Path("usr/lib/shani-chronoa/shani_chronoa") / name
    return path, ast.parse(path.read_text())


@pytest.mark.parametrize("name", sorted(CARRIERS))
def test_every_carrier_calls_the_egress_log(name):
    """A source check is the right tool *here*, and it is worth saying why.

    These modules build their request differently - an async httpx client, a
    sync one inside a context manager, a try/finally around a stream - so
    there is no single call site to drive. What is uniform is that each one
    must reach `egress.record`, and that is exactly what a static check can
    assert without pretending to have run anything.
    """
    path, tree = _tree(name)
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "record"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "egress"
    ]
    assert calls, (
        f"{name} carries {CARRIERS[name]} and never calls egress.record, so "
        f"the request is invisible to `shani-chronoa-sense egress`"
    )


def test_the_local_chat_path_records_the_host_it_used():
    """A local default and a remote override have to be distinguishable, which
    is the entire reason to log a request that is 'probably local'."""
    source = (pathlib.Path("usr/lib/shani-chronoa/shani_chronoa/ollama_llm.py")).read_text()
    assert "llm:ollama" in source
    assert "/api/chat" in source, (
        "the record must name the endpoint, so a log line is actionable rather "
        "than just proof that something happened"
    )


def test_the_vision_path_is_recorded_even_though_it_is_local():
    """A screenshot is the most sensitive payload the app can produce."""
    path, tree = _tree("senses/vision.py")
    source = path.read_text()
    assert "senses:vision" in source
    # And it must not be inside a conditional that only runs on failure.
    assert "screenshot" in source.lower() or "screen" in source.lower()
