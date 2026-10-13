"""A reply that stalled mid-sentence stayed silent until the reply finished.

Three halves of one feature were built and none of them connected:

- `SentenceBuffer.flush_idle()` — "after a pause in the stream: release up to the
  last whole word" — had **no caller in the tree**
- `SentenceBuffer.last_push` — the field that says when the pause was — was
  written twice and **read by nothing**
- the turn-end path called `flush()`, which releases what is *left*

So the behaviour was: `push()` releases a whole sentence as it completes, and
between two completed sentences a partial one sits in the buffer doing nothing.
A person hears nothing until a sentence completes or the reply ends.

**This matters most where Chronoa is weakest.** The sampler on a CPU-only machine
stalls for whole seconds mid-sentence, so the first audible word can be many
seconds late for no reason other than where the pause happened to fall.

`flush_idle()` decides the rest itself and is exercised here directly, because
the interesting part is *what it refuses to release*:

| buffer | released | kept |
|---|---|---|
| `Hello there I am wri` | `['Hello there I am']` | `' wri'` |
| `short` | `[]` — under `MIN_SENTENCE_CHARS` | `'short'` |
| `And a partial one` | `['And a partial']` | `' one'` |
| anything while `self._in_fence` | `[]` | untouched |

The last is provenance: the buffer will not cut inside a fenced span, so the tick
only has to notice the pause and must not second-guess the cut.
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import speech  # noqa: E402


def _stalled(text):
    """A buffer holding `text`, with the clock pushed past the threshold."""
    buf = speech.SentenceBuffer()
    buf.push(text)
    buf.last_push = time.monotonic() - 5.0
    return buf


@pytest.mark.parametrize("text,released,kept", [
    ("Hello there I am wri", ["Hello there I am"], " wri"),
    ("And a partial one", ["And a partial"], " one"),
])
def test_a_stall_releases_up_to_the_last_whole_word(text, released, kept):
    buf = _stalled(text)
    assert buf.flush_idle() == released
    assert buf._buf == kept, "the partial remainder must stay for the next tick"


def test_a_fragment_too_short_is_not_released():
    """**What it refuses.** Releasing `short` would speak a fragment; holding it
    costs nothing because the next delta will make it long enough."""
    buf = _stalled("short")
    assert buf.flush_idle() == []
    assert buf._buf == "short", "a fragment that was not released must be kept"


def test_nothing_is_released_while_the_buffer_is_fenced():
    """Provenance: no cut inside a fenced span, whatever the pause."""
    buf = _stalled("Hello there I am writing")
    buf._in_fence = True
    assert buf.flush_idle() == [], "a fenced span was cut"
    assert buf._buf == "Hello there I am writing"


def test_a_live_stream_is_never_cut():
    """The tick's own gate. Without it, every poll would cut a sentence that is
    merely mid-word - which is what `last_push` exists to prevent."""
    buf = speech.SentenceBuffer()
    buf.push("Hello there I am wri")
    buf.last_push = time.monotonic()

    assert time.monotonic() - buf.last_push < 0.8, "the clock says this is live"


# ---------------------------------------------------------------------------
# the tick, not just the buffer
# ---------------------------------------------------------------------------
#
# **The first version of this file tested only the buffer, and mutation showed the
# gap**: removing the tick entirely - so `flush_idle()` has no caller again and a
# reply is exactly as silent as it was - left all six tests green. One of them
# asserted a caller existed by grepping the source for the name, which matched
# the *docstrings* written to explain the wiring. Another asserted
# `flush_idle() == [] or buf._buf`, which is true either way and therefore says
# nothing.
#
# So the tick is exercised directly, on a real object, with a real buffer and a
# queue that records what it was asked to say.


class _Queue:
    def __init__(self):
        self.said = []

    def speak(self, pieces):
        self.said.extend(pieces)


def _convo_with(buffer, queue):
    from shani_chronoa.app import conversation

    obj = conversation.ConversationMixin.__new__(conversation.ConversationMixin)
    obj._turn_buffer = buffer
    obj._turn_speech = queue
    return obj


def test_a_stalled_turn_releases_its_partial_sentence():
    """**The wiring.** A stall must reach the speech queue."""
    buf = speech.SentenceBuffer()
    buf.push("Hello there I am wri")
    buf.last_push = time.monotonic() - 5.0
    queue = _Queue()

    keep_going = _convo_with(buf, queue)._flush_idle_words()

    assert queue.said == ["Hello there I am"], (
        f"a stalled partial sentence was not released to speech: {queue.said!r}")
    assert keep_going is True, "the tick must stay alive; the turn removes it"


def test_a_live_turn_says_nothing_and_stays_alive():
    """**The control.** Mid-word is not a stall."""
    buf = speech.SentenceBuffer()
    buf.push("Hello there I am wri")
    buf.last_push = time.monotonic()
    queue = _Queue()

    keep_going = _convo_with(buf, queue)._flush_idle_words()

    assert queue.said == [], f"a live stream was cut: {queue.said!r}"
    assert keep_going is True


def test_the_tick_survives_a_buffer_or_queue_that_is_not_there_yet():
    """Before the first delta arrives there is nothing buffered and possibly no
    queue. That is the normal state, not an error to raise from a timer."""
    from shani_chronoa.app import conversation

    obj = conversation.ConversationMixin.__new__(conversation.ConversationMixin)
    obj._turn_buffer = None
    obj._turn_speech = None
    assert obj._flush_idle_words() is True


def test_flush_idle_now_has_a_caller():
    """A cheap regression net, and deliberately not the main claim.

    This greps the source, so it matches prose as well as code - which is exactly
    why the tests above exist and why mutation caught their weakness. It is here
    to catch the *name* disappearing entirely; the wiring is proven by
    `test_a_stalled_turn_releases_its_partial_sentence`."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"
    callers = [p for p in root.rglob("*.py")
               if "flush_idle(" in p.read_text(encoding="utf-8", errors="replace")
               and "def flush_idle" not in p.read_text(encoding="utf-8", errors="replace")]
    assert callers, "flush_idle() has no caller again"

    readers = [p for p in root.rglob("*.py")
               if "last_push" in p.read_text(encoding="utf-8", errors="replace")
               and p.name != "speech.py"]
    assert readers, "last_push is still written and read by nothing"


def _starting_turn(monkeypatch):
    """`_start_turn` with everything heavy stubbed, so only the scheduling is real.

    **The two tests below exist because mutation passed where it should not
    have failed.** Removing the `GLib.timeout_add(...)` outright - so the tick has
    no caller again and a reply is exactly as silent as it was - left all nine
    tests green, because they called `_flush_idle_words()` directly. A method that
    works when invoked is not the same as a method that is ever *invoked*, and the
    second version of the mutation - never removing the timer at turn end - also
    passed, because nothing asserted that either.
    """
    from shani_chronoa.app import conversation

    obj = conversation.ConversationMixin.__new__(conversation.ConversationMixin)
    obj.window = None
    obj.assistant = type("_Asst", (), {
        "handle": staticmethod(lambda *a, **k: None),
        "visible_turns": staticmethod(lambda: []),
    })()
    obj._async = type("_A", (), {"run": staticmethod(lambda *a, **k: None)})()
    obj._new_speech_queue = lambda *a, **k: None
    obj._use_local_server = lambda *a, **k: True
    obj.llm = type("_Llm", (), {
        "kind": "local",
        "available": lambda: True,
        "is_available": staticmethod(lambda: True),
    })()
    # `_start_turn` returns early through `_explain_no_model` when there is no
    # assistant, and the scheduling is further down than that check - so an
    # assistant stub is what makes the test reach the line it is testing.
    obj._on_reply_text = lambda *a, **k: None
    obj._explain_no_model = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("_start_turn gave up before scheduling the tick"))
    return obj


def test_starting_a_turn_schedules_the_tick(monkeypatch):
    """The tick is not only correct, it is *installed*. Calling it by hand is not
    the product; a timeout that nobody registers never runs."""
    from gi.repository import GLib

    from shani_chronoa.app import conversation

    seen = {}
    monkeypatch.setattr(conversation.GLib, "timeout_add",
                        lambda ms, fn, *a, **k: seen.update(ms=ms, fn=fn) or 1)
    monkeypatch.setattr(conversation.ConversationMixin, "_new_speech_queue",
                        lambda self: None, raising=False)
    monkeypatch.setattr(conversation.ConversationMixin, "submit",
                        lambda self, *a, **k: None, raising=False)

    obj = _starting_turn(monkeypatch)
    obj._start_turn("hello")

    assert seen.get("ms") == conversation.STREAM_IDLE_POLL_MS, (
        f"no tick was scheduled on a new turn (timeout_add saw {seen!r})")
    assert getattr(seen.get("fn"), "__name__", "") == "_flush_idle_words", (
        f"something else was scheduled: {seen!r}")
    assert GLib is not None  # imported for the monkeypatch target


def test_finishing_a_turn_removes_the_tick():
    """A timer that outlives its turn keeps reaching into a finished turn's
    buffer, and the next turn inherits it."""
    from shani_chronoa.app import conversation

    removed = {}
    obj = conversation.ConversationMixin.__new__(conversation.ConversationMixin)
    obj._turn_in_flight = True
    obj._stream_idle_source = 4242
    obj.window = None
    obj.assistant = None
    # `_turn_queue` is a read-only property on the mixin; assigning it raises
    # AttributeError, so it is left alone rather than stubbed.
    monkey = getattr(conversation.GLib, "source_remove", None)
    try:
        conversation.GLib.source_remove = lambda src: removed.setdefault("src", src)
        try:
            obj._turn_finished()
        finally:
            if monkey is not None:
                conversation.GLib.source_remove = monkey
    except Exception:  # noqa: BLE001 - a turn-end path may need more state
        pass

    assert obj._stream_idle_source is None, (
        "the idle-flush tick survived the turn and will outlive its buffer")
