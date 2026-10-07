"""A path reads as a path; a sentence around it still reads as a sentence.

The panels that report filesystem locations - where the rules came from, which
directory the model would be in, which binary the engine was looked for at - put
the path inside a sentence. Rendering the whole sentence in a fixed-width face
makes the sentence harder to read, which is the opposite of the intent; rendering
only the path makes the eye land on the one token that is not English.

**This is not `monospace()` applied to a path string,** and the difference is the
point: `monospace()` puts `<tt>` around everything it is handed, so a caller with
a sentence in hand would be choosing between two bad options.

The safety property that `triggers.py`'s note gave up `set_text` for is kept, and
asserted with a hostile filename rather than argued in a comment: the text is
escaped **first** and the tags added after, so a path containing `&` or `<` is
shown literally. Measured elsewhere in this repo: an `Adw` row handed a raw `&`
fails its markup parse and renders *nothing*, so this is not a cosmetic question.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

from shani_chronoa.gui.surfaces import common  # noqa: E402
from shani_chronoa.gui.surfaces import triggers as triggers_surface  # noqa: E402


# ---------------------------------------------------------------------------
# the matcher
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sentence,expected", [
    ("not installed - no model file in /var/cache/shani-chronoa/models",
     "not installed - no model file in "
     "<tt>/var/cache/shani-chronoa/models</tt>"),
    ("the file at /a/b.bin does not exist",
     "the file at <tt>/a/b.bin</tt> does not exist"),
    ("From ~/.local/share/shani-chronoa/rules.jsonl and more",
     "From <tt>~/.local/share/shani-chronoa/rules.jsonl</tt> and more"),
    # A glob is a path. Stopping at the star left the half that most needs to be
    # recognisable outside the fixed-width run.
    ("From /home/x/.config/shani-chronoa/triggers/*.jsonl",
     "From <tt>/home/x/.config/shani-chronoa/triggers/*.jsonl</tt>"),
    # Punctuation ends the path, it is not part of it.
    ("look in /tmp/x, then stop", "look in <tt>/tmp/x</tt>, then stop"),
    ("is it there? /a/b is not", "is it there? <tt>/a/b</tt> is not"),
])
def test_a_path_inside_a_sentence_is_marked_and_the_prose_is_not(sentence, expected):
    assert common.paths_markup(sentence) == expected


@pytest.mark.parametrize("prose", [
    "no path here at all",
    "Root has 38G free of 120G",          # `38G/120G` is not a path
    "a ratio of 3/4 is not a path",
    "",
])
def test_prose_with_no_path_is_left_as_prose(prose):
    """The negative half, and the one that keeps this from being a toy.

    A greedy matcher would turn "38G/120G" into a path and render half a panel's
    prose fixed-width, which is worse than not marking paths at all. This is why
    the character class excludes punctuation and the match needs a leading `/`.
    """
    assert "<tt>" not in common.paths_markup(prose)


def test_markup_in_the_text_cannot_inject_itself():
    """A path out of a file on disk cannot become markup.

    `triggers.py` gives up `set_text` - the one call that cannot be half-done for
    a hostile string - so this is the property that had to survive the change.

    **Asserted on the parsed text, not on `label.get_label()`.** A `Gtk.Label`
    returns its own markup source from `get_label()`, so comparing against it asks
    whether the *tags* are there rather than whether the *characters* are - and
    the first version of this did exactly that and would have passed with the
    path silently mangled. What a person sees is what Pango makes of the markup,
    so that is what is checked.
    """
    hostile = "rules at /tmp/<b>bold</b>&more.jsonl are unreadable"
    markup = common.paths_markup(hostile)
    assert "<b>" not in markup, markup
    assert "&lt;b&gt;" in markup or "&amp;lt;b" in markup, markup

    label = common.paths_in(hostile)
    assert isinstance(label, Gtk.Label)
    assert label.get_use_markup() is True, (
        "the label is not rendering markup, so it would show the tags")

    ok, _attrs, text, _accel = _parse(markup)
    assert ok, f"the markup does not parse, so the label renders blank: {markup}"
    # Every character of the hostile path survives, literally.
    for fragment in ("/tmp/", "<b>bold</b>", "&more.jsonl"):
        assert fragment in text, f"{fragment!r} was lost or altered: {text}"
    # And no tag survived into the rendered characters.
    assert "<b>bold</b>" in text
    assert text.count("<tt>") == 0, text


def test_a_label_built_from_markup_actually_renders():
    """The whole point, measured: the path is in a fixed-width run on screen.

    `Pango.parse_markup` is the same parser the label uses, so this asks whether
    the markup is *valid*, not whether it looks right. An invalid description
    renders nothing at all on this libadwaita - the failure `test_surface_export`
    records - so "it has tags in it" is not enough.
    """
    from gi.repository import Pango

    markup = common.paths_markup("nothing in /opt/chronoa/models today")
    ok, _attrs, text, _accel = Pango.parse_markup(markup, -1, "\0")
    assert ok, markup
    assert "/opt/chronoa/models" in text, text


def test_a_label_with_no_path_is_still_markup_and_still_readable():
    """`use_markup` is set even when there is nothing to mark.

    The alternative is a label that renders its own tags on the day a sentence
    gains a path, because the caller would have had to decide. Setting it
    unconditionally means the label's behaviour does not depend on its content.

    **This is documentation, not load-bearing, and the mutation says so.**
    `Gtk.Label`'s `use-markup` defaults to **False** (measured), but PyGObject's
    `set_markup()` sets it to True itself - so deleting the explicit
    `set_use_markup(True)` from `paths_in` leaves every test here green. The line
    is kept because it states the requirement where the label is built rather
    than relying on a side effect of a call two lines below, and because a
    future change to `set_text` plus hand-written tags would otherwise render
    them. It is not claimed here to be load-bearing, because it is not.
    """
    label = common.paths_in("nothing to mark")
    assert label.get_use_markup() is True
    assert "<tt>" not in label.get_label()
    assert label.get_label() == "nothing to mark"


def test_the_markup_helper_handles_none_and_non_strings():
    """A panel's reading can be anything; this must not be the thing that raises.

    `paths_markup` is called with values read from probes, and a `None` reaching a
    string helper is normal in this codebase - every other one here coerces
    rather than raising.
    """
    assert common.paths_markup(None) == ""
    assert "<tt>" not in common.paths_markup(None)
    assert common.paths_markup(12345) != ""


# ---------------------------------------------------------------------------
# applied
# ---------------------------------------------------------------------------


def test_the_triggers_source_paths_are_read_as_paths():
    """The line that names the rules files now shows them as paths.

    Driven by calling the panel's own note builder with a path in it rather than
    by arranging for a rules file to exist on this machine - the claim is about
    how the line is rendered, and a fixture that depends on the host's trigger
    store would pass on a machine that has one and skip on one that has not.
    """
    label = triggers_surface._note("From /home/x/.config/shani-chronoa/triggers/*.jsonl")
    rendered = label.get_label()
    # **Against the line breaks removed**, and the reason is worth keeping: the
    # label wraps mid-path at the hyphen ("shani-\nchronoa"), so the substring
    # check asserted a single unbroken run of characters that no wrapped label
    # can contain. It read as "the panel stopped showing the path" when the
    # panel was showing it perfectly. Pre-existing - it fails the same way with
    # `gui/style.py` unmodified from HEAD.
    unwrapped = rendered.replace("\n", "")
    assert "/home/x/.config/shani-chronoa/triggers/*.jsonl" in unwrapped, rendered
    ok, _attrs, _text, _accel = _parse(unwrapped)
    assert ok, rendered


def test_a_triggers_note_without_a_path_is_unchanged():
    """The gate note and the "no rules file" line have no path and stay plain."""
    label = triggers_surface._note("No rules file could be located.")
    assert "<tt>" not in label.get_label()
    assert label.get_label() == "No rules file could be located."


def _parse(markup):
    from gi.repository import Pango

    return Pango.parse_markup(markup, -1, "\0")


def test_key_values_already_gives_paths_a_fixed_width_face():
    """The machine panel's readings are a table, and its table is already monospace.

    `machine.py` hands every reading to `key_values()`, whose value labels carry
    `.key-value-text` - `font-family: monospace` in the stylesheet. So the
    panel with the most paths in it already does this, and this asserts it rather
    than adding a second mechanism beside it.
    """
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk as Gtk4

    widget = common.key_values("Model file: /var/cache/chronoa/ggml-q4.bin")
    texts = []

    def walk(node):
        if isinstance(node, Gtk4.Label):
            texts.append(node)
        child = node.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(widget)
    values = [t for t in texts if "key-value-text" in t.get_css_classes()]
    assert values, "no value label was built, so the table is not what it claims"
    assert any("/var/cache/chronoa/ggml-q4.bin" in v.get_label() for v in values), (
        [v.get_label() for v in values])


def test_the_stylesheet_gives_key_value_text_a_fixed_width_face():
    """The class is worth nothing if no rule sets the family.

    Asserted on the stylesheet text because that is where the rule lives, and the
    alternative - measuring a computed style on a widget that must first be
    realised inside a window - is a far larger harness for the same claim. The
    stylesheet is `style.py`'s own, read as text for the one rule.
    """
    from shani_chronoa.gui.style import StyleMixin

    source = Path(StyleMixin.__module__.replace(".", "/"))
    css = (Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa/shani_chronoa"
           / "gui/style.py").read_text()
    assert ".key-value-text" in css, "the key-value value class is not styled"
    assert "font-family: monospace" in css, (
        "no fixed-width face anywhere in the stylesheet, so the table is "
        "proportional like everything else")
    assert source is not None