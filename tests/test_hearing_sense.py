"""The hearing sense turns a real utterance into a transient percept.

The microphone is not present on a test host and must not be opened by these
tests at all, so the capture backend and the STT are both replaced - the
same approach `tests/test_ocr_sense.py` takes for tesseract, except that one
stands a real binary on PATH and this one cannot: a sense whose unit test
actually opened a microphone would be a test nobody can run on a build agent.

What these tests do prove is the part that is genuinely testable and is not
obvious from reading the module: that consent is consulted *before* the
recorder and the STT are even constructed, that an utterance arrives with the
right kind/sensitivity/TTL, that a transient percept never reaches disk, and
that each missing capability produces a readable reason instead of a
traceback.

They do not prove, and cannot on this host, anything acoustic. `vad.py`'s
first draft used a hardcoded RMS threshold of 400 that a real ambient-noise
capture (peak ~2046, mean ~592) would have exceeded continuously, so a
"this sensor works" claim needs a machine with a real room behind it. What is
asserted here instead is the structural property that keeps that bug from
coming back through this file: the sense owns no speech/silence decision of
its own at all.
"""

import inspect
import os
import threading

import pytest


@pytest.fixture
def hearing_mod():
    from shani_chronoa.senses import hearing as mod

    return mod


def _fake_config(monkeypatch, hearing_mod, *, allowed, reason=""):
    """Replace the config class `run()` builds per call.

    `hearing-sense-enabled` does not exist in the shipped gschema yet, so
    `ChronoaConfig.sense_allowed("hearing")` is False today and the module is
    fail-closed. These tests override the consent verdict rather than
    depending on a key that a separate, serialized change owns - the module
    itself must never create the key, or a sense could grant itself consent.
    """
    class _Config:
        whisper_model = "tiny"
        language = "en"
        hardware_profile = "auto"
        privacy_mode = True

        def sense_allowed(self, sense):
            return allowed

        def sense_allowed_reason(self, sense):
            return reason

        # The sense reads this property (config.py: `sense_allowed("hearing")`)
        # since b655563; the stub predated it, so all twelve tests stopped at an
        # AttributeError before reaching what each one checks.
        @property
        def hearing_sense_enabled(self):
            return self.sense_allowed("hearing")

    monkeypatch.setattr(hearing_mod, "ChronoaConfig", _Config)


def _fake_recorder(monkeypatch, hearing_mod, *, audio_path=None, backend=True):
    """Replace `AudioRecorder` with a recorder that never spawns `pw-record`.

    `start_auto_stop` fires the callback from a real background thread,
    because that is what the real one does and the sense has to bridge the
    thread boundary to be worth anything.

    `audio_path=None` models the real recorder's "no speech was heard" case:
    the real one calls back with None after a fully silent capture.
    """
    calls: list = []

    class _Recorder:
        def is_available(self):
            return backend

        def start_auto_stop(self, on_done, max_seconds=20.0, silence_seconds=1.2):
            calls.append(
                {"max_seconds": max_seconds, "silence_seconds": silence_seconds}
            )
            if not backend:
                return False

            def _call():
                on_done(audio_path)

            threading.Thread(target=_call, daemon=True).start()
            return True

        def cancel_auto_stop(self):
            calls.append({"cancelled": True})

    monkeypatch.setattr(hearing_mod, "AudioRecorder", _Recorder)
    return calls


def _fake_stt(
    monkeypatch,
    hearing_mod,
    *,
    text="the deploy is at four",
    raises=None,
    binary=None,
    model=None,
):
    """Replace the STT factory with a recording stand-in. Returns its constructor log.

    `binary`/`model` default to a real file that exists, so the default
    stand-in is a *working* STT; pass a path under `/nonexistent` to simulate
    each missing capability separately.

    Patched at `stt.build_stt` rather than at a backend class, because that is
    the seam `hearing.build_stt` now goes through - a patch on a class the
    module no longer names would stop applying and the tests would go on
    exercising the real thing.
    """
    constructed: list = []
    binary_path = __file__ if binary is None else binary
    model_file = __file__ if model is None else model

    class _STT:
        def __init__(self, model="base", language="en", backend=""):
            self.model = model
            self.language = language
            self.whisper_path = binary_path
            self.model_path = model_file
            self.transcribed: list = []
            constructed.append({"model": model, "language": language})

        def is_available(self):
            return os.path.exists(self.whisper_path) and os.path.exists(self.model_path)

        def transcribe(self, audio_file):
            self.transcribed.append(audio_file)
            if raises is not None:
                raise raises
            return text

    monkeypatch.setattr(
        hearing_mod.stt, "build_stt",
        lambda model, language="en", backend="": _STT(model, language, backend),
    )
    return constructed


def _write_wav(path):
    """A real WAV file standing in for the recorder's captured audio."""
    import wave

    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * 160)
    return path


@pytest.fixture
def utterance(tmp_path):
    return _write_wav(tmp_path / "chronoa-rec-test.wav")


@pytest.fixture
def hearing(monkeypatch, hearing_mod, utterance):
    """The module with consent granted, a fake backend and a fake STT."""
    _fake_config(monkeypatch, hearing_mod, allowed=True)
    calls = _fake_recorder(monkeypatch, hearing_mod, audio_path=utterance)
    constructed = _fake_stt(monkeypatch, hearing_mod)
    return hearing_mod, calls, constructed


# --- the happy path -------------------------------------------------------


def test_an_utterance_becomes_a_private_transient_percept(hearing):
    """A transcript is a Percept with this sense's declared shape."""
    from shani_chronoa.senses import SENSITIVITY_PRIVATE

    hearing_mod, _, _ = hearing

    result = hearing_mod.run({})

    assert not isinstance(result, str), result
    assert result.sense == "hearing"
    assert result.kind == "utterance"
    assert result.sensitivity == SENSITIVITY_PRIVATE
    assert result.source == "microphone"
    assert "the deploy is at four" in result.content
    assert result.metadata["model"] == "tiny"
    assert result.metadata["language"] == "en"


def test_the_percept_is_bounded_not_durable(hearing):
    """`ttl_seconds is None` would route this to disk; only memory may do that."""
    hearing_mod, _, _ = hearing

    result = hearing_mod.run({})

    assert result.ttl_seconds == 300.0
    assert result.ttl_seconds is not None
    assert result.is_expired() is False
    assert result.is_expired(now=result.created_at + 301) is True


def test_a_transcript_is_kept_in_memory_and_never_written_to_disk(hearing, tmp_path):
    """The on-disk tier belongs to the memory sense alone."""
    from shani_chronoa.senses.store import PerceptStore

    hearing_mod, _, _ = hearing
    durable = tmp_path / "percepts" / "memory.jsonl"

    store = PerceptStore(durable_path=durable)
    store.add(hearing_mod.run({}))

    assert len(store.active()) == 1
    assert store.durable() == []
    assert not durable.exists()


def test_the_capture_bounds_are_passed_straight_to_the_recorder(hearing):
    """No speech/silence decision is invented here; the recorder owns both."""
    hearing_mod, calls, _ = hearing

    hearing_mod.run({"max_seconds": 8, "silence_seconds": 0.5})

    assert calls == [{"max_seconds": 8.0, "silence_seconds": 0.5}]


def test_the_captured_recording_is_deleted_once_transcribed(hearing, utterance):
    """The user's voice is removed from disk in the same call, TTL or not."""
    hearing_mod, _, _ = hearing

    hearing_mod.run({})

    assert not utterance.exists()


# --- consent: the gate is before the microphone, not after ----------------


def test_a_denied_sense_never_constructs_the_stt_or_the_recorder(
    monkeypatch, hearing_mod, utterance
):
    """A post-hoc filter is worthless: by then the audio is already on disk."""
    _fake_config(
        monkeypatch,
        hearing_mod,
        allowed=False,
        reason="the hearing sense is turned off (enable 'hearing-sense-enabled')",
    )
    constructed = _fake_stt(monkeypatch, hearing_mod)
    recorder_calls = _fake_recorder(monkeypatch, hearing_mod, audio_path=utterance)

    result = hearing_mod.run({})

    assert isinstance(result, str)
    assert "hearing-sense-enabled" in result
    assert constructed == []
    assert recorder_calls == []


def test_hearing_is_fail_closed_while_its_consent_key_is_missing(
    gsettings_env, tmp_path, monkeypatch, hearing_mod
):
    """The real, unpatched config denies `hearing` today, and the mic stays shut.

    This is the state a parent change owns (`hearing-sense-enabled` is not in
    the gschema and not in `config._SENSE_CONSENT_KEYS`). It is asserted
    without pinning the reason text, because that text legitimately changes
    from "there is no 'hearing' sense" to "the hearing sense is turned off"
    when the key lands - the behaviour under test is that the refusal happens
    before any capture, either way.
    """
    from shani_chronoa.config import ChronoaConfig

    assert ChronoaConfig().sense_allowed("hearing") is False
    constructed = _fake_stt(monkeypatch, hearing_mod)
    recorder_calls = _fake_recorder(
        monkeypatch, hearing_mod, audio_path=_write_wav(tmp_path / "unused.wav")
    )

    result = hearing_mod.run({})

    assert isinstance(result, str)
    assert "hearing" in result
    assert constructed == []
    assert recorder_calls == []


# --- degrading with a reason, never a traceback ----------------------------


def test_a_missing_capture_backend_names_what_to_install(monkeypatch, hearing_mod):
    _fake_config(monkeypatch, hearing_mod, allowed=True)
    _fake_recorder(monkeypatch, hearing_mod, backend=False)
    _fake_stt(monkeypatch, hearing_mod)

    result = hearing_mod.run({})

    assert isinstance(result, str)
    assert "pw-record" in result and "arecord" in result


def test_a_missing_whisper_binary_is_distinguished_from_a_missing_model(
    monkeypatch, hearing_mod, utterance
):
    """The two causes have different fixes, so they get different sentences."""
    _fake_config(monkeypatch, hearing_mod, allowed=True)
    _fake_recorder(monkeypatch, hearing_mod, audio_path=utterance)

    _fake_stt(monkeypatch, hearing_mod, binary="/nonexistent/whisper-cli")
    assert "whisper-cpp" in hearing_mod.run({})

    _fake_stt(monkeypatch, hearing_mod, model="/nonexistent/ggml-tiny.bin")
    assert "is not downloaded" in hearing_mod.run({})


def test_silence_is_reported_as_a_reason_not_as_a_percept(monkeypatch, hearing_mod):
    """Nothing was perceived, so nothing is filed."""
    _fake_config(monkeypatch, hearing_mod, allowed=True)
    _fake_recorder(monkeypatch, hearing_mod, audio_path=None)
    _fake_stt(monkeypatch, hearing_mod)

    result = hearing_mod.run({})

    assert isinstance(result, str)
    assert "no speech" in result


def test_a_vanished_model_is_a_reason_not_a_traceback(monkeypatch, hearing_mod, utterance):
    """`WhisperSTT.transcribe` raises FileNotFoundError for a missing model."""
    _fake_config(monkeypatch, hearing_mod, allowed=True)
    _fake_recorder(monkeypatch, hearing_mod, audio_path=utterance)
    _fake_stt(
        monkeypatch,
        hearing_mod,
        raises=FileNotFoundError("Whisper model not found: ggml-tiny.bin"),
    )

    result = hearing_mod.run({})

    assert isinstance(result, str)
    assert "Whisper model not found" in result
    assert not utterance.exists()  # still cleaned up on the failure path


def test_bad_arguments_are_refused_before_the_microphone_opens(monkeypatch, hearing_mod):
    _fake_config(monkeypatch, hearing_mod, allowed=True)
    recorder_calls = _fake_recorder(monkeypatch, hearing_mod, audio_path=None)
    _fake_stt(monkeypatch, hearing_mod)

    assert "max_seconds must be a number" in hearing_mod.run({"max_seconds": "soon"})
    assert "max_seconds must be between" in hearing_mod.run({"max_seconds": 900})
    assert "silence_seconds must be between" in hearing_mod.run(
        {"silence_seconds": 5, "max_seconds": 2}
    )
    assert recorder_calls == []


# --- properties this file must not lose ------------------------------------


def test_the_sense_owns_no_speech_or_silence_threshold(hearing):
    """`vad.py`'s hardcoded RMS 400 was five times below a real ambient floor.

    The fix was `calibrate_noise_floor()`; the durable protection is that this
    module never makes that decision itself. If a detector is ever constructed
    here, an amplitude constant has come with it.

    Checked over the code with the module docstring stripped, because the
    docstring has to *name* the bug it is warning about - and "threshold" is
    the very word it must be able to say.
    """
    hearing_mod, _, _ = hearing

    module = inspect.getmodule(hearing_mod)
    source = inspect.getsource(module)
    code = source.split('"""', 2)[2]  # module docstring, then the rest

    assert "SilenceDetector" not in code
    assert "calibrate_noise_floor" not in code
    assert "threshold" not in code


def test_a_runaway_transcript_is_capped(monkeypatch, hearing_mod, utterance):
    """A whisper hallucination on a noise burst must not eat the turn's budget."""
    _fake_config(monkeypatch, hearing_mod, allowed=True)
    _fake_recorder(monkeypatch, hearing_mod, audio_path=utterance)
    _fake_stt(monkeypatch, hearing_mod, text="word " * 2000)

    result = hearing_mod.run({})

    # `run()` transcribes and strips, so the cap is measured against the
    # stripped 9999 chars, not the 10000 the fake was handed.
    assert result.metadata["chars_dropped"] == 9999 - hearing_mod.MAX_TRANSCRIPT_CHARS
    assert result.content.count("word") == hearing_mod.MAX_TRANSCRIPT_CHARS // 5
    assert "[...] (truncated)" in result.content


def test_the_loader_registers_the_sense(gsettings_env):
    from shani_chronoa.senses import _sense_problem, discover_senses

    senses = discover_senses()
    assert "hearing" in senses
    assert _sense_problem(senses["hearing"]) == ""
    assert senses["hearing"].is_ambient() is False
    assert senses["hearing"].poll_interval is None
