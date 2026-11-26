"""Voice: speech engines, audio devices and the speech catalogue, as data.

Every row is read from the module that would actually speak, listen or play.
Each probe is guarded on its own: a missing binary is a row that says
``installed: False``, and a probe that raises is a row that says why - neither
takes the other rows down with it. Nothing here starts an engine, plays audio
or writes a setting.
"""

from __future__ import annotations

import logging
import os
import shutil

from shani_chronoa import audio as _audio
from shani_chronoa import pipewire as _pipewire
from shani_chronoa import stt as _stt
from shani_chronoa import tts as _tts

logger = logging.getLogger(__name__)


def _clip(text, limit: int = 200) -> str:
    flat = " ".join(str(text if text is not None else "").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def _row(name: str, probe, when_off: str) -> dict:
    """One engine row. An unreadable or missing engine is `installed: False`."""
    try:
        installed, detail = probe()
        installed = bool(installed)
    except Exception as exc:  # noqa: BLE001 - a failed probe is not a crash
        logger.debug("voice probe for %s failed", name, exc_info=True)
        return {
            "name": name,
            "installed": False,
            "detail": f"not installed - the check failed ({_clip(exc)})",
        }
    return {
        "name": name,
        "installed": installed,
        "detail": _clip(detail or ("" if installed else when_off)),
    }


def _dispatcher():
    try:
        return _tts.PiperTTS(), ""
    except Exception as exc:  # noqa: BLE001
        logger.debug("could not build the speech dispatcher", exc_info=True)
        return None, _clip(exc)


def _kokoro(dispatcher):
    if dispatcher is None:
        return False, "not installed - the speech chain could not be built"
    problem = str(dispatcher.kokoro_problem() or "")
    return (
        not problem,
        "installed and able to speak" if not problem else f"not in use - {problem}",
    )


def _piper(dispatcher):
    binary = str(getattr(dispatcher, "piper_path", "") or "") if dispatcher else ""
    binary = (
        binary
        if (binary and os.path.exists(binary))
        else (shutil.which("piper-tts") or "")
    )
    return bool(binary), (
        f"installed - {binary}"
        if binary
        else "not installed - piper-tts is not on this computer"
    )


def _rhvoice():
    binary = shutil.which("RHVoice-test")
    has_voice = bool(_tts.PiperTTS._rhvoice_has_voice()) if binary else False
    return has_voice and bool(binary), (
        f"installed - {binary}"
        if binary
        else "not installed - RHVoice-test is not on this computer"
    )


def _espeak():
    binary = shutil.which("espeak-ng")
    return bool(binary), (
        f"installed - {binary}. The floor speech always has"
        if binary
        else "not installed - espeak-ng is not on this computer"
    )


def _cloud(config):
    from shani_chronoa import cloud_voice

    refusal = str(cloud_voice.CloudTTS(config=config).refusal() or "")
    return not refusal, (
        "available as a last resort" if not refusal else f"not in use - {refusal}"
    )


def _whisper():
    model = _stt.installed_model("base")
    engine = _stt.WhisperSTT(model=model)
    ready = bool(engine.is_available())
    return ready, (
        f"whisper.cpp can transcribe with the {model} model"
        if ready
        else "not installed - no whisper.cpp binary or model file is usable here"
    )


def _parakeet():
    binary = shutil.which("parakeet-cli")
    return bool(binary), (
        f"installed - {binary}"
        if binary
        else "not installed - parakeet-cli is not on this computer"
    )


def _capture():
    ready = bool(_audio.AudioRecorder().is_available())
    return ready, (
        "a recording backend is installed"
        if ready
        else "not installed - no pw-record or arecord on this computer"
    )


def _playback():
    ready = bool(_audio.AudioPlayer().is_available())
    return ready, (
        "a playback backend is installed"
        if ready
        else "not installed - no pw-play or aplay on this computer"
    )


def _cloud_row(config) -> dict:
    """A cloud row that never waits on the keyring to say it is not in use."""
    from shani_chronoa.engine_ops.models import NOT_CHECKED, bounded

    try:
        answer = bounded(lambda: _cloud(config), reraise=True)
    except Exception as exc:  # noqa: BLE001 - a failed probe is "not installed", like every row
        def failed(exc=exc):
            raise exc
        return _row("cloud", failed, "the cloud speech provider is not available")
    if answer is NOT_CHECKED:
        return {
            "name": "cloud",
            "installed": False,
            "detail": "not checked - the keyring did not answer quickly",
        }
    return _row("cloud", lambda: answer, "the cloud speech provider is not available")


def _engine_rows(config):
    dispatcher, _problem = _dispatcher()
    chosen = ""
    if dispatcher is not None:
        try:
            chosen = str(dispatcher.engine() or "")
        except Exception:  # noqa: BLE001 - an unknown choice is not a row's business
            logger.debug("could not resolve the speaking engine", exc_info=True)
    rows = [
        _row("kokoro", lambda: _kokoro(dispatcher), "Kokoro cannot speak here"),
        _row("piper", lambda: _piper(dispatcher), "Piper is not installed"),
        _row("rhvoice", _rhvoice, "RHVoice is not installed"),
        _row("espeak-ng", _espeak, "espeak-ng is not on this computer"),
        _cloud_row(config),
        _row("whisper", _whisper, "speech input is not available"),
        _row("parakeet", _parakeet, "Parakeet is not installed"),
        _row("capture", _capture, "no recording backend is installed"),
        _row("playback", _playback, "no playback backend is installed"),
    ]
    if chosen:
        for row in rows:
            if row["name"] == chosen:
                row["detail"] += ". This is the engine that would speak the next reply"
    return rows


def _device_rows() -> dict:
    out: dict[str, list[dict]] = {"input": [], "output": []}
    for kind, getter in (
        ("input", _pipewire.list_inputs),
        ("output", _pipewire.list_outputs),
    ):
        try:
            found = getter()
        except Exception:  # noqa: BLE001 - no graph is an empty list, not a crash
            logger.debug("could not list %s devices", kind, exc_info=True)
            continue
        for device in found:
            try:
                name = str(getattr(device, "name", "") or "")
                label = str(
                    getattr(device, "label", "")
                    or getattr(device, "description", "")
                    or name
                )
                description = str(getattr(device, "description", "") or "")
            except Exception:  # noqa: BLE001
                continue
            out[kind].append(
                {"name": name, "label": label, "description": description, "kind": kind}
            )
    return out


def _catalogue_rows() -> list[dict]:
    """Speech models setup can install. setup_wizard is GTK, so import it lazily."""
    try:
        from shani_chronoa import setup_wizard

        rows = setup_wizard.stt_catalogue()
    except Exception:  # noqa: BLE001 - a missing catalog is an empty one
        logger.debug("could not read the speech catalogue", exc_info=True)
        return []
    out = []
    for entry in rows:
        if isinstance(entry, dict):
            out.append(
                {
                    "key": str(entry.get("key", "") or ""),
                    "label": str(entry.get("label", "") or ""),
                    "detail": str(entry.get("detail", entry.get("subtitle", "")) or ""),
                    "engine": str(entry.get("engine", "") or ""),
                }
            )
            continue
        parts = list(entry) if isinstance(entry, (list, tuple)) else []
        key, label, detail, engine = (parts + ["", "", "", ""])[:4]
        out.append(
            {
                "key": str(key),
                "label": str(label),
                "detail": str(detail),
                "engine": str(engine),
            }
        )
    return out


def engines(config=None) -> dict:
    """Everything the Voice panel shows: engines, devices, and install catalogue."""
    try:
        catalogue = _catalogue_rows()
    except Exception:  # noqa: BLE001 - an unreadable catalog is an empty one
        logger.debug("could not read the speech catalogue", exc_info=True)
        catalogue = []
    return {
        "engines": _engine_rows(config),
        "devices": _device_rows(),
        "catalogue": catalogue,
    }
