"""Cloud speech recognition and speech synthesis - **opt-in, per capability, off by default**.

**There was no cloud voice at all before this module.** Measured by reading every
network primitive in the speech path:

    $ grep -nE "https?://|httpx|socket|AF_INET" stt.py stt_parakeet.py tts.py \
          sherpa.py voices.py
    sherpa.py:31  github.com/k2-fsa/sherpa-onnx/releases      <- engine download
    voices.py:44  github.com/rhasspy/piper/releases           <- voice download
    voices.py:45  huggingface.co/rhasspy/piper-voices         <- voice download

Every one of those is a **download**, not an inference call. Speech went to
`whisper-cli`, to Parakeet, to `espeak-ng`, to Piper, to sherpa-onnx - all local
subprocesses - and the only thing "cloud" meant in this package was
`cloud_llm.py`'s text. So a person who chose the setup wizard's **"In the cloud"**
branch got a Chronoa that could think and **could not hear**, because that branch
routes `cloud-keys -> done` and never visits the Ears or Voice pages.

## Why this is not simply another provider in `cloud_llm.py`

Four reasons, each of which is a reason a *gate* has to exist rather than a
reused one:

1. **Audio is a different disclosure from text.** `redactor` exists to keep API
   keys out of prompts. There is no redaction that makes "the last thirty seconds
   of your microphone" safe to send - it is either sent or not sent, and the only
   honest instrument is consent. So this module has its own switch per capability
   rather than borrowing `cloud-fallback-enabled`.
2. **"Fallback" is the wrong word.** `cloud-fallback-enabled` describes a
   *fallback*: the local model is tried first and the cloud is the last resort.
   Cloud speech here follows the same shape - it is used only when there is no
   local engine - but it is used *while the microphone is open*, on every turn,
   which is not what "fallback" describes to a person reading a switch. Hence
   `cloud-stt-enabled` and `cloud-tts-enabled`, default **false**, and they are
   separate because a person may well send audio to transcribe and still want the
   reply spoken on the machine that generated it.
3. **The privacy gate has to be per request, not per selection.** This is the
   part that differs structurally from `cloud_llm.py`. There, privacy is enforced
   when the chain is chosen (`app/brain.py:_maybe_enable_cloud_fallback`) and when
   it is toggled (`_toggle_privacy` drops `self.llm`), and `cloud_llm` itself only
   *records* `egress.privacy_mode_enabled()`. That is sound because a chat turn is
   request-scoped: no request outlives the decision to send it. **Listening does
   not work that way.** A mic can be open for minutes, so privacy mode can be
   turned on halfway through a dictation, and the next chunk of audio would
   otherwise be sent by a chain that was chosen legally ten minutes ago. So
   `_privacy_refusal()` is called inside `transcribe()` and inside
   `synthesize_to_bytes()`, on every call, and fails **closed**.
4. **`egress.MODEL_HOSTS` is a model-*download* allowlist** (`huggingface.co`,
   `hf.co`, `github.com`, `githubusercontent.com`) and `cloud_llm.py` never calls
   `check_destination` at all - it only calls `egress.record()`. Reusing that
   allowlist here would be wrong in both directions: these hosts are not where
   audio is transcribed, and an audio endpoint is not a place to fetch a model
   from. So this module does its own destination check and then records.

## Which providers actually have the routes - measured, not remembered

Probed live on **2026-10-06** with a **real request body and a real WAV**, and
classified by **the error message rather than the status code**. That last part is
not pedantry; it is the reason the first version of this table was wrong three
times over (see `probe_capabilities`'s docstring).

| provider | `/audio/transcriptions` | `/audio/speech` |
|---|---|---|
| `openai` | needs a key | needs a key |
| `groq` | needs a key | needs a key |
| `openrouter` | needs a key | route, **mp3/pcm only** |
| `kilo` | needs a key (`PAID_MODEL_AUTH_REQUIRED`) | **no route** - `"only accepts the path /chat/completions"` |
| `llm7` | needs a key | **no route** |
| `blockrun` | **no route** | route, but **HTTP 402 - 0.002 USD in USDC per request** (x402) |
| `google` | **no route** | **no route** |
| `opencode-zen` | **no route** | **no route** |
| `anthropic` | **no route** | **no route** |

Five consequences, and the fourth is the one that cost a rewrite:

- **No cloud speech is anonymous anywhere.** Every provider with a route answers
  401 without a key - Kilo with the unusually explicit *"You need to sign in to
  use speech-to-text."* This matters because the *text* fallback chain is
  deliberately keyless, so the assumption that speech follows suit is a natural
  and wrong one: enabling `cloud-stt-enabled` with no key configured enables
  nothing at all.
- **`kilo` has no speech route at all**, which an empty POST cannot detect. Its
  gateway answers `400` on *any* path but `/chat/completions`.
- **`blockrun` names its speech models after ElevenLabs**, so `tts-1` is a 400 on
  a working route - the failure mode that reads as "speech is broken here". And
  that route bills **crypto**: 0.002 USD in USDC on Base, per request. Its *chat*
  tier is free, which is exactly what makes this easy to get wrong, so BlockRun is
  deliberately **not** offered for speech.
- **`openrouter` cannot return WAV** (only `mp3`/`pcm`), so `_to_wav` converts
  with the `ffmpeg` already used by `recordings.py`, and **raises** if `ffmpeg`
  is absent rather than handing back MP3 under a name every caller here believes
  means WAV.
- **The corrected classifier caught its own author.** `verdict()` has a
  `needs-paid` state because the first version read BlockRun's 402 as `no` - the
  same "a status code is not a capability" mistake this module documents, made
  one function above where it was being described. The live probe and the table
  now agree on all nine providers.

## What this does NOT do

- It does not change which provider answers text. `cloud_llm` is untouched.
- It does not upload anything unless the matching switch is on **and** privacy
  mode is off **and** the request is for a provider with the route.
- It does not make local speech worse or slower. Cloud speech is consulted only
  when there is no local engine, exactly like the cloud LLM fallback.
"""

import logging
import os
import shutil
import subprocess
from typing import Optional, Tuple

import httpx

from shani_chronoa import egress
from shani_chronoa.redaction import redactor

logger = logging.getLogger(__name__)

#: The GSettings switches. Both default false; see this module's docstring for
#: why they are two switches and not one, and why neither reuses
#: `cloud-fallback-enabled`.
STT_SWITCH = "cloud-stt-enabled"
TTS_SWITCH = "cloud-tts-enabled"

#: The OpenAI-compatible transcription model. `whisper-1` is the conventional
#: name on this API shape - OpenAI's own, and the one Groq and OpenRouter mirror
#: on the same route - so it is the default that works across the table above.
#: Overridable per machine, because a provider that only serves a differently
#: named model should not require a code change.
DEFAULT_STT_MODEL = "whisper-1"
#: The OpenAI-compatible speech voice. Same reasoning; `wav` rather than `mp3`
#: because everything downstream here (`audio.py`, `ffprobe` in the slot tests)
#: already speaks WAV and re-encoding would be a pointless step.
DEFAULT_TTS_MODEL = "tts-1"
DEFAULT_TTS_VOICE = "alloy"
DEFAULT_TTS_FORMAT = "wav"

#: Measured 2026-10-06; see the module docstring for the probe and the table.
#: Kept as plain sets so the *reason* a provider is absent is one edit away.
STT_ROUTES: frozenset = frozenset({"openai", "groq", "openrouter", "kilo", "llm7"})
#: **BlockRun is deliberately absent**, and its route set is the only place that
#: shows it. Measured 2026-10-06: `/audio/speech` there answers **HTTP 402** with
#: an x402 payment challenge - 0.002 USD in USDC on Base, per request. Its *chat*
#: tier is free, which is why this is easy to get wrong: the same hostname that
#: answers unauthenticated for text bills crypto for speech.
#:
#: A switch labelled "Cloud speech synthesis" that quietly bills a wallet is worse
#: than not offering the provider at all, so it is out. `TTS_MODELS` keeps its
#: ElevenLabs model name so the finding stays in the file, and
#: `probe_capabilities()` still reports it as `needs-paid` - recorded, not lost.
TTS_ROUTES: frozenset = frozenset({"openai", "groq", "openrouter"})

#: **There is no anonymous cloud speech anywhere.** Measured: every provider with
#: a speech route answered 401 without a key - OpenAI and Groq with "you didn't
#: provide an API key", LLM7 with "Missing API key", OpenRouter with "No cookie
#: auth credentials found", and Kilo with the unusually explicit
#: `"PAID_MODEL_AUTH_REQUIRED": "You need to sign in to use speech-to-text."`.
#:
#: That is worth stating because the *text* fallback chain is explicitly keyless
#: (`DEFAULT_PROVIDER_ORDER` is llm7/kilo/blockrun, all anonymous), so the
#: natural assumption is that speech follows suit. It does not: turning
#: `cloud-stt-enabled` on with no key configured enables nothing at all.
KEY_REQUIRED_FOR_SPEECH = True

#: The speech model each provider names its own thing. **Not uniform, and a
#: wrong name is a 400 rather than a 404** - measured: BlockRun answered "Unknown
#: speech model: tts-1. Available models: elevenlabs/flash-v2.5,
#: elevenlabs/turbo-v2.5, elevenlabs/multilingual-v2, elevenlabs/v3,
#: bytedance/seed-audio-1.0". Sending `tts-1` there fails on a route that works.
TTS_MODELS = {"blockrun": "elevenlabs/turbo-v2.5"}
STT_MODELS = {}

#: Which `response_format` each provider accepts. Measured: OpenAI returns WAV;
#: **OpenRouter's validator accepts only `mp3` and `pcm`** and rejects `wav` with
#: a ZodError naming `response_format`. Asking for a format a provider does not
#: have is a 400 on the one provider here that would otherwise have been the
#: cheapest way to get a voice.
TTS_FORMATS = {
    "openrouter": ("mp3",),
}

#: Providers with the route, in the order they are tried. The keyed ones first
#: because a person who configured a key configured it on purpose. `requires_key`
#: is honoured by the builder - and since every speech route needs one,
#: `_candidates` requires a key for all of them rather than relying on
#: `CloudProvider.requires_key`, which is about the *text* chain.
STT_ORDER: Tuple[str, ...] = ("openai", "groq", "openrouter", "kilo", "llm7")
TTS_ORDER: Tuple[str, ...] = ("openai", "groq", "openrouter")


class CloudVoiceRefused(Exception):
    """A cloud speech request that must not be made.

    Separate from `httpx` errors on purpose: a refusal is a *decision* and the
    surfaces have to be able to say so ("privacy mode is on, so nothing was
    sent") rather than reporting it as a network problem, which would send
    somebody looking at the wrong thing.
    """


def _privacy_refusal(what: str) -> Optional[str]:
    """Why this request must not leave the machine, or None when it may.

    Read fresh on every call, from the same `privacy-mode` value every other
    egress site reads (`egress.privacy_mode_enabled()`), and it **fails toward
    ON** - the function it delegates to never raises for that reason. This is the
    per-request gate the module docstring argues for: a mic stays open for
    minutes, so a decision made when the chain was built says nothing about the
    chunk being recorded now.
    """
    if egress.privacy_mode_enabled():
        return (f"Privacy mode is on, so {what} was not sent to a cloud "
                f"provider. Turn privacy mode off to allow it.")
    return None


def _read_switch(name: str, config=None) -> bool:
    """`name` from GSettings, defaulting **false** and never raising.

    `egress`'s own note explains why the import is inside the function - `config`
    pulls in `gi`/`Gio`, and this module is imported by `tts.py` and `stt.py`, so
    a module-level import would make speech synthesis depend on GObject being
    importable at all.

    The injectable half is for **`gui/surfaces/voice.py`**, whose panel is
    required by `tests/test_surface_voice.py` not to create anything in the config
    home. Constructing a fresh `ChronoaConfig()` there instantiates a
    `Gio.Settings`, which on first use creates `~/.config/glib-2.0` - so a
    *read-only* panel that asked the cloud engine's availability by building its
    own config wrote to the user's disk. The panel already holds a config;
    handing it over is also just better dependency direction.
    """
    try:
        if config is None:
            from shani_chronoa.config import ChronoaConfig
            config = ChronoaConfig()
        return bool(config.get_bool(name, False))
    except Exception:  # noqa: BLE001 - a switch that cannot be read is a switch that is off
        logger.exception("could not read %s; treating cloud speech as off", name)
        return False


def _provider_base(pid: str) -> str:
    from shani_chronoa import cloud_llm
    provider = cloud_llm.PROVIDERS.get(pid)
    return provider.base_url.rstrip("/") if provider else ""


def _provider_key(pid: str, api_keys: Optional[dict] = None, config=None) -> str:
    """The person's key for `pid`, keyring first, exactly as `cloud_llm` gets it."""
    if api_keys is not None:
        return api_keys.get(pid, "") or ""
    try:
        if config is None:
            from shani_chronoa.config import ChronoaConfig
            config = ChronoaConfig()
        return config.cloud_llm_api_keys().get(pid, "") or ""
    except Exception:  # noqa: BLE001
        logger.exception("could not read the %s API key", pid)
        return ""


def _requires_key(pid: str) -> bool:
    from shani_chronoa import cloud_llm
    provider = cloud_llm.PROVIDERS.get(pid)
    return bool(provider and provider.requires_key)


def _candidates(order, routes, api_keys: Optional[dict], config=None) -> list:
    """`(pid, base_url, key)` for every provider that can serve the route.

    A provider with no route contributes nothing, and **neither does one without
    a key**, because `KEY_REQUIRED_FOR_SPEECH` is measured rather than assumed:
    every provider with a speech route answers 401 unauthenticated, Kilo with the
    explicit `"PAID_MODEL_AUTH_REQUIRED"`. Building one anyway would leave a
    backend that fails its first real request - the thing `CloudLLMChain`'s own
    docstring says it avoids, and the thing that would otherwise show up as a
    turn that mysteriously says nothing.
    """
    out = []
    for pid in order:
        if pid not in routes:
            continue
        base = _provider_base(pid)
        if not base:
            continue
        key = _provider_key(pid, api_keys, config)
        if KEY_REQUIRED_FOR_SPEECH and not key:
            continue
        if _requires_key(pid) and not key:
            continue
        out.append((pid, base, key))
    return out


def _tts_request(pid: str, text: str) -> tuple:
    """`(model, response_format)` this provider actually wants.

    Two measured non-uniformities live here. BlockRun names its speech models
    after ElevenLabs (`elevenlabs/turbo-v2.5`), and sending `tts-1` there is a
    400 on a working route. OpenRouter's validator accepts only `mp3` and `pcm`
    and rejects `wav` outright. Asking for a format or a model a provider does
    not have would make the two cheapest providers into two 400s.
    """
    model = TTS_MODELS.get(pid, DEFAULT_TTS_MODEL)
    formats = TTS_FORMATS.get(pid, (DEFAULT_TTS_FORMAT,))
    return model, formats[0]


def _to_wav(audio: bytes, fmt: str) -> bytes:
    """WAV bytes, converting with ffmpeg when the provider could not send WAV.

    `ffmpeg` is already a dependency (`recordings.py` uses it for subtitles and
    transcripts), so this is not a new dependency - but if it is *missing* this
    raises rather than handing back MP3 bytes under a name every caller here
    believes means WAV. `ffprobe` would then read the header and the audio
    pipeline would fail three frames later instead of here, with the truth.
    """
    if fmt == DEFAULT_TTS_FORMAT:
        return audio
    if not shutil.which("ffmpeg"):
        raise CloudVoiceRefused(
            f"that provider speaks {fmt} and ffmpeg is not installed, so its "
            f"audio cannot be turned into the WAV everything else here expects")
    process = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
         "-f", "wav", "pipe:1"],
        input=audio, capture_output=True, timeout=60)
    if process.returncode != 0 or not process.stdout:
        raise CloudVoiceRefused(
            f"ffmpeg could not convert the provider's {fmt} to WAV: "
            f"{process.stderr.decode(errors='replace')[:200]}")
    return process.stdout


def probe_capabilities(timeout: float = 30.0, audio_wav: bytes = b"") -> dict:
    """Re-measure which providers really have the audio routes.

    **With a real request body and a real WAV, classifying the error message -
    not the status code.** That distinction is the whole reason this function
    exists in this shape.

    The first version sent an empty POST and read `400` as "the route exists".
    Measured, that produced a table wrong in three places:

    - **Kilo**: `400 {"error": "Invalid path", "message": "This endpoint only
      accepts the path /chat/completions."}` - it has **no** speech route at all,
      and an empty POST to a chat-only gateway says the same thing a valid body
      to a real route says.
    - **BlockRun**: `400 {"error": "Unknown speech model: tts-1. Available models:
      elevenlabs/flash-v2.5, ..."}` - the route exists and `tts-1` is simply the
      wrong name for it.
    - **OpenRouter**: `400 ZodError ... response_format ... values: ["mp3",
      "pcm"]` - the route exists and **wav** is not one of the answers.

    All three answered `400`. An empty POST cannot tell them apart, so the first
    table confidently claimed a speech route Kilo does not have and a WAV
    OpenRouter will not produce. **A status code is not a capability; the
    server's own sentence about what is wrong is.**

    Returns `{pid: {"base_url", "stt": {...}, "tts": {...}}}` where each verdict
    is `"yes"`, `"no"`, `"needs-key"` or `"unreachable"` - four states, because
    "I could not ask" and "it does not have one" are different answers and this
    repository's standing rule is that a check which cannot distinguish them is
    the expensive kind of wrong.
    """
    from shani_chronoa import cloud_llm
    if not audio_wav:
        audio_wav = _probe_tone()

    def verdict(response, ok_predicate) -> dict:
        if response.is_success:
            detail = ok_predicate(response)
            return detail if detail is not None else {
                "verdict": "no", "status": response.status_code,
                "detail": "succeeded but the answer was not usable: "
                          + response.text[:160]}
        text = response.text
        lowered = text.lower()
        # **Payment is its own answer, not "no" and not "needs-key".** Measured
        # 2026-10-06: BlockRun's `/audio/speech` answers HTTP 402 with an x402
        # challenge - `{"x402Version":2,"accepts":[{"scheme":"exact","network":
        # "eip155:8453","asset":"0x8335...","amount":"2000"}],"error":"Payment
        # Required","message":"This endpoint requires x402 payment","price":
        # {"amount":"0.002000","currency":"USD"}}`. The route exists and the
        # model name I send is right; the *access* costs 0.002 USD in USDC on
        # Base.
        #
        # So it is deliberately **not** in `TTS_ROUTES`: a reply silently billed to
        # a crypto wallet, behind a switch labelled "cloud speech synthesis" with
        # no mention of money, is worse than not offering it. Classified as its
        # own state so the finding is recorded rather than lost.
        if response.status_code == 402 or "payment required" in lowered \
                or "x402" in lowered:
            return {"verdict": "needs-paid", "status": response.status_code,
                    "detail": text[:200]}
        if response.status_code == 401 or "API key" in text or "auth" in lowered:
            return {"verdict": "needs-key", "status": response.status_code,
                    "detail": text[:160]}
        return {"verdict": "no", "status": response.status_code, "detail": text[:200]}

    out = {}
    for pid, provider in sorted(cloud_llm.PROVIDERS.items()):
        base = provider.base_url.rstrip("/")
        row = {"base_url": base}
        try:
            row["stt"] = verdict(
                httpx.post(base + "/audio/transcriptions",
                           files={"file": ("probe.wav", audio_wav, "audio/wav")},
                           data={"model": "whisper-1"}, timeout=timeout),
                lambda r: {"verdict": "yes", "detail": r.text[:120]}
                if isinstance(r.json().get("text"), str) else None)
        except Exception as exc:  # noqa: BLE001
            row["stt"] = {"verdict": "unreachable", "detail": type(exc).__name__}
        model, fmt = _tts_request(pid, "hello")
        try:
            row["tts"] = verdict(
                httpx.post(base + "/audio/speech", timeout=timeout,
                           json={"model": model, "input": "hello",
                                 "voice": DEFAULT_TTS_VOICE, "response_format": fmt}),
                lambda r: {"verdict": "yes",
                           "detail": f"{len(r.content)} bytes of {fmt}"}
                if r.content[:4] in (b"RIFF", b"ID3", b"\xff\xfb") else None)
        except Exception as exc:  # noqa: BLE001
            row["tts"] = {"verdict": "unreachable", "detail": type(exc).__name__}
        out[pid] = row

    # Anthropic is not in PROVIDERS (it has its own native-protocol adapter) and
    # has neither route; probed so the answer does not depend on knowing that.
    try:
        response = httpx.post("https://api.anthropic.com/v1/audio/transcriptions",
                              files={"file": ("probe.wav", audio_wav, "audio/wav")},
                              data={}, timeout=timeout)
        out["anthropic"] = {
            "base_url": "https://api.anthropic.com/v1",
            "stt": verdict(response, lambda r: {"verdict": "yes", "detail": ""}),
            "tts": {"verdict": "no", "status": None,
                    "detail": "not probed: same host as stt, and measured 404"},
        }
    except Exception as exc:  # noqa: BLE001
        out["anthropic"] = {"base_url": "https://api.anthropic.com/v1",
                            "stt": {"verdict": "unreachable", "detail": type(exc).__name__},
                            "tts": {"verdict": "unreachable", "detail": type(exc).__name__}}
    return out


def _probe_tone() -> bytes:
    """A short real WAV for the probe: a route that accepts nothing is not a route."""
    import io
    import math
    import struct
    import wave
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"".join(
            struct.pack("<h", int(3000 * math.sin(i * 0.05)))
            for i in range(16000)))
    return buffer.getvalue()


def _record(component: str, url: str, status: Optional[int], audio_bytes: int,
            purpose: str) -> None:
    """One line in the egress log, with the real audio size.

    `payload_size` is used for the text path and is meaningless here - what
    matters about a speech request is how much of a person's voice left the
    machine, and the byte count is the only honest proxy available.

    `purpose` is `"speech-recognition"` or `"speech-synthesis"` rather than
    empty, so an audit of the log can tell *why* audio left without having to
    recognise a path shape. `record` is what decides `violation`, and it treats
    a non-empty `purpose` as a consented fetch - so a purpose that says
    "recognising speech" is exactly the right claim to make here, because
    recognising speech is what the person agreed to by turning the switch on.
    """
    try:
        egress.record(component, url, method="POST", status=status,
                      bytes_out=audio_bytes,
                      privacy_mode=egress.privacy_mode_enabled(),
                      purpose=purpose)
    except Exception:  # noqa: BLE001 - the log must never be why speech fails
        logger.exception("could not record the cloud speech request to %s", url)


def _auth_headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"} if key else {}


class CloudSTT:
    """Cloud speech recognition, with the same surface as `WhisperSTT`.

    `is_available()` / `transcribe(path)` / `transcribe_stream(bytes)` is
    `WhisperSTT`'s public surface exactly, which is what lets this be a
    *selection* rather than a branch every caller has to write - the same
    argument `stt.build_stt()` makes about the two local backends.

    **Only use this when there is no local engine.** It is a fallback in the
    sense `cloud_llm` is: local first, cloud when local cannot answer.
    """

    def __init__(self, model: str = "", language: str = "en",
                 api_keys: Optional[dict] = None,
                 transport: Optional[httpx.BaseTransport] = None,
                 config=None) -> None:
        self.model = model or DEFAULT_STT_MODEL
        self.language = language or ""
        self._api_keys = api_keys
        self._transport = transport
        self._config = config
        self.last_provider = ""

    #: What the app's status line must call this. `_stt_backend_label()` used to
    #: answer from the *configured* backend, so a machine running cloud
    #: recognition logged "stt=Whisper.cpp" and named a program that was never
    #: invoked - the repo's own "two log lines a millisecond apart, disagreeing"
    #: failure, reached from a different direction. Read off the object instead.
    label = "Cloud"

    # -- the gate ---------------------------------------------------------
    def refusal(self) -> Optional[str]:
        """Why this instance would not send anything, or None.

        Exposed rather than kept private so a caller that is about to say "cloud
        speech is unavailable" can say *why* - "privacy mode is on" and "you have
        not configured a key" are different sentences and only one of them is
        something the person can act on.
        """
        if not _read_switch(STT_SWITCH, self._config):
            return (f"Cloud speech recognition is off ({STT_SWITCH} is not set). "
                    f"It is off by default because it sends your voice to "
                    f"somebody else's computer.")
        refusal = _privacy_refusal("your recording")
        if refusal:
            return refusal
        if not _candidates(STT_ORDER, STT_ROUTES, self._api_keys, self._config):
            return ("No configured cloud provider offers speech recognition. "
                    "OpenAI, Groq, OpenRouter, Kilo and LLM7 have the route, and "
                    "every one of them needs a key - measured, none accepts an "
                    "anonymous request. Google, BlockRun and OpenCode Zen have "
                    "no transcription endpoint at all.")
        return None

    def is_available(self) -> bool:
        """Whether a request could be made right now.

        The honest question, and deliberately the *same* question `WhisperSTT`
        answers - so a caller can ask either without knowing which it has.
        """
        return self.refusal() is None

    # -- the work ---------------------------------------------------------
    def _post(self, pid: str, base: str, key: str, filename: str,
              audio: bytes) -> str:
        url = f"{base}/audio/transcriptions"
        files = {"file": (filename, audio, "audio/wav")}
        # A provider may name its transcription model differently; measured names
        # live in `STT_MODELS` so a wrong name is one edit rather than a 400 on
        # a route that works.
        data = {"model": STT_MODELS.get(pid, self.model), "response_format": "json"}
        if self.language:
            data["language"] = self.language
        with httpx.Client(timeout=60.0, transport=self._transport) as client:
            response = client.post(url, files=files, data=data,
                                   headers=_auth_headers(key))
        _record(f"cloud_stt:{pid}", url, response.status_code, len(audio),
                "speech-recognition")
        if not response.is_success:
            raise CloudVoiceRefused(
                f"{pid} refused the transcription (HTTP {response.status_code})")
        try:
            payload = response.json()
        except ValueError as exc:
            raise CloudVoiceRefused(
                f"{pid} returned a transcription that was not JSON: {exc}") from exc
        text = payload.get("text")
        if not isinstance(text, str):
            raise CloudVoiceRefused(
                f"{pid} returned no 'text' field: {sorted(payload)}")
        return text

    def _transcribe_bytes(self, audio: bytes, filename: str) -> str:
        refusal = self.refusal()
        if refusal:
            raise CloudVoiceRefused(refusal)
        if not audio:
            return ""
        last: Optional[Exception] = None
        for pid, base, key in _candidates(STT_ORDER, STT_ROUTES, self._api_keys, self._config):
            try:
                self.last_provider = pid
                return self._post(pid, base, key, filename, audio)
            except Exception as exc:  # noqa: BLE001 - fall through to the next one
                last = exc
                logger.warning("cloud STT via %s failed: %s", pid, exc)
        raise CloudVoiceRefused(f"no cloud provider transcribed it ({last})")

    def transcribe(self, audio_file: str) -> str:
        """Transcribe a WAV on disk. `WhisperSTT`'s signature, unchanged."""
        with open(audio_file, "rb") as handle:
            audio = handle.read()
        return self._transcribe_bytes(audio, os.path.basename(audio_file))

    def transcribe_stream(self, audio_data: bytes) -> str:
        """Transcribe raw bytes. `WhisperSTT`'s other signature, unchanged."""
        return self._transcribe_bytes(audio_data, "capture.wav")


class CloudTTS:
    """Cloud speech synthesis, with `PiperTTS`'s surface.

    **This is the last link of `PiperTTS.engine()`'s chain, after Kokoro, Piper
    and espeak-ng.** That ordering is the whole design: nothing local is displaced
    by turning this on, and a machine that has `espeak-ng` (a hard package
    dependency) always has a voice. Someone who enables this has *added* a
    fallback for a machine with no neural voice, not replaced one.
    """

    def __init__(self, voice: str = "", model: str = "",
                 api_keys: Optional[dict] = None,
                 transport: Optional[httpx.BaseTransport] = None,
                 config=None) -> None:
        self.voice = voice or DEFAULT_TTS_VOICE
        self.model = model or DEFAULT_TTS_MODEL
        self._api_keys = api_keys
        self._transport = transport
        #: An injected config is used for the switch **and** for the keys, so a
        #: caller that must not touch the settings store (the read-only Voice
        #: panel) can hand over one it already holds.
        self._config = config
        self.last_provider = ""

    def refusal(self) -> Optional[str]:
        if not _read_switch(TTS_SWITCH, self._config):
            return (f"Cloud speech synthesis is off ({TTS_SWITCH} is not set). "
                    f"It is off by default because it sends the reply to "
                    f"somebody else's computer.")
        refusal = _privacy_refusal("the reply")
        if refusal:
            return refusal
        if not _candidates(TTS_ORDER, TTS_ROUTES, self._api_keys, self._config):
            return ("No configured cloud provider offers speech synthesis. "
                    "OpenAI, Groq and OpenRouter have the route, and all three "
                    "need a key. LLM7, Kilo, Google and OpenCode Zen have no "
                    "speech endpoint; BlockRun has one but bills 0.002 USD in "
                    "crypto per request, so it is not offered here.")
        return None

    def is_available(self) -> bool:
        return self.refusal() is None

    def _post(self, pid: str, base: str, key: str, text: str) -> bytes:
        url = f"{base}/audio/speech"
        model, fmt = _tts_request(pid, text)
        payload = {"model": model, "input": redactor.sanitize(text),
                   "voice": self.voice, "response_format": fmt}
        sent = len(str(payload["input"]).encode("utf-8", "replace"))
        with httpx.Client(timeout=60.0, transport=self._transport) as client:
            response = client.post(url, json=payload, headers=_auth_headers(key))
        _record(f"cloud_tts:{pid}", url, response.status_code, sent,
                "speech-synthesis")
        if not response.is_success:
            raise CloudVoiceRefused(
                f"{pid} refused the synthesis (HTTP {response.status_code})")
        if not response.content:
            raise CloudVoiceRefused(f"{pid} returned no audio")
        # A provider that cannot send WAV is still usable, as long as the audio
        # is really converted rather than handed on under a name every caller
        # here believes means WAV.
        return _to_wav(response.content, fmt)

    def synthesize_to_bytes(self, text: str) -> bytes:
        """The reply as WAV bytes. `PiperTTS`'s signature, unchanged.

        Raises `CloudVoiceRefused` rather than returning `b""`. A silent empty
        answer here would be indistinguishable from a reply that was empty, and
        the caller would speak nothing and say nothing about why.
        """
        refusal = self.refusal()
        if refusal:
            raise CloudVoiceRefused(refusal)
        if not text.strip():
            return b""
        last: Optional[Exception] = None
        for pid, base, key in _candidates(TTS_ORDER, TTS_ROUTES, self._api_keys, self._config):
            try:
                self.last_provider = pid
                return self._post(pid, base, key, text)
            except Exception as exc:  # noqa: BLE001 - fall through to the next one
                last = exc
                logger.warning("cloud TTS via %s failed: %s", pid, exc)
        raise CloudVoiceRefused(f"no cloud provider spoke it ({last})")

    def synthesize(self, text: str, output_file: str) -> bool:
        """Write the WAV to `output_file`. `PiperTTS`'s signature, unchanged."""
        try:
            audio = self.synthesize_to_bytes(text)
        except CloudVoiceRefused as exc:
            logger.warning("cloud speech synthesis declined: %s", exc)
            return False
        if not audio:
            return False
        with open(output_file, "wb") as handle:
            handle.write(audio)
        return True