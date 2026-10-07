"""A switch left off by choice is "Turned off", not "Needs attention".

The health vocabulary had three words - ok, attention, unknown - so every panel
whose consent key was shut said "Needs attention" in red. Rendered on a machine
with default settings, five of the sidebar's rows were red, and the Devices panel
printed "This is a setting on this machine, not a fault." directly under its own
red "Needs attention". Red that is always on stops meaning anything, which costs
the rows where it is true. These pin the decisions per panel, and keep the real
faults red.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw  # noqa: E402

Adw.init()

from shani_chronoa.gui.surfaces import common, devices, machine, senses  # noqa: E402


def test_off_is_a_word_with_its_own_colour():
    assert common.STATUS_WORDS[common.STATUS_OFF] == "Turned off"
    assert common.STATUS_CLASSES[common.STATUS_OFF] == "status-off"
    assert common.STATUS_OFF != common.STATUS_ATTENTION


def test_the_stylesheet_colours_the_off_dot():
    """A fourth word with no CSS rule is the uncoloured dot the vocabulary guards against."""
    from shani_chronoa.gui import style
    sheet = Path(style.__file__).read_text()
    assert ".status-dot.status-off" in sheet
    assert ".status-row.status-off .status-dot" in sheet


def test_a_phone_gate_left_off_is_off_not_attention():
    assert devices.HEALTH[devices.STATE_REFUSED] == common.STATUS_OFF


def _status_class(widget):
    classes = widget.get_css_classes()
    return next(c for c in common.STATUS_CLASSES.values() if c in classes)


def test_senses_some_granted_is_ready_none_granted_is_off():
    assert _status_class(senses._summary(29, 49)) == "status-ok"
    assert _status_class(senses._summary(0, 49)) == "status-off"
    assert _status_class(senses._summary(49, 49)) == "status-ok"


def _reading(state):
    return machine.Reading(name="x", state=state)


def test_machine_senses_off_on_purpose_are_not_a_fault():
    recorder = common.StatusRecorder()
    machine._summary_label(recorder, [
        _reading(machine.STATE_READING), _reading(machine.STATE_REFUSED),
        _reading(machine.STATE_ABSENT)])
    assert recorder.status() == common.STATUS_OK


def test_machine_a_sense_that_failed_is_still_attention():
    """Control: the reclassification must not swallow real failures."""
    recorder = common.StatusRecorder()
    machine._summary_label(recorder, [
        _reading(machine.STATE_READING), _reading(machine.STATE_REFUSED),
        _reading(machine.STATE_FAILED)])
    assert recorder.status() == common.STATUS_ATTENTION

