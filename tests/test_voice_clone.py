"""The consent gate on voice cloning, and the cascade link that carried it.

Two subjects in one file because they are one contract: `tts.engine()` may
answer `"voice-clone"` only when `voice_clone.check()` would say yes, and a
defect in either half shows up as the other half's behaviour. The cascade half
exists because it shipped broken - see
`TestTheCascadeDoesNotEnableCloningByAbsence`.

Every refusal here is asserted as a **distinct sentence**, not as a substring.
The module's own argument is that the three refusals have three different
remedies (turn on the switch, record the agreement, re-record it against the
file you actually want), so a gate that collapsed them would still refuse - and
a test asserting only "it said no" would still pass.
"""

import json
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from shani_chronoa import tts
from shani_chronoa import voice_clone


class _Settings:
    """The two reads this module makes, so nothing else can be a variable.

    `get_bool` **asserts the key it is handed** rather than answering whatever
    it is given. A stub that returns a value for any key would let a wrong
    constant pass every gate test here, which is the same reason the registry
    fixture asserts the path it resolved to.
    """

    def __init__(self, clone_on: bool, chosen: str = ""):
        self.clone_on = clone_on
        self.chosen = chosen

    def get_bool(self, key, default=None):
        assert key == voice_clone.CONSENT_KEY, (
            f"voice_clone read an unrelated key {key!r}; a stub that answers "
            f"everything would let a wrong constant pass every test here")
        return self.clone_on

    def get(self, key, default=None):
        assert key == tts.PiperTTS.CLONE_VOICE_KEY, (
            f"tts read an unrelated preference {key!r}")
        return self.chosen


@pytest.fixture
def clip(tmp_path):
    """A small real file, because the gate hashes bytes rather than names."""
    path = tmp_path / "alice-5s.wav"
    path.write_bytes(b"RIFF----WAVEfmt " + b"\x00" * 200)
    return path


@pytest.fixture
def registry_path():
    """`conftest`'s autouse `_hermetic_env` already redirects `XDG_DATA_HOME`.

    Asserted rather than assumed: the registry is resolved per call *through*
    `files.data_home()`, which is exactly the design that makes it relocatable
    and also exactly the design that writes into a real user's home when a probe
    forgets the variable. A test that silently ran against the developer's own
    consent registry would be testing their machine, not this one.
    """
    path = voice_clone.registry_path()
    home = os.environ["XDG_DATA_HOME"]
    assert path.is_relative_to(home), (
        f"the registry resolved to {path}, outside the test's own "
        f"XDG_DATA_HOME {home} - this run would touch a real user's data")
    return path


def _register(clip, voice="Alice", speaker="Alice", basis="written consent"):
    return voice_clone.register(voice=voice, clip=str(clip),
                                speaker=speaker, basis=basis)


# --------------------------------------------------------------------------
# The control every refusal below depends on.
# --------------------------------------------------------------------------

class TestTheGateOpensWhenItShould:
    """A gate that can only refuse is indistinguishable from a broken one.

    Every refusal test in the file is satisfiable by a `check()` that returns
    `(False, "because")` unconditionally. This is the one assertion that makes
    them mean something, so it is written first and is the reason the rest are
    worth reading.
    """

    def test_a_complete_registration_opens_the_gate(self, clip, registry_path):
        _register(clip)
        allowed, why = voice_clone.check("Alice", config=_Settings(True))

        assert allowed is True, f"a fully agreed voice was refused: {why}"
        assert why == ""

    def test_opening_the_gate_needs_both_halves_not_one(self, clip, registry_path):
        """Both gates, and the control that `check()` can tell them apart.

        The pair is asserted together on purpose: each refusal on its own is
        satisfiable by the gate having been removed wholesale, which is why the
        positive case above exists. What is added here is that the two refusals
        are *different sentences*, which a single blanket refusal cannot be.
        """
        _register(clip)

        _, switch_off = voice_clone.check("Alice", config=_Settings(False))
        _, unregistered = voice_clone.check("Bob", config=_Settings(True))

        assert switch_off != unregistered, (
            "the switch being off and no registration existing produced the "
            "same sentence, so a person cannot tell which remedy applies")
        # The switch's sentence must name the *row*, per the refusal rule this
        # repository holds: a raw key is a spelling no screen uses.
        assert voice_clone.gate_label() in switch_off, (
            f"the switch refusal does not name the Settings row: {switch_off}")
        assert voice_clone.CONSENT_KEY not in switch_off, (
            f"the switch refusal names the raw key rather than the row: {switch_off}")


class TestBothGatesAreRequired:
    def test_a_registered_voice_still_refuses_with_the_switch_off(
            self, clip, registry_path):
        """The switch is a gate in its own right, not a hint.

        Written with a **complete** registration on purpose. Without one, the
        switch-off case is refused by the registry lookup instead, so this test
        would stay green with the switch removed entirely - a witness that
        passes with the defect it names.
        """
        _register(clip)
        assert voice_clone.check("Alice", config=_Settings(True))[0] is True, (
            "the control failed: this voice is not fully agreed, so the "
            "off-by-default assertion below would prove nothing")

        allowed, why = voice_clone.check("Alice", config=_Settings(False))

        assert allowed is False, (
            "a registered voice spoke with the switch off - the switch is not "
            "doing any work")
        assert voice_clone.gate_label() in why, (
            f"the refusal does not name the Settings row to change: {why}")

    def test_the_schema_default_is_off(self):
        """"Off by default" is a property of the **schema**, so read the schema.

        The module cannot enforce this: `get_bool(key, False)` answers `False`
        for an unknown key and for an off one, so the difference shows up only
        on a machine whose schema predates the key - which is every machine
        that has not run setup since the feature landed. A claim about a
        default that is checked anywhere but the declaration is a claim about
        the checker.
        """
        import re
        from pathlib import Path

        schema = Path(__file__).resolve().parents[1] / (
            "usr/share/glib-2.0/schemas/org.shani.chronoa.gschema.xml")
        assert schema.is_file(), f"no schema at {schema}"

        found = re.search(
            r'<key name="%s" type="b">\s*<default>([^<]+)</default>' % re.escape(
                voice_clone.CONSENT_KEY),
            schema.read_text())

        assert found, (
            f"{voice_clone.CONSENT_KEY} is not a boolean key in the schema, so "
            f"a machine that has never run setup reads the Python default "
            f"instead of the declared one")
        assert found.group(1).strip() == "false", (
            f"{voice_clone.CONSENT_KEY} ships defaulting to "
            f"{found.group(1)!r}; a permission whose default is permissive is "
            f"the wrong default")

    def test_registering_does_not_itself_enable_it(self, clip, registry_path):
        """Registration is not consent to *use* it, and both are required.

        The two gates are kept apart deliberately: the registry says a person
        agreed to be built from a recording; the switch says this machine may
        do that. A single gate would let a registry written on one machine
        authorise cloning on another with no local decision.
        """
        _register(clip)

        assert voice_clone.check("Alice", config=_Settings(False))[0] is False, (
            "a registered voice spoke with the switch off - one gate is "
            "sufficient, which is the defect the pair exists to prevent")
        assert voice_clone.check("Alice", config=_Settings(True))[0] is True


class TestTheConsentIsForSpecificAudio:
    """The digest is the load-bearing part, so it is tested by breaking it."""

    def test_replacing_the_recording_stops_the_agreement_applying(
            self, clip, registry_path):
        _register(clip)
        assert voice_clone.check("Alice", config=_Settings(True))[0] is True

        # Same path, different bytes. This is the substitution the digest
        # exists for, and it is the one a path-based check would wave through.
        clip.write_bytes(b"RIFF----WAVEfmt " + b"\x01" * 200)
        allowed, why = voice_clone.check("Alice", config=_Settings(True))

        assert allowed is False, (
            "a replaced recording still carried the earlier agreement")
        assert "agreed to" in why, (
            f"the substitution refusal does not say the audio is the thing "
            f"that was agreed to: {why}")

    def test_the_substitution_refusal_differs_from_a_missing_one(
            self, clip, registry_path):
        _register(clip)
        clip.write_bytes(b"totally different audio")

        allowed, substituted = voice_clone.check("Alice", config=_Settings(True))
        _, missing = voice_clone.check("Bob", config=_Settings(True))

        # Asserted before the comparison, and this is the part that was
        # missing. Without it the two sentences are "the substituted case's
        # answer" and "the missing case's answer" - and when the digest
        # comparison is dropped the first becomes `""`, so two *different*
        # values still satisfy "they differ" and the test passes with the
        # defect that matters most in this file fully present.
        assert allowed is False, (
            "the substituted recording was not refused, so the sentence being "
            "compared is an allowance and the comparison below proves nothing")
        assert substituted != missing, (
            "a substituted recording and an unregistered voice produced one "
            "sentence; the first is more serious and has a different remedy")

    def test_moving_the_recording_is_its_own_answer(self, clip, registry_path):
        _register(clip)
        moved = clip.with_suffix(".moved.wav")
        clip.rename(moved)

        allowed, why = voice_clone.check("Alice", config=_Settings(True))

        assert allowed is False
        assert "not at" in why, f"expected a moved-file sentence, got: {why}"


class TestRevokeTakesEffectImmediately:
    def test_a_revoked_voice_stops(self, clip, registry_path):
        _register(clip)
        assert voice_clone.check("Alice", config=_Settings(True))[0] is True

        assert voice_clone.revoke("Alice") is True

        allowed, why = voice_clone.check("Alice", config=_Settings(True))
        assert allowed is False, "a withdrawn agreement still applied"
        assert "Alice" in why

    def test_revoke_reports_honestly_when_there_was_nothing(self, registry_path):
        assert voice_clone.revoke("Nobody") is False, (
            "revoking a voice that was never registered claimed success")


class TestItFailsClosed:
    def test_an_unreadable_setting_is_a_refusal(self, clip, registry_path):
        """Fails **closed**, which is the opposite of `_piper_enabled`.

        That asymmetry is deliberate and documented in both places: Piper's
        absence must not change how every reply sounds, and this one's absence
        must stop a voice being built from a person who did not agree.
        """
        _register(clip)

        class Unreadable:
            def get_bool(self, key, default=None):
                raise RuntimeError("dconf is not answering")

        allowed, why = voice_clone.check("Alice", config=Unreadable())
        assert allowed is False, (
            "an unreadable permission was treated as consent")

    def test_an_unreadable_registry_is_not_an_absent_one(self, registry_path):
        """The distinction `_read_registry` cannot make and `check` can.

        Before `_registry_problem()` existed these two produced one identical
        sentence, and the corrupt case told the person to *record the
        agreement* - which writes into a file nothing reads back. Failing closed
        was already right; the remedy named was not.
        """
        path = registry_path
        path.parent.mkdir(parents=True, exist_ok=True)

        path.write_text("{not json at all")
        _, corrupt = voice_clone.check("Alice", config=_Settings(True))

        path.unlink()
        _, absent = voice_clone.check("Alice", config=_Settings(True))

        assert corrupt != absent, (
            "a corrupt registry and an absent one produced the same sentence, "
            "so the corrupt case named a remedy that cannot work")
        assert "could not be read" in corrupt, (
            f"the corrupt case does not say the file could not be read: {corrupt}")

    def test_an_unreadable_registry_names_no_voice(self, registry_path):
        """It must not claim to know anything about a person.

        The safe half and the honest half are different requirements: refusing
        is about the gate, saying "Alice was never agreed to" is about a person,
        and a file that cannot be parsed is not evidence about either.

        The assertion is on the **claim form**, not on the word "agreed to".
        The honest sentence has to mention agreement to say it cannot be
        determined - *"whether 'Alice' was ever agreed to cannot be told"* -
        so a bare substring test would flag the correct output and pass the
        incorrect one. What must be absent is the affirmative construction the
        genuine "nothing is registered" refusal actually uses.
        """
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text("{not json at all")

        _, why = voice_clone.check("Alice", config=_Settings(True))

        assert "cannot be told" in why, (
            f"the sentence does not disclaim knowledge of the voice: {why}")
        assert "has been agreed to" not in why, (
            f"a file that could not be parsed was treated as evidence that a "
            f"person did not agree: {why}")

    def test_a_registry_holding_a_list_is_not_a_registry(self, registry_path):
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text('["Alice"]')

        allowed, why = voice_clone.check("Alice", config=_Settings(True))

        assert allowed is False
        assert "could not be read" in why or "not a mapping" in why, why


class TestTheRegistryIsPrivate:
    def test_the_file_and_its_directory_are_not_world_readable(
            self, clip, registry_path):
        """0700 directory, 0600 file - measured, not asserted from the source.

        A consent registry is a list of people who agreed to be built from a
        recording of themselves. It is the one file here whose contents are
        about a person rather than about the machine.
        """
        _register(clip)

        assert stat.S_IMODE(registry_path.stat().st_mode) == 0o600, (
            f"the registry is {stat.S_IMODE(registry_path.stat().st_mode):04o}, "
            f"not 0600")
        assert stat.S_IMODE(registry_path.parent.stat().st_mode) == 0o700, (
            f"its directory is {stat.S_IMODE(registry_path.parent.stat().st_mode):04o}, "
            f"not 0700")

    def test_writing_a_second_voice_does_not_relax_the_modes(
            self, clip, registry_path):
        """The `chmod` is inside the write, so a second write must redo it.

        Worth its own case because `mkdir(mode=0o700)` alone would not: the
        directory is created by the *first* write and left alone by the second,
        and on a machine whose umask is `002` the first is the one that matters.
        """
        _register(clip)
        second = clip.with_name("bob.wav")
        second.write_bytes(b"RIFF" + b"\x02" * 200)

        _register(second, voice="Bob", speaker="Bob")

        assert stat.S_IMODE(registry_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(registry_path.parent.stat().st_mode) == 0o700


class TestRegisterRefusesWhatItCannotRecord:
    @pytest.mark.parametrize("over,fragment", [
        ({"voice": "  "}, "needs a name"),
        ({"speaker": ""}, "say who the voice belongs to"),
        ({"basis": "   "}, "on what basis"),
    ])
    def test_an_unreviewable_record_is_refused(self, clip, registry_path,
                                               over, fragment):
        """A record nobody can review is not a consent record.

        Each of these is refused *in its own right*. The first version of this
        loop overrode one keyword at a time from a dict that still carried the
        others, so `{"speaker": ""}` also removed a valid basis and passed for
        the wrong reason - which is the mistake this parametrisation exists to
        make impossible.
        """
        kwargs = {"voice": "Alice", "speaker": "Alice",
                  "basis": "written consent", "clip": str(clip)}
        kwargs.update(over)

        with pytest.raises(ValueError) as exc:
            voice_clone.register(**kwargs)

        assert fragment in str(exc.value)

    def test_a_registration_that_refused_wrote_nothing(self, clip, registry_path):
        with pytest.raises(ValueError):
            voice_clone.register(voice="Alice", clip=str(clip),
                                 speaker="", basis="consent")

        assert not registry_path.exists(), (
            "a refused registration left a file behind")


# --------------------------------------------------------------------------
# The cascade.
# --------------------------------------------------------------------------

ORDINARY_ENGINES = {"kokoro", "piper", "rhvoice", "espeak-ng", "cloud"}


def _piper(**attrs):
    """A `PiperTTS` with the attributes the cascade reads and nothing else."""
    piper = tts.PiperTTS.__new__(tts.PiperTTS)
    piper.piper_path = "/nonexistent/piper"
    piper.voice_path = "/nonexistent/voice"
    piper.piper_trial = False
    piper.kokoro_trial_voice = ""
    piper.rate = 1.0
    piper._config = _Settings(attrs.get("clone_on", False),
                              attrs.get("chosen", ""))
    return piper


class TestTheCascadeDoesNotEnableCloningByAbsence:
    """The defect: an absent setting selected cloning.

    `_engine` read `clone_reason` as "nothing is in the way", so its default of
    `""` meant *chosen*, and `_clone_reason` returned `""` for the ordinary
    case of nothing configured. Measured on a machine with nothing configured:
    `engine()` returned `'voice-clone'`, so the cascade was unreachable below
    its first link, and no refusal said so - a short-circuit that answers
    `voice-clone` looks like an answer.
    """

    def test_nothing_configured_does_not_select_cloning(self, registry_path):
        piper = _piper()

        chosen = piper.engine()

        assert chosen in ORDINARY_ENGINES, (
            f"with nothing configured at all the cascade answered {chosen!r}; "
            f"a machine that has never touched voice cloning must not be "
            f"answered with a cloned voice")

    def test_a_caller_that_says_nothing_about_cloning_is_told_no(self):
        """The default is the half that was wrong on its own.

        Three existing callers pass only `kokoro_reason`. With the default at
        `""` each of them got `'voice-clone'`; the parameter's own docstring
        said it "defaults to a non-empty string" and the signature said the
        opposite. The docstring was the specification and the code violated it.
        """
        piper = _piper()

        chosen = piper._engine("kokoro is switched off")

        assert chosen in ORDINARY_ENGINES, (
            f"a caller that never mentioned cloning was answered {chosen!r}")

    def test_an_empty_choice_is_a_refusal_not_an_absence(self, registry_path):
        piper = _piper()

        reason = piper._clone_reason()

        assert reason, (
            "_clone_reason() returned '' for the ordinary no-voice-chosen case. "
            "'' is what the cascade reads as *nothing is in the way*, so an "
            "empty string was the most enabling answer available")
        assert "Speak with which recorded voice" in reason, (
            f"the refusal does not name the Settings row that changes it: {reason}")

    def test_a_chosen_voice_that_is_not_registered_is_refused(
            self, registry_path):
        """`_clone_reason` must honour `check()`'s verdict, not re-derive it.

        The link between the gate and the cascade is where the shipped defect
        lived, and this is the one case that pins the *forward* direction: a
        voice that has been chosen and is not agreed to must be refused here
        too. With `check()`'s answer ignored this returns "", which is what the
        cascade reads as "nothing is in the way" - so the defect this file
        records would return through the same door from the other side.
        """
        piper = _piper(clone_on=True, chosen="Alice")

        reason = piper._clone_reason()

        assert reason, (
            "a chosen but unregistered voice produced no refusal, so the "
            "cascade would select a voice nobody agreed to")
        assert "Alice" in reason, (
            f"the refusal does not name the voice that was chosen: {reason}")

    def test_a_fully_agreed_voice_still_refuses_on_the_runtime(
            self, clip, registry_path):
        """Consent and capability are separate, and both are asked.

        On a box with no KittenTTS runtime the gate opens and the cascade must
        still decline rather than pick an engine that cannot run. This is the
        over-fix in the other direction from the shipped defect: a refusal that
        is dropped whenever the switch and the registry are both happy would
        make `engine()` answer `voice-clone` on every install with those two
        set up.
        """
        _register(clip)
        piper = _piper(clone_on=True, chosen="Alice")

        assert voice_clone.check("Alice", config=_Settings(True))[0] is True, (
            "the gate should be open for this case to be about the runtime")

        reason = piper._clone_reason()

        if voice_clone.clone_problem():
            assert reason, (
                "the runtime is missing and the switch and the registry are "
                "both satisfied, yet the cascade would pick an engine that "
                "cannot run")
        else:
            assert reason == "", (
                f"nothing is in the way and a refusal was still produced: {reason}")

    def test_the_refusal_carries_the_gate_s_own_sentence(self, clip, registry_path):
        """One answer, propagated - not a second, differently worded refusal.

        A cascade that re-derived the reason instead of carrying it would be a
        second implementation of the gate, which is the drift this repository
        records for `wpctl` (three copies) and the finite check (four). The
        assertion is on the *whole sentence*, because a shared substring is
        satisfiable by a reworded refusal that says something slightly wrong.
        """
        _register(clip)
        piper = _piper(clone_on=True, chosen="Alice")
        piper.rate = 1.0
        piper._config = _Settings(True, "Nobody")

        _, expected = voice_clone.check("Nobody", config=_Settings(True))
        assert piper._clone_reason() == expected

    def test_each_half_is_load_bearing(self, registry_path):
        """Both halves, and neither one alone is a fix.

        Measured by reverting each alone: the default alone leaves `engine()`
        broken (the explicit pass-through still carries `""` from
        `_clone_reason`), and `_clone_reason` alone leaves every direct caller
        broken (they still inherit the empty default). Two plausible-looking
        half-fixes, each of which a reviewer would accept.

        The default is read from the **signature**, because it is a signature
        fact: a monkeypatched bound method carries its own defaults and would
        assert nothing about the one callers actually inherit.
        """
        import inspect

        default = inspect.signature(tts.PiperTTS._engine).parameters[
            "clone_reason"].default
        assert default, (
            "clone_reason defaults to '' again, which the cascade reads as "
            "*chosen*; a parameter whose absence enables a feature is the "
            "wrong default in a permission")

        assert _piper()._clone_reason(), (
            "_clone_reason must refuse an empty choice; without this half "
            "engine() passes '' straight through and selects cloning anyway")


class TestSpeakHasNoVoiceArgument:
    """The surface is the property: choosing whose voice to speak is not the
    model's to make.

    Asserted rather than asserted-about, because a `voice=` parameter on `speak`
    would be a consent gate checked and passed on an agreement that is about a
    person rather than about the words.
    """

    def test_speak_takes_no_voice_parameter(self):
        from shani_chronoa.skills import speak

        schemas = speak.SKILLS
        assert schemas, "the speak skill declares no schemas"

        for skill in schemas:
            properties = (skill.schema.get("function", {})
                                    .get("parameters", {})
                                    .get("properties", {}))
            assert "voice" not in properties, (
                "the speak tool takes a `voice` argument: the model can then "
                "ask to be answered in somebody's registered voice")
            assert "cloned_voice" not in properties

    def test_the_choice_is_read_from_a_setting_named_in_the_class(self):
        assert tts.PiperTTS.CLONE_VOICE_KEY == "voice-clone-voice"

# --------------------------------------------------------------------------
# The Settings rows.
#
# In their own section because they were **the one half of this feature that
# nothing executed**. `voice_clone.check()`, `tts.engine()` and the schema were
# all driven by the tests above; `_clone_rows` was written, never run, and so it
# shipped calling two `Adw.ComboRow` methods this build does not have. Every
# assertion below is about **what the picker writes**, not about how it looks -
# the appearance was right and the two bugs were both in the write path.
# --------------------------------------------------------------------------

_CLONE_ROW_HARNESS = textwrap.dedent(
    """
    import json
    import pathlib
    import gi
    gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
    from gi.repository import Gtk, Adw, GLib
    from shani_chronoa.config import ChronoaConfig
    from shani_chronoa import tts, voice_clone

    out = {}

    def walk(node):
        found = []
        child = node.get_first_child()
        while child:
            found.extend(walk(child))
            found.append(child)
            child = child.get_next_sibling()
        return found

    TITLE = "Speak with which recorded voice"

    class App(Gtk.Application):
        def __init__(s):
            super().__init__(application_id="test.surface.voice.clone")
            s.config = ChronoaConfig()
            s.window = None
            s._wake_word_active = False
            s.tts = tts.PiperTTS()
        def activate_action(s, name, arg): pass

    app = App()
    work = pathlib.Path.cwd()

    def register(name, speaker, basis):
        clip = work / (name + ".wav")
        clip.write_bytes(b"RIFF----WAVEfmt " + b"\\x00" * 200)
        voice_clone.register(voice=name, clip=str(clip), speaker=speaker,
                             basis=basis)

    def picker_of(window):
        # The clone picker, **selected by its title**. The window holds other
        # ComboRows - "Speaking with", the voice style, the reply style - so a
        # harness taking the first ComboRow it found would drive whichever sorts
        # first, and a fixture shaped unlike the thing it stands in for tests
        # the fixture.
        rows = [r for r in walk(window.get_child())
                if isinstance(r, Adw.ComboRow) and r.get_title() == TITLE]
        return rows[0] if rows else None

    def snapshot(picker):
        model = picker.get_model()
        return {
            "n_items": model.get_n_items(),
            "labels": [model.get_string(i)
                       for i in range(model.get_n_items())],
            "selected": picker.get_selected(),
            "sensitive": picker.get_sensitive(),
        }

    def build():
        window = __import__(
            "shani_chronoa.settings_window", fromlist=["SettingsWindow"]
        ).SettingsWindow(app)
        return window, picker_of(window)

    def on_activate(a):
        # --- nothing registered: the row exists and offers nothing to pick.
        window, picker = build()
        out["empty"] = snapshot(picker) if picker else None
        out["empty_has_row"] = picker is not None

        register("Alice", "Alice", "written consent")
        register("Bob", "Bob", "verbal, recorded")

        # --- two registered: labels, and index 0 is the ordinary voices.
        window, picker = build()
        out["two"] = snapshot(picker) if picker else None

        # **What each index WRITES**, which is the whole property. `set_model`
        # carries strings only, so the value lives beside the model and this is
        # the only place the pairing can be observed.
        a.config.set(tts.PiperTTS.CLONE_VOICE_KEY, "")
        picker.set_selected(1)
        out["writes_index_1"] = a.config.get(tts.PiperTTS.CLONE_VOICE_KEY)
        picker.set_selected(2)
        out["writes_index_2"] = a.config.get(tts.PiperTTS.CLONE_VOICE_KEY)
        picker.set_selected(0)
        out["writes_index_0"] = a.config.get(tts.PiperTTS.CLONE_VOICE_KEY)

        # --- a stored choice that names a voice nobody registered.
        a.config.set(tts.PiperTTS.CLONE_VOICE_KEY, "Carol")
        window, picker = build()
        out["stale"] = snapshot(picker) if picker else None

        # --- a stored choice that IS registered, shown where it was left.
        a.config.set(tts.PiperTTS.CLONE_VOICE_KEY, "Bob")
        window, picker = build()
        out["remembered"] = snapshot(picker) if picker else None

        a.quit()

    app.connect("activate", on_activate)
    GLib.timeout_add(60000, lambda: (app.quit(), False)[1])
    app.run([])
    print("RESULT" + json.dumps(out))
    """
)


@pytest.fixture(scope="module")
def clone_rows(tmp_path_factory, compiled_schema_dir):
    """The picker, read off a **real** `SettingsWindow`.

    Subprocess, like the timbre harness beside it, because the window is a
    `Gtk.Window`: `SettingsWindow.__new__` builds one whose vfuncs are
    uninitialised, and a Python attribute does not shadow a vfunc - the call
    reaches the real method and raises "object is not initialized". Every path
    it reads or writes is redirected, so a registry this creates cannot reach a
    real user's `voices/`.
    """
    work = tmp_path_factory.mktemp("clone-rows")
    (work / "config").mkdir()
    (work / "data").mkdir()
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(Path("usr/lib/shani-chronoa").resolve())
    env["XDG_CONFIG_HOME"] = str(work / "config")
    env["XDG_DATA_HOME"] = str(work / "data")
    env["GSETTINGS_SCHEMA_DIR"] = str(compiled_schema_dir)
    env["GSETTINGS_BACKEND"] = "keyfile"
    env.pop("PYTHONWARNINGS", None)
    proc = subprocess.run([sys.executable, "-c", _CLONE_ROW_HARNESS],
                          capture_output=True, text=True, timeout=180,
                          env=env, cwd=str(work))
    payload = None
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT"):
            payload = json.loads(line[len("RESULT"):])
    assert payload is not None, f"no result:\n{proc.stdout}\n{proc.stderr[-3000:]}"
    payload["_stderr"] = proc.stderr
    return payload


class TestTheCloneRowIsARealControl:
    def test_the_picker_is_on_the_page(self, clone_rows):
        assert clone_rows["empty_has_row"] is True, (
            "no 'Speak with which recorded voice' row was built, so none of the "
            "assertions below are about anything:\n" + clone_rows["_stderr"])

    def test_nothing_registered_leaves_nothing_to_pick(self, clone_rows):
        """One entry and it is the ordinary voices, so the control is inert.

        **And sensitive is asserted, not inferred from the count.** A picker
        showing one entry is either correctly disabled or a control that looks
        available and cannot be moved off its only value, and those look alike.
        """
        empty = clone_rows["empty"]
        assert empty["labels"] == ["The ordinary voices"], empty["labels"]
        assert empty["sensitive"] is False, (
            "the picker is sensitive with nothing to pick, so it offers a choice "
            "it cannot express")

    def test_every_registered_voice_is_offered_beside_the_ordinary_ones(self, clone_rows):
        assert clone_rows["two"]["labels"] == [
            "The ordinary voices", "Alice", "Bob"], clone_rows["two"]["labels"]

    def test_the_picker_becomes_usable_once_something_is_registered(self, clone_rows):
        assert clone_rows["two"]["sensitive"] is True

    @pytest.mark.parametrize("index,expected", [
        (0, ""),          # the ordinary voices, which is the empty setting
        (1, "Alice"),
        (2, "Bob"),
    ])
    def test_each_entry_writes_the_voice_it_names(self, clone_rows, index, expected):
        """**The property both of the row's bugs broke.**

        `Adw.ComboRow.append(label, value)` does not exist on libadwaita 1.9.1
        and `get_selected_item().get_value()` does not either - `get_selected_
        item()` returns a plain `Gtk.StringObject`. So the first version crashed
        the whole Settings window on build, and the second would have crashed on
        every selection change *even with the first fixed*.

        Asserted as a mapping rather than as "the setting changed", because a
        picker that always wrote the same voice would pass the weaker form.
        """
        written = clone_rows[f"writes_index_{index}"]
        assert written == expected, (
            f"picking entry {index} wrote {written!r} rather than {expected!r}")

    def test_the_entry_a_person_left_it_on_is_the_entry_shown(self, clone_rows):
        assert clone_rows["remembered"]["selected"] == 2, (
            "a stored choice naming a registered voice is not the entry shown, so "
            "the row opens contradicting the setting: "
            f"{clone_rows['remembered']}")

    def test_a_stored_choice_naming_nobody_falls_back_to_the_ordinary_voices(
            self, clone_rows):
        """Not an exception, and not the stale name.

        A setting left pointing at a revoked voice is the ordinary state after a
        revoke. The gate refuses either way - that is `TestRevokeTakesEffect-
        Immediately` - so what is pinned here is that **the row does not claim a
        selection it cannot honour**.
        """
        stale = clone_rows["stale"]
        assert stale["selected"] == 0, (
            "a stored voice that is not registered is still shown as selected: "
            f"{stale}")
        assert stale["labels"][stale["selected"]] == "The ordinary voices", stale
