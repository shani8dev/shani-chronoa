"""A consent switch that is on in Settings is on in the sandboxed child too.

Measured 2026-10-15: with `run-command-enabled=true`, `run_command` answered
"running commands is turned off" every time. The skill runs in a confined
child, the child reads its consent through GSettings, and the sandbox does not
let it read the settings store (`~/.config` is outside its allowlist). Only a
*fresh* "allow" was handed across, as `SHANI_CHRONOA_CONSENT_GRANT`; a switch
already on was not, so on was read as off.

The dispatcher reads the switch in this process and hands the child the same
one-key grant. Captured at the boundary - the argv the sandbox is given - so
the test does not depend on bubblewrap being installed.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import buildmode, config as config_mod, tools  # noqa: E402
from shani_chronoa.config import CONSENT_GRANT_ENV, ChronoaConfig  # noqa: E402


def _child_argv(monkeypatch, name, arguments):
    seen = []

    def execute(argv, config, **_kw):
        seen.append(list(argv))
        return 0, "ok", 1

    monkeypatch.setattr(tools._SANDBOX, "execute", execute)
    tools.execute_tool(name, arguments)
    assert seen, f"{name} never reached the sandbox"
    return seen[0]


def _grants(argv):
    return [a for a in argv if a.startswith(CONSENT_GRANT_ENV + "=")]


def test_a_switch_that_is_on_is_handed_to_the_child(tmp_path, monkeypatch):
    project = tmp_path / "shop"
    project.mkdir()
    ChronoaConfig().set("run-command-enabled", "true")
    buildmode.set_enabled(True)
    buildmode.set_project(str(project))
    try:
        argv = _child_argv(monkeypatch, "run_command", {"command": "true"})
    finally:
        buildmode.set_project("")
        buildmode.set_enabled(False)
    assert _grants(argv) == [f"{CONSENT_GRANT_ENV}=run-command-enabled"], argv


def test_an_ungated_tool_gets_no_grant(monkeypatch):
    name = "get_battery_status"
    assert tools._consent_key_for(name) is None
    argv = _child_argv(monkeypatch, name, {})
    assert not _grants(argv), f"{name} has no switch and was handed one: {argv}"


def test_the_grant_opens_only_its_own_key(monkeypatch):
    monkeypatch.setenv(CONSENT_GRANT_ENV, "run-command-enabled")
    config = ChronoaConfig()
    assert config.get_bool("run-command-enabled", False)
    config.set("goals-enabled", "false")
    assert not config.get_bool("goals-enabled", True)
    assert config_mod.granted_consent_key() == "run-command-enabled"
