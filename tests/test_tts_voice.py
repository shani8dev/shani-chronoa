"""Chronoa speaks with a woman's voice in every engine.

Piper's default (en_US-lessac) and RHVoice's SLT are female; espeak-ng's
default for a language is male - measured 99 Hz median pitch for en-us
against 200 Hz for en-us+f3 - and espeak-ng is the one engine every Shanios
image ships.
"""

import subprocess

from shani_chronoa import tts as tts_mod


def _argv(monkeypatch, engine, slt=False):
    seen = {}
    t = tts_mod.PiperTTS()
    # `*args, **kw` rather than a zero-argument lambda: `_synthesize` calls
    # `engine(text)` (it has to, since Kokoro is skipped per-text for a script
    # it has no voice for), and a stub with a narrower signature would turn that
    # into a TypeError in every test here instead of the assertion this helper
    # exists to make.
    monkeypatch.setattr(t, "engine", lambda *args, **kw: engine)
    monkeypatch.setattr(tts_mod.PiperTTS, "_rhvoice_voice_installed", staticmethod(lambda name: slt and name == "slt"))

    def run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(tts_mod.subprocess, "run", run)
    t.synthesize("hello", "/tmp/x.wav")
    return seen.get("cmd", [])


def test_espeak_uses_its_female_variant(monkeypatch):
    cmd = _argv(monkeypatch, "espeak-ng")
    assert cmd[cmd.index("-v") + 1] == "en-us+f3"


def test_rhvoice_picks_slt_when_it_is_installed(monkeypatch):
    cmd = _argv(monkeypatch, "rhvoice", slt=True)
    assert cmd[cmd.index("-p") + 1] == "slt"


def test_rhvoice_without_slt_is_left_to_its_default(monkeypatch):
    assert "-p" not in _argv(monkeypatch, "rhvoice", slt=False)


def test_piper_default_is_the_female_lessac_voice():
    assert tts_mod.PiperTTS().voice == "en_US-lessac-medium"
