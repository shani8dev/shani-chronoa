"""The orb tells dictation from a question.

**Dictation ran for five minutes instead of twenty seconds** and ended on a
longer silence, and the orb showed the same green microphone either way - so
a person glancing at it could not tell which kind of listening was open.
The *state line* said "Dictating…" (the window reached into the app's own
`_dictating` flag to say it), and the orb did not.

So dictation is a state of its own: its own colour, **its own icon** - a
microphone with waves rather than a plain microphone, because the icon is the
signal that survives greyscale and colour blindness - and its own word. The
window no longer reaches into another object to answer a question about
itself.
"""

from __future__ import annotations

import inspect
import sys

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

sys.path.insert(0, "usr/lib/shani-chronoa")
from shani_chronoa.app.voice import VoiceMixin  # noqa: E402
from shani_chronoa.gui import AssistantState, widgets  # noqa: E402
from shani_chronoa.gui import style as style_module  # noqa: E402


class _Stub:
    def __init__(self, dictating):
        self._dictating = dictating


class TestTheStateExists:
    def test_there_is_a_dictating_state(self):
        assert AssistantState.DICTATING.value == "dictating"

    def test_it_has_its_own_colour(self):
        """**No two states may share a colour** - the invariant the
        repository already holds for the other six."""
        colours = [c for c, _ in widgets._STATE_STYLE.values()]
        assert len(set(colours)) == len(AssistantState), (
            f"two states share a colour: {widgets._STATE_STYLE}")

    def test_it_has_its_own_icon(self):
        """**The icon is the signal that survives greyscale**, so a
        dictating orb that looks like a listening one has told the reader
        nothing."""
        # **Idle and listening share the microphone icon by design** - both
        # involve the mic - so the comparison is with *listening*, the state
        # dictation used to be indistinguishable from.
        assert (widgets._STATE_STYLE[AssistantState.DICTATING][1]
                != widgets._STATE_STYLE[AssistantState.LISTENING][1]), (
            "the dictating state shares the listening icon")

    def test_it_says_its_own_word(self):
        assert widgets._STATE_LABELS[AssistantState.DICTATING] == "Dictating…"

    def test_it_paints_itself(self):
        """**A state without a CSS rule is a state that renders
        unstyled** whatever the table says."""
        css = inspect.getsource(style_module)
        assert ".chronoa-orb.state-dictating" in css

    def test_it_can_stop_animating(self):
        """**A pulsing orb is the point of some states**, and this one
        pulses - so it needs its own reduced-motion rule, or the animation
        ignores the accessibility setting."""
        css = inspect.getsource(style_module)
        assert ".reduce-motion .chronoa-orb.state-dictating" in css

    def test_the_level_can_reach_it(self):
        """**The halo pulses with the microphone's level**, and a dictation
        is a microphone the person is talking into."""
        assert AssistantState.DICTATING in widgets._LIVE_STATES


class TestTheVoicePathOpensIt:
    def test_dictating_opens_the_dictating_state(self):
        """**The state the orb shows while the microphone is open** is
        decided in one place, so the dictation path and the question path
        cannot disagree."""
        assert (VoiceMixin._listening_state(_Stub(True))
                is AssistantState.DICTATING)
        assert (VoiceMixin._listening_state(_Stub(False))
                is AssistantState.LISTENING)

    def test_the_window_does_not_reach_into_the_app_to_ask(self):
        """**The window used to read the app's own `_dictating` flag** -
        a private attribute of another object, read from a window, to answer
        a question the state machine could have answered itself.

        The state carries the word now, so the reach is gone.
        """
        import ast
        from shani_chronoa.gui import window as window_module
        source = inspect.getsource(window_module)
        assert "_is_dictating" not in source, (
            "the window still asks the app whether it is dictating")
        # **By the tree, not by the spelling.** A source-text check for
        # `"_app._dictating"` is satisfied by `getattr(self._app, "_dictating",
        # False)` - which is the same reach written differently - so the check
        # walks the AST for the name itself, in an attribute or in a string.
        tree = ast.parse(source)
        reached = [node for node in ast.walk(tree)
                   if (isinstance(node, ast.Attribute)
                       and node.attr == "_dictating")
                   or (isinstance(node, ast.Constant)
                       and node.value == "_dictating")]
        assert not reached, (
            "the window still reaches into the app for its private dictation "
            f"flag: {reached}")
