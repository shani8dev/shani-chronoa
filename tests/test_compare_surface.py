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
    app = SimpleNamespace(_conversation_root=root)
    return compare_module.build(app)


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
    app = SimpleNamespace(_conversation_root="/nonexistent/store")
    labels = _labels(compare_module.build(app).view)
    assert compare_module.NEED_TWO in labels or compare_module.ONLY_ONE in labels \
        or compare_module.UNREADABLE in labels, labels


def test_an_app_that_cannot_say_where_conversations_live_says_so():
    app = SimpleNamespace()
    assert compare_module.UNREADABLE in _labels(compare_module.build(app).view)


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
    titles = [t for _sid, t in compare_module._conversations(root)]
    assert titles[0] == "chat 2", titles


def test_an_untitled_conversation_is_named_rather_than_blank():
    root = _store([_user("a")], [_user("b")])
    sid = store.new_session(root)
    (root / f"{sid}.jsonl").write_text("", encoding="utf-8")
    titles = [t for _s, t in compare_module._conversations(root)]
    assert "" not in titles, titles


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