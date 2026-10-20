"""Mutation set for the context bar - how full the model's window is.

**"It forgot what I said" has to be distinguishable from "the window is
full"**, and the rail - the only place that distinction was drawn - is
deleted below 1040px, the app's own width being 460.

The set is the three ways the bar can be wrong: it can speak when there
is nothing to say, it can show the wrong number, and it can lose the
breakdown that says what is filling the window.
"""

TEST_FILE = "tests/test_the_context_bar.py"

_BAR = "usr/lib/shani-chronoa/shani_chronoa/gui/context_bar.py"

MUTATIONS = [
    {
        "file": _BAR,
        "old": "        if fraction < WARN_AT:\n",
        "new": "        if False:\n",
        # **A bar for a window at 15% is chrome on every window**, for a
        # reading nobody needs - and the window's minimum height is a
        # promise.
        "label": "the bar is on screen for a window that is barely used",
        "expect": ["test_a_window_working_normally_shows_no_bar"],
    },
    {
        "file": _BAR,
        "old": "        fraction = max(0.0, min(1.0, float(usable) / 100.0))\n",
        "new": "        raw = getattr(report, \"percent\", None)\n"
               "        fraction = max(0.0, min(1.0, float(raw or 0) / 100.0))\n",
        # **A window that is 80% full of input and wants 20% of output left
        # over is not 80% usable** - the raw share is the alarming number and
        # the usable one answers "can another question still fit".
        "label": "the bar shows the raw share rather than the usable one",
        "expect": ["test_the_bar_shows_the_usable_share_not_the_raw_one"],
    },
    {
        "file": _BAR,
        "old": "                for label, tokens, _share in rows():\n"
               "                    parts.append(f\"{label}: {tokens:,} tokens\")\n",
        "new": "                for label, tokens, _share in rows():\n"
               "                    pass\n",
        # **What is filling the window** is the other half of the question -
        # a percentage alone does not say what to do about it.
        "label": "the tooltip loses the breakdown of what is filling it",
        "expect": ["test_the_tooltip_carries_the_breakdown"],
    },
    {
        "file": _BAR,
        "old": "        if getattr(report, \"elided\", False):\n"
               "            parts.append(\"part of this conversation was already dropped to \"\n"
               "                         \"make the request fit\")\n",
        "new": "",
        # **"It forgot what I said"** - the thing the bar exists to explain.
        "label": "the tooltip does not say part of the conversation was dropped",
        "expect": ["test_a_dropped_part_of_the_conversation_is_said"],
    },
    {
        "file": _BAR,
        "old": "        if raw is not None and usable is not None and raw != usable:\n",
        "new": "        if False:\n",
        # The raw share and the reserve are the numbers that make the
        # tooltip answer the question; without them it shows one number and
        # hides what it is a number of.
        "label": "the tooltip does not say the raw share or the reserve",
        "expect": ["test_the_raw_share_is_in_the_tooltip"],
    },
]
