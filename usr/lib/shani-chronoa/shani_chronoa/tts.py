"""Text-to-speech module using Piper TTS.

Provides local text-to-speech synthesis using Piper voice models.
"""

import logging
import subprocess
import os
import tempfile
from typing import Optional

logger = logging.getLogger(__name__)


class PiperTTS:
    """Text-to-speech using Piper."""

    def __init__(self, voice: str = "en_US-lessac-medium", piper_path: str = "/usr/bin/piper") -> None:
        self.voice = voice
        self.piper_path = piper_path
        self.voice_path = self._get_voice_path(voice)

    def _get_voice_path(self, voice: str) -> str:
        """Get the path to the Piper voice model file."""
        voice_dir = os.path.expanduser("~/.local/share/piper/voices")
        voice_file = f"{voice}.onnx"
        path = os.path.join(voice_dir, voice_file)
        if not os.path.exists(path):
            # Try system-wide location
            path = f"/usr/share/piper/voices/{voice_file}"
        return path

    def synthesize(self, text: str, output_file: str) -> bool:
        """Synthesize text to speech.

        Args:
            text: The text to synthesize
            output_file: Path to output audio file (WAV or PCM)

        Returns:
            True if synthesis succeeded
        """
        if not os.path.exists(self.voice_path):
            logger.error(f"Piper voice model not found: {self.voice_path}")
            return False

        try:
            cmd = [
                self.piper_path,
                "--model", self.voice_path,
                "--output_file", output_file,
            ]

            # Pipe text to piper via stdin
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            stdout, stderr = process.communicate(input=text.encode("utf-8"), timeout=60)

            if process.returncode != 0:
                logger.error(f"Piper synthesis failed: {stderr.decode()}")
                return False

            return os.path.exists(output_file)

        except subprocess.TimeoutExpired:
            logger.error("Piper synthesis timed out")
            return False
        except Exception as e:
            logger.error(f"Piper synthesis error: {e}")
            return False

    def synthesize_to_bytes(self, text: str) -> bytes:
        """Synthesize text to audio bytes.

        Args:
            text: The text to synthesize

        Returns:
            Audio bytes (WAV format)
        """
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            if self.synthesize(text, tmp_path):
                with open(tmp_path, "rb") as f:
                    return f.read()
            return b""
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def list_voices(self) -> list[dict]:
        """List available Piper voices."""
        voices_dir = "/usr/share/piper/voices"
        if not os.path.exists(voices_dir):
            return []

        voices = []
        for f in os.listdir(voices_dir):
            if f.endswith(".onnx"):
                voice_name = f.replace(".onnx", "")
                voices.append({"name": voice_name, "file": f})
        return voices

    def is_available(self) -> bool:
        """Check if Piper TTS is available."""
        return os.path.exists(self.piper_path) and os.path.exists(self.voice_path)
