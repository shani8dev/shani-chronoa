"""A skill schema is capped, and the refusal names the path.

`harness-study/laya-decision`'s `laya/structured.py` states the principle:

> an object of properties, each an enum choice, a boolean, or a bounded integer
> scale. **Anything that cannot be answered from a fixed option set (free strings,
> arrays, nested objects) is rejected with an error that names the path.**

Chronoa had **no caps at all**: `is_valid_schema` checked shape and nothing about
size, so a schema with 200 parameters would register and be sent to a model that
cannot hold it.

**The principle transferred; the numbers did not, and measuring said so.**
`solve_math` enumerates its hundred arithmetic operations as one enum, so porting
`MAX_OPTIONS = 32` verbatim made the loader skip a **shipped skill** - 257 -> 256 on
the first run. The first measurement was circular too: it took the maxima over the
*registered* registry, which the cap had already filtered, and reported 24 options
because the 100-option skill was gone by then.

The caps are measured over every built-in skill with the cap bypassed:

| what | measured max | cap |
|---|---|---|
| properties | 22 (`browse`) | 32 |
| enum options | **100** (`solve_math`) | **128** |
| nesting depth | 2 | 4 |

So `test_nothing_that_ships_is_refused` runs the real registry through the caps,
which is the control that would have caught the first version.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "usr" / "lib"))

from shani_chronoa.skills import (is_valid_schema, schema_refusal,  # noqa: E402
                                  _SCHEMA_MAX_OPTIONS, _SCHEMA_MAX_PROPERTIES)


def _schema(properties, name="probe"):
    return {"type": "function",
            "function": {"name": name,
                         "parameters": {"type": "object", "properties": properties}}}


class TestTheCapsAreMeasuredNotCopied:
    """The control that would have caught `MAX_OPTIONS = 32`."""

    def test_nothing_that_ships_is_refused(self):
        """Every built-in skill through the caps. A cap that drops a shipped skill
        is a cap that was not measured."""
        from shani_chronoa.tools import discover_skills

        tools, _ = discover_skills()
        assert len(tools) == 257, (
            f"expected the whole registry, got {len(tools)} - a cap dropped a "
            f"skill that ships")
        for tool in tools:
            function = tool.get("function", tool)
            parameters = function.get("parameters") or {}
            said = schema_refusal(parameters)
            assert not said, f"{function.get('name')} was refused: {said}"

    def test_the_caps_have_headroom_over_the_measured_maximum(self):
        """And the headroom is asserted rather than remembered, because a cap that
        exactly equals the maximum has none."""
        from shani_chronoa.tools import discover_skills

        tools, _ = discover_skills()
        widest = max(len(t.get("function", t).get("parameters", {})
                        .get("properties") or {}) for t in tools)
        assert _SCHEMA_MAX_PROPERTIES > widest, (
            f"the widest shipped schema ({widest}) is at the cap, so the next "
            f"property on it is refused")

        # And on the enum side, which is the cap that broke a shipped skill the
        # first time. **A cap exactly equal to the measured maximum has no
        # headroom**: `solve_math` enumerates exactly 100 operations, so
        # `> 100` lets it through and `> 99` does not.
        def _options(schema):
            for prop in (schema or {}).get("properties", {}).values():
                found = prop.get("enum")
                if found:
                    yield len(found)
                yield from _options(prop)

        listed = [n for t in tools
                  for n in _options(t.get("function", t).get("parameters") or {})]
        assert listed, "no shipped schema has an enum, so the cap is untested"
        assert _SCHEMA_MAX_OPTIONS > max(listed), (
            f"the widest shipped enum ({max(listed)}) is at the cap, so the "
            f"next operation on it is refused - which is how `solve_math` was "
            f"lost to laya's 32 in the first version")


class TestTheRefusalNamesThePath:
    def test_too_many_properties_says_the_count_and_the_path(self):
        big = {f"p{i}": {"type": "string"} for i in range(_SCHEMA_MAX_PROPERTIES + 1)}
        said = schema_refusal(_schema(big)["function"]["parameters"])
        assert "properties" in said
        assert str(_SCHEMA_MAX_PROPERTIES) in said or "past" in said, said

    def test_too_many_options_says_the_property_it_is_on(self):
        """Not just 'an enum is too big' - *which* enum, or a person adding a
        schema cannot find it."""
        options = [f"o{i}" for i in range(_SCHEMA_MAX_OPTIONS + 1)]
        said = schema_refusal(_schema({"operation": {"enum": options}})
                              ["function"]["parameters"])
        assert "operation" in said, said
        assert "options" in said, said

    def test_too_deep_says_where_it_was_found(self):
        deep = {"type": "object", "properties": {"a": {"type": "object", "properties": {
            "b": {"type": "object", "properties": {"c": {"type": "object",
            "properties": {"d": {"type": "object", "properties": {
                "e": {"type": "object", "properties": {"f": {"type": "string"}}}}}}}}}}}}}
        said = schema_refusal(deep)
        assert "nest" in said or "depth" in said, said
        # **The property where it ran out, not the one past it.** The message
        # names `e`, the deepest property whose nesting was still counted; the
        # first version of this asserted `f`, which is the property past the
        # limit and never appears. Both the refusal and the test are right now.
        assert "properties.e" in said, said

    def test_an_empty_and_a_malformed_enum_are_refused(self):
        assert schema_refusal(_schema({"a": {"enum": []}})["function"]["parameters"])
        assert schema_refusal(_schema({"a": {"enum": "abc"}})["function"]["parameters"])

    def test_a_property_that_is_not_an_object_is_refused(self):
        said = schema_refusal(_schema({"a": "string"})["function"]["parameters"])
        assert "a" in said, said

    def test_a_parameters_block_that_is_not_an_object_is_refused(self):
        """**The guard this test file had no witness for.**

        `schema_refusal` checks `parameters.properties` itself, not only each
        property inside it, and the mutation that removed that top-level guard
        found no failing test - the per-property test above covers a different
        line. `is_valid_schema` already refuses this shape structurally, so
        without the reason it would be skipped with a warning that named nothing.
        """
        # **`None` is deliberately absent from this list.** `None` is what an
        # absent key reads as, and the guard's `is not None` half exists to allow
        # it - the first version of this test included `None` and failed on
        # correct code, which is the "an absence presented as a defect" trap in
        # its usual place: a schema with no parameters at all is legal.
        # `{}` is absent from this list for the same reason `None` was: an empty
        # `properties` block is a valid schema - a skill that takes no arguments.
        # The first version carried it and failed on correct code, which is the
        # second time in this one file that a list of bad values accidentally
        # held a good one.
        for bad in ("string", 42, ["a", "b"], True):
            assert schema_refusal({"type": "object", "properties": {}}) == ""
            schema = {"type": "object", "properties": bad}
            said = schema_refusal(schema)
            assert said, f"properties={bad!r} was accepted with no reason"
            assert "properties" in said, said

    def test_an_absent_parameters_block_is_fine(self):
        """The other half of the same guard, and the reason it is written
        `is not None and not isinstance(...)`. Many skills take no arguments."""
        assert schema_refusal({}) == ""
        assert schema_refusal({"type": "object"}) == ""

    def test_a_valid_schema_gives_no_reason(self):
        good = _schema({"path": {"type": "string"}, "what": {"enum": ["a", "b"]}})
        assert schema_refusal(good["function"]["parameters"]) == ""


class TestIsValidSchemaCarriesTheCaps:
    def test_an_oversized_schema_is_refused(self):
        big = {f"p{i}": {"type": "string"} for i in range(_SCHEMA_MAX_PROPERTIES + 1)}
        assert is_valid_schema(_schema(big)) is False

    def test_a_valid_schema_is_unaffected(self):
        assert is_valid_schema(_schema({"a": {"type": "string"}})) is True
        # A free string is still a valid property: plenty of skills take one.
        assert is_valid_schema(_schema({"path": {}, "text": {},
                                        "n": {"type": "integer"}})) is True

    def test_the_loader_names_the_reason(self, caplog):
        """The loader already skipped a malformed schema with a warning that could
        not say *which* part was wrong; it says so now."""
        import logging

        from shani_chronoa.skills import Skill

        big = {f"p{i}": {"type": "string"} for i in range(_SCHEMA_MAX_PROPERTIES + 1)}
        skill = Skill(name="huge",
                      schema=_schema(big, name="huge"),
                      run=lambda arguments: "ok")
        module = type(sys)("fake_skills_module")
        module.SKILLS = [skill]
        sys.modules["fake_skills_module"] = module
        # The loader's real signature is `_register(module, source, tools,
        # handlers)`. The first version invented `_register_from(..., register_into=)`,
        # which does not exist - an ImportError that looked like a product defect
        # and was a test naming a function that had never been read.
        from shani_chronoa.skills import _register

        tools, handlers = [], {}
        try:
            with caplog.at_level(logging.WARNING):
                _register(sys.modules["fake_skills_module"], "builtin:huge",
                          tools, handlers)
            assert tools == [] and handlers == {}, "an oversized schema was registered"
            assert any("properties" in r.getMessage() or "past" in r.getMessage()
                       for r in caplog.records), (
                "a schema past the caps was skipped without naming what was wrong: "
                f"{[r.getMessage() for r in caplog.records]}")
        finally:
            del sys.modules["fake_skills_module"]
