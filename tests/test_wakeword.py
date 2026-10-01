"""The whisper.cpp wake phrase: matching, utterance segmentation, availability.

The loop runs as shipped; only whisper's transcript is substituted (via
`_transcribe`), except in TestTranscribeArgv, which runs the real
`_transcribe` against a stand-in `whisper-cli` executable that records the
argv and the WAV it was handed. The real whisper.cpp, on real (synthetic)
speech in a real Shanios slot, is shani-testbed's chronoa-voice script.
"""

import os
import stat
import struct
import wave

import pytest

from shani_chronoa import wakeword as ww

_FRAME = ww._FRAME_BYTES


def _pcm(amplitude: int) -> bytes:
    return struct.pack(f"<{_FRAME // 2}h", *([amplitude] * (_FRAME // 2)))


class _Capture:
    def __init__(self, frames):
        self._frames = list(frames)

    def read(self, size):
        return self._frames.pop(0) if self._frames else b""


def _stream(loud: int, tail_silence: int = ww._END_SILENCE_FRAMES, amp: int = 9000):
    return ([_pcm(0)] * ww._CALIBRATION_FRAMES + [_pcm(0)] * 3
            + [_pcm(amp)] * loud + [_pcm(0)] * tail_silence)


def _listener(monkeypatch, heard="Hey Chronoa.", phrase=ww.DEFAULT_PHRASE):
    li = ww.WakeWordListener(phrase=phrase)
    li._backend = "pw-record"
    li._generation = 1
    calls = []

    def fake(pcm):
        calls.append(len(pcm))
        return heard

    monkeypatch.setattr(li, "_transcribe", fake)
    return li, calls


def _run(li, frames):
    fired = []
    li._listen_loop(type("P", (), {"stdout": _Capture(frames)})(), lambda: fired.append(1), li._generation)
    return fired


class TestMatchesPhrase:
    @pytest.mark.parametrize("text", [
        "Hey Chronoa.", "hey chronoa", "Okay, Chronoa.", "Hi Chronoa!",
        "Hey Chronoa. What time is it?", "  HEY   CHRONOA  ",
    ])
    def test_the_phrase_at_the_start_wakes(self, text):
        assert ww.matches_phrase(text)

    @pytest.mark.parametrize("text", [
        # whisper's own outputs for near-misses, measured with the prompt on
        "The Chronoa lot.", "The chrono is a lot.", "Hey Chronola.",
        "Hey, how are you?", "Chronology is interesting.",
        "", "Chronoa", "Hey", "I told Chronoa about it",
    ])
    def test_anything_else_does_not(self, text):
        assert not ww.matches_phrase(text)

    def test_a_custom_phrase_needs_no_training(self):
        assert ww.matches_phrase("Computer, lights", "computer")
        assert ww.matches_phrase("Okay computer.", "hey computer")
        assert not ww.matches_phrase("Hey Chronoa.", "hey computer")


class TestSegmentation:
    def test_an_utterance_after_silence_is_judged_once_and_fires(self, monkeypatch):
        li, calls = _listener(monkeypatch)
        assert _run(li, _stream(loud=8)) == [1]
        # pre-roll + the loud frames + the silence that ended it
        assert calls == [(ww._PRE_ROLL_FRAMES + 8 + ww._END_SILENCE_FRAMES) * _FRAME]

    def test_a_click_is_not_sent_to_whisper(self, monkeypatch):
        li, calls = _listener(monkeypatch)
        assert _run(li, _stream(loud=ww._MIN_SPEECH_FRAMES - 1)) == []
        assert calls == []

    def test_a_quiet_room_transcribes_nothing(self, monkeypatch):
        li, calls = _listener(monkeypatch)
        assert _run(li, [_pcm(0)] * 60) == []
        assert calls == []

    def test_long_speech_is_cut_at_the_utterance_limit(self, monkeypatch):
        li, calls = _listener(monkeypatch, heard="something else entirely")
        assert _run(li, _stream(loud=100)) == []
        assert calls and max(calls) <= ww._MAX_UTTERANCE_FRAMES * _FRAME

    def test_other_speech_does_not_fire(self, monkeypatch):
        li, calls = _listener(monkeypatch, heard="Hey, how are you?")
        assert _run(li, _stream(loud=8)) == []
        assert len(calls) == 1

    def test_end_of_stream_mid_utterance_is_still_judged(self, monkeypatch):
        li, calls = _listener(monkeypatch)
        assert _run(li, _stream(loud=8, tail_silence=0)) == [1]

    def test_the_noise_floor_is_relative_to_the_room(self, monkeypatch):
        # a loud room (fan at 3000 RMS) calibrates high; speech at the same level is not speech
        li, calls = _listener(monkeypatch)
        frames = [_pcm(3000)] * (ww._CALIBRATION_FRAMES + 20)
        assert _run(li, frames) == [] and calls == []


class TestAvailability:
    def test_reasons_name_what_to_install(self, monkeypatch):
        li = ww.WakeWordListener()
        li._backend = None
        assert "pw-record" in li.unavailable_reason()
        li._backend = "pw-record"
        li._whisper = None
        assert "whisper-cpp" in li.unavailable_reason()
        li._whisper = "/usr/bin/whisper-cli"
        monkeypatch.setattr(ww, "_find_model", lambda preferred=None: None)
        assert "model" in li.unavailable_reason()
        assert not li.is_available()
        monkeypatch.setattr(ww, "_find_model", lambda preferred=None: "/m/ggml-tiny-q5_1.bin")
        assert li.unavailable_reason() is None and li.is_available()

    def test_a_model_already_used_for_speech_input_is_found(self, monkeypatch, tmp_path):
        d = tmp_path / "whisper" / "models"
        d.mkdir(parents=True)
        (d / "ggml-base-q5_1.bin").write_bytes(b"x")
        monkeypatch.setattr("shani_chronoa.files.data_home", lambda: tmp_path)
        assert ww._find_model() == str(d / "ggml-base-q5_1.bin")
        (d / "ggml-tiny-q5_1.bin").write_bytes(b"x")
        assert ww._find_model() == str(d / "ggml-tiny-q5_1.bin"), "the tiny model is preferred"


class TestTranscribeArgv:
    def test_whisper_cli_gets_a_16k_mono_wav_and_the_phrase_as_prompt(self, tmp_path):
        log = tmp_path / "argv"
        fake = tmp_path / "whisper-cli"
        fake.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" "$@" > {log}\n'
            'while [ "$1" != "-f" ]; do shift; done; cp "$2" ' + str(tmp_path / "got.wav") + "\n"
            "echo ' Hey Chronoa.'\n"
        )
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        li = ww.WakeWordListener(phrase="hey chronoa", whisper_path=str(fake))
        li._model_path = "/m/ggml-tiny-q5_1.bin"
        out = li._transcribe(_pcm(1000) * 10)
        assert out == "Hey Chronoa."
        argv = log.read_text().split("\n")
        assert argv[argv.index("--prompt") + 1] == "Hey chronoa."
        assert argv[argv.index("-m") + 1] == "/m/ggml-tiny-q5_1.bin"
        with wave.open(str(tmp_path / "got.wav")) as w:
            assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
            assert w.getnframes() == 10 * ww._FRAME_SAMPLES
        assert not [f for f in os.listdir("/tmp") if f.startswith("chronoa-wake-") and f.endswith(".wav")
                    and os.path.getmtime(os.path.join("/tmp", f)) > (tmp_path / "argv").stat().st_mtime - 1], \
            "the utterance must not be left on disk"

    def test_a_failing_whisper_is_no_detection(self, tmp_path):
        fake = tmp_path / "whisper-cli"
        fake.write_text("#!/bin/sh\necho 'Hey Chronoa.'; exit 3\n")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        li = ww.WakeWordListener(whisper_path=str(fake))
        li._model_path = "/m/x.bin"
        assert li._transcribe(_pcm(0)) == ""
