"""The suggestion and help surfaces, and the property they exist to guarantee.

There is one test here that matters more than the rest, and it is not about
layout: **a skill that is not loaded must not be suggested.** A hardcoded list
of example sentences is a snapshot of the app as it was on the day someone typed
it, and it keeps advertising a skill a user's build failed to load, or one that
has since been renamed or removed, until someone types a suggestion and gets
silence back. `test_a_removed_skill_stops_being_suggested` pins that shut.

Everything else here is the smaller half: that the registry is read live, that
a gated skill says so *and* says which switch, and that an unexpected
third-party skill is still discoverable rather than dropped.
"""

import pytest

from shani_chronoa import capabilities
from shani_chronoa.capabilities import (
    GATE_NAMES,
    Capability,
    find_capabilities,
    gated_by,
    startup_suggestions,
)


def _tool(name, description=""):
    """One entry in the shape `discover_skills()` returns."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}},
        },
    }


# The real registry, so the grouping table is tested against reality rather
# than against the fixtures below.
REAL_TOOLS = [
    _tool("get_battery_status", "Get the current battery charge percentage and charging state."),
    _tool("set_brightness", "Get or set screen brightness as a percentage. With no level, reports the current value."),
    _tool("get_volume", "Get the current system output volume and mute state."),
    _tool("set_volume", "Set the system output volume to an exact percentage."),
    _tool("set_mute", "Mute or unmute the system output."),
    _tool("speak", "Speak text aloud using the local text-to-speech engine."),
    _tool("get_datetime", "Get the current local date and time."),
    _tool("set_timer", "Set a countdown timer that sends a desktop notification when it finishes."),
    _tool("get_clipboard", "Read the current contents of the system clipboard."),
    _tool("set_clipboard", "Write text to the system clipboard."),
    _tool("screenshot", "Capture the screen to a PNG file. Refuses without vision consent."),
    _tool("open_application", "Open or launch an installed application by name, e.g. 'firefox'."),
    _tool("web_search", "Look something up on the web and return what the page says."),
    _tool("move_pointer", "Move the pointer to absolute screen coordinates. Requires the 'input-control-enabled' consent key."),
    _tool("click_pointer", "Click a mouse button. Requires the 'input-control-enabled' consent key."),
    _tool("type_text", "Type text at the focused window. Requires the 'input-control-enabled' consent key."),
    _tool("recommend_model", "Recommend local language models that fit this machine's available memory."),
    _tool("install_model", "Download a named model into the local Ollama."),
    _tool("notify", "Send a desktop notification. Honours the 'notification-enabled' setting."),
]


class TestDerivedFromTheLiveRegistry:
    def test_it_reads_the_registry_rather_than_a_hardcoded_list(self):
        found = find_capabilities(REAL_TOOLS)
        assert [c.tool for c in found] == [t["function"]["name"] for t in REAL_TOOLS]

    def test_every_capability_has_a_human_label_not_a_tool_name(self):
        for capability in find_capabilities(REAL_TOOLS):
            assert "_" not in capability.label, (
                f"{capability.tool} is labelled {capability.label!r} - a tool "
                f"name, not something a person would scan"
            )
            assert capability.label != capability.tool, (
                f"{capability.tool} has no label of its own and falls back to "
                f"its own name"
            )

    def test_a_removed_skill_stops_being_suggested(self):
        """The property the module exists for."""
        before = startup_suggestions(find_capabilities(REAL_TOOLS))
        assert "Take a screenshot" in before

        # Screenshot support missing from this build - a broken skill, a
        # dependency not installed, a user who removed the module.
        without = [t for t in REAL_TOOLS if t["function"]["name"] != "screenshot"]
        after = startup_suggestions(find_capabilities(without))

        assert "Take a screenshot" not in after
        assert "Take a screenshot" in before, "the control case: it was offered before"

    def test_an_empty_registry_offers_nothing_rather_than_padding(self):
        """Padding the list with defaults is the failure being guarded against,
        so an empty registry must produce an empty list."""
        assert startup_suggestions(find_capabilities([])) == []

    def test_an_unexpected_skill_is_still_discoverable(self):
        """A user can drop a module into ~/.config/shani-chronoa/skills/, so the
        table is never closed. An unknown skill must land somewhere visible."""
        custom = [_tool("brew_coffee", "Start the coffee machine.")]
        found = find_capabilities(custom)
        assert len(found) == 1
        assert found[0].group == capabilities.OTHER
        assert found[0].label == "brew coffee"
        assert found[0].description == "Start the coffee machine."


class TestMalformedRegistry:
    @pytest.mark.parametrize("junk", [None, "a string", 42, {"no": "function"}, {}])
    def test_one_bad_entry_does_not_take_the_window_down(self, junk):
        """A broken third-party skill must cost that skill, not the help screen."""
        assert find_capabilities([junk]) == []
        found = find_capabilities([junk, _tool("get_datetime", "Get the time.")])
        assert [c.tool for c in found] == ["get_datetime"]

    def test_an_entry_with_no_name_is_skipped(self):
        assert find_capabilities([{"function": {"description": "no name"}}]) == []


class TestGating:
    def test_a_consent_key_named_in_the_description_is_found(self):
        assert gated_by("whatever", "Requires the 'my-own-sense-enabled' consent key.") == (
            "my-own-sense-enabled"
        )

    def test_a_known_gate_is_known_even_without_the_wording(self):
        assert gated_by("notify", "Send a notification.") == "notification-enabled"

    def test_an_ungated_skill_has_no_gate(self):
        assert gated_by("get_datetime", "Get the current date and time.") is None

    def test_gated_capabilities_explain_the_switch_by_name(self):
        by_tool = {c.tool: c for c in find_capabilities(REAL_TOOLS)}
        pointer = by_tool["move_pointer"]
        # "input-control-enabled" means nothing to someone who has never opened
        # a terminal; the row has to name the thing they can find in Settings.
        assert "Let Chronoa act" in pointer.gate_help(allowed=False)

    def test_the_gate_text_says_which_state_it_is_in(self):
        by_tool = {c.tool: c for c in find_capabilities(REAL_TOOLS)}
        notify = by_tool["notify"]
        opened = notify.gate_help(allowed=True)
        closed = notify.gate_help(allowed=False)
        assert opened.startswith("On."), opened
        assert closed.startswith("Off."), closed
        # Both states name the switch, and must not stutter ("allow Allow
        # notifications"), which is how the first draft read.
        # The label comes from GATE_NAMES rather than being spelled out here:
        # what this pins is that BOTH states name the switch, not which words
        # the switch happens to be called this week.
        label = GATE_NAMES[notify.consent_key]
        assert label in opened
        assert label in closed
        assert "Settings" in closed

    def test_an_ungated_capability_has_no_gate_prose(self):
        by_tool = {c.tool: c for c in find_capabilities(REAL_TOOLS)}
        assert by_tool["get_datetime"].gate_help(allowed=False) == ""

    def test_a_gate_that_is_off_is_reported_as_off(self):
        """Gates fail *silently* - the skill refuses and the user sees nothing.
        Naming the switch is the only thing standing between that and a help
        screen that reads as broken."""
        class Config:
            def sense_allowed(self, key):
                return key == "notification-enabled"

        by_tool = {c.tool: c for c in find_capabilities(REAL_TOOLS)}
        assert by_tool["notify"].gate_is_open(Config()) is True
        assert by_tool["move_pointer"].gate_is_open(Config()) is False

    def test_a_gate_that_cannot_be_checked_reads_as_closed(self):
        """A consent key the app cannot resolve, or whose check raises, has not
        been shown to be on - so it must read as closed rather than propagate
        the error into the help window or round up to available."""

        class Broken:
            def sense_allowed(self, key):
                raise RuntimeError("no such schema key")

        gated = Capability(
            tool="x", group="g", label="L", description="d",
            consent_key="not-a-real-sense-enabled",
        )
        assert gated.gate_is_open(Broken()) is False


class TestSuggestions:
    def test_suggestions_need_no_free_argument(self):
        """A suggestion that needs the user to supply "which app" is not a
        suggestion, it is another question."""
        for capability in find_capabilities(REAL_TOOLS):
            if capability.tool in {"open_application", "install_model"}:
                assert capability.example == "", f"{capability.tool} takes a free argument"

    def test_the_order_is_deliberate_and_stable(self):
        found = find_capabilities(REAL_TOOLS)
        suggestions = startup_suggestions(found)
        assert suggestions == [
            "How much battery is left?",
            "What time is it?",
            "Set a timer for 10 minutes",
            "Which model would fit this machine?",
            "Search the web for the Arch wiki",
            "Take a screenshot",
        ]

    def test_a_short_registry_still_offers_something_useful(self):
        found = find_capabilities([_tool("get_datetime", "Get the current date and time.")])
        assert startup_suggestions(found) == ["What time is it?"]

    def test_every_suggestion_maps_to_a_capability_that_exists(self):
        found = find_capabilities(REAL_TOOLS)
        examples = {c.example for c in found}
        for suggestion in startup_suggestions(found):
            assert suggestion in examples


class TestDescriptions:
    def test_a_wordy_model_facing_description_becomes_one_sentence(self):
        capability = find_capabilities([
            _tool("set_brightness",
                  "Get or set screen brightness as a percentage. With no level, "
                  "reports the current value and exits without changing anything.")
        ])[0]
        assert capability.description == "Get or set screen brightness as a percentage."

    def test_a_long_sentence_is_truncated_rather_than_left_to_overflow(self):
        capability = find_capabilities([
            _tool("x", "z" * 400 + ". Second sentence.")
        ])[0]
        assert len(capability.description) <= 118
        assert capability.description.endswith("…")

    def test_a_missing_description_is_empty_not_a_crash(self):
        assert find_capabilities([_tool("x", "")])[0].description == ""


class TestAgainstTheRealRegistry:
    """The grouping table against the app's actual 19 built-in skills, so a
    rename cannot quietly drop a skill out of the help screen."""

    def test_the_builtin_registry_builds_cleanly(self):
        from shani_chronoa.skills import discover_skills
        tools, handlers = discover_skills()
        found = find_capabilities(tools)
        assert len(found) == len(handlers) > 10

    def test_no_builtin_skill_lands_in_the_other_group(self):
        from shani_chronoa.skills import discover_skills
        tools, _ = discover_skills()
        strays = [c.tool for c in find_capabilities(tools) if c.group == capabilities.OTHER]
        assert strays == [], f"ungrouped built-ins: {strays}"

    def test_the_pointer_controls_are_grouped_together(self):
        from shani_chronoa.skills import discover_skills
        tools, _ = discover_skills()
        groups = {c.tool: c.group for c in find_capabilities(tools)}
        assert groups["move_pointer"] == groups["click_pointer"] == groups["type_text"]

    def test_every_group_is_in_the_display_order(self):
        from shani_chronoa.skills import discover_skills
        tools, _ = discover_skills()
        for capability in find_capabilities(tools):
            assert capability.group in capabilities.GROUP_ORDER + (capabilities.OTHER,)


def test_describe_names_broken_tools_and_their_workarounds():
    """A broken tool must not hide in the "present" column, and its group is
    the only place a workaround makes sense to look.

    `Capabilities.broken` maps a command name to *why* it is not usable, and
    `describe()` listed the present ones and the absent ones but never named a
    broken one at all — so a machine where `ffmpeg` segfaults reported it as
    "present" while every reply had to route around it. The two helpers that
    could have said so — `has_command` and `alternatives_for` — had no caller.
    """
    from shani_chronoa.capability import Capabilities, describe

    cap = Capabilities(commands={"ffmpeg": "/usr/bin/ffmpeg", "sox": "/usr/bin/sox"},
                       broken={"ffmpeg": "returns exit 139 on every call"})
    out = describe(cap)

    assert "not usable" in out and "ffmpeg" in out, \
        f"a broken tool does not appear in the inventory:\n{out}"
    assert "returns exit 139" in out, f"the reason is not carried through:\n{out}"
    assert "could be worked around with: sox" in out, \
        f"the working sibling in the same group is not named:\n{out}"

    # And the inventory's own building blocks must agree with each other:
    # `has_command` is the one place to ask the question, and `alternatives_for`
    # is the one place to ask it about every candidate at once.
    assert cap.has_command("sox") is True
    assert cap.has_command("vlc") is False
    assert cap.alternatives_for("ffmpeg", ("ffmpeg", "sox", "vlc")) == ["sox"]
