"""`speech_gate.Busy` documented a contract the module did not have.

    class Busy(Exception):
        '''Raised when transcription should not start yet.

        A distinct exception rather than a `False` return, so a caller that
        forgets to handle it fails loudly at the first turn instead of silently
        transcribing whenever it felt like it.
        '''

(as it stood, verbatim except for the indentation)

**Nothing raised it.** `wait()` returns `(waited, reason)`, and `Gate.run()` -
the only caller - returns a bool and records `last_reason`. So the docstring
promised "a distinct exception rather than a `False` return" about a function that
returns a *tuple*, and a caller who believed it would write:

    try:
        ...
    except Busy:
        ...

which never fires. That is the silent failure the docstring existed to prevent,
described by the docstring itself. The class was unreferenced in the tree -
`app/voice.py`'s "Busy" is a comment about the gate's behaviour, not a `catch`.

## Why it was removed rather than re-documented

The throwaway type was not the valuable part. The valuable part is **why the
return replaced the raise**, and that belongs next to the function that returns:

- a timeout of zero must not defer - assistd's `gpu_busy_timeout_ms = 0` means
  "ask, and do not queue behind a stream that may never end", and an exception has
  to be raised at a moment the caller has already committed to waiting
- **losing what was said is worse than a slow transcript** - on timeout the gate
  proceeds and says so, which is why "fail loudly" was the wrong instinct: the
  loud thing would be *dropping the user's words*

The behaviour is asserted, not just the prose: a gate that never clears still
returns, still reports, and never raises.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr/lib/shani-chronoa"))

from shani_chronoa import speech_gate  # noqa: E402


def test_the_exception_is_gone():
    """A caller must not be able to write `except Busy:` and get dead code."""
    assert not hasattr(speech_gate, "Busy"), (
        "Busy exists again, and its docstring describes a raise that `wait()` "
        "does not perform")


def test_a_gate_that_never_clears_still_returns_and_says_why():
    """**The behaviour, not the prose.** It proceeds rather than raising, and the
    reason names the timeout - which is the whole point of not raising."""
    gate = speech_gate.Gate(lambda: True, timeout=0.0)

    waited = gate.run()

    assert waited is False, f"a zero timeout must not report a wait: {waited!r}"
    assert "transcribing anyway" in gate.last_reason, gate.last_reason


def test_a_gate_that_clears_reports_the_wait():
    state = {"busy": True}

    def busy():
        return state["busy"]

    gate = speech_gate.Gate(busy, timeout=5.0)
    state["busy"] = False

    assert gate.run() is False, "an idle model needs no wait"
    assert "idle" in gate.last_reason, gate.last_reason


def test_wait_returns_a_pair_not_an_exception():
    """The contract the old docstring described wrongly."""
    result = speech_gate.wait(lambda: False, timeout=0.0)

    assert isinstance(result, tuple) and len(result) == 2, result
    assert result[0] is False and isinstance(result[1], str)


def test_the_rationale_survives_on_the_function_that_returns():
    """So the knowledge is not lost with the class."""
    doc = speech_gate.wait.__doc__ or ""

    assert "returns rather than raising" in doc, (
        "wait()'s docstring no longer says why it returns instead of raising; the "
        "reasoning moved out with the class and came back with nothing")
    for phrase in ("gpu_busy_timeout_ms", "losing what was said"):
        assert phrase in doc, f"the rationale lost {phrase!r}"
