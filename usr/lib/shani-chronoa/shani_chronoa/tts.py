"""Text-to-speech module using Piper TTS.

Provides local text-to-speech synthesis using Piper voice models.
"""

import logging
import subprocess
import os
import shutil
import tempfile
from typing import Optional

logger = logging.getLogger(__name__)


class PiperTTS:
    """Text-to-speech using Piper."""

    def __init__(self, voice: str = "en_US-lessac-medium", piper_path: Optional[str] = None) -> None:
        self.voice = voice
        # Only the TTS binary, installed as piper-tts: on Arch /usr/bin/piper
        # is the GTK gaming-mouse configurator (extra/piper), which the old
        # default would have launched with TTS arguments
        self.piper_path = piper_path or shutil.which("piper-tts") or "/usr/bin/piper-tts"
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

    # Engines, best first. Piper is not in the Arch repos (it needs
    # onnxruntime, which is not either); RHVoice (extra/rhvoice + a
    # rhvoice-language-* and rhvoice-voice-*) sounds natural; espeak-ng
    # ships in every Shanios image, so speech always works.
    def engine(self) -> Optional[str]:
        if os.path.exists(self.piper_path) and os.path.exists(self.voice_path):
            return "piper"
        if shutil.which("RHVoice-test") and self._rhvoice_has_voice():
            return "rhvoice"
        if shutil.which("espeak-ng"):
            return "espeak-ng"
        return None

    @staticmethod
    def _rhvoice_has_voice() -> bool:
        return any(os.path.isdir(d) and os.listdir(d)
                   for d in ("/usr/share/RHVoice/voices", "/usr/local/share/RHVoice/voices"))

    def _lang(self) -> str:
        """Piper voice id en_US-lessac-medium -> espeak language en-us."""
        return self.voice.split("-", 1)[0].replace("_", "-").lower() or "en"

    def synthesize(self, text: str, output_file: str) -> bool:
        """Synthesize text to a WAV file with the best available engine."""
        eng = self.engine()
        if eng == "piper":
            cmd = [self.piper_path, "--model", self.voice_path, "--output_file", output_file]
        elif eng == "rhvoice":
            cmd = ["RHVoice-test", "-o", output_file]
        elif eng == "espeak-ng":
            cmd = ["espeak-ng", "--stdin", "-v", self._lang(), "-w", output_file]
        else:
            logger.error("No speech engine: install rhvoice (+ a voice) or espeak-ng")
            return False
        try:
            process = subprocess.run(cmd, input=text.encode("utf-8"), capture_output=True, timeout=60)
        except subprocess.TimeoutExpired:
            logger.error("%s synthesis timed out", eng)
            return False
        except OSError as e:
            logger.error("%s synthesis error: %s", eng, e)
            return False
        if process.returncode != 0:
            logger.error("%s synthesis failed: %s", eng, process.stderr.decode(errors="replace"))
            return False
        return os.path.exists(output_file) and os.path.getsize(output_file) > 44  # > a bare WAV header

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
        """Some speech engine is installed (see engine())."""
        return self.engine() is not None
