"""Recordings: subtitles, who said what, the sound and speaker parsers, and a cleaned copy.

The parsers are checked against the exact lines sherpa-onnx v1.13.8 printed on
this machine (2026-10-02). The clean-up runs the real ffmpeg on a real file;
whisper and the sherpa programs themselves are shani-testbed's chronoa-extras.
"""

import shutil
import subprocess

import pytest

from shani_chronoa import recordings, sounds, speakers
from shani_chronoa.recordings import Segment


def test_srt_is_numbered_and_timed():
    srt = recordings.to_srt([Segment(0.0, 2.5, "Hello there."), Segment(3661.2, 3663.0, "Later.", "Speaker 2")])
    assert srt == ("1\n00:00:00,000 --> 00:00:02,500\nHello there.\n\n"
                   "2\n01:01:01,200 --> 01:01:03,000\n[Speaker 2] Later.\n")


def test_each_segment_goes_to_the_speaker_who_overlaps_it_most():
    turns = [(0.0, 23.9, "Speaker 1"), (24.4, 26.0, "Speaker 2"), (26.0, 27.5, "Speaker 1")]
    segs = [Segment(0.5, 5.0, "a"), Segment(23.5, 25.8, "b"), Segment(26.2, 27.0, "c"), Segment(40, 41, "d")]
    out = recordings.assign_speakers(segs, turns)
    assert [s.speaker for s in out] == ["Speaker 1", "Speaker 2", "Speaker 1", ""]
    assert recordings.to_text(out[:3]) == "Speaker 1: a\nSpeaker 2: b\nSpeaker 1: c"
    assert recordings.to_text([Segment(0, 1, "plain"), Segment(1, 2, "text")]) == "plain text"


def test_the_sherpa_outputs_parse():
    diar = "Started\n0.031 -- 23.858 speaker_00\n24.449 -- 25.968 speaker_01\n25.968 -- 27.453 speaker_00\n"
    assert speakers.parse(diar) == [(0.031, 23.858, "Speaker 1"), (24.449, 25.968, "Speaker 2"),
                                    (25.968, 27.453, "Speaker 1")]
    tags = ('AudioEvent(name="Animal", index=72, prob=0.90101)\n'
            'AudioEvent(name="Cat", index=81, prob=0.899119)\nAudioEvent(name="Meow", index=83, prob=0.2)\n')
    heard = sounds.parse(tags)
    assert heard[1] == sounds.Heard("Cat", 0.899119)
    assert sounds.describe(heard) == "animal (90%), cat (90%)", "below the floor is left out"
    assert sounds.describe([]) == "nothing it recognises clearly"


def test_both_say_how_to_set_them_up_when_missing():
    assert "More -> Sounds" in sounds.problem()
    assert "More -> Who said what" in speakers.problem()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is not installed")
def test_clean_makes_a_new_file_and_keeps_the_rate(tmp_path):
    src = tmp_path / "note.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=300:d=2", "-f", "lavfi", "-i",
                    "anoisesrc=d=2:a=0.05", "-filter_complex", "amix=inputs=2", "-ar", "16000", "-ac", "1",
                    str(src)], check=True)
    target = tmp_path / "note-clean.wav"
    recordings.clean(src, target)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=sample_rate", "-of", "csv=p=0",
                            str(target)], capture_output=True, text=True).stdout.strip()
    assert target.is_file() and probe == "16000", "loudnorm resampled to 192 kHz; the chain must not"
    with pytest.raises(recordings.RecordingError):
        recordings.clean(src, target)  # never over an existing file


def test_the_skill_routes_and_refuses(tmp_path, monkeypatch):
    from shani_chronoa.skills import recording
    monkeypatch.setenv("HOME", str(tmp_path))
    doc = tmp_path / "a.txt"
    doc.write_text("x")
    assert "not an audio or video file" in recording._run({"action": "text", "path": str(doc)})
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")
    assert "More -> Sounds" in recording._run({"action": "sounds", "path": str(wav)})
    assert "language is a code" in recording._run({"action": "text", "path": str(wav), "language": "en; rm"})


# --- the sound trigger: "the doorbell rang" as an event (microphone faked) ---

def _heard(*pairs):
    return lambda seconds: [sounds.Heard(name, p) for name, p in pairs]


def test_a_sound_fires_only_when_it_is_heard_confidently():
    from shani_chronoa.triggers import desktop_sources as ds
    hit = ds.read_sound("doorbell", listen=_heard(("Doorbell", 0.82), ("Speech", 0.4)))
    assert hit.event is not None and "doorbell (82%)" in hit.event.summary
    quiet = ds.read_sound("doorbell", listen=_heard(("Doorbell", 0.12), ("Music", 0.7)))
    assert quiet.event is None and "no doorbell heard" in quiet.detail
    strict = ds.read_sound("dog:0.9", listen=_heard(("Dog", 0.8)))
    assert strict.event is None, "the confidence asked for is the one used"
    assert hit.fingerprint != quiet.fingerprint, "heard and not heard are different states, so a change fires"


def test_bad_sources_and_a_missing_model_are_unavailable_not_silence():
    from shani_chronoa.triggers import desktop_sources as ds
    from shani_chronoa.triggers.common import SIGNAL_UNAVAILABLE
    for bad in ("", "doorbell:2", "rm -rf /", "x:abc"):
        r = ds.read_sound(bad, listen=_heard(("Doorbell", 0.99)))
        assert r.status == SIGNAL_UNAVAILABLE and r.event is None, f"{bad!r} was not refused: {r.detail}"
    missing = ds.read_sound("doorbell")  # no model installed in the test home
    assert missing.status == SIGNAL_UNAVAILABLE and "More -> Sounds" in missing.detail, \
        "a missing model must read as unavailable, never as 'nothing heard'"


def test_the_sound_event_is_registered_and_off_by_default():
    from shani_chronoa.config import ChronoaConfig
    from shani_chronoa.triggers import EVENT_SOUND, EVENT_TYPES
    assert EVENT_SOUND == "sound" and EVENT_SOUND in EVENT_TYPES
    assert ChronoaConfig().get_bool("sound-sense-enabled", True) is False


def test_listening_on_request_needs_its_consent(monkeypatch):
    from shani_chronoa.config import ChronoaConfig
    from shani_chronoa.skills import recording
    called = []
    monkeypatch.setattr(sounds, "listen", lambda seconds: called.append(seconds) or [sounds.Heard("Dog", 0.7)])
    monkeypatch.setattr(sounds, "problem", lambda: "")
    assert "turned off" in recording._run({"action": "listen"}) and called == [], "the microphone is not touched"
    # `heard-sound-sense-enabled`, NOT `sound-sense-enabled`. Two keys for two
    # things, on purpose: the heard-sound sense is *ask and it listens once*,
    # while `sound-sense-enabled` is *an automatic rule may listen for a
    # doorbell* - a person who allows the second has not agreed to the first.
    # This test granted the wrong one, so the granted path was never reached.
    ChronoaConfig().set("heard-sound-sense-enabled", "true")
    assert "dog (70%)" in recording._run({"action": "listen", "seconds": 3}) and called == [3.0]
    # ...and the neighbouring key still buys nothing, which is the whole point
    # of keeping them apart.
    ChronoaConfig().set("heard-sound-sense-enabled", "false")
    ChronoaConfig().set("sound-sense-enabled", "true")
    assert "turned off" in recording._run({"action": "listen"}), \
        "granting the sound-event key must not open the microphone"


# --- `recording action=streams` (measured reachable through the real dispatch) ---
#
# `pipewire.list_streams()` is the half that was missing: an app's
# `Stream/Output/Audio` node, which `list_inputs`/`list_outputs` never matched.
# Measured here, through the real dispatch path: consent off refuses by name,
# a quiet machine reports itself quiet, and a playing application is listed
# with the name to capture it by.


class _Config:
    def __init__(self, allowed=True, reason=""):
        self._allowed, self._reason = allowed, reason

    def sense_allowed(self, sense): return self._allowed

    def sense_allowed_reason(self, sense): return self._reason


class _Stream:
    def __init__(self, name, label, recordable=True):
        self.name, self.label, self.recordable = name, label, recordable

    def __repr__(self):  # so the failure text names the thing
        return "<%s %s recordable=%s>" % (type(self).__name__, self.name, self.recordable)


def test_streams_is_advertised_and_not_just_reachable(monkeypatch):
    """**Reachable but unadvertised is the mirror of the dead-control trap.**

    `_run` accepts the action (proven through the real dispatch path), but the
    model is offered `action` as an enum - if `streams` is not in it, no
    request ever names it and the feature is invisible while working.
    """
    from shani_chronoa import tools

    offered = [entry["function"]["parameters"]["properties"]["action"]["enum"]
               for entry in tools.TOOLS
               if entry.get("function", {}).get("name") == "recording"]

    assert offered, "recording is not in the registry at all"
    assert "streams" in offered[0], offered[0]

    # and the control: an action that is genuinely absent is NOT offered
    for absent in ("streamz", "meeting"):
        assert absent not in offered[0], absent


def test_streams_requires_the_same_consent_as_listening(monkeypatch):
    """A stream name says what is playing. `media.name` measured
    `/tmp/tone.wav`; on a real desktop it would say a meeting's title, so this
    is the microphone's consent and not a free one."""
    from shani_chronoa.skills import recording

    monkeypatch.setattr("shani_chronoa.config.ChronoaConfig",
                        lambda: _Config(allowed=False, reason="turned off in Settings"))
    monkeypatch.setattr("shani_chronoa.pipewire.list_streams",
                        lambda: pytest.fail("the graph was read with consent off"))

    said = recording._run({"action": "streams"})

    assert "turned off" in said, said
    assert "Settings" in said, said


def test_a_quiet_machine_reports_itself_quiet(monkeypatch):
    """Empty is not a failure - reporting one there is the confident wrong
    answer this file keeps recording."""
    from shani_chronoa.skills import recording

    monkeypatch.setattr("shani_chronoa.config.ChronoaConfig", lambda: _Config())
    monkeypatch.setattr("shani_chronoa.pipewire.list_streams", lambda: [])

    said = recording._run({"action": "streams"})

    assert "Nothing is playing" in said, said
    assert "not a" in said and "failure" in said, said


def test_a_playing_application_is_listed_with_its_capture_name(monkeypatch):
    """The name is the point: it is what `pw-record --target` takes, and without
    it the list is decorative."""
    from shani_chronoa.skills import recording

    monkeypatch.setattr("shani_chronoa.config.ChronoaConfig", lambda: _Config())
    monkeypatch.setattr("shani_chronoa.pipewire.list_streams",
                        lambda: [_Stream("pw-play", "/tmp/tone.wav", True)])

    said = recording._run({"action": "streams"})

    assert "1 of 1 can be captured" in said, said
    assert "/tmp/tone.wav" in said, said
    assert "capture as: pw-play" in said, said


def test_an_applications_microphone_is_not_offered_as_capture_target(monkeypatch):
    """`Stream/Input` is audio going INTO an app. Offering it is a dead control."""
    from shani_chronoa.skills import recording

    monkeypatch.setattr("shani_chronoa.config.ChronoaConfig", lambda: _Config())
    monkeypatch.setattr("shani_chronoa.pipewire.list_streams",
                        lambda: [_Stream("chromium", "Audio input", False)])

    said = recording._run({"action": "streams"})

    assert "None of these can be captured" in said, said
    assert "capture as:" not in said, said
    assert "own microphone" in said, said


def test_no_pipewire_is_said_plainly(monkeypatch):
    """Absent, not silent - and not a report about what is playing."""
    from shani_chronoa.skills import recording

    monkeypatch.setattr("shani_chronoa.config.ChronoaConfig", lambda: _Config())
    monkeypatch.setattr("shani_chronoa.pipewire.is_available", lambda: False)
    monkeypatch.setattr("shani_chronoa.pipewire.list_streams",
                        lambda: pytest.fail("the graph was read with no pw-dump"))

    said = recording._run({"action": "streams"})

    assert "pw-dump" in said and "not installed" in said, said


def test_an_unknown_action_is_still_refused():
    """The control: `streams` must not be matching by accident."""
    from shani_chronoa.skills import recording

    said = recording._run({"action": "streamz"})

    assert "must be one of" in said, said
    assert "streams" in said, said
