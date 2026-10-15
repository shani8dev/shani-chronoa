"""Every answer to a question is reachable, at the width the app permits.

`gui/asking.py` put the answers in a `Gtk.Box`, which neither wraps nor
shrinks. Measured, the row asked for **799px** inside a window that permits
**380px** (the app's own `set_size_request`, read from the product by the
tests here), so at the width the app opens at (460px, its `set_default_size`)
`No, and stop this turn` was cut off mid-word at the right edge and
`Provide feedback` - the one option that carries a person's own objection back
to the model - was **entirely off-screen with no way to scroll to it**.

Every other part of the question fitted comfortably: the heading asked for
74px, the diff 117px, the hint 250px. **The row of answers was the whole of
the overflow**, which is why this file is about one widget and not about text
wrapping - and why the first version of it, which added width bounds to three
labels and one header, fixed nothing that measurement could see.

**The property is one number.** The question row's *minimum* width has to fit
inside the window's own declared minimum, and that single assertion is what a
non-wrapping answer row, a header that lays out in one line and a diff line
that lays out in one line all fail. Three tests that each check one widget would
pass with two of the three broken.

What is deliberately **not** asserted: any particular width, and any pixel.
The window's minimum is read out of the product rather than restated, so a
change to it arrives here as a different answer instead of as this file
asserting a number it copied.
"""

import inspect
from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa.gui import asking, blocks, window as window_mod  # noqa: E402
from shani_chronoa import ask_bridge  # noqa: E402


#: A path long enough to be unbreakable, which is the shape that overflowed: a
#: label wraps at spaces, so one long token sets the minimum for the whole row.
LONG_PATH = ("/tmp/opencode/live/.live-window-state/da/project/"
             "greet.py")

QUESTION = (
    f"Shani wants to edit a file: {LONG_PATH}.\n\n"
    "That needs the 'file-edit-enabled' permission, which is currently off. "
    "Allow it?\n\n"
    "If you allow this once - edit_file runs this time, and the next one asks "
    "again. Closing this question without answering counts as no, never as yes."
)

BEFORE = 'def greet(name):\n    print("hello " + name)\n'
AFTER = ('def greet(name: str) -> str:\n'
         '    line = f"Hello, {name}"\n'
         '    return line\n')

OPTIONS = ["Allow this once", "Allow for this turn", "No, don't allow",
           "No, and stop this turn", "Provide feedback"]


def declared_minimum_width() -> int:
    """The width `set_size_request` gives the window.

    Read out of the module rather than restated, so raising or lowering the
    product's minimum arrives here as a different answer rather than as this
    file asserting a number it copied - the same rule as
    `test_the_window_minimum_is_not_below_the_layouts_floor.declared_minimum`.
    """
    source = inspect.getsource(window_mod.ChronoaWindow._setup_window)
    for line in source.splitlines():
        if "set_size_request" in line:
            digits = "".join(c if c.isdigit() else " " for c in line).split()
            if len(digits) == 2:
                return int(digits[0])
    raise AssertionError(
        "set_size_request is no longer a literal (width, height) pair; teach "
        "this test the new shape rather than letting it guess")


class _Host(asking.AskingMixin):
    """The minimum a window provides for `show_question` to build its row.

    `AskingMixin` is the unit under test and is written to be mixed into
    `ChronoaWindow`; this supplies exactly the attributes `show_question`
    reads and nothing more, so every assertion is about what `show_question`
    **built**. What is deliberately not stubbed is the construction itself -
    there is no display-free way to ask "would this have fitted", and standing
    in a measured width here would be asserting the fixture.
    """

    def __init__(self) -> None:
        self._question_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                     spacing=6)
        self._question_widgets: list = []
        self._pending_question = None
        self._pending_options: list = []
        self._input_placeholder = Gtk.Label()
        self._input_entry = Gtk.TextView()

    def _on_input_changed(self, _buffer) -> None:
        """The real one shows or hides the placeholder; nothing here to show."""


@pytest.fixture()
def host(monkeypatch):
    """A window carrying one real approval, diff and all.

    The preview's path is a **`Path`, as production builds it** -
    `tools.approval_preview` resolves through `files.resolve` and hands
    `InlineDiff` a `Path`, so the header shows a basename. A `str` here would
    make `InlineDiff`'s `getattr(path, "name", str(path))` fall back to the
    whole 412px path, and the first version of this fixture did exactly that
    and reported a 470px overflow that no real question has - a fixture shaped
    unlike the data, measuring its own fiction rather than the product.
    """
    monkeypatch.setitem(ask_bridge._previews, QUESTION,
                        (Path(LONG_PATH), BEFORE, AFTER))
    h = _Host()
    h.show_question(QUESTION, list(OPTIONS), lambda answer: None)
    return h


def _walk(widget, out=None):
    out = [] if out is None else out
    out.append(widget)
    child = widget.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out


def _buttons(host):
    return [w for w in _walk(host._question_row) if isinstance(w, Gtk.Button)]


def _min_width(widget) -> int:
    """The width the widget asks for - what a narrow window cannot go below."""
    return widget.measure(Gtk.Orientation.HORIZONTAL, -1)[0]


class TestTheQuestionFitsTheWindow:

    def test_the_whole_question_row_fits_the_smallest_window_the_app_permits(self, host):
        """The one property: a question the window cannot show is not a question.

        This is the assertion the three defects all fail, which is why it is
        the first one - a heading that overflows, a diff that overflows and a
        non-wrapping option row are three different causes of one number being
        wrong, and splitting it into three tests would let two of them through.
        """
        assert _min_width(host._question_row) <= declared_minimum_width(), (
            f"the question row asks for {_min_width(host._question_row)}px, and "
            f"the smallest window the app permits is "
            f"{declared_minimum_width()}px - everything past that edge is a "
            f"control nobody can press or text nobody can read")

    def test_no_single_button_is_wider_than_the_window(self, host):
        """Wrapping a row cannot rescue a button that cannot itself fit."""
        for button in _buttons(host):
            assert _min_width(button) <= declared_minimum_width(), (
                f"the option {button.get_label()!r} asks for "
                f"{_min_width(button)}px on its own, so it cannot be laid out "
                f"at any width this app permits")

    def test_a_changed_diff_line_does_not_set_the_width(self, host):
        """A diff line is ellipsised, and an ellipsis is what bounds it.

        Deliberately the *mechanism* rather than a character bound: a
        `set_max_width_chars` was tried here first and measured inert (a
        non-wrapping label lays out in one line whatever the bound says), and a
        test that asserted the bound was present would have pinned a line that
        does nothing.
        """
        lines = [w for w in _walk(host._question_row)
                 if isinstance(w, Gtk.Label)
                 and {"inline-diff-add", "inline-diff-del"} & set(w.get_css_classes())]
        assert lines, "the diff the approval is about is not on the row"
        for line in lines:
            assert line.get_ellipsize() is not None, (
                "a changed line that does not ellipsise asks for its full "
                "width and pushes the answers off the edge")

    def test_the_diff_header_does_not_set_the_width_either(self, host):
        """The one label in the diff that had no ellipsise, and it was the whole width.

        `getattr(path, "name", str(path))` falls back to the entire path for a
        `str`, and a header label lays out in one line however long it is. A
        74-character name asked for **558px** before this and 15px after.
        """
        head = [w for w in _walk(host._question_row)
                if isinstance(w, Gtk.Label) and "heading" in w.get_css_classes()
                and w.get_label().endswith(".py")]
        assert head, "the diff's own file-name heading is not on the row"
        for label in head:
            assert label.get_ellipsize() is not None, (
                "the diff header lays out in one line, so without an ellipsis "
                "it asks for the whole path and sets the width of the row")

    def test_the_diff_actually_shown_is_the_one_the_question_is_about(self, host):
        """The control: a row that draws no diff passes every bound above.

        Without this, an `InlineDiff` that stopped being built would leave five
        bounds and no subject.
        """
        text = [w.get_text() for w in _walk(host._question_row)
                if isinstance(w, Gtk.Label)
                and {"inline-diff-add", "inline-diff-del"} & set(w.get_css_classes())]
        assert any("Hello" in t for t in text), (
            f"the changed line is missing from the question: {text}")
        assert any("hello " in t for t in text), (
            f"the removed line is missing from the question: {text}")


class TestEveryAnswerIsThere:

    def test_every_option_is_a_button_on_the_row(self, host):
        """A control that is not a widget cannot be pressed.

        Counted against the list that was passed in rather than a literal, so a
        question with a different number of answers is held to the same rule.
        """
        labels = [b.get_label() for b in _buttons(host)]
        assert labels == OPTIONS, (
            f"the question offers {OPTIONS} but the row carries {labels} - an "
            f"option that is not a button on screen is not an option")

    def test_the_row_can_wrap(self, host):
        """A `Gtk.Box` cannot, and a row that cannot wrap clips its last answer.

        Asserted on the container rather than on a width, because the width is
        already held by `test_the_whole_question_row_fits...`; this is the
        property that makes that width *sufficient* rather than lucky.
        """
        rows = [w for w in _walk(host._question_row)
                if isinstance(w, Gtk.FlowBox)]
        assert rows, "the answers are not in a flow box, so they cannot wrap"
        assert rows[0].get_max_children_per_line() > 1, (
            "the answer row is limited to one per line, so it can only ever "
            "clip")

    def test_the_row_reports_what_the_buttons_need_and_not_four_times_one(self, host):
        """The half of the flow-box fix that is about *natural* width.

        `Gtk.FlowBox` sizes a non-homogeneous box as if every child were as wide
        as the widest, so four answers asked for *four times* the widest button -
        and would have overflowed the narrow window the wrap was fixed for. The
        row's **minimum** is left to GTK (that is what lets a narrow window
        allocate less and wrap), so the correction is only visible in its
        **natural** width, which is why the first version of this file asserted
        only the minimum and the mutation came back a survivor.
        """
        rows = [w for w in _walk(host._question_row) if isinstance(w, Gtk.FlowBox)]
        natural = rows[0].measure(Gtk.Orientation.HORIZONTAL, -1)[1]
        buttons = _buttons(host)
        # Natural, not minimum: laid out in one row the buttons take the width
        # they ask for, and that sum is the width the row would need to avoid
        # wrapping. Measuring minima here made the assertion stricter than the
        # property it names, and it failed against the fixed code.
        needed = sum(b.measure(Gtk.Orientation.HORIZONTAL, -1)[1] for b in buttons)
        assert natural <= needed, (
            f"the answer row reports {natural}px of natural width for buttons "
            f"that need {needed}px between them - a flow box asks for "
            f"four-times-the-widest unless `do_measure` says otherwise")

    def test_the_option_row_constructs(self):
        """The regression that cost the whole prompt, silently.

        `Gtk.FlowBox` has no `spacing` property - it is `column-spacing` and
        `row-spacing` - and passing `spacing=` raised inside `show_question`,
        which `gui/questions.py` catches and answers with an empty string. The
        widgets were built, the row was never made visible, and the tool was
        told nobody answered. Every test that walks the widget tree passes with
        that bug: the tree is fully populated. Only construction catches it.
        """
        row = asking._OptionRow()
        assert row.get_column_spacing() > 0
        assert row.get_row_spacing() > 0

    def test_the_question_row_is_actually_made_visible(self, host):
        """The end of `show_question`, and the line the `spacing=` bug never reached.

        `set_visible(True)` is the last statement, so anything raising above it
        leaves a fully-built, correctly-bounded, permanently hidden row. A
        widget-tree assertion cannot see that; this can.
        """
        assert host._question_row.get_visible(), (
            "the question row was built and never shown - show_question raised "
            "before its last line, and questions.py resolved the prompt as "
            "unanswered")
        assert host._pending_options == OPTIONS


class TestTheCancelSplitOffersTheTurnGrant:

    def test_the_turn_scoped_grant_is_offered_by_both_presenters(self, monkeypatch):
        """A grant `parse_reply` understands, that nobody is offered.

        `ALLOW_TURN` is the one grant scoped to a single action that ends with
        the request on screen, which is why it survives `is_bypass_immune`. The
        cancel-split presenter is the one that offers `CANCEL_CHOICE`, so
        every question showing it also had **no way to say "yes, this once, and
        not again for the rest of this request"** - which is the whole point of
        asking a person rather than taking a session grant.
        """
        from shani_chronoa import permissions
        monkeypatch.setattr(ask_bridge, "has_text_presenter", lambda: False)
        options = permissions.approval_request(
            "delete_file", "/tmp/x", "delete-file-enabled").options_with_cancel()
        assert permissions.ALLOW_TURN_CHOICE in options, (
            f"the cancel-split presenter cannot grant a turn: {options}")

    def test_the_grant_is_ordered_before_the_ones_that_outlive_it(self, monkeypatch):
        """Ordering carries meaning and a value matching none is a refusal.

        A turn grant is strictly narrower than a session grant, so offering the
        broader one first would make the narrower one look like the stricter
        choice being demoted.
        """
        from shani_chronoa import permissions
        monkeypatch.setattr(ask_bridge, "has_text_presenter", lambda: False)
        options = permissions.approval_request(
            "delete_file", "/tmp/x", "delete-file-enabled").options_with_cancel()
        turn = options.index(permissions.ALLOW_TURN_CHOICE)
        assert turn < options.index(permissions.DENY_CHOICE), options
        if permissions.ALLOW_SESSION_CHOICE in options:
            assert turn < options.index(permissions.ALLOW_SESSION_CHOICE), options

    def test_a_bypass_immune_action_still_offers_the_turn_grant(self, monkeypatch):
        """The control against over-correcting into "immune means no options".

        `is_bypass_immune` is why the *session* grant is withheld - a standing
        grant would be ignored anyway, and being immune is precisely that a
        grant stretched invisibly across a session. A turn grant is neither, so
        withholding it would leave an immune action with no way to say yes once.
        """
        from shani_chronoa import permissions
        monkeypatch.setattr(ask_bridge, "has_text_presenter", lambda: False)
        request = permissions.approval_request(
            "delete_file", "/tmp/x", "delete-file-enabled")
        assert permissions.is_bypass_immune(request.action)
        options = request.options_with_cancel()
        assert permissions.ALLOW_SESSION_CHOICE not in options, options
        assert permissions.ALLOW_TURN_CHOICE in options, options

    def test_the_two_presenters_offer_the_same_allowances(self, monkeypatch):
        """Two lists answering "may I say yes" is two that can disagree.

        The cancel split adds a way to stop the turn; it must not also remove a
        way to allow the action.
        """
        from shani_chronoa import permissions
        monkeypatch.setattr(ask_bridge, "has_text_presenter", lambda: False)
        request = permissions.approval_request(
            "edit_file", "/tmp/x", "file-edit-enabled")
        plain = [o for o in request.options if o != permissions.DENY_CHOICE]
        split = [o for o in request.options_with_cancel()
                 if o not in (permissions.DENY_CHOICE, permissions.CANCEL_CHOICE)]
        assert plain == split, (
            f"the plain presenter offers {plain} and the cancel-split offers "
            f"{split}; only the refusal and the cancel are meant to differ")


class TestTheDiffBoundsItself:

    def test_a_long_file_name_does_not_set_the_width(self):
        """The header on an input long enough to need the ellipsis.

        The first version of this file checked the header bound on `greet.py` -
        eight characters, far inside any bound - and the mutation that removed
        the bound came back a survivor. **A fixture that cannot tell the
        behaviour from its opposite passes the mutation that removes it.**
        """
        long_name = ("a_really_quite_long_module_name_that_somebody_actually"
                     "_chose_on_purpose.py")
        short = blocks.InlineDiff("/tmp/some/deep/tree/greet.py",
                                  BEFORE, AFTER)
        long = blocks.InlineDiff(f"/tmp/some/deep/tree/{long_name}",
                                 BEFORE, AFTER)
        widest = lambda diff: max(_min_width(w) for w in _walk(diff)
                                  if isinstance(w, Gtk.Label))
        assert widest(long) <= widest(short) + 2, (
            f"a {len(long_name)}-character file name asks for "
            f"{widest(long)}px where an 8-character one asks for "
            f"{widest(short)}px - the header lays out in one line, so only the "
            f"ellipsis stops it setting the width of everything below")

    def test_a_changed_line_longer_than_the_shortest_name_is_not_wider(self):
        """The same property for the lines, on a line long enough to show it."""
        long_before = "x = 1\n"
        long_after = "x = 1\n" + "".join(
            f"z{_n} = a_very_long_identifier_name{_n} = {2 ** _n}\n"
            for _n in range(40))
        short = blocks.InlineDiff("/tmp/x.py", BEFORE, AFTER)
        long = blocks.InlineDiff("/tmp/x.py", long_before, long_after)
        line_width = lambda diff: max(
            _min_width(w) for w in _walk(diff) if isinstance(w, Gtk.Label)
            and {"inline-diff-add", "inline-diff-del"} & set(w.get_css_classes()))
        assert line_width(long) <= line_width(short) + 2, (
            f"a long changed line asks for {line_width(long)}px where a short "
            f"one asks for {line_width(short)}px - without the ellipsis the "
            f"length of a code line is the width of the approval row")
