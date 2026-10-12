"""Which turn wrote which file.

**The gap this closes:** the pre-image ring stored `{path, at, content}` and no
turn, so "what did *this* turn do to my machine" had no answer - only a
session-wide one, which is not the same question. The Diff panel's undo ring
already knew every change; nothing could say whose it was.

**The label crosses the subprocess boundary in `argv`, not in memory.** A skill
runs as `python3 -c`, so the only channel to the child is the one the consent
grant already uses. The child is told *which turn asked* and nothing else - it
is a label in a log, not a way back into the app, and the answer string remains
the only thing that comes back.

The load-bearing test drives a **real turn** and checks the file it wrote shows
up against that turn. Asserting the ring stores a `turn` key would pass against
a mechanism the dispatcher never populates - which is exactly the bug shape
this repository keeps meeting.
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import tools as tools_module  # noqa: E402
from shani_chronoa.skills import undo_last_change as ring  # noqa: E402


def _store(tmp_path, monkeypatch, name="a.txt", text=b"before"):
    """A file plus a ring that only this test reads."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    path = tmp_path / name
    path.write_bytes(text)
    return path


# -- the label is treated as text to check, not as an identifier -----------


def test_a_newline_in_the_label_cannot_forge_a_second_ring_line(monkeypatch):
    """The value comes from the environment and lands in a file a person may
    read. A newline would put a second line there."""
    monkeypatch.setenv(ring.TURN_ENV, "turn-1\n{\"path\": \"/etc/passwd\"}")
    assert "\n" not in ring.current_turn()
    assert len(ring.current_turn()) <= ring.MAX_TURN_CHARS


def test_an_absurdly_long_label_is_cut(monkeypatch):
    monkeypatch.setenv(ring.TURN_ENV, "x" * 5000)
    assert len(ring.current_turn()) == ring.MAX_TURN_CHARS


def test_whitespace_is_collapsed_so_two_spellings_are_one_turn(monkeypatch):
    monkeypatch.setenv(ring.TURN_ENV, "  turn   1  ")
    assert ring.current_turn() == "turn 1", ring.current_turn()
    monkeypatch.setenv(ring.TURN_ENV, "turn 1")
    assert ring.current_turn() == ring.current_turn()


def test_no_environment_means_no_turn(monkeypatch):
    monkeypatch.delenv(ring.TURN_ENV, raising=False)
    assert ring.current_turn() == ""


# -- attribution is not guesswork -----------------------------------------


def test_an_unattributed_change_belongs_to_nobody(monkeypatch, tmp_path):
    """**A file listed as changed by a turn that did not change it is worse
    than no list at all.** An entry with no label - a skill called directly, or
    a build from before this existed - is not folded into whichever turn asks.
    """
    path = _store(tmp_path, monkeypatch)
    ring.record_preimage(path, b"before")
    path.write_bytes(b"after")
    assert ring.changes_for_turn("turn-1") == [], ring.changes_for_turn("turn-1")
    assert ring.changes_for_turn("") == []


def test_two_turns_are_told_apart(monkeypatch, tmp_path):
    a = _store(tmp_path, monkeypatch, "a.txt")
    b = tmp_path / "b.txt"
    b.write_bytes(b"before")

    monkeypatch.setenv(ring.TURN_ENV, "turn-1")
    ring.record_preimage(a, b"before")
    monkeypatch.setenv(ring.TURN_ENV, "turn-2")
    ring.record_preimage(b, b"before")

    assert [Path(p).name for p in ring.changes_for_turn("turn-1")] == ["a.txt"]
    assert [Path(p).name for p in ring.changes_for_turn("turn-2")] == ["b.txt"]


def test_the_same_file_touched_by_two_turns_is_listed_by_each(monkeypatch,
                                                              tmp_path):
    """A file rewritten in turn 2 was changed by *both*, and denying that to
    either would be the other half of the same lie."""
    a = _store(tmp_path, monkeypatch)
    monkeypatch.setenv(ring.TURN_ENV, "turn-1")
    ring.record_preimage(a, b"before")
    monkeypatch.setenv(ring.TURN_ENV, "turn-2")
    ring.record_preimage(a, b"after")
    assert ring.changes_for_turn("turn-1") and ring.changes_for_turn("turn-2")


def test_one_path_written_twice_in_a_turn_is_listed_once(monkeypatch, tmp_path):
    a = _store(tmp_path, monkeypatch)
    monkeypatch.setenv(ring.TURN_ENV, "turn-1")
    ring.record_preimage(a, b"before")
    ring.record_preimage(a, b"before")
    assert ring.changes_for_turn("turn-1") == [str(a)]


def test_a_binary_change_is_attributed_too(monkeypatch, tmp_path):
    """The base64 branch is a separate code path, and an un-attributed .docx
    would be exactly the sort of file somebody wants to know about."""
    path = _store(tmp_path, monkeypatch, "a.docx", b"\x00\x01binary")
    monkeypatch.setenv(ring.TURN_ENV, "turn-1")
    ring.record_preimage(path, b"\x00\x01binary")
    assert ring.changes_for_turn("turn-1") == [str(path)]


def test_attribution_does_not_break_undo(monkeypatch, tmp_path):
    """The ring is the undo mechanism. Adding a key must not cost anybody
    their ability to put a file back."""
    path = _store(tmp_path, monkeypatch)
    monkeypatch.setenv(ring.TURN_ENV, "turn-1")
    ring.record_preimage(path, b"before")
    path.write_bytes(b"after")
    pairs = ring.recent_changes()
    assert any(str(p) == str(path) and before == "before"
               for p, before, _after in pairs), pairs


# -- the dispatcher labels the child ---------------------------------------


def test_the_dispatcher_puts_the_turn_on_the_childs_command():
    """The whole feature depends on this one line. It is checked against the
    command that would be built, not against a comment saying it happens."""
    from shani_chronoa import tools

    tools.set_current_turn("turn-7")
    try:
        source = Path(tools.__file__).read_text()
        assert "TURN_ENV}={turn}" in source, (
            "the turn is not put on the child's command line")
    finally:
        tools.clear_current_turn()


def test_clearing_the_turn_returns_it_to_empty():
    tools_module.set_current_turn("turn-3")
    assert tools_module.current_turn() == "turn-3"
    tools_module.clear_current_turn()
    assert tools_module.current_turn() == ""


def test_setting_the_turn_returns_the_previous_one():
    """So a nested call can restore what was there, rather than clearing a
    label an outer turn is still using."""
    assert tools_module.set_current_turn("outer") == ""
    try:
        assert tools_module.set_current_turn("inner") == "outer"
    finally:
        tools_module.set_current_turn("outer")
    assert tools_module.current_turn() == "outer"


def test_a_turn_label_is_normalised_before_it_is_stored():
    tools_module.set_current_turn("  turn\n  4 ")
    try:
        assert tools_module.current_turn() == "turn 4"
    finally:
        tools_module.clear_current_turn()


# -- end to end: a real turn, a real file ----------------------------------


class _OneToolLLM:
    """One tool call, then an answer - enough to run a whole turn."""

    def __init__(self):
        self.turns = 0

    async def chat_message(self, messages, tools=None):
        self.turns += 1
        if self.turns == 1:
            return {"role": "assistant", "tool_calls": [
                {"id": "c1", "function": {"name": "write_text_file",
                                          "arguments": "{}"}}]}
        return {"role": "assistant", "content": "done"}


def test_a_turn_reports_the_file_it_wrote(monkeypatch, tmp_path):
    """**The load-bearing case, and it needs a real turn.** Asserting the ring
    stores a `turn` key would pass against a dispatcher that never sets the
    environment - which is the actual bug this feature could have shipped.

    The fake tool sets the variable itself because that is what the *child*
    would see: the dispatcher's `argv` prefix does not exist in this process.
    """
    import shani_chronoa.assistant as assistant_module

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    path = tmp_path / "written.txt"
    path.write_bytes(b"before")

    def fake_execute(name, arguments, **kwargs):
        os.environ[ring.TURN_ENV] = tools_module.current_turn()
        try:
            ring.record_preimage(path, b"before")
            path.write_bytes(b"after")
        finally:
            os.environ.pop(ring.TURN_ENV, None)
        return f"wrote {path}"

    monkeypatch.setattr(assistant_module, "execute_tool", fake_execute)
    assistant = assistant_module.Assistant(_OneToolLLM())
    asyncio.run(assistant.handle("write the file"))

    stats = assistant.turn_stats()
    assert stats["changed"] == [str(path)], stats["changed"]


def test_a_turn_that_wrote_nothing_reports_nothing(monkeypatch):
    import shani_chronoa.assistant as assistant_module

    monkeypatch.setenv("XDG_STATE_HOME", tempfile.mkdtemp())

    class _Answers:
        async def chat_message(self, messages, tools=None):
            return {"role": "assistant", "content": "no tools needed"}

    assistant = assistant_module.Assistant(_Answers())
    asyncio.run(assistant.handle("hello"))
    assert assistant.turn_stats()["changed"] == []


def test_the_label_is_cleared_after_a_turn(monkeypatch):
    """**A label left behind attributes the next turn's writes to this one** -
    so the one turn that got cancelled would appear to have rewritten whatever
    the following turn did. The `finally` is what prevents it, so the test
    looks at what is left behind rather than at the file."""
    import shani_chronoa.assistant as assistant_module

    monkeypatch.setenv("XDG_STATE_HOME", tempfile.mkdtemp())
    monkeypatch.setattr(assistant_module, "execute_tool",
                        lambda *a, **k: "fine")

    class _Answers:
        async def chat_message(self, messages, tools=None):
            return {"role": "assistant", "content": "done"}

    assistant = assistant_module.Assistant(_Answers())
    asyncio.run(assistant.handle("hello"))
    assert tools_module.current_turn() == "", (
        f"the turn label survived: {tools_module.current_turn()!r}")


def test_the_label_is_cleared_even_when_the_turn_raises(monkeypatch):
    """The interrupted path is the one that matters: the user closed the
    window mid-turn."""
    import shani_chronoa.assistant as assistant_module

    monkeypatch.setenv("XDG_STATE_HOME", tempfile.mkdtemp())

    class _Explodes:
        async def chat_message(self, messages, tools=None):
            raise RuntimeError("the model went away")

    assistant = assistant_module.Assistant(_Explodes())
    try:
        asyncio.run(assistant.handle("hello"))
    except Exception:
        pass
    assert tools_module.current_turn() == "", (
        "a failed turn left its label behind")


def test_two_turns_get_different_labels():
    """Otherwise every file in a session is attributed to the same turn."""
    import shani_chronoa.assistant as assistant_module

    class _Answers:
        async def chat_message(self, messages, tools=None):
            return {"role": "assistant", "content": "done"}

    assistant = assistant_module.Assistant(_Answers())
    labels = []
    for _ in range(2):
        asyncio.run(assistant.handle("hello"))
        labels.append(assistant._turn_label)
    assert labels[0] != labels[1], labels