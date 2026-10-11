"""Mutation set for the background stop-list and the reaper.

The two measured findings this file checks:

1. **Six background watchers were started and could not be stopped.**
   `app/application.py` starts them unconditionally; each has a `stop()`; a scan
   found that stop called by nothing and a grep over `gui/` and `app/tray.py`
   found none of the six named on screen. Mutations here re-break the reaching.
2. **The child supervisor could see a hung child and had nothing to do about
   it.** `check_heartbeats` reports `DISCONNECTED` and every caller only reads it.
   Measured cost, on this box, 2026-10-11: two `whisper-cli` runs up 44 and 2
   minutes on 3-4 second WAVs held 4.7 GB each; killing four PIDs took available
   memory from 9 GB to 18 GB.

The reaper is guarded two ways, because pid reuse is real - **and both guards
are load-bearing**, so both are mutated here. A reaper without the re-probe
signals a pid that may already belong to a stranger, and a reaper that kills a
pid-less stand-in takes down whatever happens to be at that number.
"""

TEST_FILE = "tests/test_what_is_running.py"

_BG = "usr/lib/shani-chronoa/shani_chronoa/background.py"
_CS = "usr/lib/shani-chronoa/shani_chronoa/child_supervisor.py"

MUTATIONS = [
    # -- 1. the stop that existed and nothing called -------------------------
    {
        "file": _BG,
        "old": "        add(key, name, getattr(component, \"stop\", None) if component else None,\n"
               "            running=component is not None)\n",
        "new": "        add(key, name, None, running=component is not None)\n",
        "label": "the six watchers' rows have no stop at all, so the panel shows "
                 "a dead button",
        # **The finding, restored.** This is the shipped behaviour: the stop
        # existed in the component and was never reached.
        "expect": ["test_all_six_watchers_get_a_stop"],
    },
    {
        "file": _BG,
        "old": "        running=bool(wake and getattr(wake, \"is_listening\", lambda: False)()))\n",
        "new": "        running=bool(wake))\n",
        "label": "the wake-word row claims to be running whenever the listener "
                 "object exists",
        "expect": ["test_the_wake_word_row_is_not_claimed_running_when_idle"],
    },

    # -- 2. a stop that was requested is not a stop that happened -------------
    {
        "file": _BG,
        "old": "            self.running = True\n"
               "            return f\"Could not stop it: {type(exc).__name__}: {exc}\"\n",
        "new": "            return f\"Could not stop it: {type(exc).__name__}: {exc}\"\n",
        "label": "a failed stop leaves its row reading stopped",
        # **The confident wrong answer this module exists to prevent.** A row
        # that reads "stopped" after a `systemctl` failure is the panel lying.
        # The witness is the *succeeded-then-failed* one, not the plain raising
        # one: the line looks redundant on a first failure (the row was already
        # running), so only a retry distinguishes the two - which is why the
        # first version of this set had no witness at all for it.
        "expect": ["test_a_stop_that_succeeded_then_failed_does_not_read_stopped"],
    },
    {
        "file": _BG,
        "old": "        if self.stop is None:\n"
               "            return self.why_not or \"there is no way to stop this one.\"\n",
        "new": "        return \"stopped.\"\n",
        "label": "a row with no stop reports a success instead of the reason",
        "expect": ["test_a_component_with_no_stop_says_so_rather_than_showing_a_button"],
    },
    {
        "file": _BG,
        "old": "            sentence = item.request_stop()\n"
               "            if item.running:\n"
               "                refused.append(item)\n"
               "            else:\n"
               "                said.append(f\"{item.label}: {sentence}\")\n",
        "new": "            said.append(f\"{item.label}: {item.request_stop()}\")\n",
        "label": "the stop-all button reports success for the rows that could not "
                 "be stopped",
        # **ECC's #2463 lesson, applied to the machine.** A button that stops
        # eight of ten and says "stopped" is a green dashboard over a broken
        # writer, and the fix is the second half of the sentence.
        "expect": ["test_force_stop_all_answers_for_a_stop_that_failed"],
    },

    # -- 3. the negative half of the summary ---------------------------------
    {
        "file": _BG,
        "old": "    if stuck:\n"
               "        parts.append(\"running with no way to stop it from here: \" + \", \".join(stuck))\n",
        "new": "",
        "label": "the summary drops the things it could not stop, so it reads as "
                 "'nothing else is running'",
        "expect": ["test_the_summary_names_the_negative_half",
                   "test_a_summary_that_omits_the_negative_half_is_refused"],
    },
    {
        "file": _BG,
        "old": "    if not on and not stuck:\n"
               "        return \"nothing is running in the background.\"\n",
        "new": "    return \"nothing is running in the background.\"\n",
        "label": "an empty inventory is claimed while things are still running",
        "expect": ["test_the_summary_names_the_negative_half"],
    },

    # -- 4. the reaper: it must exist and it must be guarded -------------------
    {
        "file": _CS,
        "old": "        reaped: List[str] = []\n"
               "        for status in self.check_heartbeats():\n"
               "            if status.state is not ChildState.DISCONNECTED:\n"
               "                continue\n",
        "new": "        return []\n"
               "        reaped: List[str] = []\n"
               "        for status in self.check_heartbeats():\n"
               "            if status.state is not ChildState.DISCONNECTED:\n"
               "                continue\n",
        "label": "**the finding, restored verbatim**: the reaper does nothing",
        "expect": ["test_reaping_a_hung_child_really_kills_it"],
    },
    {
        "file": _CS,
        "old": "            if child.probe() is not None:\n"
               "                logger.info(\"%r exited on its own before it could be reaped\",\n"
               "                            status.name)\n"
               "                continue\n",
        "new": "",
        "label": "the re-probe guard is gone, so a pid that already belongs to a "
                 "stranger is signalled",
        # **The pid-reuse guard.** Its absence cannot be seen by running the
        # happy path, which is why this mutation is in the set.
        "expect": ["test_a_child_that_exits_before_the_signal_is_not_signalled"],
    },
    {
        "file": _CS,
        "old": "            if child is None or child.pid is None:\n"
               "                continue\n",
        # Only the pid half is removed. Replacing the whole guard with a bare
        # `continue` made the reaper skip *every* child, which the real-kill
        # test already catches - so it said nothing about the pid at all.
        "new": "            if child is None:\n"
               "                continue\n",
        "label": "the reaper reaches a child with no pid and signals whatever is "
                 "at that number",
        "expect": ["test_a_child_with_no_pid_is_left_alone"],
    },
    {
        "file": _CS,
        "old": "            if status.state is not ChildState.DISCONNECTED:\n"
               "                continue\n",
        # `if False:` rather than removing the line, so the loop still runs and
        # reaches a *healthy* child. A bare `continue` made the reaper a no-op
        # again, which the real-kill test already covers.
        "new": "            if False:\n"
               "                continue\n",
        "label": "**the over-fix**: every tracked child is signalled, healthy or not",
        # Reaping everything that is silent would kill a slow child that is still
        # working, and a pid-less stand-in would take an unrelated process down.
        "expect": ["test_a_healthy_child_is_never_signalled"],
    },
    {
        "file": _CS,
        "old": "        for status in self.check_heartbeats():\n",
        "new": "        for status in []:\n",
        "label": "the reaper never looks at what is running",
        "expect": ["test_reaping_a_hung_child_really_kills_it"],
    },
]
