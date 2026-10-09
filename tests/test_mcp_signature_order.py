"""A badly-ordered schema must not take the whole MCP server down.

`mcp.py` builds a Python signature from each skill's JSON schema, because the
`mcp` package infers a tool's parameters from the function's signature and
provides no way to hand it a pre-built schema. That translation has one
constraint a JSON schema does not: **a non-default argument may not follow a
defaulted one.**

So a schema whose `required` list names a property *after* an optional one
produced `ValueError: non-default argument follows default argument` from
`inspect.Signature` - and `build_server()` calls that for every tool, so one
badly-ordered schema lost all 200 of them rather than the one. `default_apps`
hit it once and was reordered by hand, which is a workaround the next author
cannot be expected to repeat.

Both fixes are asserted here from the same direction the defect arrived from:
a schema as an author would write it, not a pre-sorted one.

- the wrapper is built, and the required parameter is first;
- every shipped skill still produces the signature it did before, so the sort
  that fixes the bad case changes nothing for the 200 that were already valid
  (a stable sort preserves the author's order inside each group);
- a schema so malformed no signature exists is *skipped*, not raised - the
  module's own docstring promises that and it is now true.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import tools  # noqa: E402
from shani_chronoa.mcp import _build_tool_function  # noqa: E402


def _schema(**properties_required_after):
    """A valid schema whose `required` names a property after an optional one.

    Well-formed for `is_valid_schema` - that is the point: the validation this
    module already runs cannot see the defect, so the signature construction
    has to.
    """
    return {
        "type": "function",
        "function": {
            "name": "probe_tool",
            "description": "a tool written the way an author would write it",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    **properties_required_after,
                },
                "required": ["target"],
            },
        },
    }


class TestABadlyOrderedSchema:
    def test_it_builds_a_wrapper_rather_than_raising(self):
        """The defect, asserted from the outside in.

        The old behaviour was an exception out of `_build_tool_function`, which
        `build_server` does not catch - so the failure a user saw was a server
        that started with nothing on it at all.
        """
        schema = _schema(limit={"type": "integer"})
        # `target` is required and listed *before* the optional `limit`, so put
        # the required one after: the shape that used to raise.
        schema["function"]["parameters"]["properties"] = {
            "limit": {"type": "integer"}, "target": {"type": "string"}}

        fn = _build_tool_function(schema)

        assert fn is not None, "the schema was skipped, so the MCP server loses the tool"
        order = [p.name for p in fn.__signature__.parameters.values()]
        assert order == ["target", "limit"], (
            f"the required parameter must be built first, not {order!r}")

    def test_the_wrapper_is_still_callable_with_both_arguments(self):
        """A reordered signature must not change what a call means.

        `target` is required and `limit` is not; if the sort swapped their
        *meaning* rather than their position, a client's keyword arguments
        would land on the wrong parameter and every call would be wrong in a
        way no signature error reports.
        """
        schema = _schema(limit={"type": "integer"})
        schema["function"]["parameters"]["properties"] = {
            "limit": {"type": "integer"}, "target": {"type": "string"}}
        fn = _build_tool_function(schema)
        params = fn.__signature__.parameters
        assert params["target"].default is inspect.Parameter.empty, (
            "a required property must not acquire a default")
        assert params["limit"].default is None, "an optional property keeps its default"

    def test_a_schema_no_signature_exists_for_is_skipped_not_raised(self):
        """The docstring's promise, made true.

        `is_valid_schema` catches structural damage; a duplicate parameter name
        is the shape it cannot see (a properties dict cannot hold one, so the
        schema has to be sneaked past it), and `inspect.Signature` refuses it.
        The wrapper must come back None - one tool lost, not the server.
        """
        schema = _schema()

        class Duplicating(dict):
            """Holds one key, so it is truthy, and reports the key twice."""

            def items(self):  # noqa: D102 - the point is to build a bad signature
                return [("target", {"type": "string"}),
                        ("target", {"type": "string"})]

        # `properties` must survive `properties.get("properties", {}) or {}`,
        # which replaces an *empty* dict with a plain one - so it has to carry
        # the key it reports twice.
        schema["function"]["parameters"]["properties"] = Duplicating(
            target={"type": "string"})
        assert _build_tool_function(schema) is None


class TestNothingElseChanged:
    def test_every_shipped_tool_still_builds(self):
        """Whatever the shipped set is, all of it must still be exposed.

        A failure here means the sort moved something it should not have, or
        the skip guard started swallowing real tools - one of which would be
        a capability silently missing from every MCP client.
        """
        schemas, _ = tools.discover_skills()
        skipped = [schema["function"]["name"] for schema in schemas
                   if _build_tool_function(schema) is None]
        assert skipped == [], (
            f"{len(skipped)} of {len(schemas)} shipped skills were skipped: "
            f"{skipped[:8]}")

    def test_the_sort_reorders_nothing_that_was_already_valid(self):
        """Stable sort: the author's order survives inside each group.

        Asserted on every shipped skill rather than one, because this is the
        claim that makes the fix safe - a signature whose parameters changed
        position is a signature whose calls could land differently.
        """
        schemas, _ = tools.discover_skills()
        reordered = []
        for schema in schemas:
            function = schema["function"]
            listed = list(function.get("parameters", {}).get("properties", {}))
            fn = _build_tool_function(schema)
            if fn is None:
                continue
            built = [p.name for p in fn.__signature__.parameters.values()]
            if built != listed:
                reordered.append(function["name"])
        assert reordered == [], (
            f"the sort moved parameters in {reordered}")


class TestEveryShippedSchemaIsWellOrderedToday:
    def test_no_skill_needs_the_rescue_any_more(self):
        """The regression the workaround was for, asserted as a property.

        This is not the fix's justification - the fix covers the next author,
        including one dropping a module into `~/.config/shani-chronoa/skills/`
        - but it says whether the shipped tree still carries one, which is the
        thing that found the defect in `default_apps`.
        """
        schemas, _ = tools.discover_skills()
        badly_ordered = []
        for schema in schemas:
            function = schema["function"]
            params = function.get("parameters", {}) or {}
            required = set(params.get("required", []) or [])
            seen_optional = False
            for name in params.get("properties", {}) or {}:
                if name in required and seen_optional:
                    badly_ordered.append(function["name"])
                elif name not in required:
                    seen_optional = True
        assert badly_ordered == [], (
            f"a shipped skill lists {badly_ordered} with a required property "
            f"after an optional one")
