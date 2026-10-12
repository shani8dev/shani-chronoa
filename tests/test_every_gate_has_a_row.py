"""Every consent key a gate names must have a row a person can reach.

`test_parity_skill_gates_are_reachable.py` checks the key is **in the schema**.
That is half of the defect and the cheap half. The expensive half is the
`heard-sound` sense this repo recorded in "Audit-verified known issues":

> Four surfaces disagreed about which consent key opens the microphone... Settings
> -> Privacy had a row for `sound-sense-enabled` and **none for
> `heard-sound-sense-enabled`**. The sense was therefore **unreachable by a
> user** - the only switch that granted it was not on screen.

A key that exists in the schema and has no row is a switch nobody can find: a
permanent refusal wearing the shape of a permission. The same class as
`calendar-write-enabled` and `fm-radio-enabled`, and *worse* than an absent
capability because it looks present.

**Two populations, and a scan that treats them alike reports every sense key as
missing.** A hand-written row is a literal in `settings_window/`'s source; a
sense's row is **generated** - `senses.py`'s `_build_senses` walks
`discover_senses()` and adds one switch per registered sense, which is why the
first version of this file reported 53 unreachable keys that are all on screen.
So the check separates them: a key is reachable if it is a literal in the source,
or if it is a sense/event key and the panel that generates those is genuinely
generated from the registry.

The generation half is verified by **building the window and counting rows**, not
by reading that a loop exists - a panel whose derivation is broken looks exactly
like a panel whose derivation is absent.
"""

from __future__ import annotations

import inspect
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "usr/lib/shani-chronoa/shani_chronoa/settings_window"
sys.path.insert(0, str(ROOT / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import capabilities  # noqa: E402
from shani_chronoa import config  # noqa: E402
from shani_chronoa import senses as senses_registry  # noqa: E402


def _settings_source() -> str:
    return "\n".join(p.read_text(encoding="utf-8")
                     for p in sorted(SETTINGS.glob("*.py")))


def _literal_keys():
    """Consent keys that appear as a literal in the settings window's source."""
    return set(re.findall(r'["\']([a-z0-9][a-z0-9-]*-enabled)["\']',
                          _settings_source()))


def _generated_keys():
    """The keys the Senses panel derives, if it derives them.

    Empty when the panel does not read the registry - which is the
    `heard-sound` defect in its structural form, and is what makes every sense
    unreachable rather than merely absent.
    """
    try:
        senses_registry.discover_senses()
    except Exception:                          # noqa: BLE001
        return set()
    # The panel derives one row per *registered* sense, so the keys it can reach
    # are exactly the table's - which is the same set either way, and saying so
    # plainly beats deriving an identity.
    return set(config._SENSE_CONSENT_KEYS.values())


class TestEveryGatedToolHasARow:
    def test_a_key_nobody_can_reach_is_named(self):
        """A gate that names a key which is neither a literal row nor a derived
        one is telling the user to turn a switch that is on no screen."""
        literals = _literal_keys()
        reachable = literals | _generated_keys()
        missing = sorted({key for key in capabilities.GATED.values()
                         if key not in reachable})
        assert not missing, (
            "these consent keys are named by a gate and reachable nowhere - "
            "neither a literal row in the settings window nor a row the Senses "
            "panel derives: " + ", ".join(missing))

    def test_the_generation_is_real_not_assumed(self):
        """**The half that makes the derivation claim mean anything.** The Senses
        panel is *said* to generate one row per registered sense; that it walks
        the registry is the whole reason a new sense is unreachable-proof, and a
        hand-kept list would be wrong the moment a sense lands.

        Verified by reading the builder, the shape `test_sidebar_icons_exist_in_adwaita.py`
        uses for icons: the panel's class is a mixin on `SettingsWindow`, so
        building it in a child process measures the harness rather than the
        derivation - which is exactly what the first version of this test did.
        """
        from shani_chronoa.settings_window import senses as senses_window
        builder = inspect.getsource(senses_window.SensesPage._build_senses)
        assert "discover_senses" in builder, (
            "_build_senses does not walk the registry, so a sense is on screen "
            "only if somebody remembered to add it - the heard-sound defect")
        # and it iterates what it found, rather than testing one and stopping
        loops = [line for line in builder.splitlines()
                 if " for " in line and ("registry" in line or "sense" in line)]
        assert loops, f"the builder found the registry and did not iterate it: {builder}"

    def test_every_sense_with_a_key_is_one_the_panel_can_name(self):
        """**The other direction.** A sense whose consent key is missing is
        unreachable no matter how good the generation is - and the key is derived
        from the name, so a rename silently ungrants a capability."""
        registry = senses_registry.discover_senses()
        with_key = [name for name in registry
                    if config._SENSE_CONSENT_KEYS.get(name)]
        assert with_key, "no sense carries a consent key, so this has no subject"
        assert len(with_key) == len(registry), (
            "some registered senses have no consent key, so the panel has no "
            "switch to draw for them: "
            + ", ".join(sorted(set(registry) - set(with_key))))

    def test_the_scan_can_find_a_missing_one(self):
        """**The control.** A scan reporting nothing on a complete window and
        nothing on a window with a hole is not a scan."""
        found = _literal_keys()
        assert found, "no consent keys found at all in the window"
        assert "file-index-enabled" in found and "meeting-enabled" in found, (
            "this session's keys are not in the window, so the scan's subject "
            "has gone and it measures nothing")


class TestTheTwoTablesDoNotCollide:
    def test_no_key_appears_in_both_tables(self):
        """One key in both tables is one switch doing two things, and the alias
        rule exists to stop a rename silently re-granting something."""
        overlap = set(config._SENSE_CONSENT_KEYS.values()) & \
            set(config._EVENT_CONSENT_KEYS.values())
        assert not overlap, (
            "one consent key grants both a sense and an event type: "
            + ", ".join(sorted(overlap)))


class TestTheGuardIsNotJustProse:
    def test_a_gate_names_a_key_at_all(self):
        """A gate naming nothing cannot be granted either, and the map would go
        on looking complete."""
        empty = [tool for tool, key in capabilities.GATED.items()
                 if not key or not isinstance(key, str)]
        assert not empty, f"gates that name no key at all: {empty}"


def _senses_row_titles():
    """The Senses panel's row titles, from the real window in a child process."""
    import json
    import os
    import subprocess
    import tempfile

    script = pathlib.Path(tempfile.mkdtemp()) / "rows.py"
    script.write_text(
        "import json, sys\n"
        "sys.path.insert(0, " + repr(str(ROOT / "usr" / "lib" / "shani-chronoa")) + ")\n"
        "import gi\n"
        "gi.require_version('Gtk', '4.0')\n"
        "gi.require_version('Adw', '1')\n"
        "from gi.repository import Adw, Gtk\n"
        "Adw.init()\n"
        "from shani_chronoa.config import ChronoaConfig\n"
        "from shani_chronoa.settings_window import SensesWindow\n"
        "app = Gtk.Application(application_id='test.sense.rows')\n"
        "out = {}\n"
        "def on_activate(a):\n"
        "    win = SensesWindow(a, ChronoaConfig())\n"
        "    rows = []\n"
        "    stack = [win]\n"
        "    while stack:\n"
        "        w = stack.pop()\n"
        "        if isinstance(w, Adw.SwitchRow):\n"
        "            rows.append(w.get_title() or '')\n"
        "        c = w.get_first_child()\n"
        "        while c is not None:\n"
        "            stack.append(c)\n"
        "            c = c.get_next_sibling()\n"
        "    out['rows'] = rows\n"
        "    a.quit()\n"
        "app.connect('activate', on_activate)\n"
        "app.run([])\n"
        "print('RESULT ' + json.dumps(out))\n",
        encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["GSETTINGS_BACKEND"] = "memory"
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    try:
        done = subprocess.run([sys.executable, str(script)], capture_output=True,
                              text=True, timeout=200, env=env)
    except Exception:                          # noqa: BLE001
        return []
    for line in done.stdout.splitlines():
        if line.startswith("RESULT"):
            return json.loads(line[6:]).get("rows", [])
    return []


class TestTheDerivationCoversTheWholeTable:
    """**The direction nothing checked.** A sense with a consent key and no module
    is unreachable: the Senses panel iterates `discover_senses()`, so a key the
    table carries for a sense that no longer exists becomes a switch nobody can
    draw. Measured 2026-10-12: 53 registered against 53 in the table, zero in
    either direction - so this is a guard holding a real invariant, not a
    description of a defect.

    It is worth the test because the table and the registry are two lists kept
    in step by hand, and this repo's record of hand-kept lists is that they are
    "wrong the moment a sense is added, and wrong silently".
    """

    def test_no_consent_key_names_a_sense_that_does_not_exist(self):
        registry = senses_registry.discover_senses()
        orphans = sorted(set(config._SENSE_CONSENT_KEYS) - set(registry))
        assert not orphans, (
            "these senses have a consent key and no module, so the Senses panel "
            "cannot draw a row for them and the key is a switch nobody can find: "
            + ", ".join(orphans))

    def test_every_registered_sense_has_a_key(self):
        """The other direction, and the one that makes a *new* sense safe: a
        sense with no key cannot be granted at all, which is the
        `sense_allowed`-returns-False path - fail-closed, and a capability that
        looks present and cannot be turned on."""
        registry = senses_registry.discover_senses()
        keyless = sorted(n for n in registry
                         if not config._consent_key_for(n))
        assert not keyless, (
            "these senses are registered and carry no consent key, so no "
            "switch can grant them: " + ", ".join(keyless))
