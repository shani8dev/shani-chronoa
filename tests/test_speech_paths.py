"""STT/TTS find the binaries Arch/Shanios really ships."""

import os


def _exe(d, name):
    p = d / name
    p.write_text("#!/bin/sh\n")
    p.chmod(0o755)
    return p


def test_stt_uses_whisper_cli_and_ggml_model(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"; bindir.mkdir()
    cli = _exe(bindir, "whisper-cli")
    monkeypatch.setenv("PATH", str(bindir))
    models = tmp_path / "home" / ".local/share/whisper/models"
    models.mkdir(parents=True)
    (models / "ggml-base.bin").write_bytes(b"x")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    from shani_chronoa.stt import WhisperSTT
    s = WhisperSTT(model="base")
    assert s.whisper_path == str(cli)
    assert s.model_path == str(models / "ggml-base.bin")


def test_tts_never_runs_the_mouse_configurator(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"; bindir.mkdir()
    _exe(bindir, "piper")  # extra/piper: the GTK gaming-mouse app
    monkeypatch.setenv("PATH", str(bindir))
    from shani_chronoa.tts import PiperTTS
    assert os.path.basename(PiperTTS().piper_path) == "piper-tts"
    tts = _exe(bindir, "piper-tts")
    assert PiperTTS().piper_path == str(tts)


def test_tts_falls_back_to_espeak_and_really_speaks(tmp_path, monkeypatch):
    """Without Piper the image's espeak-ng is used; if it is installed here,
    synthesize a real WAV."""
    import shutil
    from shani_chronoa.tts import PiperTTS
    t = PiperTTS(piper_path=str(tmp_path / "no-piper"))
    monkeypatch.setattr(PiperTTS, "_rhvoice_has_voice", staticmethod(lambda: False))
    if not shutil.which("espeak-ng"):
        import pytest
        pytest.skip("espeak-ng not installed")
    assert t.engine() == "espeak-ng" and t.is_available()
    wav = t.synthesize_to_bytes("Hello from Shanios")
    assert wav[:4] == b"RIFF" and len(wav) > 1000
