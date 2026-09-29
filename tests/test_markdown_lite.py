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
