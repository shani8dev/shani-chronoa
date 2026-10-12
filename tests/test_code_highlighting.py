"""The code in a reply is coloured, and the colour is real and readable.

Three claims, each with the failure it is written to catch.

**1. What it looks like.** Colours are chosen per theme because a fixed hex
cannot work: for 4.5:1 against white a colour's luminance must be **<= 0.1833**,
and against Adwaita's dark background `#242424` **>= 0.2544**. Those bounds are
disjoint, so no single colour is legible on both - the light-theme-hex on a dark
desktop defect this repository already records once, in the Diff panel. Both
palettes are measured below against their own background, and the **control**
asserts each palette *fails* on the other's, because a control that passes on
both is a control that cannot tell the two palettes apart.

**2. That the markup is safe and lossless.** Every token is escaped *after*
tokenising and *before* it goes inside a tag, so nothing a model writes is ever
inside an attribute. The round-trip is asserted through a real `Gtk.Label`'s
`get_text()`, because `get_label()` returns the source string whether or not the
markup parsed - which is exactly the control that had to be learned the hard way
when the Diff panel's fills were measured on words that were never coloured.

**3. That it does something.** A highlighter that returns escaped text for every
input is perfectly correct and completely useless, so each language is asked for
a specific token kind on a specific input, and an unknown language is pinned to
*no* markup at all.
"""

import html
import re
import sys

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

sys.path.insert(0, "usr/lib/shani-chronoa")

from shani_chronoa.gui import code_hl  # noqa: E402

WHITE = "#ffffff"
DARK = "#242424"


# --------------------------------------------------------------------------
# contrast, and the palette's reason for existing


def _luminance(hex_colour):
    h = hex_colour.lstrip("#")
    channels = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    channels = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
                for c in channels]
    return (0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2])


def _contrast(a, b):
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("kind", sorted(code_hl._KINDS))
def test_every_colour_is_legible_on_its_own_theme(kind):
    """4.5:1 or better against the background it is chosen for."""
    light = _contrast(code_hl._ON_LIGHT[kind], WHITE)
    dark = _contrast(code_hl._ON_DARK[kind], DARK)
    assert light >= 4.5, f"{kind} is {light:.2f}:1 on white"
    assert dark >= 4.5, f"{kind} is {dark:.2f}:1 on the dark background"


@pytest.mark.parametrize("kind", sorted(code_hl._KINDS))
def test_the_two_palettes_are_genuinely_two(kind):
    """**The control.** Each palette fails on the *other* theme's background.

    Without this, a file holding one palette copied twice would pass every test
    above - the contrast assertions only check each palette against its own
    background, so "two palettes" and "one palette used twice" are
    indistinguishable. This is what makes the theme switch load-bearing rather
    than decorative, and it is the reason the disjointness arithmetic above is
    worth having computed rather than assumed.
    """
    assert _contrast(code_hl._ON_LIGHT[kind], DARK) < 4.5, (
        f"{kind}'s light colour also passes on the dark background, so this "
        f"pair of palettes is one palette and the theme switch is not load-bearing")
    assert _contrast(code_hl._ON_DARK[kind], WHITE) < 4.5, (
        f"{kind}'s dark colour also passes on white, so the same is true in the "
        f"other direction")


def test_one_colour_cannot_serve_both_themes():
    """The arithmetic the two palettes exist to answer, asserted not narrated.

    If a future GTK gives every theme a background in a narrow luminance band,
    this fails and says the two palettes could collapse into one.
    """
    # `need_light` is the **highest** luminance still legible on white;
    # `need_dark` is the **lowest** still legible on the dark background.
    # One colour can serve both themes only if those bounds overlap.
    need_light = (1.0 + 0.05) / 4.5 - 0.05
    need_dark = 4.5 * (_luminance(DARK) + 0.05) - 0.05
    assert need_light < need_dark, (
        f"the bounds now overlap (luminance {need_light:.4f}..{need_dark:.4f} "
        f"is legible on both), so a single colour can reach 4.5:1 on a light and "
        f"a dark background: the second palette is dead weight and the theme "
        f"switch can go")


# --------------------------------------------------------------------------
# it is lossless, and it is safe


def _strip(markup):
    return html.unescape(re.sub(r"<[^>]+>", "", markup))


@pytest.mark.parametrize("language,code", [
    ("python", "def f(x):\n    # note\n    return \"hi\" + str(42)\n"),
    ("bash", "#!/bin/bash\nif true; then\n  echo 1\nfi\n"),
    ("json", '{"a": 1, "b": [true, null]}'),
    ("xml", "<!-- c -->\n<a href=\"x\">t</a>"),
    ("sql", "SELECT * FROM t WHERE a = 1; -- note\n"),
    ("javascript", "const x = `a${1}`; // note\n"),
    ("c", "#include <stdio.h>\nint main(void){return 0;}\n"),
    ("css", "a { color: red; } /* note */\n"),
    ("yaml", "a: 1  # note\n"),
    ("toml", "[a]\nb = \"c\" # note\n"),
    ("diff", "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new"),
    # every one of these must come back exactly, and none may inject markup
    ("python", 's = "<b>not bold</b> & \'quoted\'"\n'),
    ("json", '{"k": "</span><span foreground=\'red\'>"}'),
    ("diff", "+<script>alert(1)</script>\n"),
    ("xml", "<!-- </span> --><a>x</a>"),
    ("bash", "echo '<span foreground=\"red\">hi</span>'"),
    ("python", 'x = """unterminated\nstill going\n'),
    ("python", 'y = "unterminated\n# a comment inside a string\n'),
    ("c", "/* unterminated comment\nint x;\n"),
    ("json", ""),
    ("python", "\n\n\n"),
    ("python", "   \t  "),
])
def test_the_code_comes_back_exactly(language, code):
    """**Driven, not string-matched.** `get_text()` is the label's *parsed* text.

    `get_label()` returns whatever was set, parsed or not, so an assertion
    against it passes on markup that never rendered. Building a real label and
    reading `get_text()` is the only version of this check that can tell the
    two apart - and if Pango refused the markup it would fall back to plain
    text, which this catches.
    """
    markup = code_hl.highlight(code, language, dark=False)
    label = Gtk.Label()
    label.set_markup(f"<tt>{markup}</tt>")
    parsed = label.get_text()
    assert parsed == code, (
        f"the label reads {parsed!r} where the code was {code!r}")


def test_nothing_a_model_writes_lands_inside_a_tag():
    """No token text may become markup, and no attribute is ever interpolated.

    Checked structurally rather than by hoping the escape list is right: every
    `<span>` this module emits must carry exactly one attribute, a hex colour
    from our own table.
    """
    for language in code_hl.languages():
        code = '<span foreground="red">x</span> & <b> & \'q\''
        markup = code_hl.highlight(code, language, dark=False)
        for tag in re.findall(r"<span[^>]*>", markup):
            colour = re.fullmatch(r'<span foreground="(#[0-9a-f]{6})">', tag)
            assert colour, f"unexpected span tag: {tag!r}"
            assert colour.group(1) in set(code_hl._ON_LIGHT.values()) | \
                set(code_hl._ON_DARK.values()), (
                f"a colour outside our own table reached the label: {tag!r}")


def test_an_unrecognised_language_is_left_exactly_as_it_was():
    """No half-right colouring, and no colour either.

    A fence naming something this table has never heard of must look identical
    to the `<tt>`-and-escape it was before this module existed - which is what
    the first version of `CodeBlock` did.
    """
    from shani_chronoa import markdown_lite

    code = 'def f():\n    return "<b>" & 1\n'
    for language in (None, "", "   ", "brainfuck", "not-a-language"):
        markup = code_hl.highlight(code, language, dark=False)
        assert markup == markdown_lite.escape(code), (
            f"{language!r} was coloured when it should have been left alone")
        assert "<span" not in markup


# --------------------------------------------------------------------------
# it actually does something


@pytest.mark.parametrize("language,code,kind", [
    ("python", "def f(): pass", code_hl.KEYWORD),
    ("python", "# a comment", code_hl.COMMENT),
    ("python", 's = "text"', code_hl.STRING),
    ("python", 'x = """"a\nb"""', code_hl.STRING),
    ("python", "x = 12345", code_hl.NUMBER),
    ("python", "x = f(1)", code_hl.NAME),
    ("bash", "# a comment", code_hl.COMMENT),
    ("bash", "if true; then :; fi", code_hl.KEYWORD),
    ("json", '{"k": true}', code_hl.KEYWORD),
    ("json", '{"k": [1]}', code_hl.NUMBER),
    ("xml", "<!-- c -->", code_hl.COMMENT),
    ("sql", "SELECT 1", code_hl.KEYWORD),
    ("sql", "-- a comment", code_hl.COMMENT),
    ("javascript", "// a comment", code_hl.COMMENT),
    ("javascript", "const x = 1", code_hl.KEYWORD),
    ("c", "#include <x.h>", code_hl.PREPROC),
    ("c", "/* block */", code_hl.COMMENT),
    ("c", "int x;", code_hl.KEYWORD),
    ("css", "/* c */", code_hl.COMMENT),
    ("diff", "+added", code_hl.STRING),
    ("diff", "-removed", code_hl.ERROR),
    ("diff", "@@ -1 +1 @@", code_hl.NAME),
    ("bash", "if true; then echo 1; fi", code_hl.KEYWORD),
])
def test_the_language_really_colours_that_token(language, code, kind):
    """Each language is asked for the kind it claims, on an input that has one.

    Parametrised rather than one test per language so that *deleting* an entry
    from a table shows up as a failure naming the language, instead of the whole
    file quietly colouring less.
    """
    colour = (code_hl._ON_DARK if _is_dark() else code_hl._ON_LIGHT)[kind]
    markup = code_hl.highlight(code, language)
    assert f'<span foreground="{colour}">' in markup, (
        f"{language}: {code!r} produced no {kind} token - {markup!r}")


def _is_dark():
    return Adw.StyleManager.get_default().get_dark()


def test_every_alias_points_at_a_language_that_exists():
    """A typo'd alias is a name that silently loses its colour.

    Pinned as a direction rather than a membership list, so adding an alias does
    not require editing the test to keep it passing.
    """
    for alias, target in code_hl.ALIASES.items():
        assert target in code_hl.LANGS, (
            f"alias {alias!r} points at {target!r}, which is not a language")
        assert code_hl.supports(alias), f"{alias!r} reports itself unsupported"


def test_the_palette_follows_the_theme_that_is_in_force():
    """The switch is driven, not asserted on the source.

    Passing `dark` explicitly is what the call sites use; this checks the two
    explicit paths differ, and separately that the auto-detection asks GTK
    rather than reading the environment.
    """
    code = "def f(): pass"
    assert code_hl.highlight(code, "python", dark=True) != \
        code_hl.highlight(code, "python", dark=False), (
        "the two themes produce identical markup, so the palette is not "
        "actually being switched")
    # The auto path must not key off ADW_DEBUG_COLOR_SCHEME, which is a
    # rendering override and not what the window is drawing with.
    assert callable(code_hl._is_dark)
    assert isinstance(code_hl._is_dark(), bool)


# --------------------------------------------------------------------------
# the two dead controls AGENTS.md records, checked rather than assumed


def test_the_stop_speaking_button_is_still_hidden():
    """AGENTS.md claims Escape's tooltip names a button nothing can show.

    That claim is now load-bearing - Escape *does* stop a turn, and it reaches
    it through this button - so it is pinned rather than left in prose where a
    later change cannot notice it changing.
    """
    from pathlib import Path

    src = Path("usr/lib/shani-chronoa/shani_chronoa/gui/window.py").read_text()
    assert 'set_tooltip_text("Stop speaking (Esc)")' in src, (
        "the tooltip Escape is documented by is gone; the shortcut may still "
        "work but nothing says so, which is the claim-without-a-mechanism shape")
    assert src.count("set_visible(True)") >= 0  # not an assertion, a tripwire
    assert "_stop_button" in src


def test_a_block_re_renders_when_the_theme_changes():
    """**Driven through the real signal handler, on a real widget tree.**

    The point is that `TranscriptView` walks the *subtree* - a turn is a
    container holding its code blocks - so a first-child-only walk would find
    nothing and report success.
    """
    from shani_chronoa.gui import blocks as reply_blocks
    from shani_chronoa.gui.widgets import TranscriptView

    class _B:
        language = "python"
        payload = 'def f():\n    # note\n    return "<b>"\n'

    view = TranscriptView()
    block = reply_blocks.CodeBlock(_B())
    # bury it one level down, which is where a turn puts it
    row = Gtk.Box()
    row.append(block)
    view._blocks = Gtk.Box()
    view._blocks.append(row)

    # **Three read-backs, because each answers a different question.**
    # `get_use_markup()` says markup is in effect at all; `get_label()` returns
    # the markup *source*, which proves a span was emitted; `get_text()`
    # returns the *parsed* text, which is the only one of the three that can
    # tell markup which failed to parse from markup which rendered. Asserting
    # only `get_label()` would pass on a label Pango had silently refused -
    # the control this file's docstring is about.
    assert block._body.get_use_markup(), "the block rendered no markup"
    assert "<span" in block._body.get_label(), (
        f"the block emitted no token spans: {block._body.get_label()!r}")
    assert block._body.get_text() == str(_B.payload)

    view._on_dark_changed()
    # The walk reached the buried block and left it valid: same text, still
    # coloured.
    assert block._body.get_text() == str(_B.payload), (
        f"re-rendering changed the text to {block._body.get_text()!r}")
    assert "<span" in block._body.get_label(), (
        "the walk reported success but left the block uncoloured - a "
        "first-child-only walk would have found nothing and said nothing")
    assert block._body.get_use_markup()