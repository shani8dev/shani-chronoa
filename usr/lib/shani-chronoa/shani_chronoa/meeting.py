"""A meeting: an hour of microphone, a transcript with speakers, and notes.

**The three pieces that turn a long recording into a meeting**, and why each is
separate from `recordings`:

* **capture that long** - `AudioRecorder.start_auto_stop` already produces one WAV
  for a capture; a meeting is that with `max_seconds` measured in minutes and
  `silence_seconds` large enough not to stop at every pause in a paragraph.
* **an end status, not a path or None** - a capture that ended the way it was
  meant to and one that died on its own are different facts, and `on_done(None)`
  cannot tell them apart: `pw-record` dying mid-meeting and a room falling silent
  look identical from the outside, which is the failure
  `test_child_supervisor_live.py` was written to catch.
* **notes** - `recordings.transcribe` gives segments, `assign_speakers` gives who;
  neither turns them into the thing a person reads afterwards.

**A meeting is the widest thing this program can ask for.** An ordinary turn holds
the microphone for twenty seconds; dictation for five minutes; this for an hour,
with other people speaking in it. So `meeting-enabled` defaults **false** and its
schema entry says so in those terms - a one-line "record meetings" row would be
asking for something most people have not agreed to.

**Nothing here is a cloud call.** The transcript is whisper.cpp, the speakers are
sherpa-onnx, the notes are the local model. Everything about a meeting stays on
the machine; that is the reason to use this rather than the alternative.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import List, NamedTuple, Optional

logger = logging.getLogger(__name__)

#: How long a meeting may run, and what silence means. Dictation's ceiling is
#: 300s with 2.5s of silence, which is right for a sentence and wrong here: a
#: question pauses for two seconds and a *room* pauses longer, so a threshold
#: tuned for a turn ends the meeting at the first breath.
MAX_MEETING_SECONDS = 2 * 60 * 60.0
MEETING_SILENCE_SECONDS = 20.0

#: How much transcript is sent to the local model for notes. The window is 8192
#: context and the schemas eat most of it on a small model, so a meeting longer
#: than this is summarised in sections and **the notes say how many**, rather
#: than silently dropping the last hour of a two-hour meeting.
NOTES_MAX_CHARS = 12000

#: How a meeting ended. Only one of these is a fault, and the caller must be
#: able to tell them apart without inspecting the audio file.
END_SILENCE = "silence"        # nobody spoke for MEETING_SILENCE_SECONDS
END_LIMIT = "limit"            # the ceiling, reached
END_CANCELLED = "cancelled"    # the person stopped it
END_DIED = "died"              # the capture child died on its own


class MeetingResult(NamedTuple):
    """A meeting, and how it ended."""

    wav: Optional[str]
    seconds: float
    why: str
    segments: Optional[List] = None
    note: Optional[str] = None

    @property
    def stopped_cleanly(self) -> bool:
        """True when the meeting ended the way the person meant.

        `died` is the one that is not: a child that exits on its own is a fault
        and the transcript is a fragment, so a caller that reported it as a
        finished meeting would be telling the person they have the whole thing
        when they have a third of it.
        """
        return self.why != END_DIED


class MeetingError(RuntimeError):
    """The message is fit to show a person."""


def _sentence(why: str, seconds: float) -> str:
    """The one line a person reads about how their meeting ended."""
    minutes = seconds / 60.0
    where = f"{minutes:.0f} minutes" if minutes >= 1 else f"{seconds:.0f} seconds"
    return {
        END_SILENCE: f"stopped after {where} of quiet - the transcript is complete",
        END_LIMIT: f"stopped at the {where} ceiling - the transcript is complete",
        END_CANCELLED: f"you stopped it after {where} - the transcript is complete",
        END_DIED: f"the recording failed after {where} and the transcript is only "
                  f"part of the meeting",
    }.get(why, f"ended after {where}")


def capture(timeout: float = MAX_MEETING_SECONDS, recorder=None,
            on_level=None) -> MeetingResult:
    """Record a meeting to a WAV file. Returns how it ended.

    `recorder=` exists so a test can hand in a recorder whose child is a stub;
    everything here composes the real `AudioRecorder` otherwise.

    The generation is bumped by `start_auto_stop` exactly as for a turn, so the
    `on_done` this hands it is a *this-meeting* callback rather than a
    *some-meeting* one.
    """
    if recorder is None:
        from shani_chronoa.audio import AudioRecorder
        recorder = AudioRecorder()

    started = time.monotonic()
    outcome = {"wav": None, "why": END_DIED}

    def on_done(wav: Optional[str]) -> None:
        outcome["wav"] = wav
        # A path means the child reached silence, the ceiling, or the person's
        # own stop, and closed its pipe on the way out. `None` means it did not,
        # and the distinction is the whole of `pipe_closed`.
        if wav is not None:
            outcome["why"] = _reason_from_recorder(recorder)
        else:
            outcome["why"] = END_DIED

    silence = MEETING_SILENCE_SECONDS if timeout >= MEETING_SILENCE_SECONDS else timeout
    try:
        started_ok = recorder.start_auto_stop(
            on_done, max_seconds=timeout, silence_seconds=silence,
            on_level=on_level)
    except Exception as exc:                     # noqa: BLE001 - a sentence, not a raise
        raise MeetingError(f"the microphone could not be started ({exc})") from exc
    if not started_ok:
        raise MeetingError("the microphone would not start - no capture device, or "
                           "another recording is already using it")

    if recorder._auto_stop_thread is not None:
        recorder._auto_stop_thread.join(timeout=timeout + 30)

    seconds = time.monotonic() - started
    return MeetingResult(wav=outcome["wav"], seconds=seconds, why=outcome["why"])


def _reason_from_recorder(recorder) -> str:
    """Why a capture that *did* produce a file ended as it did.

    `AudioRecorder.auto_stop_reason()` already answers this - `"limit"` means the
    clock ran out mid-speech, `"silence"` means the person finished - so this maps
    that vocabulary rather than re-deriving it from `_stop_epoch`, which is a
    counter for a different question (which frames were already in flight when a
    stop press landed) and only accidentally correlates.

    The trap being avoided: a recorder whose reason is not one of the three known
    values is reported as silence, which would describe an interrupted capture as
    a finished one.
    """
    reason = ""
    getter = getattr(recorder, "auto_stop_reason", None)
    if callable(getter):
        try:
            reason = str(getter() or "")
        except Exception:                        # noqa: BLE001 - a reason, not a raise
            reason = ""
    if reason == "limit":
        return END_LIMIT
    if reason == "silence":
        return END_SILENCE
    # The person pressed stop, or the recorder did not say. `cancelled` is the
    # safer reading: a transcript that may be cut short described as complete is
    # the failure this whole enum exists to prevent.
    return END_CANCELLED if reason == "cancelled" else END_CANCELLED


def transcribe(wav: Path, language: str = "auto", with_speakers: bool = True):
    """`(segments, why-not)` for one recorded meeting."""
    from shani_chronoa import recordings
    try:
        segments = recordings.segments_for(wav, language=language,
                                          with_speakers=with_speakers)
    except recordings.RecordingError as exc:
        return None, str(exc)
    return segments, ""


def notes(segments, config=None, transport=None):
    """Notes from the segments, or `(None, why-not)`.

    **The local model, and it opts in per run.** A summary is the one output here
    a person is likely to quote onwards, so it is worth being explicit that it is
    a model writing prose about a meeting, not a record of one - the transcript
    stays the record, and the note says which turns it came from.

    A meeting longer than `NOTES_MAX_CHARS` is summarised in sections and the note
    names the count, so a two-hour meeting does not silently keep only its first
    hour. Fewer sections than one means the whole meeting was used.
    """
    if not segments:
        return None, "there is nothing to summarise"
    from shani_chronoa import local_llm

    lines = []
    for segment in segments:
        speaker = f"[{segment.speaker}] " if getattr(segment, "speaker", "") else ""
        lines.append(f"{speaker}{segment.text}".strip())
    transcript = "\n".join(lines)

    sections = max(1, (len(transcript) + NOTES_MAX_CHARS - 1) // NOTES_MAX_CHARS)
    chunks = []
    for index in range(sections):
        piece = transcript[index * NOTES_MAX_CHARS:(index + 1) * NOTES_MAX_CHARS]
        chunks.append(piece)

    engine = local_llm.LocalLLM if hasattr(local_llm, "LocalLLM") else None
    if engine is None:
        return None, "no local model is configured here, so notes cannot be written"

    prompt = (
        "You are writing notes about a meeting from its transcript. "
        "Rules: only what the transcript says - never invent a decision, an "
        "owner or a date. Mark anything you are unsure of as unclear. "
        "List decisions, actions with owners, and open questions, in that "
        "order. Keep it under 300 words.")

    notes_text = []
    for index, piece in enumerate(chunks):
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content":
                f"Section {index + 1} of {sections} of the transcript:\\n\\n{piece}"},
        ]
        try:
            reply = engine(config=config, transport=transport).chat(
                messages, stream=False)
        except TypeError:
            reply = engine(config=config).chat(messages, stream=False)
        except Exception as exc:                 # noqa: BLE001
            return None, f"the local model could not be reached ({exc})"
        text = (reply.get("content") or "").strip() if isinstance(reply, dict) else ""
        if not text:
            return None, "the local model answered with nothing, so no notes"
        notes_text.append(text)

    if sections == 1:
        return notes_text[0], ""
    return ("These notes are a summary of the whole meeting, in "
            f"{sections} sections.\\n\\n"
            + "\\n\\n---\\n\\n".join(notes_text)), ""
