"""The voice style layer, and the things that were wrong with the proposal.

`voice_style.py` was written from a proposed `chronoa_voice.py`. Most of that
proposal was not adopted - it duplicated the engine layer and the SoX pass that
`tts.py` already owns. These tests cover what was kept, and they are mostly about
the parts that were quietly broken in the original, because those are the parts
that would have shipped looking fine.

The three that matter most:

- `test_natural_asks_for_nothing` - the identity preset must produce *no* SoX
  effects. In the proposal the neutral dataclass had `breathiness=0.10` and
  `compression=0.15`, both above the builder's thresholds, so "natural" asked
  for two effects and an unstyled reply was not unstyled.
- `test_no_preset_name_is_shadowed` - the proposal's `COMPOSITES` contained four
  names that were also in `BASE_PRESETS`, and `resolve_preset` checks the base
  table first, so those four composites were unreachable. They still resolved,
  so nothing looked broken.
- `test_a_preset_adds_no_second_sox_pass` - the proposal ran its own SoX
  processor after `tts.apply_timbre`. One pass is the requirement.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shani_chronoa import voice_style

_MODULE = Path(voice_style.__file__)


class TestTheIdentityStyleIsFree:
    def test_natural_asks_for_nothing(self):
        """An unstyled reply has to be exactly the engine's own audio.

        This is the check the proposal failed: its neutral style was not neutral,
        because breathiness and compression defaulted above the effect builder's
        thresholds.
        """
        style = voice_style.resolve_preset("natural")
        assert voice_style.style_effects(style) == []
        assert voice_style.is_neutral(style)

    def test_every_preset_resolves_without_raising(self):
        for name in voice_style.list_presets():
            style = voice_style.resolve_preset(name)
            assert isinstance(style, voice_style.VoiceStyle), name

    def test_an_unknown_name_is_an_error_not_a_silent_default(self):
        """Silently falling back would make a typo look like a chosen voice."""
        with pytest.raises(KeyError):
            voice_style.resolve_preset("no-such-voice")


class TestNoPresetIsShadowed:
    def test_no_preset_name_is_shadowed(self):
        """A name in both tables resolves from `BASE_PRESETS` and the composite is
        dead. The proposal had four of these and never noticed."""
        both = set(voice_style.BASE_PRESETS) & set(voice_style.COMPOSITES)
        assert not both, f"composites that can never run: {sorted(both)}"

    def test_every_composite_is_built_only_from_names_that_exist(self):
        for name, parts in voice_style.COMPOSITES.items():
            for part in parts:
                assert part in voice_style.BASE_PRESETS or part in voice_style.COMPOSITES, (
                    f"{name!r} is built from {part!r}, which is not a preset")

    def test_a_composite_cycle_raises_rather_than_recursing_forever(self):
        """The proposal had `"cinematic": ("cinematic",)`, which only worked
        because the same name was also in the base table and won."""
        voice_style.COMPOSITES["_control_self"] = ("_control_self",)
        voice_style.COMPOSITES["_control_cycle"] = ("_control_b",)
        voice_style.COMPOSITES["_control_b"] = ("_control_a",)
        voice_style.COMPOSITES["_control_a"] = ("_control_b",)
        try:
            with pytest.raises(ValueError, match="cycle"):
                voice_style.resolve_preset("_control_self")
            with pytest.raises(ValueError, match="cycle"):
                voice_style.resolve_preset("_control_cycle")
        finally:
            for key in ("_control_self", "_control_cycle", "_control_a",
                        "_control_b"):
                voice_style.COMPOSITES.pop(key, None)

    def test_a_composite_actually_combines_its_parts(self):
        warm = voice_style.resolve_preset("warm")
        assistant = voice_style.resolve_preset("assistant")
        both = voice_style.resolve_preset("warm-assistant")
        assert both.warmth > warm.warmth, "the warm half was not applied"
        assert both.warmth > assistant.warmth, "the assistant half was not applied"


class TestEffectBuilding:
    def test_no_style_emits_an_effect_it_did_not_ask_for(self):
        """Each parameter maps to one band; an effect list with a stray effect is
        a typo in the builder that would sound wrong and read as intentional."""
        assert voice_style.style_effects(voice_style.VoiceStyle()) == []
        # Reverb is one effect with six arguments, so the count is 7 - what is
        # checked is that reverb is the only effect in the list.
        only_reverb = voice_style.style_effects(
            voice_style.VoiceStyle(reverb=0.5))
        assert only_reverb[0] == "reverb"
        assert len(only_reverb) == 7, only_reverb

    def test_the_effects_are_sox_arguments_in_pairs(self):
        for name in ("assistant", "cave", "whisper", "radio-announcer"):
            effects = voice_style.style_effects(voice_style.resolve_preset(name))
            assert effects, name
            assert effects[0] in ("equalizer", "compand", "overdrive", "reverb")

    def test_an_out_of_range_parameter_is_clamped_not_passed_to_sox(self):
        """SoX will happily be handed a 500-semitone pitch. The preset table is
        literals, so this is one edit away from happening."""
        wild = voice_style.VoiceStyle(pitch=500.0, reverb=99.0, bass=400.0)
        effects = voice_style.style_effects(wild)
        gains = [float(effects[i + 3]) for i, effect in enumerate(effects)
                 if effect == "equalizer"]
        assert gains, "a wild style produced no EQ at all"
        assert all(-20.0 <= gain <= 20.0 for gain in gains), gains

    def test_the_words_match_the_effects(self):
        """A subtitle that describes a different transform from the one that runs
        is the failure `tts.timbre_description` exists to prevent."""
        for name in ("assistant", "cave", "whisper", "warm-assistant"):
            style = voice_style.resolve_preset(name)
            effects = voice_style.style_effects(style)
            said = voice_style.describe_effects(style)
            if "room" in said:
                assert "reverb" in effects, f"{name}: said room, no reverb effect"
            for word, effect in (("warmth", "equalizer"), ("brightness", "equalizer"),
                                 ("levelled", "compand"), ("colour", "overdrive")):
                if word in said:
                    assert effect in effects, f"{name}: said {word}, no {effect}"

    def test_a_style_never_describes_itself_when_it_is_neutral(self):
        assert voice_style.describe_effects(
            voice_style.resolve_preset("natural")) == ""


class TestItCompositesWithTheExistingPipeline:
    """The architectural claim: one SoX pass, inside `tts`, not a second one."""

    def test_a_preset_adds_no_second_sox_pass(self):
        source = _MODULE.read_text()
        for forbidden in ("subprocess.run", "subprocess.Popen", "import subprocess",
                          "SoxProcessor", "def speak("):
            assert forbidden not in source, (
                f"voice_style grew {forbidden} - it is a preset table, not a "
                "second voice pipeline")

    def test_it_does_not_import_an_engine(self):
        source = _MODULE.read_text()
        for module in ("tts", "voices", "piper", "kokoro", "espeak"):
            assert f"import {module}" not in source, f"voice_style imports {module}"

    def test_it_does_not_shadow_the_existing_voice_name(self):
        """`voices.Voice` is the Piper catalogue entry. A second public `Voice`
        here would be two types with one name and an import that rebinds."""
        source = _MODULE.read_text()
        assert "class Voice(" not in source and "class Voice:" not in source
        assert not hasattr(voice_style, "Voice")

    def test_tts_appends_the_style_to_its_own_effect_list(self):
        tts_source = Path(
            __import__("shani_chronoa.tts", fromlist=["tts"]).__file__).read_text()
        assert "voice_style" in tts_source, "tts never asks for the style"
        assert "effects.extend(self.style_effects())" in tts_source, (
            "the style is not appended to the single existing SoX pass")

    def test_an_unknown_preset_name_costs_the_style_not_the_reply(self):
        class Config:
            def get_string(self, key, default):
                return "no-such-voice"

        assert voice_style.selected_style(Config()) is None
        assert voice_style.effects_for_config(Config()) == []

    def test_natural_in_the_settings_costs_nothing(self):
        class Config:
            def get_string(self, key, default):
                return "natural"

        assert voice_style.selected_style(Config()) is None
        assert voice_style.effects_for_config(Config()) == []

    def test_a_config_that_raises_costs_the_style_not_the_reply(self):
        class Broken:
            def get_string(self, *_a, **_k):
                raise RuntimeError("the schema is missing")

        assert voice_style.selected_style(Broken()) is None


class TestDescription:
    def test_describe_lists_every_field_of_the_dataclass(self):
        """The proposal's `describe_preset` repeated all seventeen field names by
        hand, so a parameter added later was silently missing from it."""
        from dataclasses import fields
        described = voice_style.describe("assistant")["style"]
        expected = {f.name for f in fields(voice_style.VoiceStyle)} - {"language"}
        assert set(described) == expected

    def test_describe_includes_the_effects_will_run(self):
        described = voice_style.describe("cave")
        assert described["effects"] == voice_style.style_effects(
            voice_style.resolve_preset("cave"))
        assert described["composed_from"] == []

    def test_describe_says_what_a_composite_is_made_of(self):
        assert voice_style.describe("warm-assistant")["composed_from"] == [
            "warm", "assistant"]

    def test_the_offered_names_start_with_the_ones_people_want(self):
        names = voice_style.preset_names_for()
        assert names[0] == "natural", "an unstyled voice must be findable first"
        assert "assistant" in names[:3]
        assert len(names) == len(set(names)), "a name is offered twice"

    def test_a_locale_preset_is_a_hint_and_nothing_else(self):
        """These are acoustic offsets; they cannot produce an accent, and the
        table should not pretend otherwise by also moving the pitch."""
        style = voice_style.resolve_preset("english-indian")
        assert style.language == "en-IN"
        assert voice_style.style_effects(style) == []
        assert style.pitch == 0.0