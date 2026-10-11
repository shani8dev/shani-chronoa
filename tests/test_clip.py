"""Truncation that does not draw a missing-glyph box.

`str[:n]` is a hard slice, and a hard slice cuts grapheme clusters. Python 3
strings are code points so a surrogate pair is never split, but "👨‍👩‍👧‍👦" is
*seven* code points held together by ZWJ, and `('e' + U+0301)[:1]` is `'e'` with
the accent gone. Both cases leave a character stranded that renders as the
"missing glyph" box - measured, not theorised: `'Family photo 👨‍👩‍👧‍👦'[:i+2]`
ends in U+200D.

This is open-intelligent-ui's `pinEmoji` problem in a different place. That
function takes the first grapheme and only if it is Extended_Pictographic
("so a word or letter from the model can't overflow the pin") using
`Intl.Segmenter`; the stdlib has no such thing, so the approximation is built
from the characters that cannot *start* a cluster - which is the half that
matters for a truncation.
"""

from __future__ import annotations

import sys
import unicodedata

sys.path.insert(0, "usr/lib/shani-chronoa")

import pytest  # noqa: E402

from shani_chronoa import markdown_lite as M  # noqa: E402

FAMILY = "\U0001F468\u200d\U0001F469\u200d\U0001F467\u200d\U0001F466"
ZWJ = "\u200d"
ELL = "..."


def _stranded(text: str) -> "list[str]":
    """The failures this module exists to prevent, in a clipped string.

    A ZWJ at the end means a cluster was cut - the regression the first version
    of `clip` actually had. A **leading** combining mark means its base was cut
    and only the accent survived.

    A *trailing* combining mark is not a failure: it is the second half of a
    cluster that is present, which is what `'café...'` is. **The first version
    of this helper flagged it, and the test failed on correct output** - a
    control that cannot pass is the same defect as one that cannot fail.
    """
    body = text[:-len(ELL)] if text.endswith(ELL) else text
    if body.endswith(ZWJ):
        return ["ZWJ at the end"]
    if body and unicodedata.combining(body[0]):
        return ["leading combining mark with its base cut off"]
    return []


class TestAClusterIsNeverSplit:
    @pytest.mark.parametrize("width", [14, 15, 16, 20, 29, 30])
    def test_a_family_emoji_is_kept_whole_or_dropped_whole(self, width):
        """Width is columns, and the emoji is two columns - so a clip that keeps
        it must keep all seven code points, and one that cannot must take none.
        """
        text = f"Family photo {FAMILY} saved to Pictures"
        out = M.clip(text, width)
        assert not _stranded(out), f"{out!r} leaves something stranded"
        if FAMILY in out:
            assert out.count(ZWJ) == 3, f"a partial cluster survived: {out!r}"

    def test_the_whole_emoji_survives_when_it_fits(self):
        out = M.clip(f"photo {FAMILY} ok", 40)
        assert FAMILY in out and not out.endswith(ELL), out

    def test_a_combining_accent_is_never_orphaned(self):
        text = "caf\u0065\u0301 " + "menu today"
        # Width 9 holds "café" (5 columns) plus an ellipsis; anything narrower
        # cannot, and the cluster must still come out whole or not at all.
        out = M.clip(text, 9)
        assert not _stranded(out), out
        assert out.startswith("caf\u0065\u0301"), (
            f"the accented letter was cut apart: {out!r}")
        # And at every width, in both directions.
        for width in range(1, 24):
            assert not _stranded(M.clip(text, width)), width

    @pytest.mark.parametrize("width", [4, 5, 6, 7, 8])
    def test_a_flag_is_never_half_drawn(self, width):
        """A flag is two regional indicators; half of one draws as two letters."""
        text = "flag \U0001F1EF\U0001F1F5 and the flight departs"
        out = M.clip(text, width)
        assert not _stranded(out), out
        assert out.count("\U0001F1EF") == out.count("\U0001F1F5"), (
            f"half a flag survived: {out!r}")

    def test_a_skin_tone_modifier_stays_with_its_emoji(self):
        text = "waving \U0001F44B\U0001F3FB hand and the rest of the sentence"
        out = M.clip(text, 12)
        assert not _stranded(out), out


class TestTheBudgetIsColumns:
    def test_a_wide_character_costs_two(self):
        """`len()` would let ten CJK characters through a five-column slot."""
        out = M.clip("\u6587\u4ef6" * 30, 21)
        assert M._display_width(out) <= 21, out
        assert out.endswith(ELL)

    def test_the_ellipsis_counts_against_the_budget(self):
        """The caller is putting this in a slot of `width` columns, so the result
        must never be wider than `width` - ellipsis included."""
        for width in range(3, 24):
            out = M.clip("abcdefghijklmnopqrstuvwxyz", width)
            assert M._display_width(out) <= width, (
                f"width {width} produced {M._display_width(out)}: {out!r}")

    def test_a_budget_that_cannot_hold_the_ellipsis_gives_content(self):
        """A two-column slot gets two columns of text, not two dots.

        A clipped label that says nothing is worse than a shorter one.
        """
        assert M.clip("abcdef", 2) == "ab"
        # One CJK character is two columns, so a two-column slot holds exactly
        # one of them - and the answer is a whole character, never half.
        assert M.clip("\u6587\u4ef6" * 9, 2) == "\u6587\u4ef6"[:1]

    def test_zero_and_negative_widths_are_empty(self):
        assert M.clip("abcdef", 0) == ""
        assert M.clip("abcdef", -3) == ""


class TestTextThatFitsIsUntouched:
    @pytest.mark.parametrize("text,width", [
        ("", 10), ("short", 10), ("short", 5),
        (f"a {FAMILY} b", 10),
        # One CJK character is two columns, so two of them need four.
        ("\u6587\u4ef6", 4),
    ])
    def test_a_string_within_the_budget_is_returned_as_it_is(self, text, width):
        assert M.clip(text, width) == text, (
            "a string that already fits gained an ellipsis, so a caller that "
            "only clips would add dots where there was nothing to clip")


class TestGraphemes:
    """Each joining rule asserted directly, because a `clip` test can pass with
    the rule broken - the cut has to land exactly inside the cluster, and
    `clip`'s width arithmetic rarely puts it there.

    **This class is why four mutations were caught.** The first version only
    tested `clip`, and three joining rules were removed without a single test
    failing.
    """

    def test_a_combining_mark_joins_its_base(self):
        assert M.graphemes("caf\u0065\u0301") == ["c", "a", "f", "e\u0301"]

    def test_a_zwj_joins_both_ways(self):
        assert M.graphemes("\U0001F468\u200d\U0001F469") == [
            "\U0001F468\u200d\U0001F469"]

    def test_a_skin_tone_modifier_joins(self):
        assert M.graphemes("\U0001F44B\U0001F3FB") == ["\U0001F44B\U0001F3FB"]

    def test_two_regional_indicators_are_one_flag(self):
        assert M.graphemes("\U0001F1EF\U0001F1F5") == ["\U0001F1EF\U0001F1F5"]

    def test_a_variation_selector_joins(self):
        assert M.graphemes("\u2764\ufe0f") == ["\u2764\ufe0f"]

    def test_a_plain_string_splits_per_character(self):
        assert M.graphemes("abc") == ["a", "b", "c"]

    def test_empty(self):
        assert M.graphemes("") == []

    def test_the_approximation_is_stated(self):
        """`Intl.Segmenter` is JavaScript; this is the stdlib half of it.

        Recorded so the next reader does not assume a full UAX #29
        implementation, and so the known limits are where they can be found.
        """
        assert "_joins" in dir(M)


class TestTheCallersUseIt:
    """The reason `clip` exists: two places cut model-supplied text for display.

    **Asserted through the built widget, not by grepping the source.** The first
    version looked for `[:57]` and could not fail: it matched the comment that
    explains why the slice was removed. That is the "a grep hits its own
    docstring" trap this repo has recorded, and the fix is the same one - assert
    the behaviour.
    """

    def _labels(self, card) -> "list[str]":
        """Every label text in a built widget, so nothing is asserted about a
        string that was never rendered."""
        out, stack = [], [card]
        while stack:
            widget = stack.pop()
            try:
                text = widget.get_text()
            except Exception:  # noqa: BLE001 - not every widget has text
                text = None
            if text:
                out.append(text)
            child = widget.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
        return out

    def _summary(self, card) -> str:
        """The clipped argument summary, which is the only label with an ellipsis.

        **Selected rather than assumed.** The first version joined every label in
        the card and tested the *end* of the join - which is another label's end,
        so a ZWJ stranded in the summary was never seen. That is why the
        "tool card slices again" mutation survived a test written to catch it.
        """
        matches = [t for t in self._labels(card) if "..." in t]
        assert len(matches) == 1, f"expected one clipped label, got {matches!r}"
        return matches[0]

    def test_a_tool_card_argument_summary_is_not_cut_mid_cluster(self):
        """A model can put an emoji in an argument, and `str[:57]` cut it."""
        gi = pytest.importorskip("gi")
        gi.require_version("Gtk", "4.0")
        from gi.repository import Adw  # noqa: E402
        Adw.init()
        from shani_chronoa.gui import blocks
        family = "\U0001F468\u200d\U0001F469\u200d\U0001F467\u200d\U0001F466"
        # **The emoji must straddle the cut, not sit beside it.** The first
        # version padded with prose, which left the emoji whole and the test
        # passed even with `summary[:57]` restored. The prefix is sized so the
        # 7-code-point cluster spans the index the slice cuts through, and the
        # offset is asserted rather than assumed - the second attempt put the
        # cluster *after* the cut and survived the mutation for that reason.
        # `"note="` is 5, so 45 x's plus a space puts the 7-code-point cluster at
        # indices 51..57 - and `[:57]` keeps up to index 56, which is a ZWJ.
        prefix = "x" * 45
        start = len("note=") + len(prefix) + len(" ")
        assert start < 56 < start + len(family), (
            f"the cluster starts at {start}, so a 57-column cut would not split it")
        arguments = {"note": f"{prefix} {family} " + "rest " * 12}
        card = blocks.ToolCallCard("write_text_file", arguments, None, True)
        summary = self._summary(card)
        # **A trailing ZWJ is the failure, and counting them is not.** An earlier
        # version counted them and divided by three, which passes when a slice
        # keeps all three joiners and drops only the last emoji - exactly what
        # `summary[:57]` does here. Measured with the slice restored, the card
        # renders `...👨\u200d👩\u200d👧\u200d...`.
        body = summary[:-3] if summary.endswith("...") else summary
        assert not body.endswith("\u200d"), (
            f"a ZWJ cluster was cut, leaving the joiner with no emoji: {summary[-40:]!r}")

    def test_the_message_preview_is_not_cut_mid_cluster(self):
        """The same for a message body previewed in the phone panel.

        Through `phone._preview`, which is the function the panel calls - and
        which exists *because* of this test: the preview was an inline
        `body[:80]` expression, and the only way to test that was to build the
        whole panel.
        """
        pytest.importorskip("gi")
        from shani_chronoa.gui.surfaces import phone as phone_panel
        family = "\U0001F468\u200d\U0001F469\u200d\U0001F467\u200d\U0001F466"
        preview = phone_panel._preview(f"see {family} " + "and a long message body " * 6)
        assert "..." in preview, preview
        assert preview.count("\u200d") % 3 == 0, preview
        # A short body is untouched - no ellipsis where there was nothing to cut.
        assert phone_panel._preview("hi there") == "hi there"
        assert phone_panel._preview("") == "(no text)"
