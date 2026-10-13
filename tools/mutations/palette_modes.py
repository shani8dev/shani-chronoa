"""Mutation set for the palette naming a mode Chronoa was not in.

Asked whether the mode strip and the Ctrl+K palette duplicate each other. The
duplication is deliberate and fine - the strip is always visible, the palette is
searchable, and the mode tables are listed in both on purpose. **The conflict was
worse than duplication: the two surfaces answered the same question differently,
and the palette was wrong.**

`_palette_mode_entries` built each entry as

    Entry(table[0][1], row[2] if row else "", ...)

- a **title from `table[0][1]`**, which is the table's *first* row's word and is
therefore a constant, identical whatever state the app is in, next to a
**subtitle from the current row**. The chip beside it, `_draw`, does the opposite
and correct thing: `row = next((r for r in table if r[0] == current), table[0])`
and then prints `row[1]`.

Measured over the seven tables `_MODE_TABLES` actually lists, with each forced to
every row it can hold: **20 rows, 7 right, 13 misnamed.** Chronoa `Asleep` was
listed as "Here", `Plan` as "Working", `Build` as "General", "No questions" as
"Ask first" and every cloud egress as "Nothing leaves". The subtitle was always
right, which is what made this worse rather than better: each row was a
*plausible description of the wrong state*.

**The trap the first version of the tests fell into is the one this file's whole
premise is about.** On a default machine every mode *is* its table's first row,
so the defect is invisible until something moves - and the first version of this
set's own control measured the wrong keys, forcing `asleep` and `dont_ask` when
the tables spell them `sleeping` and `auto`. Every mutation below restores the
shipped behaviour, widens the fix wrongly, or breaks the cross-check the fix
exists to make the two surfaces agree on.
"""

TEST_FILE = "tests/test_palette_agrees_with_the_mode_strip.py"

_WINDOW = "usr/lib/shani-chronoa/shani_chronoa/gui/window.py"

# The shipped two lines, verbatim.
_SHIPPED = (
    '            row = next((r for r in table if r[0] == current), None)\n'
    '            icon = row[3] if row and len(row) > 3 and row[3] else "preferences-system-symbolic"\n'
    '            out.append(palette_module.Entry(\n'
    '                table[0][1], row[2] if row else "", icon,\n'
    '                (lambda a=action_name: self._activate_mode(a)),\n'
    '                group="Modes", weight=0))\n'
)

_FIXED = (
    '            row = next((r for r in table if r[0] == current), table[0])\n'
    '            icon = row[3] if len(row) > 3 and row[3] else "preferences-system-symbolic"\n'
    '            out.append(palette_module.Entry(\n'
    '                row[1], row[2], icon,\n'
    '                (lambda a=action_name: self._activate_mode(a)),\n'
    '                group="Modes", weight=0))\n'
)

_CROSS = "test_the_palette_and_the_chip_say_the_same_thing"

MUTATIONS = [
    {
        "file": _WINDOW,
        "old": _FIXED,
        "new": _SHIPPED,
        # The shipped bug, verbatim: every non-first row of every table named
        # the row above it.
        "label": "the palette titles every mode with its table's first row (the shipped bug)",
        "expect": ["test_the_palette_names_the_mode_you_are_in", _CROSS,
                   "test_no_palette_mode_entry_is_titled_with_a_constant",
                   "test_a_mode_whose_key_matches_no_row_agrees_with_the_chip"],
    },
    {
        "file": _WINDOW,
        "old": "                row[1], row[2], icon,\n",
        "new": "                table[0][1], row[2], icon,\n",
        # Title back to the constant, subtitle still live: the *half* fix, and the
        # shape that reads best on screen - every row carries a true sentence.
        "label": "the title is the table's first row while the line still tracks",
        "expect": ["test_the_palette_names_the_mode_you_are_in", _CROSS,
                   "test_no_palette_mode_entry_is_titled_with_a_constant"],
    },
    {
        "file": _WINDOW,
        "old": "                row[1], row[2], icon,\n",
        "new": "                row[2], row[1], icon,\n",
        # The over-fix that looks like a fix. Title and line come from the same
        # row either way, so a reader would see a live sentence - under the wrong
        # heading. The `matching` lookup is by subtitle, so it finds nothing.
        "label": "title and line are swapped",
        "expect": ["test_the_palette_names_the_mode_you_are_in", _CROSS],
    },
    {
        "file": _WINDOW,
        "old": '            row = next((r for r in table if r[0] == current), table[0])\n',
        "new": '            row = next((r for r in table if r[0] == current), table[-1])\n',
        # The fallback pointed at the *last* row. On an unreadable key the chip
        # says the first row and the palette says the last - the exact divergence
        # the shared fallback exists to prevent, reached through the fix.
        "label": "an unmatched key falls back to the last row rather than the first",
        "expect": ["test_a_mode_whose_key_matches_no_row_agrees_with_the_chip"],
    },
    {
        "file": _WINDOW,
        "old": '            row = next((r for r in table if r[0] == current), table[0])\n',
        "new": '            row = next((r for r in table if r[0] == current), None)\n'
               '            if row is None:\n'
               '                continue\n',
        # Dropping the entry instead of falling back. This is the one genuinely
        # tempting alternative - "an unreadable key is not a mode, so offer
        # nothing" - and it is strictly worse: the palette is then *missing* the
        # mode where the chip beside it still shows one, so the two surfaces
        # disagree about whether it exists at all.
        "label": "an unmatched key drops the entry instead of falling back",
        "expect": ["test_a_mode_whose_key_matches_no_row_agrees_with_the_chip"],
    },
    {
        "file": _WINDOW,
        "old": '            row = next((r for r in table if r[0] == current), table[0])\n',
        "new": '            row = next((r for r in table if r[0] == current), None)\n'
               '            row = row or table[0]\n',
        # The `or` form. Works, and is the shape that silently breaks the moment a
        # row is ever falsy - a tuple is not, so it is equivalent today, which is
        # exactly why the shared form is worth pinning rather than leaving to
        # taste. Recorded as equivalent if it survives.
        "label": "the fallback is reached through `or` rather than a default argument",
        "equivalent": True,
        "why": "`row or table[0]` reaches the same row as "
               "`next(..., table[0])` for every input the tables can produce - "
               "the table's rows are non-empty tuples, so the first is always "
               "truthy. It is a second spelling of one rule, which is the "
               "duplication this file's own argument is about; kept so the "
               "choice between them is deliberate rather than accidental.",
    },
    {
        "file": _WINDOW,
        "old": '            icon = row[3] if len(row) > 3 and row[3] else "preferences-system-symbolic"\n',
        "new": '            icon = "preferences-system-symbolic"\n',
        # A constant icon. Not the defect, but the same shape as the constant
        # title: a widget asserting something it does not know, on the same three
        # lines the fix touches.
        "label": "every mode entry carries the same icon",
        "expect": ["test_the_palette_and_the_chip_say_the_same_thing"],
    },
    {
        "file": _WINDOW,
        "old": '            out.append(palette_module.Entry(\n'
               '                row[1], row[2], icon,\n',
        "new": '            out.append(palette_module.Entry(\n'
               '                row[1], "", icon,\n',
        # The line dropped, title right. The palette then names the mode and
        # describes nothing, which is the "a row that reports nothing when
        # pressed is the same defect as a button wired to nothing" shape one
        # level up.
        "label": "the mode is named but its sentence is gone",
        "expect": ["test_the_palette_names_the_mode_you_are_in", _CROSS],
    },
]