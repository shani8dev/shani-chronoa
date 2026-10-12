"""The mode strip checked against the engine that is actually answering.

**The strip answers "what is it allowed to be"; the Models panel answers "what
is answering".** That split is deliberate. But since `_apply_answer_mode` began
*moving* the engine, the two can disagree, and before anything moved they could
not — so nothing was watching for it.

The direction that matters is the privacy one: a person who chose **Nothing
leaves**, with a cloud provider answering, and no line anywhere saying so.

These tests drive `_note_engine_disagreement` on a stand-in that answers
"what is answering", because that is the question the method asks. The real
engine is `BrainMixin.current_llm_kind`, already covered in
`tests/test_answer_mode.py`; substituting it here keeps this file about the
check rather than about which object is a cloud.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")

from shani_chronoa.gui import window as window_module  # noqa: E402


class _Config:
    def __init__(self, privacy=False, mode="local"):
        self.privacy_mode = privacy
        self.answer_mode = mode


def _strip(*, privacy=False, mode="local", engine="local", problem=""):
    app = SimpleNamespace(
        _answer_mode_problem=problem,
        current_llm_kind=lambda: engine)
    strip = window_module._ModeStrip(app, _Config(privacy, mode))
    return strip


def _note(strip):
    window_module._ModeStrip._note_engine_disagreement(strip)


def _marked(strip):
    return strip._answer_chip.has_css_class("mode-disagrees")


# -- the privacy direction, which is the one that matters ------------------


def test_nothing_leaves_with_a_cloud_answering_is_flagged():
    """**The case this exists for.** The chosen mode says nothing leaves this
    machine; a provider is answering; and nothing on screen says so."""
    strip = _strip(privacy=True, mode="local", engine="cloud")
    _note(strip)
    assert _marked(strip), "a cloud engine under 'Nothing leaves' is not flagged"
    tooltip = strip._answer_chip.get_tooltip_text()
    assert "cloud" in tooltip, tooltip
    assert "Models" in tooltip, tooltip


def test_this_machine_answers_with_a_cloud_answering_is_flagged():
    strip = _strip(privacy=False, mode="local", engine="cloud")
    _note(strip)
    assert _marked(strip)


def test_the_warning_names_what_to_do_about_it():
    """A warning that says only "wrong" makes the reader go and find out, and
    the way to find out is the Models panel — so it is named."""
    strip = _strip(privacy=True, mode="local", engine="cloud")
    _note(strip)
    assert "switch back" in strip._answer_chip.get_tooltip_text().lower(), \
        strip._answer_chip.get_tooltip_text()


# -- the other direction ---------------------------------------------------


def test_cloud_chosen_but_this_machine_answering_is_flagged():
    strip = _strip(privacy=False, mode="cloud", engine="local")
    _note(strip)
    assert _marked(strip)


def test_the_cloud_refusal_is_named_rather_than_guessed():
    """The engine already produced a sentence saying why. A second guess
    beside it is a second answer to "what is wrong"."""
    strip = _strip(mode="cloud", engine="local",
                   problem="no cloud provider is reachable: the free gateways "
                           "are rate-limited")
    _note(strip)
    tooltip = strip._answer_chip.get_tooltip_text()
    assert "rate-limited" in tooltip, tooltip


def test_a_cloud_refusal_with_no_recorded_reason_still_flags():
    """The engine may have moved before the app started watching, so there may
    be no reason on record. Silence about *why* would be worse than nothing."""
    strip = _strip(mode="cloud", engine="local", problem="")
    _note(strip)
    assert _marked(strip)
    assert strip._answer_chip.get_tooltip_text()


# -- agreeing, which is the common case and must stay quiet ----------------


def test_local_chosen_and_local_answering_is_not_flagged():
    strip = _strip(privacy=False, mode="local", engine="local")
    _note(strip)
    assert not _marked(strip)


def test_nothing_leaves_and_local_answering_is_not_flagged():
    strip = _strip(privacy=True, mode="local", engine="local")
    _note(strip)
    assert not _marked(strip)


def test_local_then_cloud_chosen_and_local_answering_is_not_flagged():
    """"Answer here when you can, reach a provider when you cannot" - answering
    here is the point of the choice, so a local engine is in force."""
    strip = _strip(privacy=False, mode="local-cloud", engine="local")
    _note(strip)
    assert not _marked(strip)


def test_local_then_cloud_with_a_cloud_answering_is_not_flagged():
    """"Answer here when you can, reach a provider when you cannot" - a
    provider answering is precisely what that mode is for. Flagging it would
    train the reader to ignore the mark, which is the cost of crying wolf."""
    strip = _strip(privacy=False, mode="local-cloud", engine="cloud")
    _note(strip)
    assert not _marked(strip)


def test_going_back_to_agreeing_clears_the_mark():
    """The mark is a state, not a latch: a refusal that later succeeds must
    stop being reported, or the strip warns forever."""
    strip = _strip(mode="cloud", engine="local", problem="no provider")
    _note(strip)
    assert _marked(strip)
    strip._app.current_llm_kind = lambda: "cloud"
    _note(strip)
    assert not _marked(strip), "the mark survived the engine catching up"


# -- when there is nothing to check against --------------------------------


def test_an_app_that_cannot_name_the_engine_says_nothing():
    """Before anything moved there was no such reader, and that must not raise
    from `refresh()` — which runs from `__init__`."""
    app = SimpleNamespace()
    strip = window_module._ModeStrip(app, _Config())
    _note(strip)                      # must not raise
    assert not _marked(strip)


def test_a_reader_that_raises_is_not_fatal():
    app = SimpleNamespace(
        _answer_mode_problem="",
        current_llm_kind=lambda: (_ for _ in ()).throw(RuntimeError("no llm")))
    strip = window_module._ModeStrip(app, _Config())
    _note(strip)                      # must not raise
    assert not _marked(strip)


def test_an_engine_named_neither_local_nor_cloud_is_ignored():
    """`current_llm_kind` answers "none" before anything is loaded. Comparing
    "none" against "local" would warn about a machine that has not started."""
    for name in ("none", "", None):
        strip = _strip(mode="cloud", engine=name)
        _note(strip)
        assert not _marked(strip), f"flagged for an engine named {name!r}"


# -- the stylesheet -------------------------------------------------------


def test_the_disagreement_has_a_visible_rule():
    """A warning that exists only in a tooltip is a warning nobody sees. The
    tooltip carries the sentence; the rule makes it findable."""
    css = Path(window_module.__file__).with_name("style.py").read_text()
    assert ".mode-disagrees" in css, "the class is set but never styled"