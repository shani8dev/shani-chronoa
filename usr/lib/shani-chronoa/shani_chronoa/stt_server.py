"""Keep the speech model loaded: whisper.cpp's `whisper-server` as a child of Chronoa, on 127.0.0.1.

`whisper-cli` loads the model from disk for every utterance; assistd keeps one
whisper context resident for the same reason (`whisper.rs:86`). Arch's
whisper-cpp ships `whisper-server`, so the first transcription starts it with
the same model and language, and later ones are an HTTP POST of the WAV.
Started lazily, one per model, stopped when Chronoa exits; anything going
wrong falls back to `whisper-cli`, so speech input never depends on it.
"""

from __future__ import annotations

import atexit
import logging
import shutil
import subprocess
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

HOST, PORT = "127.0.0.1", 8766
_LOCK = threading.Lock()
_STATE: dict = {"proc": None, "model": "", "language": ""}
STARTUP_SECONDS = 30.0


def binary() -> str:
    return shutil.which("whisper-server") or ""


def _up(timeout: float = 1.0) -> bool:
    import httpx
    try:
        return httpx.get(f"http://{HOST}:{PORT}/", timeout=timeout).status_code == 200
    except httpx.HTTPError:
        return False


def stop() -> None:
    proc = _STATE.get("proc")
    _STATE.update(proc=None, model="", language="")
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


atexit.register(stop)


def ensure(model_path: str, language: str = "en") -> bool:
    """A server for this model is answering (starting one if needed); False to fall back to the CLI."""
    with _LOCK:
        proc = _STATE.get("proc")
        if proc is not None and proc.poll() is None and _STATE["model"] == model_path \
                and _STATE["language"] == language:
            return True
        stop()
        if not binary():
            return False
        if _up(0.3):  # the port is someone else's; do not talk to a server we did not start
            logger.warning("Port %d is already in use; using whisper-cli", PORT)
            return False
        argv = [binary(), "-m", model_path, "--host", HOST, "--port", str(PORT), "-l", language,
                "--prompt", "Chronoa.", "-nt"]
        try:
            proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            logger.warning("Could not start whisper-server: %s", exc)
            return False
        deadline = time.monotonic() + STARTUP_SECONDS
        while time.monotonic() < deadline and proc.poll() is None:
            if _up(0.5):
                _STATE.update(proc=proc, model=model_path, language=language)
                logger.info("whisper-server is holding %s", model_path)
                return True
            time.sleep(0.2)
        proc.kill()
        logger.warning("whisper-server did not come up; using whisper-cli")
        return False


def transcribe(audio_file: str) -> Optional[str]:
    """The transcript from the running server, or None to fall back (never raises)."""
    import httpx
    try:
        with open(audio_file, "rb") as handle:
            r = httpx.post(f"http://{HOST}:{PORT}/inference", timeout=120,
                           files={"file": ("audio.wav", handle, "audio/wav")},
                           data={"response_format": "text", "temperature": "0.0"})
        if r.status_code != 200:
            return None
        return r.text.strip()
    except (OSError, httpx.HTTPError):
        return None
