"""Mutation set for the window carrying the context bar.

**The bar is built and not put anywhere is a widget nothing sees** - the
shape the rail's Context card had for as long as it existed, on a column
the app deletes at its own width.

The set is the two ways the wiring can be wrong: the bar is never added,
and the tick never updates it.
"""

TEST_FILE = "tests/test_the_window_carries_the_context_bar.py"

_WINDOW = "usr/lib/shani-chronoa/shani_chronoa/gui/window.py"

MUTATIONS = [
    {
        "file": _WINDOW,
        "old": "        main_box.append(self._context_bar)\n",
        "new": "",
        # The bar is built and never put on screen: a widget nothing sees.
        "label": "the context bar is never put on screen",
        "expect": ["test_the_bar_is_in_the_window"],
    },
    {
        "file": _WINDOW,
        "old": "        if getattr(self, \"_context_bar\", None) is not None:\n"
               "            try:\n"
               "                self._context_bar.set_report(self._rail_context())\n"
               "            except Exception as exc:  # noqa: BLE001 - the tick must never die\n"
               "                logger.warning(\"The context bar failed to refresh: %s\", exc)\n",
        "new": "",
        # **The quiet failure.** The rail's refresh and the bar's are two
        # calls in one tick, and the rail is hidden on the narrow window
        # where the bar matters most - so a tick that only refreshed the
        # rail would leave the bar stale exactly where it is the only thing
        # on screen, and nothing would say so.
        "label": "the tick never updates the context bar",
        "expect": ["test_the_bar_reads_the_same_report_the_rail_reads"],
    },
]
