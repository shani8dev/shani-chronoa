"""`EventSink` refuses a field that is not a callable, at construction.

`harness-study/laya-decision`'s `laya/hooks.py` states the rule:

> a plain callable is only accepted on the single-event `on_predict_start=`
> parameters, because `hooks=` reads for method names and a bare function has
> none.

An API that introspects must refuse the shape it cannot introspect rather than
accept it and never call it. Chronoa learned the same lesson in
`skills/__init__.py`, where `run=lambda a: ...` passed `callable()` and then
failed on **every** call in a subprocess - the fix there was at the loader, not at
the caller.

Before this, `EventSink(on_text="not a function")` constructed cleanly and the
TypeError arrived at the call site - mid-turn, after the model had run and the
tool had been called.

Every construction site in the tree passes a real callable, so this is defence at
the seam and not a behaviour change: `app/conversation.py`, `events.fan_in` and
the streaming tests all pass functions or bound methods.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "usr" / "lib"))

from shani_chronoa.events import EventSink  # noqa: E402

_FIELDS = ("on_tool_start", "on_tool_finish", "on_text", "on_state",
           "on_compaction", "on_cloud_turn")


class TestAFieldThatIsNotACallableIsRefused:
    @pytest.mark.parametrize("value", ["not a function", 42, 0, [1], {"a": 1},
                                       b"bytes", object()])
    def test_a_non_callable_is_refused_for_every_field(self, value):
        """Every field, because one built defensively and five left open is the
        shape that fails on the field nobody checked."""
        for field in _FIELDS:
            with pytest.raises(TypeError):
                EventSink(**{field: value})

    def test_the_message_names_the_field_and_the_type(self):
        """A refusal that names neither is a refusal that cannot be acted on - the
        `arith`-style trap of being told something is wrong without being told
        what."""
        with pytest.raises(TypeError) as refusal:
            EventSink(on_text="oops")
        said = str(refusal.value)
        assert "on_text" in said, said
        assert "str" in said, said
        # **And why the refusal is here at all.** A message that names the field
        # and the type and nothing else says what is wrong without saying why it
        # is being caught at construction rather than at the call site - and that
        # is the half that makes the refusal actionable, because the reader's next
        # question is "why now". The mutation that drops it survived this test
        # until this assertion existed.
        assert "before the turn runs" in said, (
            f"the refusal does not say why it is raised at construction: {said!r}")

    def test_a_false_bool_is_not_a_callable(self):
        """`callable(False)` is False, but the field's truthiness is what a naive
        guard would test - and 0 and "" are falsy non-callables."""
        for field in _FIELDS:
            with pytest.raises(TypeError):
                EventSink(**{field: False})

    def test_a_zero_is_not_a_callable_and_not_none(self):
        """0 is the "absent" value several readers in this tree accept, so it must
        not slip through as None."""
        with pytest.raises(TypeError):
            EventSink(on_compaction=0)


class TestAValidSinkIsUnchanged:
    def test_an_empty_sink_builds(self):
        assert EventSink() is not None

    def test_a_real_callable_builds(self):
        seen = []
        assert EventSink(on_text=seen.append) is not None

    def test_a_bound_method_and_a_lambda_build(self):
        class _S:
            def note(self, delta):
                return delta

        holder = _S()
        assert EventSink(on_text=holder.note) is not None
        assert EventSink(on_state=lambda st: None) is not None

    def test_a_callable_object_builds(self):
        """Not every sink is a function: an object with `__call__` is what a GUI
        sink in another process hands over."""
        class _Callable:
            def __call__(self, *args):
                return args

        assert EventSink(on_tool_start=_Callable()) is not None

    def test_fan_in_still_builds_its_forwarding_sink(self):
        from shani_chronoa import events

        combined = events.fan_in(EventSink(), None)
        assert combined.on_text is not None

    def test_a_sink_with_no_fields_at_all_fires_nothing(self):
        """The "any subset" property this class's own docstring states, kept
        working by the guard."""
        seen = []
        sink = EventSink()
        sink.fire("on_text", "hi")
        assert seen == []
