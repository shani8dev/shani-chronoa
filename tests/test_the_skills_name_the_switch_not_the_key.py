"""A refusal names the switch, not its key.

**A key is not a word any screen shows.** "Enable
'accessibility-sense-enabled'" sends the person looking for a switch
called *Screen text (accessibility)* - which is what Settings calls it,
and what every other refusal in this app now says.

The shared helpers are the ones that know the wording:
`config.sense_allowed_reason()` for a sense, and
`capabilities.gate_label()` for a tool's consent key. Both name the
**row**, so the refusal and the Settings row agree.

The four sites below are the person-facing ones. The *schema
descriptions* also mention keys, and that is right: they are read by the
model, which needs the exact spelling.
"""

from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")

from shani_chronoa import capabilities  # noqa: E402
from shani_chronoa.config import ChronoaConfig  # noqa: E402
from shani_chronoa.skills import list_windows, login_history, ui_elements  # noqa: E402


def _assert_names_the_switch(said: str, key: str) -> None:
    """The refusal names the switch as Settings spells it, and not the key."""
    assert key not in said, (
        f"the refusal names the raw key: {said!r}")
    label = capabilities.gate_label(key)
    assert label in said, (
        f"the refusal does not name the switch {label!r}: {said!r}")


class TestTheSenses:
    def test_login_history_names_its_switch(self):
        """**Reading other people's presence** is the switch, and it is
        called "Who is on this machine"."""
        allowed, why = login_history._consent(ChronoaConfig())
        assert not allowed, "the login history switch was granted on this box"
        _assert_names_the_switch(why, "sessions-sense-enabled")

    def test_list_windows_names_its_switch(self):
        """**The accessibility bus** is the route, and the switch is called
        "Screen text (accessibility)"."""
        said = list_windows._via_accessibility()
        _assert_names_the_switch(said, "accessibility-sense-enabled")


class TestTheTools:
    def test_ui_elements_names_its_switch(self):
        """**Acting on another app's controls** is the switch, and it is
        called "Let Chronoa act"."""
        # **The switch is granted on this machine** - it defaults on - so the
        # refusal is produced by a config that says otherwise. A test that
        # only works when the machine happens to have the switch off is a
        # test that cannot run.
        class _Off:
            input_control_enabled = False

        allowed, why = ui_elements._consent(_Off())
        assert not allowed, "the stub config said the switch was on"
        _assert_names_the_switch(why, "input-control-enabled")

    def test_the_sense_helper_itself_names_the_row(self):
        """**The helper is the shared answer**, so the refusal and the
        Settings row cannot disagree about what the switch is called."""
        reason = ChronoaConfig().sense_allowed_reason("sessions")
        assert "sessions-sense-enabled" not in reason, reason
        assert "Who is on this machine" in reason, reason
