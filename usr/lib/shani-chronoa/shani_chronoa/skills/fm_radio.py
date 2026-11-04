"""Skill: FM broadcast radio - what hardware is here, and demodulating the audio.

The skill: the assistant's consent and its tool schema. The engine is
shani-link's (shani_link/fm_radio.py) - device plumbing, used by people directly
through the Shani Link app with no consent step - and every name it has is
reachable here, so the assistant's code and tests see one module's worth.
"""
from __future__ import annotations

from shani_chronoa import _link
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_engine = _link.module("fm_radio")
globals().update({k: v for k, v in vars(_engine).items() if not k.startswith("__")})


_CONSENT_KEY = "fm-radio-enabled"


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """(allowed, why-not) - the house signature `test_question_presenter.py` sweeps."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (f"Using the radio is turned off. Nothing was received or tuned. "
                       f"Enable '{_CONSENT_KEY}' in Settings to allow it.")
    return True, ""


SCHEMA = {
    "type": "function",
    "function": {
        "name": "fm_radio",
        "description": (
            "FM and AM broadcast radio. Five actions: 'hardware' for whether this "
            "computer can receive anything at all (a laptop usually cannot, and that is "
            "a true answer rather than a failure - it needs an RTL2832U USB dongle), "
            "'bands' for the coverage, 'tune' to a frequency, 'decode' to demodulate a "
            "recorded IQ capture, and 'scan' to look for carriers in one. A frequency is "
            "given in MHz, e.g. 98.6. rtl_fm is narrowband and retunes per channel, so it "
            "cannot scan - scanning needs a wideband recording. No station names are "
            "guessed: a carrier gives a frequency, and naming it would need a table for "
            "your region. Requires the 'fm-radio-enabled' consent key."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(_ACTIONS),
                    "description": "hardware: what can receive here. bands: coverage. "
                                   "tune: one frequency. decode: demodulate a capture. "
                                   "scan: find carriers in a capture.",
                },
                "frequency": {
                    "type": "string",
                    "description": "For tune: the frequency in MHz, e.g. '98.6'. A bare "
                                   "number is read as MHz; kHz and Hz suffixes work too.",
                },
                "seconds": {
                    "type": "integer",
                    "description": "For tune: how long to listen, default 10.",
                },
                "file": {
                    "type": "string",
                    "description": "For decode and scan: path to a 16-bit stereo IQ WAV "
                                   "capture, as rtl_fm -F wav writes.",
                },
                "centre_mhz": {
                    "type": "string",
                    "description": "For scan: what frequency the capture was centred on, "
                                   "so a tone can be reported in MHz rather than Hz.",
                },
                  "out": {
                      "type": "string",
                      "description": "For decode: where to write the demodulated audio as "
                                     "a 16-bit mono WAV, so it can actually be listened to. "
                                     "Defaults to a .audio.wav beside the capture. Use "
                                     "out='-' for the numbers only and no file.",
                  },
                "deemphasis_us": {
                    "type": "integer",
                    "description": "For decode: the de-emphasis constant, 75 for North "
                                   "America and 50 for Europe and most of the rest of "
                                   "the world. Default 75.",
                },
            },
            "required": ["action"],
        },
    },
}


def run(arguments: dict) -> str:
    """The assistant's way in: its consent first, then shani-link's engine."""
    if isinstance(arguments, dict):
        allowed, refusal = _consent(ChronoaConfig())
        if not allowed:
            return refusal
    return _engine.run(arguments)


SKILLS = [Skill(name="fm_radio", schema=SCHEMA, run=run)]
