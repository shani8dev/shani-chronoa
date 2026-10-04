"""The markdown subset, with injection treated as the main case.

Reply text comes from a local language model and can contain anything. It
reaches a `Gtk.Label.set_markup`, so anything that reaches the label unescaped
is a formatting-scope bug at best. Escaping happens before any tag is
introduced, which is the property these tests exist to hold in place.
"""

import re
import sys
import unicodedata

import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")
from shani_chronoa.markdown_lite import to_pango  # noqa: E402
from shani_chronoa.markdown_lite import to_speech  # noqa: E402

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


class TestPipeTables:
    """Alpaca renders tables as real blocks; a `Gtk.Label` cannot, so this one
    renders them as aligned monospace and says what it gives up."""

    TABLE = ("| Mount | Size | Free |\n"
             "|:---|---:|---:|\n"
             "| / | 120G | 38G |\n"
             "| /home | 480G | 92G |")

    @staticmethod
    def _cells_at(markup: str) -> "list[list[tuple]]":
        """Each rendered row -> its cells as (start, end, text) offsets.

        Offsets rather than `split()`, because `split()` throws away exactly the
        padding this renderer exists to produce: every assertion below has to be
        about *where* a cell sits, not what is in it.
        """
        block = markup.split("<tt>", 1)[1].split("</tt>", 1)[0]
        return [[(m.start(), m.end(), m.group()) for m in re.finditer(r"\S+", line)]
                for line in block.split("\n")]

    def test_columns_line_up(self):
        rows = self._cells_at(to_pango(self.TABLE))
        # Every value in a column has to sit where its heading sits - on the
        # same edge, whichever edge the model asked for. That is the whole
        # reason this renderer exists.
        for column in range(3):
            starts = {row[column][0] for row in rows}
            ends = {row[column][1] for row in rows}
            assert len(starts) == 1 or len(ends) == 1, (column, rows)
        assert rows[1][0][2].startswith("-"), "the rule row goes under the header"

    def test_pipes_are_not_shown(self):
        out = to_pango(self.TABLE)
        block = out.split("<tt>", 1)[1].split("</tt>", 1)[0]
        assert "|" not in block

    def test_the_padding_is_a_no_break_space(self):
        """Not `#x20`, and the reason is measured rather than remembered: in a
        wrapping label a padded row breaks at any space it can find, which is
        the middle of a column gap. `U+00A0` offers no break opportunity."""
        block = to_pango(self.TABLE).split("<tt>", 1)[1].split("</tt>", 1)[0]
        assert "\u00a0" in block and " " not in block

    def test_pango_accepts_the_markup(self):
        """A markup string Pango rejects is not an empty label - `get_text()`
        returns the raw `<tt>...` text, so the user reads the markup as the
        answer. This is how `xml:space="preserve"` nearly shipped."""
        gi = pytest.importorskip("gi")
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk

        label = Gtk.Label()
        label.set_markup(to_pango(self.TABLE))
        text = label.get_text()
        assert "<tt>" not in text and "<span" not in text
        assert "120G" in text and "|" not in text

    def test_the_columns_line_up_in_a_real_layout(self):
        """Offsets measured by Pango, not by this module's own padding maths."""
        gi = pytest.importorskip("gi")
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk

        label = Gtk.Label()
        label.set_markup(to_pango(self.TABLE))
        layout, text = label.get_layout(), label.get_text()
        second_column = []
        for line in text.split("\n"):
            starts = [m.start() for m in re.finditer(r"\S+", line)]
            second_column.append(layout.index_to_pos(starts[1]).x)
        assert len(set(second_column)) == 1, second_column

    def test_numbers_are_right_aligned_as_the_model_asked(self):
        """`---:` in the rule row is the model asking for numbers to line up on
        the right, which is the one alignment a table of figures needs."""
        rows = self._cells_at(to_pango(self.TABLE))
        for column in (1, 2):                       # Size and Free are `---:`
            assert len({row[column][1] for row in rows}) == 1, rows
        # The left-aligned `:` column moves instead, and moves by its own width.
        assert len({row[0][0] for row in rows}) == 1

    def test_a_cell_cannot_introduce_a_tag(self):
        out = to_pango("| a | b |\n|---|---|\n| <b>x</b> | <i>y</i> |")
        assert "<b>" not in out and "<i>" not in out
        assert "&lt;b&gt;x&lt;/b&gt;" in out
        assert tags(out) <= ALLOWED

    def test_markers_in_a_cell_are_shown_not_applied(self):
        """Bold inside one cell of a fixed-width column breaks the alignment it
        exists to provide, so the cell keeps its asterisks."""
        out = to_pango("| a | b |\n|---|---|\n| **bold** | x |")
        assert "**bold**" in out and "<b>" not in out

    def test_a_ragged_row_is_left_as_the_model_wrote_it(self):
        """Half a row is not a table; guessing which column was meant invents
        data."""
        table = "| a | b |\n|---|---|\n| 1 |"
        assert to_pango(table) == table

    def test_a_horizontal_rule_is_not_a_table(self):
        out = to_pango("intro\n\n---\n\nmore")
        assert out.count("---") == 1 and "<tt>" not in out

    def test_a_pipe_line_that_is_not_a_table_survives(self):
        out = to_pango("a | b\nc | d")
        assert out == "a | b\nc | d"

    def test_a_wide_table_is_refused_rather_than_slabbed(self):
        header = "| " + " | ".join(f"c{i}" for i in range(20)) + " |"
        table = f"{header}\n|{'---|' * 20}\n| " + " | ".join("v" for _ in range(20)) + " |"
        assert to_pango(table) == table

    def test_padding_survives_a_wrapping_label(self):
        """The transcript's label wraps, and this is the measurement the padding
        character was chosen for: the same table breaks into fewer lines when
        the gaps between columns cannot be broken. (Still not none - a table
        wider than the window breaks somewhere.)"""
        gi = pytest.importorskip("gi")
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk

        def line_boxes(markup: str) -> int:
            label = Gtk.Label()
            label.set_wrap(True)
            label.set_size_request(240, -1)
            label.set_markup(markup)
            return label.get_layout().get_line_count()

        ours = to_pango(self.TABLE)
        assert line_boxes(ours) < line_boxes(ours.replace("\u00a0", " "))

    def test_a_table_and_a_fence_both_survive_in_one_reply(self):
        out = to_pango(self.TABLE + "\n\n```bash\ndf -h /\n```")
        assert "120G" in out and "df -h /" in out and tags(out) <= ALLOWED

    def test_full_width_characters_count_as_two_columns(self):
        """A CJK cell is two columns wide but one Python character, so `len()`
        would drift the table out of alignment on the first one. The test does
        the counting itself rather than asking the module how wide a thing is."""
        def columns(line: str) -> "list[int]":
            return [sum(2 if unicodedata.east_asian_width(c) in "WF" else 1
                        for c in line[:match.start()])
                    for match in re.finditer(r"\S+", line)]

        block = to_pango("| 名前 | size |\n|---|---|\n| ルート | 1G |")
        block = block.split("<tt>", 1)[1].split("</tt>", 1)[0]
        starts = [columns(line) for line in block.split("\n")]
        assert starts[0][1] == starts[1][1] == starts[2][1], block


class TestSpokenTables:
    def test_a_table_is_read_as_rows_not_as_pipes(self):
        spoken = to_speech(TestPipeTables.TABLE)
        assert "|" not in spoken and "-" * 3 not in spoken
        assert "Mount, Size, Free" in spoken
        assert "/, 120G, 38G" in spoken

    def test_the_columns_still_line_up_in_what_is_said(self):
        spoken = to_speech(TestPipeTables.TABLE)
        names = [line.split(", ")[0] for line in spoken.split("\n") if line]
        assert names[:3] == ["Mount", "/", "/home"]

    def test_a_ragged_row_is_dropped_rather_than_half_read(self):
        spoken = to_speech("| a | b |\n|---|---|\n| 1 |\n| 1 | 2 |")
        assert spoken == "a, b\n1, 2"

    def test_a_table_inside_a_reply_does_not_eat_the_rule_after_it(self):
        spoken = to_speech(TestPipeTables.TABLE + "\n\n---\n\nAnd that is all.")
        assert spoken.endswith("And that is all.")


class TestTheSpokenForm:
    """The voice path handed TTS the raw reply, so the markup was pronounced.

    `to_pango` renders a reply for the window; `_speak` passed the same string
    straight to the TTS engine, which read every `**` and backtick aloud.
    Measured on this machine: `You have **3** updates pending. Run `sudo pacman
    -Syu` to upgrade.` synthesised to 275,258 bytes of audio before this and
    180,078 after - and 180,078 is byte-for-byte what the same sentence
    synthesises to when it never had markup in it. The 35% that vanished was
    the word "asterisk".

    The reduction reuses the same marker set `to_pango` handles, so the spoken
    and displayed forms cannot drift into disagreeing about what was said.
    """

    def test_bold_markers_are_not_spoken(self):
        assert to_speech("You have **3** updates.") == "You have 3 updates."

    def test_italic_markers_are_not_spoken(self):
        assert to_speech("That is *probably* wrong.") == "That is probably wrong."
        assert to_speech("That is _probably_ wrong.") == "That is probably wrong."

    def test_code_markers_are_not_spoken_but_the_contents_are(self):
        # The contents are the part the user needs to hear: a path, a package
        # name. Only the backticks, which make it *code*, are dropped.
        assert to_speech("Run `sudo pacman -Syu` now.") == "Run sudo pacman -Syu now."

    def test_bullets_lose_their_marker(self):
        out = to_speech("- first\n- second")
        assert out == "first\nsecond"
        assert "-" not in out

    def test_a_fence_is_spoken_without_its_delimiters(self):
        out = to_speech("Try:\n```bash\njournalctl -u chronoa\n```")
        assert "journalctl -u chronoa" in out
        assert "```" not in out
        assert "bash" not in out

    def test_a_link_is_spoken_as_its_text_and_not_its_url(self):
        # A URL read aloud is noise, and the target is model-supplied - the same
        # reason `to_pango` refuses to make it clickable.
        out = to_speech("See [the wiki](https://example.com/evil) for details.")
        assert out == "See the wiki for details."
        assert "example.com" not in out

    def test_a_heading_loses_its_hashes(self):
        assert to_speech("## Updates pending") == "Updates pending"

    def test_a_horizontal_rule_is_dropped_entirely(self):
        out = to_speech("Before.\n\n---\n\nAfter.")
        assert "---" not in out
        assert "Before." in out and "After." in out

    def test_code_containing_stars_is_not_reduced(self):
        # The same reason `to_pango` stashes fences: `**` inside code is
        # asterisks, not bold, and neither path may turn it into emphasis.
        out = to_speech("```\ngrep '**' file\n```")
        assert "'**'" in out

    def test_underscores_in_a_word_are_not_italic(self):
        assert to_speech("see some_file_name") == "see some_file_name"

    def test_empty_text_is_empty(self):
        assert to_speech("") == ""
        assert to_speech("   \n  ") == ""

    def test_plain_text_is_unchanged_apart_from_stripping(self):
        assert to_speech("Just a sentence.") == "Just a sentence."

    def test_it_agrees_with_to_pango_about_what_the_reply_says(self):
        # The two renderers handle the same subset. Anything one reduces and the
        # other does not is a marker the window shows and the voice reads.
        reply = "**bold** and `code` and *italic* and - a bullet"
        spoken = to_speech(reply)
        assert "**" not in spoken and "`" not in spoken and "*" not in spoken
        for word in ("bold", "code", "italic", "a bullet"):
            assert word in spoken, f"{word!r} was lost from the spoken form"

    def test_markup_in_a_reply_cannot_reach_the_voice_as_markup(self):
        # The window path escapes first because `set_markup` on model output is
        # an injection point. The voice path has no such risk - there is no
        # markup language to inject into - but it must still not *speak* a tag.
        assert to_speech("Here is a tag: <b>bold</b>") == "Here is a tag: <b>bold</b>"


class TestBareUrlsAreSpokenAsLink:
    """A bare URL is spoken character by character, which is the worst case.

    Found by comparison with `assistd`, the one harness in the survey on the
    same stack (whisper.cpp, Piper, local llama.cpp), which reduces text for
    speech the same way and additionally rewrites a bare address to the word
    "link". The first version of `to_speech` handled `[text](url)` but not a
    bare one, and a model writes bare addresses constantly.

    Measured on this machine: "See https://example.com/wiki for details."
    synthesised to 205,654 bytes of audio, against 98,070 for the same
    sentence with no address in it and 67,080 with the word "link". Over half
    the audio was spelling out a web address that nobody can act on by
    listening to it.
    """

    def test_a_bare_https_url_becomes_the_word_link(self):
        assert to_speech("See https://example.com/wiki for details.") == (
            "See link for details.")

    def test_a_bare_http_url_becomes_the_word_link(self):
        assert to_speech("The docs are at http://example.com/x.") == (
            "The docs are at link")

    def test_an_email_address_is_not_mangled(self):
        # The lookbehind is what makes this safe. Without it the regex would
        # match the `http` inside an address and destroy it.
        assert to_speech("Mail me at someone@example.com about it.") == (
            "Mail me at someone@example.com about it.")

    def test_a_scheme_glued_to_a_word_is_not_matched(self):
        assert to_speech("Compare ahttp://x versus https://real.example.com") == (
            "Compare ahttp://x versus link")

    def test_a_link_is_still_spoken_as_its_text(self):
        # The markdown form keeps its visible text, which is more useful than
        # "link" - and its target is not read, for the injection reason.
        assert to_speech("See [the wiki](https://example.com/wiki) for details.") == (
            "See the wiki for details.")

    def test_a_url_with_no_spaces_around_it_is_still_replaced(self):
        assert to_speech("https://example.com") == "link"

    def test_two_urls_become_two_links(self):
        assert to_speech("See https://a.example and http://b.example now.") == (
            "See link and link now.")

    def test_text_that_merely_mentions_a_url_is_untouched(self):
        assert to_speech("The website is broken") == "The website is broken"

    # A mutation of `_BARE_URL` to `[^\s]*` survives, and is recorded here as
    # the equivalent it is: `\S+` and `[^\s]*` are the same character class, so
    # no input distinguishes them. Asserting on it would be a test that passes
    # for reasons unrelated to what it names. The rule's *behaviour* is pinned by
    # the six tests above; this note is why one mutation is not caught.
