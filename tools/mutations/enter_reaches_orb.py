"""Mutation set for the orb's tooltip promising a key that reached nothing.

The orb carried `Start listening (Enter)` while `toggle-listening` had **no
accelerator anywhere in the tree** - 13 `set_accels_for_action` calls, none of them
that one - and Enter was the composer's *send*, which returns early on an empty
composer. So the keypress a newcomer makes after reading that tooltip did nothing
at all, and this repository had already written the rule for it twenty lines below
the defect: *"a hint that names a key which is not bound is worse than no hint"*.

The fix routes Enter-on-nothing to the orb's own action. Every mutation below is
a way that routing can be wrong, and the two that matter most are the first two:
**the shipped behaviour restored verbatim**, and the over-fix that swallows
Enter-to-send - which is the convention every chat UI has *and* the only route for
a person who cannot reach the Send button, so a fix that broke it would pass every
other test in the file.
"""

TEST_FILE = "tests/test_enter_reaches_the_orb.py"

_WINDOW = "usr/lib/shani-chronoa/shani_chronoa/gui/window.py"

_ROUTE = (
    '        if self._composer_is_empty():\n'
    '            self._app.activate_action("toggle-listening", None)\n'
    '            return True\n'
)

MUTATIONS = [
    {
        "file": _WINDOW,
        "old": _ROUTE,
        "new": "",
        # The shipped behaviour, verbatim: Enter on an empty composer asks for
        # nothing, so the tooltip's promise resolves to silence.
        "label": "Enter on nothing does not reach the orb (the shipped bug)",
        "expect": ["test_the_orb_names_a_key_that_reaches_the_orb",
                   "test_the_key_reaches_the_very_action_the_orb_carries",
                   "test_enter_on_an_empty_composer_reaches_listening",
                   "test_the_keypad_enter_does_too",
                   "test_whitespace_only_is_empty_because_submit_would_have_discarded_it"],
    },
    {
        "file": _WINDOW,
        "old": _ROUTE + "        self._on_input_activate(self._input_entry)\n",
        "new": '        self._app.activate_action("toggle-listening", None)\n',
        # The over-fix. Enter-to-send is the a11y path as well as the convention,
        # so this is the mutation that must never survive.
        # `test_the_orb_names_a_key_that_reaches_the_orb` was named here first
        # and the runner reported it NOT caught: with the over-fix *every*
        # Enter asks for toggle-listening, so that witness passes either way.
        # Correcting the name rather than adding a test.
        "label": "Enter always opens the microphone and never sends (the over-fix)",
        "expect": ["test_enter_with_text_still_sends"],
    },
    {
        "file": _WINDOW,
        "old": '            self._app.activate_action("toggle-listening", None)\n',
        "new": '            self._app.activate_action("reply-style", None)\n',
        # The shape of the `reply-style:qwen3-8b` defect this file records for a
        # menu built from a loop that leaked a variable: every row looked right
        # and every row named the wrong action. Only reading the *name* sees it.
        "label": "the key reaches the wrong action (every row right, every row wrong)",
        "expect": ["test_the_key_reaches_the_very_action_the_orb_carries",
                   "test_the_orb_names_a_key_that_reaches_the_orb"],
    },
    {
        "file": _WINDOW,
        "old": '        if getattr(self, "_attachments", ()):\n            return False\n',
        "new": "",
        # Three files attached and no words yet is somebody about to type about
        # three files, not an invitation to open a microphone.
        "label": "attached files no longer count as something to send",
        "expect": ["test_enter_with_attachments_does_not_open_the_microphone"],
    },
    {
        "file": _WINDOW,
        "old": "        return self._pending_question is None\n",
        "new": "        return True\n",
        # The trust moment: a question is answered from its own buttons.
        "label": "Enter opens the microphone in the middle of a question",
        "expect": ["test_enter_during_a_question_does_not_open_the_microphone"],
    },
    {
        "file": _WINDOW,
        "old": "        if self._input_text().strip():\n",
        "new": "        if self._input_text():\n",
        # `strip()` is what keeps this agreeing with `_submit_input`, which
        # compares `raw.strip()`. Without it the two drift apart silently.
        "label": "whitespace-only is no longer empty, disagreeing with _submit_input",
        # `test_enter_with_text_still_sends` was named here and the runner
        # reported it NOT caught: its text is non-empty, and stripping or not
        # stripping a non-empty string does not change it.
        "expect": ["test_whitespace_only_is_empty_because_submit_would_have_discarded_it"],
    },
    {
        "file": _WINDOW,
        "old": "        if shift:\n"
               "            return False            # let it through: a newline\n",
        "new": "        if False:\n"
               "            return False\n",
        # Shift+Enter is the only way to type two lines.
        "label": "Shift+Enter opens the microphone instead of making a newline",
        "expect": ["test_shift_enter_is_still_a_newline"],
    },
]