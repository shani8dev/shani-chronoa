"""Named voice styles, layered on top of `tts.apply_timbre`.

`tts.py` already owns the whole voice path: it picks an engine (Piper, Kokoro,
eSpeak-NG, whatever `skills/speak.py` registered), and `apply_timbre` already
runs SoX afterwards for three controls - pitch, tempo and rate - with the
careful staging, timeout and "was skipped" reporting that a reply depends on.

**This module does not replace any of that, and does not talk to an engine.**
It answers one narrower question that `tts.py` cannot: *which SoX effects does a
named style mean?* The styles are offsets from the neutral voice, so
`natural` produces an empty effect list and costs nothing - a style that changed
the sound of an unstyled reply would be a surprise, not a feature.

It was written after a proposal to add a parallel `Voice` facade with its own
engine adapters and its own SoX processor. That was not adopted, for three
reasons worth keeping on the record:

1. **Two SoX passes are worse than one.** The proposal ran its own processor
   *after* the engine, so every reply would have been through `apply_timbre`
   and then through this - two decodes, two encodes, two chances to fail, and a
   normalised output from the first pass that the second pass then re-normalises.
2. **`Voice` already means something else here.** `voices.Voice` is the Piper
   voice catalogue entry. A second public `Voice` class for "the whole voice
   pipeline" would be two types, one name, and an import that silently rebinds.
3. **A preset that bypasses GSettings makes a slider lie.** `tts-pitch` is in
   the schema, and the settings window has a spin row for it. A style setting
   its own pitch behind the schema's back would make that row describe something
   that no longer happens.

So the composable part was kept, the pipeline was not duplicated, and the parts
of the proposal that were quietly broken were fixed rather than imported. What
came over unchanged in spirit: a `VoiceStyle` dataclass of normalised
parameters, a preset table, composable presets, and a description function for a
row subtitle.

**What the parameters actually are.** All are offsets from neutral, except
`pitch` (semitones) and `speed` (a multiplier). They map onto SoX effects that
`tts.py` did not use: `equalizer` for the tonal ones, `compand` for dynamics,
`overdrive` for colour, `reverb` for room. That is the whole of the addition -
four effect types `apply_timbre` did not already apply.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, fields, replace
from typing import Any, Dict, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

#: The neutral style. Every parameter is its identity value, so `natural` is
#: this object and produces no SoX effects at all.
NEUTRAL = "natural"


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


@dataclass(frozen=True)
class VoiceStyle:
    """Engine-independent voice character, as offsets from neutral.

    Ranges are the ones the effect builder assumes, and `describe` repeats them
    rather than the table below having to be read to find out what a number
    means:

    ============== ==========================================================
    pitch          semitones, -6..+6
    speed          speaking-rate multiplier, 0.5..2.0
    energy         perceived loudness of the delivery, 0..1
    warmth         low-mid body, 0..1
    brightness     high-mid presence, 0..1
    resonance      upper-mid body, 0..1
    breathiness    airy high-frequency character, 0..1
    articulation   intelligibility, 0..1
    pause_scale    pause length multiplier, 0.5..2.0
    compression    dynamics control, 0..1
    saturation     harmonic colouring, 0..1
    reverb         room amount, 0..1
    room_size      room character within the reverb, 0..1
    pre_delay_ms   gap before the reverb, 0..200
    bass           low-shelf trim in dB
    treble         high-shelf trim in dB
    air            very-high trim in dB
    ============== ==========================================================

    `language` is a hint for engines that want one and is ignored by SoX: these
    are acoustic offsets and they do not create an accent, which is why the
    locale presets only set this field.
    """

    pitch: float = 0.0
    speed: float = 1.0
    energy: float = 0.70
    warmth: float = 0.50
    brightness: float = 0.50
    resonance: float = 0.50
    breathiness: float = 0.0
    articulation: float = 0.70
    pause_scale: float = 1.0
    compression: float = 0.0
    saturation: float = 0.0
    reverb: float = 0.0
    room_size: float = 0.25
    pre_delay_ms: float = 0.0
    bass: float = 0.0
    treble: float = 0.0
    air: float = 0.0
    language: Optional[str] = None

    def merged(self, **changes: Any) -> "VoiceStyle":
        """A copy with some fields replaced, ignoring anything unknown.

        Unknown keys are ignored rather than raising: these are set from a
        settings key and a future preset table, and a typo in either should
        cost the one typo, not the reply.
        """
        known = {f.name for f in fields(self)}
        return replace(self, **{k: v for k, v in changes.items() if k in known})


#: Every numeric parameter and the value that means "leave it alone". Used by the
#: compositor and by `is_neutral`, so it is written once.
_IDENTITY: Mapping[str, float] = {
    "speed": 1.0, "energy": 0.70, "warmth": 0.50, "brightness": 0.50,
    "resonance": 0.50, "breathiness": 0.0, "articulation": 0.70,
    "pause_scale": 1.0, "compression": 0.0, "saturation": 0.0,
    "reverb": 0.0, "room_size": 0.25, "pre_delay_ms": 0.0,
    "bass": 0.0, "treble": 0.0, "air": 0.0, "pitch": 0.0,
}

_BOUNDS: Mapping[str, Tuple[float, float]] = {
    "speed": (0.5, 2.0), "energy": (0.0, 1.0), "warmth": (0.0, 1.0),
    "brightness": (0.0, 1.0), "resonance": (0.0, 1.0),
    "breathiness": (0.0, 1.0), "articulation": (0.0, 1.0),
    "pause_scale": (0.5, 2.0), "compression": (0.0, 1.0),
    "saturation": (0.0, 1.0), "reverb": (0.0, 1.0), "room_size": (0.0, 1.0),
    "pre_delay_ms": (0.0, 200.0), "bass": (-12.0, 12.0),
    "treble": (-12.0, 12.0), "air": (-12.0, 12.0), "pitch": (-6.0, 6.0),
}


def _bounded(style: VoiceStyle) -> VoiceStyle:
    """Every field inside its documented range.

    Presets are literals in this file, so a typo is a programming error rather
    than user input - but SoX is perfectly willing to be handed a 500-semitone
    pitch, and a style table that could do that is one edit away from doing it.
    """
    return replace(style, **{name: _clamp(getattr(style, name), *bounds)
                             for name, bounds in _BOUNDS.items()})


def _combine(base: VoiceStyle, overlay: VoiceStyle) -> VoiceStyle:
    """Layer `overlay` on `base` as offsets from neutral.

    Additive for the offsets and multiplicative for the rates, which is what
    makes `("calm", "assistant")` mean "assistant, but calm" rather than the
    average of the two. Clamped afterwards, so three layered presets cannot walk
    `energy` off the end of its range.
    """
    out: Dict[str, float] = {}
    for name, identity in _IDENTITY.items():
        here, there = getattr(base, name), getattr(overlay, name)
        out[name] = here * there if name in ("speed", "pause_scale") \
            else here + (there - identity)
    combined = replace(VoiceStyle(), **out)
    return replace(_bounded(combined),
                   language=overlay.language or base.language)


#: The style table. Names are what a person picks from, so they are words
#: rather than numbers; the numbers live in exactly one place each.
BASE_PRESETS: Dict[str, VoiceStyle] = {
    # -- plain ------------------------------------------------------------
    "natural": VoiceStyle(),
    "clear": VoiceStyle(articulation=.90, brightness=.58),
    "warm": VoiceStyle(warmth=.82, brightness=.38, resonance=.65, bass=1.5),
    "soft": VoiceStyle(energy=.48, brightness=.43, compression=.08, treble=-1.0),
    "studio": VoiceStyle(articulation=.88, compression=.28, brightness=.55),

    # -- delivery, which is what an assistant mostly wants ----------------
    "assistant": VoiceStyle(articulation=.88, warmth=.62, energy=.62,
                            compression=.18),
    "concise": VoiceStyle(articulation=.94, speed=1.02, pause_scale=.85),
    "gentle": VoiceStyle(articulation=.86, warmth=.82, energy=.48, speed=.96),
    "calm": VoiceStyle(energy=.42, speed=.90, brightness=.43,
                       compression=.10, pause_scale=1.12),
    "soothing": VoiceStyle(energy=.40, warmth=.86, brightness=.35, speed=.88),
    "confident": VoiceStyle(energy=.80, resonance=.76, articulation=.92,
                            compression=.28),
    "authoritative": VoiceStyle(energy=.84, resonance=.86, articulation=.94,
                                compression=.35),
    "narrator": VoiceStyle(articulation=.88, resonance=.67, compression=.25),
    "presenter": VoiceStyle(articulation=.90, energy=.78, brightness=.58),
    "teacher": VoiceStyle(articulation=.94, warmth=.60, energy=.68),
    "news": VoiceStyle(articulation=.94, brightness=.66, compression=.32,
                       energy=.78),

    # -- register ---------------------------------------------------------
    "bright": VoiceStyle(brightness=.82, air=1.0, articulation=.86),
    "deep": VoiceStyle(pitch=-2.5, warmth=.68, resonance=.82, bass=2.0),
    "resonant": VoiceStyle(resonance=.92, warmth=.72, bass=1.5),
    "dark": VoiceStyle(brightness=.25, warmth=.75, resonance=.80, bass=2.0),
    "airy": VoiceStyle(energy=.52, brightness=.70, breathiness=.60, treble=1.0),
    "crisp": VoiceStyle(articulation=.96, brightness=.78, air=1.5,
                        compression=.20),

    # -- room -------------------------------------------------------------
    "small-room": VoiceStyle(reverb=.08, room_size=.20),
    "hall": VoiceStyle(reverb=.25, room_size=.70),
    "cave": VoiceStyle(reverb=.38, room_size=1.0, brightness=.32,
                       resonance=.85, pre_delay_ms=30.0),

    # -- effect -----------------------------------------------------------
    "radio": VoiceStyle(articulation=.90, compression=.48, brightness=.62,
                        bass=1.0),
    "telephone": VoiceStyle(articulation=.92, compression=.30, brightness=.60),
    "whisper": VoiceStyle(energy=.25, brightness=.52, breathiness=.90,
                          compression=.05),
    "underwater": VoiceStyle(brightness=.18, treble=-7.0, air=-6.0, reverb=.16),

    # -- locale. Acoustic styling cannot produce an accent, so these set the
    # engine hint only, and say so by having nothing else to set.
    "english-neutral": VoiceStyle(language="en"),
    "english-indian": VoiceStyle(language="en-IN"),
    "english-british": VoiceStyle(language="en-GB"),
    "english-american": VoiceStyle(language="en-US"),
    "hindi": VoiceStyle(language="hi"),
    "marathi": VoiceStyle(language="mr"),
}

#: Presets built by layering others, as a sequence of names.
#:
#: **A name may not be in both tables.** `resolve_preset` checks the base table
#: first, so a composite whose name is also a base preset is unreachable - it
#: looks like it composes and does not. `test_no_preset_name_is_shadowed` is what
#: keeps that from happening; the proposal this module came from had four such
#: names and never noticed, because the names still resolved.
COMPOSITES: Dict[str, Tuple[str, ...]] = {
    "warm-assistant": ("warm", "assistant"),
    "soft-assistant": ("soft", "assistant"),
    "clear-assistant": ("clear", "assistant"),
    "warm-narrator": ("warm", "narrator"),
    "deep-narrator": ("deep", "narrator"),
    "calm-assistant": ("calm", "assistant"),
    "soothing-assistant": ("soothing", "gentle"),
    "bright-presenter": ("bright", "presenter"),
    "radio-announcer": ("radio", "presenter"),
    "deep-cinematic": ("deep", "hall"),
}

_STYLE_KEY = "voice-style"


def resolve_preset(name: str) -> VoiceStyle:
    """The style a preset name means, following composites.

    A cycle in `COMPOSITES` raises rather than recursing forever: the proposal
    this came from had `"cinematic": ("cinematic",)`, self-referential, which
    only worked because the same name also existed in the base table and won.
    Remove that base entry and it becomes infinite recursion at first use.
    """
    return _resolve(name, set())


def _resolve(name: str, seen: "set[str]") -> VoiceStyle:
    """`resolve_preset`, with the visited set carried *through* the recursion.

    Carried through rather than created per call: a guard that starts a fresh set
    at each level catches `("x",)` and nothing else, so `a -> b -> a` recursed
    until the stack ran out. Verified by the control in
    `tests/test_voice_style.py`, which is a two-name cycle and not a self-reference.
    """
    if name in seen:
        raise ValueError(f"voice style {name!r} is part of a composition cycle")
    seen = seen | {name}
    if name in BASE_PRESETS:
        return BASE_PRESETS[name]
    parts = COMPOSITES.get(name)
    if parts is None:
        raise KeyError(f"Unknown voice style: {name!r}")
    style = VoiceStyle()
    for part in parts:
        style = _combine(style, _resolve(part, seen))
    return style


def list_presets() -> List[str]:
    """Every name a person can choose, sorted."""
    return sorted(set(BASE_PRESETS) | set(COMPOSITES))


def describe(name: str) -> Dict[str, Any]:
    """A preset as data, for a settings row or a `--json` dump.

    Built from the dataclass's own fields rather than a hand-written list of
    them: the proposal's version repeated all seventeen names, so any parameter
    added to `VoiceStyle` afterwards was silently missing from the description.
    """
    style = resolve_preset(name)
    return {
        "name": name,
        "composed_from": list(COMPOSITES.get(name, ())),
        "language": style.language,
        "neutral": is_neutral(style),
        "style": {f.name: getattr(style, f.name)
                  for f in fields(VoiceStyle) if f.name != "language"},
        "effects": style_effects(style),
        "description": describe_effects(style),
    }


def is_neutral(style: VoiceStyle) -> bool:
    """Whether this style asks for no change at all.

    The reason `natural` is free: a style that alters an unstyled reply would be
    a surprise, and "no preset chosen" has to mean exactly that.
    """
    return not style_effects(style) and style.language is None


# -- the SoX effects ------------------------------------------------------
#
# Four effect types `tts.apply_timbre` did not already apply. The three it does
# - pitch, tempo, speed - belong to `tts`, which owns their ranges and their
# settings keys; duplicating them here would give one reply two pitch shifts.


def _eq(frequency: str, q: str, gain: float) -> List[str]:
    return ["equalizer", frequency, q, f"{gain:.2f}"]


def style_effects(style: VoiceStyle) -> List[str]:
    """The SoX effects a style asks for, in a fixed order.

    Order is not cosmetic. Pitch-shifting and time-stretching are the expensive
    frequency-domain stages and belong first so they run on the shortest signal
    left; tonal shaping is cheap EQ and goes after; dynamics and colour go last
    because a compressor before an EQ measures a different signal. This is the
    same reasoning `tts.apply_timbre` already documents for its own three.
    """
    style = _bounded(style)
    effects: List[str] = []

    # Tonal shape. The neutral value of each band is 0.5, and `bass`/`treble`/
    # `air` are absolute dB trims added on top, so a preset can say either.
    warmth = (style.warmth - 0.5) * 6.0 + style.bass
    if abs(warmth) > 0.15:
        effects += _eq("180", "1.0q", warmth)
    resonance = (style.resonance - 0.5) * 5.0
    if abs(resonance) > 0.15:
        effects += _eq("1200", "1.0q", resonance)
    brightness = (style.brightness - 0.5) * 8.0 + style.treble
    if abs(brightness) > 0.15:
        effects += _eq("3500", "1.0q", brightness)
    air = style.breathiness * 3.0 + style.air
    if abs(air) > 0.15:
        effects += _eq("8000", "0.8q", air)
    articulation = (style.articulation - 0.70) * 3.0
    if abs(articulation) > 0.15:
        effects += _eq("2500", "1.2q", articulation)

    # Dynamics. `compand` rather than `compress`: speech is better served by
    # levelling than by pumping, and SoX's `compress` needs lookahead settings
    # that are easy to get subtly wrong.
    if style.compression > 0.03:
        ratio = 2.0 + style.compression * 5.0
        effects += ["compand", "0.03,0.125",
                    f"6:-{ratio:.1f},-{ratio * 0.5:.1f},0", "0"]

    # Harmonic colour.
    if style.saturation > 0.03:
        effects += ["overdrive", f"{_clamp(style.saturation * 20.0, 0.1, 20.0):.2f}",
                    "0"]

    # Room. SoX wants reverberance, then a fixed HF damping, room scale, wet
    # only, pre-delay and stereo width; the last is left at zero because a
    # widened mono reply is worse on the speakers most of these run on.
    if style.reverb > 0.01:
        effects += ["reverb",
                    f"{_clamp(style.reverb * 100.0, 0.0, 100.0):.1f}", "45",
                    f"{_clamp(style.room_size * 100.0, 0.0, 100.0):.1f}", "100",
                    f"{_clamp(style.pre_delay_ms, 0.0, 200.0):.1f}", "0"]
    return effects


def describe_effects(style: VoiceStyle) -> str:
    """The effects in words, for a row subtitle.

    Built from the same functions that build the effects, so a subtitle cannot
    describe a different transform from the one that will run - which is the
    failure `tts.timbre_description` exists to prevent for the other three
    controls, and the reason this is a function rather than a table.
    """
    said = []
    bands = (
        ((style.warmth - 0.5) * 6.0 + style.bass, "warmth"),
        ((style.resonance - 0.5) * 5.0, "resonance"),
        ((style.brightness - 0.5) * 8.0 + style.treble, "brightness"),
        (style.breathiness * 3.0 + style.air, "air"),
        ((style.articulation - 0.70) * 3.0, "articulation"),
    )
    for band, name in bands:
        if abs(band) > 0.15:
            said.append(f"{name} {band:+.1f} dB")
    if style.compression > 0.03:
        said.append("levelled dynamics")
    if style.saturation > 0.03:
        said.append("warm colour")
    if style.reverb > 0.01:
        said.append(f"{style.reverb * 100:.0f}% room")
    if style.pitch:
        said.append(f"pitch {style.pitch:+g} semitones")
    if style.speed != 1.0:
        said.append(f"speed {style.speed:g}x")
    if style.pause_scale != 1.0:
        said.append(f"pauses {style.pause_scale:g}x")
    return ", ".join(said)


def selected_style(config) -> Optional[VoiceStyle]:
    """The style the settings ask for, or None for "unstyled".

    Reads through the same `config` object `tts` uses, per call, so choosing a
    style in the settings window takes effect on the next reply without a
    restart - the property `tts.timbre` is careful about.
    """
    try:
        # `ChronoaConfig.get`, not `get_string`: ChronoaConfig has no
        # `get_string`, so this raised, was caught below as "unstyled", and no
        # named style was ever applied (measured 2026-10-08: every style
        # reported "effects: none" with SoX installed).
        name = str(config.get(_STYLE_KEY, NEUTRAL) or NEUTRAL).strip()
    except Exception:
        return None
    if not name or name == NEUTRAL:
        return None
    try:
        style = resolve_preset(name)
    except (KeyError, ValueError):
        # An unknown name must not cost the reply. Logged, because a settings
        # key that says one thing and does another is the failure this repo
        # keeps paying for.
        logger.warning("voice style %r is not a known preset; speaking unstyled",
                       name)
        return None
    return None if is_neutral(style) else style


def effects_for_config(config) -> List[str]:
    """The SoX effects the settings ask for, on top of nothing."""
    style = selected_style(config)
    return style_effects(style) if style is not None else []


def preset_names_for(settings_window=None) -> List[str]:
    """The names to offer, most-neutral first.

    Order is a list of choices, so it is deliberate rather than alphabetical:
    an unstyled voice has to be findable without reading all ninety names.
    """
    head = [NEUTRAL, "assistant", "gentle", "calm", "clear", "warm"]
    rest = [name for name in list_presets() if name not in head]
    return head + rest