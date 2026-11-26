"""Voice engine rows: every probe stands in, so no machine hardware is needed."""

import sys
from types import SimpleNamespace

from shani_chronoa import cloud_voice
from shani_chronoa.engine_ops import voice


class FakeTTS:
    def __init__(self, path):
        self.piper_path = path
        self.voice_path = ""

    def engine(self):
        return "espeak-ng"

    def kokoro_problem(self):
        return ""

    @staticmethod
    def _rhvoice_has_voice():
        return False


class FakeSTT:
    model = "base"

    def __init__(self, model=""):
        self.model = model

    def is_available(self):
        return True


class FakeAudio:
    def is_available(self):
        return True


def _devices():
    return [
        SimpleNamespace(
            name="hyperx", label="Headset", description="USB headset", kind="input"
        )
    ]


def test_engines_returns_stable_shapes_with_stand_ins(tmp_path, monkeypatch):
    binary = tmp_path / "piper-tts"
    binary.write_text("fake", encoding="utf-8")
    monkeypatch.setattr(voice._tts, "PiperTTS", lambda: FakeTTS(str(binary)))
    monkeypatch.setattr(voice._stt, "installed_model", lambda _p: "base")
    monkeypatch.setattr(voice._stt, "WhisperSTT", FakeSTT)
    monkeypatch.setattr(voice._audio, "AudioRecorder", FakeAudio)
    monkeypatch.setattr(voice._audio, "AudioPlayer", FakeAudio)
    monkeypatch.setattr(voice._pipewire, "list_inputs", _devices)
    monkeypatch.setattr(voice._pipewire, "list_outputs", lambda: [])
    monkeypatch.setattr(voice, "_cloud", lambda _c: (False, "cloud off"))
    monkeypatch.setattr(
        voice,
        "_catalogue_rows",
        lambda: [
            {
                "key": "base",
                "label": "Whisper base",
                "detail": "142 MB",
                "engine": "whisper-cli",
            }
        ],
    )
    monkeypatch.setattr(
        voice.shutil,
        "which",
        lambda name: "/usr/bin/espeak-ng" if name == "espeak-ng" else None,
    )

    result = voice.engines(config=object())

    assert set(result) == {"engines", "devices", "catalogue"}
    assert all(set(row) == {"name", "installed", "detail"} for row in result["engines"])
    by_name = {row["name"]: row for row in result["engines"]}
    assert by_name["piper"]["installed"] is True
    assert by_name["kokoro"]["installed"] is True
    assert by_name["whisper"]["installed"] is True
    assert by_name["rhvoice"]["installed"] is False
    assert "would speak" in by_name["espeak-ng"]["detail"]
    assert result["devices"] == {
        "input": [
            {
                "name": "hyperx",
                "label": "Headset",
                "description": "USB headset",
                "kind": "input",
            }
        ],
        "output": [],
    }
    assert result["catalogue"][0]["engine"] == "whisper-cli"


def test_missing_binaries_and_failing_probes_are_not_installed_rows(monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("no binary here")

    monkeypatch.setattr(voice._tts, "PiperTTS", broken)
    monkeypatch.setattr(voice._stt, "WhisperSTT", broken)
    monkeypatch.setattr(voice._audio, "AudioRecorder", broken)
    monkeypatch.setattr(voice._audio, "AudioPlayer", broken)
    monkeypatch.setattr(voice, "_cloud", broken)
    monkeypatch.setattr(voice, "_catalogue_rows", broken)
    monkeypatch.setattr(voice._pipewire, "list_inputs", broken)
    monkeypatch.setattr(voice._pipewire, "list_outputs", broken)
    monkeypatch.setattr(voice.shutil, "which", lambda _name: None)

    result = voice.engines()

    assert result["engines"]
    assert all(row["installed"] is False for row in result["engines"])
    assert all(row["detail"].startswith("not installed") for row in result["engines"])
    assert result["devices"] == {"input": [], "output": []}
    assert result["catalogue"] == []


def test_stt_catalogue_is_read_lazily_from_the_setup_wizard(monkeypatch):
    fake = SimpleNamespace(
        stt_catalogue=lambda: [
            ("base", "Whisper base", "142 MB", "whisper-cli"),
            ("parakeet:en", "Parakeet (en)", "15 MB", "parakeet-cli"),
        ]
    )
    monkeypatch.setitem(sys.modules, "shani_chronoa.setup_wizard", fake)

    rows = voice._catalogue_rows()

    assert rows == [
        {
            "key": "base",
            "label": "Whisper base",
            "detail": "142 MB",
            "engine": "whisper-cli",
        },
        {
            "key": "parakeet:en",
            "label": "Parakeet (en)",
            "detail": "15 MB",
            "engine": "parakeet-cli",
        },
    ]


def test_cloud_probe_uses_the_handed_config(monkeypatch):
    seen = {}

    class FakeCloud:
        def __init__(self, config=None):
            seen["config"] = config

        def refusal(self):
            return "privacy mode is on"

    monkeypatch.setattr(cloud_voice, "CloudTTS", FakeCloud)
    marker = object()

    assert voice._cloud(marker) == (False, "not in use - privacy mode is on")
    assert seen["config"] is marker
