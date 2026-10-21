"""Mutation set for the palette's group captions.

**The palette's groups were never drawn.** `Entry.group` was used for the
dedup key and for the search, and for nothing else - so opening the palette
showed 260 rows in one flat list, with nothing to say which was a panel,
which a skill and which a command. Measured on a real capture of the palette
opened on a fresh install: "Conversation / the main window", "Quick ask / one
question, no conversation", "Conversations / Remembering", "Compare /
Remembering" - four kinds of thing, one list, no headings.

The captions are rows in the same list the entries are in, which is why the
keyboard navigation had to change: the row at index *n* is not the entry at
index *n*. The set is the ways that comes apart - a caption that is not
drawn, a caption that can be chosen, a navigation that counts the captions,
and the two ways the search can put one group's rows on both sides of
another's.
"""

TEST_FILE = "tests/test_the_palette_shows_its_groups.py"

_PALETTE = "usr/lib/shani-chronoa/shani_chronoa/gui/palette.py"

MUTATIONS = [
    {
        "file": _PALETTE,
        "old": "                self._append_group_header(group)\n",
        "new": "                pass\n",
        # The flat list is back: 260 rows and no headings.
        "label": "the palette draws no caption naming the kind of thing",
        "expect": ["test_the_palette_draws_a_caption_naming_each_group"],
    },
    {
        "file": _PALETTE,
        "old": "        row.set_selectable(False)\n",
        "new": "        row.set_selectable(True)\n",
        # A heading that can be highlighted is a control that does nothing.
        "label": "a caption can be selected",
        "expect": ["test_a_caption_is_not_a_destination"],
    },
    {
        "file": _PALETTE,
        "old": "        row.set_activatable(False)\n",
        "new": "        row.set_activatable(True)\n",
        # And one that can be activated runs nothing when Enter reaches it.
        "label": "a caption can be activated",
        "expect": ["test_a_caption_is_not_a_destination"],
    },
    {
        "file": _PALETTE,
        "old": "        for index, candidate in enumerate(self._entry_rows):\n"
               "            if candidate is row:\n"
               "                return index\n",
        "new": "        for index in range(len(self._visible)):\n"
               "            if self._list.get_row_at_index(index) is row:\n"
               "                return index\n",
        # The navigation counts the list's rows, so the caption rows shift
        # every entry's number - and the selected entry is not found at all.
        "label": "the navigation counts the caption rows as entries",
        "expect": ["test_the_arrow_keys_step_over_the_captions"],
    },
    {
        "file": _PALETTE,
        "old": "        row = self._entry_rows[index]\n"
               "        self._list.select_row(row)\n",
        "new": "        row = self._list.get_row_at_index(index)\n"
               "        self._list.select_row(row)\n",
        # Selecting by the list's own number lands on a caption, which is
        # not selectable - so the selection never moves and Enter does
        # nothing at all.
        "label": "the selection is made by the list's row number",
        "expect": ["test_the_arrow_keys_step_over_the_captions"],
    },
    {
        "file": _PALETTE,
        "old": "        if self._entry_rows:\n"
               "            self._list.select_row(self._entry_rows[0])\n",
        "new": "        if self._entry_rows:\n"
               "            self._list.select_row(self._list.get_row_at_index(0))\n",
        # The palette opens with its first *row* selected, and the first row
        # is a caption.
        "label": "the palette opens with a caption selected",
        "expect": ["test_the_arrow_keys_step_over_the_captions"],
    },
    {
        "file": _PALETTE,
        "old": "        return (group_rank.get(entry.group, 2),\n"
               "                group_order.get(entry.group, 0),\n"
               "                match_rank(entry),\n"
               "                entry.weight)\n",
        "new": "        return (match_rank(entry),\n"
               "                entry.weight)\n",
        # Ordering by how well each entry matched puts one group's rows on
        # both sides of another group's - so the caption sits over rows that
        # are not that kind of thing.
        "label": "the search interleaves the groups again",
        "expect": ["test_a_search_keeps_the_rows_of_one_group_together"],
    },
    {
        "file": _PALETTE,
        "old": "        return (group_rank.get(entry.group, 2),\n"
               "                group_order.get(entry.group, 0),\n"
               "                match_rank(entry),\n"
               "                entry.weight)\n",
        "new": "        return (group_order.get(entry.group, 0),\n"
               "                match_rank(entry),\n"
               "                entry.weight)\n",
        # The groups are ordered by the caller's list rather than by how
        # well they matched, so a page is buried under every skill whose
        # prompt merely mentions it.
        "label": "the group that matched best is not the first group",
        "expect": ["test_the_group_that_matched_best_comes_first"],
    },
]
