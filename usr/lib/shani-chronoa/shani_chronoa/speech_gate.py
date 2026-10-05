"""Do not run speech recognition while the model is still thinking.

`assistd` wraps its transcriber in a `QueuedTranscriber` whose first question is
not "what did I hear" but **"is the GPU busy"**: if an LLM stream is in flight, or
another process holds the video memory, or the daemon is not in its `Active`
presence state, it either waits for the stream to drain or falls back to the CPU
- and it logs which of those happened, every time.

That is worth having here for a concrete reason rather than a general one:
**whisper.cpp and llama.cpp share this machine.** A transcript that starts while
the model is still generating competes with it for the same accelerator, so both
get slower and the transcript is the one whose lateness the person notices -
they said the words and then waited.

So the gate is here, and it is deliberately small: a busy probe the caller
supplies, a bounded wait, and no CPU fallback. A CPU fallback would mean a second
whisper model resident at once, which costs memory to avoid a wait; the wait is
cheaper and this is the only caller that wanted it.

**Deferral is never silent and never unbounded.** `wait()` returns whether it
waited and why it stopped, and a caller that does not want to wait gets `False`
immediately rather than a number that means "after at most 4 seconds". The reason
string is the point: "the model is generating" and "there is no model at all" are
different situations and a log that cannot tell them apart cannot be acted on.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional, Tuple

logger = logging.getLogger(__name__)

#: How long to wait for the model, by default. A turn on a small local model:
#: runs seconds; four is long enough for most of those and short enough that the
#: microphone does not feel broken.
DEFAULT_TIMEOUT = 4.0

#: How often to look. The probe is a boolean read in this design, so this only
#: needs to be fast enough that the wait ends promptly.
POLL = 0.05


class Busy(Exception):
    """Raised when transcription should not start yet.

    A distinct exception rather than a `False` return, so a caller that forgets
    to handle it fails loudly at the first turn instead of silently transcribing
    whenever it felt like it.
    """


def wait(busy: Optional[Callable[[], bool]], timeout: float = DEFAULT_TIMEOUT,
         poll: float = POLL, now: Callable[[], float] = time.monotonic,
         sleep: Callable[[float], None] = time.sleep) -> Tuple[bool, str]:
    """Block until `busy()` is False. Returns `(waited, reason)`.

    `now` and `sleep` are parameters so a test can drive the clock rather than
    really waiting four seconds - which is the difference between a test that
    proves the timeout and a test that measures how long four seconds is.
    """
    if busy is None:
        return False, "no busy probe: transcribing now"
    if not busy():
        return False, "the model was idle"
    deadline = now() + max(timeout, 0.0)
    # `waited` means *it actually slept*, not "it was busy on the first look".
    # Those differ at a zero timeout, which is the interesting case: assistd's
    # `gpu_busy_timeout_ms = 0` means "ask, and do not queue behind a stream that
    # may never end", and reporting that as a wait would put a deferral count on
    # something that never happened.
    slept = False
    while now() < deadline:
        sleep(poll)
        slept = True
        if not busy():
            return True, "waited for the model to finish generating"
    if busy():
        return slept, (f"still generating after {timeout:g}s, so transcribing "
                       "anyway rather than losing what was said")
    return slept, "the model finished as the timeout expired"


def window_busy(window) -> Optional[Callable[[], bool]]:
    """A probe for the main window's assistant state, or None without one.

    Read through the window rather than a flag of our own because the window owns
    the state and already broadcasts it: `AssistantState.THINKING`, `SPEAKING` and
    `INTERRUPTING` are all states where the machine is occupied, and a separate
    flag would be one more thing that can disagree with the orb.
    """
    if window is None:
        return None
    from shani_chronoa.gui.widgets import AssistantState

    busy_states = (AssistantState.THINKING, AssistantState.SPEAKING,
                   AssistantState.INTERRUPTING)

    def busy() -> bool:
        try:
            return window.get_state() in busy_states
        except Exception:                        # noqa: BLE001
            # A probe that cannot answer must not raise into the audio path: an
            # exception here would lose the transcript entirely, and "I could not
            # tell" is not a reason to drop what somebody said.
            return False

    return busy


class Gate:
    """The probe, plus the last reason - so a log line can explain the delay.

    Kept as an object rather than a bare callable because "why did that take two
    seconds" is the question a person asks when speech feels slow, and the answer
    has to have been recorded at the time.
    """

    def __init__(self, busy: Optional[Callable[[], bool]] = None,
                 timeout: float = DEFAULT_TIMEOUT) -> None:
        self._busy = busy
        self.timeout = timeout
        self.last_reason = ""
        self.deferred = 0

    def __call__(self) -> bool:
        if self._busy is None:
            return False
        try:
            return bool(self._busy())
        except Exception as exc:                 # noqa: BLE001
            self.last_reason = f"the busy probe failed ({type(exc).__name__}), so not waiting"
            logger.debug("busy probe failed: %s", exc, exc_info=True)
            return False

    def run(self) -> bool:
        """Wait if needed. Returns True when it actually waited."""
        waited, reason = wait(self, self.timeout)
        if waited:
            self.deferred += 1
        self.last_reason = reason
        logger.info("transcription gate: %s", reason)
        return waited