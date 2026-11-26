"""Health engine: audit rows and bounded probes, all with stand-ins."""

import json
from pathlib import Path
from types import SimpleNamespace

from shani_chronoa import (
    capabilities,
    config as config_mod,
    local_llm,
    stt,
    tool_tracking,
    tts,
)
from shani_chronoa.engine_ops import health


def _record(name, when, origin="user"):
    return {
        "timestamp": when,
        "tool_name": name,
        "args": {"api-key": "must-never-be-returned"},
        "result": "done",
        "duration_ms": 12,
        "origin": origin,
        "verdict": "verified",
        "evidence": "post-condition held",
        "undone": False,
    }


def test_activity_merges_durable_and_live_calls_without_secrets(tmp_path, monkeypatch):
    log = tmp_path / "tool_calls.log"
    old = _record("get_volume", "2026-11-26T10:00:00+00:00")
    mid = _record("set_timer", "2026-11-26T11:00:00+00:00")
    log.write_text(
        json.dumps(old) + "\n" + json.dumps(mid) + "\nnot json\n", encoding="utf-8"
    )
    monkeypatch.setattr(tool_tracking, "log_files", lambda: [log])

    class FakeTracker:
        def get_calls(self):
            # The live copy of a durable call, plus one newer call.
            return [
                SimpleNamespace(**old),
                SimpleNamespace(**_record("notify", "2026-11-26T12:00:00+00:00")),
            ]

    monkeypatch.setattr(tool_tracking, "ToolTracker", FakeTracker)

    result = health.activity()

    assert result["truncated"] is False
    assert [row["tool_name"] for row in result["calls"]] == [
        "notify",
        "set_timer",
        "get_volume",
    ]
    assert set(result["calls"][0]) == {
        "timestamp",
        "tool_name",
        "duration_ms",
        "origin",
        "verdict",
        "evidence",
        "undone",
    }
    assert "api-key" not in json.dumps(result)
    assert "must-never-be-returned" not in json.dumps(result)


def test_activity_since_and_limit_report_truncation(monkeypatch):
    records = [
        _record("first", "2026-11-26T11:00:00+00:00"),
        _record("second", "2026-11-26T12:00:00+00:00"),
    ]
    monkeypatch.setattr(tool_tracking, "log_files", lambda: [])
    monkeypatch.setattr(
        tool_tracking, "ToolTracker", lambda: SimpleNamespace(get_calls=lambda: records)
    )

    result = health.activity(since="2026-11-26T10:30:00+00:00", limit=1)

    assert [row["tool_name"] for row in result["calls"]] == ["second"]
    assert result["truncated"] is True


def test_activity_unreadable_log_is_skipped(monkeypatch, tmp_path):
    missing = tmp_path / "gone.log"
    monkeypatch.setattr(tool_tracking, "log_files", lambda: [missing])
    monkeypatch.setattr(
        tool_tracking, "ToolTracker", lambda: SimpleNamespace(get_calls=lambda: [])
    )

    assert health.activity() == {"calls": [], "truncated": False}


def test_probe_uses_each_engine_stand_in(monkeypatch):
    monkeypatch.setattr(local_llm, "installed", lambda: ["1.7B"])
    monkeypatch.setattr(stt, "installed_model", lambda _p: "base")

    class FakeSTT:
        def __init__(self, model=""):
            self.model = model

        def is_available(self):
            return True

    monkeypatch.setattr(stt, "WhisperSTT", FakeSTT)
    monkeypatch.setattr(
        tts, "PiperTTS", lambda: SimpleNamespace(engine=lambda: "espeak-ng")
    )
    monkeypatch.setattr(
        health.shutil,
        "which",
        lambda name: (
            "/bin/" + name if name in ("pw-record", "aplay", "espeak-ng") else None
        ),
    )
    monkeypatch.setattr(
        config_mod,
        "ChronoaConfig",
        lambda: SimpleNamespace(setting_readable=lambda _key: True),
    )
    monkeypatch.setattr(
        capabilities, "live_capabilities", lambda: ([object(), object()], "")
    )

    result = health.probe()

    checks = {row["title"]: row for row in result["checks"]}
    assert all(set(row) == {"title", "status", "detail"} for row in result["checks"])
    assert all(
        row["status"] in ("working", "not working", "could not determine")
        for row in result["checks"]
    )
    assert checks["Local model"]["status"] == "working"
    assert checks["Speech in"]["status"] == "working"
    assert checks["Speech out"]["status"] == "working"
    assert checks["Audio capture"]["status"] == "working"
    assert checks["Audio playback"]["status"] == "working"
    assert checks["Speech floor (espeak-ng)"]["status"] == "working"
    assert checks["Settings store"]["status"] == "working"
    assert checks["Capabilities"]["status"] == "working"


def test_probe_names_missing_binaries_and_demotes_a_raising_probe(monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(local_llm, "installed", lambda: [])
    monkeypatch.setattr(stt, "installed_model", broken)
    monkeypatch.setattr(tts, "PiperTTS", broken)
    monkeypatch.setattr(health.shutil, "which", lambda _name: None)
    monkeypatch.setattr(config_mod, "ChronoaConfig", broken)
    monkeypatch.setattr(capabilities, "live_capabilities", broken)

    checks = {row["title"]: row for row in health.probe()["checks"]}

    assert checks["Local model"]["status"] == "not working"
    assert checks["Speech in"]["status"] == "could not determine"
    assert "probe exploded" in checks["Speech in"]["detail"]
    assert checks["Audio capture"]["status"] == "not working"
    assert "pw-record" in checks["Audio capture"]["detail"]
    assert checks["Capabilities"]["status"] == "could not determine"


def test_probe_never_imports_gtk_or_runs_a_subprocess():
    package = Path(health.__file__).parent
    source = "\n".join(
        (package / name).read_text(encoding="utf-8")
        for name in ("automation.py", "voice.py", "health.py")
    )

    assert "import gi" not in source
    assert "Gtk" not in source
    assert "import subprocess" not in source
    assert "subprocess.run" not in source
    assert "subprocess.Popen" not in source
