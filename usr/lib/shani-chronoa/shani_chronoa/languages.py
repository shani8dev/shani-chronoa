"""Languages beyond English: reading them (OCR), speaking them (Piper) and hearing them (whisper).

Each language is up to three things, installed together from setup:

- **Reading** - tesseract's language data. ShaniOS ships `tesseract` and
  `tesseract-data-eng`; others come from tessdata_fast (a few MB each, pinned
  by sha256 at one commit) into `~/.local/share/shani-chronoa/tessdata`, beside
  links to the system's English and script-detection data, because tesseract
  reads from one directory only.
- **Speaking** - a Piper voice from `voices.VOICES` where one exists. A reply
  written in a language's script is spoken with that language's voice
  (`voice_for_text`), so nothing has to be switched by hand.
- **Hearing** - whisper's models are multilingual already; turning a language
  on sets whisper to detect the spoken language (`language` = `auto`) instead
  of assuming English. The base model's accuracy outside English varies a lot
  by language, and setup says so.

The chosen languages are the `extra-languages` setting, comma-separated codes.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, NamedTuple, Optional

from shani_chronoa import files
from shani_chronoa.stt_provision import ModelSpec, install_verified

logger = logging.getLogger(__name__)

SETTING = "extra-languages"
SYSTEM_TESSDATA = Path("/usr/share/tessdata")
_TESSDATA = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/87416418657359cb625c412a48b6e1d6d41c29bd"


class Language(NamedTuple):
    code: str           # ISO 639-1, also whisper's code
    label: str          # native name, then English
    tesseract: str      # tesseract's code
    tess_size: int
    tess_sha256: str
    voice: str = ""     # a key in voices.VOICES, or "" when Piper has none
    script: str = ""    # the script its text is written in, for choosing a voice


#: tessdata_fast sizes and sha256 are from a download of each file at the pinned commit (2026-10-02).
LANGUAGES = {
    "hi": Language("hi", "हिन्दी - Hindi", "hin", 1_122_751,
                   "4c73ffc59d497c186b19d1e90f5d721d678ea6b2e277b719bee4e2af12271825",
                   "hi_IN-priyamvada-medium", "devanagari"),
    "mr": Language("mr", "मराठी - Marathi", "mar", 2_118_233,
                   "0ba3f2d116972e72fe9e176bc84c38e81dfb6670f4ed1f7f6c8e16a27da7cb61",
                   "mr_IN-google-medium", "devanagari"),
    "bn": Language("bn", "বাংলা - Bengali", "ben", 855_841,
                   "31163084c279aaebd376216f0c3d5c17ad4b5fee8db49dae79c20000b5de5964",
                   "bn_BD-google-medium", "bengali"),
    "te": Language("te", "తెలుగు - Telugu", "tel", 2_769_654,
                   "d10691fddd5b67802e1c12800ebb321d3b8bcd8d24a2ac3ff206f93188c04ab5",
                   "te_IN-padmavathi-medium", "telugu"),
    "ml": Language("ml", "മലയാളം - Malayalam", "mal", 5_275_996,
                   "bd05cbf1b197e7810d2903419aedb06f9ef77bfedf50b358673c1d18d707cdb4",
                   "ml_IN-meera-medium", "malayalam"),
    "ur": Language("ur", "اردو - Urdu", "urd", 1_398_718,
                   "62e8250ce2a994106e313a82e26a516a39e2cf159d0ce3c5b5008387fd0d555f",
                   "ur_PK-aegis_female-medium", "arabic"),
    "ta": Language("ta", "தமிழ் - Tamil", "tam", 3_237_963,
                   "d02fbec24be4b07e32e80d0ccfc3b6b67a3c5d61c9d0a7c8532677990912c6ec", "", "tamil"),
    "gu": Language("gu", "ગુજરાતી - Gujarati", "guj", 1_418_394,
                   "fa69658614b4946a9afae8853d67e0689838803dfa3d12c2e35ec53ee6f8df34", "", "gujarati"),
    "kn": Language("kn", "ಕನ್ನಡ - Kannada", "kan", 3_608_331,
                   "bd31e6b6ae93271e3bcf5383d306d8eefbb91542937cd6d735a5930c970e61d8", "", "kannada"),
    "pa": Language("pa", "ਪੰਜਾਬੀ - Punjabi", "pan", 497_721,
                   "1ec0907fc3534065ea9ae190c6bb7ec9e5c74fd9d2fa996aaec7407f11ad8131", "", "gurmukhi"),
    "ne": Language("ne", "नेपाली - Nepali", "nep", 1_002_911,
                   "280ba9450b4f21afbf5985e0de87857b75a972577c50ef0603c141ddde4f1cb8", "", "devanagari"),
    "or": Language("or", "ଓଡ଼ିଆ - Odia", "ori", 1_480_066,
                   "36f3135e61d501a3acfad41f5fe60b8e791274fff4c5375c969fdcca980cdbac", "", "oriya"),
}

#: Unicode blocks of the scripts above (start, end inclusive).
_SCRIPTS = {
    "devanagari": (0x0900, 0x097F), "bengali": (0x0980, 0x09FF), "gurmukhi": (0x0A00, 0x0A7F),
    "gujarati": (0x0A80, 0x0AFF), "oriya": (0x0B00, 0x0B7F), "tamil": (0x0B80, 0x0BFF),
    "telugu": (0x0C00, 0x0C7F), "kannada": (0x0C80, 0x0CFF), "malayalam": (0x0D00, 0x0D7F),
    "arabic": (0x0600, 0x06FF),
}


def tessdata_dir() -> Path:
    return files.data_home() / "shani-chronoa" / "tessdata"


def tess_spec(lang: Language) -> ModelSpec:
    return ModelSpec(f"tess-{lang.tesseract}", f"{lang.tesseract}.traineddata", lang.tess_size, lang.tess_sha256,
                     f"{lang.label} text recognition", _TESSDATA)


def chosen(config=None) -> "list[str]":
    """The languages the person turned on, in the order they chose them."""
    from shani_chronoa.config import ChronoaConfig
    config = config or ChronoaConfig()
    return [c for c in (x.strip() for x in config.get(SETTING, "").split(",")) if c in LANGUAGES]


def reads(code: str) -> bool:
    return (tessdata_dir() / f"{LANGUAGES[code].tesseract}.traineddata").is_file()


def speaks(code: str) -> bool:
    from shani_chronoa import voices
    voice = LANGUAGES[code].voice
    return bool(voice) and voices.voice_installed(voice)


def _link_system_data() -> None:
    """tesseract reads one directory, so the user's holds links to the system's English and OSD data."""
    directory = tessdata_dir()
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("eng.traineddata", "osd.traineddata"):
        source, link = SYSTEM_TESSDATA / name, directory / name
        if source.is_file() and not link.exists():
            try:
                link.symlink_to(source)
            except OSError as exc:
                logger.debug("could not link %s: %s", source, exc)


def install(code: str, *, speech: bool = True, progress: Optional[Callable[[int, int], None]] = None,
            config=None, transport=None) -> "list[str]":
    """Install `code`'s reading data and, if Piper has one and `speech`, its voice. Returns what was installed."""
    from shani_chronoa import voices
    from shani_chronoa.config import ChronoaConfig
    config = config or ChronoaConfig()
    lang = LANGUAGES[code]
    done = []
    _link_system_data()
    install_verified(tess_spec(lang), tessdata_dir(), config=config, progress=progress, transport=transport,
                     label="tessdata")
    done.append("reading")
    if speech and lang.voice:
        voices.install_piper(progress=progress, config=config, transport=transport)
        voices.install_voice(lang.voice, progress=progress, config=config, transport=transport)
        done.append("speaking")
    current = chosen(config)
    if code not in current:
        config.set(SETTING, ",".join(current + [code]))
    return done


def set_listening(enabled: bool, config=None) -> None:
    """Whisper detects the spoken language (`auto`), or assumes English."""
    from shani_chronoa.config import ChronoaConfig
    (config or ChronoaConfig()).set("language", "auto" if enabled else "en")


def ocr_languages(config=None) -> "list[str]":
    """tesseract codes to read with by default: English, then each installed language turned on."""
    return ["eng"] + [LANGUAGES[c].tesseract for c in chosen(config) if reads(c)]


def script_of(text: str) -> str:
    """The non-Latin script most of `text`'s letters are in, or '' (English, or no clear majority)."""
    counts: "dict[str, int]" = {}
    letters = 0
    for ch in text:
        if not ch.isalpha():
            continue
        letters += 1
        point = ord(ch)
        for script, (lo, hi) in _SCRIPTS.items():
            if lo <= point <= hi:
                counts[script] = counts.get(script, 0) + 1
                break
    if not counts or not letters:
        return ""
    script, n = max(counts.items(), key=lambda kv: kv[1])
    return script if n * 2 > letters else ""


def voice_for_text(text: str, config=None) -> "Optional[tuple[str, str]]":
    """(voice id, language code) to speak `text` with when it is in a script one of the person's languages uses.

    Devanagari is Hindi, Marathi and Nepali; the first of those the person
    turned on, with a voice installed, wins. None means: use the usual voice.
    """
    script = script_of(text)
    if not script:
        return None
    for code in chosen(config):
        lang = LANGUAGES[code]
        if lang.script == script and lang.voice and speaks(code):
            return lang.voice, code
    return None


def espeak_language(text: str, config=None) -> str:
    """An espeak-ng language for `text` when it is in one of the person's languages' scripts; '' otherwise."""
    script = script_of(text)
    if not script:
        return ""
    for code in chosen(config):
        if LANGUAGES[code].script == script:
            return code
    return next((c for c, lang in LANGUAGES.items() if lang.script == script), "")


def install_size(code: str, speech: bool = True) -> int:
    from shani_chronoa import voices
    lang = LANGUAGES[code]
    size = lang.tess_size
    if speech and lang.voice:
        v = voices.VOICES[lang.voice]
        size += v.onnx_size + v.json_size
    return size


def tesseract_env() -> "dict[str, str]":
    """TESSDATA_PREFIX for tesseract when the user directory has data, else {}."""
    directory = tessdata_dir()
    if directory.is_dir() and any(directory.glob("*.traineddata")):
        return {"TESSDATA_PREFIX": str(directory)}
    return {}

