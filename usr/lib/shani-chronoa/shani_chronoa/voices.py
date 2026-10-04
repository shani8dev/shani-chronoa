"""A natural voice without root: Piper's release build and its voices, and Kokoro through sherpa-onnx.

Piper is not in the Arch repos as a *binary*, because the distribution's `piper`
package is the GTK gaming-mouse configurator; `rhasspy/piper`'s own Linux release
is a 26 MB self-contained tarball (its onnxruntime and espeak-ng data included)
that runs from any directory, so setup puts it under
~/.local/share/shani-chronoa/piper and a voice where `tts.PiperTTS` already looks
(~/.local/share/piper/voices).

Kokoro, the neural voice, is installed the same way (`install_kokoro`): the
sherpa-onnx release, which runs Kokoro the way Piper's program runs Piper, and
its packaging of the Kokoro model - two pinned archives in the user's home and
nothing system-wide. Piper stays, because a user's existing setup should keep
working and the fallback chain is not allowed to be a single engine deep.

Every file is verified against the digest pinned here, through the same
`stt_provision._install` the speech and language models use - there is exactly
one downloader in this package and a second one is how a verification step would
come to be optional.

The Piper tarball's digest was computed from the release asset on 2026-10-02
(the 2023 release predates GitHub's published digests); the voices' are their
Hugging Face LFS sha256 at the pinned piper-voices revision, and each voice's
small .onnx.json config is pinned by its own sha256. The sherpa-onnx and Kokoro
archives' digests are the ones GitHub publishes for those release assets.
"""

from __future__ import annotations

import logging
import os
import shutil
import tarfile
from pathlib import Path
from typing import Callable, NamedTuple, Optional

from shani_chronoa import files
from shani_chronoa.stt_provision import ModelSpec, _install

logger = logging.getLogger(__name__)

_PIPER = ModelSpec("piper-linux-x86_64", "piper_linux_x86_64.tar.gz", 26_460_462,
                   "a50cb45f355b7af1f6d758c1b360717877ba0a398cc8cbe6d2a7a3a26e225992",
                   "Piper 2023.11.14-2", "https://github.com/rhasspy/piper/releases/download/2023.11.14-2")
_VOICES_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/c10ece1aade47bb51c153c893d14e5bf8e5b7117"
#: Every English voice here is the same size (they share one architecture); the others are not.
VOICE_BYTES = 63_201_294


class Voice(NamedTuple):
    label: str
    folder: str
    onnx_sha256: str
    json_sha256: str
    json_size: int
    onnx_size: int = VOICE_BYTES
    #: ISO 639-1 code; the setup's Voice page lists the English ones, the Languages page the rest
    language: str = "en"
    #: for a multi-speaker model, the one to use (Piper's --speaker)
    speaker: Optional[int] = None


#: voice id -> Voice. Female voices, as Chronoa's is, except where a model's
#: dataset does not say (its label then names no gender).
VOICES = {
    "en_US-lessac-medium": Voice("Lessac - clear, American (the default)", "en/en_US/lessac/medium",
                                 "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f",
                                 "efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0", 4885),
    "en_GB-jenny_dioco-medium": Voice("Jenny - soft, British", "en/en_GB/jenny_dioco/medium",
                                      "469c630d209e139dd392a66bf4abde4ab86390a0269c1e47b4e5d7ce81526b01",
                                      "a9a7a93a317c9a3cb6563e37eb057df9ef09c06188a8a4341b0fcb58cba54dd4", 4895),
    "en_US-amy-medium": Voice("Amy - warm, American", "en/en_US/amy/medium",
                              "b3a6e47b57b8c7fbe6a0ce2518161a50f59a9cdd8a50835c02cb02bdd6206c18",
                              "95a23eb4d42909d38df73bb9ac7f45f597dbfcde2d1bf9526fdeaf5466977d77", 4882),
    "en_US-hfc_female-medium": Voice("HFC - calm, American", "en/en_US/hfc_female/medium",
                                     "914c473788fc1fa8b63ace1cdcdb44588f4ae523d3ab37df1536616835a140b7",
                                     "03f1fa0622b80463283592d97aca9f6e89aec345a5c56b7257723e0093c58b6c", 5033),
    # Indian languages (and Urdu), from the same pinned revision; sizes and
    # digests are each file's LFS size/sha256 (.onnx) or a download (.onnx.json)
    "hi_IN-priyamvada-medium": Voice("Priyamvada - Hindi", "hi/hi_IN/priyamvada/medium",
                                     "aa63bcf2cd493b55a450f280e23cf77f03afc9af7015e6e5acd43b652f166c88",
                                     "5efc0ccf7529f3528996d46e0fac1f969f681d44a8e55bfa6236ff8841b5d52d", 4973,
                                     63_516_050, "hi"),
    # OpenSLR 64, which this model is trained on, is female speakers only
    "mr_IN-google-medium": Voice("Marathi", "mr/mr_IN/google/medium",
                                 "e1200d474a74ebd6d1737be2c7affe56f1f9efc18915d4595d7f5c2b15cf06f4",
                                 "11055302ee1e3c9902e5e96c03cbfc0a9eec29b1b06b91ef52a3c66e9873edbb", 5467,
                                 76_768_179, "mr", 0),
    "ml_IN-meera-medium": Voice("Meera - Malayalam", "ml/ml_IN/meera/medium",
                                "0c3e730f8294286694cac5d33f4c94d050ed8ea74c5fd6d0d492d38cb57b5102",
                                "ad51935143f548d139a84c6ad1702b757cbceb52701167c0c1c98bebda7203e6", 5045,
                                62_950_044, "ml"),
    "te_IN-padmavathi-medium": Voice("Padmavathi - Telugu", "te/te_IN/padmavathi/medium",
                                     "414aa5960d91ceb6e45bbdf8c27fdc71af09f205130d7be4e99470f3c2cfa57d",
                                     "6c86e4ee99d379815f78a75f23cdad62ccf50370062dd915c233d6e22de7109f", 4974,
                                     63_516_050, "te"),
    "bn_BD-google-medium": Voice("Bengali", "bn/bn_BD/google/medium",
                                 "f2e7518ed5534a755024a48c71b80bf617efaf12570bbdf3ce255a9526a8afd3",
                                 "bc7e5e39e2a874bdad186620576ce18089b5a06c5645e258bcea3d56fdb11c0a", 5494,
                                 76_782_515, "bn", 0),
    "ur_PK-aegis_female-medium": Voice("Aegis - Urdu", "ur/ur_PK/aegis_female/medium",
                                       "3ec306035cfd0bbc22342fee5890450708a16dd231809522731678aa583c961f",
                                       "a8385c217a7e276acf5234bf01dbd0adb007a57c32ffbcc57d2db7eac3979bb6", 5236,
                                       63_515_589, "ur"),
}


def voices_for(language: str) -> "list[str]":
    return [key for key, v in VOICES.items() if v.language == language]


def piper_dir() -> Path:
    return files.data_home() / "shani-chronoa" / "piper"


def piper_binary() -> str:
    """A Piper TTS binary: the system one if a package ever provides it, else the user-local install."""
    local = piper_dir() / "piper" / "piper"
    return shutil.which("piper-tts") or (str(local) if os.access(local, os.X_OK) else "")


def voice_dir() -> Path:
    return files.data_home() / "piper" / "voices"


def voice_installed(voice: str) -> bool:
    return (voice_dir() / f"{voice}.onnx").is_file() and (voice_dir() / f"{voice}.onnx.json").is_file()


def install_piper(progress: Optional[Callable[[int, int], None]] = None, config=None, transport=None) -> str:
    """Fetch, verify and unpack Piper; returns the binary's path."""
    if piper_binary():
        return piper_binary()
    archive = _install(_PIPER, user_dir=piper_dir(), system_dir=Path("/usr/share/piper"), existing=None,
                       config=config, progress=progress, transport=transport, label="voice")
    try:
        with tarfile.open(archive) as tar:
            # data filter: no absolute paths, no "..", no devices or setuid bits
            tar.extractall(piper_dir(), filter="data")
    finally:
        archive.unlink(missing_ok=True)
    binary = piper_dir() / "piper" / "piper"
    if not os.access(binary, os.X_OK):
        raise RuntimeError("the Piper archive did not contain piper/piper")
    return str(binary)


def install_voice(voice: str, progress: Optional[Callable[[int, int], None]] = None, config=None,
                  transport=None) -> Path:
    v = VOICES[voice]
    base = f"{_VOICES_URL}/{v.folder}"
    for spec in (ModelSpec(voice, f"{voice}.onnx", v.onnx_size, v.onnx_sha256, v.label, base),
                 ModelSpec(voice + "-config", f"{voice}.onnx.json", v.json_size, v.json_sha256, v.label, base)):
        existing = voice_dir() / spec.filename
        _install(spec, user_dir=voice_dir(), system_dir=Path("/usr/share/piper/voices"),
                 existing=existing if existing.is_file() else None, config=config,
                 progress=progress, transport=transport, label="voice")
    return voice_dir() / f"{voice}.onnx"


# ---------------------------------------------------------------------------
# Kokoro, through sherpa-onnx - installed exactly the way Piper is
# ---------------------------------------------------------------------------
#
# Kokoro is only a model; something has to turn text into phonemes, run the
# ONNX graph and write the WAV. Piper ships that something as a program, which
# is why there is no piper.py. sherpa-onnx (k2-fsa, Apache-2.0) ships the same
# kind of program for Kokoro: `sherpa-onnx-offline-tts`, in a 27 MB Linux
# release that carries its own onnxruntime (`lib/`, found through the binary's
# `$ORIGIN/../lib` rpath), so nothing has to be installed system-wide and
# Python needs no onnxruntime or numpy. The model is sherpa-onnx's own
# packaging of Kokoro 82M v0.19 (int8, English): model, voices.bin with eleven
# speakers, tokens and the espeak-ng data, in one archive.
#
# Both archives' digests are the ones GitHub publishes for the release assets,
# and match a download of each (2026-10-02). Measured in a ShaniOS slot: 7.7 s
# to produce 6.4 s of speech on a CPU (RTF 1.2, the same with 2 or 4 threads),
# which is why Kokoro stays opt-in (`kokoro-tts-enabled`) - see tts.py.

_KOKORO_MODEL_DIR = "kokoro-int8-en-v0_19"
_KOKORO_MODEL = ModelSpec("kokoro", f"{_KOKORO_MODEL_DIR}.tar.bz2", 103_248_205,
                          "c9f0dd393615805b0bab050c340834d5e684e732aec91c0e860cd30e982c08bd",
                          "Kokoro 82M v0.19 (int8, English)",
                          "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models")


class KokoroVoice(NamedTuple):
    """One Kokoro voice: the label the setup shows, and its speaker id in voices.bin."""
    label: str
    sid: int


#: Female voices only, as Chronoa's is in every engine. The ids were checked
#: byte for byte: each speaker's slice of voices.bin matches that voice's own
#: style bank (onnx-community/Kokoro-82M-ONNX, voices/<name>.bin) at row 30.
KOKORO_VOICES = {
    "af_sarah": KokoroVoice("Sarah - warm, American", 3),
    "af_bella": KokoroVoice("Bella - bright, American", 1),
    "af_nicole": KokoroVoice("Nicole - soft, American", 2),
    "af_sky": KokoroVoice("Sky - light, American", 4),
    "bf_emma": KokoroVoice("Emma - clear, British", 7),
    "bf_isabella": KokoroVoice("Isabella - warm, British", 8),
}
KOKORO_DEFAULT_VOICE = "af_sarah"


def kokoro_binary() -> str:
    from shani_chronoa import sherpa
    return sherpa.binary("sherpa-onnx-offline-tts")


def kokoro_dir() -> Path:
    """The user's own Kokoro directory, beside the Piper voices."""
    return files.data_home() / "kokoro"


def kokoro_model_dir() -> Path:
    return kokoro_dir() / _KOKORO_MODEL_DIR


def kokoro_files() -> "dict[str, Path]":
    """The files sherpa-onnx is given, by the option that names each."""
    d = kokoro_model_dir()
    return {"--kokoro-model": d / "model.int8.onnx", "--kokoro-voices": d / "voices.bin",
            "--kokoro-tokens": d / "tokens.txt", "--kokoro-data-dir": d / "espeak-ng-data"}


def kokoro_installed(voice: str = KOKORO_DEFAULT_VOICE) -> bool:
    """Whether the program and every model file it needs are on disk (and `voice` is one we know)."""
    return (voice in KOKORO_VOICES and bool(kokoro_binary())
            and all(p.exists() for p in kokoro_files().values()))


def kokoro_problem(voice: str = KOKORO_DEFAULT_VOICE) -> str:
    """Why Kokoro cannot speak here, or '' when it can - the missing half named, never 'not ready'."""
    if voice not in KOKORO_VOICES:
        return f"there is no Kokoro voice called {voice!r}; choose one of {', '.join(sorted(KOKORO_VOICES))}"
    if not kokoro_binary():
        return "the sherpa-onnx program that runs Kokoro is not installed (Chronoa's setup, Voice page)"
    missing = [str(p) for p in kokoro_files().values() if not p.exists()]
    if missing:
        return f"the Kokoro model is not installed ({missing[0]} is missing; Chronoa's setup, Voice page)"
    return ""


def install_kokoro(voice: str = KOKORO_DEFAULT_VOICE,
                   progress: Optional[Callable[[int, int], None]] = None,
                   config=None, transport=None) -> str:
    """Fetch, verify and unpack sherpa-onnx and the Kokoro model, as `install_piper` does Piper.

    Returns the program's path. Raises if, after unpacking, `kokoro_problem`
    still names something missing: a download that reports success and is then
    never found by the reader is the failure `stt_provision.py` documents.
    """
    if voice not in KOKORO_VOICES:
        raise KeyError(kokoro_problem(voice))
    from shani_chronoa import sherpa
    sherpa.install(progress=progress, config=config, transport=transport, label="kokoro")
    if not all(p.exists() for p in kokoro_files().values()):
        sherpa.install_archive(_KOKORO_MODEL, kokoro_dir(), kokoro_files()["--kokoro-model"], progress=progress,
                               config=config, transport=transport, label="kokoro")
    problem = kokoro_problem(voice)
    if problem:
        raise RuntimeError(f"Kokoro was downloaded but {problem}")
    return kokoro_binary()
