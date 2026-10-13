"""Two messages typed while one turn is running must not become two turns.

The defect, in one line of `_submit`:

    self._turn_future = self._async.run(self.assistant.handle(...), ...)

There is no lock anywhere on that path - not in `_submit`, not in
`assistant.handle`, and the composer is never disabled - so a second send
started a second `handle()` against the same assistant while the first was still
going. Measured with a stand-in `Assistant`: **peak 2 concurrent `handle()`
calls**, both turns appending to the same `_history`.

The visible damage was worse than the history, because every one of
`_turn_marks`, `_turn_speech`, `_turn_buffer`, `_turn_streamed` and
`_turn_future` is reassigned by the second turn:

- the running turn's streamed text was written into the **second** turn's
  speech queue, and its reply landed inside the **second** turn's bubble - two
  answers interleaved into one;
- Stop reads `_turn_future`, so it stopped whichever turn was submitted
  **last**, which is the one that had not started;
- the first turn's elapsed clock restarted, so the wait grew by nothing while
  the reading under it went backwards.

Queuing is the fix rather than refusing, because `window._submit_input` adds the
user turn to the transcript *before* it emits `user-input` - a refusal would
leave a question on screen that Chronoa never answers.

**These tests drive the real `_submit` and the real `_on_response_ready`.** A
test that read the source for `queue.append` would pass on code that never
drains, and the drain is the half that matters: a queue nobody drains is a
transcript full of questions that are never answered, which is the exact failure
this file exists to rule out.
"""

import asyncio
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa.app import conversation as conversation_module  # noqa: E402
from shani_chronoa.app import voice as voice_module  # noqa: E402

TURN_SECONDS = 0.12


class _Assistant:
    """Records how many `handle()` calls overlap and the order they finish in."""

    def __init__(self):
        self.live = 0
        self.peak = 0
        self.order = []
        #: Text this turn must fail on, or None. **Keyed on the message rather
        #: than a flag switched off by the test**, because a flag set after the
        #: second `_submit` is a race with a thread the test does not own: the
        #: first version flipped `fail = False` immediately and the "failing"
        #: turn usually won the race and answered normally - which is why the
        #: first run reported the failing text *in* `order`.
        self.fail_on = None

    async def handle(self, text, sink=None, channel=""):
        self.live += 1
        self.peak = max(self.peak, self.live)
        try:
            await asyncio.sleep(TURN_SECONDS)
            if self.fail_on is not None and text == self.fail_on:
                raise RuntimeError("the model went away")
            self.order.append(text)
            return f"answer to {text}"
        finally:
            self.live -= 1


class _Async:
    """Runs the coroutine on a thread and calls back like `AsyncBridge` does.

    **Cancellation is modelled honestly**, because it is the whole reason the
    stop path has its own line: the real bridge drops a `CancelledError` without
    ever invoking the callback. A stub that always called back would make the
    in-flight flag clear itself and the test would pass on code that strands
    every queued message forever.
    """

    def run(self, coro, callback):
        loop = asyncio.new_event_loop()
        box = {}

        def go():
            try:
                box["result"] = loop.run_until_complete(coro)
            except asyncio.CancelledError:
                box["result"] = "cancelled"
            except BaseException as exc:  # noqa: BLE001 - forwarded, like the bridge
                box["result"] = exc
            if box.get("result") != "cancelled":
                callback(box["result"])

        threading.Thread(target=go, daemon=True).start()

        class _Future:
            def done(self):
                return "result" in box

            def cancel(self):
                box["result"] = "cancelled"
                loop.call_soon_threadsafe(lambda: None)
                coro.close()
                return True

        return _Future()


class _Window:
    def __init__(self):
        self.statuses = []
        self.responses = []
        self.notices = []
        self.orb = []

    def set_status(self, text):
        self.statuses.append(text)

    def set_response(self, text):
        self.responses.append(text)

    def add_notice_row(self, sentence, kind="compaction"):
        self.notices.append((sentence, kind))

    def set_orb_state(self, state):
        self.orb.append(state)

    def set_state(self, _state):
        pass

    def get_state(self):
        return None


def _mixin(fail_on=None):
    assistant = _Assistant()
    assistant.fail_on = fail_on
    mixin = conversation_module.ConversationMixin.__new__(
        conversation_module.ConversationMixin)
    mixin.assistant = assistant
    mixin.llm = type("_L", (), {"is_available": lambda self: True})()
    mixin.window = _Window()
    mixin._async = _Async()
    mixin.event_sink = lambda: None
    mixin._use_local_server = lambda: False
    # A stopped queue, so `_on_response_ready` takes the non-streaming path and
    # does not need a speech backend to verify the drain.
    mixin._new_speech_queue = lambda: type(
        "_Q", (), {"stopped": True, "speak": lambda self, *a: None,
                   "close": lambda self: None})()
    for name in ("_mark", "_log_latency", "_follow_active_conversation",
                 "_maybe_title", "_capture_memory_from_turn"):
        setattr(mixin, name, lambda *a, **k: None)
    return mixin, assistant


def _settle(seconds=1.2):
    """Long enough for three 0.12s turns plus thread scheduling."""
    time.sleep(seconds)


class TestTwoMessagesAreNeverTwoTurns:

    def test_a_message_typed_mid_turn_does_not_overlap_it(self):
        mixin, assistant = _mixin()
        mixin._submit("first")
        mixin._submit("typed while the first was running")
        _settle()
        assert assistant.peak == 1, (
            f"peak {assistant.peak} concurrent handle() calls on one Assistant "
            f"- two turns shared the history, the speech queue and the elapsed "
            f"clock, and the reply you saw was whichever turn wrote last")

    def test_and_both_are_answered_in_the_order_they_were_typed(self):
        mixin, assistant = _mixin()
        mixin._submit("first")
        mixin._submit("second")
        mixin._submit("third")
        _settle()
        assert assistant.order == ["first", "second", "third"], (
            f"answered in the order {assistant.order} - the transcript shows "
            f"them in the order they were typed, so the two disagree about what "
            f"was asked")

    def test_the_first_message_still_starts_at_once(self):
        """**The control.** A queue that also holds an idle turn is a queue that
        delays every reply behind the next one typed, which is a worse bug than
        the one being fixed."""
        mixin, assistant = _mixin()
        mixin._submit("only message")
        time.sleep(0.04)
        assert assistant.live == 1, (
            "nothing was running a moment after the only message was sent")

    def test_a_waiting_message_says_so_rather_than_looking_ignored(self):
        """**The claim the screen has to back.** The message is already in the
        transcript, so with nothing else a second question reads as ignored."""
        mixin, _ = _mixin()
        mixin._submit("first")
        mixin._submit("second")
        waiting = [s for s in mixin.window.statuses if "waiting" in s]
        assert waiting, (
            f"no status said a message was waiting; statuses were "
            f"{mixin.window.statuses}")

    def test_a_plain_first_turn_reports_no_waiting_message(self):
        """**The control for the status above**, and the reason the count is in
        it: a status that always says the same thing teaches nothing."""
        mixin, _ = _mixin()
        mixin._submit("only message")
        assert not [s for s in mixin.window.statuses if "waiting" in s], (
            "a turn with nothing queued behind it claimed messages were waiting")


class TestTheQueueDrainsOnBothExits:

    def test_a_failed_turn_still_drains_what_was_behind_it(self):
        """**The exit that returns early.** `_on_response_ready` returns as soon
        as it has drawn the failure, so a queue drained only at the bottom would
        strand every message behind the one that failed - and it would strand
        them at the exact moment somebody is watching hardest, where a dead
        queue reads as the app hanging."""
        mixin, assistant = _mixin(fail_on="this one fails")
        mixin._submit("this one fails")
        mixin._submit("typed behind it")
        _settle()
        assert assistant.order == ["typed behind it"], (
            f"answered {assistant.order} - the message behind a failed turn was "
            f"never answered, though its question is on screen")
        assert not mixin.window.notices or mixin.window.notices[0][1] == "error", (
            f"the failure was not drawn as a failure: {mixin.window.notices}")

    def test_an_idle_app_keeps_no_queue_attribute_worth_fighting(self):
        """**Why these are properties.** This is a `Mixin` mixed into
        `Application` and nothing runs an initialiser, so a plain attribute
        would raise on the very first message typed in a fresh process."""
        mixin, _ = _mixin()
        assert mixin._turn_in_flight is False
        assert mixin._turn_queue == []


class TestStopReleasesTheMessagesBehindIt:

    @staticmethod
    def _stop(mixin):
        """Drive the real Stop handler, against the object the turn runs on.

        **Both mixins live on one `Application` in production**, so Stop's
        `self._turn_in_flight = False` has to land on the same object `_submit`
        set it on. The first version built a *second* object for the voice
        mixin and called the handler there, so the flag was cleared on an object
        the conversation never looked at - and the test reported the very
        failure it was written to catch, as though the fix were absent. Two
        mixins that are one object in production are two objects in a test
        unless the test says otherwise.
        """
        mixin.player = type("_P", (), {"stop": lambda self: None})()
        mixin._silence_reply = lambda: None
        mixin._listening = False
        voice_module.VoiceMixin._stop_speaking(mixin, None, None)

    def test_stopping_does_not_strand_the_messages_behind_it(self):
        """**`CancelledError` never calls back.** The real `AsyncBridge` drops a
        cancelled coroutine *without invoking its callback*, so the one line
        that clears the in-flight flag - `_on_response_ready` - never runs for a
        stopped turn. Nothing else clears it, so every message typed after a
        Stop is queued behind a turn that no longer exists, and the queue never
        drains again for the life of the process."""
        mixin, assistant = _mixin()
        mixin._submit("first")
        mixin._submit("typed behind it")
        self._stop(mixin)
        assert not mixin._turn_in_flight, (
            "a stopped turn still claims to be running, so nothing typed after "
            "it will ever start")
        mixin._submit("typed after the stop")
        _settle()
        assert assistant.peak == 1, f"peak {assistant.peak} - Stop did not help"
        assert assistant.order == ["typed behind it", "typed after the stop"], (
            f"answered {assistant.order} - the messages behind a stopped turn "
            f"came back out of order, and the transcript shows them in order")

    def test_stopping_twice_still_leaves_the_order_intact(self):
        mixin, assistant = _mixin()
        mixin._submit("first")
        mixin._submit("second")
        mixin._submit("third")
        self._stop(mixin)
        self._stop(mixin)
        mixin._submit("fourth")
        _settle()
        assert assistant.order[:2] == ["second", "third"], (
            f"answered {assistant.order} - two stops reordered the queue")