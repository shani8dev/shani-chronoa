"""Which downloadable models are on this machine, one entry per kind.

**Why this exists.** Every kind of model had an "is it installed?" function, in
its own module, and a Setup page that downloads it - but the pages that *need*
a model each said "not installed" in their own words and, apart from speech,
offered nothing to press (measured 2026-10-15: of nine kinds, only speech had
a download on the page that uses it). This is the one table a page asks, and
`gui.surfaces.common.model_banner` turns a missing entry into a banner whose
Download button opens Setup on that kind's own page.

Setup stays the one place that downloads: it shows sizes, asks the one consent
(`model-download-enabled`) and reports progress. This module only answers.

Every check is a file-system look (no network, no subprocess) and is wrapped:
a check that raises reads as "not installed", never as a broken page.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Kind:
    key: str
    label: str            # "A speech model"
    enables: str          # what having it lets Chronoa do, for the banner
    setup_page: str       # the Setup wizard page that downloads it
    check: Callable[[], bool]


def _llm() -> bool:
    from shani_chronoa import local_llm
    return bool(local_llm.installed())


def _stt() -> bool:
    """Any speech model on disk, read from the files alone.

    **No `ChronoaConfig` here** - `recordings.whisper_model()` builds one to
    read the preferred model, and constructing it with the file backend writes
    ~/.config/glib-2.0/settings: a page that only asks "is it installed?" wrote
    to the home directory (caught by the Voice page's writes-nothing test).
    """
    from shani_chronoa import engines, stt_provision
    whisper = any(stt_provision.is_provisioned(key) for key in stt_provision.MODELS)
    # `engines.has`, not `shutil.which`: the two spellings this used - one of
    # which named `whisper-cpp`, a package rather than a program - could only
    # see a system copy, so a machine that had just downloaded whisper.cpp into
    # its own data directory was reported as having no speech program.
    has_cli = engines.has("whisper-cli")
    return (whisper and has_cli) or bool(stt_provision.is_parakeet_provisioned())


def _voice() -> bool:
    """A natural voice is installed: any Piper voice with Piper, or Kokoro."""
    from shani_chronoa import voices
    piper = bool(voices.piper_binary()) and any(voices.voice_installed(k) for k in voices.VOICES)
    try:
        kokoro = bool(voices.kokoro_installed())
    except Exception:  # noqa: BLE001
        kokoro = False
    return piper or kokoro


def _vision() -> bool:
    from shani_chronoa import local_vision
    return bool(local_vision.installed())


def _imagine() -> bool:
    from shani_chronoa import imagegen
    return bool(imagegen.installed())


def _memory() -> bool:
    from shani_chronoa import local_embed
    return bool(local_embed.installed())


def _photos() -> bool:
    from shani_chronoa.opencv import runtime
    return bool(runtime.installed())


def _sounds() -> bool:
    from shani_chronoa import sounds
    return bool(sounds.installed())


def _speakers() -> bool:
    from shani_chronoa import speakers
    return bool(speakers.installed())


KINDS: Dict[str, Kind] = {k.key: k for k in (
    Kind("llm", "A language model", "answer on this computer, without the cloud", "brain", _llm),
    Kind("stt", "A speech model", "hear you and transcribe recordings", "ears", _stt),
    Kind("voice", "A natural voice", "speak in a human voice instead of the robotic fallback",
         "voice", _voice),
    Kind("vision", "A vision model", "describe pictures and what is on your screen", "eyes",
         _vision),
    Kind("imagine", "An image model", "draw pictures from a description", "imagine", _imagine),
    Kind("memory", "An embedding model", "find things by meaning, not only by exact words",
         "memory", _memory),
    Kind("photos", "The photo tools", "find faces and sort your photos", "photos", _photos),
    Kind("sounds", "A sound model", "name the sounds around you", "sounds", _sounds),
    Kind("speakers", "A speaker model", "tell speakers apart in a recording", "speakers",
         _speakers),
)}


def installed(key: str) -> Optional[bool]:
    """True/False for a known kind, None for an unknown one."""
    kind = KINDS.get(key)
    if kind is None:
        return None
    try:
        return bool(kind.check())
    except Exception:  # noqa: BLE001 - a check that raises is "not installed"
        logger.debug("model check for %s failed", key, exc_info=True)
        return False


def missing() -> "list[str]":
    """Every kind not on this machine, in table order."""
    return [key for key in KINDS if installed(key) is False]
