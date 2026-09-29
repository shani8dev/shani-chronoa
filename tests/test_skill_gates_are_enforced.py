"""The `screenshot` skill's declared consent gate was never enforced.

`capabilities.py` declares `"screenshot": "vision-sense-enabled"`, that key
exists in the shipped gschema, and the settings window has a vision toggle to
turn it off. `screenshot.py` read no consent setting at all - `_run` went
straight to `capture_screen()`. So the gate was real to the user interface and
absent from the code that acts: turning vision consent off changed what the
settings screen said and nothing about whether a capture happened.

This is the failure class this repo already has a name for. `AGENTS.md` records
modules that were "fully built, unit-tested, and never actually wired into
anything that runs", and `capabilities.py`'s own module docstring says to grep
for real callers before trusting a declared gate. A gate declared to the UI is
the same claim as a gate, minus the enforcement.

The other declared gates are asserted here too, so this file fails if any of
them regresses the same way rather than only guarding the one already found.
"""

import importlib
import inspect
import pathlib

import pytest

from shani_chronoa import capabilities
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import discover_skills, screenshot

# A declared gate is only real if the implementing module actually consults one
# of these. Listed rather than pattern-matched loosely, so a module cannot pass
# by merely mentioning the word in a docstring.
_CONSENT_CALLS = (
    "sense_allowed(",
    "notification_enabled",
    "input_control_enabled",
    "_consent(",
)

# tool -> module that implements it
_IMPL = {
    "screenshot": "shani_chronoa.skills.screenshot",
    "move_pointer": "shani_chronoa.skills.input_control",
    "click_pointer": "shani_chronoa.skills.input_control",
    "type_text": "shani_chronoa.skills.input_control",
    "notify": "shani_chronoa.skills.notify",
    # The everyday-task skills that act rather than read. `press_key` reuses the
    # existing input-control gate rather than minting its own: a key combination
    # can do anything a person at the machine could, so splitting it from the
    # pointer and typing would imply finer control than exists.
    "press_key": "shani_chronoa.skills.press_key",
    "delete_file": "shani_chronoa.skills.delete_file",
    "kill_process": "shani_chronoa.skills.kill_process",
    "close_window": "shani_chronoa.skills.close_window",
    "connect_wifi": "shani_chronoa.skills.connect_wifi",
    # Scanning is passive but is still a radio operation, so it reuses the
    # `network` sense consent rather than minting a scanning key.
    "list_wifi_networks": "shani_chronoa.skills.list_wifi_networks",
}


@pytest.fixture
def vision_off():
    config = ChronoaConfig()
    config.set("vision-sense-enabled", "false")
    config.set("privacy-mode", "true")
    return config


def _body_source(module_name):
    """The module's own code, with its docstring stripped.

    The docstring is excluded on purpose: these modules all *describe* their
    consent behaviour in prose, so including it would let a module pass this
    check by documenting a gate it does not implement - which is exactly the
    bug being guarded against.
    """
    module = importlib.import_module(module_name)
    text = pathlib.Path(module.__file__).read_text()
    docstring = inspect.getdoc(module)
    if docstring:
        text = text.replace(docstring, "")
    return text


class TestScreenshotRespectsItsGate:
    def test_a_denied_capture_never_reaches_the_screen(self, monkeypatch, vision_off):
        reached = []

        def forbidden():
            reached.append(True)
            raise AssertionError("capture_screen ran despite vision consent being off")

        monkeypatch.setattr(screenshot, "capture_screen", forbidden)
        result = screenshot._run({})
        assert reached == [], "the screen was read while vision consent was off"
        assert "not permitted" in result
        assert "vision-sense-enabled" in result, (
            "the refusal must name the setting to change, or the user cannot act"
        )

    def test_the_refusal_quotes_the_shared_reason(self, monkeypatch, vision_off):
        monkeypatch.setattr(screenshot, "capture_screen", lambda: pytest.fail("read"))
        result = screenshot._run({})
        assert vision_off.sense_allowed_reason("vision") in result

    def test_a_granted_capture_still_works(self, monkeypatch):
        """A gate that refuses unconditionally is a break, not a fix."""

        # The real Capture, not a stand-in: an earlier hand-rolled fake was
        # missing `downscaled` and failed for a reason that had nothing to do
        # with the gate.
        import time as _time

        from shani_chronoa.screengrab import Capture

        capture = Capture(
            data=b"\x89PNG fake", source="test", backend="test", width=800,
            height=600, native_width=1920, native_height=1080,
            captured_at=_time.time(),
        )
        monkeypatch.setattr(screenshot, "capture_screen", lambda: capture)

        config = ChronoaConfig()
        config.set("vision-sense-enabled", "true")
        config.set("privacy-mode", "false")
        out = screenshot._run({})
        assert "not permitted" not in out
        assert ".png" in out


class TestEveryDeclaredGateIsEnforced:
    def test_no_declared_gate_is_advertised_without_a_check(self):
        tools, _ = discover_skills()
        ungated = []
        for capability in capabilities.find_capabilities(tools):
            if not capability.consent_key:
                continue
            module_name = _IMPL.get(capability.tool)
            assert module_name, (
                f"{capability.tool} declares the gate "
                f"{capability.consent_key!r} but this test does not know which "
                f"module implements it - add it to _IMPL rather than skipping it"
            )
            body = _body_source(module_name)
            if not any(call in body for call in _CONSENT_CALLS):
                ungated.append((capability.tool, capability.consent_key))
        assert not ungated, (
            "these tools advertise a consent gate in capabilities.py that their "
            f"own code never checks: {ungated}"
        )

    def test_the_source_reader_is_not_vacuous(self):
        """If _body_source silently returned nothing, every assertion above
        would pass for the wrong reason."""
        body = _body_source("shani_chronoa.skills.screenshot")
        assert "capture_screen" in body, "failed to read the module's own code"
        head = body.split('"""')[0]
        assert "vision" not in head, (
            "the docstring was not stripped, so a prose mention could satisfy "
            "the gate check"
        )
