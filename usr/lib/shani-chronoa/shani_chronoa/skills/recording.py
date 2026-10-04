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

import tempfile
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import Skill

_ACTIONS = ("text", "subtitles", "speakers", "clean", "sounds", "listen")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "recording",
        "description": (
            "Work with the sound of an audio or video file, on this computer. text: what was said; subtitles: "
            "save an .srt beside it; speakers: who said what, turn by turn; clean: a copy with background "
            "noise removed; sounds: what can be heard in it (music, a dog, traffic...); listen: what can be "
            "heard around the computer right now (the microphone, a few seconds; needs the sound consent)."
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


def _run(arguments: dict) -> str:
    from shani_chronoa import recordings
    action = (arguments.get("action") or "").strip().lower()
    if action not in _ACTIONS:
        return f"action must be one of {', '.join(_ACTIONS)}."
    if action == "listen":
        from shani_chronoa import sounds
        from shani_chronoa.config import ChronoaConfig
        config = ChronoaConfig()
        if not config.get_bool("sound-sense-enabled", False):
            return ("Listening is turned off (enable 'Let Chronoa listen for sounds like the doorbell' in "
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
