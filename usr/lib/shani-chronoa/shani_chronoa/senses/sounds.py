"""What is happening in the room, right now.

`sounds.py` can label a recording - that is the hard part, and it is done. What
was missing was the part that decides *when* to listen, which is why a rule like
"tell me when the doorbell rings" could be written but never fired on its own:
only `senses/audio.py` existed, and that answers questions about a recording
the user already has.

This module is that missing layer. It listens for a few seconds, labels what it
heard, and hands back a percept. It is the same code path the `sound` trigger
uses, so both agree on what "a doorbell at 60% confidence" means.

The sense is named `heard-sound`, not `what_can_be_heard`, because a sense name
becomes a gsettings key (`heard-sound-sense-enabled`) and glib-compile-schemas
discards the whole schema file on one illegal key name - an underscore would
have taken every other key with it.

## The microphone is the most private sensor in the system

Nothing else here can hear what happens in someone's home. A camera sees a
room; this hears the conversations in it. So the sense is
`SENSITIVITY_PRIVATE`, not merely personal, its key defaults to off, and the
refusal says which of those two things is true rather than reading as a bug.

It is on-demand only. There is no `poll_interval`: a background sense that
opens the microphone on a timer would be a microphone that is never actually
off, which is a different thing from a microphone you asked to use once. The
`sound` trigger already provides the "tell me when X happens" path for people
who want continuous listening, and it asks for the key first.

## Nothing is kept

`listen()` records into a temporary directory and deletes it when it returns,
so this leaves no audio behind. That is also why the percept carries labels
and confidences rather than a recording: there is no file to point at.

## Absent is not silent

A model that is not installed, a microphone that is busy, and a genuinely
quiet room all come back as a refusal with the reason attached. None of them
answers "nothing is happening", because that claim is not supportable from a
failed read - the same rule `idle` follows for an unreadable clock.
"""

from __future__ import annotations

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PRIVATE, Sense

KIND = "machine-state"

#: The registered sense name, and the stem of its consent key
#: (`heard-sound-sense-enabled`). Held in one place because the gate, the
#: schema and the `Sense` entry must all agree: a mismatch denies the sense
#: forever rather than failing loudly.
NAME = "heard-sound"

#: A room's noises reveal who lives in it and what they do at what hour.
SENSITIVITY = SENSITIVITY_PRIVATE

_TTL_SECONDS = 5.0

#: Long enough to catch a knock or a doorbell, short enough that asking a
#: question about the room does not feel like it is eavesdropping on it.
_LISTEN_SECONDS = 3.0

#: Below this the classifier is guessing. `sounds.describe` uses the same
#: floor for the human-readable summary.
_FLOOR = 0.3


def _clamp_seconds(value: object) -> float:
    """A listen length inside what `sounds.listen` accepts (1-30 s)."""
    try:
        seconds = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _LISTEN_SECONDS
    if seconds != seconds:  # NaN compares false against everything
        return _LISTEN_SECONDS
    return min(30.0, max(1.0, seconds))


_SCHEMA = {
    "type": "function",
    "function": {
        "name": NAME,
        "description": (
            "Listen for a few seconds and report what can be heard in the room "
            "right now - a doorbell, a knock, a dog, a baby, an alarm, speech. "
            "Use it when asked what is going on nearby, whether something "
            "happened, or whether anyone is there. The recording is labelled and "
            "deleted immediately; nothing is kept. Needs the "
            "'heard-sound-sense-enabled' "
            "consent key and an optional audio-tagging model, which the setup "
            "wizard can install."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "seconds": {
                    "type": "number",
                    "description": (
                        "How long to listen for, 1 to 30 seconds. Defaults to 3, "
                        "which is enough for most household sounds."
                    ),
                },
            },
        },
    },
}


def _run(arguments: dict) -> str:
    config = ChronoaConfig()
    # The gate is on the sense's own registered name, not on the event type.
    # `sound` is the *event*; gating on it would consult `sound-sense-enabled`,
    # which is a different key and lets the two drift apart silently.
    if not config.sense_allowed(NAME):
        return (
            f"Not listening: {config.sense_allowed_reason(NAME)}. This is the "
            f"most private sensor there is - it hears the room, including any "
            f"conversation in it - so it stays off until you turn it on."
        )

    # Imported here rather than at module scope: the sense registry imports
    # every sense module at startup, and this one must not drag the audio
    # stack (and its optional model loader) into a process that only wants to
    # list the senses.
    from shani_chronoa import sounds

    problem = sounds.problem()
    if problem:
        # `problem()` already names where to fix it, so it is repeated here
        # verbatim rather than paraphrased into a second, vaguer instruction.
        return f"Not listening: {problem}."

    seconds = _clamp_seconds((arguments or {}).get("seconds", _LISTEN_SECONDS))
    try:
        heard = sounds.listen(seconds=seconds)
    except Exception as exc:  # noqa: BLE001 - reported, never raised at the user
        return (
            f"Not listening: {exc}. That means what is in the room is unknown - "
            f"not that the room is quiet, which is a very different claim."
        )

    summary = sounds.describe(heard, floor=_FLOOR)
    if summary == "nothing it recognises clearly":
        return (
            f"Listened for {seconds:.0f} seconds and heard nothing it can name. "
            f"That is not proof the room was silent - it is only that nothing was "
            f"recognisable."
        )

    top = heard[0]
    return f"Listened for {seconds:.0f} seconds and heard {summary} (best guess: {top.name.lower()})."


_SENSE = Sense(
    name=NAME,
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
)

SENSES = [_SENSE]