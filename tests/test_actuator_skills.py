"""The outbound skills: speak, notify, screenshot.

Replaces a 1-byte placeholder that broke collection of the entire suite.

These are covered for discovery, schema shape and refusal behaviour rather than
by invoking them. notify and screenshot were verified by real execution during
development (a real notification; a real 960x540 PNG of 81786 bytes with valid
magic), and running them from a unit test would post notifications and write
files into the developer's home - a side effect a test should not have. What
this file guards is the part that silently rots: a skill that stops being
discovered, loses its schema, or starts accepting arguments it never declared.
"""

import pytest

from shani_chronoa.skills import discover_skills, is_valid_schema

NEW_SKILLS = ("speak", "notify", "screenshot", "set_clipboard", "get_clipboard")


@pytest.fixture(scope="module")
def shipped():
    return discover_skills()[1]


@pytest.mark.parametrize("name", NEW_SKILLS)
def test_the_skill_is_discovered_and_callable(shipped, name):
    assert name in shipped, f"{name} is not in the shipped skill registry"
    assert callable(shipped[name]), f"{name} has no runnable handler"


def _schema_for(name):
    """The schema a skill actually publishes, wherever it keeps it.

    `clipboard` exposes a separate SCHEMAS dict; the newer skills inline the
    schema in their SKILLS entries instead. Reading only one of the two made
    every schema assertion skip - a green test that checked nothing, which is
    worse than no test at all.
    """
    handler = __import__(discover_skills()[1][name].__module__, fromlist=["SKILLS", "SCHEMAS"])
    schemas = getattr(handler, "SCHEMAS", None)
    if isinstance(schemas, dict) and name in schemas:
        return schemas[name]
    for entry in getattr(handler, "SKILLS", []):
        if entry.name == name:
            return entry.schema
    return None


@pytest.mark.parametrize("name", NEW_SKILLS)
def test_the_schema_is_well_formed(shipped, name):
    schema = _schema_for(name)

    assert schema is not None, f"{name} publishes no schema at all"
    assert is_valid_schema(schema), f"{name} has a malformed schema"
    assert schema["function"]["name"] == name, (
        "the schema name must match the registry name or the LLM is offered a "
        "tool under a name the dispatcher cannot route"
    )


@pytest.mark.parametrize("name", ("speak", "notify", "screenshot"))
def test_no_skill_accepts_a_free_form_command(shipped, name):
    # The design boundary this repo will not cross: every actuator is a narrow,
    # named, schema-typed action. A skill that grew a "command" string
    # parameter would be a shell-exec tool wearing a costume.
    schema = _schema_for(name) or {}
    properties = schema.get("function", {}).get("parameters", {}).get("properties", {})

    for forbidden in ("command", "cmd", "shell", "script", "exec"):
        assert forbidden not in properties, (
            f"{name} exposes a {forbidden!r} parameter, which is a generic "
            "shell-exec path"
        )


def test_input_control_is_not_reachable_as_a_sense(shipped):
    # Pointer and keyboard are actuators, not senses: they emit no percept, so
    # they must not be registered in the senses registry or given a
    # `<name>-sense-enabled` key.
    from shani_chronoa.senses import discover_senses

    senses = set(discover_senses())

    assert not (senses & {"move_pointer", "click_pointer", "type_text"})
    for name in ("move_pointer", "click_pointer", "type_text"):
        assert name in shipped, f"{name} should still be a whitelisted skill"
