"""A sense switch that renders but writes nothing is the one failure none of
the other surface tests can see.

`test_settings_surface.py` proves a switch exists for every consent key, is
sensitive, and shows the right initial state. `test_sense_grouping.py` proves
every sense is in exactly one group. `test_settings_surface_is_complete.py`
proves every sense has a row. All three keep passing if the row's `on_toggle`
callback stops writing - which was confirmed by mutation, not assumed: adding an
early `return` to `SettingsWindow._set_sense()` leaves the whole settings surface
suite green, because "the row is there" and "the row does something" are two
different claims and only the first was being tested.

The distinction matters more here than anywhere else in this file's surface. A
consent switch is the only place a user can grant a capability. A dead one is
indistinguishable from a denied one: Chronoa correctly refuses to sense, and the
window confidently shows the permission as granted, so the user concludes the
feature is broken rather than that they are being lied to. `AGENTS.md` records
this exact class shipping three times in this repo already.

So this toggles the real `Adw.SwitchRow` and reads the value back through a
**fresh** `ChronoaConfig` - a new `Gio.Settings` handle over the same keyfile,
not the instance the window holds. Reading the same object back would pass even
if the write went nowhere but was cached.
"""

import json
import pathlib
import subprocess
import sys
import textwrap

import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")
from shani_chronoa.config import _SENSE_CONSENT_ALIASES  # noqa: E402

# A child process, like the sibling harnesses: a Gtk.Application built
# in-process leaves a main loop and a display connection behind that upsets
# every later test in the session.
_HARNESS = textwrap.dedent(
    """
    import json
    import gi
    gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
    from gi.repository import Gtk, Adw, GLib

    from shani_chronoa.config import (
        ChronoaConfig, _SENSE_CONSENT_KEYS, _SENSE_DEFAULT_ENABLED,
        _NETWORKED_SENSES, _SENSE_CONSENT_ALIASES,
    )
    from shani_chronoa.senses import discover_senses

    class App(Gtk.Application):
        def __init__(self):
            super().__init__(application_id="test.chronoa.switchwrites")
            self.config = ChronoaConfig()
            self.window = None
            self._wake_word_active = False
        def activate_action(self, name, arg):
            pass

    app = App()
    out = {"rows": {}, "guard": {}}

    def on_activate(a):
        from shani_chronoa.settings_window import SettingsWindow

        # A missing compiled schema makes ChronoaConfig degrade to Python
        # defaults with an empty _valid_keys: set() returns early and get_bool()
        # answers the default. Every write then looks dropped and every read
        # looks plausible, so this suite would report a defect that is not in
        # the code. Establish the store is real before believing any result.
        out["guard"]["settings_loaded"] = a.config._settings is not None
        out["guard"]["keys_valid"] = sorted(
            k for k in _SENSE_CONSENT_KEYS.values() if k in a.config._valid_keys)
        if not out["guard"]["settings_loaded"]:
            a.quit()
            return False

        w = SettingsWindow(a)
        before = {n: a.config.get_bool(k)
                  for n, k in _SENSE_CONSENT_KEYS.items() if k}

        for name, row in w._sense_rows.items():
            key = _SENSE_CONSENT_KEYS.get(name)
            if not key:
                continue
            was = row.get_active()
            row.set_active(not was)
            out["rows"][name] = {
                "from": was,
                "to": not was,
                "wrote": ChronoaConfig().get_bool(key) == (not was),
                "row_followed": row.get_active() == (not was),
            }
            row.set_active(was)
            out["rows"][name]["reverted"] = (
                ChronoaConfig().get_bool(key) == was)
            out["rows"][name]["collateral"] = sorted(
                other for other, k2 in _SENSE_CONSENT_KEYS.items()
                if k2 and other != name
                and a.config.get_bool(k2) != before[other])

        # Whether the row is *expected* to follow the write. Privacy mode gates
        # the networked senses independently of consent, so their row correctly
        # refuses to move - that is the feature working, not the switch dead.
        out["snapback_expected"] = sorted(
            n for n in w._sense_rows
            if n in _NETWORKED_SENSES or n in _SENSE_CONSENT_ALIASES)
        out["registry"] = sorted(discover_senses())
        a.quit()
        return False

    app.connect("activate", on_activate)
    GLib.timeout_add(30000, lambda: (app.quit(), False)[1])
    app.run([])
    print("RESULT" + json.dumps(out))
    """
)


@pytest.fixture(scope="module")
def toggled(tmp_path_factory, compiled_schema_dir):
    import os

    work = tmp_path_factory.mktemp("switchwrites")
    (work / "config").mkdir()
    (work / "data").mkdir()
    env = dict(os.environ)
    env.update(
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONPATH=str(pathlib.Path("usr/lib/shani-chronoa").resolve()),
        GSETTINGS_SCHEMA_DIR=str(compiled_schema_dir),
        GSETTINGS_BACKEND="keyfile",
        XDG_CONFIG_HOME=str(work / "config"),
        XDG_DATA_HOME=str(work / "data"),
        XDG_STATE_HOME=str(work),
    )
    proc = subprocess.run(
        [sys.executable, "-c", _HARNESS], capture_output=True, text=True,
        timeout=180, env=env, cwd=str(work),
    )
    payload = None
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT"):
            payload = json.loads(line[len("RESULT"):])
    assert payload is not None, (
        f"harness produced no result:\n{proc.stdout}\n{proc.stderr[-2000:]}"
    )
    return payload


class TestTheStoreUnderneathIsReal:
    """If this fails, every other result in the file is meaningless."""

    def test_the_config_actually_loaded_the_compiled_schema(self, toggled):
        assert toggled["guard"]["settings_loaded"] is True, (
            "ChronoaConfig found no schema, so set() was a silent no-op and "
            "get_bool() was answering Python defaults. Every result below "
            "would be a measurement of the harness, not of the code."
        )

    def test_every_sense_consent_key_exists_in_the_schema(self, toggled):
        from shani_chronoa.config import _SENSE_CONSENT_KEYS

        valid = set(toggled["guard"]["keys_valid"])
        missing = sorted(k for k in _SENSE_CONSENT_KEYS.values()
                         if k and k not in valid)
        assert not missing, (
            f"the compiled schema does not declare these consent keys, so no "
            f"switch for them could ever write: {missing}"
        )


class TestEverySwitchActuallyWrites:
    def test_no_sense_row_is_missing_from_the_report(self, toggled):
        from shani_chronoa.senses import discover_senses

        assert not (set(discover_senses()) - set(toggled["rows"])), (
            "these senses produced no toggle result at all: "
            f"{sorted(set(discover_senses()) - set(toggled['rows']))}"
        )

    def test_toggling_a_switch_reaches_the_stored_setting(self, toggled):
        """The claim itself: on for the seven that ship on, off for `git`.

        `git` is the load-bearing case. It is the one sense in the recent
        machine-state batch that defaults off, so it is the only row whose
        switch starts in the "grant" position - and a switch that is present,
        labelled and correctly off is exactly the shape of a permission that
        cannot be granted.
        """
        dead = sorted(
            name for name, r in toggled["rows"].items()
            if not r["wrote"] and name not in toggled["snapback_expected"]
        )
        assert not dead, (
            "these switches render and are labelled but their toggle never "
            "reached the stored setting, so the permission cannot actually be "
            f"granted or revoked: {dead}"
        )

    def test_git_can_be_granted_and_withdrawn(self, toggled):
        """Named rather than swept, so a regression names the sense that broke."""
        row = toggled["rows"]["git"]
        assert row["from"] is False, (
            "git is documented as defaulting off; if it now ships on, the "
            "reasoning in config._SENSE_DEFAULT_ENABLED needs revisiting"
        )
        assert row["to"] is True and row["wrote"] is True, (
            "the git switch did not grant the sense: " f"{row}"
        )
        assert row["reverted"] is True, (
            f"the git switch could not be withdrawn again: {row}"
        )

    def test_a_withdrawn_switch_does_not_snap_back(self, toggled):
        """A row that reverts to its old state after a write is showing the
        user something the system does not agree with."""
        stuck = sorted(
            name for name, r in toggled["rows"].items()
            if not r["row_followed"] and name not in toggled["snapback_expected"]
        )
        assert not stuck, f"switches did not reflect their own write: {stuck}"

    def test_toggling_one_switch_moves_no_other(self, toggled):
        noisy = {n: r["collateral"] for n, r in toggled["rows"].items()
                 if r["collateral"]}
        assert not noisy, (
            "toggling one sense switch changed another sense's stored "
            f"permission: {noisy}"
        )
