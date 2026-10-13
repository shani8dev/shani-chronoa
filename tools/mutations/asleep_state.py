"""Mutation set for `Asleep` being reachable, and for the guard that read it.

`presence.py` documents three states and `AGENTS.md` records why the third one
matters: *"Asleep means stop it **and** do not start it again to answer the next
question, which no pair of Run/Stop buttons can express."*

Measured before this change, `Asleep` was offered and unreachable, twice over:

1. **The cycle read the machine.** `_cycle_presence` cycled from
   `presence.detect(...)`, which answers *"is the model resident"* and can only
   return ACTIVE or DROWSY - a property `tests/test_presence.py` pins
   deliberately. So from a released model the target was always SLEEPING,
   `apply()` answered "already not resident" without doing anything, and the
   button stayed on "Go to sleep" forever. `Wake up` never appeared.
2. **Nothing wrote the choice.** `self.app._presence` was set once at startup
   from `detect()` and read once by `brain._use_local_server` - so the guard that
   implements "do not start it again" was permanently False.
3. **The fallback started it anyway.** `_explain_no_model` called
   `start_service()` unconditionally on the branch where a model is installed, so
   even a live guard would have been defeated one statement later.

The model the fix rests on is two questions the first version conflated:
`detect()` asks **is the model resident** (observable), `_presence` asks **what
did the person choose** (SLEEPING is a decision about behaviour, so `detect()`
cannot see it). The cycle is driven by the choice, written back on success, and
it is what the row reports.

Three ways each half is written wrong, all of which look correct:

- **cycling from `detect()`** - the original defect; the second and third presses
  are the same press.
- **not writing the choice back** - the machine is right and the app never
  learns, so `brain` keeps waking it.
- **reporting the machine rather than the choice** - the row keeps promising
  "the next question loads it again" about a model `brain` now refuses to load.

**Witness names are bare**, per the runner's format doc, so the parametrized cycle
test is named once rather than three times with its parameters spelled out - and
a mutation that only breaks one of the three transitions is still caught.
"""

TEST_FILE = "tests/test_asleep_is_reachable.py"

_WIN = "usr/lib/shani-chronoa/shani_chronoa/settings_window/window.py"
_BRAIN = "usr/lib/shani-chronoa/shani_chronoa/app/brain.py"
_MODELS = "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/models.py"
_PRESENCE = "usr/lib/shani-chronoa/shani_chronoa/presence.py"

MUTATIONS = [
    {
        "file": _WIN,
        "old": ('        current = getattr(self.app, "_presence", None)\n'
                '        if current is None:\n'
                '            current = presence.detect(local_llm.is_up)\n'),
        "new": "        current = presence.detect(local_llm.is_up)\n",
        "label": "the cycle reads the machine, so every press from a released "
                 "model is the same press",
        "expect": ["test_every_state_in_the_cycle_is_reachable"],
        "why": "the original defect. From DROWSY the target is SLEEPING either "
               "way, so the first press still looks right - it is the press "
               "after that catches it, because Wake up can never be reached",
    },
    {
        # The write-back, with the `try` left in place so the syntax stays valid.
        "file": _WIN,
        "old": "                        self.app._presence = target\n",
        "new": "                        pass\n",
        "label": "the choice is never recorded, so brain cannot honour it",
        "expect": ["test_every_state_in_the_cycle_is_reachable",
                   "test_the_model_is_not_started_when_the_choice_is_to_stay_asleep"],
        "why": "the machine is right and the app never learns, so the next press "
               "cycles from `detect()` again and SLEEPING is unreachable twice",
    },
    {
        "file": _WIN,
        "old": "                row.set_subtitle(target.detail() if reached\n",
        "new": "                row.set_subtitle(presence.detect(\n"
               "                    local_llm.is_up).detail() if reached\n",
        "label": "the row reports the machine, not the choice",
        "expect": ["test_the_cycle_reports_the_choice_it_reached"],
        "why": "the row keeps promising \"the next question loads it again\" "
               "about a model `brain` now refuses to load. Named against the "
               "cycle's own report, not `_refresh_presence_row` - the first run "
               "had `test_the_row_reports_the_choice_not_the_machine` here, which "
               "calls the helper, so the mutation failed a test the list did not "
               "name and was reported as a survivor with the failure unexplained",
    },
    {
        "file": _WIN,
        "old": "        if getattr(self, \"_presence_cycle_button\", None) is not None:\n"
               "            self._presence_cycle_button.set_label(chosen.action())\n",
        "new": "        if False:\n"
               "            self._presence_cycle_button.set_label(chosen.action())\n",
        "label": "the cycle button keeps its build-time label",
        "expect": ["test_a_released_model_shows_the_verb_for_a_released_model",
                   "test_the_row_reports_the_choice_not_the_machine"],
        "why": "Asleep never offers Wake up, and a released model still says "
               "Free the model",
    },
    {
        "file": _WIN,
        "old": "        if getattr(self, \"_presence_row\", None) is not None:\n"
               "            self._refresh_presence_row()\n",
        "new": "        if False:\n"
               "            self._refresh_presence_row()\n",
        "label": "the row is never refreshed on show",
        "expect": ["test_showing_the_window_rereads_the_choice"],
        "why": "both labels were written once at build time, so the verb is true "
               "only of the moment the window was built. The witness drives "
               "`_on_window_visible`, because a window built in a test reports "
               "`get_visible() == False` and returns before the refresh runs - "
               "which is why nothing caught this the first time",
    },
    {
        # `_run_model` must override an Asleep choice: asking for the model is
        # the one thing that means it.
        "file": _WIN,
        "old": "                    self.app._presence = presence.Presence.ACTIVE\n",
        "new": "                    pass\n",
        "label": "Run the model leaves an Asleep choice standing",
        "expect": ["test_running_the_model_clears_an_asleep_choice"],
        "why": "a running server the next question still refuses - the choice "
               "and the machine disagreeing, which is what `detect()`'s own "
               "docstring says presence exists not to do",
    },
    {
        "file": _BRAIN,
        "old": '            if getattr(self, "_presence", Presence.ACTIVE) is Presence.SLEEPING:\n',
        "new": "            if False:\n",
        "label": "_explain_no_model starts the service anyway",
        "expect": ["test_asleep_refuses_to_start_and_names_the_way_out"],
        "why": "the second defect: the fallback path is the one that defeats "
               "`_use_local_server`'s guard, one statement later",
    },
    {
        "file": _BRAIN,
        "old": '                self.window.set_response(\n'
               '                    "The model is asleep, so nothing was started. Press "\n'
               '                    "\\"Wake up\\" in Settings > Models > Right now to load it, "\n'
               '                    "then send your question again.")\n',
        "new": '                self.window.set_response(\n'
               '                    "no model")\n',
        "label": "the refusal names no way out",
        "expect": ["test_asleep_refuses_to_start_and_names_the_way_out"],
        "why": "a refusal nobody can act on is the refusal this repo records as "
               "useless, however true it is",
    },
    {
        "file": _BRAIN,
        "old": '            if not wake or getattr(self, "_presence", Presence.ACTIVE) is Presence.SLEEPING:\n',
        "new": "            if not wake:\n",
        "label": "the presence half of the wake guard is dropped",
        "expect": ["test_the_guard_is_live_where_it_is_read"],
        "why": "a question starts the server the person asked to leave alone, "
               "which is the whole of what Asleep means",
    },
    # -- the Models panel's release, which the reachable choice made a lie ----
    {
        "file": _MODELS,
        "old": "    if chosen is Presence.SLEEPING:\n",
        "new": "    if False:\n",
        "label": "the Models panel promises a wake it refuses to perform",
        "expect": ["test_an_asleep_model_is_not_promised_a_wake"],
        "why": "Asleep became reachable in the same change, which made the "
               "panel's one sentence - the next question loads it again - false "
               "on the one state a person chose deliberately",
    },
    {
        "file": _MODELS,
        "old": '        set_summary("Nothing to release - it was already stopped. The next "\n'
               '                    "question loads it again.")\n',
        "new": '        set_summary("Released the model - its memory is back.")\n',
        "label": "a release with nothing to release claims a release",
        "expect": ["test_a_release_with_nothing_to_release_does_not_claim_a_release"],
        "why": "stop_service() returns '' for an already-inactive unit, so the "
               "success path cannot tell the two apart on its own",
    },
    {
        "file": _MODELS,
        "old": '                    + " Press \\"Wake up\\" in Settings > Models > Right now.")\n',
        "new": '                    + ".")\n',
        "label": "the asleep refusal names no way out",
        "expect": ["test_an_asleep_model_is_not_promised_a_wake"],
        "why": "the panel owns Free-the-model but not Wake-up, which lives in "
               "Settings - so without the sentence it is a dead end",
    },
    # -- a stored choice must not outlive the machine it was true of ----------
    {
        "file": _PRESENCE,
        "old": "    if chosen is Presence.SLEEPING:\n        return Presence.SLEEPING\n",
        "new": "    if chosen is Presence.SLEEPING or True:\n        return Presence.SLEEPING\n",
        "label": "reconcile always answers Asleep, whatever the machine says",
        "expect": ["test_the_row_does_not_claim_an_unloaded_model_one_a_question_loaded",
                   "test_the_cycle_reports_the_choice_it_reached",
                   "test_the_label_and_the_press_agree"],
        "why": "the row would say Asleep about a machine with nothing running, "
               "which is the over-refusal direction of the same defect. "
               "**test_a_released_model_does_not_claim_to_be_loaded used to be "
               "named here and the runner reported NOT caught** - with the "
               "mutation applied the row reads Asleep's sentence, which also "
               "does not contain 'loaded and answering', so the test passes "
               "either way. It belongs to the *unchanged* mutation below, where "
               "ACTIVE survives and does say it.",
    },
    {
        "file": _PRESENCE,
        "old": ("        return Presence.SLEEPING\n"
                "    return Presence.ACTIVE if is_up() else Presence.DROWSY\n"),
        "new": ("        return Presence.SLEEPING\n"
                "    return chosen\n"),
        "label": "reconcile returns the choice unchanged, so a stale one stands",
        "expect": ["test_the_row_does_not_claim_an_unloaded_model_one_a_question_loaded",
                   "test_a_released_model_does_not_claim_to_be_loaded"],
        "why": "the defect itself: a stored DROWSY outlives the question that "
               "cold-started the model, and the row claims it is not loaded",
    },
    {
        "file": _WIN,
        "old": "        chosen = presence.reconcile(chosen, local_llm.is_up)\n",
        "new": "        pass\n",
        "label": "the row never reconciles the choice with the machine",
        "expect": ["test_the_row_does_not_claim_an_unloaded_model_one_a_question_loaded",
                   "test_a_released_model_does_not_claim_to_be_loaded",
                   "test_showing_the_window_rereads_the_choice"],
        "why": "the rule exists and nothing calls it - which is the shape this "
               "file records for a guard nobody runs",
    },
    {
        "file": _WIN,
        "old": "        current = presence.reconcile(current, local_llm.is_up)\n"
               "        target = current.next()\n",
        "new": "        target = current.next()\n",
        "label": "the cycle presses from the stored choice, so the verb and the "
                 "action disagree",
        "expect": ["test_the_label_and_the_press_agree"],
        "why": "the row labels from the reconciled value and the press used the "
               "raw one: with a stale DROWSY and the machine up the button said "
               "'Free the model' (so Drowsy) and the press went to SLEEPING",
    },
]
