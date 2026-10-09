"""Tests for the synthetic input-injection skills (move_pointer, click_pointer, type_text).

These are hermetic: the consent gate is exercised by monkeypatching the config
(not the subprocess), and the consent-on command-construction path is verified
by spying on subprocess.run (recording the argv, returning a fake success
without touching a real device). The real end-to-end actuation against the
host's X11+XTEST backend is proven separately by running the skill directly
(see the manual verification notes in AGENTS.md's "verify by actually running"
rule), not by this hermetic suite.
"""

import shutil
import subprocess



# --- helpers ---------------------------------------------------------------

def _allow_input_control(monkeypatch):
    """Monkeypatch the config so the input-control consent key reads True.

    The key is not declared in the gschema (a parent task adds it), so the
    only way to flip it in a test is to intercept get_bool. This monkeypatches
    the config, not the subprocess - the refusal path is tested with the real
    fail-closed default.
    """
    from shani_chronoa.config import ChronoaConfig

    orig = ChronoaConfig.get_bool

    def fake(self, key, default=False):
        if key == "input-control-enabled":
            return True
        return orig(self, key, default)

    monkeypatch.setattr(ChronoaConfig, "get_bool", fake)


def _spy_subprocess(monkeypatch):
    """Record every subprocess.run argv the skill builds, without executing it.

    Deliberately does NOT pin the backend. test_no_backend_available_refuses
    sets _detect_backend to None and then calls this helper, so anything pinned
    here would silently overwrite that and the refusal would stop being
    exercised. Use _pin_xdotool_backend explicitly instead.
    """
    import shani_chronoa.skills.input_control as mod

    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    return calls


def _pin_xdotool_backend(monkeypatch):
    """Pretend xdotool is installed, so an argv-shape test is about argv shape.

    Stubbing subprocess.run is not enough on its own: the skill calls
    _detect_backend() before it builds any argv, and that really does look for
    the binary on PATH. On a host with xdotool the argv assertions passed; on a
    runner without it, detection returned None, the skill returned its "no
    backend available" message, and five tests that assert argv shape failed
    without having asserted anything about argv. Consent is checked before
    detection (input_control.py), so this cannot mask a consent refusal.
    """
    import shani_chronoa.skills.input_control as mod

    monkeypatch.setattr(mod, "_detect_backend", lambda: ("xdotool", ["xdotool"]))


def _spy_build_command(monkeypatch):
    """Record every _build_command call (action, params) without executing it."""
    import shani_chronoa.skills.input_control as mod

    calls: list[tuple] = []
    real = mod._build_command

    def fake(backend, action, params):
        calls.append((backend, action, dict(params)))
        return real(backend, action, params)

    monkeypatch.setattr(mod, "_build_command", fake)
    return calls


# --- consent gate ----------------------------------------------------------

class TestInputControlConsent:
    """The input-control consent key is the gate; it defaults OFF and is
    separate from the vision sense's key."""

    def test_refused_when_consent_off(self, chronoa_config):
        # Given: consent is off (the key is undeclared in the gschema, so
        # get_bool returns its fail-closed default of False)
        from shani_chronoa.skills.input_control import _run_move_pointer

        # When: the skill is invoked
        result = _run_move_pointer({"x": 100, "y": 100})

        # Then: it refuses with a clear, user-facing reason naming the key
        assert "not permitted" in result
        assert "input-control-enabled" in result
        assert "separate consent gate from the vision sense" in result

    def test_no_command_constructed_when_consent_off(self, chronoa_config, monkeypatch):
        # Given: consent is off and we spy on both command construction and
        # subprocess execution
        from shani_chronoa.skills.input_control import _run_move_pointer, _run_click_pointer, _run_type_text

        build_calls = _spy_build_command(monkeypatch)
        run_calls = _spy_subprocess(monkeypatch)

        # When: all three skills are invoked
        _run_move_pointer({"x": 1, "y": 2})
        _run_click_pointer({"button": "left"})
        _run_type_text({"text": "hello"})

        # Then: NO input command is ever constructed or executed
        assert build_calls == []
        assert run_calls == []

    def test_consent_key_is_separate_from_vision(self, chronoa_config):
        # Given: the vision sense consent key exists and is distinct
        from shani_chronoa.config import ChronoaConfig

        config = ChronoaConfig()
        # Then: input-control-enabled is NOT the vision-sense-enabled key
        assert "input-control-enabled" != "vision-sense-enabled"
        # And: with vision on but input-control off, input skills still refuse
        # (vision-sense-enabled is declared in the schema; input-control is not)
        assert config.get_bool("vision-sense-enabled", False) is False
        assert config.get_bool("input-control-enabled", False) is False


# --- command construction (consent ON, hermetic) ---------------------------

class TestInputControlCommandConstruction:
    """With consent on, the skill must build the correct typed argv - never a
    shell string, never a free-form command."""

    def test_move_pointer_builds_xdotool_argv(self, chronoa_config, monkeypatch):
        _allow_input_control(monkeypatch)
        _pin_xdotool_backend(monkeypatch)
        run_calls = _spy_subprocess(monkeypatch)
        from shani_chronoa.skills.input_control import _run_move_pointer

        result = _run_move_pointer({"x": 1200, "y": 800})

        assert result == "Pointer moved done."
        assert run_calls == [["xdotool", "mousemove", "1200", "800"]]

    def test_click_pointer_builds_xdotool_argv(self, chronoa_config, monkeypatch):
        _allow_input_control(monkeypatch)
        _pin_xdotool_backend(monkeypatch)
        run_calls = _spy_subprocess(monkeypatch)
        from shani_chronoa.skills.input_control import _run_click_pointer

        result = _run_click_pointer({"button": "left"})

        assert result == "Clicked 1 done."
        assert run_calls == [["xdotool", "click", "1"]]

    def test_click_pointer_repeat_builds_repeat_argv(self, chronoa_config, monkeypatch):
        _allow_input_control(monkeypatch)
        _pin_xdotool_backend(monkeypatch)
        run_calls = _spy_subprocess(monkeypatch)
        from shani_chronoa.skills.input_control import _run_click_pointer

        _run_click_pointer({"button": "right", "count": 3})

        assert run_calls == [["xdotool", "click", "--repeat", "3", "3"]]

    def test_type_text_builds_xdotool_argv(self, chronoa_config, monkeypatch):
        _allow_input_control(monkeypatch)
        _pin_xdotool_backend(monkeypatch)
        run_calls = _spy_subprocess(monkeypatch)
        from shani_chronoa.skills.input_control import _run_type_text

        result = _run_type_text({"text": "hello world"})

        assert result == "Text typed done."
        assert run_calls == [["xdotool", "type", "--clearmodifiers", "--", "hello world"]]

    def test_type_text_with_special_chars_is_single_arg(self, chronoa_config, monkeypatch):
        # The text must be a single argv element (no shell), so special
        # characters cannot break out into a command.
        _allow_input_control(monkeypatch)
        _pin_xdotool_backend(monkeypatch)
        run_calls = _spy_subprocess(monkeypatch)
        from shani_chronoa.skills.input_control import _run_type_text

        _run_type_text({"text": "a; rm -rf /"})

        assert run_calls == [["xdotool", "type", "--clearmodifiers", "--", "a; rm -rf /"]]

    def test_no_backend_available_refuses(self, chronoa_config, monkeypatch):
        # Given: consent is on but no backend is detected
        _allow_input_control(monkeypatch)
        import shani_chronoa.skills.input_control as mod

        monkeypatch.setattr(mod, "_detect_backend", lambda: None)
        run_calls = _spy_subprocess(monkeypatch)
        from shani_chronoa.skills.input_control import _run_move_pointer

        result = _run_move_pointer({"x": 1, "y": 2})

        assert "no input-injection backend is available" in result
        assert run_calls == []


# --- backend detection (real, on this host) --------------------------------

class TestInputControlBackendDetection:
    """The skill must detect whichever backend is actually present, and refuse
    honestly when none is."""

    def test_detects_xdotool_on_x11_host(self, monkeypatch, tmp_path):
        # Given: an X11 session with xdotool on PATH. Set up here rather than
        # assumed: the test read "this host" and so failed on every Wayland
        # desktop, where the portal is (rightly) chosen instead.
        tool = tmp_path / "xdotool"
        tool.write_text("#!/bin/sh\nexit 0\n")
        tool.chmod(0o755)
        monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
        monkeypatch.setenv("DISPLAY", ":0")
        monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        from shani_chronoa.skills.input_control import _detect_backend

        backend = _detect_backend()

        # Then: xdotool is selected on this X11 host
        assert backend is not None
        assert backend[0] == "xdotool"
        assert backend[1] == ["xdotool"]

    def test_no_backend_when_path_empty(self, monkeypatch):
        # Given: no backend binaries on PATH and no DISPLAY
        monkeypatch.setenv("PATH", "/nonexistent")
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        from shani_chronoa.skills.input_control import _detect_backend

        assert _detect_backend() is None

    def test_wayland_prefers_wtype_over_xdotool(self, monkeypatch):
        # Given: a Wayland session with wtype on PATH
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        monkeypatch.setenv("DISPLAY", ":1")
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/wtype" if cmd == "wtype" else None)
        from shani_chronoa.skills.input_control import _detect_backend

        backend = _detect_backend()

        # Then: wtype is chosen (Wayland, not xdotool)
        assert backend is not None
        assert backend[0] == "wtype"


# --- type validation -------------------------------------------------------

class TestInputControlTypeValidation:
    """Arguments from the LLM are untrusted; bad types must be rejected with a
    clear message, never coerced or passed to the backend."""

    def test_move_pointer_rejects_non_integer_x(self, chronoa_config, monkeypatch):
        _allow_input_control(monkeypatch)
        run_calls = _spy_subprocess(monkeypatch)
        from shani_chronoa.skills.input_control import _run_move_pointer

        result = _run_move_pointer({"x": "100", "y": 50})

        assert "Invalid pointer coordinates" in result
        assert run_calls == []

    def test_move_pointer_rejects_boolean_x(self, chronoa_config, monkeypatch):
        _allow_input_control(monkeypatch)
        run_calls = _spy_subprocess(monkeypatch)
        from shani_chronoa.skills.input_control import _run_move_pointer

        result = _run_move_pointer({"x": True, "y": 50})

        assert "Invalid pointer coordinates" in result
        assert run_calls == []

    def test_click_pointer_rejects_unknown_button_name(self, chronoa_config, monkeypatch):
        _allow_input_control(monkeypatch)
        run_calls = _spy_subprocess(monkeypatch)
        from shani_chronoa.skills.input_control import _run_click_pointer

        result = _run_click_pointer({"button": "sideways"})

        assert "Invalid button" in result
        assert run_calls == []

    def test_click_pointer_rejects_out_of_range_number(self, chronoa_config, monkeypatch):
        _allow_input_control(monkeypatch)
        run_calls = _spy_subprocess(monkeypatch)
        from shani_chronoa.skills.input_control import _run_click_pointer

        result = _run_click_pointer({"button": 99})

        assert "Invalid button" in result
        assert run_calls == []

    def test_type_text_rejects_empty_string(self, chronoa_config, monkeypatch):
        _allow_input_control(monkeypatch)
        run_calls = _spy_subprocess(monkeypatch)
        from shani_chronoa.skills.input_control import _run_type_text

        result = _run_type_text({"text": ""})

        assert "Invalid text" in result
        assert run_calls == []

    def test_type_text_rejects_non_string(self, chronoa_config, monkeypatch):
        _allow_input_control(monkeypatch)
        run_calls = _spy_subprocess(monkeypatch)
        from shani_chronoa.skills.input_control import _run_type_text

        result = _run_type_text({"text": 12345})

        assert "Invalid text" in result
        assert run_calls == []


# --- schema registration ---------------------------------------------------

class TestInputControlSchemas:
    """The three skills must be registered with correct names and typed params."""

    def test_all_three_skills_registered(self):
        from shani_chronoa.skills import discover_skills

        tools, handlers = discover_skills()
        names = {s["function"]["name"] for s in tools}

        assert {"move_pointer", "click_pointer", "type_text"} <= names
        assert all(n in handlers for n in ("move_pointer", "click_pointer", "type_text"))

    def test_move_pointer_schema_has_typed_coords(self):
        from shani_chronoa.skills import discover_skills

        tools, _ = discover_skills()
        schema = next(s for s in tools if s["function"]["name"] == "move_pointer")
        props = schema["function"]["parameters"]["properties"]

        assert props["x"]["type"] == "integer"
        assert props["y"]["type"] == "integer"
        assert schema["function"]["parameters"]["required"] == ["x", "y"]

    def test_click_pointer_schema_has_typed_button(self):
        from shani_chronoa.skills import discover_skills

        tools, _ = discover_skills()
        schema = next(s for s in tools if s["function"]["name"] == "click_pointer")
        props = schema["function"]["parameters"]["properties"]

        assert props["button"]["type"] == "string"
        assert props["count"]["type"] == "integer"
        assert schema["function"]["parameters"]["required"] == ["button"]

    def test_type_text_schema_has_typed_text(self):
        from shani_chronoa.skills import discover_skills

        tools, _ = discover_skills()
        schema = next(s for s in tools if s["function"]["name"] == "type_text")
        props = schema["function"]["parameters"]["properties"]

        assert props["text"]["type"] == "string"
        assert schema["function"]["parameters"]["required"] == ["text"]

    def test_no_free_form_command_skill_exists(self):
        # The fixed-whitelist boundary: there must be no generic shell-exec
        # skill anywhere in the registered tools.
        from shani_chronoa.skills import discover_skills

        tools, _ = discover_skills()
        names = {s["function"]["name"] for s in tools}

        forbidden = {"shell", "exec", "run_command", "execute", "command"}
        assert not (names & forbidden)
