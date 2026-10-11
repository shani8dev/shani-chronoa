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
import pathlib
import shutil
import time
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


def recordings_dir():
    """Where a meeting is kept, beside Chronoa's other state.

    **It used to have nowhere.** `capture()` took whatever path the recorder
    handed back, and `AudioRecorder` writes with `tempfile.mkstemp` — so every
    meeting landed in `/tmp`. On a ShaniOS install `/tmp` is cleared on reboot
    (and `/var`, which the temp dir is under, is tmpfs by the image's own fstab),
    which means an hour of microphone was thrown away by the next restart. The
    thing this feature exists to produce was the thing it deleted.

    Resolved per call and never at import, like every other data path in this
    tree: an import-time constant would point the test suite at the real
    `~/.local/state`, which has already happened to the percept store.
    """
    from shani_chronoa import files
    path = files.state_home() / "shani-chronoa" / "meetings"
    path.mkdir(parents=True, exist_ok=True)
    # 0700 on the directory itself. `mkdir(mode=...)` applies to the leaf only
    # when `parents=True`, and leaves an already-created directory exactly as it
    # was, so the mode is set separately — the same reasoning `egress.py` records.
    try:
        path.chmod(0o700)
    except OSError as exc:                     # a read-only home is not a fatal error
        logger.info("could not tighten %s: %s", path, exc)
    return path


def _capture_time(path: pathlib.Path) -> "tuple[int, float, str]":
    """Sort key: the capture time in the name, then the mtime, then the name.

    **The name, not the mtime.** `_keep()` names a file `meeting-%Y%m%d-%H%M%S`
    because two captures in the same second must not collide, which makes the
    name the capture time *by construction*. The mtime is when the file was
    last written, which a plain `cp` changes, which `tar` preserves to the
    second for every file in a backup, and which a restore puts back from a
    machine whose clock differed. Sorting on it and calling the result "newest
    first" is a claim about something the shelf does not know.

    The mtime and the name are still kept as tiebreakers so two files with an
    unparseable name - a hand-renamed recording, say - still order stably
    instead of falling back to directory order.
    """
    name = path.name
    stamp = name[len("meeting-"):-len(".wav")] if name.startswith("meeting-") else ""
    try:
        # `time.mktime`, not `struct_time.timestamp()`: `strptime` returns a
        # `struct_time`, which has no such method, and the stamp is local time
        # because `_keep` wrote it with `time.strftime` on a `time.localtime`.
        parsed = int(time.mktime(time.strptime(stamp, "%Y%m%d-%H%M%S")))
    except (TypeError, ValueError):
        parsed = 0
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (parsed, mtime, name)


def list_recordings() -> "List[dict]":
    """Every meeting on this machine, newest first, as `{"path", "name", "bytes",
    "when"}`.

    Read off the directory rather than kept in an index: a meeting is a file,
    and a list of meetings that disagrees with the files is a list of lies.

    Newest first is decided by `_capture_time`, which is why that is a function
    rather than `stat().st_mtime` inline.
    """
    try:
        entries = sorted(recordings_dir().glob("meeting-*.wav"),
                         key=_capture_time, reverse=True)
    except OSError as exc:
        logger.info("could not list meetings: %s", exc)
        return []
    out = []
    for path in entries:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        out.append({"path": str(path), "name": path.name, "bytes": size,
                    "when": path.name[len("meeting-"):-len(".wav")]})
    return out


def _keep(wav: Optional[str]) -> Optional[str]:
    """Move a finished capture out of the temp dir and give it a real name.

    The recorder's path is used as the source, never the destination: a rename
    across filesystems is a copy, and `/tmp` and `$XDG_STATE_HOME` are on
    different ones here more often than not, so `shutil.move` rather than
    `os.replace`. If the move fails the original path is handed back **with its
    bytes intact** rather than a meeting that was lost to a rename — a temp file
    that will be cleared is better than no file to point at.
    """
    if not wav:
        return None
    source = pathlib.Path(wav)
    try:
        source = pathlib.Path(wav).resolve()
    except OSError:
        pass
    if not source.is_file():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S",
                          time.localtime(source.stat().st_mtime))
    target = recordings_dir() / f"meeting-{stamp}.wav"
    # Two meetings inside the same second would collide, and a second one
    # silently overwriting an hour of somebody's meeting is the worst outcome
    # this function could have.
    counter = 2
    while target.exists():
        target = recordings_dir() / f"meeting-{stamp}-{counter}.wav"
        counter += 1
    try:
        shutil.move(str(source), str(target))
    except OSError as exc:
        logger.warning("could not move %s into place (%s); the recording is "
                       "still at the temporary path and will be cleared", wav, exc)
        return str(source)
    return str(target)


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
    # **Into the state dir, or nowhere.** The recorder hands back a temp path
    # and the system clears those; see `recordings_dir()` for why this is the
    # difference between an hour of microphone kept and an hour discarded.
    kept = _keep(outcome["wav"])
    return MeetingResult(wav=kept, seconds=seconds, why=outcome["why"])


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


def transcribe(wav, language: str = "auto", with_speakers: bool = True):
    """`(segments, why-not)` for one recorded meeting.

    `wav` is coerced to a `Path` because the skill hands it a **string** from the
    model's `path` argument, and `recordings.segments_for` reaches for
    `src.name` - the first version typed the parameter as `Path` and trust that
    annotation, so `notes` on a real path raised `AttributeError: 'str' object has
    no attribute 'name'` instead of answering. A path that is not one is a
    sentence, never a traceback.
    """
    import pathlib
    from shani_chronoa import recordings
    try:
        wav = pathlib.Path(wav)
    except TypeError as exc:
        return None, f"that is not a path ({exc})"
    try:
        segments = recordings.segments_for(wav, language=language,
                                          with_speakers=with_speakers)
    except recordings.RecordingError as exc:
        return None, str(exc)
    except OSError as exc:
        return None, f"that file could not be read ({exc})"
    return segments, ""


def _run_coroutine_sync(coro):
    """Drive one coroutine to a result from code with no event loop.

    `notes()` is called from a bare thread (the skill's child, and the surface's
    `_background`), so there is no loop to post to and `AsyncBridge` has not
    been built. A coroutine that is never awaited is *created and discarded* -
    the exact failure `AGENTS.md` records for `app.py`'s `async def` handlers,
    where the whole chat pipeline silently became dead code. So it is driven
    here, explicitly.

    If a loop is already running this is being called from the main loop, which
    `quick_ask.py` documents as the thing that hangs a GTK handler - that gets a
    readable refusal rather than `RuntimeError`.
    """
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        raise RuntimeError(
            "notes() is being called from inside a running event loop; drive it "
            "from a worker thread (the surface's `_background`), as it is built to be")
    return asyncio.run(coro)


def _reply_text(reply) -> str:
    """The model's words, however the backend shaped them.

    `chat_message()` returns a dict whose `content` is normally a string, but a
    backend that flattened its own blocks into a list must not be stringified -
    `distill.py`'s `_ask_chat_message` records that a list joined by `str()`
    "parses as a skill name only by accident".
    """
    if not isinstance(reply, dict):
        return ""
    content = reply.get("content") or ""
    if isinstance(content, list):
        return "".join(str(part.get("text", "")) if isinstance(part, dict) else str(part)
                       for part in content).strip()
    return content.strip() if isinstance(content, str) else str(content).strip()


def _drive_one_section(backend, messages, section: int, of: int):
    """One section through the local model, by this codebase's own interface.

    **`chat_message()` is what every backend here has, and `.chat()` is not.**
    `distill.py`'s `_ask_chat_message` documents that bug in full: a first
    version called `.chat()`, only `OllamaLLM` defines it, llama.cpp and the
    whole cloud chain raised `AttributeError`, the loop moved on, and a machine
    with a configured provider reported "no language model is configured" while
    the provider sat right there with a key.

    **`meeting.notes()` then shipped the same mistake twice over**, and the
    second half is why it never surfaced:

        engine = local_llm.LocalLLM          # __new__ takes (model_key,) alone
        engine(config=config, transport=transport)   -> TypeError
        except TypeError:
            engine(config=config).chat(...)          -> TypeError, uncaught

    Both calls passed arguments the constructor does not have, and the
    `except TypeError` handler was itself the *same* wrong call without
    `transport` - so the fallback raised the identical error outside any
    handler. Measured: `notes()` raises
    `TypeError: LocalLLM.__new__() got an unexpected keyword argument 'config'`
    on every call, which means the meeting skill's `notes` action has never once
    produced notes on any machine.

    `config` and `transport` are accepted by `notes()` for its three callers but
    are **not part of `chat_message()`'s contract** - checked, not assumed: no
    `def chat(` in this package takes either. They are no longer forwarded to
    anything, and that is the honest reading rather than a parameter quietly
    kept alive by being passed to a function that cannot take it.
    """
    reply = _run_coroutine_sync(backend.chat_message(messages, tools=None))
    return _reply_text(reply)


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

    if not hasattr(local_llm, "LocalLLM"):
        return None, "no local model is configured here, so notes cannot be written"
    # Built once and shared by every section: one connection pool, one model
    # load, and the construct-time refusal surfaces before any money is spent.
    backend = local_llm.LocalLLM()

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
            text = _drive_one_section(backend, messages, index + 1, sections)
        except Exception as exc:                 # noqa: BLE001
            return None, f"the local model could not be reached ({exc})"
        if not text:
            return None, "the local model answered with nothing, so no notes"
        notes_text.append(text)

    if sections == 1:
        return notes_text[0], ""
    return ("These notes are a summary of the whole meeting, in "
            f"{sections} sections.\\n\\n"
            + "\\n\\n---\\n\\n".join(notes_text)), ""
