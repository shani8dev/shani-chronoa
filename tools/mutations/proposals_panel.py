"""Mutation set for the Memory panel's "Proposed" section - the door a person walks through.

**Why the panel needs a set when the acceptor already has one.** `sleep_proposals.py`
guards `decide_candidate`. Nothing in that file can see whether the section is
rendered, whether the section is rendered *empty*, or whether the note survives
the press that made it necessary. Those are panel facts, and one of them was a
real defect in this code before it was a test.

**The defect this set was written for.** `_proposals_section()` returned `None`
when nothing was waiting. Answering the last proposal is *precisely* the moment
nothing is waiting - so the section vanished on the press, and with it the note
that said what the press had done. Measured, not reasoned about: with the store
write failing, pressing Keep made the group disappear and the page read "No
facts remembered yet", which reports a failed write as a clean empty store. The
`_NOTE_DIES_WITH_THE_ROW` mutation below is that shipped bug, re-applied.

**Both directions on every guard that can be over-applied.** A section that is
never empty is absence-as-noise; a section that hides what is waiting while the
gate is closed quotes a sentence from last night to somebody who turned memory
off. A note that appears without having read the outcome reports an outcome
nobody asked about.
"""

TEST_FILE = "tests/test_memory_panel_proposals.py"

_PANEL = "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/memory.py"

# --- the empty condition, in all three directions --------------------------

_NOTE_DIES_WITH_THE_ROW = (
    "        if not denied and not rows and not self._decision_note:\n"
    "            return None\n",
    "        if not denied and not rows:\n"
    "            return None\n",
)

_SECTION_ALWAYS = (
    "        if not denied and not rows and not self._decision_note:\n"
    "            return None\n",
    "        if False:\n"
    "            return None\n",
)

_NO_NOTE_ROW = (
    "        if self._decision_note:\n"
    "            note = common.row(PROPOSED_ANSWERED_TITLE, self._decision_note)\n"
    "            note.add_css_class(NOTE_CSS)\n"
    "            _add(group, note)\n",
    "",
)

_NOTE_HARDCODE = (
    "        said = _DECISION_SENTENCES.get(outcome, _UNDESCRIBED)\n",
    "        said = \"Remembered - it will be in your memory now.\"\n",
)

_OUTCOMES_COLLAPSED = (
    "_DECISION_SENTENCES = {\n"
    "    \"kept\": \"Remembered - it will be in your memory now.\",\n"
    "    \"kept-unrecorded\": \"You agreed to it, but it is NOT in your memory.\",\n",
    "_DECISION_SENTENCES = {\n"
    "    \"kept\": \"Remembered - it will be in your memory now.\",\n"
    "    \"kept-unrecorded\": \"Remembered - it will be in your memory now.\",\n",
)

#: **The shipped defect, verbatim.** Before the fix the reason was only the
#: fallback for an outcome this table does not name, so a *known* outcome
#: carried none and `kept-unrecorded` rendered as "You agreed to it, but it is
#: NOT in your memory: " - a sentence with a colon and nothing after it. Found
#: by the test written to check the reason was carried at all, against the
#: unmutated tree, which is the direction that matters.
_NO_REASON_APPENDED = (
    "        if reason and outcome not in _REASON_IS_A_VERB:\n"
    "            said = f\"{said} {reason}\"\n",
    "        if False:\n"
    "            said = f\"{said} {reason}\"\n",
)

# --- the exception path ----------------------------------------------------

_NO_TRY = (
    "        try:\n"
    "            from shani_chronoa.senses.memory import decide_candidate\n"
    "\n"
    "            outcome, reason = decide_candidate(\n"
    "                proposal.session_id, proposal.index, accept, self._store)\n"
    "        except Exception:  # noqa: BLE001 - a refused decision must not take the window down\n"
    "            logger.warning(\"could not decide a proposed fact\", exc_info=True)\n"
    "            self._decision_note = \"That could not be recorded, so nothing changed.\"\n"
    "            self.refresh()\n"
    "            return\n",
    "        from shani_chronoa.senses.memory import decide_candidate\n"
    "\n"
    "        outcome, reason = decide_candidate(\n"
    "            proposal.session_id, proposal.index, accept, self._store)\n",
)

_NO_NOISE_LOG = (
    "        except Exception:  # noqa: BLE001 - a refused decision must not take the window down\n"
    "            logger.warning(\"could not decide a proposed fact\", exc_info=True)\n",
    "        except Exception:  # noqa: BLE001 - a refused decision must not take the window down\n"
    "            pass\n",
)

# --- the gate --------------------------------------------------------------

_NO_GATE = (
    "            denied = _consent_error()\n"
    "            rows = [] if denied else pending_proposals()\n",
    "            denied = \"\"\n"
    "            rows = pending_proposals()\n",
)

_GATE_ONLY_STOPS_LISTING = (
    "            denied = _consent_error()\n"
    "            rows = [] if denied else pending_proposals()\n",
    "            denied = _consent_error()\n"
    "            rows = pending_proposals()\n",
)

_BUTTONS_WITHOUT_GATE = (
    "        if denied:\n"
    "            _add(group, common.row(PROPOSED_OFF_TITLE, denied))\n"
    "            return group\n",
    "",
)

_NO_READ_TRY = (
    "        try:\n"
    "            from shani_chronoa.senses.memory import _consent_error, pending_proposals\n"
    "\n"
    "            denied = _consent_error()\n"
    "            rows = [] if denied else pending_proposals()\n"
    "        except Exception:  # noqa: BLE001 - a missing sleep pass proposes nothing\n"
    "            logger.warning(\"could not read the sleep proposals\", exc_info=True)\n"
    "            return None\n",
    "        from shani_chronoa.senses.memory import _consent_error, pending_proposals\n"
    "\n"
    "        denied = _consent_error()\n"
    "        rows = [] if denied else pending_proposals()\n",
)

# --- the buttons -----------------------------------------------------------

KEEP_TRUE = '            keep.connect("clicked", lambda _b, p=proposal: self._decide(p, True))\n'
KEEP_FALSE = '            keep.connect("clicked", lambda _b, p=proposal: self._decide(p, False))\n'
DROP_FALSE = '            drop.connect("clicked", lambda _b, p=proposal: self._decide(p, False))\n'
DROP_TRUE = '            drop.connect("clicked", lambda _b, p=proposal: self._decide(p, True))\n'
LATE_KEEP_TRUE = '            keep.connect("clicked", lambda _b: self._decide(proposal, True))\n'
LATE_DROP_FALSE = '            drop.connect("clicked", lambda _b: self._decide(proposal, False))\n'


#: The two `connect` calls are not adjacent - `drop = Gtk.Button(...)` sits
#: between them - so both are *pairs* of one-line anchors rather than one
#: block. Each half is checked on its own: an anchor that does not match
#: would silently skip half a mutation and the tally would still read as if
#: both had run.
_KEEP_P = (KEEP_TRUE, DROP_FALSE)
_KEEP_N = (KEEP_FALSE, DROP_TRUE)
_LATE_N = (LATE_KEEP_TRUE, LATE_DROP_FALSE)


_ONLY_KEEP = (
    "            drop = Gtk.Button(label=DROP_LABEL)\n"
    "            drop.connect(\"clicked\", lambda _b, p=proposal: self._decide(p, False))\n"
    "            suffix = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)\n"
    "            suffix.append(drop)\n"
    "            suffix.append(keep)\n",
    "            suffix = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)\n"
    "            suffix.append(keep)\n",
)

# --- the provenance line ---------------------------------------------------

_NO_TURN = (
    "    said = f\"turn {int(proposal.source_index) + 1}\"\n",
    "    said = f\"turn {int(proposal.source_index)}\"\n",
)

_NO_SUMMARY = (
    "    summary = \" \".join(str(proposal.summary or \"\").split())\n"
    "    if summary:\n"
    "        return f\"From that conversation - {said}. It was about: {_clip(summary, 110)}\"\n",
    "    summary = \"\"\n"
    "    if summary:\n"
    "        return f\"From that conversation - {said}. It was about: {_clip(summary, 110)}\"\n",
)

_ROLE_ALWAYS = (
    "    if proposal.source_role and proposal.source_role != \"user\":\n"
    "        said += f\" by {proposal.source_role}\"\n",
    "    if proposal.source_role:\n"
    "        said += f\" by {proposal.source_role}\"\n",
)

_NO_CLIP = (
    "            _add(group, common.row(_clip(proposal.text, 90),\n",
    "            _add(group, common.row(proposal.text,\n",
)

# --- placement -------------------------------------------------------------

_SECTION_BELOW_FACTS = (
    "        proposals = self._proposals_section()\n"
    "        if proposals is not None:\n"
    "            self._content.append(proposals)\n",
    "",
)

# --- the shared acceptor, both directions ---------------------------------

_PANEL_DECIDES = (
    "            outcome, reason = decide_candidate(\n"
    "                proposal.session_id, proposal.index, accept, self._store)\n",
    "            kept = self._store.add(proposal.text)\n"
    "            outcome, reason = decide_candidate(\n"
    "                proposal.session_id, proposal.index, accept, self._store)\n",
)

MUTATIONS = [
    # --- the empty condition: the shipped bug, and its two over-fixes ----
    {
        "label": "the note vanishes on the press that made it necessary",
        "file": _PANEL,
        "old": _NOTE_DIES_WITH_THE_ROW[0],
        "new": _NOTE_DIES_WITH_THE_ROW[1],
        "why": "**This one shipped.** `_proposals_section` returned None the "
               "moment the last proposal was answered, so the section - and the "
               "note in it - left with the row. Measured: with the store write "
               "failing, the page then reads \"No facts remembered yet\", which "
               "is a failed write reported as a clean empty store.",
        "expect": ["test_the_note_carries_the_reason_and_not_only_the_verdict",
                   "test_a_write_that_failed_is_said_to_have_failed"],
    },
    {
        "label": "the section is always on the page, empty or not",
        "file": _PANEL,
        "old": _SECTION_ALWAYS[0],
        "new": _SECTION_ALWAYS[1],
        "why": "The over-fix: the note is now reachable, at the price of a "
               "permanent heading on a panel someone opened to look at their "
               "facts. Absence as noise - the thing this section was written "
               "against, one page over from a panel that *must* say \"no "
               "facts remembered\" because it is the thing being asked about.",
        "expect": ["test_nothing_waiting_means_no_section_at_all"],
    },
    {
        "label": "the note row is never added",
        "file": _PANEL,
        "old": _NO_NOTE_ROW[0],
        "new": _NO_NOTE_ROW[1],
        "why": "The section stays up (the condition now holds) and says nothing "
               "- so the fix for the shipped bug is reverted by the next edit "
               "rather than by a mutation. Measured to be caught, because a "
               "condition that keeps a group alive for no visible reason is "
               "indistinguishable from a correct one until a press happens.",
        "expect": ["test_a_kept_answer_is_visible_rather_than_logged"],
    },
    {
        "label": "every outcome reads as remembered",
        "file": _PANEL,
        "old": _OUTCOMES_COLLAPSED[0],
        "new": _OUTCOMES_COLLAPSED[1],
        "why": "`kept` and `kept-unrecorded` are different facts about a "
               "person, and the panel is where they are read rather than "
               "logged. This is the exact row that makes `kept-unrecorded` "
               "worth having.",
        "expect": ["test_a_write_that_failed_is_said_to_have_failed",
                   "test_the_note_carries_the_reason_and_not_only_the_verdict"],
    },
    {
        "label": "the note ignores the outcome entirely",
        "file": _PANEL,
        "old": _NOTE_HARDCODE[0],
        "new": _NOTE_HARDCODE[1],
        "why": "The over-fix for the one above, and the one a plausible edit "
               "makes: the row is always there, so every test about the row's "
               "*existence* passes, and only the test that pressed and read the "
               "wrong outcome survives to fail.",
        "expect": ["test_the_note_carries_the_reason_and_not_only_the_verdict",
                   "test_a_write_that_failed_is_said_to_have_failed"],
    },
    {
        "label": "the acceptor's reason is dropped from the note",
        "file": _PANEL,
        "old": _NO_REASON_APPENDED[0],
        "new": _NO_REASON_APPENDED[1],
        "why": "`kept-unrecorded` is a prefix and a reason; the prefix alone "
               "\"You agreed to it, but it is NOT in your memory:\" is a "
               "sentence with a colon and nothing after it. Kept because the "
               "reason is what tells a person whether to try again.",
        "expect": ["test_the_note_carries_the_reason_and_not_only_the_verdict"],
    },

    # --- the exception path ----------------------------------------------
    {
        "label": "an acceptor that raises takes the window down",
        "file": _PANEL,
        "old": _NO_TRY[0],
        "new": _NO_TRY[1],
        "why": "A refused decision - a store that vanished, a digest that "
               "could not be written back - becomes a traceback out of a "
               "button handler, which in GTK tears the run down. The acceptor "
               "refuses rather than raises, but the panel's other caller of it "
               "is a GUI thread and a guard that assumes every caller returns "
               "is not a guard.",
        "expect": ["test_an_acceptor_that_raised_is_reported_as_not_recorded"],
    },
    {
        "label": "a refused decision is swallowed without a log line",
        "file": _PANEL,
        "old": _NO_NOISE_LOG[0],
        "new": _NO_NOISE_LOG[1],
        "why": "The row still appears, so every test about the row passes. What "
               "disappears is the only record that the write path raised at "
               "all, which is the difference between a rare failure and one "
               "that is merely rare and silent.",
        # The runner named a different test than this one and reported
        # "failed for another reason" - its rule for a witness that names
        # the wrong test. Corrected by name; no new test was written,
        # because the coverage was already there and mine pointed beside it.
        "expect": ["test_a_refused_decision_is_logged_not_only_shown"],
    },

    # --- the gate, in all three directions ------------------------------
    {
        "label": "the section offers Keep with the gate closed",
        "file": _PANEL,
        "old": _NO_GATE[0],
        "new": _NO_GATE[1],
        "why": "`decide_candidate` refuses, so the press is answered \"nothing "
               "changed\" - a control that can only ever refuse, which is the "
               "same shape as the button that is present and does nothing.",
        "expect": ["test_with_consent_off_the_row_explains_and_offers_no_buttons",
                   "test_consent_off_does_not_even_list_what_is_waiting"],
    },
    {
        "label": "the gate explains but still lists what is waiting",
        "file": _PANEL,
        "old": _GATE_ONLY_STOPS_LISTING[0],
        "new": _GATE_ONLY_STOPS_LISTING[1],
        "equivalent": True,
        "why": "Equivalent **because of the `if denied:` three lines below it**: "
               "the refusal row is added and the group is returned before the "
               "loop that would render `rows` is ever reached, so `rows` is not "
               "read while `denied` holds however it was computed. The "
               "`[] if denied else` clause is therefore unreachable defence in "
               "depth rather than the guard - which is worth recording, because "
               "the clause reads like the gate and the early return is what "
               "actually is one. The gate's own mutation above removes the early "
               "return and is caught; this one cannot be, and a witness that "
               "passed either way would read as coverage.",
    },
    {
        "label": "the refusal row is removed, leaving the buttons",
        "file": _PANEL,
        "old": _BUTTONS_WITHOUT_GATE[0],
        "new": _BUTTONS_WITHOUT_GATE[1],
        "why": "With `rows` empty this section would be an empty group - so the "
               "press has nothing to press. The mutation that is unreachable "
               "*and* the one that would make the section a permanently empty "
               "heading if the gate were also weakened. Both halves are "
               "guarded.",
        "expect": ["test_with_consent_off_the_row_explains_and_offers_no_buttons"],
    },
    {
        "label": "a sleep pass that cannot be read takes the panel down",
        "file": _PANEL,
        "old": _NO_READ_TRY[0],
        "new": _NO_READ_TRY[1],
        "why": "The panel is the one surface that must open when nothing else "
               "works. An ImportError from the sense, raised while *building* "
               "the page, means a person cannot open Memory at all - the one "
               "panel whose job is to show what is held.",
        # The runner named a different test than this one and reported
        # "failed for another reason" - its rule for a witness that names
        # the wrong test. Corrected by name; no new test was written,
        # because the coverage was already there and mine pointed beside it.
        "expect": ["test_a_sleep_pass_that_cannot_be_read_does_not_take_the_panel_down",
                   "test_a_sleep_pass_that_cannot_be_read_does_not_take_the_panel_down"],
    },

    # --- the buttons ----------------------------------------------------
    {
        "label": "Keep discards and Not now keeps",
        "file": _PANEL,
        "old": list(_KEEP_P),
        "new": list(_KEEP_N),
        "why": "Both buttons exist, both look like answers, and the acceptor is "
               "still the shared one - so every guard holds and the only thing "
               "wrong is the word. A panel that asks a question and then lies "
               "about its own buttons is worse than no buttons.",
        "expect": ["test_keep_stores_the_fact_where_the_panel_says_it_is_stored",
                   "test_not_now_drops_it_and_stores_nothing"],
    },
    {
        "label": "each row's buttons decide about the last proposal",
        "file": _PANEL,
        "old": [KEEP_TRUE, DROP_FALSE],
        "new": list(_LATE_N),
        "why": "Dropping the default argument is the one edit that passes every "
               "single-proposal test - the page renders one row, the press "
               "keeps that row's sentence, and the note is right. It needs two "
               "proposals to be visible, which is why that fixture exists.",
        "expect": ["test_two_proposals_each_carry_their_own_buttons"],
    },
    {
        "label": "there is only one way to answer",
        "file": _PANEL,
        "old": _ONLY_KEEP[0],
        "new": _ONLY_KEEP[1],
        "why": "The section becomes one-sided: a person can agree to something "
               "sleep noticed and cannot decline it, so every proposal has to be "
               "answered by storing it. The over-fix of a section that felt "
               "too eager to act.",
        "expect": ["test_a_waiting_proposal_appears_with_both_answers",
                   "test_not_now_drops_it_and_stores_nothing"],
    },

    # --- the provenance line --------------------------------------------
    {
        "label": "the row names the wrong turn",
        "file": _PANEL,
        "old": _NO_TURN[0],
        "new": _NO_TURN[1],
        "why": "`Candidate.source_index` is zero-based throughout "
               "consolidation. Dropping the +1 points a person at the message "
               "before the one the sentence came from, and it is off by one in "
               "the direction that still looks plausible.",
        "expect": ["test_the_row_says_which_turn_the_sentence_came_from"],
    },
    {
        "label": "the row does not say what the conversation was about",
        "file": _PANEL,
        "old": _NO_SUMMARY[0],
        "new": _NO_SUMMARY[1],
        "why": "Agreeing needs the context the sentence was read out of. A bare "
               "sentence with a turn number is a quote a person has to take on "
               "faith.",
        "expect": ["test_the_row_names_what_the_conversation_was_about"],
    },
    {
        "label": "the row names the speaker as if it were an aside",
        "file": _PANEL,
        "old": _ROLE_ALWAYS[0],
        "new": _ROLE_ALWAYS[1],
        "why": "The over-fix: the role is *shown* more often, so a test for the "
               "presence of the word passes while every user-sourced proposal - "
               "which is all of them, because sleep reads what a person said - "
               "gains a misleading \"by user\".",
        # The runner named a different test than this one and reported
        # "failed for another reason" - its rule for a witness that names
        # the wrong test. Corrected by name; no new test was written,
        # because the coverage was already there and mine pointed beside it.
        "expect": ["test_the_row_names_a_speaker_that_is_not_the_person"],
    },
    {
        "label": "the row shows the whole sentence, however long",
        "file": _PANEL,
        "old": _NO_CLIP[0],
        "new": _NO_CLIP[1],
        "why": "A proposal is a span out of a conversation and may be a "
               "paragraph. Without the clip one row can be taller than the "
               "panel, and the button that answers it is pushed off the bottom "
               "of a page whose only job is that button.",
        # The runner named a different test than this one and reported
        # "failed for another reason" - its rule for a witness that names
        # the wrong test. Corrected by name; no new test was written,
        # because the coverage was already there and mine pointed beside it.
        "expect": ["test_a_long_proposal_is_clipped_so_the_button_stays_on_screen",
                   "test_a_long_proposal_is_clipped_so_the_button_stays_on_screen"],
    },

    # --- placement -------------------------------------------------------
    {
        "label": "what needs answering is below what is already answered",
        "file": _PANEL,
        "old": _SECTION_BELOW_FACTS[0],
        "new": _SECTION_BELOW_FACTS[1],
        "why": "Below the fact groups it is the last thing on a panel that is "
               "mostly things already settled, at the bottom of a scroller - "
               "and the one row on the page anyone has to *act* on is the one "
               "you have to scroll for.",
        "expect": ["test_the_section_is_above_the_facts_because_it_is_the_only_"
                   "action",
                   "test_a_waiting_proposal_appears_with_both_answers"],
    },

    # --- the shared acceptor, both directions ---------------------------
    {
        "label": "the panel writes to the store beside calling the acceptor",
        "file": _PANEL,
        "old": _PANEL_DECIDES[0],
        "new": _PANEL_DECIDES[1],
        "why": "Not a plausible edit so much as the documented one. Two "
               "implementations of \"did the person agree to this?\" is exactly "
               "the pair that drifts, and this one writes to durable memory - so "
               "the row can say Remembered while the digest still holds the "
               "proposal and offers it again tomorrow.",
        "expect": ["test_the_panel_reaches_the_shared_acceptor_and_writes_"
                   "nothing_itself"],
    },
]