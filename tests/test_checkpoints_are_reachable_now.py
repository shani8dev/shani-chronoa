"""A capability that existed, worked, and was called by nothing.

Studying `harness-study/TencentDB-Agent-Memory` turned up a gap this repo could
not see in itself. That project's cold-start story is *"import the save file,
then get to work"* - go back to a known-good point rather than start over. Reading
its README prompted the question, and the answer here was measured, not assumed:

`conversation_store.checkpoint()`, `revert()` and `list_checkpoints()` are all
implemented, all documented, and **had zero callers** in `shani_chronoa/` and in
`tests/` - verified by grepping the package for `checkpoint(` and `revert(`.

So the thing its own docstring describes was unreachable:

    A checkpoint is a copy of the conversation at a point the user wants to
    come back to - "before I edited that file" - rather than a reset.

A person could neither save that point nor return to it, from the panel, from
Settings, from the wizard, or by any other route. This is the same shape as
`conversation_store.fork()` before `_branch_button` wired it, and of the five
dead ends closed earlier in the session.

The distinction under test is the module's own and it is the one worth keeping:
**a checkpoint is not a reset.** `revert()` rewrites the current transcript and
leaves the checkpoint in place, so the same point can be returned to again - or a
different one, if the first revert was not right. That is why the menu offers
every numbered checkpoint rather than a single "undo".

**The loop is proven end-to-end against a real session on disk**, not by asserting
a button exists - the button is the half that was missing, and the store half is
what tells you the port is real.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import conversation_store as cs  # noqa: E402

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk  # noqa: E402


def _walk(node, out=None):
    out = [] if out is None else out
    if isinstance(node, Gtk.Widget):
        out.append(node)
    child = node.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out


class _Config:
    def get(self, key, default=""):
        return str(default)

    def get_bool(self, key, default=False):
        return bool(default)

    def get_double(self, key, default=0.0):
        return float(default)


class _App:
    def __init__(self):
        self.config = _Config()
        self.window = None
        self.tts = None
        self.stt = None

    def activate_action(self, name, arg):
        pass


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A real session directory, so the whole loop runs against the filesystem."""
    monkeypatch.setattr(cs, "session_dir", lambda: tmp_path)
    sid = cs.new_session(tmp_path)
    return tmp_path, sid


def _write(root, sid, messages):
    cs.rewrite(messages, cs.session_path(root, sid))


def test_the_loop_saves_edits_and_returns(store):
    """**The port, proven.** checkpoint -> change your mind -> revert -> intact."""
    root, sid = store
    _write(root, sid, [{"role": "user", "content": "do the thing"},
                       {"role": "assistant", "content": "done"}])
    assert cs.list_checkpoints(root, sid) == []

    assert cs.checkpoint(root, sid) is True
    assert cs.list_checkpoints(root, sid) == [1]

    #  replaces the file rather than appending, so the regretted
    # turn is written as the full transcript including it - three turns.
    _write(root, sid, [{"role": "user", "content": "do the thing"},
                       {"role": "assistant", "content": "done"},
                       {"role": "user", "content": "a change I regret"}])
    assert len(cs.load(cs.session_path(root, sid))) == 3

    assert cs.revert(root, sid, 1) is True
    assert len(cs.load(cs.session_path(root, sid))) == 2

    # **Not a reset.** This is the whole reason checkpoints are numbered rather
    # than a single undo, and the reason `revert()` rewrites instead of deleting.
    assert cs.list_checkpoints(root, sid) == [1], (
        "the checkpoint was consumed by the revert, so the same point cannot be "
        "returned to twice")


def test_the_panel_can_reach_the_capability(store):
    """The half that was missing: a control that calls it.

    The button is icon-only, so it is found by icon name and the popover's own
    contents are read - asserting on a label would not find it at all.
    """
    from shani_chronoa.gui.surfaces import conversations

    root, sid = store
    _write(root, sid, [{"role": "user", "content": "hello"}])
    cs.checkpoint(root, sid)
    conversations._STORE_ITEMS = [{"id": sid, "title": "t", "updated": 0.0,
                                   "messages": 1, "active": False}]

    page = conversations.build(_App())
    found = [w for w in _walk(page)
             if isinstance(w, Gtk.MenuButton)
             and w.get_icon_name() == "document-save-symbolic"]

    assert found, (
        "checkpoint() and revert() have no caller, so this capability is "
        "unreachable from anywhere - the panel must offer it")
    items = [b.get_label() for b in _walk(found[0].get_popover())
             if isinstance(b, Gtk.Button)]

    assert "Set a checkpoint here" in items, items
    assert "Return to checkpoint #1" in items, (
        f"a saved checkpoint cannot be returned to. Menu offers: {items}")
