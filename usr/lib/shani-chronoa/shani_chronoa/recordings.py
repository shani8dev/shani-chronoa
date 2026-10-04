"""The speech in a recording or video: its text, subtitles, who said what, and a cleaned-up copy.

Everything here is tools every ShaniOS install has, plus the speech model the
setup's Ears step downloads:

- ffmpeg takes the audio out of anything it can read (16 kHz mono, which is
  what whisper wants), and cleans a recording with its own denoisers,
  `anlmdn` (non-local means) then `afftdn` (FFT). Measured 2026-10-02 on real
  two-person speech with steady white noise added, against the clean
  original: 11.8 dB signal-to-noise untouched, 13.4 dB cleaned. A real but
  small gain - a neural denoiser would do far more - and no level
  normalisation: `loudnorm` was measured to make it worse (it lifts the quiet
  gaps, which is where the noise is) and resamples to 192 kHz.
- whisper-cli writes timed segments (`-oj`), which become plain text, `.srt`
  subtitles, or - when the optional speaker model is installed (`speakers.py`)
  - a transcript with each turn given to a speaker.

Recordings longer than `MAX_SECONDS` are cut there, and the answer says so: on a
processor whisper takes a while per minute of speech.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, NamedTuple, Optional

MAX_SECONDS = 1800
AUDIO = {".wav", ".mp3", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".flac", ".wma", ".webm"}
VIDEO = {".mp4", ".mkv", ".mov", ".avi", ".m4v", ".mpg", ".mpeg", ".wmv", ".3gp"}

#: the best of the ffmpeg chains measured (see the module docstring); band filters made it worse
CLEAN_FILTER = "anlmdn=s=7,afftdn=nr=12:tn=1"


class Segment(NamedTuple):
    start: float
    end: float
    text: str
    speaker: str = ""


class RecordingError(RuntimeError):
    """The message is fit to show a person."""


def _ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise RecordingError("ffmpeg is not installed")
    return path


def duration(path: Path) -> float:
    if not shutil.which("ffprobe"):
        return 0.0
    proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                          capture_output=True, text=True, timeout=60)
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return 0.0


def extract_audio(src: Path, wav: Path) -> None:
    """16 kHz mono 16-bit WAV of the first MAX_SECONDS of `src`."""
    proc = subprocess.run([_ffmpeg(), "-v", "error", "-nostdin", "-y", "-t", str(MAX_SECONDS), "-i", str(src),
                           "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)],
                          capture_output=True, text=True, timeout=600)
    if proc.returncode != 0 or not wav.is_file() or wav.stat().st_size <= 44:
        raise RecordingError(f"no audio could be read from {src.name}: {proc.stderr.strip()[-200:] or 'it has none'}")


def whisper_model() -> "tuple[str, str]":
    """(whisper-cli, model file) to use: the configured model, else the largest one installed."""
    from shani_chronoa.config import ChronoaConfig
    from shani_chronoa.stt import WhisperSTT
    wanted = (ChronoaConfig().get("whisper-model", "") or "").strip()
    for name in [wanted] + ["small", "base", "tiny"]:
        if not name:
            continue
        stt = WhisperSTT(model=name)
        if os.path.exists(stt.model_path):
            if not (shutil.which(stt.whisper_path) or os.access(stt.whisper_path, os.X_OK)):
                raise RecordingError("whisper.cpp (whisper-cli) is not installed")
            return stt.whisper_path, stt.model_path
    raise RecordingError("no speech model is installed (Chronoa's setup, Ears)")


def transcribe(wav: Path, language: str = "auto") -> List[Segment]:
    """whisper's timed segments for a 16 kHz WAV."""
    binary, model = whisper_model()
    out = wav.with_suffix("")
    proc = subprocess.run([binary, "-m", model, "-f", str(wav), "-l", language, "-oj", "-of", str(out), "-np",
                           "-t", str(max(1, min(4, (os.cpu_count() or 2) - 1)))],
                          capture_output=True, text=True, timeout=3600)
    data_file = out.with_suffix(".json")
    if proc.returncode != 0 or not data_file.is_file():
        raise RecordingError(f"whisper could not transcribe it: {proc.stderr.strip()[-200:]}")
    data = json.loads(data_file.read_text(encoding="utf-8", errors="replace"))
    data_file.unlink(missing_ok=True)
    segments = []
    for item in data.get("transcription", []):
        text = " ".join(str(item.get("text", "")).split())
        offsets = item.get("offsets") or {}
        if text:
            segments.append(Segment(offsets.get("from", 0) / 1000.0, offsets.get("to", 0) / 1000.0, text))
    return segments


def _stamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(segments: List[Segment]) -> str:
    blocks = []
    for i, seg in enumerate(segments, 1):
        who = f"[{seg.speaker}] " if seg.speaker else ""
        blocks.append(f"{i}\n{_stamp(seg.start)} --> {_stamp(seg.end)}\n{who}{seg.text}\n")
    return "\n".join(blocks)


def to_text(segments: List[Segment]) -> str:
    """Plain text; with speakers, one paragraph per turn."""
    if not any(s.speaker for s in segments):
        return " ".join(s.text for s in segments)
    turns: "list[list]" = []
    for seg in segments:
        if turns and turns[-1][0] == seg.speaker:
            turns[-1][1].append(seg.text)
        else:
            turns.append([seg.speaker, [seg.text]])
    return "\n".join(f"{who}: {' '.join(words)}" for who, words in turns)


def assign_speakers(segments: List[Segment], turns: "list[tuple[float, float, str]]") -> List[Segment]:
    """Each segment goes to the speaker whose turns overlap it most."""
    out = []
    for seg in segments:
        best, overlap = "", 0.0
        for start, end, who in turns:
            o = min(seg.end, end) - max(seg.start, start)
            if o > overlap:
                best, overlap = who, o
        out.append(seg._replace(speaker=best))
    return out


def claim(target: Path) -> None:
    """Create `target` empty, exclusively, so it is ours before ffmpeg writes it.

    ffmpeg's own `-n` ("do not overwrite") exits 0 when the file exists - it
    prints "already exists. Exiting." and writes nothing (measured, ffmpeg 6.1)
    - so its exit code cannot say whether it did the work. Claiming the name
    first makes "it exists" an error here, and means a failure can only ever
    remove a file this call made.
    """
    try:
        with open(target, "x"):
            pass
    except FileExistsError as exc:
        raise RecordingError(f"{target} already exists") from exc


def clean(src: Path, target: Path) -> None:
    """`target` with the cleaned sound; a video keeps its picture untouched."""
    is_video = src.suffix.lower() in VIDEO
    claim(target)
    args = [_ffmpeg(), "-v", "error", "-nostdin", "-y", "-i", str(src), "-af", CLEAN_FILTER]
    if is_video:
        args += ["-c:v", "copy", "-c:a", "aac", "-b:a", "160k"]
    proc = subprocess.run(args + [str(target)], capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0:
        target.unlink(missing_ok=True)
        raise RecordingError(f"ffmpeg could not clean {src.name}: {proc.stderr.strip()[-200:]}")


def segments_for(src: Path, language: str = "auto", with_speakers: bool = False,
                 speakers_count: Optional[int] = None) -> "tuple[List[Segment], str]":
    """Timed segments for any audio or video file, and a note (cut short, no speaker model...)."""
    notes = []
    length = duration(src)
    if length > MAX_SECONDS:
        notes.append(f"only the first {MAX_SECONDS // 60} minutes were used")
    with tempfile.TemporaryDirectory(prefix="chronoa-rec-") as tmp:
        wav = Path(tmp) / "audio.wav"
        extract_audio(src, wav)
        segments = transcribe(wav, language)
        if with_speakers:
            from shani_chronoa import speakers
            problem = speakers.problem()
            if problem:
                notes.append(problem)
            else:
                segments = assign_speakers(segments, speakers.diarize(wav, speakers_count))
    return segments, "; ".join(notes)


#: a YouTube link: watch, youtu.be and shorts forms, nothing else
_YOUTUBE = re.compile(r"^https://(?:(?:www\.|m\.)?youtube\.com/(?:watch\?v=|shorts/)|youtu\.be/)[A-Za-z0-9_-]{6,20}(?:[&?#][^\s]*)?$")


def is_youtube(text: str) -> bool:
    return bool(_YOUTUBE.match((text or "").strip()))


def youtube_captions(url: str, language: str = "en") -> "tuple[str, str]":
    """(title, caption text) for a YouTube video, via yt-dlp's subtitles - the video itself is never downloaded.

    Adopted from Alpaca's "attach YouTube captions", done with a system tool
    (yt-dlp, in Arch's extra) instead of a Python package. The person's own
    uploaded captions are preferred; YouTube's automatic ones otherwise.
    """
    if not is_youtube(url):
        raise RecordingError("that is not a YouTube link")
    if not shutil.which("yt-dlp"):
        raise RecordingError("yt-dlp is not installed (sudo pacman -S yt-dlp)")
    lang = language if language and language != "auto" else "en"
    with tempfile.TemporaryDirectory(prefix="chronoa-yt-") as tmp:
        proc = subprocess.run(["yt-dlp", "--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs",
                               # the real tracks only: "en.*" also matches YouTube's machine-translated
                               # ones ("en-de"...), each a separate request, and YouTube answers 429
                               ",".join([lang, f"{lang}-orig"] + ([f"{lang}-US", f"{lang}-GB"] if lang == "en" else [])), "--sub-format", "vtt", "--no-playlist", "--ignore-no-formats-error", "--print", "title",
                               "--no-simulate", "-o", f"{tmp}/v.%(ext)s", "--", url.strip()],
                              capture_output=True, text=True, timeout=180)
        subs = sorted(Path(tmp).glob("*.vtt"))
        if not subs:
            last = (proc.stderr.strip().splitlines() or ["no captions"])[-1][:200]
            raise RecordingError(f"no {lang} captions for that video ({last})")
        title = (proc.stdout.strip().splitlines() or ["YouTube video"])[0]
        return title, _vtt_text(subs[0].read_text(encoding="utf-8", errors="replace"))


def _vtt_text(vtt: str) -> str:
    """Caption text from WebVTT, without timings, tags or the rolling repeats automatic captions have."""
    lines, last = [], ""
    for line in vtt.splitlines():
        line = line.strip()
        if not line or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")) or "-->" in line or line.isdigit():
            continue
        line = re.sub(r"<[^>]+>", "", line).strip()
        if line and line != last:
            lines.append(line)
            last = line
    return " ".join(lines)
