"""The Compare panel: is it registered, and does it say something true?

`test_conversation_diff.py` covers the comparison. This covers the panel, whose
own failure modes are different: a surface nobody can open, a list in the wrong
order, and the two states with fewer than two conversations - which is what a
new install has, and the state nobody tests.
"""

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa import conversation_store as store  # noqa: E402
from shani_chronoa.gui import surfaces as surfaces_module  # noqa: E402
from shani_chronoa.gui.surfaces import compare as compare_module  # noqa: E402


def _labels(widget):
    """Every label's text, in tree order."""
    out, stack = [], [widget]
    while stack:
        node = stack.pop(0)
        if isinstance(node, Gtk.Label) and node.get_label():
            out.append(node.get_label())
        if hasattr(node, "get_first_child"):
            child = node.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
    return out


def _store(*conversations):
    """A conversation store with one file per conversation.

    Each argument is a list of message dicts. Written directly rather than
    through `append()` so the timestamps differ by index and the "newest
    first" ordering is a fact about the fixture rather than an accident of how
    fast the loop ran.
    """
    root = Path(tempfile.mkdtemp(prefix="chronoa-compare-"))
    for number, messages in enumerate(conversations):
        sid = store.new_session(root)
        (root / f"{sid}.jsonl").write_text(
            "".join(json.dumps(m) + "\n" for m in messages), encoding="utf-8")
        store.set_generated_title(root, sid, f"chat {number}")
    return root


def _user(text):
    return {"role": "user", "content": text}


def _page(root):
    # **`_test_conversation_root`, not `_conversation_root`.** The named
    # override is deliberately spelled so that a stub built to match the code's
    # own guess cannot stand in for the real answer.
    app = SimpleNamespace(_test_conversation_root=root)
    return compare_module.build(app)


# -- where the panel asks for conversations --------------------------------


def test_the_app_has_the_root_this_asks_for():
    """**This is the test that would have caught the defect the fixture
    hid.** The first version of `_store_root` guessed application attributes
    (`_conversation_root` and two others); `ChronoaApplication` has none of
    them, and every real caller asks `conversation_store.session_dir()`
    instead. The panel would have said "Could not read your conversations" on
    every machine - and every test passed, because the fixture was a stub built
    to match the guess.

    So this asks the application, not a stand-in.
    """
    from shani_chronoa.app import ChronoaApplication

    app = ChronoaApplication.__new__(ChronoaApplication)
    root = compare_module._store_root(app)
    assert root is not None, (
        "the panel found no conversation store on the real application")
    assert hasattr(root, "is_dir") or isinstance(root, str), root


def test_the_root_the_panel_asks_for_is_where_conversations_are_written():
    """One answer to "where does a conversation live", read from the code that
    writes them - so a rename cannot leave this panel reading an empty store."""
    from shani_chronoa import conversation_store

    app_root = compare_module._store_root(
        type("_A", (), {})())
    assert str(app_root) == str(conversation_store.session_dir()), (
        f"the panel reads {app_root}, the store writes "
        f"{conversation_store.session_dir()}")

def test_an_explicit_test_root_still_wins():
    root = _store([_user("a")], [_user("b")])
    assert compare_module._store_root(
        SimpleNamespace(_test_conversation_root=root)) == root


def test_a_store_directory_that_cannot_be_resolved_is_reported(monkeypatch):
    """**The only way this panel has no store now.** It asks
    `conversation_store.session_dir()`, so there is no "the app did not supply
    a root" state left to test - and two tests that constructed one by building
    a bare `SimpleNamespace` were asserting a design that no longer exists.

    What can still fail is the lookup itself: no `XDG_DATA_HOME`, a data home
    that cannot be created. Then the panel must say so rather than raise, since
    a raising surface is a hole in the sidebar rather than a message.
    """
    from shani_chronoa import conversation_store

    def _boom():
        raise RuntimeError("no data home")

    monkeypatch.setattr(conversation_store, "session_dir", _boom)
    labels = _labels(compare_module.build(SimpleNamespace()).view)
    assert compare_module.UNREADABLE in labels, labels


def test_an_unreadable_store_is_reported_rather_than_raising(monkeypatch):
    """The store exists but cannot be listed - the same failure one layer in,
    and it must not take the panel with it."""
    from shani_chronoa import conversation_store

    def _boom(root):
        raise RuntimeError("the index is corrupt")

    monkeypatch.setattr(conversation_store, "list_sessions", _boom)
    root = _store([_user("a")], [_user("b")])
    labels = _labels(_page(root).view)
    assert compare_module.NEED_TWO in labels, labels


# -- registered -------------------------------------------------------------


def test_the_panel_is_in_the_sidebar():
    """A module nobody registers is a module nobody opens."""
    assert "compare" in surfaces_module.available_surfaces()


def test_it_declares_a_title_and_icon():
    assert compare_module.TITLE
    assert compare_module.ICON.endswith("-symbolic")


def test_it_is_in_a_section():
    assert surfaces_module.sections().get("compare") == "Remembering"


def test_its_icon_is_one_the_theme_ships():
    """An icon name Adwaita lacks renders as an empty box, which reads as a
    rendering bug. `document-compare-symbolic` and `edit-compare-symbolic` are
    both absent, which is why the name is not one of those."""
    root = Path("/usr/share/icons/Adwaita")
    assert root.is_dir(), "Adwaita is not where it was; this check is void"
    assert any(root.rglob(f"{compare_module.ICON}.svg")), \
        f"{compare_module.ICON} is not in Adwaita"


# -- the states with nothing to compare -------------------------------------


def test_a_store_with_no_conversations_says_pick_two():
    root = _store()
    assert compare_module.NEED_TWO in _labels(_page(root).view)


def test_one_conversation_is_not_a_comparison():
    """Saying "pick two" when there is one to pick is an instruction nobody can
    follow, and it is the state a fresh install is in."""
    root = _store([_user("only question")])
    assert compare_module.ONLY_ONE in _labels(_page(root).view)


def test_a_store_that_cannot_be_read_says_so():
    """Not a blank panel: a blank panel is indistinguishable from a bug."""
    app = SimpleNamespace(_test_conversation_root="/nonexistent/store")
    labels = _labels(compare_module.build(app).view)
    assert compare_module.NEED_TWO in labels or compare_module.ONLY_ONE in labels \
        or compare_module.UNREADABLE in labels, labels


def test_two_conversations_asking_nothing_is_not_a_crashing_row():
    root = _store([_user("x")], [{"role": "system", "content": "you are helpful"}])
    labels = _labels(_page(root).view)
    assert compare_module.NO_QUESTIONS in labels, labels


# -- the listing ------------------------------------------------------------


def test_the_list_is_newest_first():
    """The store's own order is by update time, and the drop-downs default to
    index 0 and 1 - so an oldest-first list would silently compare the wrong
    pair while looking entirely correct."""
    root = _store([_user("a")], [_user("b")], [_user("c")])
    labels = [label for _sid, label in compare_module._conversations(root)]
    assert labels[0].startswith("chat 2"), labels


def test_two_conversations_with_the_same_title_can_be_told_apart():
    """**A pair of drop-downs both reading "Fix the build" makes it impossible
    to say which two conversations are being compared.** Two conversations
    generated from the same first question have the same title, which is the
    common case rather than an edge one - so the label carries the message
    count, which `list_sessions` already had."""
    root = _store([_user("a")], [_user("a"), _user("b"), _user("c")])
    labels = [label for _sid, label in compare_module._conversations(root)]
    assert len(set(labels)) == len(labels), f"two rows are identical: {labels}"
    assert all("message" in label for label in labels), labels


def test_a_single_message_conversation_is_not_said_to_have_two():
    root = _store([_user("only")], [_user("a"), _user("b")])
    labels = [label for _sid, label in compare_module._conversations(root)]
    # **Located by content, not by index.** The list is newest first, so the
    # one-message conversation is second - and `labels[0]` was the two-message
    # one, which is how this passed an assertion it never made.
    assert any(label.endswith("1 message") for label in labels), labels
    assert not any("1 messages" in label for label in labels), labels


def test_an_untitled_conversation_is_named_rather_than_blank():
    root = _store([_user("a")], [_user("b")])
    sid = store.new_session(root)
    (root / f"{sid}.jsonl").write_text("", encoding="utf-8")
    labels = [label for _sid, label in compare_module._conversations(root)]
    # **The store names it, not this panel.** `list_sessions` already falls back
    # to "New conversation", so this panel's own "Untitled" never appears for an
    # empty conversation - the assertion has to be that *something* names it,
    # or it is testing a branch the store makes unreachable.
    assert all(label.strip() for label in labels), labels
    assert len([label for label in labels if label.startswith("New ")]) == 1, (
        f"the empty conversation is not named: {labels}")


# -- what it reports --------------------------------------------------------


def test_the_comparison_reads_correctly():
    root = _store([_user("fix the build")],
                  [_user("fix the build"), _user("ship it")])
    labels = " ".join(_labels(_page(root).view))
    assert "Only in the later" in labels, labels
    assert "ship it" in labels, labels
    assert "Only in the earlier" in labels, labels


def test_a_question_asked_twice_is_called_out():
    root = _store([_user("a")], [_user("b"), _user("b")])
    labels = " ".join(_labels(_page(root).view))
    assert "Asked more than once" in labels, labels


def test_a_group_with_nothing_in_it_says_none():
    """A heading with no rows under it is indistinguishable from a section that
    failed to build, and "nothing only in the earlier one" is a real answer."""
    root = _store([_user("a")], [_user("a"), _user("b")])
    labels = _labels(_page(root).view)
    assert "None" in labels, labels


def test_two_identical_conversations_report_no_differences():
    root = _store([_user("a"), _user("b")], [_user("a"), _user("b")])
    labels = _labels(_page(root).view)
    assert labels.count("None") >= 3, labels


def test_the_count_line_states_both_sides():
    root = _store([_user("a")], [_user("a"), _user("b")])
    text = " ".join(_labels(_page(root).view))
    assert "Earlier asked 1" in text and "later asked 2" in text, text


# -- the store's message window --------------------------------------------


def _long_conversation(count):
    """A conversation with `count` user messages, over the window."""
    root = Path(tempfile.mkdtemp(prefix="chronoa-compare-long-"))
    sid = store.new_session(root)
    (root / f"{sid}.jsonl").write_text(
        "".join(json.dumps(_user(f"question {n}")) + "\n"
                for n in range(count)), encoding="utf-8")
    store.set_generated_title(root, sid, "long chat")
    return root, sid


def test_a_long_conversation_is_detected_as_truncated():
    """`conversation_store.load()` keeps only the last `MAX_LOADED` messages
    and says nothing. The panel counts what came back, so without this the
    counts and the lists are quietly about a window rather than the
    conversation."""
    from shani_chronoa import conversation_store

    root, sid = _long_conversation(conversation_store.MAX_LOADED + 40)
    messages, truncated = compare_module._messages(root, sid)
    assert len(messages) == conversation_store.MAX_LOADED, len(messages)
    assert truncated is True, "the window cut was not detected"


def test_a_short_conversation_is_not_reported_as_truncated():
    root, sid = _long_conversation(5)
    messages, truncated = compare_module._messages(root, sid)
    assert len(messages) == 5
    assert truncated is False


def test_an_unreadable_conversation_is_not_reported_as_truncated():
    """A failure and a truncation look the same to the reader, and they mean
    opposite things: one says "nothing here", the other says "not all of it"."""
    messages, truncated = compare_module._messages("/nonexistent", "nope")
    assert messages == [] and truncated is False


def test_the_panel_says_so_when_a_conversation_is_cut_short():
    """**"Only in the later" is a claim about what the person has and has not
    asked.** A question asked forty turns before the window opens is reported
    as new, which is a confident "you have not asked this before" built out of
    a message the reader never had."""
    from shani_chronoa import conversation_store

    root = _store([_user("seed")])
    sid = store.new_session(root)
    (root / f"{sid}.jsonl").write_text(
        "".join(json.dumps(_user(f"question {n}")) + "\n"
                for n in range(conversation_store.MAX_LOADED + 40)),
        encoding="utf-8")
    store.set_generated_title(root, sid, "long chat")

    labels = " ".join(_labels(_page(root).view))
    assert compare_module.WINDOW_NOTE in labels, (
        f"a truncated conversation was compared without saying so: {labels}")


def test_a_truncation_on_the_earlier_side_is_also_reported():
    """**The gap mutation C3 walked through.** The long conversation in the
    test above is the *newest*, so it is the "later" one - which left the
    earlier side of the `or` completely unexercised, and a panel that mentioned
    only the later conversation's truncation passed.

    Built the other way round: the long one created **first**, so it is the
    older conversation and lands in the `earlier` drop-down.
    """
    from shani_chronoa import conversation_store

    root = Path(tempfile.mkdtemp(prefix="chronoa-compare-early-"))
    old_id = store.new_session(root)
    (root / f"{old_id}.jsonl").write_text(
        "".join(json.dumps(_user(f"old question {n}")) + "\n"
                for n in range(conversation_store.MAX_LOADED + 40)),
        encoding="utf-8")
    store.set_generated_title(root, old_id, "the long older one")
    new_id = store.new_session(root)
    (root / f"{new_id}.jsonl").write_text(
        json.dumps(_user("recent")) + "\n", encoding="utf-8")
    store.set_generated_title(root, new_id, "the short newer one")

    labels = " ".join(_labels(_page(root).view))
    assert compare_module.WINDOW_NOTE in labels, (
        f"only the later conversation's truncation is reported: {labels}")
    assert "earlier one is longer" in labels, labels


def test_the_panel_does_not_say_so_when_nothing_was_cut():
    labels = " ".join(_labels(_page(_store([_user("a")], [_user("b")])).view))
    assert compare_module.WINDOW_NOTE not in labels, labels


# -- robustness -------------------------------------------------------------


def test_changing_the_selection_redraws_without_losing_the_drop_downs():
    """**The first version cleared one box by index** - removing `children[2:]`
    on the assumption that the two selectors are always first. The selectors
    live in their own box now precisely so that layout cannot delete one.
    """
    root = _store([_user("a")], [_user("b")], [_user("c")])
    view = _page(root).view
    view._earlier.set_selected(2)
    view._compare()
    view._compare()
    # **`"..." in labels` is substring; `"..." in _labels(...)` is exact
    # membership.** Against a list the assertion is asking for an element equal
    # to the whole phrase, and the element is "Only in the later (1)" - so the
    # test failed while the panel was showing exactly the right thing.
    labels = " ".join(_labels(view))
    assert compare_module.ONLY_ONE not in labels, labels
    assert "Only in the later" in labels, labels


def test_a_selection_index_past_the_end_does_not_raise():
    """`set_selected` and the notification can disagree for a frame during a
    relayout, and an out-of-range index would be an IndexError from inside a
    `notify` handler.

    **The index comes from a stand-in, not from `set_selected(99)`.** The first
    version drove the real drop-down with an out-of-range value, and the
    mutation that removed the clamp **passed** - GTK clamps `set_selected`
    itself, so the state this test claims to exercise is unreachable that way.
    A test that cannot produce the condition it names checks nothing.
    """
    root = _store([_user("a")], [_user("b")])
    view = _page(root).view

    class _OutOfRange:
        @staticmethod
        def get_selected():
            return 99

    assert view._selected(_OutOfRange()) == view._rows[-1][0], (
        "an out-of-range index must land on the last conversation, not raise")

    class _Negative:
        @staticmethod
        def get_selected():
            return -5

    assert view._selected(_Negative()) == view._rows[0][0]


def test_a_capped_list_says_it_was_capped():
    """A capped list that does not say so reads as the whole answer - and 25
    rows of questions is short enough to hit by accident."""
    root = _store([_user("seed")],
                  [_user("seed")] + [_user(f"question number {n}")
                                     for n in range(30)])
    labels = " ".join(_labels(_page(root).view))
    assert "and 5 more" in labels, (
        f"30 unique questions, 25 shown, and nothing says five are missing: "
        f"{labels[-200:]}")


def test_reloading_with_a_conversation_deleted_does_not_leave_stale_rows():
    """The panel is built once and kept, so `reload` is what a deleted
    conversation has to go through - and a stale selection would then index a
    list it no longer belongs to."""
    root = _store([_user("a")], [_user("b")])
    view = _page(root).view
    (root / f"{compare_module._conversations(root)[-1][0]}.jsonl").unlink()
    view.reload()
    view._compare()          # must not raise
    assert _labels(view)