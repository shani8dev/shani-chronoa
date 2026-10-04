"""Second round from assistd/sayri: cache-friendly tools, context fit, degraded speech, app launch, search, presets."""

import asyncio
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from shani_chronoa import capabilities, local_llm, conversation_store
from shani_chronoa.config import ChronoaConfig


def _tool(name):
    return {"type": "function", "function": {"name": name, "description": name, "parameters": {}}}


def test_history_is_cut_to_the_real_context_by_whole_turns():
    big = "x" * 9000
    msgs = [{"role": "system", "content": "S"},
            {"role": "user", "content": "old " + big},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "function": {"name": "f", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "1", "content": big},
            {"role": "user", "content": "new question"}]
    out = local_llm.fit_to_context(msgs, n_ctx=4096)
    assert out[0]["content"] == "S" and out[-1]["content"].endswith("new question")
    assert all(m.get("role") != "tool" for m in out), "a tool result is never kept without its call"
    assert "earlier message(s)" in out[-1]["content"]
    assert local_llm.fit_to_context(msgs, n_ctx=100_000) == msgs, "nothing is cut when it fits"


def test_speech_degrades_after_repeated_failures_and_recovers(monkeypatch):
    from shani_chronoa.tts import PiperTTS
    t = PiperTTS()
    calls = []
    monkeypatch.setattr(t, "_synthesize", lambda text, out: calls.append(text) or False)
    for _ in range(3):
        assert not t.synthesize("hi", "/tmp/x.wav")
    assert "paused" in t.degraded_reason
    assert not t.synthesize("hi", "/tmp/x.wav") and len(calls) == 3, "while degraded, no 60 s attempt is made"
    t._degraded_until = time.monotonic() - 1  # cool-down over: one attempt through
    monkeypatch.setattr(t, "_synthesize", lambda text, out: True)
    assert t.synthesize("hi", "/tmp/x.wav") and t.degraded_reason == ""


def test_open_application_falls_back_to_the_desktop_search(monkeypatch):
    from shani_chronoa.skills import open_application as oa

    class Info:
        def __init__(self, name, id_):
            self.name, self.id_ = name, id_

        def should_show(self):
            return True

        def get_display_name(self):
            return self.name

        def get_id(self):
            return self.id_
    rhythm = Info("Rhythmbox", "org.gnome.Rhythmbox3.desktop")
    monkeypatch.setattr(oa.Gio.AppInfo, "get_all", staticmethod(lambda: [Info("Files", "org.gnome.Nautilus.desktop")]))
    monkeypatch.setattr(oa.Gio.DesktopAppInfo, "search", staticmethod(lambda q: [["org.gnome.Rhythmbox3.desktop"]]))
    monkeypatch.setattr(oa.Gio.DesktopAppInfo, "new", staticmethod(lambda i: rhythm))
    assert oa.find_app("files").get_display_name() == "Files"
    assert oa.find_app("music player") is rhythm
    assert oa._alive(os.getpid()) and not oa._alive(2 ** 22 + 7)


def test_an_app_that_dies_at_once_is_not_reported_as_opened(monkeypatch):
    from shani_chronoa.skills import open_application as oa

    class Info(oa.Gio.DesktopAppInfo):
        pass
    fake = SimpleNamespace(get_display_name=lambda: "Broken")
    monkeypatch.setattr(oa, "find_app", lambda name: fake)
    monkeypatch.setattr(oa.Gio, "DesktopAppInfo", type(fake))  # isinstance(...) -> True for the fake
    fake.launch_uris_as_manager = lambda *a: (a[5](None, 999_999_999), True)[1]
    monkeypatch.setattr(oa, "ALIVE_SECONDS", 0.01)
    assert "exited straight away" in oa._run({"name": "broken"})


def test_echo_of_the_reply_is_not_a_follow_up():
    from shani_chronoa.app import _is_echo
    reply = "The timer is set for ten minutes, starting now."
    assert _is_echo("the timer is set for ten minutes", reply)
    assert not _is_echo("make it twenty minutes instead", reply)


def test_sound_cues_are_off_by_default():
    from shani_chronoa import cues
    assert cues.play("listening") is False


def test_presets_set_only_action_permissions_and_read_back():
    c = ChronoaConfig()
    assert "vision-sense-enabled" not in capabilities.ACTION_CONSENT_KEYS
    capabilities.apply_preset("everyday", c)
    assert capabilities.current_preset(c) == "everyday"
    assert c.get_bool("file-edit-enabled") and not c.get_bool("file-delete-enabled")
    assert not c.get_bool("power-control-enabled")
    capabilities.apply_preset("full", c)
    assert c.get_bool("file-delete-enabled") and capabilities.current_preset(c) == "full"
    c.set("power-control-enabled", "false")
    assert capabilities.current_preset(c) == "custom"
    capabilities.apply_preset("chat", c)
    assert not any(c.get_bool(k) for k in capabilities.ACTION_CONSENT_KEYS)


def test_search_ranks_the_best_match_and_forgets_deleted(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    root = conversation_store.session_dir()
    a = conversation_store.active_path(root)
    conversation_store.append({"role": "user", "content": "router router router password reset"}, a)
    conversation_store.new_session(root)
    b = conversation_store.active_path(root)
    conversation_store.append({"role": "user", "content": "the router is in the hall, next to the shelf, near the door"}, b)
    hits = conversation_store.search(root, "router")
    assert [h["id"] for h in hits][:2] == [a.stem, b.stem], "bm25: the message most about routers first"
    conversation_store.append({"role": "assistant", "content": "Hold the reset pin."}, b)
    assert conversation_store.search(root, "reset pin")[0]["id"] == b.stem, "new lines are indexed incrementally"
    conversation_store.delete(root, b.stem)
    import sqlite3
    rows = sqlite3.connect(root / conversation_store.SEARCH_DB).execute("SELECT count(*) FROM msgs WHERE sid = ?", (b.stem,))
    assert rows.fetchone()[0] == 0, "a deleted conversation's words leave the index at once"
    assert oct(os.stat(root / conversation_store.SEARCH_DB).st_mode)[-3:] == "600"


def test_stt_server_falls_back_without_the_binary(monkeypatch):
    from shani_chronoa import stt_server
    monkeypatch.setattr(stt_server, "binary", lambda: "")
    assert stt_server.ensure("/nope/model.bin") is False
    assert stt_server.transcribe("/nope.wav") is None
