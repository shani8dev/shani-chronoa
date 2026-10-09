"""The Siri/Google parity skills gate on switches a person can actually reach.

`calendar_edit` shipped refusing **every** call with

    Refusing: changing your calendar is turned off
    (enable 'calendar-write-enabled' in Settings). Nothing was changed.

and the switch it named was not in the schema at all. So it was not a gate, it
was a permanent refusal wearing the shape of a permission: the skill looked
configurable, the model advertised it, and no user action could ever open it.
This is the same defect class as the `heard-sound` sense whose only switch was
not on screen (AGENTS.md, "Audit-verified known issues") - a capability that is
unreachable rather than absent, which is the worse of the two because it looks
present.

Measured, not inferred. Before: the key was absent from the schema, so
`get_bool` returned the Python default `False` for every value and the refusal
was unconditional. After: defaulting to `false` still refuses, and a granted key
lets the call through to the next check - which is where the honest
environmental refusal ("no Evolution Data Server bindings") appears, a different
and truthful answer.

Run: `python3 -m pytest tests/test_parity_skill_gates_are_reachable.py`

Two properties are asserted, and the second is the one that matters:

1. every gate these skills name is a real schema key, so `get_bool` is reading a
   setting rather than falling back to a default nobody can change; and
2. the gate is *reachable* - granting the key actually changes the skill's
   answer. A key that exists but that nothing reads passes the first test and
   leaves the skill dead, which is the failure this file is named for.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")

SCHEMA = pathlib.Path("usr/share/glib-2.0/schemas/org.shani.chronoa.gschema.xml")

# tool -> the consent key its own description and source name.
PARITY_GATES = {
    "news": "web-sense-enabled",
    "maps": "web-sense-enabled",
    "take_photo": "vision-sense-enabled",
    "toggle_wifi": "radio-control-enabled",
    "calendar_edit": "calendar-write-enabled",
}


def _schema_keys() -> set[str]:
    import re
    return set(re.findall(r'<key\s+name="([^"]+)"', SCHEMA.read_text()))


@pytest.mark.parametrize("tool,key", sorted(PARITY_GATES.items()))
def test_the_gate_named_by_the_skill_is_a_real_schema_key(tool: str, key: str) -> None:
    """A gate that is not in the schema cannot be turned on by anybody."""
    from shani_chronoa import capabilities

    assert capabilities.GATED.get(tool) == key, (
        f"{tool} is declared to gate on {key!r} here, but capabilities.GATED says "
        f"{capabilities.GATED.get(tool)!r}"
    )
    assert key in _schema_keys(), (
        f"{tool} gates on {key!r}, which is not in the schema. get_bool() then "
        "returns the Python default False forever and the skill refuses every "
        "call while telling the user to enable a switch that does not exist."
    )


def test_the_schema_compiles_with_the_new_key() -> None:
    """glib-compile-schemas silently discards the whole file on a bad key.

    AGENTS.md records that a schema error makes *every* setting fall back to a
    Python default with exit code 0 and no visible error, so a key that parses
    is not evidence the file does. Compiling is the check, and the compiled file
    has to exist afterwards.
    """
    import shutil
    import subprocess
    import tempfile

    if not shutil.which("glib-compile-schemas"):
        pytest.skip("glib-compile-schemas is not installed")

    with tempfile.TemporaryDirectory() as tmp:
        for xml in SCHEMA.parent.glob("*.xml"):
            shutil.copy(xml, tmp)
        result = subprocess.run(
            ["glib-compile-schemas", tmp],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert (pathlib.Path(tmp) / "gschemas.compiled").exists()
        # And the key survives into the compiled database, not just the XML.
        listing = subprocess.run(
            ["gsettings", "list-keys", "org.shani.chronoa"],
            capture_output=True, text=True, timeout=60,
            env={"PATH": "/usr/bin:/bin", "GSETTINGS_SCHEMA_DIR": tmp,
                 "GSETTINGS_BACKEND": "memory"},
        )
        assert "calendar-write-enabled" in listing.stdout, (
            "the key is in the XML but not in the compiled schema"
        )


def _config():
    """A config on a private keyfile backend.

    `GSETTINGS_BACKEND=memory` is per-process, and a skill runs in a sandboxed
    *child*, so a grant made in-process would be invisible to it and read as
    "turned off" - the same trap AGENTS.md records for the trigger probe. A
    keyfile under a temp XDG_CONFIG_HOME is inherited by the child, which is
    what makes the round trip below mean anything.
    """
    from shani_chronoa.config import ChronoaConfig
    return ChronoaConfig()


def test_calendar_edit_refuses_until_the_write_key_is_granted(tmp_path) -> None:
    """The whole point: the refusal must be reversible by a user.

    Runs the registered handler through `execute_tool_outcome` rather than
    calling `_run` directly - AGENTS.md is explicit that a test which calls the
    function is not a test that the skill is reachable, and the three skills
    that were dead for exactly this reason all passed their direct-call tests.
    """
    from shani_chronoa import tools

    monkey = pytest.MonkeyPatch()
    monkey.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkey.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkey.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    (tmp_path / "config").mkdir()
    try:
        args = {"action": "create", "title": "Meeting", "start": "tomorrow 3pm"}

        refused = tools.execute_tool_outcome("calendar_edit", args).text
        assert "Refusing" in refused, (
            "with the default (off) the skill must refuse, or this test is "
            f"asserting nothing: {refused!r}"
        )
        assert "calendar-write-enabled" in refused, (
            "the refusal should name the switch that would open it, so a person "
            f"can act on it: {refused!r}"
        )

        config = _config()
        config.set("calendar-write-enabled", "true")
        assert config.get_bool("calendar-write-enabled", False) is True, (
            "the grant did not take; everything after this would be vacuous"
        )

        after = tools.execute_tool_outcome("calendar_edit", args).text
        assert "Refusing: changing your calendar is turned off" not in after, (
            "the gate is still closed after it was granted, so the key exists "
            f"but nothing reads it - the skill is dead either way: {after!r}"
        )
    finally:
        monkey.undo()


def test_toggle_wifi_says_which_switch_turns_the_radio_on() -> None:
    """`toggle_wifi`'s refusal must name a key that exists.

    Cheaper than the calendar round trip and the same property: a refusal that
    names a switch nobody has is how `calendar_edit` spent its life.
    """
    from shani_chronoa.skills import toggle_wifi

    assert toggle_wifi._CONSENT_KEY in _schema_keys()
    # The status half needs no permission, so asking for it must not be refused
    # on the radio key - a skill that gates its own report is a skill that
    # cannot explain why it is silent.
    result = toggle_wifi._run({"action": "status"})
    assert "radio-control-enabled" not in result, (
        f"status should not be refused for a missing radio grant: {result!r}"
    )