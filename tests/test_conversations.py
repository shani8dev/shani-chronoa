"""Many saved conversations: the index, the skill, and the app following a switch.

Real files in a temp XDG_DATA_HOME; the app part drives the real
ChronoaApplication methods on a stand-in object (no window, no model).
"""

import json
import os
from types import SimpleNamespace

import pytest

from shani_chronoa import conversation_store
from shani_chronoa.assistant import Assistant
from shani_chronoa.config import ChronoaConfig


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return conversation_store.session_dir()


def _say(path, *pairs):
    for role, text in pairs:
        conversation_store.append({"role": role, "content": text}, path)


def test_the_old_single_transcript_is_folded_in_once(root):
    root.mkdir(parents=True)
    legacy = root / "current.jsonl"
    legacy.write_text(json.dumps({"role": "user", "content": "plan my trip to Goa"}) + "\n")
    path = conversation_store.active_path(root)
    assert not legacy.exists() and path.exists()
    [only] = conversation_store.list_sessions(root)
    assert only["title"] == "plan my trip to Goa" and only["active"] and only["messages"] == 1
    assert conversation_store.active_path(root) == path, "a second call must not migrate or create again"
    assert oct(os.stat(root / conversation_store.INDEX).st_mode)[-3:] == "600"


def test_titles_come_from_the_first_thing_said_and_new_reuses_a_blank(root):
    first = conversation_store.active_path(root)
    _say(first, ("user", "what is my router's admin address?"), ("assistant", "192.168.1.1"))
    blank = conversation_store.new_session(root)
    assert conversation_store.new_session(root) == blank, "an empty open conversation is reused, not stacked"
    _say(conversation_store.active_path(root), ("user", "x" * 100))
    titles = [s["title"] for s in conversation_store.list_sessions(root)]
    assert titles[1] == "what is my router's admin address?"
    assert titles[0].endswith("…") and len(titles[0]) == conversation_store.TITLE_CHARS


def test_switch_rename_copy_delete_and_search(root):
    a = conversation_store.active_path(root)
    _say(a, ("user", "router password reset steps"), ("assistant", "Hold the reset pin for 10 seconds"),
         ("user", "and the wifi name?"), ("assistant", "It is on the sticker"))
    conversation_store.new_session(root)
    b = conversation_store.active_path(root)
    _say(b, ("user", "dinner ideas"), ("assistant", "Try dal"))

    assert conversation_store.switch(root, "router") == a.stem
    assert conversation_store.switch(root, "nothing like this") == ""
    assert conversation_store.rename(root, "router", "Home network") == a.stem
    hits = conversation_store.search(root, "reset pin", exclude=b.stem)
    assert hits and hits[0]["title"] == "Home network" and "reset pin" in hits[0]["snippet"]
    assert conversation_store.search(root, "reset pin", exclude=a.stem) == []

    copy = conversation_store.fork(root, "Home network", keep_user_turns=1)
    kept = conversation_store.load(root / f"{copy}.jsonl")
    assert [m["content"] for m in kept] == ["router password reset steps", "Hold the reset pin for 10 seconds"]
    assert conversation_store.index(root)["active"] == copy

    assert conversation_store.delete(root, copy) == copy and not (root / f"{copy}.jsonl").exists()
    assert conversation_store.index(root)["active"] in (a.stem, b.stem)
    title, md = conversation_store.export_markdown(root, "Home network")
    assert title == "Home network" and "**You:** router password reset steps" in md


def test_ids_are_never_paths(root):
    with pytest.raises(ValueError):
        conversation_store.session_path(root, "../../etc/passwd")
    assert conversation_store.switch(root, "../x") == ""


def test_the_skill_end_to_end(root, tmp_path):
    from shani_chronoa.skills import conversations as conv
    a = conversation_store.active_path(root)
    _say(a, ("user", "remind me how to renew my passport"), ("assistant", "Book at the passport seva site"))
    assert "Started a new conversation" in conv._run({"action": "new"})
    assert "renew my passport" in conv._run({"action": "list"})
    found = conv._run({"action": "search", "query": "passport seva"})
    assert "renew my passport" in found and "seva" in found
    assert "Opened" in conv._run({"action": "open", "which": "passport"})
    out = tmp_path / "out.md"
    assert "Saved" in conv._run({"action": "export", "path": str(out)})
    assert "**Chronoa:** Book at the passport seva site" in out.read_text()
    assert "already exists" in conv._run({"action": "export", "path": str(out)})
    # delete is gated
    assert "Refusing" in conv._run({"action": "delete", "which": "passport"})
    assert a.exists()
    ChronoaConfig().set("file-delete-enabled", "true")
    assert "Deleted" in conv._run({"action": "delete", "which": "passport"})
    assert not a.exists()


def test_the_app_follows_a_switch_made_during_a_turn(root):
    from shani_chronoa.app import ChronoaApplication
    a = conversation_store.active_path(root)
    _say(a, ("user", "first chat"), ("assistant", "hello"))
    assistant = Assistant(llm=None, session_path=a)
    shown = []
    window = SimpleNamespace(show_conversation=lambda turns: shown.append(list(turns)),
                             set_status=lambda *_: None, set_response=lambda *_: None)
    app = SimpleNamespace(assistant=assistant, window=window)
    app._follow_active_conversation = lambda force=False: ChronoaApplication._follow_active_conversation(app, force)

    ChronoaApplication._follow_active_conversation(app)
    assert shown == [], "nothing changed, so nothing is redrawn"
    conversation_store.new_session(root)                      # what the skill does
    ChronoaApplication._follow_active_conversation(app)
    assert assistant.session_path != a and shown[-1] == []
    ChronoaApplication._open_conversation(app, a.stem)
    assert assistant.session_path == a and shown[-1] == [("user", "first chat"), ("assistant", "hello")]
    # Ctrl+N keeps the old conversation instead of deleting it
    ChronoaApplication._reset_conversation(app, None, None)
    assert a.exists() and assistant.session_path != a


def test_a_generated_title_never_replaces_a_chosen_one(root):
    a = conversation_store.active_path(root)
    _say(a, ("user", "my router keeps dropping wifi every evening"), ("assistant", "Try moving it"))
    assert conversation_store.set_generated_title(root, a.stem, '"Evening Wi-Fi drops."')
    assert conversation_store.index(root)["sessions"][a.stem]["title"] == "Evening Wi-Fi drops"
    assert not conversation_store.set_generated_title(root, a.stem, "Another"), "once per conversation"
    b = conversation_store.new_session(root)
    _say(root / f"{b}.jsonl", ("user", "dinner"), ("assistant", "dal"))
    conversation_store.rename(root, b, "Food plans")
    assert not conversation_store.set_generated_title(root, b, "Dal tonight")
    assert conversation_store.index(root)["sessions"][b]["title"] == "Food plans"
    assert not conversation_store.set_generated_title(root, "../x", "t")


def test_the_title_request_is_schema_constrained(monkeypatch):
    import asyncio
    from shani_chronoa import local_llm
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"title": "Router Wi-Fi drops"}'}}]})
    import httpx
    llm = local_llm.LocalLLM()
    client = httpx.AsyncClient(base_url=local_llm.BASE_URL, transport=httpx.MockTransport(handler))

    async def get_client():
        return client
    llm._get_client = get_client
    monkeypatch.setattr(local_llm, "context_tokens", lambda default=8192: 8192)
    assert asyncio.run(llm.suggest_title("my router drops", "move it")) == "Router Wi-Fi drops"
    assert seen[0]["temperature"] == 0.2 and seen[0]["response_format"]["type"] == "json_schema"
