"""A packaged voice is a voice the person has.

`shani-piper-voice` ships `en_US-lessac-medium` to /usr/share/piper/voices -
the directory `tts.PiperTTS` already reads. Speech works offline, but the
setup wizard's readiness check (`voices.voice_installed`) looked only at the
user's own directory, so first boot still offered - and performed - a
redundant 63 MB download. Same class of defect as the whisper model path
(`stt.py` checks the system dir, and `stt_provision.model_path` prefers the
user copy but answers with the system one): the reader and the checker must
agree about where a packaged thing lives.
"""

from pathlib import Path

from shani_chronoa import voices


def _plant(root: Path, voice: str):
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{voice}.onnx").write_bytes(b"voice")
    (root / f"{voice}.onnx.json").write_bytes(b"{}")


def test_a_packaged_voice_counts_as_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(voices, "SYSTEM_VOICE_DIR", tmp_path / "sys")
    _plant(tmp_path / "sys", "en_US-lessac-medium")
    assert voices.voice_installed("en_US-lessac-medium") is True


def test_a_voice_with_only_half_its_files_does_not_count(tmp_path, monkeypatch):
    system = tmp_path / "sys"
    system.mkdir()
    (system / "en_US-lessac-medium.onnx").write_bytes(b"voice")  # no .onnx.json
    monkeypatch.setattr(voices, "SYSTEM_VOICE_DIR", system)
    assert voices.voice_installed("en_US-lessac-medium") is False


def test_install_voice_never_downloads_a_packaged_voice(tmp_path, monkeypatch):
    """`install_voice` used to pass `existing=<user path>`, so a packaged
    voice was re-downloaded. It must resolve user-or-system, as whisper does.
    """
    monkeypatch.setattr(voices, "SYSTEM_VOICE_DIR", tmp_path / "sys")
    _plant(tmp_path / "sys", "en_US-lessac-medium")
    seen = []
    monkeypatch.setattr(voices, "_install",
                        lambda spec, **kw: seen.append(kw["existing"]) or kw["existing"])
    voices.install_voice("en_US-lessac-medium")
    assert len(seen) == 2, "both files must be resolved before any fetch"
    assert all(e is not None and e.parent == tmp_path / "sys" for e in seen), seen


def test_reads_counts_packaged_tessdata_and_speaks_the_packaged_voice(tmp_path, monkeypatch):
    from shani_chronoa import languages
    system = tmp_path / "sys-tessdata"
    system.mkdir()
    (system / "hin.traineddata").write_bytes(b"ocr")
    monkeypatch.setattr(languages, "SYSTEM_TESSDATA", system)
    assert languages.reads("hi") is True
    assert languages.reads("mr") is False


def test_speaks_counts_the_packaged_voice(tmp_path, monkeypatch):
    from shani_chronoa import languages, voices
    monkeypatch.setattr(voices, "SYSTEM_VOICE_DIR", tmp_path / "sys-voices")
    _plant(tmp_path / "sys-voices", "hi_IN-priyamvada-medium")
    assert languages.speaks("hi") is True


def test_sanskrit_falls_back_to_a_voice_espeak_ng_has():
    """`espeak-ng -v sa` exits 1, so the reply would go unspoken."""
    from shani_chronoa import languages

    class Config:
        def get(self, key, default=""):
            return "sa" if key == languages.SETTING else default

    assert languages.espeak_language("संस्कृतम्", Config()) == "hi"


def test_a_language_espeak_ng_has_is_left_alone():
    from shani_chronoa import languages

    class Config:
        def get(self, key, default=""):
            return "mr" if key == languages.SETTING else default

    assert languages.espeak_language("मराठी", Config()) == "mr"
