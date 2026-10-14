"""Chronoa's voice, end to end, through real audio - and scored, not just run.

A private PipeWire (its own runtime dir and D-Bus; nothing reaches the real
microphone or speakers) with two virtual devices:

- ``demo_mic``: a source fed by playing into the ``mic_feed`` sink, so a known
  spoken sentence becomes what Chronoa's microphone hears;
- ``demo_speaker``: a sink whose output is recorded, so what Chronoa says can
  be heard back.

Then Chronoa's own code does the work - `audio.AudioRecorder` with its
silence auto-stop, `stt.build_stt` (whisper.cpp), `tts.PiperTTS` (its fallback
chain, espeak-ng here) and `audio.AudioPlayer` - and each direction is scored
by word error rate against the text that was actually spoken. A transcript
that merely exists is not evidence; "convert two hundred dollars" came back as
"convert to $100" in the first probe, which a non-empty check would pass.

Skipped without PipeWire, whisper-cli, espeak-ng or a whisper model. The model
is found in `$CHRONOA_TEST_WHISPER_MODEL`, Chronoa's own model folder, or the
shani-install-media test cache next to this repo.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import wave
from array import array
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_TOOLS = ("pipewire", "wireplumber", "pw-loopback", "pw-play", "pw-record", "pw-cli",
          "dbus-daemon", "whisper-cli", "espeak-ng")


def _model() -> str:
    candidates = [os.environ.get("CHRONOA_TEST_WHISPER_MODEL", ""),
                  str(Path.home() / ".local/share/whisper/models/ggml-base-q5_1.bin"),
                  str(REPO.parent / "shani-install-media/test-env/cache/whisper-models/ggml-base-q5_1.bin")]
    return next((c for c in candidates if c and os.path.isfile(c)), "")


#: Downloaded voices and models kept between runs (see `.gitignore`).
CACHE = REPO / "cache" / "data-home"
_MODEL = _model()


pytestmark = pytest.mark.skipif(
    not all(shutil.which(t) for t in _TOOLS) or not _MODEL,
    reason="needs PipeWire, wireplumber, whisper-cli, espeak-ng and a whisper model")


# --- scoring -------------------------------------------------------------------

_NUMBERS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
            "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"}


def words(text: str) -> list:
    """Lower-case words with punctuation dropped and small numbers as digits."""
    out = []
    for w in re.findall(r"[a-z0-9']+", text.lower()):
        out.append(_NUMBERS.get(w, w))
    return out


def wer(reference: str, heard: str) -> float:
    """Word error rate: edits needed to turn `heard` into `reference`, per reference word."""
    ref, hyp = words(reference), words(heard)
    row = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, row[0] = row[0], i
        for j, h in enumerate(hyp, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (r != h))
    return row[len(hyp)] / max(1, len(ref))


def rms(path: str) -> float:
    with wave.open(path) as w:
        frames = w.readframes(w.getnframes())
        width = w.getsampwidth()
    if width != 2 or not frames:
        return 0.0
    samples = array("h", frames)
    return (sum(s * s for s in samples) / len(samples)) ** 0.5 / 32768


def test_the_scorer_itself():
    assert wer("convert two hundred dollars", "convert two hundred dollars") == 0
    assert wer("convert two hundred dollars", "convert to $100") == 0.75
    assert wer("remind me at nine", "Remind me at 9.") == 0


# --- the private audio graph ---------------------------------------------------

class _PrivateAudio:
    """A PipeWire graph nobody else can hear, torn down by recorded PID."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.run = Path(tempfile.mkdtemp(prefix="cwa."))
        os.chmod(self.run, 0o700)
        home = root / "home"
        home.mkdir(exist_ok=True)
        self.env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "LANG": "C.UTF-8",
                    "XDG_RUNTIME_DIR": str(self.run), "PIPEWIRE_RUNTIME_DIR": str(self.run)}
        out = subprocess.run(["dbus-daemon", "--session", "--fork", "--print-address=1",
                              "--print-pid=1"], env=self.env, capture_output=True, text=True,
                             timeout=20, check=True).stdout.split()
        self.env["DBUS_SESSION_BUS_ADDRESS"], self.bus_pid = out[0], int(out[1])
        self.procs = []
        self._spawn(["pipewire"], 1.5)
        self._spawn(["wireplumber"], 2.0)
        self._spawn(["pw-loopback", "-n", "demo-mic",
                     "--capture-props=media.class=Audio/Sink node.name=mic_feed node.description=mic_feed",
                     "--playback-props=media.class=Audio/Source node.name=demo_mic node.description=demo_mic"], 0)
        self._spawn(["pw-loopback", "-n", "demo-speaker",
                     "--capture-props=media.class=Audio/Sink node.name=demo_speaker node.description=demo_speaker",
                     "--playback-props=media.class=Audio/Source node.name=speaker_tap node.description=speaker_tap"], 0)
        # **A graph that fails to come up cleans up after itself.** Everything
        # above this line has already spawned `dbus-daemon`, `pipewire`,
        # `wireplumber` and two `pw-loopback`s, and the failure arrives 15 seconds
        # later. If `__init__` simply raised, the caller's `except` would have no
        # object to call `stop()` on - so four daemons survive, holding Bluetooth's
        # `sco_listen` socket and a runtime dir somebody later `rmtree`s from under
        # them. Measured: two runs that skipped left exactly two orphaned graphs,
        # and the run after them could not start one.
        try:
            self._wait_for_nodes(("mic_feed", "demo_mic", "demo_speaker",
                                  "speaker_tap"))
        except BaseException:
            self.stop()
            raise

    def _spawn(self, argv, settle: float) -> None:
        log = open(self.root / f"{argv[0]}-{len(self.procs)}.log", "w")
        self.procs.append(subprocess.Popen(argv, env=self.env, stdout=log, stderr=subprocess.STDOUT))
        time.sleep(settle)

    def _wait_for_nodes(self, names) -> None:
        end = time.monotonic() + 15
        while time.monotonic() < end:
            listing = subprocess.run(["pw-cli", "ls", "Node"], env=self.env,
                                     capture_output=True, text=True, timeout=10).stdout
            if all(f'node.name = "{n}"' in listing for n in names):
                return
            time.sleep(0.3)
        raise AssertionError(f"the virtual devices did not appear: {names}")

    def say_into_mic(self, wav: str) -> None:
        subprocess.run(["pw-play", "--target", "mic_feed", wav], env=self.env, timeout=60, check=True)

    def record_speaker(self, path: str) -> subprocess.Popen:
        return subprocess.Popen(["pw-record", "--target", "speaker_tap", "--rate", "16000",
                                 "--channels", "1", path], env=self.env)

    def _daemons_of_this_graph(self) -> "list[int]":
        """Every live process whose runtime directory is **this graph's**.

        `pipewire` and `wireplumber` **daemonise**: the `Popen` returns and exits,
        and the real process is reparented to `systemd --user`. So `self.procs`
        holds handles that are already dead, `stop()`'s `poll()` says they are
        finished, and nothing reaches the daemon.

        Measured, and the consequences are not theoretical:

        - the orphan keeps serving a socket directory that `stop()` has already
          `rmtree`'d - a live daemon whose `XDG_RUNTIME_DIR` no longer exists;
        - it keeps holding Bluetooth's `sco_listen` socket, so the **next** graph's
          `wireplumber` logs `sco_listen: listen(): Address already in use` and
          `org.bluez.Error.NotPermitted`, and the capture that follows is empty.

        So each run leaked a daemon that quietly poisoned the next one, which is
        the reason this file was flaky rather than consistently red - and the
        reason "no audio server on this machine" was the wrong conclusion to draw
        from it.

        Matched on `XDG_RUNTIME_DIR` rather than on a name, because a name matches
        whatever else happens to be called `pipewire` - including the real desktop
        session's, which this must never touch.
        """
        mine = str(self.run)
        found = []
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                environ = (entry / "environ").read_bytes().split(b"\0")
            except OSError:
                continue                      # gone, or not ours to read
            for item in environ:
                if item == b"XDG_RUNTIME_DIR=" + mine.encode():
                    found.append(int(entry.name))
                    break
        return found

    def stop(self) -> None:
        for proc in reversed(self.procs):
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        # **And the ones that daemonised away from their Popen.** Found by runtime
        # directory and killed by pid, which is the only addressing that reaches
        # a process whose handle is already spent.
        for pid in self._daemons_of_this_graph():
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                continue
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if not self._daemons_of_this_graph():
                break
            time.sleep(0.2)
        for pid in self._daemons_of_this_graph():
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            os.kill(self.bus_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        shutil.rmtree(self.run, ignore_errors=True)
        # A GIO client can recreate `gvfs/` here a moment after the first removal
        # (seen once); a second pass after a beat leaves nothing behind.
        time.sleep(0.5)
        shutil.rmtree(self.run, ignore_errors=True)


@pytest.fixture(scope="module")
def audio(tmp_path_factory):
    """The private graph, or a skip that says the graph did not come up.

    **A graph that will not start is a skip, not a failure.** `_PrivateAudio`
    already knows how to tell - it waits fifteen seconds for its four virtual
    devices and raises `AssertionError` - so this catches that and reports it,
    naming the condition rather than leaving a chain of
    `FileNotFoundError: Audio file not found` behind it.

    Two narrower attempts got here first and both are worth recording. Asking
    whether the **binaries** are on `PATH` is the wrong question - this machine
    has all nine of them, and every test then failed with
    `pw_context_connect() failed: Host is down`. Asking whether an **ambient**
    `Pw.Context` can connect is also the wrong question, and more expensively so:
    these tests talk to *this* graph, through its own runtime dir, and here the
    private graph comes up perfectly well while the ambient session has no server
    at all. That second probe over-skipped **5 of 19** tests which can and do run
    on this machine - a skip is not coverage, and this one would have cost real
    checks to save nothing.
    """
    try:
        graph = _PrivateAudio(tmp_path_factory.mktemp("audio"))
    except AssertionError as exc:
        pytest.skip(f"the private PipeWire graph did not come up: {exc}")
    try:
        yield graph
    finally:
        graph.stop()


@pytest.fixture
def in_that_graph(audio, monkeypatch, tmp_path):
    """This process (and so Chronoa's pw-record/pw-play) talks to the private graph."""
    for key in ("XDG_RUNTIME_DIR", "PIPEWIRE_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"):
        monkeypatch.setenv(key, audio.env[key])
    models = tmp_path / "data" / "whisper" / "models"
    models.mkdir(parents=True)
    os.symlink(_MODEL, models / "ggml-base-q5_1.bin")
    # Voices installed once into the repo's git-ignored cache (`cache/data-home`,
    # laid out like a data home) are linked in, so nothing is downloaded again.
    for rel in ("piper", "kokoro", "shani-chronoa/piper", "shani-chronoa/sherpa-onnx"):
        source = CACHE / rel
        if source.exists():
            target = tmp_path / "data" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(source, target)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("SHANI_CHRONOA_STT_SERVER", "0")
    return audio


def _espeak(text: str, path: Path) -> str:
    subprocess.run(["espeak-ng", "-v", "en-us", "-s", "140", "-w", str(path), text], check=True)
    return str(path)


def _listen(audio, say_wav, silence=2.0, max_seconds=25.0):
    """Chronoa's recorder on the virtual mic while `say_wav` (or nothing) is spoken into it."""
    from shani_chronoa.audio import AudioRecorder
    recorder = AudioRecorder(target="demo_mic")
    box, done = {}, threading.Event()

    def finished(path):
        box["path"] = path
        done.set()

    assert recorder.start_auto_stop(finished, max_seconds=max_seconds, silence_seconds=silence)
    time.sleep(0.8)
    if say_wav:
        audio.say_into_mic(say_wav)
    assert done.wait(max_seconds + 10), "the recorder never stopped on its own"
    return box.get("path")


# --- the tests -------------------------------------------------------------------

REQUEST = "Check the weather in London, and remind me tomorrow at nine to pack my bags."


def test_chronoa_hears_what_was_said(in_that_graph, tmp_path):
    from shani_chronoa.stt import build_stt
    path = _listen(in_that_graph, _espeak(REQUEST, tmp_path / "say.wav"))
    assert path and os.path.getsize(path) > 10_000, "nothing was recorded"
    stt = build_stt("base")
    assert stt.is_available(), f"whisper is not usable: {stt.model_path}"
    heard = stt.transcribe(path)
    score = wer(REQUEST, heard)
    print(f"\nsaid:  {REQUEST}\nheard: {heard}\nWER:   {score:.2f}")
    assert score <= 0.30, f"heard {heard!r} for {REQUEST!r} (WER {score:.2f})"


def test_silence_is_not_a_request(in_that_graph):
    """The control: nothing spoken must not come back as words."""
    from shani_chronoa.stt import build_stt
    path = _listen(in_that_graph, None, silence=1.0, max_seconds=6.0)
    heard = build_stt("base").transcribe(path) if path else ""
    assert len(words(heard)) <= 2, f"silence was transcribed as {heard!r}"


REPLY = "It is eight degrees in London. I will remind you tomorrow at nine to pack your bags."
#: Words that carry the reply's meaning apart from its numbers.
KEY_WORDS = ("london", "remind", "tomorrow", "pack")


def _speak_and_hear(graph, tmp_path):
    """Chronoa's voice into the virtual speaker; return (engine, transcript, loudness)."""
    from shani_chronoa.audio import AudioPlayer
    from shani_chronoa.stt import build_stt
    from shani_chronoa.tts import PiperTTS
    tts = PiperTTS()
    engine = tts.engine(REPLY) or ""
    wav = tts.synthesize_to_bytes(REPLY)
    assert wav[:4] == b"RIFF", "no speech was produced"
    spoken = tmp_path / "reply.wav"
    spoken.write_bytes(wav)
    capture = tmp_path / "heard-reply.wav"
    rec = graph.record_speaker(str(capture))
    time.sleep(0.8)
    try:
        assert AudioPlayer(target="demo_speaker").play_file(str(spoken)), "playback did not finish"
        time.sleep(0.8)
    finally:
        rec.terminate()
        rec.wait(timeout=10)
    heard = build_stt("base").transcribe(str(capture))
    print(f"\nengine: {engine}\nsaid:  {REPLY}\nheard: {heard}\nWER:   {wer(REPLY, heard):.2f}")
    return engine, heard, rms(str(capture))


def test_what_chronoa_says_is_audible_and_recognisable(in_that_graph, tmp_path):
    _engine, heard, loudness = _speak_and_hear(in_that_graph, tmp_path)
    assert loudness > 0.005, f"the speaker recording is silent (RMS {loudness:.4f})"
    missing = [w for w in KEY_WORDS if w not in words(heard)]
    assert not missing, f"the reply was heard as {heard!r}; lost {missing}"


def test_a_neural_voice_is_understood_word_for_word(in_that_graph, tmp_path):
    """The bar hearing meets (WER 0.13 measured). espeak-ng cannot meet it.

    espeak-ng is the last fallback, a formant synthesiser. Measured 2026-10-08
    through whisper base: WER 0.35 and 0.47 on two runs of the same sentence,
    with the numbers the reply is about lost ("eight degrees" heard as "made to
    breathe", "at nine" as "time"). So with espeak-ng this is skipped *with that
    reason* rather than passed on a lowered bar; it runs as soon as Piper or
    Kokoro can speak.
    """
    from shani_chronoa.tts import PiperTTS
    if "espeak" in (PiperTTS().engine(REPLY) or "espeak").lower():
        pytest.skip("only espeak-ng can speak here; measured WER 0.35-0.47 with numbers "
                    "lost - install a Piper voice (Settings, Voice) to hold the voice to "
                    "the same bar as hearing")
    engine, heard, _ = _speak_and_hear(in_that_graph, tmp_path)
    score = wer(REPLY, heard)
    assert score <= 0.30, f"{engine} was heard as {heard!r} (WER {score:.2f})"


# --- which espeak-ng settings are easiest to understand --------------------------

SENTENCES = (REPLY, "Your flight leaves at five twenty seven and the total is two hundred dollars.")


@pytest.mark.parametrize("variant,speed", [("+f3", 175), ("+f3", 150), ("", 175), ("+m3", 160), ("+f2", 150)])
def test_espeak_settings_measured(in_that_graph, tmp_path, monkeypatch, variant, speed):
    """A measurement, printed: mean WER of Chronoa's espeak-ng path per variant and speed.

    Rate, pitch, tempo and every named style are SoX effects applied after the
    engine (`tts.apply_timbre`), so without SoX they change nothing; the engine's
    own variant and words-per-minute are what reach the ear either way. The
    assertion is only that speech stays recognisable, the numbers are the point.
    """
    from shani_chronoa import tts as tts_mod
    from shani_chronoa.audio import AudioPlayer
    from shani_chronoa.stt import build_stt
    monkeypatch.setattr(tts_mod, "ESPEAK_VARIANT", variant)
    engine = tts_mod.PiperTTS()
    if "espeak" not in (engine.engine(REPLY) or ""):
        pytest.skip("a voice other than espeak-ng speaks here")
    engine.rate = speed / 175
    stt = build_stt("base")
    scores = []
    for n, sentence in enumerate(SENTENCES):
        spoken = tmp_path / f"s{n}.wav"
        spoken.write_bytes(engine.synthesize_to_bytes(sentence))
        capture = tmp_path / f"h{n}.wav"
        rec = in_that_graph.record_speaker(str(capture))
        time.sleep(0.6)
        AudioPlayer(target="demo_speaker").play_file(str(spoken))
        time.sleep(0.6)
        rec.terminate(); rec.wait(timeout=10)
        heard = stt.transcribe(str(capture))
        scores.append(wer(sentence, heard))
        print(f"\n  [{variant or 'default'} {speed}wpm] heard: {heard}")
    mean = sum(scores) / len(scores)
    print(f"\nESPEAK {variant or 'default':8} {speed}wpm  mean WER {mean:.2f}  ({', '.join(f'{s:.2f}' for s in scores)})")
    assert mean < 0.8, "speech stopped being recognisable at all"


@pytest.mark.skipif(not shutil.which("sox"), reason="styles are SoX effects; without SoX they change nothing")
@pytest.mark.parametrize("style", ["natural", "assistant", "gentle", "calm", "clear", "warm"])
def test_voice_styles_measured(in_that_graph, tmp_path, gsettings_env, style):
    """A measurement, printed: mean WER of each selectable `voice-style` on the default voice."""
    from shani_chronoa.audio import AudioPlayer
    from shani_chronoa.config import ChronoaConfig
    from shani_chronoa.stt import build_stt
    from shani_chronoa.tts import PiperTTS
    config = ChronoaConfig()
    config.set("voice-style", style)
    engine = PiperTTS(config=config)
    applied = engine.timbre_description()
    stt = build_stt("base")
    scores = []
    for n, sentence in enumerate(SENTENCES):
        spoken = tmp_path / f"s{n}.wav"
        spoken.write_bytes(engine.synthesize_to_bytes(sentence))
        capture = tmp_path / f"h{n}.wav"
        rec = in_that_graph.record_speaker(str(capture))
        time.sleep(0.6)
        AudioPlayer(target="demo_speaker").play_file(str(spoken))
        time.sleep(0.6)
        rec.terminate(); rec.wait(timeout=10)
        scores.append(wer(sentence, stt.transcribe(str(capture))))
    mean = sum(scores) / len(scores)
    print(f"\nSTYLE {style:10} mean WER {mean:.2f}  ({', '.join(f'{s:.2f}' for s in scores)})  effects: {applied or 'none'}")
    assert mean < 0.8, "speech stopped being recognisable at all"



# --- which voice engine: understood, how soon, how fast ---------------------------

def _wav_seconds(data: bytes) -> float:
    import io
    with wave.open(io.BytesIO(data)) as w:
        return w.getnframes() / float(w.getframerate())


@pytest.mark.parametrize("engine", ["espeak-ng", "piper", "kokoro"])
def test_voice_engines_measured(in_that_graph, tmp_path, gsettings_env, monkeypatch, engine):
    """A measurement, printed: each engine through Chronoa's own TTS path.

    WER: Chronoa's voice through the virtual speaker, transcribed by whisper.
    First audio: how long the first sentence takes to synthesise - the silence
    before Chronoa starts talking, since replies are spoken sentence by sentence.
    RTF: synthesis time / audio length; above 1.0 the next sentence is not ready
    when the current one ends, so a reply has gaps.
    """
    from shani_chronoa.audio import AudioPlayer
    from shani_chronoa.config import ChronoaConfig
    from shani_chronoa.stt import build_stt
    from shani_chronoa.tts import PiperTTS
    config = ChronoaConfig()
    config.set("kokoro-tts-enabled", "true" if engine == "kokoro" else "false")
    tts = PiperTTS(config=config)
    # **`*args`, not a named second parameter.** `8ae9a88` ("piper: the second
    # voice in the chain gets a switch too") gave `_engine` a second argument,
    # `piper_reason`, and `engine()` calls `self._engine(reason, piper_reason)`.
    # This was `lambda self, reason: engine`, which now raises
    # `TypeError: <lambda>() takes 2 positional arguments but 3 were given` on
    # every call - so the failure surfaced as a missing `heard-reply.wav` rather
    # than as a broken seam, which is how this file looked like live-audio
    # flakiness instead of an arity mismatch. `*args` cannot be defeated by a
    # parameter being added.
    monkeypatch.setattr(PiperTTS, "_engine", lambda self, *_a, **_k: engine)
    if engine != "espeak-ng":
        from shani_chronoa import voices
        ready = (voices.piper_binary() and (voices.voice_dir() / "en_US-lessac-medium.onnx").is_file()
                 if engine == "piper" else not voices.kokoro_problem(voices.KOKORO_DEFAULT_VOICE))
        if not ready:
            pytest.skip(f"{engine} is not installed in cache/data-home")
    stt = build_stt("base")
    scores, synth, audio = [], 0.0, 0.0
    first = None
    for n, sentence in enumerate((REPLY,) + SENTENCES[1:]):
        start = time.monotonic()
        data = tts.synthesize_to_bytes(sentence)
        took = time.monotonic() - start
        assert data[:4] == b"RIFF", f"{engine} produced no speech"
        first = took if first is None else first
        synth, audio = synth + took, audio + _wav_seconds(data)
        spoken = tmp_path / f"{engine}{n}.wav"
        spoken.write_bytes(data)
        capture = tmp_path / f"{engine}{n}-heard.wav"
        rec = in_that_graph.record_speaker(str(capture))
        time.sleep(0.6)
        AudioPlayer(target="demo_speaker").play_file(str(spoken))
        time.sleep(0.6)
        rec.terminate(); rec.wait(timeout=10)
        heard = stt.transcribe(str(capture))
        scores.append(wer(sentence, heard))
        print(f"\n  [{engine}] heard: {heard}")
    mean = sum(scores) / len(scores)
    print(f"\nENGINE {engine:9} WER {mean:.2f}  first audio {first:.2f}s  RTF {synth / audio:.2f}"
          f"  ({', '.join(f'{x:.2f}' for x in scores)})")
    assert mean < 0.8, "speech stopped being recognisable at all"
