"""Cloud speech recognition and synthesis: the gates, and what they refuse.

**There was no cloud voice in this package at all.** Measured before writing any
of this, by listing every network primitive in the speech path:

    $ grep -nE "https?://|httpx|socket|AF_INET" stt.py stt_parakeet.py tts.py \\
          sherpa.py voices.py
    sherpa.py:31  github.com/k2-fsa/sherpa-onnx/releases   <- an engine download
    voices.py:44  github.com/rhasspy/piper/releases        <- a voice download
    voices.py:45  huggingface.co/rhasspy/piper-voices      <- a voice download

Every one is a **download**. Speech was whisper.cpp, Parakeet, espeak-ng, Piper
and sherpa-onnx - all local subprocesses - and "cloud" in this package meant
`cloud_llm.py`'s text and nothing else.

## What makes this file a gate test rather than a unit test

The dangerous assertion in this area is not "does it return the right text", it
is **"does it send audio when it should not"**. A gate test that only checks
`is_available() is False` proves the flag was read; it does not prove a request
was not made. So every refusal case here installs a transport that **raises if it
is ever called**, and the test passes only when the refusal happens *before* the
transport. A refusal that happens after the bytes left is not a refusal.

The three refusals are kept apart because they are three different sentences to a
person, and only one of them is something they can act on:

- the switch is off - they have not agreed yet;
- privacy mode is on - they have withdrawn agreement, *right now*;
- nothing is configured that has the route - there is no action to take.

## The one this file exists for

`test_privacy_turned_on_mid_dictation_stops_the_next_chunk` is the structural
difference from `cloud_llm.py`, and it is why the privacy gate lives **inside**
`transcribe()` rather than in the constructor.

`cloud_llm` enforces privacy when the chain is *chosen*
(`app/brain.py:_maybe_enable_cloud_fallback`) and when it is *toggled*
(`_toggle_privacy` drops `self.llm`); `cloud_llm` itself only records
`egress.privacy_mode_enabled()`. That is sound for a chat turn, which is
request-scoped: no request outlives the decision that permitted it.

**Listening is not request-scoped.** A microphone stays open for minutes -
dictation alone has a 300-second ceiling against an ordinary turn's 20 - so
privacy mode can be turned on halfway through and the *next* chunk of audio would
otherwise be sent by a chain that was chosen legally before the person changed
their mind. The same is true of synthesis across a long reply, which is why the
gate is re-read per call there too and not per engine decision.
"""

import io
import math
import shutil
import struct
import subprocess
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

httpx = pytest.importorskip("httpx")

from shani_chronoa import cloud_voice  # noqa: E402


#: A transport that fails the test if it is reached. Not a mock that returns a
#: canned answer - a mock would let a "refused" test pass on a response, which
#: is the opposite of what is being claimed.
class Tripwire:
    def __init__(self):
        self.calls = []

    def handle_request(self, request):
        self.calls.append(request)
        raise AssertionError(
            f"a request reached the network: {request.method} {request.url}. "
            "This case is supposed to be refused before anything is sent.")


@pytest.fixture
def wires(monkeypatch):
    """The three gates, each one under the test's control, and a tripwire."""
    state = {"stt": False, "tts": False, "privacy": False,
             "keys": {"openai": "sk-test-not-a-real-key"}}
    monkeypatch.setattr(cloud_voice, "_read_switch",
                        lambda name, config=None: (
                            state["stt"] if name == cloud_voice.STT_SWITCH
                            else state["tts"]))
    monkeypatch.setattr(cloud_voice, "_provider_key",
                        lambda pid, api_keys=None, config=None: state["keys"].get(pid, ""))
    monkeypatch.setattr(cloud_voice, "egress", type("E", (), {
        "privacy_mode_enabled": staticmethod(lambda: state["privacy"]),
        "record": staticmethod(lambda *a, **k: None),
    }))
    state["tripwire"] = Tripwire()
    return state


def _stt(wires):
    return cloud_voice.CloudSTT(transport=wires["tripwire"])


def _tts(wires):
    return cloud_voice.CloudTTS(transport=wires["tripwire"])


# ── the gates ────────────────────────────────────────────────────────────

def test_cloud_speech_is_off_until_it_is_asked_for(wires):
    """The default is the whole safety property; this is what pins it."""
    assert cloud_voice.STT_SWITCH == "cloud-stt-enabled"
    assert cloud_voice.TTS_SWITCH == "cloud-tts-enabled"
    # Neither switch may be the existing `cloud-fallback-enabled`: a person who
    # allowed prompts to leave for a *text* fallback has not agreed to upload a
    # recording, and one switch cannot express that difference.
    assert cloud_voice.STT_SWITCH != "cloud-fallback-enabled"
    assert cloud_voice.TTS_SWITCH != "cloud-fallback-enabled"
    assert _stt(wires).is_available() is False
    assert _tts(wires).is_available() is False


def test_an_unasked_switch_sends_nothing_at_all(wires):
    with pytest.raises(cloud_voice.CloudVoiceRefused) as raised:
        _stt(wires).transcribe_stream(b"RIFF....fake audio")
    assert "off" in str(raised.value)
    assert wires["tripwire"].calls == [], "a recording left the machine while the switch was off"


def test_privacy_mode_on_sends_nothing_at_all(wires):
    wires["stt"] = wires["tts"] = True
    wires["privacy"] = True
    assert _stt(wires).is_available() is False
    assert _tts(wires).is_available() is False
    with pytest.raises(cloud_voice.CloudVoiceRefused):
        _stt(wires).transcribe_stream(b"RIFF....fake audio")
    with pytest.raises(cloud_voice.CloudVoiceRefused):
        _tts(wires).synthesize_to_bytes("the reply")
    assert wires["tripwire"].calls == [], "audio left the machine while privacy mode was on"


def test_privacy_turned_on_mid_dictation_stops_the_next_chunk(wires):
    """**The structural one.** A mic stays open for minutes; a turn does not.

    This is the difference from `cloud_llm.py`, whose privacy gate is at chain
    *selection*. Built as a real sequence - privacy off, a chunk goes; privacy
    switched on; the next chunk must not - because a gate that is only ever
    tested in one state cannot tell "reads the flag every time" from "read it
    once when the object was built", which is the mistake being guarded against.
    """
    wires["stt"] = True
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, json={"text": "what time is it"})

    engine = cloud_voice.CloudSTT(transport=httpx.MockTransport(handler))

    assert engine.is_available() is True
    assert engine.transcribe_stream(b"RIFF first chunk") == "what time is it"
    assert len(sent) == 1

    # The person turns privacy mode on mid-dictation.
    wires["privacy"] = True

    assert engine.is_available() is False, (
        "the engine still claims it is available after privacy mode was "
        "switched on, so it would keep sending the next recording")
    with pytest.raises(cloud_voice.CloudVoiceRefused) as raised:
        engine.transcribe_stream(b"RIFF second chunk")
    assert "Privacy mode" in str(raised.value)
    assert len(sent) == 1, "the second chunk was sent after privacy mode was turned on"


def test_privacy_turned_on_mid_reply_stops_the_next_synthesis(wires):
    wires["tts"] = True
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, content=b"RIFF....wav")

    engine = cloud_voice.CloudTTS(transport=httpx.MockTransport(handler))
    assert engine.synthesize_to_bytes("first") == b"RIFF....wav"
    wires["privacy"] = True
    with pytest.raises(cloud_voice.CloudVoiceRefused):
        engine.synthesize_to_bytes("second")
    assert len(sent) == 1, "the second reply was sent after privacy mode was turned on"


def test_no_key_means_no_cloud_speech_at_all(wires):
    """**Measured: no cloud speech is anonymous anywhere.**

    The *text* fallback chain is deliberately keyless - `DEFAULT_PROVIDER_ORDER`
    is llm7/kilo/blockrun and all three answer unauthenticated - so the natural
    assumption is that speech follows suit. It does not: every provider with a
    speech route answers 401 without a key, Kilo with the unusually explicit
    `"PAID_MODEL_AUTH_REQUIRED": "You need to sign in to use speech-to-text."`.

    So enabling `cloud-stt-enabled` with no key configured must enable *nothing*,
    and must say why in terms somebody can act on.
    """
    wires["stt"] = wires["tts"] = True
    wires["keys"] = {}
    for engine, what in ((_stt(wires), "recognition"), (_tts(wires), "synthesis")):
        assert engine.is_available() is False, (
            f"cloud speech {what} claimed to be available with no key "
            f"configured, so it would try and fail on its first turn")
        assert "key" in (engine.refusal() or ""), (
            f"the refusal does not mention the missing key: {engine.refusal()!r}")
    with pytest.raises(cloud_voice.CloudVoiceRefused):
        _stt(wires).transcribe_stream(b"RIFF....audio")
    assert wires["tripwire"].calls == []


def test_synthesis_refuses_by_raising_rather_than_returning_nothing(wires):
    """An empty answer is a lie about a reply that was never sent."""
    wires["tts"] = True
    wires["keys"] = {}
    with pytest.raises(cloud_voice.CloudVoiceRefused):
        _tts(wires).synthesize_to_bytes("hello")
    assert wires["tripwire"].calls == []


# ── what it does when every gate is open ─────────────────────────────────

def test_a_transcription_that_arrives_is_returned(wires):
    wires["stt"] = True
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = request.content
        return httpx.Response(200, json={"text": "turn the lights off"})

    engine = cloud_voice.CloudSTT(language="en",
                                  transport=httpx.MockTransport(handler))
    assert engine.transcribe_stream(b"RIFF....audio") == "turn the lights off"
    assert seen["url"].endswith("/audio/transcriptions")
    assert seen["auth"] == "Bearer sk-test-not-a-real-key"
    assert b"RIFF" in seen["body"], "the audio itself was not in the request"


def test_a_reply_is_sent_and_the_wav_comes_back(wires):
    wires["tts"] = True
    seen = {}

    def handler(request):
        import json as _json
        seen["url"] = str(request.url)
        seen["payload"] = _json.loads(request.content)
        return httpx.Response(200, content=b"RIFF....cloud wav")

    engine = cloud_voice.CloudTTS(transport=httpx.MockTransport(handler))
    assert engine.synthesize_to_bytes("here is your reply") == b"RIFF....cloud wav"
    assert seen["url"].endswith("/audio/speech")
    assert seen["payload"]["input"] == "here is your reply"
    # WAV, not mp3: everything downstream here already speaks WAV.
    assert seen["payload"]["response_format"] == "wav"


def test_a_provider_that_answers_with_an_error_body_is_not_a_transcription(wires):
    """Kilo and BlockRun return 200 with an `error` key - measured, not assumed."""
    wires["stt"] = True

    def handler(request):
        return httpx.Response(200, json={"error": "rate limit reached"})

    engine = cloud_voice.CloudSTT(transport=httpx.MockTransport(handler))
    with pytest.raises(cloud_voice.CloudVoiceRefused) as raised:
        engine.transcribe_stream(b"RIFF....audio")
    assert "no 'text' field" in str(raised.value)


def test_the_request_is_written_to_the_egress_log_with_its_real_size(wires, monkeypatch):
    """Audio size is the number somebody auditing the log needs, so it must be real."""
    wires["stt"] = True
    recorded = []
    monkeypatch.setattr(cloud_voice.egress, "record",
                        lambda *a, **k: recorded.append((a, k)))

    def handler(request):
        return httpx.Response(200, json={"text": "ok"})

    engine = cloud_voice.CloudSTT(transport=httpx.MockTransport(handler))
    audio = b"RIFF" + b"\0" * 4096
    engine.transcribe_stream(audio)

    assert len(recorded) == 1, "a cloud transcription left without a log line"
    args, kwargs = recorded[0]
    assert kwargs["method"] == "POST"
    assert kwargs["bytes_out"] == len(audio), (
        f"the log says {kwargs['bytes_out']} bytes for a {len(audio)}-byte "
        "recording, so the audit trail understates what was sent")
    assert kwargs["purpose"] == "speech-recognition"
    assert args[0].startswith("cloud_stt:"), (
        f"the log attributes the request to {args[0]!r}, which does not say "
        "what kind of request it was")


# ── what it must not displace ────────────────────────────────────────────

def test_cloud_voice_never_displaces_espeak(wires, monkeypatch):
    """`espeak-ng` is a hard package dependency, so it is on every install.

    Cloud synthesis is the *last* link of the chain. If it ever came before
    espeak-ng, turning the switch on would send every reply off the machine of a
    machine that could have spoken it locally - the opposite of what "local-first,
    opt-in fallback" means.
    """
    from shani_chronoa import tts
    monkeypatch.setattr(cloud_voice, "CloudTTS",
                        lambda *a, **k: type("C", (), {"is_available": staticmethod(lambda: True)})())
    monkeypatch.setattr(tts.shutil, "which", lambda name: "/usr/bin/espeak-ng"
                        if name == "espeak-ng" else None)
    piper = tts.PiperTTS.__new__(tts.PiperTTS)
    piper.piper_path = "/nonexistent/piper"
    piper.voice_path = "/nonexistent/voice"
    piper.piper_trial = False
    piper.kokoro_trial_voice = ""
    piper.rate = 1.0
    assert piper._engine("kokoro is off for some reason") == "espeak-ng", (
        "cloud synthesis took the engine slot from espeak-ng, which is present "
        "on every Shanios image")


def test_a_local_recogniser_is_always_preferred_to_a_cloud_one(wires):
    """`app/voice.py:_build_stt` may only reach for the cloud when local cannot listen."""
    import inspect
    from shani_chronoa.app import voice as voice_mod
    source = inspect.getsource(voice_mod.VoiceMixin._build_stt)
    assert "if not local.is_available():" in source, (
        "the cloud STT is not behind a local-availability check, so a machine "
        "with a perfectly good local model would upload its recordings")
    # And the cloud must come *after* the local build, never instead of it.
    assert source.index("local = stt.build_stt(") < source.index("cloud_voice.CloudSTT(")


def test_build_stt_actually_prefers_local_and_falls_back(wires, monkeypatch):
    """**Drives the real method**, because a source check cannot see the branch.

    The two halves, in order, because the second only means something after the
    first: with no local recogniser the method must return a `CloudSTT`; then,
    the moment a local one works, it must return that instead - without anybody
    turning a switch back off. That is why this is a *selection* re-made per
    utterance rather than a backend setting.
    """
    from shani_chronoa import stt as stt_mod
    from shani_chronoa.app import ChronoaApplication

    class NoLocal:
        def is_available(self):
            return False

    class GoodLocal:
        label = "Whisper.cpp"

        def is_available(self):
            return True

    monkeypatch.setattr(stt_mod, "build_stt", lambda **kw: NoLocal())
    monkeypatch.setattr(cloud_voice, "_read_switch", lambda name, config=None: True)
    monkeypatch.setattr(cloud_voice, "_provider_key",
                        lambda pid, api_keys=None, config=None: "sk-test")
    monkeypatch.setattr(cloud_voice.egress, "privacy_mode_enabled", lambda: False)

    app = ChronoaApplication()
    app.stt = None

    chosen = app._build_stt()
    assert isinstance(chosen, cloud_voice.CloudSTT), (
        f"with no local recogniser and the switch on, _build_stt returned "
        f"{type(chosen).__name__} - so a machine that cannot listen at all is "
        "not reaching for the cloud")

    monkeypatch.setattr(stt_mod, "build_stt", lambda **kw: GoodLocal())
    local = app._build_stt()
    assert isinstance(local, GoodLocal), (
        "a working local recogniser was displaced by the cloud one, which is "
        "the opposite of local-first and would upload every recording on a "
        "machine that does not have to")


def test_the_status_line_names_the_engine_that_actually_ran(wires, monkeypatch):
    """A machine transcribing in the cloud must not log "Whisper.cpp".

    `_stt_backend_label()` used to answer from the *configured* backend, so once
    `_build_stt` started handing back a cloud engine the startup line named a
    program that was never invoked - the repo's own "two log lines a millisecond
    apart, disagreeing" failure, reached from a different direction.
    """
    from shani_chronoa import stt as stt_mod
    from shani_chronoa.app import ChronoaApplication

    class NoLocal:
        def is_available(self):
            return False

    monkeypatch.setattr(stt_mod, "build_stt", lambda **kw: NoLocal())
    monkeypatch.setattr(cloud_voice, "_read_switch", lambda name, config=None: True)
    monkeypatch.setattr(cloud_voice, "_provider_key",
                        lambda pid, api_keys=None, config=None: "sk-test")
    monkeypatch.setattr(cloud_voice.egress, "privacy_mode_enabled", lambda: False)

    app = ChronoaApplication()
    app.stt = app._build_stt()
    assert app._stt_backend_label() == "Cloud", (
        f"the status line says {app._stt_backend_label()!r} while a cloud "
        "provider is transcribing, naming a program that was never run")
    assert "Whisper.cpp" not in app._stt_backend_label()


# ── the capability table is a measurement, so pin the measurement ────────

def test_the_capability_table_matches_the_recorded_probe():
    """A table in a docstring is a claim. This is the claim, as data.

    Measured live on 2026-10-06 with `probe_capabilities()` - a **real body and a
    real WAV**, classified by the error message. `True` means "has the route, and
    answers 401 without a key"; that is the only sense in which it is true,
    because none of these is anonymous.

    If a provider gains or drops a route this fails, which is the point - it is a
    dated measurement and AGENTS.md's standing rule is not to trust a stale note
    about what a provider offers.
    """
    measured = {
        "openai": (True, True), "groq": (True, True), "openrouter": (True, True),
        "kilo": (True, False), "llm7": (True, False), "blockrun": (False, False),
        "google": (False, False), "opencode-zen": (False, False),
    }
    from shani_chronoa import cloud_llm
    assert set(measured) == set(cloud_llm.PROVIDERS), (
        "the measured table and the provider table have drifted apart")
    for pid, (stt, tts) in measured.items():
        assert (pid in cloud_voice.STT_ROUTES) is stt, (
            f"{pid} speech recognition: recorded {stt}, table says "
            f"{pid in cloud_voice.STT_ROUTES}")
        assert (pid in cloud_voice.TTS_ROUTES) is tts, (
            f"{pid} speech synthesis: recorded {tts}, table says "
            f"{pid in cloud_voice.TTS_ROUTES}")


def test_kilo_is_not_a_speech_provider_despite_answering_400():
    """**The correction that the first version of this table got wrong.**

    An empty POST to Kilo returns `400`, and reading that as "the route exists"
    put Kilo in both route sets. A real body returns the truth:

        {"error": "Invalid path", "error_type": "invalid_path",
         "message": "This endpoint only accepts the path `/chat/completions`."}

    Kilo is a chat-only gateway. It has **no** speech endpoint, and it says so in
    a 400 that is indistinguishable from a valid request with a bad body - which
    is why the table is now pinned by the measured verdict rather than by a
    status code.
    """
    assert "kilo" in cloud_llm_ids()
    assert "kilo" not in cloud_voice.TTS_ROUTES, (
        "Kilo has no /audio/speech: it answered 'This endpoint only accepts the "
        "path /chat/completions'. Putting it back would make every synthesis "
        "fall through to a 400.")


def test_a_provider_that_bills_crypto_is_not_offered_and_says_why():
    """**BlockRun's speech endpoint charges 0.002 USD in USDC per request.**

    Measured: HTTP 402 with an x402 challenge -
    `{"x402Version":2,"accepts":[{"scheme":"exact","network":"eip155:8453",
    "amount":"2000",...}],"error":"Payment Required","message":"This endpoint
    requires x402 payment","price":{"amount":"0.002000","currency":"USD"}}`.

    Three things follow, and each is asserted:

    - **it is not in the route set.** Its *chat* tier is free, which is what makes
      this easy to get wrong - a switch labelled "Cloud speech synthesis" that
      quietly bills a wallet is worse than not offering the provider;
    - **it is not merely "no"**. The route exists and the model name is right;
      only the access costs money, so the probe reports `needs-paid` as its own
      state. Reading a 402 as "no capability" is the same "a status code is not a
      capability" mistake this file documents, made by this file's own first
      classifier;
    - **the model name is still on record**, so the finding is in the module rather
      than lost.
    """
    assert "blockrun" not in cloud_voice.TTS_ROUTES, (
        "BlockRun's speech endpoint bills crypto per request (HTTP 402, x402); "
        "offering it behind a switch that does not mention money is worse than "
        "not offering it")
    assert cloud_voice.TTS_MODELS["blockrun"].startswith("elevenlabs/"), (
        "the ElevenLabs model name is kept so the finding stays recorded in the "
        "module, not only in this test")
    assert cloud_voice._tts_request("blockrun", "hi")[0] == cloud_voice.TTS_MODELS["blockrun"]

    def paid(url, **kwargs):
        return httpx.Response(402, json={
            "x402Version": 2, "error": "Payment Required",
            "message": "This endpoint requires x402 payment",
            "price": {"amount": "0.002000", "currency": "USD"}})

    original = cloud_voice.httpx.post
    cloud_voice.httpx.post = paid
    try:
        table = cloud_voice.probe_capabilities(timeout=1.0, audio_wav=_make_wav())
    finally:
        cloud_voice.httpx.post = original
    assert table["blockrun"]["tts"]["verdict"] == "needs-paid", (
        "a 402 is neither a working route nor a missing one, and collapsing it "
        f"to either loses the finding: {table['blockrun']['tts']}")
    assert table["openai"]["tts"]["verdict"] == "needs-paid"


def test_a_provider_that_names_its_models_differently_is_sent_its_own_name():
    """BlockRun answers 400 `Unknown speech model: tts-1` on a route that works."""
    assert cloud_voice.TTS_MODELS["blockrun"].startswith("elevenlabs/"), (
        "BlockRun's speech models are named after ElevenLabs; sending `tts-1` "
        "is a 400 on a working route")
    model, fmt = cloud_voice._tts_request("blockrun", "hello")
    assert model == cloud_voice.TTS_MODELS["blockrun"]
    # And OpenAI, which does take `tts-1`.
    assert cloud_voice._tts_request("openai", "hello")[0] == cloud_voice.DEFAULT_TTS_MODEL


def test_a_provider_that_cannot_return_wav_is_asked_for_what_it_can():
    """OpenRouter's validator accepts only `mp3` and `pcm` and rejects `wav`."""
    assert cloud_voice.TTS_FORMATS["openrouter"] == ("mp3",)
    model, fmt = cloud_voice._tts_request("openrouter", "hello")
    assert fmt == "mp3", (
        "asking OpenRouter for wav is a 400 ZodError naming response_format")
    assert cloud_voice._tts_request("openai", "hello")[1] == "wav"


def test_audio_that_is_not_wav_is_converted_or_the_call_is_refused():
    """Never hand back MP3 under a name every caller here believes means WAV.

    **Real MP3 bytes, produced by ffmpeg from a real WAV.** A first version fed
    it `b"ID3fake-mp3"` and asserted a RIFF header came back; it raised instead,
    which is the code being right and the fixture being fake - the same
    "negative control that cannot fail" trap in a new place. `ffprobe` is the
    check that it really is audio and not merely four lucky bytes.
    """
    wav = b"RIFF....converted"
    assert cloud_voice._to_wav(wav, "wav") == wav          # already right
    if not shutil.which("ffmpeg"):
        with pytest.raises(cloud_voice.CloudVoiceRefused) as raised:
            cloud_voice._to_wav(b"ID3anything", "mp3")
        assert "ffmpeg" in str(raised.value)
        return

    tone = Path(__file__).with_name("_tone.wav")
    tone.write_bytes(_make_wav())
    mp3 = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(tone),
         "-f", "mp3", "pipe:1"], capture_output=True, timeout=60)
    assert mp3.returncode == 0 and mp3.stdout, f"could not build an MP3 fixture: {mp3.stderr[:200]}"
    assert mp3.stdout[:3] in (b"ID3", b"\xff\xfb"), "the MP3 fixture is not MP3"

    converted = cloud_voice._to_wav(mp3.stdout, "mp3")
    assert converted[:4] == b"RIFF", (
        f"the conversion returned {converted[:8]!r}, which is not a RIFF/WAVE "
        "header - so a caller would play garbage believing it was WAV")
    probe = subprocess.run(["ffprobe", "-v", "error", "-i", "pipe:0",
                            "-show_entries", "format=format_name",
                            "-of", "default=nw=1"],
                           input=converted, capture_output=True, timeout=30)
    assert b"wav" in probe.stdout.lower(), f"ffprobe says {probe.stdout!r}, not WAV"

    # Garbage in is still a refusal, not a plausible-looking empty answer.
    with pytest.raises(cloud_voice.CloudVoiceRefused):
        cloud_voice._to_wav(b"not audio at all", "mp3")
    try:
        tone.unlink()
    except OSError:
        pass


def test_no_cloud_speech_is_anonymous_anywhere():
    """Every measured speech route answers 401 without a key. Kilo says so outright."""
    assert cloud_voice.KEY_REQUIRED_FOR_SPEECH is True
    # And the builder enforces it rather than relying on
    # `CloudProvider.requires_key`, which is about the *text* chain.
    import inspect
    source = inspect.getsource(cloud_voice._candidates)
    assert "KEY_REQUIRED_FOR_SPEECH and not key" in source, (
        "_candidates must refuse a keyless speech provider; building one would "
        "leave a backend that fails on its first real request")


def test_probe_capabilities_answers_without_raising(monkeypatch):
    """It has to survive an unreachable host, since it is a diagnostic.

    "I could not ask" and "it does not have one" are different answers, and a
    probe that collapses them is the expensive kind of wrong - so an unreachable
    host is its own verdict.
    """
    import httpx as _httpx

    def boom(*a, **k):
        raise _httpx.ConnectError("no route to host")

    monkeypatch.setattr(cloud_voice.httpx, "post", boom)
    table = cloud_voice.probe_capabilities(timeout=0.1)
    assert "openai" in table
    assert table["openai"]["stt"]["verdict"] == "unreachable", (
        "a provider it could not reach must be reported as unreachable, not as "
        "one without the route")


def test_probe_capabilities_tells_a_bad_route_from_a_missing_key(monkeypatch):
    """The distinction the empty-POST probe could not make, now asserted."""
    def answer(url, **kwargs):
        if url.endswith("/audio/speech"):
            return httpx.Response(400, json={
                "error": "Invalid path",
                "message": "This endpoint only accepts the path `/chat/completions`."})
        return httpx.Response(401, json={"error": {"message": "Invalid API Key"}})

    monkeypatch.setattr(cloud_voice.httpx, "post", answer)
    table = cloud_voice.probe_capabilities(timeout=1.0, audio_wav=_make_wav())
    assert table["kilo"]["tts"]["verdict"] == "no", (
        "a 400 saying the path is invalid is not a speech route")
    assert table["openai"]["stt"]["verdict"] == "needs-key", (
        "a 401 about the key means the route is there and the key is not")


def _make_wav() -> bytes:
    """A short real WAV: a route that rejects it is not telling us about the route."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"".join(
            struct.pack("<h", int(3000 * math.sin(i * 0.05)))
            for i in range(16000)))
    return buffer.getvalue()


def cloud_llm_ids():
    from shani_chronoa import cloud_llm
    return set(cloud_llm.PROVIDERS)
