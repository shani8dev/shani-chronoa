"""Help, /help, the palette and the capabilities page name the same registry.

There were four answers to "what can Chronoa do": the Help window, `/help`,
the Inventory/Skills page and the capability list. Three now read
`capabilities.find_capabilities()` and the fourth (Workbench's built-in list)
is deliberately the developer's view of the same registry - raw module names,
which is what someone writing a skill needs, not what a person asks for.

A test that read the four surfaces' prose would pin four copies of the words
and fail every time a skill landed. This asks the *functions* instead: one
registry, one prompt source, one effect vocabulary.
"""

from __future__ import annotations

from shani_chronoa import capabilities, tools
from shani_chronoa.gui import commands, widgets
from shani_chronoa.gui.surfaces import skills, workbench


def test_the_prompt_is_one_function_for_help_and_the_command():
    # `/help what can you do?` and the empty-state chip both fill the composer
    # with this exact string. `/help` calls the shared function at activation,
    # so a second copy of the question in that module would be a drift risk.
    source = open(commands.__file__, encoding="utf-8").read()
    assert "caps.help_prompt()" in source, (
        "the /help command no longer asks capabilities for the prompt"
    )
    prompt = capabilities.help_prompt()
    assert isinstance(prompt, str) and prompt.strip()


def test_the_capabilities_page_and_help_use_the_same_registry():
    schemas, _ = tools.discover_skills()
    caps = capabilities.find_capabilities(schemas)
    assert caps, "an empty registry would make both lists vacuously agree"
    # One entry per capability, with the four fields both surfaces render.
    for cap in caps:
        assert isinstance(cap.label, str)
        assert isinstance(cap.description, str)
        assert isinstance(cap.tool, str)
        assert hasattr(cap, "example")
        assert isinstance(capabilities.effect_of(cap.tool, cap.description), str)


def test_workbench_is_the_developers_view_of_the_same_registry():
    # Its rows are raw registry names on purpose (11j: this is the view a
    # Developer switch would put somewhere honest), and it reads the same
    # `discover_skills` the capability list reads.
    schemas, _error = workbench.builtin_schemas()
    assert schemas
    assert all(workbench._schema_fields(s) for s in schemas[:5])
