"""Mutation set for the window carrying the timers.

**The strip is built and not put anywhere is a widget nothing sees** -
the shape this feature has been in all along, and the reason the rail
being hidden was not noticed: the rail's Timers section existed, was
correct, and was on a column the app deletes at its own width.

The set is the three ways the wiring can be wrong: the strip is never
added, the tick never refreshes it, and it is added in the wrong place.

The third is a **pair** - the strip's append is removed and the composer
appends it after itself instead - because the two lines are not
adjacent and must change together.
"""

TEST_FILE = "tests/test_the_window_carries_the_timers.py"

_WINDOW = "usr/lib/shani-chronoa/shani_chronoa/gui/window.py"

MUTATIONS = [
    {
        "file": _WINDOW,
        "old": "        main_box.append(self._timer_strip)\n",
        "new": "",
        # The strip is built and never put on screen: a widget nothing sees.
        "label": "the timer strip is never put on screen",
        "expect": ["test_the_strip_is_in_the_window",
                   "test_the_strip_is_above_the_composer"],
    },
    {
        "file": _WINDOW,
        "old": "        if getattr(self, \"_timer_strip\", None) is not None:\n"
               "            try:\n"
               "                self._timer_strip.refresh()\n"
               "            except Exception as exc:  # noqa: BLE001 - the tick must never die\n"
               "                logger.warning(\"The timer strip failed to refresh: %s\", exc)\n",
        "new": "",
        # **The quiet failure.** The rail's refresh and the strip's refresh
        # are two calls in one tick, and the rail is hidden on the narrow
        # window where the timers matter most - so a tick that only
        # refreshed the rail leaves the timers stale exactly where they are
        # the only thing on screen, and nothing says so.
        "label": "the tick never refreshes the timer strip",
        "expect": ["test_the_tick_refreshes_the_strip"],
    },
    {
        "file": _WINDOW,
        "old": ["        main_box.append(self._timer_strip)\n",
                "        main_box.append(reading_width(composer_card))\n"],
        "new": ["",
                "        main_box.append(reading_width(composer_card))\n"
                "        main_box.append(self._timer_strip)\n"],
        # Below the composer, so the strip is under the thing a person is
        # typing into - and the window's own minimum height is a promise
        # about what fits above it.
        "label": "the timer strip sits below the composer",
        "expect": ["test_the_strip_is_above_the_composer"],
    },
]
