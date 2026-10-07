"""Backend features that worked and had no place in the UI, now surfaced.

The MCP server and the channel bridge (two binaries, no UI), the daemon's dream
pass, the reflexes, and the outcome-predictor comparison (`model_zoo`, imported
by nothing). Found by listing every backend module the GUI never names
(2026-10-08). Each test builds the real panel and drives the real backend.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()


def _walk(node, out=None):
    out = [] if out is None else out
    out.append(node)
    child = node.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out


def _texts(widget):
    return " ".join(n.get_text() for n in _walk(widget) if isinstance(n, Gtk.Label))


class _App:
    def __init__(self, gateways=""):
        self.config = type("C", (), {"get": lambda _s, k, d="": gateways if k == "gateways" else d})()


def test_connections_is_a_registered_panel():
    from shani_chronoa.gui import surfaces
    assert "connections" in surfaces.available_surfaces()
    assert surfaces.settings_target("connections") == "settings:privacy"


def test_the_connections_panel_names_both_binaries_and_a_valid_client_config():
    from shani_chronoa.gui.surfaces import connections
    page = connections.build(_App("telegram"))
    said = _texts(page)
    assert "shani-chronoa-mcp" in said and "shani-chronoa-bridge" in said, said
    assert "Chronoa is accepting it" in said          # telegram is in gateways
    assert "add 'whatsapp' to Gateways" in said       # whatsapp is not
    config = json.loads(connections.mcp_client_config("/usr/bin/shani-chronoa-mcp"))
    assert config["mcpServers"]["shani-chronoa"]["command"] == "/usr/bin/shani-chronoa-mcp"
    assert page.status() in ("ok", "off")


def test_a_running_bridge_is_seen(tmp_path):
    """A real process named like the bridge; the panel must find it. Killed by PID."""
    from shani_chronoa.gui.surfaces import connections
    fake = tmp_path / "shani-chronoa-bridge"
    # A Python script, as the real one is: `exec sleep` would replace the
    # command line and the fake would stop looking like the bridge.
    fake.write_text("import time\ntime.sleep(30)\n")
    proc = subprocess.Popen([sys.executable, str(fake), "telegram"])
    try:
        import time
        time.sleep(0.3)
        seen = connections._running("shani-chronoa-bridge")
    finally:
        proc.kill()
        proc.wait(timeout=5)
    assert any("telegram" in line for line in seen), seen
    assert not connections._running("shani-chronoa-bridge-not-a-real-name")


def test_the_learning_panel_offers_the_three_background_passes():
    from shani_chronoa.gui.surfaces import learning
    page = learning.build(None)
    labels = {b.get_label() for b in _walk(page) if isinstance(b, Gtk.Button)}
    assert {"Dream now", "Check now", "Compare"} <= labels, labels


@pytest.mark.parametrize("name", ["_dream_text", "_reflex_text", "_zoo_text"])
def test_each_pass_returns_its_own_report(name):
    """Run for real on the test's empty home: an honest sentence, not an exception."""
    from shani_chronoa.gui.surfaces import learning
    text = getattr(learning, name)()
    assert isinstance(text, str) and text.strip(), name


def _arm(view, timeout=30):
    import time
    from gi.repository import GLib
    view._form_result.set_text("")
    view._on_arm(view._form_arm)
    ctx, start = GLib.MainContext.default(), time.time()
    while view._form_result.get_text() in ("", "Arming...") and time.time() - start < timeout:
        ctx.iteration(False)
        time.sleep(0.02)
    return view._form_result.get_text()


def test_the_triggers_panel_arms_a_rule_through_the_chat_skill(gsettings_env):
    """The panel could delete a rule and not make one. The form goes through
    `manage_triggers` - consent, validation, destructive refusal and all."""
    from shani_chronoa.config import ChronoaConfig
    from shani_chronoa.gui.surfaces import triggers
    view = triggers.build(_App()).surface
    view._form_name.set_text("battery-note")
    view._form_text.set_text("Discharging")
    view._form_sense.set_selected(view._form_senses.index("power"))
    view._form_skill.set_selected(view._form_skills.index("notify"))

    view._form_args.set_text("{oops")
    assert "not valid JSON" in _arm(view)

    view._form_args.set_text('{"summary": "on battery"}')
    assert "turned off" in _arm(view), "armed without the consent key"

    ChronoaConfig().set("trigger-control-enabled", "true")
    said = _arm(view)
    assert said.startswith("Armed 'battery-note'"), said
    assert view.row_count() == 1, "the new rule is not listed"

    view._form_name.set_text("nope")
    view._form_skill.set_selected(view._form_skills.index("delete_file"))
    view._form_args.set_text('{"path": "~/x"}')
    assert "destructive" in _arm(view)


def test_the_form_arms_an_event_rule_too(gsettings_env):
    """Eighteen event types could only be armed from chat; the form had senses only."""
    from shani_chronoa.config import ChronoaConfig
    from shani_chronoa.gui.surfaces import triggers
    ChronoaConfig().set("trigger-control-enabled", "true")
    view = triggers.build(_App()).surface
    view._form_kind.set_selected(view._form_kinds.index("screenlock"))
    assert not view._form_sense.get_parent().get_visible()
    assert view._form_source.get_parent().get_visible()
    view._form_name.set_text("locked-note")
    view._form_source.set_text("locked")
    view._form_skill.set_selected(view._form_skills.index("notify"))
    view._form_args.set_text('{"summary": "screen locked"}')
    assert _arm(view).startswith("Armed 'locked-note': screenlock on locked")
    view._form_name.set_text("bad-source")
    view._form_source.set_text("banana")
    assert "source must be one of" in _arm(view)
