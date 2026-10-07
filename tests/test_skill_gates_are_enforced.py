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

import ast
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
#: Bare names, not substrings. Matched against real `ast` references below, so
#: `def _consent(...)` no longer counts as consulting consent - only an actual
#: call or attribute access does. The earlier substring form passed a skill that
#: had stopped calling its gate entirely, as long as the helper was still
#: defined in the file: verified by deleting `delete_file`'s gate check and
#: watching this test stay green.
_CONSENT_NAMES = (
    # Called, in the skills that route a decision through a helper or the
    # sense API directly.
    "sense_allowed",
    "sense_allowed_reason",
    "_consent",
    # Read as a property, in the skills that consult an existing accessor.
    "notification_enabled",
    "input_control_enabled",
    # `config.vision_sense_enabled` is the same kind of read and was missing
    # here, so `screenshot.py` was reported as "consults the vision sense, and
    # the reader missed it" - a false alarm about a gate that exists and works
    # (`config.py:627` is a property that returns `sense_allowed("vision")`).
    # It is listed, not special-cased, so the check stays "did the module
    # consult a real gate" rather than "does the reader know about this file".
    "vision_sense_enabled",
)
# `get_bool` is deliberately NOT in that list even though every gate reads a key
# through it. It appears *inside* the `_consent` helper's own body, so accepting
# it would let a skill that never calls its gate pass on the strength of a
# function it merely defines - which is the exact hole this change was made to
# close, and accepting it is what made the first attempt at the fix a no-op.

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
    "power_action": "shani_chronoa.skills.power_action",
    "install_app": "shani_chronoa.skills.install_app",
    "vpn_control": "shani_chronoa.skills.vpn_control",
    "connect_wifi": "shani_chronoa.skills.connect_wifi",
    # The lab-network builder. All four tools map to one module because they are
    # one surface split into read (`lab_network_list`, `lab_network_status`) and
    # write (`lab_network_create`, `lab_network_destroy`) halves sharing a single consent key - and the
    # check this table feeds requires the enforcing code to be findable in the
    # module it names.
    "lab_network_list": "shani_chronoa.skills.lab_network",
    "lab_network_create": "shani_chronoa.skills.lab_network",
    "lab_network_destroy": "shani_chronoa.skills.lab_network",
    "lab_network_status": "shani_chronoa.skills.lab_network",
    "capture_packets": "shani_chronoa.skills.capture_packets",
    # Scanning is passive but is still a radio operation, so it reuses the
    # `network` sense consent rather than minting a scanning key.
    "list_wifi_networks": "shani_chronoa.skills.list_wifi_networks",
    "control_service": "shani_chronoa.skills.control_service",
    "find_and_replace": "shani_chronoa.skills.find_and_replace",
    "manage_mount": "shani_chronoa.skills.manage_mount",
    "toggle_bluetooth": "shani_chronoa.skills.toggle_bluetooth",
    "set_mic_mute": "shani_chronoa.skills.set_mic_mute",
    "lock_screen": "shani_chronoa.skills.lock_screen",
    "empty_trash": "shani_chronoa.skills.empty_trash",
    "search_documents": "shani_chronoa.skills.search_documents",
    "calendar_events": "shani_chronoa.skills.calendar_events",
    "phone": "shani_chronoa.skills.phone",
    "airplane_mode": "shani_chronoa.skills.airplane_mode",
    "desktop_setting": "shani_chronoa.skills.desktop_setting",
    "set_theme": "shani_chronoa.skills.set_theme",
    "set_timezone": "shani_chronoa.skills.set_timezone",
    # The 2026-10-07 matrix skills that change something, plus login_history,
    # which reuses the `sessions` sense's key.
    "default_apps": "shani_chronoa.skills.default_apps",
    "print_queue": "shani_chronoa.skills.print_queue",
    "set_hostname": "shani_chronoa.skills.set_hostname",
    "set_locale": "shani_chronoa.skills.set_locale",
    "speed_test": "shani_chronoa.skills.speed_test",
    "login_history": "shani_chronoa.skills.login_history",
    # Sense-backed: each checks its sense's switch in its own source.
    "snapshot_status": "shani_chronoa.skills.snapshot_status",
    "security_status": "shani_chronoa.skills.security_status",
    "list_containers": "shani_chronoa.skills.list_containers",
    "boot_report": "shani_chronoa.skills.boot_report",
    "disk_health": "shani_chronoa.skills.disk_health",
    "temperatures": "shani_chronoa.skills.temperatures",
    "usb_devices": "shani_chronoa.skills.usb_devices",
    "crash_report": "shani_chronoa.skills.crash_report",
    "set_screensaver": "shani_chronoa.skills.set_screensaver",
    "set_sleep_inhibit": "shani_chronoa.skills.set_sleep_inhibit",
    # All three share `appearance-control-enabled` with set_theme: they are the
    # same permission, "change how this desktop looks", and three separate keys
    # for it would imply a distinction the user cannot act on.
    "set_wallpaper": "shani_chronoa.skills.set_wallpaper",
    "set_scaling": "shani_chronoa.skills.set_scaling",
    "toggle_night_light": "shani_chronoa.skills.toggle_night_light",
    # Shares `file-delete-enabled` with permanent deletion: it does remove the
    # file from where it was, and the difference is only that this is
    # recoverable. Refusing to gate it separately would let "clear this out" be
    # answered with the irreversible tool whenever the permanent one is allowed.
    "trash_file": "shani_chronoa.skills.trash_file",
    # Reuses `input-control-enabled` rather than minting a layout key: the layout
    # decides what every later keystroke produces, which is the same argument
    # `press_key` gives for sharing that gate.
    "set_keyboard_layout": "shani_chronoa.skills.set_keyboard_layout",
    # Both directions of a single-file edit, sharing one key.
    "edit_file": "shani_chronoa.skills.edit_file",
    "undo_last_change": "shani_chronoa.skills.undo_last_change",
    # Reuses the `git` sense's key, so the two surfaces reporting uncommitted
    # filenames cannot disagree about whether that is permitted.
    "git_inspect": "shani_chronoa.skills.git_inspect",
    "todo_list": "shani_chronoa.skills.todo_list",
    "manage_triggers": "shani_chronoa.skills.manage_triggers",
    "conversations": "shani_chronoa.skills.conversations",
    "office_document": "shani_chronoa.skills.office_document",
    "ui_elements": "shani_chronoa.skills.ui_elements",
    "android_device": "shani_chronoa.skills.android_device",
}


@pytest.fixture
def vision_off():
    config = ChronoaConfig()
    config.set("vision-sense-enabled", "false")
    config.set("privacy-mode", "true")
    return config


def _consulted_names(module_name):
    """Every function/attribute name the module actually references.

    Parsed rather than grepped. Three things a text scan cannot tell apart, and
    all three matter for a consent check:

    - `def _consent(...)` - a *definition* does not consult consent, but its
      source contains the string `_consent(`.
    - `config.input_control_enabled` - a property read, which appears as an
      attribute access rather than a call.
    - the module docstring, which describes the gate in prose.

    A gate that is advertised, defined, documented and never called is the
    failure this test exists to catch, and a substring match cannot see it.
    """
    module = importlib.import_module(module_name)
    tree = ast.parse(pathlib.Path(module.__file__).read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
    return names


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
            consulted = _consulted_names(module_name)
            if not any(name in consulted for name in _CONSENT_NAMES):
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

    def test_the_ast_reader_is_not_vacuous(self):
        """The same guard for _consulted_names, which is what the gate check
        actually uses now.

        Two ways this could be vacuous, and they fail in opposite directions.
        Returning too little would make every gated skill look ungated, which
        is loud - so that is not the dangerous case. Returning too much is the
        dangerous one: an over-broad match would satisfy the gate check for a
        skill that consults nothing, which is exactly the bug being guarded
        against. So this asserts on a module that genuinely does and genuinely
        does not consult consent.
        """
        gated = _consulted_names("shani_chronoa.skills.screenshot")
        assert "capture_screen" in gated, "failed to read the module's own code"
        # **Any recognised gate name, not `sense_allowed` specifically.**
        # `screenshot.py` consults the vision gate by reading the property
        # `config.vision_sense_enabled` (`config.py:627` returns
        # `sense_allowed("vision")`), and never calls `sense_allowed` itself -
        # so demanding that exact name asserted *how* the gate is consulted
        # rather than *that* it is, and failed against a correct skill. The
        # mutation that matters is still caught: deleting the gate from
        # `screenshot.py` empties this intersection and fails the test
        # (verified, alongside two others that also catch it).
        assert set(_CONSENT_NAMES) & gated, (
            f"screenshot consults a gate and the reader missed all of "
            f"them: {sorted(_CONSENT_NAMES)}")

        # `calculate` is an ungated skill: it must not look like it consults
        # consent just because the reader is matching too freely.
        ungated = _consulted_names("shani_chronoa.skills.calculate")
        assert "capture_screen" not in ungated
        assert not any(name in ungated for name in _CONSENT_NAMES), (
            f"an ungated skill matched the consent set: "
            f"{sorted(set(_CONSENT_NAMES) & ungated)} - the reader is too broad")
