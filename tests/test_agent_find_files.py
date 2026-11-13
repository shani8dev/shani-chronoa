"""`Agent.FindFiles`: the shell launcher's search by meaning, over the folders a
person keeps things in, through file_index (keyword when no embedding model
answers - and `how` says so). The first search starts the index in the
background; one after it finds the file."""

import json
import time

from shani_chronoa import agent_service


class _Params:
    def __init__(self, values):
        self._values = values

    def unpack(self):
        return self._values


class _Invocation:
    def __init__(self):
        self.value = None
        self.error = None

    def return_value(self, variant):
        self.value = variant.unpack()

    def return_dbus_error(self, name, message):
        self.error = (name, message)


def find(query, limit=8):
    inv = _Invocation()
    agent_service.AgentService(object()).FindFiles(None, ":1.9", None, None, "FindFiles", _Params((query, limit)), inv)
    return json.loads(inv.value[0])


def test_a_file_is_found_by_its_words_after_the_background_index(tmp_path, monkeypatch):
    for var, sub in (("XDG_STATE_HOME", "state"), ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache"), ("XDG_CONFIG_HOME", "config")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    docs = tmp_path / "Documents"
    docs.mkdir()
    (docs / "scan0001.txt").write_text("Electricity bill for March from City Power")
    (docs / "recipe.txt").write_text("Two cups of flour and an egg")
    monkeypatch.setenv("XDG_DOCUMENTS_DIR", str(docs))
    monkeypatch.setenv("XDG_DOWNLOAD_DIR", str(tmp_path / "none"))
    monkeypatch.setenv("XDG_DESKTOP_DIR", str(tmp_path / "none"))
    monkeypatch.setattr(agent_service.AgentService, "_last_index", None)
    first = find("electricity bill")
    assert first["indexing"] is True
    deadline = time.time() + 30
    while agent_service.AgentService._index_lock.locked() and time.time() < deadline:
        time.sleep(0.1)
    got = find("electricity bill")
    assert [h["path"] for h in got["hits"]] == [str(docs / "scan0001.txt")]
    assert got["how"] and got["indexing"] is False          # it says how; it does not index again so soon


def test_a_short_query_asks_nothing():
    assert find("ab") == {"how": "", "hits": [], "indexing": False}
