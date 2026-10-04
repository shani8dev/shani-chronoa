r"""The `sing` skill: the surface that makes singing.py and prosody.py reachable.

`singing.py` and `prosody.py` were fully built, unit-tested and verified on a
real image, and no product code imported either of them - `grep -rn "singing\|
prosody" --include=*.py usr/` returned nothing outside the two modules
themselves. Asking Chronoa to sing produced a spoken reply, because the only
consumer of that code was the test harness. `skills/sing.py` is that surface, so
this file is mostly about the ways it can be *wrong quietly*, which is how a
missing surface survived in the first place:

* it is in the real tool list the model sees (a skill nothing registers is
  exactly the dead module this replaces - the repo's own `feat:`-message rule);
* asking for a song does NOT get a tune. There is no tune database, and the
  result string names the contour it sang, so a caller cannot answer "here is
  Twinkle Twinkle" about four rising notes;
* the whitelist of shapes cannot drift from `prosody.Melody.from_shape`, which is
  the code that builds them - the first version of that list had four of the five
  and would have refused a legitimate "wave";
* a missing transposer refuses by name instead of falling back to speaking;
* a missing SoX costs the voice style and says so in the returned string,
  because the model reads that string and the listener cannot;
* an unknown style name is refused rather than ignored - the failure `sing.py`
  itself calls a confident wrong answer;
* the audio is played through the app's own `AudioPlayer`, so barge-in still
  interrupts it, and a synthesised-but-unplayable line says which half failed.

WHAT IS STUBBED, and what that costs. `PiperTTS`, `AudioPlayer` and the three
external binaries (espeak-ng, soundstretch, sox) are stubbed, because they are
process boundaries. Everything between them is real: the syllable segmentation,
the melody, `song_plan`, `apply_song`'s cut-and-join, the style's effect list
and the whole `_run` body. The soundstretch and sox stubs are real executables on
`PATH` rather than patched functions, because `apply_song` and `_apply_style`
each look the binary up themselves and run it - patching the functions under test
would have left most of this skill unexercised.

What is NOT here: whether the result is in tune. The soundstretch stub copies its
input, so these tests cannot and do not claim the audio is pitched correctly.
That is measured on a real image by
`../shani-testbed/slot-tests/chronoa-singing.sh`, where the plan +0/+2/+4/+6
comes back measured at +0.0/+2.0/+4.2/+6.1 semitones.
"""

from __future__ import annotations

import math
import os
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import singing, skills, voice_style  # noqa: E402
from shani_chronoa.skills import sing  # noqa: E402

LINE = "Twinkle twinkle little star"


# --- process-boundary stubs ---------------------------------------------------

_ESPEAK = r"""#!/bin/sh
# self-test espeak-ng: phonemes for prosody's `-q -x --ipa=1`, a WAV for `-w`.
case " $* " in
  *" -x "*) printf 'twɪŋkəl twɪŋkəl ˈlɪtəl stɑr\n'; exit 0 ;;
esac
out=""; prev=""
for a in "$@"; do [ "$prev" = "-w" ] && out="$a"; prev="$a"; done
[ -n "$out" ] || exit 1
printf 'RIFF\064\017\000\000WAVEfmt \020\000\000\000\001\000\001\000\042\126\000\000\104\254\000\000\002\000\020\000data\240\017\000\000' > "$out"
head -c 4000 /dev/zero | tr '\000' '\200' >> "$out"
"""

_SOUNDSTRETCH = r"""#!/bin/sh
# self-test soundstretch: copy input to output. It does NOT pitch-shift, so
# nothing here can claim the audio is in tune - see the module docstring.
cp "$1" "$2"
"""

_SOX_COPY = r"""#!/bin/sh
# self-test sox: succeed and copy, ignoring the effect list.
echo "sox $*" >> "$SOX_LOG"
cp "$1" "$2"
"""

_SOX_SILENT = r"""#!/bin/sh
# self-test sox: exit 0 having written nothing - the shape of a lying tool.
echo "sox $*" >> "$SOX_LOG"
exit 0
"""

_SOX_FAIL = r"""#!/bin/sh
echo "sox $*" >> "$SOX_LOG"
echo "sox FAIL: sox WARN wav: can't open input file" >&2
exit 2
"""


class _StubTTS:
    """Writes a real WAV, so the singing path runs on real audio rather than a mock."""

    def __init__(self, ok=True, engine="espeak-ng"):
        self.ok, self._engine = ok, engine

    def engine(self, text=""):
        return self._engine

    def synthesize(self, text, out):
        if not self.ok:
            return False
        _wav(Path(out), seconds=1.2)
        return True


class _StubPlayer:
    def __init__(self, available=True, fail=False):
        self.available, self.fail, self.played = available, fail, []

    def is_available(self):
        return self.available

    def play_bytes(self, audio):
        if self.fail:
            raise RuntimeError("pw-play went away")
        self.played.append(len(audio))


def _wav(path: Path, seconds: float = 1.2, hz: float = 220.0, rate: int = 22050) -> Path:
    """A real WAV with energy at 220 Hz, so a duration is a real duration."""
    frames = bytearray()
    for i in range(int(rate * seconds)):
        frames += struct.pack("<h", int(11000 * math.sin(2 * math.pi * hz * i / rate)))
    data = bytes(frames)
    path.write_bytes(
        b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
        + b"data" + struct.pack("<I", len(data)) + data)
    return path


def _environment(monkeypatch, tmp_path, *, sox="copy", shifter=True):
    """Put real stub executables on PATH and return (tts, player, sox_log)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "sox.log"
    log.write_text("")
    monkeypatch.setenv("SOX_LOG", str(log))

    def _install(name: str, body: str) -> None:
        path = bin_dir / name
        path.write_text(body)
        path.chmod(0o755)

    _install("espeak-ng", _ESPEAK)
    if shifter:
        _install("soundstretch", _SOUNDSTRETCH)
    bodies = {"copy": _SOX_COPY, "silent": _SOX_SILENT, "fail": _SOX_FAIL}
    if sox != "none":
        _install("sox", bodies[sox])
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")

    tts, player = _StubTTS(), _StubPlayer()
    monkeypatch.setattr(sing, "PiperTTS", lambda: tts)
    monkeypatch.setattr(sing, "AudioPlayer", lambda: player)
    return tts, player, log


# --- registration -------------------------------------------------------------


def test_sing_is_in_the_tool_list_the_model_sees():
    """A skill nothing registers is the dead module this file exists to prevent."""
    _tools, handlers = skills.discover_skills()
    assert "sing" in handlers, "sing is not registered as a skill"
    assert handlers["sing"] is sing._run


def test_the_schema_is_a_valid_function_schema_naming_the_honest_limits():
    from shani_chronoa.skills import is_valid_schema

    schema = sing.SKILLS[0].schema
    assert is_valid_schema(schema)
    description = schema["function"]["description"]
    assert "does NOT know any song" in description, (
        "the description has to stop the model presenting a contour as a tune")
    assert schema["function"]["parameters"]["required"] == ["text"]
    assert schema["function"]["parameters"]["properties"]["shape"]["enum"] == list(sing._SHAPES)


def test_long_text_wants_the_file_transport_and_short_text_does_not():
    assert sing.wants_by_reference({"text": "a" * (sing._BY_REFERENCE_TEXT_BYTES + 1)})
    assert not sing.wants_by_reference({"text": "twinkle twinkle"})
    assert not sing.wants_by_reference({"text": 7})


# --- refusals -----------------------------------------------------------------


def test_no_transposer_refuses_by_name_instead_of_reading_the_line(monkeypatch, tmp_path):
    _environment(monkeypatch, tmp_path, shifter=False)
    said = sing._run({"text": LINE})
    assert said.startswith("Did not sing:"), said
    assert "soundtouch" in said and "rubberband" in said, said
    assert "speak skill" in said, "it should say what still works"


def test_an_unknown_shape_is_refused_and_the_shapes_are_listed(monkeypatch, tmp_path):
    _environment(monkeypatch, tmp_path)
    said = sing._run({"text": LINE, "shape": "wobble"})
    assert "not a melody shape" in said, said
    for shape in sing._SHAPES:
        assert shape in said, said


def test_an_unknown_style_name_is_refused_not_ignored(monkeypatch, tmp_path):
    """Ignoring it would sing in the default voice and report success."""
    _environment(monkeypatch, tmp_path)
    said = sing._run({"text": LINE, "voice_style": "moonbeam"})
    assert said.startswith("Did not sing:"), said
    assert "moonbeam" in said, said


def test_an_empty_line_says_so(monkeypatch, tmp_path):
    _environment(monkeypatch, tmp_path)
    assert "empty" in sing._run({"text": "   "})


def test_an_engine_that_produces_nothing_says_which_engine(monkeypatch, tmp_path):
    tts, _player, _log = _environment(monkeypatch, tmp_path)
    monkeypatch.setattr(sing, "PiperTTS", lambda: _StubTTS(ok=False, engine="kokoro"))
    said = sing._run({"text": LINE})
    assert "kokoro" in said, said


def test_no_engine_at_all_names_what_to_install(monkeypatch, tmp_path):
    _environment(monkeypatch, tmp_path)
    monkeypatch.setattr(sing, "PiperTTS", lambda: _StubTTS(engine=None))
    said = sing._run({"text": LINE})
    assert "no text-to-speech engine" in said, said


# --- the happy path, and what it claims ---------------------------------------


def test_it_sings_the_words_it_was_given_and_names_the_contour(monkeypatch, tmp_path):
    _tts, player, _log = _environment(monkeypatch, tmp_path)
    said = sing._run({"text": LINE, "shape": "rising"})
    assert "Sang" in said, said
    # The stub espeak-ng answers with 7 vowel nuclei, and the reported syllable
    # count has to be that number rather than a word count or a constant: the
    # claim under test is that the words given decide the segmentation.
    assert "7 syllables" in said, said
    syllables = int(said.split()[1])
    notes = int(said.split(" as ")[1].split()[0])
    assert 0 < notes <= syllables, f"a plan cannot have more notes than syllables: {said}"
    assert "rising contour" in said, said
    assert "espeak-ng" in said, "the engine that ran must be named"
    assert "not the tune of any named song" in said, said
    assert player.played, "the audio was never handed to the player"
    assert player.played[0] > 44, "an empty file was handed to the player"


def test_every_shape_produces_its_own_contour_and_is_one_the_code_builds():
    """Four shapes that all sang the same thing would be one shape with aliases."""
    from shani_chronoa import prosody

    seen = {}
    for shape in sing._SHAPES:
        # from_shape raises KeyError for a name it does not build, so this is
        # also the check that the whitelist cannot drift away from the code.
        seen[shape] = tuple(round(v, 2) for v in prosody.Melody.from_shape(shape, 8).semitones)
    assert len(set(seen.values())) == len(sing._SHAPES), f"collapsed: {seen}"
    assert seen["rising"] == tuple(sorted(seen["rising"])), seen["rising"]
    assert seen["falling"] == tuple(sorted(seen["falling"], reverse=True)), seen["falling"]
    assert set(seen["level"]) == {0.0}, seen["level"]


def test_a_missing_sox_costs_the_style_and_says_so(monkeypatch, tmp_path):
    """The model reads the result string; the listener cannot see what was lost."""
    _tts, player, log = _environment(monkeypatch, tmp_path, sox="none")
    said = sing._run({"text": LINE, "voice_style": "soothing"})
    assert "Sang" in said, said
    assert "soothing voice style was skipped" in said, said
    assert "sox is not installed" in said, said
    assert not log.read_text(), "sox must not have run at all"
    assert player.played, "the line is still sung, just unstyled"


def test_a_sox_that_writes_nothing_is_reported_not_silently_accepted(monkeypatch, tmp_path):
    _environment(monkeypatch, tmp_path, sox="silent")
    said = sing._run({"text": LINE, "voice_style": "soothing"})
    assert "sox failed" in said, said
    assert "Sang" in said, "the notes were still sung, so it must not read as a failure"


def test_a_sox_that_exits_non_zero_is_reported_with_its_own_message(monkeypatch, tmp_path):
    _environment(monkeypatch, tmp_path, sox="fail")
    said = sing._run({"text": LINE, "voice_style": "soothing"})
    assert "sox failed" in said, said
    assert "can't open input file" in said, said


def test_a_style_that_applies_cleanly_is_reported_and_really_ran(monkeypatch, tmp_path):
    _environment(monkeypatch, tmp_path)
    said = sing._run({"text": LINE, "voice_style": "soothing"})
    assert "in the soothing voice" in said, said
    log = (tmp_path / "sox.log").read_text()
    effects = voice_style.style_effects(voice_style.resolve_preset("soothing"))
    assert effects, "the soothing preset must ask for something, or this proves nothing"
    for token in effects:
        assert token in log, f"sox was never handed {token!r}: {log}"


def test_no_style_configured_claims_no_voice(monkeypatch, tmp_path):
    """With nothing chosen in Settings the result must not mention a voice."""
    _environment(monkeypatch, tmp_path)
    said = sing._run({"text": LINE})
    assert "Sang" in said, said
    assert " in the " not in said, said
    assert not (tmp_path / "sox.log").read_text(), "no style was asked for, so sox should not run"


# --- playback -----------------------------------------------------------------


# --- the rendering is kept, and is what the post-condition checks -------------


def test_the_song_is_saved_where_the_user_can_find_it(monkeypatch, tmp_path):
    """"Sing that again" needs a file; the first version deleted its own output."""
    from shani_chronoa import files

    monkeypatch.setattr(files, "data_home", lambda: tmp_path / "data")
    monkeypatch.setattr(sing.files, "data_home", lambda: tmp_path / "data")
    _environment(monkeypatch, tmp_path)
    said = sing._run({"text": LINE})
    saved = list((tmp_path / "data" / "sing").glob("sung-*.wav"))
    assert len(saved) == 1, f"expected one rendering: {saved}"
    assert saved[0].stat().st_size > 44, "a header with no audio is not a song"
    assert str(saved[0]) in said, f"the result has to name the file: {said}"


def test_the_post_condition_verifies_a_real_rendering(monkeypatch, tmp_path):
    from shani_chronoa import verification

    monkeypatch.setattr(sing.files, "data_home", lambda: tmp_path / "data")
    assert sing.POST_CONDITION is not None, "no post-condition declared"
    assert verification.post_condition_for("shani_chronoa.skills.sing") is sing.POST_CONDITION

    ok, evidence = sing._verify_sung({})
    assert not ok, "nothing rendered yet, so nothing can be verified"
    assert "no rendering" in evidence, evidence

    _environment(monkeypatch, tmp_path)
    sing._run({"text": LINE})
    ok, evidence = sing._verify_sung({})
    assert ok, evidence
    assert "bytes" in evidence, evidence


def test_a_header_only_wav_is_a_failed_verification_not_a_verified_one(monkeypatch, tmp_path):
    """The failure a synthesis reports as success: a file, and no audio in it."""
    monkeypatch.setattr(sing.files, "data_home", lambda: tmp_path / "data")
    directory = tmp_path / "data" / "sing"
    directory.mkdir(parents=True)
    # A well-formed 44-byte header and zero samples: what an engine writes when
    # it thinks it succeeded and produced nothing.
    (directory / "sung-20260101-000000-1.wav").write_bytes(
        b"RIFF" + struct.pack("<I", 36) + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, 22050, 44100, 2, 16)
        + b"data" + struct.pack("<I", 0))
    ok, evidence = sing._verify_sung({})
    assert not ok, evidence
    assert "no audio" in evidence, evidence


def test_old_renderings_are_pruned_so_this_cannot_grow_forever(monkeypatch, tmp_path):
    monkeypatch.setattr(sing.files, "data_home", lambda: tmp_path / "data")
    directory = tmp_path / "data" / "sing"
    directory.mkdir(parents=True)
    stale = []
    for index in range(sing._KEEP_RENDERINGS + 5):
        path = directory / f"sung-2026010{index % 9}-00000{index % 9}-{index}.wav"
        path.write_bytes(b"RIFF" + b"\0" * 100)
        os.utime(path, (1_600_000_000 + index, 1_600_000_000 + index))
        stale.append(path)
    _environment(monkeypatch, tmp_path)
    sing._run({"text": LINE})
    left = sorted(directory.glob("sung-*.wav"))
    assert len(left) == sing._KEEP_RENDERINGS, f"expected the newest {sing._KEEP_RENDERINGS}: {len(left)}"
    assert left[-1].stat().st_size > 44, "the newest one must be this song"


def test_no_playback_backend_is_reported_as_not_played(monkeypatch, tmp_path):
    _environment(monkeypatch, tmp_path)
    monkeypatch.setattr(sing, "AudioPlayer", lambda: _StubPlayer(available=False))
    said = sing._run({"text": LINE})
    assert "not played" in said, said
    assert "pw-play" in said, said


def test_a_player_that_raises_is_reported_and_still_says_what_was_sung(monkeypatch, tmp_path):
    _environment(monkeypatch, tmp_path)
    monkeypatch.setattr(sing, "AudioPlayer", lambda: _StubPlayer(fail=True))
    said = sing._run({"text": LINE})
    assert "Sang" in said, said
    assert "Could not play" in said, said