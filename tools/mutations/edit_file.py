"""Mutation set for `edit_file`'s re-typed `old_string` correction.

A mutation is a dict with the file, the anchor, the replacement, what it breaks,
and the tests that must fail. `TEST_FILE` says which file the run is against.

**Six of these first reported survivors and all six were the mutation or the
witness, not the code.** The recorded versions of those lessons:

- the "a single line is treated as a quote" mutation aimed at the region scan
  alone, then the out scan alone; each is a no-op, because a one-line span is
  only restorable if *both* loops allow it. They are not adjacent, so that
  mutation is a **pair** of anchors.
- the blank-line witness used `"alpha\n\nbeta\n"` for both region and
  replacement, which leaves the region with no usable key at all.
- the short-needle witness used a file that contained none of its needles.
- the ambiguity witness used two runs differing in *letters*, so there was no
  ambiguity to find.
- the span-floor witness used a nine-character run that passes the floor anyway.
- the boundary case used `return 1` (seven tight characters) instead of `return`
  (exactly six).

Each is corrected here, and the runner's `also:` line is what would show a
witness failing for an unrelated reason.
"""

TEST_FILE = "tests/test_edit_what_the_model_meant.py"

_EDIT = "usr/lib/shani-chronoa/shani_chronoa/skills/edit_file.py"

MUTATIONS = [
    # -- the retry is not reached at all --------------------------------------
    {
        "file": _EDIT,
        "old": "        old, count, offset = _match_ignoring_whitespace(text, old)\n",
        "new": "        old, count, offset = old, 0, -1\n",
        "label": "the second chance is removed (the refusal stands)",
        "expect": ["test_the_edit_that_used_to_be_refused_now_lands"],
    },
    {
        "file": _EDIT,
        "old": "    if count == 0:\n        # **A second chance, and it is not a guess.**",
        "new": "    if False:\n        # **A second chance, and it is not a guess.**",
        "label": "the retry block is dead",
        "expect": ["test_the_edit_that_used_to_be_refused_now_lands"],
    },

    # -- the match resolves ambiguity -----------------------------------------
    {
        "file": _EDIT,
        "old": "    if hits != 1:\n        return old, (0 if hits == 0 else hits), -1\n",
        "new": "    if hits == 0:\n        return old, 0, -1\n",
        "label": "an ambiguous needle is resolved to the first place",
        "expect": ["test_an_ambiguous_needle_comes_back_ambiguous",
                   "test_an_ambiguous_string_is_still_refused_with_its_lines"],
    },
    {
        "file": _EDIT,
        "old": "    if len(_tight(old)) < 6:\n        return old, 0, -1\n",
        "new": "",
        "label": "the six-character floor on a needle is dropped",
        # The runner matches a witness as a **prefix of a parametrized id**, so
        # naming the base test is enough and does not rot when a case is added.
        "expect": ["test_a_short_needle_is_never_retried"],
    },
    {
        "file": _EDIT,
        "old": "                if _tight(\"\\n\".join(file_lines[index:end + 1])) == wanted:\n",
        "new": "                if True:\n",
        "label": "the comparison always matches, so any span is 'the one'",
        "expect": ["test_the_retry_is_not_fooled_by_a_reorder"],
    },

    # -- line alignment -------------------------------------------------------
    {
        "file": _EDIT,
        "old": "                        if end + 1 < len(file_lines):\n"
               "                            first_span += \"\\n\"\n",
        "new": "",
        "label": "the trailing newline is not kept, so the substitution duplicates it",
        "expect": ["test_the_write_keeps_the_untouched_line_exactly"],
    },
    {
        "file": _EDIT,
        "old": "    file_lines = text.split(\"\\n\")\n",
        "new": "    file_lines = text.splitlines()\n",
        "label": "splitting drops the newlines, so the offsets no longer line up",
        "expect": ["test_the_retry_returns_the_whole_span_not_the_needles_length"],
    },

    # -- the quoted-line restore ----------------------------------------------
    {
        "file": _EDIT,
        "old": "        fuzzy = count == 1\n",
        "new": "        fuzzy = False\n",
        "label": "the write-side correction never runs, so the diff changes the quoted lines",
        "expect": ["test_the_write_keeps_the_untouched_line_exactly"],
    },
    # **A pair of anchors: either one alone is an equivalent mutant.** The region
    # scan decides which spans are candidates, the out scan decides which are
    # applied, and a one-line span is only restorable if both allow it.
    {
        "file": _EDIT,
        "old": ["        for length in range(2, min(10, len(region_lines) - index) + 1):\n",
                "        for length in range(min(10, len(out) - start), 1, -1):\n"],
        "new": ["        for length in range(1, min(10, len(region_lines) - index) + 1):\n",
                "        for length in range(min(10, len(out) - start), 0, -1):\n"],
        "label": "a single line is treated as a quote, so a new line can be overwritten",
        "expect": ["test_a_single_line_is_not_evidence_of_a_quote"],
    },
    {
        "file": _EDIT,
        "old": "            if any(not line.strip() for line in span):\n                continue\n"
               "            hit = unique.get(_tight(\"\".join(span)))\n",
        "new": "            hit = unique.get(_tight(\"\".join(span)))\n",
        "label": "blank lines are allowed in a restorable span",
        "expect": ["test_blank_lines_are_never_part_of_a_run"],
    },
    {
        "file": _EDIT,
        "old": "            if len(tight) < 6:\n                continue\n",
        "new": "",
        "label": "the six-character floor on a restorable span is dropped",
        "expect": ["test_a_run_too_short_to_identify_a_place_is_left_alone"],
    },
    {
        "file": _EDIT,
        "old": "    unique = {k: v[0] for k, v in where.items() if len(v) == 1}\n",
        "new": "    unique = {k: v[0] for k, v in where.items()}\n",
        "label": "an ambiguous run is restored to the first place it matches",
        "expect": ["test_an_ambiguous_run_is_left_alone"],
    },

    # -- the tight comparison itself -------------------------------------------
    {
        "file": _EDIT,
        "old": "    return \"\".join(text.split())\n",
        "new": "    return text.replace(\" \", \"\").replace(\"\\n\", \"\")\n",
        "label": "tight only strips spaces and newlines, not tabs",
        "expect": ["test_the_tight_form_is_a_real_comparison"],
    },
]
