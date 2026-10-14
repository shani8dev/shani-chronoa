"""Mutation set for the secure-field guard, in both directions.

**The defect.** `type_text`, `key`, `portal_type` and `portal_key` typed into
whatever the compositor had focused, on four routes that are all session-wide -
uinput, the RemoteDesktop portal, and XTEST - and a chat surface was refused
while a password box was typed into in exactly the same way. Nothing anywhere
asked what held focus.

**Why a boolean was not enough, and what the three verdicts are for.**
`_secure_field()` returns `("clear"|"secure"|"unknown", reason)`. `"unknown"` is
never returned as `"clear"`: with `toolkit-accessibility` off, or on a desktop
with no AT-SPI at all, the tree publishes nothing, and a guard that reads an
unreachable bus as permission has failed open on the exact field it exists to
protect - the same shape as `_comms_app` once reading an unreachable a11y bus as
"not a chat window". So `"unknown"` **proceeds and discloses** in the reply, and
`ui-require-verified-focus` turns it into a refusal for a person who wants
strictness. `_secure_require` **fails open**, unlike `_comms_override`, because
that key asks for *more* strictness and an unreadable config must not impose it.

**The set carries both directions on every guard that can be over-applied.** A
refusal list that grew one word too far refuses to type into a terminal, and
into a `Gtk.Entry` - which is the role of almost every field a driver wants to
type into, so `entry` in `_SECURE_ROLES` would refuse the whole feature. Both
directions are asserted here, plus the two structural facts the first version
got wrong: the question is **app-agnostic** (the routes are session-wide, so
"what is focused in *this* app" asks about the wrong thing and refused every call
on this box), and it is asked **after the window is raised**, which is the one
moment the answer exists.

**Two instruments that could not see the defect, both recorded so they are not
trusted again.** `_actionable`'s own exclusion carried the string `"password"`
where `get_role_name()` returns `"password text"` - so it matched nothing and
password boxes were left out of the observation for a reason nobody had written
down; correcting the spelling would have *added* them, which is the wrong
direction, and the exclusion is deliberate. And the first version of
`test_the_comms_refusal_...` measured the wrong thing twice: `comms-typing-enabled`
**defaults to true**, so under `conftest`'s keyfile backend the comms refusal
never fired and the test was measuring a granted harness rather than an ordering.
Two other tests took **exactly 30 seconds each** while the guard was placed after
`find_window`, because a consent gate that waits for a window search reads as a
hang.
"""

TEST_FILE = "tests/test_injection_never_types_into_a_secret.py"

_UI = "usr/lib/shani-chronoa/shani_chronoa/ui_driver.py"

_ROLES = '_SECURE_ROLES = ("password text", "password", "pin")'

_EDITABLE_CHECK = (
    '                    if Atspi.StateType.EDITABLE not in states:\n'
    '                        continue\n'
    '                    role = (node.get_role_name() or "").strip().lower()\n'
)

_FOCUSED_CHECK = (
    '                    if Atspi.StateType.FOCUSED not in states:\n'
    '                        continue\n'
    + _EDITABLE_CHECK
)

_MUTATIONS = [
    # -- the shipped defect: nothing asked what held focus --------------------
    {
        "file": _UI,
        "old": '    if verdict == "secure":\n',
        "new": '    if False:\n',
        "label": "**the shipped defect**: a field known to hold a secret is no "
                 "longer refused, so every keystroke lands in it",
        "expect": ["test_a_secret_box_stops_the_keys_before_any_are_sent",
                   "test_no_switch_lifts_the_refusal",
                   "test_a_chord_on_the_xtest_route_is_guarded_too"],
    },
    {
        "file": _UI,
        "old": _ROLES,
        "new": '_SECURE_ROLES = ("password", "pin")',
        "label": "the refusal list loses `\"password text\"` - the role the guard "
                 "exists for, and the one `get_role_name()` actually returns",
        # **Not** `test_a_secret_box_stops_the_keys_before_any_are_sent`: it
        # stubs `_secure_field`, so the tuple this mutation changes is never
        # consulted. Correct the name rather than write a test.
        "expect": ["test_a_focused_password_box_is_the_only_refusal"],
    },
    {
        "file": _UI,
        "old": _FOCUSED_CHECK,
        "new": _EDITABLE_CHECK,
        "label": "the FOCUSED check is dropped, so **any** editable node is read "
                 "as the target - a login form sitting open in the background "
                 "would refuse an ordinary field",
        # **Not** `test_a_desktop_with_nothing_focused_is_unknown`: its only
        # node is unfocused *and* uneditable, so the EDITABLE check below still
        # skips it and that fixture cannot tell this mutation from the next.
        "expect": ["test_only_the_field_that_holds_focus_counts"],
    },
    {
        "file": _UI,
        "old": _EDITABLE_CHECK,
        "new": '                    role = (node.get_role_name() or "").strip().lower()\n',
        "label": "the EDITABLE check is dropped, so a focused **button** is read "
                 "as the field being typed into and reported as clear",
        "expect": ["test_a_focused_button_is_not_the_field_being_typed_into"],
    },

    # -- the over-fix, in both directions -------------------------------------
    {
        "file": _UI,
        "old": _ROLES,
        "new": '_SECURE_ROLES = ("password text", "password", "pin", "entry")',
        "label": "**the over-fix**: `entry` joins the refusal list, and `entry` "
                 "is the role of almost every field a driver wants to type into - "
                 "this would refuse the whole feature",
        "expect": ["test_no_other_role_reads_as_a_secret"],
    },
    {
        "file": _UI,
        "old": "    refusal, note = _secure_refusal(app)\n"
               "    if refusal:\n"
               "        raise GuiUnavailable(refusal)\n"
               "    return note\n",
        "new": "    refusal, note = _secure_refusal(app)\n"
               "    if refusal is not None:\n"
               "        raise GuiUnavailable(refusal)\n"
               "    return note\n",
        "label": "the gate tests `is not None` rather than truthiness, so an "
                 "**empty** refusal stops being one - the exact shape of refusal "
                 "this file already records for `stop_service()` returning `''`",
        "equivalent": True,
        "why": "`_secure_refusal` always returns a `str` - `\"\"` for go, a "
               "sentence for refuse, never `None` - so `if refusal:` and "
               "`if refusal is not None:` reach the same branch for every input "
               "the function can produce. It is here so the set carries an edit "
               "the runner must classify as equivalent rather than as a "
               "survivor: a set whose verdicts are all \"caught\" has never "
               "demonstrated it can tell the two apart.",
    },

    # -- the disclosure, and the door that turns it into a refusal ------------
    {
        "file": _UI,
        "old": '    return ("unknown", "nothing on this desktop reports a focused text field")\n',
        "new": '    return ("clear", "")\n',
        "label": "**failed open on the exact field this exists to protect**: an "
                 "unreachable a11y tree reads as permission, and the keystroke "
                 "goes out with nothing said",
        # **Not** `test_a_silent_tree_types_and_says_so`: it stubs
        # `_secure_field`, so the return this mutation changes is never
        # reached. Correct the name rather than write a test - the runner's
        # "NOT caught by" line is what said so.
        "expect": ["test_a_desktop_with_nothing_focused_is_unknown",
                   "test_an_empty_desktop_is_unknown",
                   "test_a_focused_button_is_not_the_field_being_typed_into"],
    },
    {
        "file": _UI,
        "old": '        if _secure_require():\n',
        "new": '        if False:\n',
        "label": "`ui-require-verified-focus` stops existing as a door, so a "
                 "person who asked to see the focused field first is not asked",
        "expect": ["test_the_require_key_turns_the_silence_into_a_refusal"],
    },
    {
        "file": _UI,
        "old": "    except Exception:  # noqa: BLE001 - an unreadable config is not a stricter one\n"
               "        return False\n",
        "new": "    except Exception:  # noqa: BLE001 - an unreadable config is not a stricter one\n"
               "        return True\n",
        "label": "**fails closed instead of open**: an unreadable config - a "
                 "machine whose schema predates the key - imposes strictness and "
                 "refuses every keystroke the driver sends",
        "expect": ["test_an_unreadable_config_is_not_a_stricter_one"],
    },

    # -- the refusal's own sentence -------------------------------------------
    {
        "file": _UI,
        "old": '        if label and label != _SECURE_REQUIRE_KEY:\n'
               '            return label\n',
        "new": '        if False:\n'
               '            return label\n',
        "label": "the refusal names the raw key again, so it sends whoever "
                 "reads it hunting for a switch no screen spells that way - the "
                 "defect measured 47 times across 20 files and closed in one "
                 "place",
        "expect": ["test_the_require_key_turns_the_silence_into_a_refusal",
                   "test_the_switch_that_turns_the_silence_into_a_refusal_is_reachable"],
    },
    {
        "file": _UI,
        "old": 'f"hidden, and no switch makes that safe.", "")\n',
        "new": 'f"hidden.", "")\n',
        "label": "the refusal stops saying that **no switch makes it safe**, so "
                 "it reads as though a setting would lift it",
        "expect": ["test_no_switch_lifts_the_refusal"],
    },

    # -- the four call sites, each with its own witness -----------------------
    {
        "file": _UI,
        "old": "    refusal, note = _secure_refusal(app)\n"
               "    if refusal:\n"
               "        raise GuiUnavailable(refusal)\n"
               "    return note\n",
        "new": "    refusal, note = _secure_refusal(app)\n"
               "    if False:\n"
               "        raise GuiUnavailable(refusal)\n"
               "    return note\n",
        "label": "the gate never raises, so all four sites wave keystrokes "
                 "through - a chord, a string, a portal and an XTEST window alike",
        "expect": ["test_a_secret_box_stops_the_keys_before_any_are_sent",
                   "test_a_chord_through_the_portal_is_held_to_the_same_rule",
                   "test_a_chord_on_the_xtest_route_is_guarded_too",
                   "test_the_require_key_turns_the_silence_into_a_refusal"],
    },
    {
        "file": _UI,
        "old": '    secure_note = _secure_gate(app)\n'
               '    try:\n'
               '        with _portal_input() as remote:\n'
               '            remote.type_text(text)\n',
        "new": '    secure_note = ""\n'
               '    try:\n'
               '        with _portal_input() as remote:\n'
               '            remote.type_text(text)\n',
        "label": "`portal_type` loses its gate - **the native Wayland route**, "
                 "where no X window exists and the app is never raised",
        "expect": ["test_a_secret_box_stops_the_keys_before_any_are_sent",
                   "test_a_silent_tree_types_and_says_so",
                   "test_the_require_key_turns_the_silence_into_a_refusal"],
    },
    {
        "file": _UI,
        "old": '        secure_note = _secure_gate(app)\n'
               '        box = window_box(window_id)\n',
        "new": '        secure_note = ""\n'
               '        box = window_box(window_id)\n',
        "label": "`type_text` loses its gate, and with it the **ordering** - the "
                 "question is asked after the window is raised, which is the one "
                 "moment the answer exists",
        # `test_a_secret_box_stops_the_keys_before_any_are_sent` drives
        # `portal_type`, so it passes either way here - a witness that passes
        # with the defect it names reads as coverage.
        "expect": ["test_the_field_is_asked_after_the_window_is_raised"],
    },
    {
        "file": _UI,
        "old": "    _secure_gate(app)\n"
               "    from shani_chronoa import portal\n",
        "new": "    from shani_chronoa import portal\n",
        "label": "`portal_key` loses its gate - a **chord** can paste into a "
                 "password box as readily as a string can type into one",
        "expect": ["test_a_chord_through_the_portal_is_held_to_the_same_rule"],
    },
    {
        "file": _UI,
        "old": '    secure_note = _secure_gate(app)\n'
               '    proc = _xdotool(["key", keys])\n',
        "new": '    secure_note = ""\n'
               '    proc = _xdotool(["key", keys])\n',
        "label": "`key`'s XTEST route loses its gate, so the chord path is "
                 "unguarded while `type_text` still is",
        "expect": ["test_a_chord_on_the_xtest_route_is_guarded_too"],
    },

    # -- the sibling exclusion, and the spelling that matched nothing --------
    #   Both mutations below are **equivalent**, and that is the measured finding
    #   rather than a gap: `if role == "password text": return False` sits above
    #   `role in ("entry", "text", "combo box")`, and neither spelling is in that
    #   tuple, so the line changes no answer for any input. It is kept because it
    #   is where a reader looks, and `test_the_exclusion_is_what_carries_the_
    #   rule_not_the_role_tuple` holds the two halves in step - so the guard
    #   cannot be deleted *and* the role quietly accepted, which is the one edit
    #   that would turn `drive` into something that addresses a login box.
    {
        "file": _UI,
        "old": '        if role == "password text":\n'
               '            return False\n',
        "new": '        if False:\n'
               '            return False\n',
        "label": "`_actionable` stops excluding password boxes - currently "
                 "**equivalent**, because no secret role is in the accepted "
                 "tuple the line above already refuses",
        "equivalent": True,
        "why": "`role in (\"entry\", \"text\", \"combo box\")` is False for "
               "every spelling of a secret field, so deleting the explicit "
               "exclusion reaches the same answer for every input. The two "
               "mutations are kept as the pair that proves the accepted tuple "
               "is what is carrying the rule, and a test asserts they cannot "
               "drift apart.",
    },
    {
        "file": _UI,
        "old": '        if role == "password text":\n'
               '            return False\n',
        "new": '        if role == "password":\n'
               '            return False\n',
        "label": "the exclusion is **corrected** to the spelling that matched "
                 "nothing - also **equivalent**, for the same reason",
        "equivalent": True,
        "why": "the same proof: `\"password\"` is not in "
               "(`\"entry\", \"text\", \"combo box\"`) either, so both spellings "
               "fall through to the same answer. It is here because this is the "
               "correction a reader is most likely to make \"while here\", and "
               "the set should say what it would and would not change.",
    },
]

#: Every mutation in one list, so `tools/mutate.py --check-anchors` can count
#: them without importing `MUTATIONS` twice.
MUTATIONS = _MUTATIONS
