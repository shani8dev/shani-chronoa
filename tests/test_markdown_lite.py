"""The markdown subset, with injection treated as the main case.

Reply text comes from a local language model and can contain anything. It
reaches a `Gtk.Label.set_markup`, so anything that reaches the label unescaped
is a formatting-scope bug at best. Escaping happens before any tag is
introduced, which is the property these tests exist to hold in place.
"""

import re
import sys

import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")
from shani_chronoa.markdown_lite import to_pango  # noqa: E402

# Every tag the module is allowed to emit.
ALLOWED = {"b", "i", "tt", "span", "big", "small", "u", "s", "tt"}


def tags(markup: str) -> set:
    return set(re.findall(r"</?([a-zA-Z]+)", markup))


class TestEscapingIsStructural:
    @pytest.mark.parametrize("hostile", [
        "<b>bold</b>",
        "<i>italic</i>",
        "<tt>code</tt>",
        "<span size='huge'>big</span>",
        "a < b > c & d",
        "&#60;script&#62;",
        "<b>&amp;</b>",
        "</b>stray close",
    ])
    def test_no_tag_survives_from_the_input(self, hostile):
        """The model cannot introduce a tag. Only the ones this module writes
        may appear in the output."""
        out = to_pango(hostile)
        assert tags(out) <= ALLOWED, f"{hostile!r} produced {tags(out) - ALLOWED}"

    def test_ampersand_is_escaped(self):
        assert "&amp;" in to_pango("a & b")
        assert "& " not in to_pango("a & b")

    def test_angle_brackets_are_escaped(self):
        out = to_pango("5 < 6")
        assert "&lt;" in out and "<" not in out.replace("&lt;", "")


class TestTheSubset:
    def test_bold(self):
        assert to_pango("**hi**") == "<b>hi</b>"

    def test_italic_both_markers(self):
        assert to_pango("*hi*") == "<i>hi</i>"
        assert to_pango("_hi_") == "<i>hi</i>"

    def test_inline_code(self):
        assert to_pango("run `ls -la`") == "run <tt>ls -la</tt>"

    def test_bullets_replace_the_hyphen(self):
        out = to_pango("- one\n- two")
        assert "•" in out and "-" not in out.replace("one", "").replace("two", "")

    def test_fenced_code_is_never_interpreted(self):
        """Inside a code fence `**` is two asterisks, not emphasis. Getting this
        wrong would corrupt any pasted shell snippet containing a glob."""
        out = to_pango("```\ngrep **/*.log\n```")
        assert "**/*.log" in out
        assert "<b>" not in out

    def test_unclosed_marker_is_left_alone(self):
        """Better a visible `**` than a swallowed half-sentence."""
        assert to_pango("unclosed **bold") == "unclosed **bold"

    def test_a_link_is_not_turned_into_one(self):
        """A clickable target that came from a model is an injection route
        somewhere the user did not intend."""
        out = to_pango("[click](https://evil.example)")
        assert "href" not in out and "<a" not in out
        assert "click" in out

    def test_underscores_inside_a_word_are_not_italic(self):
        assert to_pango("some_file_name_here") == "some_file_name_here"

    def test_empty_text(self):
        assert to_pango("") == ""

    def test_multiline_preserves_structure(self):
        out = to_pango("first\n\nsecond")
        assert out.count("\n") == 2


class TestRealisticReply:
    def test_a_typical_disk_answer(self):
        out = to_pango(
            "Root has **38G** free of 120G.\n\n"
            "- `/` is 32% full\n"
            "- `/home` is 61% full\n\n"
            "```bash\ndf -h /\n```"
        )
        assert "<b>38G</b>" in out
        assert "•" in out
        assert "<tt>/</tt>" in out
        assert "df -h /" in out
        assert tags(out) <= ALLOWED
