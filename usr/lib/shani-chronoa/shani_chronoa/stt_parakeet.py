"""Speech-to-text using NVIDIA Parakeet through whisper.cpp's `parakeet-cli`.

An offline alternative to `WhisperSTT`, not a replacement. Parakeet
(`nvidia/parakeet-tdt-0.6b-v3`, CC-BY-4.0 upstream, MIT for the ggml
conversion) transcribes better than whisper.cpp at comparable sizes and its
`parakeet-cli` is already installed by Arch's `whisper-cpp`, so adding this
backend required no new package and no change to the PKGBUILD.

`WhisperSTT` is the default and stays it; this is selected explicitly. See
`stt.build_stt`, which is the only thing that should construct either class.

**Offline only, deliberately.** The *streaming* Parakeet variant
(`parakeet-tdt-0.6b-v3-streaming`) is published under a licence marked
"other" that nobody has resolved, so no streaming path, flag or documentation
of one exists here. `transcribe_stream()` exists only to satisfy the shared
interface, and does what `WhisperSTT`'s does: write the bytes to a WAV and
transcribe the file.

**Why stdout is filtered rather than trusted.** The documented output of
`parakeet-cli` interleaves the transcript with progress telemetry:

    Processing audio (176000 samples, 11.00 seconds)
    parakeet_decode: starting decode with n_frames=138
    And so, my fellow Americans, ask not what your country can do for you...

Storing that verbatim would put `parakeet_decode: starting decode with
n_frames=138` into a conversation as if the user had said it - a confident
wrong answer, which is the exact failure mode `stt_provision.py`'s module
docstring exists to prevent, reached by a different route.

Reading whisper.cpp's source, those lines currently go to *stderr*
(`parakeet_log_callback_default` uses `fputs(text, stderr)`, and the CLI's own
status lines are `fprintf(stderr, ...)`; only `token_callback` uses `printf`).
So on a current build the filter is defence in depth rather than a fix for a
live bug - and it stays, because the thing it defends is not the build we
tested against. A distro patch, a `PARAKEET_DEBUG` build, or a future
refactor that moves telemetry to stdout would otherwise put tool chatter into
the user's speech with no signal at all. The filter is deliberately narrow:
it drops only lines that cannot be speech (a C log-prefix, or a literal the
CLI's own source prints), because the alternative - guessing - would drop a
real transcript line.
"""

import logging
import os
import re
import shutil
import subprocess
import tempfile
from typing import Optional

from shani_chronoa import files

logger = logging.getLogger(__name__)

#: Same ceiling as `stt.py`. Parakeet is a bigger model than whisper's small
#: tier, so this is generous rather than tight - and the largest file offered
#: for download is well inside it.
_TIMEOUT = 300

#: Parakeet TDT v3, the offline model. The quantized spellings are tried after
#: the exact one for the same reason `WhisperSTT._get_model_path` orders its
#: names that way: an exact hit is what a distro or a hand-placed file uses,
#: and a first-use download installs a quantized build. `stt_provision`
#: writes only `ggml-parakeet-<model>-q4_0.bin` and `...-q8_0.bin`, and both
#: are on this list - a provisioner writing a name this function never looks
#: for is the bug that shipped once already (see `stt.py`'s own comment on it,
#: and `tests/test_stt_parakeet.py` for the cross-check that keeps the two
#: halves agreeing).
_QUANTIZATIONS = ("q4_0", "q8_0")

#: The offline Parakeet TDT v3 model, without a quantization suffix, so the
#: search below resolves to the q4_0 build unless a better one is present.
DEFAULT_MODEL = "tdt-0.6b-v3"

# Telemetry this module refuses to pass off as speech.
#
# The first pattern is the C library's own log-prefix convention
# (`parakeet_decode:`, and equally `parakeet_pcm_to_mel:`, `ggml_...:`). A
# transcript line cannot start with an identifier followed by a colon.
#
# The rest are literals printed by `examples/parakeet-cli/parakeet-cli.cpp`
# and the documented example output. `-ps/--print-segments` is never passed,
# so the segment dump is not expected - but two of these are cheap insurance
# against a build that prints it anyway, and neither can begin an English
# sentence.
# Each pattern below matches the telemetry's *full shape*, not a bare prefix.
# "Processing audio rooms are loud" is a sentence a person can say, and a
# filter that dropped it would be over-filtering: a lost word is still a wrong
# transcript. Every alternative requires punctuation or a number that the
# telemetry always has and a transcript practically cannot.
_PROGRESS_PATTERNS = (
    # `Processing audio (176000 samples, 11.00 seconds)` and
    # `Processing audio: total_frames=1101, chunk_size=1101`
    re.compile(r"^Processing audio\s*[(:]"),
    # `parakeet_decode: ...`, `parakeet_pcm_to_mel: ...`, `ggml_...: ...`
    re.compile(r"^(?:parakeet|whisper|ggml)_[a-z0-9_]*:"),
    # `Processing file: /tmp/x.wav`, `system_info: n_threads = 4 / 8 | ...`
    re.compile(r"^(?:Processing file|system_info|Loading Parakeet model from"
               r"|Successfully loaded Parakeet model|Output written to):"),
    # `Segments (1):`, `Segment 0: [0 -> 1101] "..."`, `Tokens [38]:`.
    # Only reachable with `-ps`, which is never passed; cheap insurance.
    re.compile(r"^(?:Segments \(\d+\):|Segment \d+:|Tokens \[\d+\]:)"),
)


def _is_progress_line(line: str) -> bool:
    """Whether `line` is tool output rather than something someone said."""
    return any(pattern.match(line) for pattern in _PROGRESS_PATTERNS)


def transcript_from(stdout: str) -> str:
    """The spoken text in `parakeet-cli`'s stdout, with telemetry removed.

    Public because it is the part worth testing directly: the filtering is the
    behaviour, and a test that has to reconstruct a subprocess to check it is
    a test that mostly asserts its own fixture.
    """
    return " ".join(
        line for line in (raw.strip() for raw in stdout.splitlines())
        if line and not _is_progress_line(line)
    ).strip()


class ParakeetSTT:
    """Speech-to-text using `parakeet-cli` from whisper.cpp.

    The public surface is deliberately identical to `WhisperSTT` -
    `model`/`whisper_path`/`language`, `transcribe`, `transcribe_stream`,
    `is_available` - so `stt.build_stt` can return either and callers cannot
    tell. `transcribe()` returns `""` on every failure rather than raising,
    except for a missing audio file or model, which `WhisperSTT` also raises.
    """

    def __init__(self, model: str = DEFAULT_MODEL, whisper_path: Optional[str] = None,
                 language: str = "en") -> None:
        self.model = model
        # Arch's whisper-cpp installs parakeet-cli at /usr/bin/parakeet-cli.
        # The literal fallback is not redundant the way a PATH lookup alone
        # would be: `senses/ocr.py` and `screengrab.py` both record that a
        # stripped PATH (a systemd unit, a sandbox) is a real environment here.
        self.whisper_path = whisper_path or shutil.which("parakeet-cli") \
            or "/usr/bin/parakeet-cli"
        self.model_path = self._get_model_path(model)
        # Recorded, never sent: `parakeet-cli` has no `-l` flag, and the model
        # is English-only. Storing it rather than dropping the parameter keeps
        # this class interchangeable with `WhisperSTT`; pretending to honour it
        # would be the dishonest kind of compatibility.
        self.language = language

    def _get_model_path(self, model: str) -> str:
        """Get the path to the Parakeet ggml model file.

        Two directories, and the order is a contract with `stt_provision`:
        the user's own copy first, then a system one. A third location invented
        here would make a provisioned model invisible to the code that reads
        it - which is precisely the failure `stt.py` documents for whisper.
        The system directory is `/usr/share/parakeet/models`, mirroring
        `/usr/share/whisper/models`, because no distro currently ships these
        files at all; it exists so a future packaging change has somewhere
        consistent to put them rather than so something can be found today.
        """
        dirs = (files.data_home() / "parakeet" / "models", "/usr/share/parakeet/models")
        names = [f"ggml-parakeet-{model}.bin", f"parakeet-{model}.bin", f"{model}.bin"]
        names += [f"ggml-parakeet-{model}-{q}.bin" for q in _QUANTIZATIONS]
        for d in dirs:
            for n in names:
                if os.path.exists(os.path.join(d, n)):
                    return os.path.join(d, n)
        return os.path.join(dirs[0], names[0])

    def transcribe(self, audio_file: str) -> str:
        """Transcribe audio file to text.

        Args:
            audio_file: Path to the audio file (WAV, FLAC, MP3, OGG)

        Returns:
            Transcribed text, or "" if the transcription failed
        """
        if not os.path.exists(audio_file):
            raise FileNotFoundError(f"Audio file not found: {audio_file}")

        if not os.path.exists(self.model_path):
            raise FileNotFoundError(f"Parakeet model not found: {self.model_path}")

        try:
            # Only documented flags are passed. `parakeet-cli`'s current source
            # also understands `-otxt`, `-of` and `-np`, but its own README does
            # not list them, and a build that rejects an unknown argument exits
            # 1 - a dependency on a flag a distro build may not have is the same
            # class of assumption as hard-depending whisper-cpp.
            cmd = [self.whisper_path, "-m", self.model_path, "-f", audio_file]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=_TIMEOUT
            )
            if result.returncode != 0:
                logger.error(f"Parakeet transcription failed: {result.stderr}")
                return ""
            return transcript_from(result.stdout)

        except subprocess.TimeoutExpired:
            logger.error("Parakeet transcription timed out")
            return ""
        except Exception as e:
            logger.error(f"Parakeet transcription error: {e}")
            return ""

    def transcribe_stream(self, audio_data: bytes) -> str:
        """Transcribe audio bytes to text.

        Not streaming recognition - the bytes are written to a WAV and
        transcribed as a file, exactly as `WhisperSTT` does. See the module
        docstring on why Parakeet's streaming variant is out of scope.

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
        """Check if Parakeet is usable: the binary *and* a model.

        One boolean over two independent causes, which is what
        `WhisperSTT.is_available` does and what `senses/hearing.py:stt_problem`
        unpacks when it needs to tell the user which half is missing. Neither
        may be reported as ready: an absent dependency has to degrade to a
        stated-unavailable state, never to a confident wrong answer.
        """
        return os.path.exists(self.whisper_path) and os.path.exists(self.model_path)