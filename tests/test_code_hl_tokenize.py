"""The token stream is lossless, and it is the same stream the markup is made of.

`code_hl.tokenize()` exists because `highlight()`'s Pango markup was the only
public output, so a renderer outside this module had no way to colour a
`Gtk.TextBuffer` without parsing markup back into tokens. The editor surface is
the consumer that forced it into being, and it needs two guarantees:

**1. Losslessness.** Concatenating the token texts must reproduce the input
byte for byte. A lexer that drops a character is invisible in markup - the span
still renders, one character short - so every test here asserts the join, not
the colours.

**2. One scanner, not two.** `highlight()` and `tokenize()` must be reading the
same stream. If they drift, the transcript and the editor colour the same file
differently and nothing fails. `test_markup_is_the_stream_plus_a_palette` pins
that by rebuilding `highlight()`'s output from `tokenize()` and asking for an
exact match, which is the assertion that fails the day someone edits one loop
and not the other.

**The controls.** Each test carries a case that must fail if the feature breaks,
because "the join equals the input" is satisfied by a lexer that returns
`[(None, code)]` for everything - a highlighter that colours nothing and is
still perfectly lossless. So `test_a_real_language_actually_produces_kinds`
asserts *kinds* appear, and `test_unknown_language_is_one_untouched_token` is
the deliberate opposite: exactly one token, and its kind `None`, so a lexer that
started guessing on unknown input fails rather than passing by being eager.
"""

import sys

import pytest

pytest.importorskip("gi")
gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

Gtk.init()

sys.path.insert(0, "usr/lib/shani-chronoa")

from shani_chronoa import markdown_lite  # noqa: E402
from shani_chronoa.gui import code_hl  # noqa: E402

SAMPLES = {
    "python": 'import os\n\n\ndef f(x):\n    """doc"""\n    return x + 1  # note\n',
    "bash": '#!/bin/sh\nfor i in 1 2 3; do\n  echo "$i"\ndone\n',
    "json": '{"a": 1, "b": [true, null], "c": "s"}\n',
    "yaml": "a: 1\nb: 'two'\n# comment\n",
    "sql": "SELECT * FROM t WHERE a = 'x' AND b > 3;\n",
    "diff": "--- a\n+++ b\n-old\n+new\n context\n",
}

#: The sample every language test runs on. A docstring is in it deliberately:
#: see `test_a_docstring_does_not_swallow_the_rest_of_the_file`.
WITH_DOCSTRING = SAMPLES["python"]


@pytest.mark.parametrize("language", sorted(SAMPLES))
def test_join_reproduces_the_input_byte_for_byte(language):
    """The invariant the markup cannot show you."""
    code = SAMPLES[language]
    joined = "".join(text for _kind, text in code_hl.tokenize(code, language))
    assert joined == code, (
        "the token stream is lossy: the editor would show a file that is not "
        "the file on disk, and the markup would not have shown it")


@pytest.mark.parametrize("language", sorted(SAMPLES))
def test_a_real_language_actually_produces_kinds(language):
    """The control for the test above.

    A lexer that returned `[(None, code)]` for everything passes
    `test_join_reproduces_the_input_byte_for_byte` perfectly. So each language
    has to hand back at least one *typed* token, or this whole file is testing
    a highlighter that does nothing.
    """
    kinds = {kind for kind, _text in code_hl.tokenize(SAMPLES[language], language)
             if kind is not None}
    assert kinds, (
        f"{language} produced no typed tokens - the stream is lossless because "
        "it is a single untouched token, not because the scanner works")


def test_every_token_kind_is_one_this_module_defines():
    """An unknown kind would KeyError in `palette()` at the consumer."""
    for language, code in SAMPLES.items():
        for kind, _text in code_hl.tokenize(code, language):
            assert kind is None or kind in code_hl._KINDS, (
                f"{language} emitted kind {kind!r}, which no palette has a "
                "colour for - the editor would raise KeyError on this file")


def test_unknown_language_is_one_untouched_token():
    """`highlight()` promises unchanged input for an unknown language.

    That promise is worth keeping in the stream too: the editor colours a file
    whose extension it has no table entry for, and half-right colouring on a
    language it does not know reads as *knowing* it.
    """
    code = "def f(x):\n    return x  # not a known language\n"
    for name in ("brainfuck", "", None, "python3-not-real"):
        tokens = code_hl.tokenize(code, name)
        assert tokens == [(None, code)], (
            f"unknown language {name!r} was tokenised as {tokens!r} - it must "
            "come back as one untyped token, or the editor colours a language "
            "this table has never heard of")


def test_a_known_alias_is_recognised():
    """The other side of the same contract, and the reason the list above is short.

    `ALIASES` maps `py`, `ts`, `rs`, `sh` and thirty more onto real languages,
    and the editor maps file extensions onto language names. If an alias stopped
    resolving, every `.ts` and `.rs` file would silently lose its colouring -
    and `test_unknown_language_is_one_untouched_token` would still pass, because
    the failure looks exactly like "an unknown language comes back untouched".
    """
    for alias, canonical in code_hl.ALIASES.items():
        assert code_hl._canonical(alias) == canonical, (
            f"alias {alias!r} no longer resolves to {canonical!r}")
    code = "def f(x):\n    return x\n"
    assert code_hl.tokenize(code, "py") == code_hl.tokenize(code, "python")


def test_empty_input_yields_no_tokens():
    """An empty file must not produce a phantom zero-length token."""
    assert code_hl.tokenize("", "python") == []


def test_markup_is_the_stream_plus_a_palette():
    """The anti-drift assertion: one scanner, two renderers.

    `highlight()` is rebuilt here out of `tokenize()`'s public output plus the
    exported palette. If someone edits `_scan` handling in one of the two and
    not the other, the two disagree and this fails - which is the whole reason
    `palette()` is exported rather than left as a second private copy.
    """
    for dark in (True, False):
        table = code_hl.palette(dark)
        for language, code in SAMPLES.items():
            rebuilt = "".join(
                markdown_lite.escape(text) if kind is None else
                f'<span foreground="{table[kind]}">'
                f'{markdown_lite.escape(text)}</span>'
                for kind, text in code_hl.tokenize(code, language))
            assert rebuilt == code_hl.highlight(code, language, dark=dark), (
                f"{language} (dark={dark}): the markup and the token stream have "
                "diverged - one of them is no longer reading the other")


def test_a_docstring_does_not_swallow_the_rest_of_the_file():
    """A regression for a bug this file exists because it was invisible.

    The triple-quote branch used to `break` out of its `for` and then **fall
    through** to the single-quote scan below, which still saw the stale `ch` -
    the `"` of a triple quote that had already been consumed. So a docstring coloured
    everything after it as one string:

        keyword  'return'      number  '1'      comment  '# note'
    became
        string   '\\n    return x + 1  # note\\n'

    This matters far more now than it did before. In the transcript it looked
    like a slightly wrong colour; in `gui/surfaces/editor.py` it means **open
    any file with a docstring and the rest of it is one green blob**. It went
    unnoticed because a lexer that colours too much is indistinguishable from
    one that colours correctly if you only look at colours - so the assertions
    have to be about the *kinds* after the docstring, not about the join, which
    was correct the whole time.
    """
    tokens = code_hl.tokenize(WITH_DOCSTRING, "python")
    start = [i for i, (_k, text) in enumerate(tokens)
             if text == '"""doc"""'][0] + 1
    after = tokens[start:]
    kinds = {kind for kind, _text in after if kind is not None}

    assert code_hl.COMMENT in kinds, (
        "the comment after the docstring is not a comment token - the "
        f"triple-quote branch is swallowing the rest of the file again: {after}")
    assert code_hl.KEYWORD in kinds, (
        f"`return` after the docstring was not recognised as code: {after}")
    assert code_hl.NUMBER in kinds, (
        f"the `1` after the docstring was not recognised as a number: {after}")

    # And the swallow is visible as its own shape. Asserting the kinds alone
    # would still pass if the scanner coloured only *part* of the tail as a
    # string, so the shape is pinned too: the text after the docstring holds no
    # string literal at all, so a `string` token there is the swallow - whole or
    # partial - and nothing else.
    assert code_hl.STRING not in kinds, (
        "text after the docstring arrived as a string token - the "
        f"triple-quote branch is swallowing the rest of the file: {after}")
    assert len(after) > 1, (
        "everything after the docstring came back as one token instead of "
        f"code: {after}")


def test_palette_covers_every_kind_and_differs_by_scheme():
    """A missing entry would be a KeyError in the editor, on first paint."""
    light = code_hl.palette(dark=False)
    dark = code_hl.palette(dark=True)
    assert set(light) == set(code_hl._KINDS), (
        "the light palette is missing a kind the scanner can emit")
    assert set(dark) == set(code_hl._KINDS)
    # They must not be the same table - a control that passes either way is
    # not a control.
    differing = [k for k in code_hl._KINDS if light[k] != dark[k]]
    assert differing, (
        "the light and dark palettes are identical, so this test cannot tell "
        "them apart - `palette()` would return the wrong one for a dark window "
        "and nothing would notice")


def test_palette_asks_gtk_when_not_told(monkeypatch):
    """`None` means "what is the window actually drawing with".

    The same rule `highlight()` follows, and for the same reason: guessing from
    the environment is how the transcript and the editor end up disagreeing.
    """
    monkeypatch.setattr(code_hl, "_is_dark", lambda: True)
    assert code_hl.palette() == code_hl.palette(dark=True)
    monkeypatch.setattr(code_hl, "_is_dark", lambda: False)
    assert code_hl.palette() == code_hl.palette(dark=False)


def test_palette_returns_a_copy_so_a_caller_cannot_corrupt_the_table():
    """The editor hands this to a per-buffer tag builder.

    A shared mutable dict would mean one surface's bug silently recolours every
    other surface for the rest of the process.
    """
    got = code_hl.palette(dark=False)
    got[code_hl.COMMENT] = "#000000"
    assert code_hl.palette(dark=False)[code_hl.COMMENT] != "#000000"