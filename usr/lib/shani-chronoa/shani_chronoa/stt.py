"""Speech-to-text module using whisper.cpp.

Provides local speech recognition using whisper.cpp models. A second backend,
NVIDIA Parakeet through the same `whisper-cpp` package, lives in
`stt_parakeet.py`; `build_stt` below is the one place that chooses between
them.
"""

import logging
import subprocess
import os
import shutil
import tempfile
from typing import Optional, Union

from shani_chronoa import files

logger = logging.getLogger(__name__)

#: The STT backends a user may select, in the spelling the `stt-backend`
#: gsetting uses. Anything else falls back to whisper.
BACKEND_WHISPER = "whisper"
BACKEND_PARAKEET = "parakeet"
BACKENDS = (BACKEND_WHISPER, BACKEND_PARAKEET)

STT = Union["WhisperSTT", "ParakeetSTT"]


def build_stt(model: str, language: str = "en", backend: str = "") -> STT:
    """Construct the STT for `backend`, defaulting to whisper.cpp.

    `WhisperSTT` is the default and stays the fallback: this backend is opt-in
    and a missing, blank or misspelled `stt-backend` must not change what
    Chronoa does today. Nothing here raises - the caller gets a real object
    either way and asks `is_available()`, which is the honest question.

    The two classes are interchangeable by construction (identical public
    surface), which is what makes this a selection rather than a branch every
    caller has to write.
    """
    if (backend or "").strip().lower() == BACKEND_PARAKEET:
        from shani_chronoa.stt_parakeet import ParakeetSTT
        # The imported lazily and inside the branch so that importing `stt`
        # does not drag in the Parakeet module for the default backend, and so
        # that a broken optional dependency cannot break speech input
        # wholesale. `stt_parakeet` imports nothing from this module, so the
        # cycle cannot form.
        return ParakeetSTT(model=model, language=language)
    if backend and (backend or "").strip().lower() not in BACKENDS:
        logger.warning(
            "Unknown STT backend %r; using %s. Valid values: %s",
            backend, BACKEND_WHISPER, ", ".join(BACKENDS),
        )
    return WhisperSTT(model=model, language=language)


class WhisperSTT:
    """Speech-to-text using whisper.cpp."""

    def __init__(self, model: str = "base", whisper_path: Optional[str] = None, language: str = "en") -> None:
        self.model = model
        # Arch's whisper-cpp installs whisper-cli; "whisper.cpp" was never a
        # binary name there, so speech input could not work on Shanios
        self.whisper_path = whisper_path or shutil.which("whisper-cli") or shutil.which("whisper.cpp") \
            or "/usr/bin/whisper-cli"
        self.model_path = self._get_model_path(model)
        self.language = language

    def _get_model_path(self, model: str) -> str:
        """Get the path to the whisper.cpp model file."""
        # whisper.cpp publishes its models as ggml-<model>.bin; <model>.bin
        # kept for files named by hand.
        #
        # The quantized names come after the exact ones, deliberately: the
        # full-precision ggml-base.bin is ~148 MB and the q5_1 build of the same
        # model is ~57 MB, so a first-use download installs the small one. Without
        # these, `stt_provision` would report success and write a file this
        # function never looks for, leaving speech input silently dead.
        dirs = (files.data_home() / "whisper" / "models", "/usr/share/whisper/models")
        names = (
            f"ggml-{model}.bin",
            f"{model}.bin",
            f"ggml-{model}-q5_1.bin",
            f"ggml-{model}-q8_0.bin",
            f"ggml-{model}-q5_k.bin",
        )
        for d in dirs:
            for n in names:
                if os.path.exists(os.path.join(d, n)):
                    return os.path.join(d, n)
        return os.path.join(dirs[0], names[0])

    def transcribe(self, audio_file: str) -> str:
        """Transcribe audio file to text.

        Args:
            audio_file: Path to the audio file (WAV, MP3, etc.)

        Returns:
            Transcribed text
        """
        if not os.path.exists(audio_file):
            raise FileNotFoundError(f"Audio file not found: {audio_file}")

        if not os.path.exists(self.model_path):
            raise FileNotFoundError(f"Whisper model not found: {self.model_path}")

        try:
            cmd = [
                self.whisper_path,
                "-m", self.model_path,
                "-f", audio_file,
                "-l", self.language,  # config.py's "language" gsetting was never wired anywhere before this
                "-otxt",
                "-np",  # No progress
            ]
            result = subprocess.run(
                cmd,
                capture_output=True, text=True, timeout=300
            )
            if result.returncode != 0:
                logger.error(f"Whisper transcription failed: {result.stderr}")
                return ""

            # Read the output text file
            txt_file = audio_file.rsplit(".", 1)[0] + ".txt"
            if os.path.exists(txt_file):
                with open(txt_file) as f:
                    text = f.read().strip()
                os.remove(txt_file)
                return text
            return result.stdout.strip()

        except subprocess.TimeoutExpired:
            logger.error("Whisper transcription timed out")
            return ""
        except Exception as e:
            logger.error(f"Whisper transcription error: {e}")
            return ""

    def transcribe_stream(self, audio_data: bytes) -> str:
        """Transcribe audio bytes to text (streaming mode).

        Args:
            audio_data: Raw audio bytes

        Returns:
            Transcribed text
        """
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(audio_data)
            tmp_path = tmp.name

        try:
            text = self.transcribe(tmp_path)
        finally:
            os.unlink(tmp_path)

        return text

    def is_available(self) -> bool:
        """Check if whisper.cpp is available."""
        return os.path.exists(self.whisper_path) and os.path.exists(self.model_path)
