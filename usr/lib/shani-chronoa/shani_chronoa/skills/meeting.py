"""Hold a meeting: record for as long as it takes, then notes with who said what.

**This is the widest thing the skill whitelist can do.** An ordinary turn holds the
microphone for twenty seconds, dictation for five minutes, and this for up to two
hours - with other people speaking in it. So `meeting-enabled` defaults **false**
and its schema row says what it is in those terms, rather than a one-line
"record meetings" asking for something nobody has agreed to.

`meeting.py` is the model; this is the surface. It composes what already worked
(`AudioRecorder`, `recordings.transcribe`, `assign_speakers`) rather than adding a
second capture path, and the two things it adds are the parts that were missing:
a ceiling measured in hours, and an end status a person can read.
"""

from __future__ import annotations

import logging

from shani_chronoa import meeting
from shani_chronoa.skills import Skill

logger = logging.getLogger(__name__)

_CONSENT_KEY = "meeting-enabled"

_ACTIONS = ("status", "record", "notes")


def _consent(config) -> "tuple[bool, str]":
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (f"Recording a meeting is turned off. The microphone was not "
                       f"opened. Enable '{_CONSENT_KEY}' in Settings to allow it.")
    return True, ""


def _run(arguments: dict):
    config = arguments.get("_config")
    if config is None:
        from shani_chronoa.config import ChronoaConfig
        config = ChronoaConfig()
    allowed, refusal = _consent(config)
    if not allowed:
        return refusal

    action = (arguments.get("action") or "record").strip().lower()
    if action not in _ACTIONS:
        return f"action must be one of {', '.join(_ACTIONS)}; {action!r} is not one"

    if action == "status":
        whisper = ""
        try:
            from shani_chronoa import stt
            whisper = ("" if stt.is_available() else
                       " whisper.cpp has no model, so a recording could not be "
                       "transcribed afterwards")
        except Exception:                        # noqa: BLE001 - a probe, not a gate
            whisper = ""
        return (f"A meeting would hold the microphone for up to "
                f"{meeting.MAX_MEETING_SECONDS / 60:.0f} minutes, write a WAV, then "
                f"transcribe it with speakers and write notes with the local model. "
                f"Everything stays on this computer.{whisper}")

    # action == "record": the microphone is opened by `meeting.capture`, which
    # blocks. A tool call on an hour of microphone is a slow tool by nature, so
    # the schema says the ceiling and the consent key is the guard.
    if action == "record":
        try:
            limit = float(arguments.get("minutes") or 0) * 60.0
        except (TypeError, ValueError):
            limit = 0.0
        limit = min(limit or meeting.MAX_MEETING_SECONDS,
                    meeting.MAX_MEETING_SECONDS)
        result = meeting.capture(timeout=limit)
        if not result.wav:
            return (f"Nothing was usable: {meeting._sentence(result.why, result.seconds)}. "
                    f"The recording failed, so there is no transcript either.")
        segments, why = meeting.transcribe(result.wav)
        if segments is None:
            return (f"Recorded {result.wav} for "
                    f"{result.seconds / 60:.1f} minutes ({meeting._sentence(result.why, result.seconds)}), "
                    f"but it could not be transcribed: {why}")
        note, note_why = meeting.notes(segments, config=config)
        head = (f"Recorded {result.wav}; {len(segments)} segment(s) over "
                f"{result.seconds / 60:.1f} minutes. "
                f"{meeting._sentence(result.why, result.seconds)}.")
        if note:
            return head + "\\n\\nNotes:\\n" + note
        return head + f"\\nNo notes: {note_why}"

    # action == "notes": from a recording that already exists
    path = str(arguments.get("path") or "").strip()
    if not path:
        return "Which recording? Give the path of a WAV that 'record' wrote."
    segments, why = meeting.transcribe(path, with_speakers=True)
    if segments is None:
        return f"{path} could not be read: {why}"
    note, note_why = meeting.notes(segments, config=config)
    if not note:
        return f"{len(segments)} segment(s) transcribed, but no notes: {note_why}"
    return f"Notes from {path} ({len(segments)} segment(s)):\\n\\n{note}"


SCHEMA = {
    "type": "function",
    "function": {
        "name": "meeting",
        "description": (
            "Hold a meeting: record the microphone for as long as it takes, "
            "transcribe it with who said what, and write notes. 'record': start "
            "now and run for the given number of minutes (default 120, the "
            "ceiling). 'notes': write notes from a recording that already "
            "exists. 'status': what a meeting would do on this machine. The "
            "recording, the transcript and the notes are all written on this "
            "computer and nothing leaves it. Needs the 'meeting-enabled' consent "
            "key, which is off by default - this holds the microphone for "
            "possibly hours, with other people speaking."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_ACTIONS),
                    "description": "record (default), notes, or status.",
                    "default": "record",
                },
                "minutes": {
                    "type": "number",
                    "description": "For record: how long to allow, in minutes "
                                   "(default and ceiling 120).",
                },
                "path": {
                    "type": "string",
                    "description": "For notes: the WAV a previous 'record' wrote.",
                },
            },
            "required": [],
        },
    },
}

SKILLS = [Skill(name="meeting", schema=SCHEMA, run=_run)]
