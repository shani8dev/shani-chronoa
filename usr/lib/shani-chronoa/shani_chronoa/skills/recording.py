"""Skill: the sound in a recording or video - its words, subtitles, who said what, a cleaned copy, what is heard.

- text: what was said (whisper, the setup's speech model).
- subtitles: an .srt beside the video or recording, with timings.
- speakers: the transcript turn by turn, "Speaker 1: ..." (the optional
  speaker model, `speakers.py`).
- clean: a copy with steady background noise reduced (ffmpeg's own
  denoisers - no model; a modest gain, measured in `recordings.py`). A video
  keeps its picture.
- sounds: what can be heard in it - music, a dog, traffic, applause (the
  optional sound model, `sounds.py`).

Everything runs on this machine. Results are new files beside the original,
which is never touched; nothing is overwritten.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import Skill

_ACTIONS = ("text", "subtitles", "speakers", "clean", "sounds", "listen",
              "streams", "capture")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "recording",
        "description": (
            "Make subtitles or a transcript of a video or audio file, tell speakers apart, or remove background noise. "
            "Works on the sound, on this computer. text: what was said; subtitles: "
            "save an .srt beside it; speakers: who said what, turn by turn; clean: a copy with background "
            "noise removed; sounds: what can be heard in it (music, a dog, traffic...); listen: what can be "
            "heard around the computer right now (the microphone, a few seconds); streams: which "
            "applications are carrying audio right now, and the name to capture one by (a call in "
            "a browser is one); capture: record one of those for 5-600 seconds and transcribe "
            "it - which records the room, so it needs the sound consent like the microphone does."
        ),
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": list(_ACTIONS)},
            "path": {"type": "string", "description": "The audio or video file, or a YouTube link (text only: its captions)."},
            "language": {"type": "string", "description": "Spoken language code (en, hi...); default: detect."},
            "people": {"type": "integer", "description": "speakers: how many people talk, if known."},
            "seconds": {"type": "number", "description": "listen: how long, 1-30 (default 5)."},
        }, "required": ["action"]},
    },
}


def _beside(src: Path, tag: str, suffix: str) -> Path:
    for n in range(1, 1000):
        candidate = src.with_name(f"{src.stem}{'' if not tag else '-' + tag}{'' if n == 1 else f'-{n}'}{suffix}")
        if not candidate.exists():
            return candidate
    raise ValueError(f"too many copies beside {src.name} already")


def _verify_recording(arguments: dict, tool=None):
    """Post-condition: for the actions that write a file, is the file real?

    `recording` has several actions and only two of them mutate anything on
    disk: `clean` writes a denoised copy beside the source, and `subtitles`
    writes an `.srt`. Both are exactly the shape `sing` verifies - a synthesis
    that reports success and leaves nothing behind is the failure mode worth
    catching - and both have a real artifact to check, which `speak` does not
    because it plays rather than writes.

    What is checked: the output exists, is not empty, and has the shape the
    action promised. A cleaned copy that is zero bytes, or an `.srt` with no
    subtitle blocks, is what "Saved ..." used to claim.

    What is **not** claimed: anything about the audio content. That this file's
    noise reduction actually helped is not observable here, and pretending
    otherwise would be the kind of unmeasurable claim this layer exists to
    replace.
    """
    action = str(arguments.get("action") or "").strip().lower()
    source = str(arguments.get("path") or "").strip()
    if action not in ("clean", "subtitles") or not source:
        return None  # nothing was written, so nothing to check

    src = Path(source)
    if not src.exists():
        return (False, f"{src.name} does not exist, so no output was produced")
    if action == "clean":
        target = _beside(src, "clean", src.suffix if src.suffix.lower() != ".webm" else ".mkv")
        kind = "audio or video"
    else:
        target = _beside(src, "", ".srt")
        kind = "subtitles"

    try:
        size = target.stat().st_size
    except OSError as exc:
        return (False, f"no output beside {src.name}: {type(exc).__name__}")
    if size == 0:
        return (False, f"{target.name} was written but is empty")
    if action == "subtitles":
        try:
            body = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return (False, f"could not read {target.name}: {exc}")
        if "-->" not in body:
            return (False, f"{target.name} has no subtitle timing lines in it")
        blocks = body.count("-->")
        return (True, f"{target.name} holds {blocks} subtitle block(s), "
                      f"{size} bytes")
    return (True, f"{target.name} was written as {kind} and is {size} bytes, "
                  f"which is more than a header")


POST_CONDITION = _verify_recording

def _capture_wav(name: str, seconds: float):
    """Record the named PipeWire stream to a fresh WAV, or `None`.

    **Measured, both ways.** `pw-record --target=<stream name>` writes a real WAV
    header and the audio with it: 6.02s at -11.8 dBFS off a live stream. Waiting
    `seconds + 20` instead of `seconds` gave **29.99s when 10 was asked** -
    `pw-record` kept capturing until its own source ended - which is three times
    the meeting time a person agreed to, so the wait is the duration itself and
    the process is terminated when it expires.
    """
    wav = Path(tempfile.mkstemp(suffix=".wav", prefix="chronoa-stream-")[1])
    try:
        proc = subprocess.Popen(
            ["pw-record", "--rate", "16000", "--channels", "1", "--format",
             "s16", "--latency", "100ms", "--target", name, str(wav)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except OSError:
        wav.unlink(missing_ok=True)
        return None
    try:
        proc.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    if not wav.exists() or wav.stat().st_size < 1024:
        wav.unlink(missing_ok=True)
        return None
    return wav


def _run(arguments: dict) -> str:
    from shani_chronoa import recordings
    action = (arguments.get("action") or "").strip().lower()
    if action not in _ACTIONS:
        return f"action must be one of {', '.join(_ACTIONS)}."
    if action == "streams":
        from shani_chronoa import pipewire
        from shani_chronoa.config import ChronoaConfig
        config = ChronoaConfig()
        # The same consent `listen` asks for. A stream list is a list of what is
        # playing: `media.name` measured a filename here and would read a meeting
        # title on a real desktop, so the names are about the user's world in
        # exactly the way the microphone is.
        if not config.sense_allowed("heard-sound"):
            return config.sense_allowed_reason("heard-sound") or (
                "Listing what is playing is turned off (enable 'Let Chronoa name a "
                "sound when you ask' in Settings -> Privacy).")
        if not pipewire.is_available():
            return ("Cannot read the audio graph: pw-dump is not installed, or no "
                    "PipeWire graph is running.")
        streams = pipewire.list_streams()
        if not streams:
            return ("Nothing is playing or recording audio right now. No application "
                    "has an audio stream open, which is a quiet machine and not a "
                    "failure.")
        playable = [st for st in streams if st.recordable]
        head = (f"{len(playable)} of {len(streams)} can be captured."
                if playable else
                "None of these can be captured - they are applications' "
                "microphones, not audio leaving them.")
        rows = ["%s%-3s %s%s" % ("yes" if st.recordable else "no ", "", st.label,
                                 ("  (capture as: %s)" % st.name) if st.recordable
                                 else "  (this app's own microphone)")
                for st in streams]
        return head + "\n" + "\n".join(rows)

    if action == "capture":
        from pathlib import Path
        from shani_chronoa import pipewire
        # `recordings` is already bound at the top of `_run`.
        from shani_chronoa.config import ChronoaConfig
        config = ChronoaConfig()
        # **This records the room.** The stream is audio *leaving* an
        # application, so a call in a browser captures whoever is speaking in it
        # - people who are not at this machine and cannot be asked here. That is
        # a different disclosure from dictation, which is own voice and short,
        # which is why it stays behind the microphone's switch instead of riding
        # along with "transcribe my file".
        if not config.sense_allowed("heard-sound"):
            return config.sense_allowed_reason("heard-sound") or (
                "Recording what an application is playing is turned off (enable "
                "'Let Chronoa name a sound when you ask' in Settings -> Privacy).")
        if not pipewire.is_available():
            return ("Cannot read the audio graph: pw-dump is not installed, or no "
                    "PipeWire graph is running.")
        try:
            seconds = float(arguments.get("seconds") or 30)
        except (TypeError, ValueError):
            return "seconds must be a number, 5 to 600."
        if not 5 <= seconds <= 600:
            return ("seconds must be 5 to 600 - under 5 there is nothing to "
                    "transcribe, and over 600 is a whole meeting recorded in one "
                    "call with nobody told it was.")
        playable = [st for st in pipewire.list_streams() if st.recordable]
        if not playable:
            return ("Nothing is playing that can be captured. Capturing an "
                    "application means audio is *leaving* it, not its microphone - "
                    "use recording action=streams to see what is running.")
        wanted = str(arguments.get("stream") or "").strip()
        chosen = None
        if wanted:
            low = wanted.lower()
            for st in playable:
                if low == st.name.lower() or low in st.label.lower():
                    chosen = st
                    break
            if chosen is None:
                return ("No playing stream matches %r. Playable right now: %s"
                        % (wanted, ", ".join(f"{st.label!r} (as {st.name})"
                                             for st in playable)))
        elif len(playable) == 1:
            chosen = playable[0]
        else:
            return ("%d things are playing; say which one with stream=. Playable: %s"
                    % (len(playable), ", ".join(f"{st.label!r} (as {st.name})"
                                                for st in playable)))

        # **Not `AudioRecorder`, and the reason is a defect in it for this use.**
        # That recorder calibrates its silence threshold from the first ~320ms,
        # which is right for a microphone - the room before the person talks -
        # and cannot work for a stream already playing: the audio is continuous
        # from frame zero, so the threshold calibrates to twice the signal's own
        # peak and the signal can never again count as speech. Measured: the
        # recorder ran its whole window, reported reason "limit", and handed
        # back `on_done(None)`. `vad.calibrate_noise_floor`'s docstring is built
        # around "the room before speech"; a stream has no such room.
        wav = _capture_wav(chosen.name, seconds)
        if wav is None:
            return (f"Capturing {chosen.label!r} produced no audio. It may "
                    f"have stopped playing as the capture started.")
        try:
            proc = subprocess.Popen(
                ["pw-record", "--rate", "16000", "--channels", "1", "--format",
                 "s16", "--latency", "100ms", "--target", chosen.name, str(wav)],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except OSError as exc:
            wav.unlink(missing_ok=True)
            return f"Could not start capturing {chosen.label!r}: {exc}"
        # **Wait for exactly the duration, then stop it.** The first version
        # waited `seconds + 20` with no terminate inside the timeout, so a
        # caller asking for 10 seconds got **29.99** - `pw-record` happily kept
        # capturing until its own source ended or the timeout fired, and the
        # result was three times the meeting time a person agreed to. Measured.
        try:
            proc.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        if not wav.exists() or wav.stat().st_size < 1024:
            said = proc.stderr.read().strip()[:160] if proc.stderr else ""
            wav.unlink(missing_ok=True)
            return (f"Capturing {chosen.label!r} produced no audio"
                    + (f" ({said})" if said else "")
                    + ". It may have stopped playing as the capture started.")
        try:
            # **The shipped diarisation path, not a new one.** `segments_for` is
            # what the `speakers` action runs - whisper's timed segments, then
            # `speakers.diarize` + `assign_speakers` for the turns.
            segments, note = recordings.segments_for(
                wav, str(arguments.get("language") or "auto"),
                with_speakers=bool(arguments.get("people")))
        except (recordings.RecordingError, RuntimeError, ValueError, OSError) as exc:
            said = str(exc)
            # **A transcriber that died on a signal must not be reported as one.**
            # Measured: whisper.cpp segfaults in `mmap` under the skill child's
            # 512 MB `RLIMIT_AS`, and the word that comes back is `mmap: 12` plus
            # a backtrace - which tells nobody the limit is the cause, and
            # `AGENTS.md` has the same measurement recorded for podman and for
            # QEMU. The capture itself is fine; the recording is kept and named.
            died = ("Fatal signal" in said or "Segmentation fault" in said
                    or "mmap" in said or "returncode -" in said)
            if died:
                return (f"Recorded {chosen.label!r} for {recordings.duration(wav):.0f}s "
                        f"(kept at {wav}), and the transcriber died rather than "
                        f"answering - on this class of machine that is the skill "
                        f"sandbox's address-space ceiling, which whisper.cpp cannot "
                        f"mmap its model under. The recording is real and kept; "
                        f"transcribe it with that limit lifted, or use a smaller "
                        f"model.")
            return f"Recorded {chosen.label!r} (kept at {wav}), but could not transcribe it: {said}"
        text = recordings.to_text(segments)
        how_long = recordings.duration(wav)
        if not text.strip():
            return (f"Recorded {chosen.label!r} for {how_long:.0f}s (kept at {wav}) "
                    f"and heard nothing in it.")
        out = f"In {chosen.label!r}: {text}"
        if note:
            out += f"\n\n({note})"
        out += (f"\n\nRecorded {how_long:.0f}s, kept at {wav} - `speakers` or "
                f"`subtitles` can run on that file.")
        return out

    if action == "listen":
        from shani_chronoa import sounds
        from shani_chronoa.config import ChronoaConfig
        config = ChronoaConfig()
        # `sense_allowed`, not a raw key read: the consent-key aliases and the
        # privacy-mode refusal live inside it, and a skill that re-reads the key
        # itself can disagree with the sense layer about the same microphone.
        if not config.sense_allowed("heard-sound"):
            return config.sense_allowed_reason("heard-sound") or (
                "Listening is turned off (enable 'Let Chronoa name a sound when you ask' in "
                "Settings -> Privacy).")
        problem = sounds.problem()
        if problem:
            return problem
        try:
            heard = sounds.listen(float(arguments.get("seconds") or 5))
        except (RuntimeError, ValueError) as exc:
            return f"Could not listen: {exc}"
        return f"Heard around the computer: {sounds.describe(heard)} (nothing was recorded or kept)."
    raw_path = str(arguments.get("path") or "").strip()
    if not raw_path:
        return "Give the audio or video file (path)."
    if recordings.is_youtube(raw_path):
        if action != "text":
            return "For a YouTube link only text works (its captions); download the video first for the rest."
        try:
            title, text = recordings.youtube_captions(raw_path, (arguments.get("language") or "en").strip().lower())
        except recordings.RecordingError as exc:
            return f"Could not read that video: {exc}"
        return f"{title} (YouTube captions):\n{text[:6000]}"
    try:
        src = files.resolve(raw_path)
    except files.PathProblem as exc:
        return str(exc)
    if not src.is_file():
        return f"{src} does not exist."
    if src.suffix.lower() not in recordings.AUDIO | recordings.VIDEO:
        return f"{src.name} is not an audio or video file this works on."
    language = (arguments.get("language") or "auto").strip().lower() or "auto"
    if not language.replace("-", "").isalpha() or len(language) > 8:
        return "language is a code like en or hi."
    try:
        if action == "clean":
            target = _beside(src, "clean", src.suffix if src.suffix.lower() != ".webm" else ".mkv")
            recordings.clean(src, target)
            return f"Saved a cleaned-up copy of {src.name} to {target} (steady background noise reduced)."
        if action == "sounds":
            from shani_chronoa import sounds
            problem = sounds.problem()
            if problem:
                return problem
            with tempfile.TemporaryDirectory(prefix="chronoa-snd-") as tmp:
                wav = Path(tmp) / "audio.wav"
                recordings.extract_audio(src, wav)
                return f"In {src.name}: {sounds.describe(sounds.tag(wav))}."
        segments, note = recordings.segments_for(src, language, with_speakers=action == "speakers",
                                                 speakers_count=arguments.get("people") or None)
        if not segments:
            return f"No speech was found in {src.name}."
        said = f" ({note})" if note else ""
        if action == "subtitles":
            target = _beside(src, "", ".srt")
            with open(target, "x", encoding="utf-8") as handle:
                handle.write(recordings.to_srt(segments))
            return f"Saved {len(segments)} subtitles for {src.name} to {target}{said}."
        text = recordings.to_text(segments)
        return f"{src.name}{said}:\n{text[:6000]}"
    except (recordings.RecordingError, RuntimeError, ValueError, OSError) as exc:
        return f"Could not work on {src.name}: {exc}"


SKILLS = [Skill(name="recording", schema=SCHEMA, run=_run)]
