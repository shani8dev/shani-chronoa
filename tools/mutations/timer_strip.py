"""Mutation set for the pending timers being on screen.

**A running timer is a thing the person asked for, and at the width the
app opens at there was nowhere to see it.** The rail's Timers section held
them, and the rail is deleted below 1040px - the app opens at 460.

The set is the three ways the strip can be wrong: it can show nothing
when something is running, it can show something when nothing is, and
its buttons can do nothing.
"""

TEST_FILE = "tests/test_the_timer_strip.py"

_TIMERS = "usr/lib/shani-chronoa/shani_chronoa/gui/timers.py"

MUTATIONS = [
    {
        "file": _TIMERS,
        "old": "        if not pending:\n            self.set_visible(False)\n            return\n",
        "new": "        if not pending:\n            return\n",
        # The strip stays on screen with nothing to say, so the chrome of a
        # quiet window is not the chrome that was measured - and the window's
        # minimum height is a promise.
        "label": "the strip is on screen with no timer running",
        "expect": ["test_nothing_is_shown_when_no_timer_is_running"],
    },
    {
        "file": _TIMERS,
        "old": "        self._heading()\n        for identifier, label, left in pending:\n",
        "new": "        for identifier, label, left in pending:\n",
        # The rows are drawn without the heading, so a row that says "kettle"
        # and "1m 35s left" is a mystery on the screen.
        "label": "the rows are drawn with no heading",
        "expect": ["test_the_strip_says_what_the_rail_used_to"],
    },
    {
        "file": _TIMERS,
        "old": '            ("window-close-symbolic", "Cancel this timer",\n'
               '             act(lambda: timer_skill.cancel_timer(identifier))),\n',
        "new": '            ("window-close-symbolic", "Cancel this timer",\n'
               '             act(lambda: None)),\n',
        # The button is there, says the right thing, and does nothing - which
        # is the shape this repository keeps finding.
        "label": "Cancel does not cancel the timer",
        "expect": ["test_a_timer_can_be_extended_and_cancelled"],
    },
    {
        "file": _TIMERS,
        "old": '            ("list-add-symbolic", "Five more minutes",\n'
               '             act(lambda: timer_skill.add_to_timer(300, identifier, label))),\n',
        "new": '            ("list-add-symbolic", "Five more minutes",\n'
               '             act(lambda: None)),\n',
        "label": "Five more minutes does not extend the timer",
        "expect": ["test_a_timer_can_be_extended_and_cancelled"],
    },
    {
        "file": _TIMERS,
        "old": "        try:\n            pending = pending_timers()\n"
               "        except Exception as exc:  # noqa: BLE001\n"
               "            logger.warning(\"the timer strip failed to refresh: %s\", exc)\n"
               "            pending = []\n",
        "new": "        pending = pending_timers()\n",
        # The tick refreshes the strip, and a tick that dies takes the rest of
        # the window with it - so a store that cannot be read must leave the
        # strip empty rather than taking the window down.
        "label": "a timer store that cannot be read takes the window down",
        "expect": ["test_a_refresh_never_raises",
                   "test_a_store_that_cannot_be_read_leaves_the_strip_empty"],
    },
]
