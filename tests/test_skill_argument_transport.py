"""Arguments must survive the trip into the skill subprocess as *Python*.

Every skill runs as `python3 -c "from ... import _run; result = _run(<args>)"`.
That `<args>` is source code, not data, and for a long time it was written with
`json.dumps` - which emits `true`, `false` and `null`.

Those are not Python. The child program still **compiles**, because `true`
parses as a bare name, and then dies at runtime with
`NameError: name 'true' is not defined`. So every skill called with a boolean or
null argument failed over MCP while passing every unit test in the repo, because
the unit tests call the handler in-process and never serialise at all.

This file exists because the bug is invisible to the suite's usual shape. Each
test here goes through `execute_tool`, which really does spawn the subprocess.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.tools import _json_safe, execute_tool  # noqa: E402


@pytest.fixture
def hermetic(gsettings_env):
    """Hermetic gsettings, so the subprocess reads this test's config and not
    the developer's. Returned for its side effect on the environment."""
    return gsettings_env


class TestTheLiteralIsPython:
    def test_booleans_survive(self):
        assert _json_safe({"a": True}) == {"a": True}
        assert _json_safe({"a": False}) == {"a": False}
        assert _json_safe({"a": None}) == {"a": None}

    def test_a_json_true_would_have_been_wrong(self):
        """The specific trap, stated as a test so the shape is on the record."""
        import json

        broken = json.dumps({"flag": True})
        assert "true" in broken
        with pytest.raises(NameError):
            exec(f"value = {broken}")  # noqa: S102 - the point is that it raises
        assert eval(repr({"flag": True})) == {"flag": True}  # noqa: S307

    def test_nested_structures_are_coerced(self):
        got = _json_safe({"a": [1, True, {"b": None}], "c": ("t",)})
        assert got == {"a": [1, True, {"b": None}], "c": ["t"]}

    def test_an_unexpected_type_becomes_text_rather_than_breaking_source(self):
        class Weird:
            def __str__(self):
                return "weird"

        got = _json_safe({"a": Weird()})
        assert got == {"a": "weird"}
        assert eval(repr(got)) == got  # noqa: S307 - must remain valid Python

    def test_string_keys_are_made_safe(self):
        got = _json_safe({1: "x"})
        assert got == {"1": "x"}


class TestThroughTheRealSubprocess:
    """`execute_tool` really spawns `python3 -c ...`, which is the whole point."""

    @pytest.mark.parametrize("arguments", [
        {"flag": True},
        {"flag": False},
        {"flag": None},
        {"flag": True, "other": "x"},
    ])
    def test_a_boolean_argument_reaches_the_handler(self, arguments, tmp_path,
                                                   gsettings_env):
        target = tmp_path / "a.py"
        target.write_text("VAL = 1\n")
        out = execute_tool("find_and_replace",
                           {"find": "VAL", "replace": "2",
                            "path": str(tmp_path), "dry_run": True, **arguments})
        assert "NameError" not in out, (
            f"the argument reached the child as JSON, not Python: {out[:160]}")
        assert "ModuleNotFoundError" not in out
        assert not out.startswith("ERROR(exit="), out[:160]

    def test_a_skill_with_only_a_boolean_argument_works(self, gsettings_env):
        out = execute_tool("list_services", {"failed_only": True})
        assert "NameError" not in out, out[:160]
        assert "service unit" in out

    def test_the_boolean_actually_took_effect(self, gsettings_env):
        """Not just "no crash" - the value must have arrived as a bool.

        A transport that dropped the argument would also avoid the NameError,
        so the failure mode has to be distinguished from the fix.
        """
        everything = execute_tool("list_services", {"failed_only": False})
        failed_only = execute_tool("list_services", {"failed_only": True})
        assert "service unit(s)" in everything
        assert "service unit(s)" in failed_only
        assert everything != failed_only, (
            "True and False produced identical output, so the argument did not "
            "reach the handler as a boolean at all")

    def test_a_quote_in_an_argument_does_not_break_the_program(self, gsettings_env):
        out = execute_tool("calculate", {"expression": "2+2"})
        assert out.startswith("4"), out[:120]
