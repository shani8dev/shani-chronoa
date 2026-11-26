"""Conversation engine ops: real store over a throwaway XDG data home."""

import pytest

from shani_chronoa import conversation_store, files
from shani_chronoa.engine_ops import conversations


@pytest.fixture
def root(tmp_path, monkeypatch):
    data = tmp_path / "xdg-data"
    data.mkdir()
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    return data / "shani-chronoa" / "sessions"


def _session(root, title, text="hello"):
    sid = conversation_store.new_session(root)
    conversation_store.append({"role": "user", "content": text}, root / f"{sid}.jsonl")
    if title != text:
        conversation_store.rename(root, sid, title)
    return sid


def test_list_sessions_uses_the_test_data_home(root):
    one = _session(root, "one", "first words")
    two = _session(root, "two", "second words")

    rows = conversations.list_sessions()

    assert [s["id"] for s in rows] == [two, one]
    assert rows[0]["title"] == "two"
    assert set(rows[0]) >= {"id", "title", "updated", "created", "messages", "active"}
    assert conversation_store.session_dir() == root


def test_list_sessions_honours_the_limit(root):
    _session(root, "one", "first words")
    _session(root, "two", "second words")

    assert len(conversations.list_sessions(limit=1)) == 1
    assert conversations.list_sessions(limit=0) == []


def test_resume_switches_the_index_and_the_live_assistant(root):
    _session(root, "first", "first words")
    wanted = _session(root, "second", "second words")

    class Assistant:
        def __init__(self):
            self.got = []

        def switch_session(self, path):
            self.got.append(path)

    class App:
        assistant = Assistant()

    app = App()
    out = conversations.resume("second", app=app)

    assert out == {"session": str(root / f"{wanted}.jsonl"), "ref": wanted}
    assert app.assistant.got == [root / f"{wanted}.jsonl"]
    assert conversation_store.index(root)["active"] == wanted


def test_resume_needs_no_app_and_refuses_an_unknown_ref(root):
    sid = _session(root, "only", "hello")

    plain = conversations.resume(sid)
    missing = conversations.resume("no such conversation")

    assert plain == {"session": str(root / f"{sid}.jsonl"), "ref": sid}
    assert "error" in missing


def test_resume_with_an_app_that_cannot_switch_is_an_error(root):
    sid = _session(root, "only", "hello")

    out = conversations.resume(sid, app=object())

    assert "no assistant" in out["error"]
    assert "error" not in conversations.resume(sid)


def test_rename_returns_the_title_the_store_kept(root):
    sid = _session(root, "old", "hello")

    out = conversations.rename("old", "a better title")

    assert out == {"ref": sid, "title": "a better title"}
    assert conversations.list_sessions()[0]["title"] == "a better title"
    assert "error" in conversations.rename("missing", "whatever")


def test_delete_refuses_without_confirm(monkeypatch, root):
    sid = _session(root, "keep me", "hello")
    calls = []
    monkeypatch.setattr(
        conversation_store, "delete", lambda *a, **k: calls.append((a, k)) or ""
    )

    out = conversations.delete(sid)

    assert "confirmation required" in out["error"]
    assert calls == []
    assert sid in {s["id"] for s in conversations.list_sessions()}


def test_delete_with_confirm_trashes_and_removes_the_session(monkeypatch, root):
    sid = _session(root, "delete me", "hello")
    trash = root.parent / "trash"
    trash.mkdir()

    def fake_trash(path):
        path.rename(trash / path.name)
        return True, ""

    monkeypatch.setattr(files, "trash", fake_trash)

    out = conversations.delete(sid, confirm=True)

    assert out == {"deleted": sid}
    assert sid not in {s["id"] for s in conversations.list_sessions()}
    assert (trash / f"{sid}.jsonl").is_file()
    assert "error" in conversations.delete("missing", confirm=True)
