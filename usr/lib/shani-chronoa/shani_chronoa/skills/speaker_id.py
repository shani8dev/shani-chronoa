"""Speaker identification: opt-in, per-person, and entirely on this machine.

`recording action=speakers` already tells turn 1 from turn 2 with sherpa-onnx's
diarization model (`speakers.py`). Those labels are local to one recording:
"Speaker 1" on Monday has no relationship to "Speaker 1" on Tuesday, and no
person's name is attached to either. This module adds the smallest useful
identity layer on top of that model:

- **enrol** a person with a short WAV, their name and the basis on which they
  agreed, storing a private copy of the clip and its digest;
- **identify** a later clip by joining it to the enrolled references and asking
  the *same* diarizer whether they fall in the same speaker cluster;
- **forget** a person, removing their clip and their consent record.

There is no new model, no embedding service and no upload. The model is the one
`speakers.py` already downloads and verifies; the comparison is a local
subprocess over a WAV built in a private temporary directory. **No audio,
embedding or name is sent anywhere** - the module imports no network client at
all, and a test checks that.

Two gates, both required. The outer one is the existing `hearing-sense-enabled`
switch (off by default): a voice is the hearing sense's own data class, so a
second switch would let a person permit one and be refused by the other. The
inner one is the per-person record: a name, who the voice belongs to, and the
basis on which they agreed, bound to the SHA-256 of the clip. An enrolment with
no basis is not a consent record, and a stored clip whose digest no longer
matches is refused rather than silently identifying somebody else. Enrol and
forget also ask for an explicit confirmation through `approvals.confirm`, so a
name cannot be enrolled by a tool call nobody watched.

This is a convenience check, not forensic speaker verification: the diarizer
clusters turns, and a person who sounds like somebody else may share a cluster.
The answer says so rather than quoting a confidence it never measured.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
import wave
from pathlib import Path

from shani_chronoa import approvals, files, speakers
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

#: The existing consent for a person's voice. A second, new key would need a
#: Settings row the UI agents own and would give two switches for one act.
CONSENT_KEY = "hearing-sense-enabled"

MAX_CLIP_BYTES = 64 * 1024 * 1024
MIN_CLIP_SECONDS = 1.0
MAX_CLIP_SECONDS = 120.0
_RATE = 16000
_WIDTH = 2
_GAP_SECONDS = 0.25

_ACTIONS = ("enrol", "identify", "list", "forget")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "speaker_id",
        "description": (
            "Opt-in voice identity using the local speaker model. enrol: remember a person's voice "
            "from a 16 kHz mono WAV, with their name and the basis on which they agreed; identify: "
            "say which enrolled voice a WAV sounds like; list: names already enrolled; forget: "
            "remove one. Needs the hearing consent and an explicit yes for enrol and forget. "
            "Nothing about the voice leaves this machine."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(_ACTIONS)},
                "name": {"type": "string", "description": "The person's name."},
                "clip": {"type": "string", "description": "A 16 kHz mono WAV file."},
                "basis": {
                    "type": "string",
                    "description": "enrol: how they agreed (e.g. 'they recorded it and agreed').",
                },
            },
            "required": ["action"],
        },
    },
}


def _store_dir() -> Path:
    return files.data_home() / "shani-chronoa" / "speaker-id"


def _registry_path() -> Path:
    return _store_dir() / "identities.json"


def _clip_path(name: str) -> Path:
    tag = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
    return _store_dir() / "clips" / f"{tag}.wav"


def _slug(name: str) -> str:
    """A readable, safe file hint; the digest in `_clip_path` is the real key."""
    return re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-")[:32] or "voice"


def _consent(config: ChronoaConfig | None = None) -> tuple[bool, str]:
    from shani_chronoa import capabilities

    cfg = config or ChronoaConfig()
    if not cfg.get_bool(CONSENT_KEY, False):
        return False, (
            f"a person's voice is off - turn on "
            f"\u201c{capabilities.gate_label(CONSENT_KEY)}\u201d in Settings"
        )
    return True, ""


def _frames_digest(path: Path) -> str:
    """Hash the PCM frames, not the WAV header.

    Both ends must hash the same thing: a stored copy is written through
    `wave`, which can lay out its header differently from the original file
    while carrying exactly the same audio. Hashing whole files here made every
    identify refuse its own enrolment, which is the wrong direction to fail.
    """
    return hashlib.sha256(_wav_frames(path)).hexdigest()


def _read_registry() -> dict:
    """Every enrolment, or `{}` for an absent/unreadable registry.

    A corrupt registry authorises nothing; it is never treated as an empty
    invitation to overwrite the evidence somebody needs to see.
    """
    path = _registry_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items() if isinstance(v, dict)}


def _save_registry(records: dict) -> None:
    """Atomic, 0600 in a 0700 directory: a voice clip is biometric data."""
    path = _registry_path()
    files.ensure_private_dir(path.parent)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(records, indent=2, sort_keys=True), encoding="utf-8")
        files.restrict_file(tmp)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _wav_frames(path: Path) -> bytes:
    """Read a usable clip, or raise ValueError naming what is wrong with it."""
    if not path.is_file():
        raise ValueError(f"no WAV at {path}")
    if path.stat().st_size > MAX_CLIP_BYTES:
        raise ValueError(f"{path.name} is too large to be a voice clip")
    try:
        with wave.open(str(path), "rb") as handle:
            if (
                handle.getnchannels() != 1
                or handle.getsampwidth() != _WIDTH
                or handle.getframerate() != _RATE
            ):
                raise ValueError("a clip must be 16 kHz mono 16-bit WAV")
            frames = handle.readframes(handle.getnframes())
    except (OSError, EOFError, wave.Error) as exc:
        raise ValueError(f"could not read {path.name} as WAV: {exc}") from exc
    seconds = len(frames) / (_RATE * _WIDTH)
    if seconds < MIN_CLIP_SECONDS:
        raise ValueError(
            f"{path.name} is under {MIN_CLIP_SECONDS:g}s - too short to compare"
        )
    if seconds > MAX_CLIP_SECONDS:
        raise ValueError(
            f"{path.name} is over {MAX_CLIP_SECONDS:g}s - use a short clip"
        )
    return frames


def _write_wav(target: Path, frames: bytes) -> None:
    with wave.open(str(target), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(_WIDTH)
        handle.setframerate(_RATE)
        handle.writeframes(frames)


def _join(
    references: dict[str, Path], unknown: Path, target: Path
) -> list[tuple[float, float, str]]:
    """Build one WAV: each reference, a gap, then the unknown clip last.

    Returns `(start, end, name_or_'')` spans so the comparison needs no
    guessing about where a person's sample ends and the question begins.
    The name comes from the caller's mapping, not from the file's folder:
    every enrolled clip lives in the same private `clips/` directory.
    """
    silence = b"\x00\x00" * int(_RATE * _GAP_SECONDS)
    spans = []
    chunks = []
    at = 0.0
    for who, ref in references.items():
        frames = _wav_frames(ref)
        end = at + len(frames) / (_RATE * _WIDTH)
        spans.append((at, end, who))
        chunks.extend((frames, silence))
        at = end + _GAP_SECONDS
    frames = _wav_frames(unknown)
    end = at + len(frames) / (_RATE * _WIDTH)
    spans.append((at, end, ""))
    chunks.append(frames)
    _write_wav(target, b"".join(chunks))
    return spans


def compare(
    references: dict[str, Path], unknown: Path, diarize=None
) -> tuple[str, str, bool]:
    """`(best name, explanation, speech was heard)` from local diarization.

    `diarize` is a test seam: the shipped path uses `speakers.diarize` over a
    WAV in a private temporary directory. Neither route touches a network.
    """
    if not references:
        return "", "nobody is enrolled yet", False
    if diarize is None:
        diarize = speakers.diarize
    with tempfile.TemporaryDirectory(prefix="chronoa-speaker-id-") as folder:
        joined = Path(folder) / "compare.wav"
        spans = _join(references, unknown, joined)
        turns = diarize(joined, count=len(references) + 1)

    def speaker_at(moment: float) -> str:
        for start, end, label in turns:
            if start - 0.05 <= moment <= end + 0.05:
                return label
        return ""

    named = {}
    for start, end, who in spans:
        if who:
            label = speaker_at((start + end) / 2)
            if label:
                named.setdefault(who, label)
    if not named:
        return "", "the local diarizer heard no enrolled turn in that clip", False

    # The unknown clip is last. Its dominant speaker is the one with the most
    # overlap in its own span, not the one with the most speech overall.
    unknown_span = spans[-1]
    weights: dict[str, float] = {}
    for start, end, label in turns:
        lo, hi = max(start, unknown_span[0]), min(end, unknown_span[1])
        if hi > lo:
            weights[label] = weights.get(label, 0.0) + (hi - lo)
    if not weights:
        return "", "the clip had no discernible speech", False
    best = max(weights, key=weights.get)
    hits = [name for name, label in named.items() if label == best]
    if not hits:
        return "", "none of the enrolled voices shared a speaker cluster with it", True
    return hits[0], (
        "local diarization put this clip in the same speaker cluster "
        "as the enrolled reference"
        if len(hits) == 1
        else "several enrolled references share that cluster"
    ), True


def _run(arguments: dict) -> str:
    args = arguments if isinstance(arguments, dict) else {}
    action = str(args.get("action") or "").strip().lower()
    allowed, why = _consent()
    if not allowed:
        return f"Refusing: {why}."
    if action not in _ACTIONS:
        return f"action must be one of {', '.join(_ACTIONS)}."

    records = _read_registry()

    if action == "list":
        if not records:
            return "Nobody is enrolled for speaker identification."
        names = ", ".join(sorted(records))
        return f"Enrolled: {names}. Clips stay on this machine; nothing was sent."

    name = str(args.get("name") or "").strip()
    if action == "enrol":
        basis = str(args.get("basis") or "").strip()
        if not name:
            return "Give the person's name."
        if not basis:
            return (
                "Give the basis on which they agreed - a consent record "
                "nobody can review is not a consent record."
            )
        ok, reason = approvals.confirm(
            "Enrol a voice for speaker identification",
            f"Remember {name}'s voice from the clip you named, agreed on this basis: "
            f"{basis[:200]}. The audio stays on this machine. Allow once?",
        )
        if not ok:
            return f"Nothing was enrolled: {reason}."
        try:
            source = files.resolve_in_home(str(args.get("clip") or ""))
            frames = _wav_frames(source)
        except (files.PathProblem, ValueError) as exc:
            return str(exc)
        digest = hashlib.sha256(frames).hexdigest()  # same bytes _frames_digest reads back
        target = _clip_path(name)
        files.ensure_private_dir(target.parent)
        try:
            _write_wav(target, frames)
            files.restrict_file(target)
        except OSError as exc:
            return f"Could not store the clip privately: {exc}"
        records[name] = {
            "speaker": name,
            "basis": basis,
            "digest": digest,
            "clip": str(target),
            "file_hint": _slug(name),
            "registered_at": time.time(),
        }
        _save_registry(records)
        return (
            f"Enrolled {name}. Their clip is stored owner-only on this machine "
            f"and the agreement records: {basis[:200]}."
        )

    if action == "forget":
        if not name:
            return "Give the name to forget."
        if name not in records:
            return f"No one called {name} is enrolled."
        ok, reason = approvals.confirm(
            "Forget an enrolled voice",
            f"Delete the stored voice clip and consent record for {name}? Allow once?",
        )
        if not ok:
            return f"Nothing was removed: {reason}."
        try:
            _clip_path(name).unlink(missing_ok=True)
        except OSError as exc:
            return f"Could not remove the stored clip: {exc}"
        records.pop(name, None)
        _save_registry(records)
        return f"Forgot {name}. The local clip and consent record are gone."

    # identify
    if not speakers.installed():
        return (
            "Telling voices apart is not set up - Chronoa's setup can install "
            "the local speaker model (More -> Who said what)."
        )
    try:
        unknown = files.resolve_in_home(str(args.get("clip") or ""))
    except files.PathProblem as exc:
        return str(exc)
    references: dict[str, Path] = {}
    for who, record in records.items():
        path = Path(str(record.get("clip") or ""))
        if not path.is_file():
            return (
                f"The stored clip for {who} is missing. Forget and re-enrol "
                "them rather than identifying against a guess."
            )
        try:
            if _frames_digest(path) != record.get("digest"):
                return (
                    f"The stored clip for {who} no longer matches its consent "
                    "record. Forget and re-enrol them before using it."
                )
        except (OSError, ValueError) as exc:
            return (
                f"The stored clip for {who} is no longer readable ({exc}). "
                "Forget and re-enrol them before using it."
            )
        references[who] = path
    if name and name not in references:
        return f"No one called {name} is enrolled."
    if name:
        references = {name: references[name]}
    ok, reason = approvals.confirm(
        "Identify a voice against enrolled people",
        f"Compare a local WAV with the enrolled voice(s) {', '.join(sorted(references))}? "
        "The audio and model stay on this machine. Allow once?",
    )
    if not ok:
        return f"Nothing was compared: {reason}."
    try:
        best, explanation, heard = compare(references, unknown)
    except (ValueError, OSError, RuntimeError) as exc:
        return f"Could not compare the clip: {exc}"
    if not best and not heard:
        return f"No enrolled voice matched. {explanation.capitalize()}."
    if name:
        verb = "matches" if best == name else "does not match"
        detail = explanation if best == name else "it fell in a different local speaker cluster"
        return f"The clip {verb} {name} ({detail}). This is local diarization, not proof of identity."
    return f"Closest enrolled voice: {best} ({explanation}). This is local diarization, not proof of identity."


SKILLS = [Skill(name="speaker_id", schema=SCHEMA, run=_run)]
