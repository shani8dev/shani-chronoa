"""MCP tool annotations must not drift from the consent keys that enforce them.

Chronoa exposes 68 skills as a real MCP server, and a host like Claude Desktop or
Cursor can show a warning *before* a call if the server says what the tool does.
That warning is only worth anything if it agrees with the gate that actually
refuses the call, and the two are maintained in different files - which is
exactly how they would quietly diverge.

So this asserts the relationship rather than the values: a destructive tool must
be one Chronoa gates behind a destructive consent key, and a read-only tool must
be one that is genuinely observational. Both directions, because a warning that
appears on a harmless read is how people learn to dismiss the ones that matter.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import tools  # noqa: E402
from shani_chronoa.capabilities import (  # noqa: E402
    DESTRUCTIVE_CONSENT_KEYS,
    MUTATING_TOOLS,
    READ_ONLY_TOOLS,
    gated_by,
    tool_annotations,
    tool_title,
)


@pytest.fixture(scope="module")
def descriptions():
    schemas, _ = tools.discover_skills()
    return {s["function"]["name"]: s["function"].get("description", "")
            for s in schemas}


class TestAnnotationsFollowTheGates:
    def test_a_destructive_tool_is_always_gate_backed(self, descriptions):
        for name, desc in descriptions.items():
            ann = tool_annotations(name, desc)
            if ann.get("destructive_hint") is not True:
                continue
            key = gated_by(name, desc)
            assert key is not None, (
                f"{name} claims destructive_hint but has no consent key, so "
                f"nothing stops a client trusting the warning over the gate"
            )
            assert key in DESTRUCTIVE_CONSENT_KEYS, (
                f"{name} claims destructive_hint but its key {key!r} is not in "
                f"DESTRUCTIVE_CONSENT_KEYS - either the key is mislisted or the "
                f"hint is wrong"
            )

    def test_every_destructive_gate_earns_its_hint(self, descriptions):
        """A gate that cannot be undone must not be reachable without a warning."""
        seen = set()
        for name, desc in descriptions.items():
            key = gated_by(name, desc)
            if key in DESTRUCTIVE_CONSENT_KEYS:
                seen.add(key)
                assert tool_annotations(name, desc).get("destructive_hint") is True, (
                    f"{name} is gated behind {key!r} but does not declare itself "
                    f"destructive, so a client would not warn"
                )

    def test_read_only_is_only_claimed_for_observational_tools(self, descriptions):
        for name, desc in descriptions.items():
            ann = tool_annotations(name, desc)
            if ann.get("read_only_hint") is True:
                assert name in READ_ONLY_TOOLS, (
                    f"{name} claims read_only_hint but is not in the allowlist - "
                    f"an ungated tool is not automatically harmless"
                )
                assert gated_by(name, desc) is None, (
                    f"{name} claims read_only_hint but is gated behind "
                    f"{gated_by(name, desc)!r}, so it is not purely observational"
                )

    def test_a_gated_tool_is_never_claimed_read_only(self, descriptions):
        for name, desc in descriptions.items():
            if gated_by(name, desc) is not None:
                assert tool_annotations(name, desc).get("read_only_hint") is not True, (
                    f"{name} needs consent to run, so it cannot also be read-only"
                )


class TestUnknownStaysUnknown:
    def test_ungated_non_observational_tools_assert_nothing(self, descriptions):
        """The honest default is no hint, not a guessed false.

        A tool that changes something and is in neither allowlist must return
        all-None, so a client treats it as unknown rather than as a positive
        "this is safe" claim.
        """
        unknown = [n for n in descriptions
                   if gated_by(n, descriptions[n]) is None
                   and n not in READ_ONLY_TOOLS
                   and n not in MUTATING_TOOLS]
        assert unknown, "expected at least one ungated, non-observational tool"
        for name in unknown:
            ann = tool_annotations(name, descriptions[name])
            assert ann.get("read_only_hint") is None, (
                f"{name} has no basis for a read_only claim and must not make one"
            )
            assert ann.get("destructive_hint") is None, (
                f"{name} has no basis for a destructive claim and must not make one"
            )

    def test_a_known_actuator_may_say_it_is_not_read_only(self, descriptions):
        """`read_only_hint: False` is the opposite of a safety claim.

        For a tool in `MUTATING_TOOLS` it is simply true - `write_text_file`
        writes - and without it the MCP library drops the annotations object
        entirely, leaving a client with no idea the tool acts. That is how
        sixteen actuators ended up unannotated over the wire.
        """
        mutating = [n for n in descriptions if n in MUTATING_TOOLS]
        assert mutating, "expected at least one ungated actuator"
        for name in mutating:
            ann = tool_annotations(name, descriptions[name])
            assert ann.get("read_only_hint") is False, (
                f"{name} is a known actuator and should say it is not read-only"
            )
            # The genuine safety claim stays unclaimed, whatever else is known.
            assert ann.get("destructive_hint") is None, (
                f"{name} must not claim it is non-destructive - whether an "
                f"action is hard to undo is a separate question"
            )
            assert ann.get("idempotent_hint") is None, (
                f"{name} must not claim repeating it is safe"
            )

    def test_no_ungated_writer_claims_it_is_non_destructive(self, descriptions):
        """The real safety invariant, across every ungated tool that acts.

        Excluding `READ_ONLY_TOOLS` on purpose: a read-only tool *is* non-
        destructive, and that is a claim the registry can justify, so banning it
        there would be banning the truth rather than a guess. What must never
        happen is a tool that changes something asserting that it does not.
        """
        writers = [n for n, d in descriptions.items()
                   if gated_by(n, d) is None and n not in READ_ONLY_TOOLS]
        assert writers, "expected at least one ungated writer"
        for name in writers:
            ann = tool_annotations(name, descriptions[name])
            assert ann.get("destructive_hint") is None, (
                f"{name} is ungated and acts, so it must not claim it is "
                f"non-destructive - that is a positive safety claim"
            )

    def test_annotate_only_known_hints(self, descriptions):
        """The dict may only contain keys ToolAnnotations accepts."""
        allowed = {"read_only_hint", "destructive_hint", "idempotent_hint",
                   "open_world_hint"}
        for name, desc in descriptions.items():
            extra = set(tool_annotations(name, desc)) - allowed
            assert not extra, f"{name} produced unknown hint keys: {extra}"


class TestTitles:
    def test_every_tool_has_a_non_empty_title(self, descriptions):
        for name in descriptions:
            title = tool_title(name)
            assert isinstance(title, str) and title.strip(), (
                f"{name} has no human-facing title"
            )
