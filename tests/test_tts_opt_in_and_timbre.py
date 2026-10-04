"""The two things about the voice that only show up when you measure them.

**Kokoro is opt-in, and the ordering is the whole change.** It was first in the
chain, which made it the default everywhere, and the numbers in `AGENTS.md` are
why that was wrong: 6.39 s of wall clock for 5.225 s of audio on the dev
machine, a real-time factor of 1.22, so every reply opened with about six
seconds of silence while espeak-ng produced 3.7 s of audio in 0.018 s. The
first test here is the control for that: with the model, the runtime and
espeak-ng all present, the engine must be espeak-ng, and must be Kokoro again
the moment the setting is turned on. Reverting `_kokoro_reason` to ask only
whether Kokoro *can* speak makes it red, which is the point of writing it
against a dispatcher whose own chain is real rather than against a stubbed
answer.

**The timbre pass degrades honestly.** SoX is an optdepend, so the case that
matters is the machine without it: the audio must be exactly what the engine
wrote and the reason must name the transform that was skipped, because a
reported pitch shift that did not happen is the expensive kind of wrong. The
complementary case is the one that is easy to get wrong quietly - the defaults.
`pitch=0`, `tempo=1.0` and `rate=1.0` must not run SoX at all, or every reply
is re-encoded for nothing and the bytes stop being the engine's.
"""

import hashlib
import json
import math
import os
import subprocess
import sys
import textwrap
import wave
from pathlib import Path

import pytest

from shani_chronoa import sherpa, tts, voices
from shani_chronoa.stt_provision import ModelSpec

SENTENCE = "Sure, I set a timer for five minutes."


class _Config:
    """The download consent gate, which is all `install_kokoro` asks for."""

    def get_bool(self, key, default):
        assert key == "model-download-enabled", f"unexpected consent key {key!r}"
        return True


def _tarball(members: dict) -> bytes:
    """A .tar.bz2 of {path: (bytes, mode)}."""
    import io
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:bz2") as tar:
        for name, (body, mode) in members.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(body), mode
            tar.addfile(info, io.BytesIO(body))
    return buf.getvalue()


#: stand-ins for the two real archives, laid out as the real ones are
SHERPA_TAR = _tarball({
    f"sherpa-onnx-{sherpa.VERSION}-linux-x64-shared/bin/sherpa-onnx-offline-tts":
        (b"#!/bin/sh\nexit 0\n", 0o755)})
MODEL_TAR = _tarball({f"{voices._KOKORO_MODEL_DIR}/{name}": (b"x", 0o644) for name in
                      ("model.int8.onnx", "voices.bin", "tokens.txt", "espeak-ng-data/phontab")})


@pytest.fixture
def pinned(monkeypatch):
    """Replace the two shipped archives with tiny ones this test can hash."""
    monkeypatch.setattr(sherpa, "RELEASE", ModelSpec(
        "sherpa-onnx", sherpa.RELEASE.filename, len(SHERPA_TAR), hashlib.sha256(SHERPA_TAR).hexdigest(),
        "", "https://github.com/x"))
    monkeypatch.setattr(voices, "_KOKORO_MODEL", ModelSpec(
        "kokoro", voices._KOKORO_MODEL.filename, len(MODEL_TAR), hashlib.sha256(MODEL_TAR).hexdigest(),
        "", "https://github.com/x"))


@pytest.fixture
def kokoro_installed(pinned, monkeypatch):
    """A Kokoro that genuinely can speak: both archives verified and unpacked by `install_kokoro`."""
    import httpx

    def handler(request):
        body = SHERPA_TAR if str(request.url).endswith(sherpa.RELEASE.filename) else MODEL_TAR
        return httpx.Response(200, headers={"content-length": str(len(body))}, content=body)
    voices.install_kokoro(config=_Config(), transport=httpx.MockTransport(handler))
    return voices.kokoro_dir()


def _transport(body: bytes):
    import httpx

    def handler(request):
        return httpx.Response(200, headers={"content-length": str(len(body))},
                              content=body)

    return httpx.MockTransport(handler)


@pytest.fixture
def only_espeak(monkeypatch, tmp_path):
    """An engine chain with espeak-ng in it and nothing above it.

    `which` is patched rather than relied on, because the answer this file needs
    is "espeak-ng is reachable and nothing better is". That is true on this
    machine by accident - no piper, no RHVoice - and would stop being true on
    one that had them, which would make the control test fail for a reason that
    has nothing to do with the thing it controls.
    """
    monkeypatch.setattr(tts.shutil, "which",
                        lambda name: "/usr/bin/espeak-ng" if name == "espeak-ng" else None)
    return tmp_path / "no-piper-here"


def _wav(path: str, seconds: float = 1.0, rate: int = 22050) -> str:
    """A real, playable WAV - not a header, so a transform has samples to move."""
    with wave.open(path, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"".join(
            int(8000 * math.sin(2 * math.pi * 440 * n / rate)).to_bytes(2, "little", signed=True)
            for n in range(int(rate * seconds))))
    return path


def _frames(path: str) -> int:
    with wave.open(path) as handle:
        return handle.getnframes()


def _fake_sox(tmp_path: Path, body: str) -> "tuple[Path, Path]":
    """A SoX stand-in on PATH: `(binary, the file it records its argv in)`.

    Written as a real script rather than a monkeypatched `subprocess.run`,
    because `tts.apply_timbre` resolves the binary with `shutil.which` and
    hands it an argv - a patched `run` would test the code against a fiction
    about how the program is found and what it is given.

    The prologue is concatenated rather than interpolated into a dedented
    template: `textwrap.dedent` works on the *whole* string, so a body written
    at column 0 (which is how it reads in this file) silently un-indents the
    lines around it and produces a script the kernel will not execute - an
    `Exec format error` that reads like a code bug and is not one.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "sox.argv"
    script = bin_dir / "sox"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "argv = sys.argv[1:]\n"
        f"with open({str(log)!r}, 'a', encoding='utf-8') as handle:\n"
        "    handle.write(repr(argv) + '\\n')\n"
        + textwrap.dedent(body))
    script.chmod(0o755)
    return script, log


# --- 1. Kokoro is opt-in -----------------------------------------------------

def test_kokoro_is_not_the_engine_when_the_setting_is_off(kokoro_installed, only_espeak,
                                                          chronoa_config):
    """The control for the whole change: everything installed, Kokoro still off.

    Asserted through the real `voices` files and the real `kokoro.problem()`,
    so the only thing that can make this pass is Kokoro being *able* to speak
    and the gate declining to use it.
    """
    dispatcher = tts.PiperTTS(piper_path=str(only_espeak), config=chronoa_config)
    assert dispatcher.kokoro_problem() == "", (
        "this test is about opting out of a voice that works; if Kokoro cannot "
        "speak here, it proves nothing"
    )

    assert dispatcher.engine(SENTENCE) == "espeak-ng", (
        "Kokoro was made the default without being benchmarked: at RTF 1.22 it "
        "cannot finish before playback would start, so every reply opened with "
        "seconds of silence"
    )

    chronoa_config.set(tts.PiperTTS.KOKORO_KEY, "true")

    assert dispatcher.engine(SENTENCE) == "kokoro", (
        "turning the setting on must restore exactly the behaviour it had when "
        "it was the default - the user has to be able to choose the voice"
    )


def test_the_kokoro_setting_is_off_in_a_fresh_profile(chronoa_config):
    """The default, read through the dispatcher's own reader.

    A schema default of `true` would be invisible here and obvious everywhere
    else: this is the state every new install starts in.
    """
    assert chronoa_config.get_bool(tts.PiperTTS.KOKORO_KEY, True) is False
    assert tts.PiperTTS(config=chronoa_config)._kokoro_enabled() is False


def test_skipping_kokoro_is_announced_with_the_reason(kokoro_installed, only_espeak,
                                                      chronoa_config, caplog, monkeypatch):
    """Which engine, and why not the other one.

    The reason has to name the switch: a user who cannot tell why their
    assistant sounds like espeak-ng has no way to ask, and a reason naming
    python-onnxruntime-cpu would send them to install a 100 MB runtime for a
    voice they never asked for.
    """
    monkeypatch.setattr(tts, "_ANNOUNCED", set())
    dispatcher = tts.PiperTTS(piper_path=str(only_espeak), config=chronoa_config)

    with caplog.at_level("INFO", logger="shani_chronoa.tts"):
        dispatcher.engine(SENTENCE)

    said = "\n".join(record.getMessage() for record in caplog.records)
    assert "espeak-ng" in said, said
    assert tts.PiperTTS.KOKORO_KEY in said, said
    assert "onnxruntime" not in said, (
        f"with the switch off, the runtime is not the reason and naming it "
        f"sends the user after the wrong package: {said}"
    )


# --- 2. the timbre pass ------------------------------------------------------

def test_the_defaults_are_a_no_op_and_not_a_re_encode(tmp_path, chronoa_config):
    """pitch=0, tempo=1.0, rate=1.0 must not touch the file at all.

    Asserted on the bytes *and* on the binary never being run, because the two
    failures are different: a re-encode leaves audio that sounds the same and
    costs a subprocess on every reply, while a silent transform leaves audio
    that sounds wrong and looks fine.
    """
    binary, log = _fake_sox(tmp_path, "sys.exit(0)")
    monkey_path = f"{tmp_path / 'bin'}:{os.environ['PATH']}"
    old_path, os.environ["PATH"] = os.environ["PATH"], monkey_path
    try:
        dispatcher = tts.PiperTTS(config=chronoa_config)
        path = _wav(str(tmp_path / "plain.wav"))
        before = open(path, "rb").read()

        assert dispatcher.timbre_effects() == []
        assert dispatcher.apply_timbre(path) == ""
    finally:
        os.environ["PATH"] = old_path

    assert open(path, "rb").read() == before, "the defaults re-encoded the audio"
    assert not log.exists(), f"sox was run for a transform that does nothing: {log.read_text()}"
    assert os.path.exists(binary)


def test_a_missing_sox_leaves_the_audio_alone_and_says_which_transform_it_skipped(
        tmp_path, chronoa_config, monkeypatch, caplog):
    """The honest degradation, on the machine most users of a fresh install are on.

    Three claims, and each has failed in this repo's history in a different
    form: the audio is byte-for-byte the engine's, the reply still plays, and
    the reason names *both* the transform and the package that would fix it.
    """
    monkeypatch.setattr(tts.PiperTTS, "sox_path", staticmethod(lambda: ""))
    chronoa_config.set("tts-pitch", "3.0")
    dispatcher = tts.PiperTTS(config=chronoa_config)
    path = _wav(str(tmp_path / "untransformed.wav"))
    before = open(path, "rb").read()

    assert dispatcher.timbre_problem(), (
        "sox is missing and a transform is set; the settings window reads this "
        "to tell the user the row will not do anything"
    )

    with caplog.at_level("WARNING", logger="shani_chronoa.tts"):
        reason = dispatcher.apply_timbre(path)

    assert open(path, "rb").read() == before, "a missing sox rewrote the audio"
    assert "pitch" in reason and "sox" in reason, (
        f"the reason must name the transform that was skipped and the package "
        f"that fixes it, not just report a failure: {reason!r}"
    )
    assert not os.path.exists(path + ".sox.wav"), "a staged file was left behind"

    # And through the real synthesise path, where the warning is emitted and the
    # reply must still be a success - a skipped preference is not a lost reply.
    assert dispatcher.synthesize.__doc__, "synthesize lost its docstring"
    out = str(tmp_path / "reply.wav")
    assert _synthesize_with(tmp_path, dispatcher, out, SENTENCE, caplog) is True


def test_a_transform_that_is_set_reaches_the_samples(tmp_path, chronoa_config, monkeypatch):
    """The chain is really run, with the effect arguments SoX documents.

    The fake halves the frames, so the assertion is on samples and duration
    rather than on "sox was called": a transform that reports success while
    handing the audio back untouched would pass a call-count assertion.
    """
    _binary, log = _fake_sox(tmp_path, textwrap.dedent("""\
        import wave
        source, target = argv[0], argv[1]
        print(" ".join(argv), file=sys.stderr)
        with wave.open(source) as handle:
            frames, rate = handle.getnframes(), handle.getframerate()
            raw = handle.readframes(frames)
        with wave.open(target, "wb") as out:
            out.setnchannels(1); out.setsampwidth(2); out.setframerate(rate)
            out.writeframes(raw[:len(raw) // 2])
    """))
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:{os.environ['PATH']}")
    chronoa_config.set("tts-tempo", "1.3")
    dispatcher = tts.PiperTTS(config=chronoa_config)
    path = _wav(str(tmp_path / "speeded.wav"))
    before = open(path, "rb").read()

    reason = dispatcher.apply_timbre(path)

    assert reason == "", f"the transform was skipped: {reason}"
    after = open(path, "rb").read()
    assert after != before, "the samples were not changed"
    assert _frames(path) * 2 == 22050, (
        f"the fake halves the frames; got {_frames(path)} of 22050, so what "
        f"was written back was not the transformed audio"
    )
    assert not os.path.exists(path + ".sox.wav")


def test_pitch_is_handed_to_sox_in_cents_and_rate_as_speed(chronoa_config):
    """The two unit mistakes this control exists for.

    SoX's `pitch` takes *cents* (`sox --help-effect pitch` prints
    `pitch [-q] shift-in-cents`), so sending semitones is 100x too large; and
    SoX's `rate` effect takes an absolute sample rate, so using it for a
    multiplier would make this control a second copy of the pitch row rather
    than the speed-and-pitch-together row it is.
    """
    dispatcher = tts.PiperTTS(config=chronoa_config)

    chronoa_config.set("tts-pitch", "3.0")
    assert dispatcher.timbre_effects() == ["pitch", "300"]

    chronoa_config.set("tts-pitch", "0.0")
    chronoa_config.set("tts-tempo", "1.4")
    chronoa_config.set("tts-rate", "0.8")
    effects = dispatcher.timbre_effects()
    assert effects == ["tempo", "1.400", "speed", "0.800"], effects
    assert "rate" not in effects, (
        "SoX's rate effect takes an absolute sample rate and preserves the "
        f"duration, which would duplicate the pitch row: {effects}"
    )


def test_sox_failing_leaves_the_engines_own_audio(tmp_path, chronoa_config, monkeypatch):
    """A half-finished transform is worse than none.

    SoX writes to a sibling and is moved over the original only once it has
    succeeded, so a failure halfway leaves playable audio and nothing to clean
    up - a partially rewritten WAV is neither playable nor original.
    """
    _fake_sox(tmp_path, "sys.stderr.write('sox FAIL formats: bad\\n'); sys.exit(2)")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:{os.environ['PATH']}")
    chronoa_config.set("tts-pitch", "2.0")
    dispatcher = tts.PiperTTS(config=chronoa_config)
    path = _wav(str(tmp_path / "failed.wav"))
    before = open(path, "rb").read()

    reason = dispatcher.apply_timbre(path)

    assert open(path, "rb").read() == before
    assert "bad" in reason, f"sox's own complaint is the useful part: {reason!r}"
    assert not os.path.exists(path + ".sox.wav")


@pytest.mark.skipif(not tts.PiperTTS.sox_path(),
                    reason="sox is not installed; the real-effect checks below need it")
def test_the_real_sox_shortens_a_reply_by_the_tempo_factor(tmp_path, chronoa_config,
                                                           monkeypatch):
    """The measurement from `AGENTS.md`, asserted: 1.3x tempo, 1/1.3 as long."""
    monkeypatch.setenv("PATH", os.environ["PATH"])
    chronoa_config.set("tts-tempo", "1.3")
    dispatcher = tts.PiperTTS(config=chronoa_config)
    path = _wav(str(tmp_path / "real.wav"), seconds=2.0)

    assert dispatcher.apply_timbre(path) == ""
    assert _frames(path) == pytest.approx(22050 * 2.0 / 1.3, rel=0.02)


def _synthesize_with(tmp_path, dispatcher, out, text, caplog):
    """`dispatcher.synthesize()` with espeak-ng faked, for the missing-sox case.

    The real espeak-ng is not required and the real one is not used: what is
    under test is what `synthesize` does with a skipped transform, and the WAV
    it has to leave alone has to be one whose bytes this file chose.
    """
    import shani_chronoa.tts as module

    written = {}

    def fake_run(cmd, **kwargs):
        written["cmd"] = cmd
        _wav(cmd[cmd.index("-w") + 1], seconds=0.5)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    real = module.subprocess.run
    module.subprocess.run = fake_run
    real_engine = dispatcher.engine
    dispatcher.engine = lambda *a, **kw: "espeak-ng"
    try:
        with caplog.at_level("WARNING", logger="shani_chronoa.tts"):
            return dispatcher.synthesize(text, out)
    finally:
        module.subprocess.run = real
        dispatcher.engine = real_engine


# --- 3. the settings rows ----------------------------------------------------

_HARNESS = textwrap.dedent(
    """
    import json
    import gi
    gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
    from gi.repository import Gtk, Adw, GLib
    from shani_chronoa.config import ChronoaConfig
    from shani_chronoa import tts

    out = {}

    def walk(node):
        found = []
        child = node.get_first_child()
        while child:
            found.extend(walk(child))
            found.append(child)
            child = child.get_next_sibling()
        return found

    class App(Gtk.Application):
        def __init__(s):
            super().__init__(application_id="test.surface.voice")
            s.config = ChronoaConfig()
            s.window = None
            s._wake_word_active = False
            # A real dispatcher, so the window's `_set_timbre` has something to
            # hand its config to. Without one the row would save the value and
            # the running app would keep speaking as if nothing changed.
            s.tts = tts.PiperTTS()
        def activate_action(s, name, arg): pass

    app = App()

    def on_activate(a):
        from shani_chronoa.settings_window import SettingsWindow
        window = SettingsWindow(a)
        rows = {r.get_title(): r for r in walk(window.get_child())
                if isinstance(r, Adw.PreferencesRow)}
        groups = [g.get_title() for g in walk(window.get_child())
                  if isinstance(g, Adw.PreferencesGroup)]

        out["groups"] = groups
        out["kokoro_row"] = any("Kokoro" in t for t in rows)
        out["kokoro_row_kind"] = type(rows.get("Use the Kokoro neural voice")).__name__
        out["pitch_row_kind"] = type(rows.get("Pitch (semitones)")).__name__
        out["rows"] = sorted(t for t in rows if t in (
            "Speaking with", "Use the Kokoro neural voice", "Pitch (semitones)",
            "Tempo", "Rate (speed and pitch together)"))

        # Flip the switch the way a person does: through the row's own signal.
        switch = rows["Use the Kokoro neural voice"]
        out["kokoro_before"] = a.config.get_bool(tts.PiperTTS.KOKORO_KEY, True)
        switch.set_active(not out["kokoro_before"])
        out["kokoro_after"] = a.config.get_bool(tts.PiperTTS.KOKORO_KEY, True)
        out["kokoro_switch_state"] = switch.get_active()
        # Read the gate through the *live* dispatcher, not through a fresh
        # config: a switch that writes the setting and leaves the running app
        # reading its own copy is the defect, and only this answers it.
        out["enabled_after_flip"] = a.tts._kokoro_enabled()
        out["kokoro_problem"] = a.tts.kokoro_problem()
        out["engine_after_flip"] = a.tts.engine("Sure, I set a timer for five minutes.")

        # And move the pitch row, then read the setting back *through the
        # dispatcher's own reader*, which is the half that would drift.
        pitch = rows["Pitch (semitones)"]
        pitch.set_value(3.0)
        out["pitch_stored"] = a.config.get_double("tts-pitch", 0.0)
        out["pitch_seen_by_dispatcher"] = a.tts.timbre()["pitch"]
        out["effects"] = a.tts.timbre_effects()
        out["pitch_subtitle"] = pitch.get_subtitle()
        out["live_subtitle"] = rows["Tempo"].get_subtitle()

        # And back to the identity, which must leave the audio alone.
        pitch.set_value(0.0)
        out["effects_back_to_zero"] = a.tts.timbre_effects()
        a.quit()

    app.connect("activate", on_activate)
    GLib.timeout_add(20000, lambda: (app.quit(), False)[1])
    app.run([])
    print("RESULT" + json.dumps(out))
    """
)


@pytest.fixture(scope="module")
def voice_window(tmp_path_factory, compiled_schema_dir):
    work = tmp_path_factory.mktemp("voice-surface")
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
    proc = subprocess.run([sys.executable, "-c", _HARNESS], capture_output=True,
                          text=True, timeout=180, env=env, cwd=str(work))
    payload = None
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT"):
            payload = json.loads(line[len("RESULT"):])
    assert payload is not None, f"no result:\n{proc.stdout}\n{proc.stderr[-2000:]}"
    payload["_stderr"] = proc.stderr
    return payload


class TestTheVoiceRowsAreRealControls:
    def test_the_group_exists(self, voice_window):
        assert "Voice output" in voice_window["groups"], voice_window["groups"]

    def test_all_four_controls_are_present(self, voice_window):
        assert voice_window["rows"] == [
            "Pitch (semitones)", "Rate (speed and pitch together)",
            "Speaking with", "Tempo", "Use the Kokoro neural voice",
        ], voice_window["rows"]

    def test_the_switch_is_a_switch_and_the_sliders_are_sliders(self, voice_window):
        assert voice_window["kokoro_row_kind"] == "SwitchRow"
        assert voice_window["pitch_row_kind"] == "SpinRow"

    def test_flipping_the_switch_writes_the_setting(self, voice_window):
        assert voice_window["kokoro_before"] is False, (
            "the harness must start from the default, or this proves nothing"
        )
        assert voice_window["kokoro_after"] is True, (
            "the row did not write the setting; a switch that only draws itself "
            "is the defect this whole file is about elsewhere"
        )
        assert voice_window["kokoro_switch_state"] is True

    def test_the_running_dispatcher_reads_the_flip_without_a_restart(self, voice_window):
        """The round trip: row -> the app's config -> the object that speaks.

        `enabled_after_flip` is asked of the live dispatcher rather than of a
        freshly built one. Two `ChronoaConfig` instances do not see each other's
        writes, so a dispatcher that kept its own would answer False here while
        the switch beside it showed on - and every reply would keep using
        espeak-ng with nothing to show for it.
        """
        assert voice_window["enabled_after_flip"] is True, (
            "the setting was written but the running dispatcher still reads the "
            "old value, so the switch would only take effect after a restart"
        )

    def test_the_engine_answers_consistently_with_that_switch(self, voice_window):
        """Kokoro if it can speak, espeak-ng if it cannot - and it says which.

        This harness is hermetic, so the Kokoro model is normally absent and the
        floor is the right answer; the assertion is on the *consistency*, which
        is what would break if the gate and the announcement disagreed.
        """
        if voice_window["kokoro_problem"] == "":
            assert voice_window["engine_after_flip"] == "kokoro"
        else:
            assert voice_window["engine_after_flip"] == "espeak-ng", (
                "with the switch on and Kokoro unable to speak, the floor is the "
                "only correct answer: "
                f"{voice_window['kokoro_problem']}"
            )

    def test_moving_a_slider_writes_it_and_the_running_dispatcher_sees_it(self, voice_window):
        assert voice_window["pitch_stored"] == 3.0
        assert voice_window["pitch_seen_by_dispatcher"] == 3.0, (
            "the window saved the value but the app's own dispatcher still "
            "reads its own copy: two ChronoaConfig instances do not see each "
            "other's writes, so the slider would need a restart"
        )
        assert voice_window["effects"] == ["pitch", "300"]

    def test_back_to_zero_is_a_no_op_again(self, voice_window):
        assert voice_window["effects_back_to_zero"] == []

    def test_a_missing_sox_is_stated_on_the_row_that_would_not_work(self, voice_window,
                                                                     monkeypatch):
        """The harness above runs with sox present or absent, whatever PATH holds.

        Whichever it is, the subtitle must not claim a transform that will not
        happen: with sox absent and a value set, it has to say so.
        """
        subtitle = voice_window["pitch_subtitle"]
        if not tts.PiperTTS.sox_path():
            assert "Not applied" in subtitle, subtitle
            assert "sox" in subtitle, subtitle
        else:
            assert "Not applied" not in subtitle, subtitle