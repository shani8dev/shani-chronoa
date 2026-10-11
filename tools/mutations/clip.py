"""Mutation set for `markdown_lite.clip` — the grapheme-boundary cut.

Rebuilt on 2026-10-11 from the committed tests and code, after the original
`/tmp` harness was lost. Each mutation's anchor is asserted to occur exactly once
in the current source, so a set that drifts from the code errors rather than
silently passing.
"""

TEST_FILE = "tests/test_clip.py"

_ML = "usr/lib/shani-chronoa/shani_chronoa/markdown_lite.py"

MUTATIONS = [
    # -- each joining rule, one at a time -------------------------------------
    {
        "file": _ML,
        "old": "    if current in _JOINERS or current in _VARIATION_SELECTORS or current in _SKIN_TONES:\n        return True\n",
        "new": "",
        "label": "the forward joining rules removed (a skin tone is orphaned)",
        "expect": ["test_a_skin_tone_modifier_stays_with_its_emoji",
                   "test_a_skin_tone_modifier_joins",
                   "test_a_variation_selector_joins"],
    },
    {
        "file": _ML,
        "old": "    if unicodedata.combining(current):\n        return True\n",
        "new": "",
        "label": "a combining mark is not joined to its base",
        "expect": ["test_a_combining_mark_joins_its_base",
                   "test_a_combining_accent_is_never_orphaned"],
    },
    {
        "file": _ML,
        "old": "    if previous in _JOINERS:\n        return True\n",
        "new": "",
        "label": "the ZWJ no longer joins forwards, so half a pair is kept",
        "expect": ["test_a_zwj_joins_both_ways"],
    },
    {
        "file": _ML,
        "old": "    return previous in _REGIONAL and current in _REGIONAL\n",
        "new": "    return False\n",
        "label": "a flag is cut in half",
        "expect": ["test_two_regional_indicators_are_one_flag",
                   "test_a_flag_is_never_half_drawn"],
    },

    # -- the cut itself --------------------------------------------------------
    {
        "file": _ML,
        "old": "    return max(_display_width(ch) for ch in cluster)\n",
        "new": "    return sum(_display_width(ch) for ch in cluster)\n",
        "label": "a cluster's width is summed per code point again",
        # **Not `test_a_wide_character_costs_two`:** for a run of same-script CJK
        # the sum and the max are the same number, so that test passes either way.
        # The ZWJ sequence is where they diverge.
        "expect": ["test_a_family_emoji_is_kept_whole_or_dropped_whole",
                   "test_a_string_within_the_budget_is_returned_as_it_is"],
    },
    {
        "file": _ML,
        "old": "    budget = width - _display_width(ellipsis)\n    if budget < 1:\n        budget = width\n",
        "new": "    budget = width\n",
        "label": "the ellipsis no longer counts against the budget",
        # **Not the two budget tests, and it is worth saying why.** The first only
        # bounds the total width, which dropping the ellipsis still satisfies; the
        # second asserts the two-column case, which is exactly what `budget = width`
        # already produces. The witnesses are the ones that assert the ellipsis is
        # *present*.
        "expect": ["test_a_wide_character_costs_two",
                   "test_a_tool_card_argument_summary_is_not_cut_mid_cluster",
                   "test_the_message_preview_is_not_cut_mid_cluster"],
    },
    {
        "file": _ML,
        "old": "    if width <= 0:\n        return \"\"\n",
        "new": "",
        "label": "the explicit zero guard is removed (the budget covers it)",
        # **Equivalent, and documented rather than counted.** With the guard gone,
        # `budget = width - _display_width(ellipsis)` is negative, so the
        # `if budget < 1: budget = width` branch makes it `width` and the loop
        # breaks on the first character - the answer is "" either way.
        "expect": ["test_zero_and_negative_widths_are_empty"],
        "equivalent": True,
        "why": "a zero or negative width already falls through the budget branch "
               "to an empty result, so no input distinguishes the two paths",
    },
    {
        "file": _ML,
        "old": "    clipped = \"\".join(out)\n    if clipped and budget < width:\n        clipped += ellipsis\n    return clipped\n",
        "new": "    return \"\".join(out)\n",
        "label": "the ellipsis is never appended, so a cut reads as complete",
        "expect": ["test_a_wide_character_costs_two",
                   "test_a_tool_card_argument_summary_is_not_cut_mid_cluster",
                   "test_the_message_preview_is_not_cut_mid_cluster"],
    },
    {
        "file": _ML,
        "old": "    if sum(_cluster_display_width(c) for c in graphemes(text)) <= width:\n        return text\n",
        "new": "",
        "label": "a string that fits is clipped anyway",
        "expect": ["test_a_string_within_the_budget_is_returned_as_it_is",
                   "test_the_whole_emoji_survives_when_it_fits"],
    },

    # -- the two call sites ----------------------------------------------------
    {
        "file": "usr/lib/shani-chronoa/shani_chronoa/gui/blocks.py",
        "old": "            summary = markdown_lite.clip(summary, 60)\n",
        "new": "            summary = summary[:57] + \"...\"\n",
        "label": "the tool-card summary is a hard slice again",
        "expect": ["test_a_tool_card_argument_summary_is_not_cut_mid_cluster"],
    },
    {
        "file": "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/phone.py",
        "old": "    line = (body or \"(no text)\").replace(\"\\n\", \" \")\n"
               "    return markdown_lite.clip(line, width)\n",
        "new": "    line = (body or \"(no text)\").replace(\"\\n\", \" \")\n"
               "    return line[:width]\n",
        "label": "the message preview is a hard slice again",
        "expect": ["test_the_message_preview_is_not_cut_mid_cluster"],
    },

    # -- the honesty of the approximation --------------------------------------
    {
        "file": _ML,
        "old": "    **An approximation, and it has to be said so.** The stdlib has no grapheme\n",
        "new": "    **An exact segmentation.** The stdlib has no grapheme\n",
        "label": "the docstring claims an exactness it does not have",
        # **A source-level witness, because a wrong claim about the code's own
        # limits is exactly what the shipped `fmdsp` comment did.**
        "expect": ["test_the_approximation_is_stated"],
    },
]
