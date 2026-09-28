"""Two capabilities the app has and the window could not reach.

Found by listing what the app actually uses and comparing it against what the
window offers, rather than by reading the window. Both are the same defect the
seventeen missing sense switches were - a setting whose only route is a
terminal - and one of them is a *consent* gate, which makes it worse: an armed
trigger rule refuses to act and the window offers no way to allow it.

Three things here are only testable because the condition is controlled rather
than inherited:

- the gate's switch must show the *stored* value on construction, which is the
  only thing that exercises the read path (toggling writes, so toggling tests
  nothing about reading);
- the no-graph branch, which this machine never reaches;
- the device labels, which a machine with no devices would never render.
"""

import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

_HARNESS = textwrap.dedent(
    """
    import json
    import gi
    gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
    from gi.repository import Gtk, Adw, GLib
    from shani_chronoa.config import ChronoaConfig
    import shani_chronoa.pipewire as pipewire

    out = {}

    def walk(node, acc):
        c = node.get_first_child()
        while c:
            walk(c, acc); acc.append(c); c = c.get_next_sibling()
        return acc

    class App(Gtk.Application):
        def __init__(s, cid):
            super().__init__(application_id=cid)
            s.config = ChronoaConfig(); s.window = None; s._wake_word_active = False
        def activate_action(s, name, arg): pass

    def picks_of(w):
        found = {}
        for r in walk(w.get_child(), []):
            if isinstance(r, Adw.PreferencesRow) and r.get_title() in ("Microphone", "Speakers"):
                found[r.get_title()] = {
                    "kind": type(r).__name__,
                    "selected": r.get_selected() if isinstance(r, Adw.ComboRow) else None,
                    "subtitle": r.get_subtitle() or "",
                    "items": ([r.get_model().get_string(i)
                               for i in range(r.get_model().get_n_items())]
                              if isinstance(r, Adw.ComboRow) else None),
                }
        return found

    # --- 1. the gate's switch must show the stored value, read not written ---
    app1 = App("test.surface.stored")
    def one(a):
        from shani_chronoa.settings_window import SettingsWindow
        a.config.set("input-control-enabled", "true")
        w = SettingsWindow(a)
        gates = [r for r in walk(w.get_child(), [])
                 if isinstance(r, Adw.SwitchRow) and "act on this machine" in r.get_title()]
        out["gate_present"] = bool(gates)
        out["gate_shows_stored_true"] = bool(gates) and gates[0].get_active()
        # And the trap this guards.
        out["get_on_bool_key"] = a.config.get("input-control-enabled")
        out["get_bool_on_bool_key"] = a.config.get_bool("input-control-enabled", False)
        a.quit()
    app1.connect("activate", one)
    app1.run([])

    # --- 2. no graph: the control must still be there and must explain itself
    app2 = App("test.surface.nograph")
    real = pipewire.is_available
    pipewire.is_available = lambda: False
    def two(a):
        from shani_chronoa.settings_window import SettingsWindow
        out["nograph"] = picks_of(SettingsWindow(a))
        a.quit()
    app2.connect("activate", two)
    app2.run([])
    pipewire.is_available = real

    # --- 3. a graph: the chooser must show readable labels, not node names ---
    app3 = App("test.surface.graph")
    real_inputs = pipewire.list_inputs
    real_outputs = pipewire.list_outputs
    from shani_chronoa.pipewire import AudioDevice
    fake = [AudioDevice(name="alsa_input.pci-0000_00_1f.3.analog-stereo",
                        description="Built-in Audio Analog Stereo",
                        kind="input", nick="Headset Mic")]
    pipewire.list_inputs = lambda: list(fake)
    pipewire.list_outputs = lambda: list(fake)
    pipewire.is_available = lambda: True
    def three(a):
        from shani_chronoa.settings_window import SettingsWindow
        out["graph"] = picks_of(SettingsWindow(a))
        a.quit()
    app3.connect("activate", three)
    app3.run([])

    print("RESULT" + json.dumps(out))
    """
)


@pytest.fixture(scope="module")
def surface(tmp_path_factory, compiled_schema_dir):
    import json

    work = tmp_path_factory.mktemp("surface")
    (work / "config").mkdir()
    (work / "data").mkdir()
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(pathlib.Path("usr/lib/shani-chronoa").resolve())
    env["XDG_CONFIG_HOME"] = str(work / "config")
    env["XDG_DATA_HOME"] = str(work / "data")
    # GSETTINGS_SCHEMA_DIR must be handed to the SUBPROCESS explicitly. It
    # cannot be inherited from conftest's gsettings_env: that fixture's
    # monkeypatch is function-scoped while this one is module-scoped, so by the
    # time this env is built the variable is not set. The harness builds its own
    # ChronoaConfig, which then cannot find org.shani.chronoa and falls back to
    # "using Python defaults" with an EMPTY _valid_keys - and set() and get_bool()
    # both return early on a key missing from it. So the write was dropped
    # without an exception and the read answered False, and the gate looked
    # permanently off for a reason that had nothing to do with the gate.
    # compiled_schema_dir is session-scoped, so requesting it costs nothing.
    env["GSETTINGS_SCHEMA_DIR"] = str(compiled_schema_dir)
    # Inherited, not replaced: PipeWire's socket lives in the session runtime
    # dir, and pointing it elsewhere made `pw-dump` fail.
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    # A keyfile backend, because there is no session bus here and dconf writes
    # fail silently without one.
    env["GSETTINGS_BACKEND"] = "keyfile"
    proc = subprocess.run(
        [sys.executable, "-c", _HARNESS],
        capture_output=True, text=True, timeout=180, env=env, cwd=str(work),
    )
    payload = None
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT"):
            payload = json.loads(line[len("RESULT"):])
    assert payload is not None, f"no result:\n{proc.stdout}\n{proc.stderr[-2000:]}"
    return payload


class TestTheActuatorGateIsReachable:
    def test_the_window_offers_it(self, surface):
        assert surface["gate_present"], (
            "input-control-enabled gates every actuator and is not in the "
            "window, so an armed rule cannot be allowed to act without a terminal"
        )

    def test_the_switch_shows_the_stored_value(self, surface):
        """The read path, not the write path.

        Toggling a switch tests writing. Only building the window with the
        setting already on tests whether it *reads* - and the first draft read
        a boolean through the string accessor, which returns the default for a
        non-string key, so the gate rendered itself permanently off while the
        setting was on.
        """
        assert surface["gate_shows_stored_true"] is True, (
            "the gate is on in the config but its switch renders off"
        )

    def test_the_trap_this_guards_is_still_a_trap(self, surface):
        assert surface["get_on_bool_key"] == ""
        assert surface["get_bool_on_bool_key"] is True


class TestTheDevicePicker:
    def test_there_is_a_microphone_control(self, surface):
        assert "Microphone" in surface["graph"] or "Microphone" in surface["nograph"], (
            "audio-input-device is honoured by the capture path and had no "
            "control, so a chosen headset could only be set with gsettings"
        )

    def test_there_is_a_speakers_control(self, surface):
        assert "Speakers" in surface["graph"] or "Speakers" in surface["nograph"]

    def test_a_graph_gives_a_chooser(self, surface):
        pick = surface["graph"].get("Microphone")
        assert pick and pick["kind"] == "ComboRow", (
            f"with devices present the row is not a chooser: {pick}"
        )

    def test_the_chooser_shows_readable_labels(self, surface):
        """A PipeWire node name is `alsa_input.pci-0000_00_1f.3.analog-stereo`.
        That is not a label a person chooses from; `AudioDevice.label` exists
        precisely for this and was unused."""
        items = surface["graph"]["Microphone"]["items"]
        assert "Headset Mic" in items, (
            f"the chooser does not show the device's readable name: {items}"
        )
        assert not any("alsa_input" in i for i in items), (
            f"a raw node name is being shown to the user: {items}"
        )

    def test_system_default_is_the_first_choice(self, surface):
        """A real option rather than the absence of one - empty means the key
        is unset and the app uses whatever the system default is."""
        assert surface["graph"]["Microphone"]["items"][0] == "System default"

    def test_no_graph_keeps_the_control_and_explains_itself(self, surface):
        pick = surface["nograph"].get("Microphone")
        assert pick, "the microphone control disappears when no graph is present"
        assert pick["subtitle"], "and it is blank rather than saying why"
        assert "PipeWire" in pick["subtitle"] or "device" in pick["subtitle"].lower()
