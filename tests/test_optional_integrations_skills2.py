"""Focused tests for the three opt-in features on the chronoa-skills2 branch.

No speaker model, backend binary, phone or second machine is needed here. What
is verified is the part that is easy to get wrong without hardware: consent,
where the bytes go, the argv that would run, and that a file somebody brings
cannot overwrite what is already here. The real binary paths stay honest - a
missing tool reports that it is missing rather than claiming a result.
"""

from __future__ import annotations

import ast
import json
import os
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from shani_chronoa import capabilities, files
from shani_chronoa.senses import store as percept_store
from shani_chronoa.skills import (
    adb_workflow,
    backup_adapters,
    barcode,
    device_sync,
    discover_skills,
)
from shani_chronoa.skills import speaker_id as sid


def _yes(monkeypatch, module):
    monkeypatch.setattr(module.approvals, "confirm", lambda *a, **k: (True, ""))


def _no(monkeypatch, module):
    monkeypatch.setattr(
        module.approvals, "confirm", lambda *a, **k: (False, "the person said no")
    )


def _config(monkeypatch, module, **values):
    class Config:
        def __init__(self, *a, **k):
            pass

        def get_bool(self, key, default=False):
            return bool(values.get(key, default))

        def get(self, key, default=""):
            return values.get(key, default)

        def set(self, key, value):
            values[key] = value

    monkeypatch.setattr(module, "ChronoaConfig", Config)
    return values


def _wav(path: Path, seconds: float = 1.5) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * int(16000 * seconds))
    return path


def _data_home(monkeypatch, tmp_path: Path) -> Path:
    """The suite's own HOME; every path helper confines user files to it."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return home


# ── speaker identification ────────────────────────────────────────────────────


class TestSpeakerConsent:
    def test_a_voice_is_off_until_the_hearing_consent_is_on(
        self, monkeypatch, tmp_path
    ):
        _data_home(monkeypatch, tmp_path)
        _config(monkeypatch, sid, **{"hearing-sense-enabled": False})
        for action in ("enrol", "identify", "list", "forget"):
            answer = sid._run({"action": action})
            assert "Refusing" in answer, answer
            assert "hearing-sense-enabled" not in answer, answer
            assert "Transcribe speech" in answer or "hearing" in answer.lower()

    def test_enrol_needs_a_basis_not_just_a_name(self, monkeypatch, tmp_path):
        _data_home(monkeypatch, tmp_path)
        _config(monkeypatch, sid, **{"hearing-sense-enabled": True})
        _yes(monkeypatch, sid)
        clip = _wav(_data_home(monkeypatch, tmp_path) / "voice.wav")
        answer = sid._run({"action": "enrol", "name": "Asha", "clip": str(clip)})
        assert "basis" in answer.lower() and sid._read_registry() == {}

    def test_no_yes_means_no_clip_is_stored(self, monkeypatch, tmp_path):
        _data_home(monkeypatch, tmp_path)
        _config(monkeypatch, sid, **{"hearing-sense-enabled": True})
        _no(monkeypatch, sid)
        clip = _wav(_data_home(monkeypatch, tmp_path) / "voice.wav")
        answer = sid._run(
            {
                "action": "enrol",
                "name": "Asha",
                "clip": str(clip),
                "basis": "they recorded it themselves",
            }
        )
        assert "Nothing was enrolled" in answer
        assert sid._read_registry() == {}


class TestSpeakerIdentification:
    def _enrol(self, monkeypatch, tmp_path, name="Asha"):
        home = _data_home(monkeypatch, tmp_path)
        _config(monkeypatch, sid, **{"hearing-sense-enabled": True})
        _yes(monkeypatch, sid)
        clip = _wav(_data_home(monkeypatch, tmp_path) / f"{name.lower()}-enrol.wav")
        out = sid._run(
            {
                "action": "enrol",
                "name": name,
                "clip": str(clip),
                "basis": "they said yes and recorded this clip",
            }
        )
        assert "Enrolled" in out, out
        return home, clip

    def test_enrolment_is_private_bound_to_audio_and_forgettable(
        self, monkeypatch, tmp_path
    ):
        home, clip = self._enrol(monkeypatch, tmp_path)
        record = sid._read_registry()["Asha"]
        stored = Path(record["clip"])
        assert stored.is_file() and stored.is_relative_to(files.data_home())
        assert stored.stat().st_mode & 0o077 == 0
        assert record["digest"] == sid._frames_digest(clip)
        assert sid._read_registry()["Asha"]["basis"].startswith("they said yes")
        assert "Forgot Asha" in sid._run({"action": "forget", "name": "Asha"})
        assert not stored.exists() and sid._read_registry() == {}

    def test_identify_uses_the_same_local_diarizer(self, monkeypatch, tmp_path):
        self._enrol(monkeypatch, tmp_path)
        monkeypatch.setattr(sid.speakers, "installed", lambda: True)
        seen = []

        def same(wav, count=None):
            seen.append(count)
            # reference 0..1.5, gap, question 1.75..3.25 - same cluster.
            return [(0.0, 1.5, "speaker_00"), (1.75, 3.25, "speaker_00")]

        monkeypatch.setattr(sid.speakers, "diarize", same)
        question = _wav(_data_home(monkeypatch, tmp_path) / "question.wav")
        out = sid._run({"action": "identify", "clip": str(question), "name": "Asha"})
        assert "matches Asha" in out, out
        assert seen == [2], seen
        assert "not proof of identity" in out

        def different(wav, count=None):
            return [(0.0, 1.5, "speaker_00"), (1.75, 3.25, "speaker_01")]

        monkeypatch.setattr(sid.speakers, "diarize", different)
        out = sid._run({"action": "identify", "clip": str(question), "name": "Asha"})
        assert "does not match Asha" in out, out

    def test_a_changed_stored_clip_is_refused_not_identified_against(
        self, monkeypatch, tmp_path
    ):
        self._enrol(monkeypatch, tmp_path)
        monkeypatch.setattr(sid.speakers, "installed", lambda: True)
        _wav(Path(sid._read_registry()["Asha"]["clip"]), seconds=2.0)
        out = sid._run({"action": "identify", "clip": str(_wav(_data_home(monkeypatch, tmp_path) / "q.wav"))})
        assert "no longer matches" in out, out

    def test_no_network_client_is_imported(self):
        tree = ast.parse(Path(sid.__file__).read_text())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        banned = {
            "httpx",
            "urllib",
            "requests",
            "socket",
            "cloud_llm",
            "websearch",
            "netjson",
        }
        assert not names & banned, (
            f"voice data could leave the machine: {names & banned}"
        )


# ── cross-device sync ─────────────────────────────────────────────────────────


class TestDeviceSync:
    def _stores(self, tmp_path):
        root = tmp_path / "sessions"
        root.mkdir()
        sid = device_sync.conversation_store.new_session(root)
        device_sync.conversation_store.append(
            {"role": "user", "content": "a private question"},
            device_sync.conversation_store.session_path(root, sid),
        )
        store = percept_store.PerceptStore(durable_path=tmp_path / "memory.jsonl")
        return root, store

    def test_export_writes_a_private_file_and_carries_no_voice_or_secrets(
        self, monkeypatch, tmp_path
    ):
        _data_home(monkeypatch, tmp_path)
        _yes(monkeypatch, device_sync)
        _config(monkeypatch, device_sync, language="hi", **{"api-key": "do-not-copy"})
        target = _data_home(monkeypatch, tmp_path) / "carry.json"
        out = device_sync._run({"action": "export", "path": str(target)})
        assert "Exported" in out and target.is_file()
        assert target.stat().st_mode & 0o077 == 0
        raw = json.loads(target.read_text())
        assert "api-key" not in raw["settings"] and "speaker_id" not in raw
        assert "nothing was sent over a network" in out

    def test_export_refuses_a_path_outside_the_home(self, monkeypatch, tmp_path):
        _data_home(monkeypatch, tmp_path)
        _yes(monkeypatch, device_sync)
        out = device_sync._run({"action": "export", "path": "/etc/somewhere.json"})
        assert "outside your home" in out

    def test_preview_writes_nothing_and_import_needs_apply_and_a_yes(
        self, monkeypatch, tmp_path
    ):
        _data_home(monkeypatch, tmp_path)
        bundle = {
            "format": device_sync.FORMAT,
            "version": device_sync.VERSION,
            "settings": {"language": "hi"},
            "conversations": [{"title": "old", "messages": []}],
            "memory": [],
            "exported_at": 1.0,
        }
        device_sync.write_bundle(_data_home(monkeypatch, tmp_path) / "in.json", bundle)
        out = device_sync._run({"action": "preview", "path": str(_data_home(monkeypatch, tmp_path) / "in.json")})
        assert "Preview" in out and "Nothing was written" in out
        out = device_sync._run({"action": "import", "path": str(_data_home(monkeypatch, tmp_path) / "in.json")})
        assert "apply=true" in out
        _no(monkeypatch, device_sync)
        out = device_sync._run(
            {"action": "import", "path": str(_data_home(monkeypatch, tmp_path) / "in.json"), "apply": True}
        )
        assert "Nothing was imported" in out

    def test_import_adds_a_new_session_and_never_duplicates_a_fact(
        self, monkeypatch, tmp_path
    ):
        root, store = self._stores(tmp_path)
        cfg = _config(monkeypatch, device_sync)
        bundle = {
            "format": device_sync.FORMAT,
            "version": device_sync.VERSION,
            "exported_at": 1.0,
            "settings": {"language": "hi"},
            "conversations": [
                {
                    "title": "from elsewhere",
                    "messages": [{"role": "user", "content": "hello there"}],
                }
            ],
            "memory": [
                percept_store._as_dict(
                    percept_store.Percept(
                        sense="memory",
                        kind="fact",
                        content="the meeting is at four",
                        created_at=123.0,
                        ttl_seconds=None,
                        source="test",
                        sensitivity="private",
                    )
                )
            ],
        }
        first = device_sync.apply_bundle(bundle, config=cfg, root=root, store=store)
        assert first == {"conversations": 1, "settings": 1, "memory": 1}, first
        again = device_sync.apply_bundle(bundle, config=cfg, root=root, store=store)
        assert again == {"conversations": 1, "settings": 0, "memory": 0}, again
        assert len(device_sync.conversation_store.list_sessions(root)) == 3
        assert len(store.durable()) == 1

    def test_a_foreign_file_is_refused(self, monkeypatch, tmp_path):
        bad = _data_home(monkeypatch, tmp_path) / "bad.json"
        bad.write_text('{"format": "something-else", "version": 1}')
        with pytest.raises(ValueError, match="not a Chronoa sync file"):
            device_sync.read_bundle(bad)


# ── INT-01 backup adapters ────────────────────────────────────────────────────


class TestBackupAdapters:
    def test_list_reports_the_three_adapters(self, monkeypatch):
        monkeypatch.setattr(
            backup_adapters,
            "installed",
            lambda: {
                "restic": "/usr/bin/restic",
                "borg": "",
                "rsync": "/usr/bin/rsync",
            },
        )
        out = backup_adapters._run({"action": "list"})
        assert "restic: /usr/bin/restic" in out and "borg: not installed" in out

    def test_plan_asks_first_and_never_runs_anything(self, monkeypatch, tmp_path):
        _yes(monkeypatch, backup_adapters)
        monkeypatch.setattr(
            backup_adapters.shutil,
            "which",
            lambda name: f"/usr/bin/{name}" if name == "restic" else None,
        )
        calls = []

        def no_run(*a, **k):
            calls.append((a, k))
            raise AssertionError("a plan must not run a backup")

        monkeypatch.setattr(backup_adapters.subprocess, "run", no_run)
        out = backup_adapters._run(
            {
                "action": "plan",
                "backend": "restic",
                "repository": str(tmp_path / "home" / "repo"),
                "paths": str(tmp_path / "home" / "docs"),
            }
        )
        assert "Planned (not run)" in out and "restic backup" in out and calls == []
        _no(monkeypatch, backup_adapters)
        out = backup_adapters._run(
            {
                "action": "plan",
                "backend": "restic",
                "repository": str(tmp_path / "home" / "repo"),
                "paths": str(tmp_path / "home" / "docs"),
            }
        )
        assert "No command was prepared" in out

    def test_check_delegates_restic_to_the_one_existing_reader(
        self, monkeypatch, tmp_path
    ):
        from shani_chronoa.skills import backup_status

        seen = []
        monkeypatch.setattr(
            backup_adapters.shutil, "which", lambda name: f"/usr/bin/{name}"
        )
        monkeypatch.setattr(
            backup_status, "_run", lambda args: seen.append(args) or "snapshots"
        )
        out = backup_adapters._run(
            {
                "action": "check",
                "backend": "restic",
                "repository": str(tmp_path / "home" / "repo"),
            }
        )
        assert out == "snapshots" and seen[0]["repository"] == str(tmp_path / "home" / "repo")

    def test_rsync_says_there_is_no_history_to_check(self, monkeypatch):
        monkeypatch.setattr(
            backup_adapters.shutil, "which", lambda name: f"/usr/bin/{name}"
        )
        assert "no history to check" in backup_adapters._run(
            {"action": "check", "backend": "rsync"}
        )


# ── INT-05 ADB workflow ───────────────────────────────────────────────────────


class TestAdbWorkflow:
    def test_consent_is_the_existing_phone_switch(self, monkeypatch, tmp_path):
        _config(monkeypatch, adb_workflow, **{"phone-control-enabled": False})
        assert adb_workflow._run({"action": "status"}).startswith("Refusing")

    def test_status_reads_the_devices_and_a_transfer_asks_argv_only(
        self, monkeypatch, tmp_path
    ):
        _config(monkeypatch, adb_workflow, **{"phone-control-enabled": True})
        _yes(monkeypatch, adb_workflow)
        monkeypatch.setattr(adb_workflow.shutil, "which", lambda name: "/usr/bin/adb")
        calls = []

        def fake(argv, **kwargs):
            calls.append(argv)
            if argv[1:3] == ["devices", "-l"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout="List of devices attached\nR58N123 device product:phone\n",
                    stderr="",
                )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(adb_workflow.subprocess, "run", fake)
        assert "1 phone(s) ready" in adb_workflow._run({"action": "status"})
        local = tmp_path / "home" / "note.txt"
        local.write_text("hello")
        out = adb_workflow._run(
            {
                "action": "push",
                "local": str(local),
                "remote": "/sdcard/Download/note.txt",
                "serial": "R58N123",
            }
        )
        assert "Pushed" in out, out
        argv = calls[-1]
        assert argv[1:3] == ["-s", "R58N123"]
        assert argv[3:] == ["push", str(local), "/sdcard/Download/note.txt"]
        assert all(isinstance(part, str) for part in argv)

    def test_a_remote_path_cannot_escape_or_be_a_shell(self, monkeypatch, tmp_path):
        _config(monkeypatch, adb_workflow, **{"phone-control-enabled": True})
        _yes(monkeypatch, adb_workflow)
        local = tmp_path / "home" / "note.txt"
        local.write_text("hello")
        for remote in ("/sdcard/../../etc/passwd", "/etc/passwd", "/sdcard/x;rm -rf /"):
            assert "plain remote path" in adb_workflow._run(
                {"action": "push", "local": str(local), "remote": remote}
            )

    def test_no_shell_string_construction(self):
        tree = ast.parse(Path(adb_workflow.__file__).read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "run"
            ):
                assert not any(kw.arg == "shell" for kw in node.keywords), (
                    "an adb call could be handed a shell string"
                )


# ── INT-06 QR/barcode ─────────────────────────────────────────────────────────


class TestBarcode:
    def test_missing_zint_is_refused_honestly(self, monkeypatch, tmp_path):
        monkeypatch.setattr(barcode.shutil, "which", lambda name: None)
        out = barcode._run(
            {
                "action": "create",
                "text": "ASSET-17",
                "output": str(tmp_path / "home" / "tag.png"),
            }
        )
        assert "zint" in out

    def test_create_asks_uses_argv_and_stays_in_the_home(self, monkeypatch, tmp_path):
        _yes(monkeypatch, barcode)
        monkeypatch.setattr(
            barcode.shutil,
            "which",
            lambda name: "/usr/bin/zint" if name == "zint" else None,
        )
        calls = []

        def fake(argv, **kwargs):
            calls.append(argv)
            Path(argv[-1]).write_bytes(b"png")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(barcode.subprocess, "run", fake)
        out = barcode._run(
            {
                "action": "create",
                "text": "ASSET-17",
                "output": str(tmp_path / "home" / "tag.png"),
            }
        )
        assert "Code 128" in out and "not run" not in out
        assert calls == [
            [
                "zint",
                "-b",
                "CODE128",
                "-d",
                "ASSET-17",
                "-o",
                str(tmp_path / "home" / "tag.png"),
            ]
        ]

        _no(monkeypatch, barcode)
        out = barcode._run(
            {
                "action": "create",
                "text": "ASSET-18",
                "output": str(tmp_path / "home" / "tag2.png"),
            }
        )
        assert "Nothing was made" in out and len(calls) == 1

    def test_read_describes_payloads_without_opening_them(self, monkeypatch, tmp_path):
        from shani_chronoa.skills import qr_code

        _yes(monkeypatch, barcode)
        monkeypatch.setattr(barcode.shutil, "which", lambda name: "/usr/bin/zbarimg")
        monkeypatch.setattr(
            qr_code,
            "_decode",
            lambda path: ["https://example.org/a", "WIFI:T:WPA;S:x;;"],
        )
        image = tmp_path / "home" / "photo.png"
        image.write_bytes(b"png")
        out = barcode._run({"action": "read", "path": str(image)})
        assert "a web address" in out and "Wi-Fi credentials" in out
        assert "nothing was opened" in out


# ── registry ───────────────────────────────────────────────────────────────────


def test_every_new_skill_is_registered_and_grouped():
    schemas, handlers = discover_skills()
    names = {s["function"]["name"] for s in schemas}
    for name in (
        "speaker_id",
        "device_sync",
        "backup_adapters",
        "adb_workflow",
        "barcode",
    ):
        assert name in names and name in handlers, name
        assert name in capabilities._GROUPS, f"{name} landed in the Other group"
    assert capabilities.GATED["speaker_id"] == "hearing-sense-enabled"
    assert capabilities.GATED["adb_workflow"] == "phone-control-enabled"
