"""Text-to-speech: the best engine the user has asked for, degrading one step at
a time.

Kokoro is a neural voice, run by sherpa-onnx's `sherpa-onnx-offline-tts`
the way Piper's program runs Piper (both installed by `voices.py` from setup),
and it is **opt-in**
(`kokoro-tts-enabled`, default false): the chain below it - Piper, then RHVoice,
then espeak-ng - is what runs unless someone turns Kokoro on. It was the default
when it was added, which the measurements in `AGENTS.md` argue against on this
class of hardware: 6.39 s of wall clock for 5.225 s of audio (RTF 1.22) on an
i7-1165G7 with onnxruntime's CPU provider, against 0.018 s for 3.7 s from
espeak-ng. Because `synthesize_to_bytes` returns the whole reply's WAV, a
default Kokoro puts ~6 s of silence in front of every answer, on top of the
model's own think time - and it was never benchmarked before being made the
first choice. With it off, espeak-ng keeps every image the voice it has today.

This class is the dispatcher for all four engines and owns the one piece of
state that must not be per-engine: the degraded-after-three-failures cooldown.
It is still named `PiperTTS` because that is what it was when it was written and
renaming it would touch every call site for no behavioural gain - the engines it
fronts are all named by `engine()`, which is what `skills/speak.py` reports to
the user.

**Timbre: pitch, tempo and rate are SoX's job, not an engine's.** Every engine
here has its own way of being told to speak faster - Piper's `--length_scale`,
Kokoro's `speed` input, espeak-ng's words-per-minute - and none of them can be
told to change pitch. So those three controls are applied once, after whichever
engine produced the WAV, by `apply_timbre`. `sox` is an optdepend and never a
hard one: a user without it gets a working assistant that says which transform
was skipped, not a half-configured one and never a reported shift that did not
happen.
"""

import logging
import subprocess
import os
import shutil
import tempfile
from typing import Optional

from shani_chronoa import files

logger = logging.getLogger(__name__)


#: espeak-ng voice variant: female (its default for a language is male).
ESPEAK_VARIANT = "+f3"

#: SoX is CPU-only and works on the whole WAV in one pass; measured at well
#: under a second for a 3.7 s reply. The bound is here for the pathological
#: case, and a timeout is reported as a skip rather than as a lost reply.
SOX_TIMEOUT = 30

#: The three timbre controls, and the identity value of each. The range is the
#: schema's own, kept here because `Adw.SpinRow` and the `<range>` element have
#: to agree: a spin row that could pick a value the schema refuses would be a
#: control that looks like it works and does not.
PITCH_RANGE = (-6.0, 6.0)
TEMPO_RANGE = (0.5, 2.0)
RATE_RANGE = (0.5, 2.0)

#: Engines this dispatcher has already announced in this process. `engine()` is
#: called on every `synthesize()` and again by `skills/speak.py`, so announcing
#: each choice without this would put the same line in the journal twice per
#: reply. The alternative - not saying it at all - is the failure this repo
#: keeps recording: a user who cannot tell which engine spoke to them, and why
#: the better one was skipped, has no way to ask.
_ANNOUNCED: set = set()


def _light_mouth(text: str) -> None:
    """A short flash on the mouth organ while speech is generated.

    A heartbeat rather than a duration: the engine takes from milliseconds to
    seconds depending on the voice and the hardware, and a light that stays on
    for a long synthesis is more informative than one that flickers. Never
    raises - it is called from the audio path.
    """
    try:
        from shani_chronoa import body

        activity = body.body.use("mouth", "preparing speech", text[:60].strip(),
                                 deadline=body.DEFAULT_DEADLINE["mouth"])
        body.body.done(activity)
    except Exception:                                   # noqa: BLE001
        pass


class PiperTTS:
    """Text-to-speech using the best engine installed."""

    def __init__(self, voice: str = "en_US-lessac-medium", piper_path: Optional[str] = None,
                 config=None) -> None:
        self.voice = voice
        # Only the TTS binary, installed as piper-tts: on Arch /usr/bin/piper
        # is the GTK gaming-mouse configurator (extra/piper), which the old
        # default would have launched with TTS arguments
        # ...or the user-local release build that Chronoa's setup installs (voices.py)
        if not piper_path:
            from shani_chronoa import voices
            piper_path = voices.piper_binary()
        self.piper_path = piper_path or "/usr/bin/piper-tts"
        self.voice_path = self._get_voice_path(voice)
        #: Speaking speed, 1.0 normal (the `speech-rate` setting): Piper's
        #: --length_scale is its inverse, espeak-ng's -s is words per minute.
        self.rate = 1.0
        #: Optional, and not just for tests. `engine()` is asked which engine
        #: can speak before every reply and `skills/speak.py` builds its own
        #: dispatcher, so the settings have to be readable without the app
        #: handing one in. Built on first use and kept, because building a
        #: `Gio.Settings` per `engine()` call would cost more than the answer.
        self._config = config
        #: Set by the setup window's "Listen to Kokoro" button: speak this one
        #: Kokoro voice now, whatever the saved settings say, without saving it.
        self.kokoro_trial_voice: Optional[str] = None
        #: The other half of trying a voice: a Piper voice is heard as Piper even
        #: while Kokoro is switched on.
        self.piper_trial = False

    def _settings(self):
        """The GSettings wrapper, built on first use and kept."""
        if self._config is None:
            from shani_chronoa.config import ChronoaConfig

            self._config = ChronoaConfig()
        return self._config

    def use_config(self, config) -> None:
        """Adopt the app's own settings object.

        Called by the settings window whenever a value this dispatcher reads
        changes, for the same reason `_set_speech_rate` assigns `tts.rate`:
        two `ChronoaConfig` instances are two views of one store and do not see
        each other's writes - measured here, writing `tts-pitch` through one and
        reading it through another returned 0.0 while the writer saw 3.0 - so a
        dispatcher that kept its own would ignore the slider until the app was
        restarted.
        """
        self._config = config

    def _get_voice_path(self, voice: str) -> str:
        """Get the path to the Piper voice model file."""
        voice_dir = files.data_home() / "piper" / "voices"
        voice_file = f"{voice}.onnx"
        path = os.path.join(voice_dir, voice_file)
        if not os.path.exists(path):
            # Try system-wide location
            path = f"/usr/share/piper/voices/{voice_file}"
        return path

    # Engines, best first.
    #
    #   kokoro     neural, 82M parameters, Apache-2.0. Opt-in through
    #              `kokoro-tts-enabled` and *not* first by default: measured at
    #              RTF 1.2 (7.7 s of wall clock for 6.4 s of audio, sherpa-onnx
    #              on a ShaniOS slot's CPU), so it cannot finish before playback
    #              would start. sherpa-onnx's release carries its own
    #              onnxruntime, so nothing system-wide is needed;
    #              `voices.install_kokoro` fetches it and the model.
    #   piper      still the same tarball install as before. It is not in the
    #              Arch repos (the distribution's `piper` is the GTK
    #              gaming-mouse configurator, a different program), and it is
    #              kept because an existing setup should keep working and
    #              because a fallback chain one engine deep is not a chain.
    #   rhvoice    extra/rhvoice + a rhvoice-language-* and a rhvoice-voice-*;
    #              sounds natural when both halves are present.
    #   espeak-ng  a hard `depends`: every Shanios image ships it, so speech
    #              always works, at the cost of sounding like a 1970s speech
    #              chip. It is the floor, not a preference.
    #
    # Kokoro is skipped for text in one of the person's extra languages'
    # scripts: it has no voice for those, and reading them out with an English
    # voice would be a confident wrong answer rather than a degraded one. Those
    # replies keep going to the Piper voice for their language, exactly as
    # before this existed.
    def engine(self, text: str = "") -> Optional[str]:
        """The best engine for `text`, or None when there is none.

        `text` is optional because two different questions get asked and both
        deserve an honest answer. `skills/speak.py` calls `engine()` with no
        argument to report what *could* speak here - a question about the
        machine. `_synthesize` calls `engine(text)` to pick the one that should
        speak *this reply* - a question about the machine and the language, and
        a different answer for a reply in Hindi than for one in English.
        """
        reason = self._kokoro_reason(text)
        chosen = self._engine(reason)
        self._announce(chosen, reason)
        return chosen

    #: The key that chooses the neural voice. Named rather than spelled at each
    #: use because the settings row, the schema, this gate and the tests all
    #: have to agree on it, and a typo in one of them is a switch that does
    #: nothing with nothing to say so.
    KOKORO_KEY = "kokoro-tts-enabled"

    def _kokoro_enabled(self) -> bool:
        """Whether the user has asked for the neural voice (or is trying one out in setup)."""
        if self.piper_trial:
            return False
        if self.kokoro_trial_voice:
            return True
        try:
            return bool(self._settings().get_bool(self.KOKORO_KEY, False))
        except Exception:  # noqa: BLE001 - an unreadable setting must not stop speech
            return False

    def _kokoro_reason(self, text: str) -> str:
        """Why Kokoro would not speak `text` here, or "" when it would.

        Checked in this order because the first answer is the one the user can
        act on: it is switched off, no runtime, no espeak-ng, an unknown voice,
        a missing model. Only when none of those applies is the *language* the
        reason, which is not a fault at all - and is worded so the log line does
        not read as one.

        The switch is checked *first*, before the missing halves. A user who
        has never enabled the neural voice does not need a paragraph about
        python-onnxruntime-cpu; they would be told to install a 100 MB runtime
        for a feature they did not ask for, and the reason they would see would
        name a package rather than the switch they can actually reach.
        """
        if not self._kokoro_enabled():
            return ("Kokoro is switched off in Settings (kokoro-tts-enabled); it "
                    "is opt-in because it takes about 6 s to produce 5 s of "
                    "audio on a four-core laptop, which is 6 s of silence before "
                    "the reply starts")

        reason = self.kokoro_problem()
        if reason or not text:
            return reason
        from shani_chronoa import languages
        if languages.script_of(text):
            return ("it has no voice for the script this text is written in, "
                    "so the reply keeps the voice for its own language")
        return ""

    def _engine(self, kokoro_reason: str) -> Optional[str]:
        """The cascade, given why Kokoro was not chosen.

        `kokoro_reason` empty is the whole opt-in gate: `_kokoro_reason`
        returns "" only when the user has turned Kokoro on *and* it can speak
        this text, so this cascade runs unchanged in every other case and
        Kokoro is reachable again by flipping one setting - which is what
        `engine()` announces, and what the test that reverts this ordering
        fails on.
        """
        if not kokoro_reason:
            return "kokoro"
        if os.path.exists(self.piper_path) and os.path.exists(self.voice_path):
            return "piper"
        if shutil.which("RHVoice-test") and self._rhvoice_has_voice():
            return "rhvoice"
        if shutil.which("espeak-ng"):
            return "espeak-ng"
        return None

    @staticmethod
    def _announce(chosen: Optional[str], kokoro_reason: str) -> None:
        """Say which engine is speaking, and why the better one was skipped."""
        key = (chosen, kokoro_reason)
        if key in _ANNOUNCED:
            return
        _ANNOUNCED.add(key)
        if chosen == "kokoro":
            logger.info("Speaking with Kokoro")
        elif kokoro_reason:
            logger.info("Speaking with %s: Kokoro was not used because %s",
                        chosen or "no engine", kokoro_reason)
        else:
            logger.info("Speaking with %s", chosen or "no engine")

    KOKORO_VOICE_KEY = "kokoro-voice"

    def kokoro_voice(self) -> str:
        """The Kokoro voice chosen in setup, or the default when the setting names none we know."""
        from shani_chronoa import voices
        if self.kokoro_trial_voice in voices.KOKORO_VOICES:
            return self.kokoro_trial_voice
        try:
            chosen = str(self._settings().get(self.KOKORO_VOICE_KEY, voices.KOKORO_DEFAULT_VOICE))
        except Exception:  # noqa: BLE001
            chosen = voices.KOKORO_DEFAULT_VOICE
        return chosen if chosen in voices.KOKORO_VOICES else voices.KOKORO_DEFAULT_VOICE

    def kokoro_problem(self) -> str:
        """Why Kokoro cannot speak here ('' when it can), for the settings and setup surfaces."""
        from shani_chronoa import voices
        return voices.kokoro_problem(self.kokoro_voice())

    def _kokoro_command(self, text: str, output_file: str) -> "list[str]":
        from shani_chronoa import voices
        cmd = [voices.kokoro_binary()]
        cmd += [f"{option}={path}" for option, path in voices.kokoro_files().items()]
        cmd += [f"--sid={voices.KOKORO_VOICES[self.kokoro_voice()].sid}",
                f"--kokoro-length-scale={1.0 / self.rate:.3f}", "--num-threads=2",
                f"--output-filename={output_file}",
                # the text is the one positional argument; a leading '-' would be read as an option
                text.lstrip("- ") or "."]
        return cmd

    @staticmethod
    def _rhvoice_voice_installed(name: str) -> bool:
        return any(os.path.isdir(os.path.join(d, name))
                   for d in ("/usr/share/RHVoice/voices", "/usr/local/share/RHVoice/voices"))

    @staticmethod
    def _rhvoice_has_voice() -> bool:
        return any(os.path.isdir(d) and os.listdir(d)
                   for d in ("/usr/share/RHVoice/voices", "/usr/local/share/RHVoice/voices"))

    def _lang(self) -> str:
        """Piper voice id en_US-lessac-medium -> espeak language en-us."""
        return self.voice.split("-", 1)[0].replace("_", "-").lower() or "en"

    def _piper_voice_for(self, text: str) -> "tuple[str, Optional[int]]":
        """The voice for this text: one of the person's languages' voices when the text is in its script."""
        from shani_chronoa import languages, voices
        try:
            pick = languages.voice_for_text(text)
        except Exception:  # noqa: BLE001 - choosing a voice must never stop speech
            pick = None
        if pick:
            path = self._get_voice_path(pick[0])
            if os.path.exists(path):
                return path, voices.VOICES[pick[0]].speaker
        own = voices.VOICES.get(self.voice)
        return self.voice_path, own.speaker if own else None

    def _espeak_lang(self, text: str) -> str:
        from shani_chronoa import languages
        try:
            return languages.espeak_language(text) or self._lang()
        except Exception:  # noqa: BLE001
            return self._lang()

    #: assistd's piper/service.rs: three failures within a minute put speech
    #: in a degraded state - every reply had been paying a 60 s timeout - then
    #: after a cool-down one attempt is let through, and success ends it.
    FAILURES_TO_DEGRADE = 3
    FAILURE_WINDOW = 60.0
    COOLDOWN = 120.0

    def synthesize(self, text: str, output_file: str) -> bool:
        """Synthesize text to a WAV file with the best available engine, unless speech is degraded.

        The timbre pass runs here rather than inside `_synthesize`, because it
        is the one step that is not an engine: whichever engine produced the
        WAV, SoX rewrites it in place afterwards. A skipped transform is never
        a failure - the audio is already there and correct - so it is reported
        and the reply still plays. Counting it as a failure would put speech
        into the degraded-after-three-failures cooldown for a preference nobody
        asked about.
        """
        import time as _time
        now = _time.monotonic()
        until = getattr(self, "_degraded_until", 0.0)
        if until and now < until:
            return False
        ok = self._synthesize(text, output_file)
        if ok:
            skipped = self.apply_timbre(output_file)
            if skipped:
                logger.warning("Voice left as the engine wrote it: %s", skipped)
        failures = [t for t in getattr(self, "_failures", []) if now - t < self.FAILURE_WINDOW]
        if ok:
            if until:
                logger.info("Speech output works again")
            self._failures, self._degraded_until, self.degraded_reason = [], 0.0, ""
            return True
        failures.append(now)
        self._failures = failures
        if len(failures) >= self.FAILURES_TO_DEGRADE:
            self._degraded_until = now + self.COOLDOWN
            if not getattr(self, "degraded_reason", ""):
                self.degraded_reason = (f"{self.engine() or 'no engine'} failed {len(failures)} times in a minute; "
                                        f"speech is paused for {int(self.COOLDOWN)} s")
                logger.error("Speech output unavailable: %s", self.degraded_reason)
        return False

    degraded_reason = ""

    def _synthesize(self, text: str, output_file: str) -> bool:
        # The engine is chosen for *this text*, not for this machine: Kokoro is
        # skipped below for a reply in one of the extra languages' scripts, and
        # `engine("")` - the form `skills/speak.py` uses - still reports the
        # best engine that could speak here at all.
        eng = self.engine(text)
        if eng == "kokoro":
            cmd = self._kokoro_command(text, output_file)
        elif eng == "piper":
            voice_path, speaker = self._piper_voice_for(text)
            cmd = [self.piper_path, "--model", voice_path, "--output_file", output_file,
                   "--length_scale", f"{1.0 / self.rate:.3f}"]
            if speaker is not None:
                cmd += ["--speaker", str(speaker)]
        elif eng == "rhvoice":
            cmd = ["RHVoice-test", "-o", output_file]
            # Chronoa's voice is a woman's in every engine: Piper's default
            # (lessac) is, and SLT is the RHVoice voice the package suggests
            # - but without -p RHVoice speaks its default English voice.
            if self._rhvoice_voice_installed("slt"):
                cmd += ["-p", "slt"]
        elif eng == "espeak-ng":
            # espeak-ng's default for a language is a male voice, and it is
            # the one engine every Shanios image ships: '+f3' is its own
            # female variant.
            cmd = ["espeak-ng", "--stdin", "-v", self._espeak_lang(text) + ESPEAK_VARIANT, "-s", str(int(175 * self.rate)),
                   "-w", output_file]
        else:
            logger.error("No speech engine: install kokoro (+ its model), piper, "
                         "rhvoice (+ a voice) or espeak-ng")
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

    # -- timbre: pitch, tempo and rate, applied by SoX ----------------------

    #: The three settings, with the identity value of each. The ranges are the
    #: schema's own and the settings window's `Adw.SpinRow` ranges are the same
    #: numbers, because a spin row that can pick a value the schema refuses is a
    #: control that looks like it works and does not.
    TIMBRE_KEYS = {"pitch": ("tts-pitch", 0.0, PITCH_RANGE),
                   "tempo": ("tts-tempo", 1.0, TEMPO_RANGE),
                   "rate": ("tts-rate", 1.0, RATE_RANGE)}

    @staticmethod
    def _read_double(config, key: str, default: float, bounds: "tuple[float, float]") -> float:
        """One timbre setting, clamped to its range.

        GSettings enforces the `<range>`, so a value from the window or from the
        CLI is already inside it. `gsettings set` refuses an out-of-range value
        itself, but a *missing* schema hands back whatever default this call
        passes, and SoX is perfectly happy to be asked for a 500-semitone
        pitch. Clamped and logged, rather than clamped silently: a slider that
        does not do what it says is the failure this file keeps warning about.
        """
        try:
            value = float(config.get_double(key, default))
        except (TypeError, ValueError):
            value = default
        low, high = bounds
        if not low <= value <= high:
            logger.warning("%s is %s, outside %s..%s; using %s",
                           key, value, low, high, min(high, max(low, value)))
            value = min(high, max(low, value))
        return value

    def timbre(self) -> "dict[str, float]":
        """The three timbre settings as they stand, read fresh every call.

        Per call, never at construction: the settings window writes them while
        the app is running, and a value captured in `__init__` would be a
        slider that has to be restarted to take effect.
        """
        config = self._settings()
        return {name: self._read_double(config, key, default, bounds)
                for name, (key, default, bounds) in self.TIMBRE_KEYS.items()}

    @staticmethod
    def sox_path() -> str:
        """The `sox` binary, or "" when it is not installed.

        An optdepend and never a hard depend, for the reason every optional
        engine in this file is optional: a user without it must get a working
        assistant that says which transform was skipped, not a half-configured
        one. `extra/sox` on Arch, the same name on Debian.
        """
        return shutil.which("sox") or ""

    def timbre_effects(self) -> "list[str]":
        """The SoX effect arguments for the configured transforms, in order.

        Empty for `pitch=0`, `tempo=1.0`, `rate=1.0`, and that emptiness is the
        point: `apply_timbre` then returns without running SoX at all, so the
        default settings do not re-encode the engine's WAV on every reply.

        The three effects are chosen so that none is a duplicate of another,
        which is not what the names suggest and is the reason the mapping is
        written down rather than guessed. Measured on espeak-ng's 3.663 s of
        22.05 kHz output:

        | setting | SoX effect | result |
        |---|---|---|
        | pitch +3 semitones | `pitch 300` | 3.663 s - frequency only |
        | tempo 1.3           | `tempo 1.300` | 2.817 s - pitch preserved |
        | rate 1.25           | `speed 1.250` | 2.930 s - both, chipmunk |

        `rate` is SoX's `speed` effect and not SoX's `rate` effect: `rate` takes
        an absolute sample rate (`sox --help-effect rate` prints `RATE[k]`), so
        `rate 27562` resamples 22.05 kHz up and rewrites the header, which
        leaves the *duration* at 3.663 s and changes only the frequency - the
        same thing the pitch control already does. `speed` is the one that
        changes speed and pitch together, which is what a control called a rate
        is for.

        `pitch` takes cents, not semitones (`pitch [-q] shift-in-cents`), so the
        conversion happens here; without it every semitone would be 100x too
        large. The order is pitch, then tempo, then speed: the two expensive
        frequency-domain stages run on the engine's own signal length, and the
        cheap resample is last.
        """
        values = self.timbre()
        effects: list = []
        semitones = values["pitch"]
        if semitones:
            cents = int(round(semitones * 100))
            effects += ["pitch", f"{cents}"]
        if values["tempo"] != 1.0:
            effects += ["tempo", f"{values['tempo']:.3f}"]
        if values["rate"] != 1.0:
            effects += ["speed", f"{values['rate']:.3f}"]
        # A named voice style, appended to the same list so the reply goes
        # through SoX **once**. Running the style as a second pass after this one
        # was the alternative and it is worse: two decodes, two encodes, and the
        # first pass's `gain -n` normalisation feeding the second pass's EQ.
        effects.extend(self.style_effects())
        return effects

    def style_effects(self) -> list:
        """The SoX effects of the selected named style, or [].

        Delegated to `voice_style`, which owns the preset table. The tonal and
        room effects are appended after pitch/tempo/speed for the same reason
        those three come first: the expensive frequency-domain stages run on the
        shortest signal left.
        """
        try:
            from shani_chronoa import voice_style
            return voice_style.effects_for_config(self._settings())
        except Exception:
            # A broken preset table must cost the style, never the reply.
            logger.warning("could not build the voice style effects",
                           exc_info=True)
            return []

    def timbre_description(self) -> str:
        """The configured transforms, in words, for a row subtitle or a log.

        One string, built once, so the settings window and the warning in
        `synthesize` cannot describe the same transform two different ways.
        """
        values = self.timbre()
        said = []
        if values["pitch"]:
            said.append(f"pitch {values['pitch']:+g} semitones")
        if values["tempo"] != 1.0:
            said.append(f"tempo {values['tempo']:g}x")
        if values["rate"] != 1.0:
            said.append(f"rate {values['rate']:g}x, which changes speed and pitch together")
        # The named style, in words, from the same function that builds its
        # effects. Left out, a skipped style would be a row describing a
        # transform that did not happen - which is the one thing this function
        # exists to prevent.
        try:
            from shani_chronoa import voice_style
            style = voice_style.selected_style(self._settings())
        except Exception:
            style = None
        if style is not None:
            described = voice_style.describe_effects(style)
            if described:
                said.append(described)
        return ", ".join(said)

    def timbre_problem(self) -> str:
        """Why the configured transforms will not be applied, or "" when they will.

        Asked by the settings window as well as by `apply_timbre`: a pitch spin
        row that silently does nothing because `sox` is missing is a control
        that looks configured and is not, and the row has to be able to say so
        without the user having to read the journal.
        """
        if not self.timbre_effects():
            return ""
        if not self.sox_path():
            return ("sox is not installed, so it is not applied at all "
                    "(on Arch: sudo pacman -S sox)")
        return ""

    def apply_timbre(self, wav_path: str) -> str:
        """Apply the configured transforms to `wav_path`, in place.

        Returns "" when the file is exactly as the engine wrote it, and
        otherwise the reason the transform was skipped - naming the transform,
        so no caller can report a pitch shift that did not happen. Never raises
        and never fails the reply: the audio that exists is playable audio.

        The rewrite goes to a sibling file and is moved over the original only
        once SoX has succeeded, so a failure halfway through leaves the engine's
        own WAV intact rather than half-transformed.
        """
        wanted = self.timbre_description()
        effects = self.timbre_effects()
        if not effects:
            return ""
        binary = self.sox_path()
        if not binary:
            return f"{wanted} was skipped - {self.timbre_problem()}"
        staged = f"{wav_path}.sox.wav"
        cmd = [binary, wav_path, staged] + effects
        try:
            try:
                done = subprocess.run(cmd, capture_output=True, timeout=SOX_TIMEOUT)
            except subprocess.TimeoutExpired:
                return f"{wanted} was skipped - sox took longer than {SOX_TIMEOUT}s"
            except OSError as exc:
                return f"{wanted} was skipped - sox could not be run: {exc}"
            if done.returncode != 0:
                detail = (done.stderr.decode("utf-8", "replace").strip()
                          or f"exit {done.returncode}")
                return f"{wanted} was skipped - sox failed: {detail}"
            if not (os.path.exists(staged) and os.path.getsize(staged) > 44):
                return f"{wanted} was skipped - sox wrote no audio"
            os.replace(staged, wav_path)
            return ""
        finally:
            # Only the failure paths leave this behind, and only when SoX got
            # far enough to create it: a stale staged file next to the caller's
            # WAV would be picked up by whatever cleans the directory next.
            if os.path.exists(staged):
                os.unlink(staged)

    def synthesize_to_bytes(self, text: str) -> bytes:
        """Synthesize text to audio bytes.

        The mouth lights while the engine runs. Synthesis is *not* playback - a
        sentence is made here and played later, sentence by sentence - so the
        light covers "Chronoa is generating speech", which is the honest reading
        and is also the part that is slow enough to be worth showing.

        Args:
            text: The text to synthesize

        Returns:
            Audio bytes (WAV format)
        """
        _light_mouth(text)
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
