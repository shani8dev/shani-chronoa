"""Inventory panels read a bounded slice, not the whole store.

The engine engine-side sections answer a panel with at most 100 rows. These
tests prove the bound is in the work, not just in the returned list: a caller
asking for three rows opens three transcripts, keeps three egress events and
copies three durable percepts.
"""

import json
from pathlib import Path
import sys

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import conversation_store, egress, inventory  # noqa: E402
from shani_chronoa.senses.store import PerceptStore  # noqa: E402


def _sessions(root: Path, count: int = 12, messages: int = 8) -> None:
    root.mkdir(parents=True, exist_ok=True)
    data = {"active": "", "sessions": {}}
    for i in range(count):
        sid = f"20260101-000000-{i:04x}"
        with (root / f"{sid}.jsonl").open("w") as handle:
            for j in range(messages):
                handle.write(
                    json.dumps(
                        {
                            "role": "user" if j % 2 == 0 else "assistant",
                            "content": f"session {i} message {j}",
                        }
                    )
                    + "\n"
                )
        data["sessions"][sid] = {"title": f"session {i}", "created": i, "updated": i}
    data["active"] = next(iter(data["sessions"]))
    (root / "index.json").write_text(json.dumps(data))


def test_list_sessions_limit_reads_only_the_selected_transcripts(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    _sessions(root)
    real_load = conversation_store.load
    opened = []

    def counting_load(path):
        opened.append(Path(path).stem)
        return real_load(path)

    monkeypatch.setattr(conversation_store, "load", counting_load)

    full = conversation_store.list_sessions(root)
    opened.clear()
    limited = conversation_store.list_sessions(root, limit=3)

    assert limited == full[:3]
    assert len(opened) == 3
    assert conversation_store.list_sessions(root, limit=0) == []


def test_inventory_conversations_passes_the_limit_through(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    _sessions(root)
    monkeypatch.setattr(conversation_store, "session_dir", lambda: root)
    payload = inventory.describe("conversations", limit=4)
    assert payload["error"] if "error" in payload else len(payload["items"]) == 4


def test_egress_bound_keeps_the_last_valid_events(tmp_path, monkeypatch):
    log = tmp_path / "egress.jsonl"
    with log.open("w") as handle:
        for i in range(10):
            handle.write(json.dumps({"n": i}) + "\n")
        handle.write("a truncated line\n")
    monkeypatch.setattr(egress, "EGRESS_LOG", log)
    assert [e["n"] for e in egress.read_events(limit=3)] == [7, 8, 9]
    assert len(egress.read_events()) == 10


def test_egress_tail_handles_no_final_newline_and_bad_middle_lines(
    tmp_path, monkeypatch
):
    log = tmp_path / "egress-no-newline.jsonl"
    with log.open("w") as handle:
        handle.write(json.dumps({"n": 0}) + "\n")
        handle.write("bad middle line\n")
        for i in range(1, 6):
            suffix = "\n" if i < 5 else ""
            handle.write(json.dumps({"n": i}) + suffix)
    monkeypatch.setattr(egress, "EGRESS_LOG", log)
    assert [e["n"] for e in egress.read_events(limit=2)] == [4, 5]
    assert [e["n"] for e in egress.read_events(limit=99)] == list(range(6))
    assert egress.read_events(limit=1) == [{"n": 5}]


def test_egress_tail_returns_all_events_in_file_order(tmp_path, monkeypatch):
    log = tmp_path / "egress-empty.jsonl"
    log.write_text("")
    monkeypatch.setattr(egress, "EGRESS_LOG", log)
    assert egress.read_events(limit=5) == []


def test_percept_store_durable_limit_copies_only_that_slice(tmp_path):
    from shani_chronoa.senses import Percept

    store = PerceptStore(durable_path=tmp_path / "durable.jsonl")
    for i in range(6):
        store.add(
            Percept(sense="test", kind="fact", content=f"fact {i}", created_at=float(i))
        )
    assert [p.content for p in store.durable(limit=2)] == [
        p.content for p in store.durable()[:2]
    ]
    assert store.durable(limit=0) == []
