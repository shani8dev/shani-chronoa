"""Mutation set for the mascot's event bus and its wiring.

`tests/test_mascot_events.py` is 14 tests that pin three things at once: the
slot's own semantics (emit/install/uninstall, never-raises, thread hop), the
two tables (`BY_NAME` and `mascot._EVENT_FACE`), and the five producer call
sites via source text. This set proves each pin bites.

**The deliberate hard cases are the value swaps.** Removing an entry from
`BY_NAME` would be caught immediately by the membership test —
`test_every_bus_kind_except_step_is_a_face_mascot_can_draw`. *Swapping* a face
for another face that is already drawn (`finished -> build-fail`,
`error -> build-ok`, `settled` reading granted twice) keeps every table-valid
invariant true and is caught by exactly one thing:
`test_each_named_producer_emits_its_exact_kind_and_text`, whose kinds are
**hardcoded in the test**, not read back from `BY_NAME`. A test that reads the
table it is guarding agrees with whatever the table says — the mirror this
suite exists to avoid. The first draft of that test read `BY_NAME` back and was
rewritten before this set was written; a set that runs against the mirror
would be 14 green organisms saying nothing.

**The routing mutations hop through the one place the thread decision can
turn.** `_main_thread` recorded as `None` and the hop condition negated both
route a producer directly on the caller's thread — the undefined-behaviour
shape `ask_bridge`'s docstring warns about — and both are caught by the same
hop test standing in for `GLib.idle_add`.

Every anchor was counted (`count == 1`) against the live files before being
written here, the way `mascot_wiring.py` says everyone should.
"""

from __future__ import annotations

_BUS = "usr/lib/shani-chronoa/shani_chronoa/mascot_events.py"
_MASCOT = "usr/lib/shani-chronoa/shani_chronoa/mascot.py"
_ASK = "usr/lib/shani-chronoa/shani_chronoa/ask_bridge.py"
_CONV = "usr/lib/shani-chronoa/shani_chronoa/app/conversation.py"

TEST_FILE = "tests/test_mascot_events.py"

MUTATIONS = [
    # -- the slot: an empty listener is the common case, not an edge case ------
    {
        "file": _BUS,
        "old": """    fn = _listener
    if fn is None:
        return
""",
        "new": """    fn = _listener
""",
        "label": "emit on an empty slot calls None - the companion is off by "
                 "default, so every producer would raise on the ordinary case",
        "expect": [
            "test_emit_without_a_listener_is_a_no_op",
        ],
    },
    {
        "file": _BUS,
        "old": """    try:
        fn(kind, text)
    except Exception:  # noqa: BLE001 - a face is never worth a failed turn
        logger.debug("mascot event %r could not be delivered", kind, exc_info=True)
""",
        "new": """    fn(kind, text)
""",
        "label": "a raising listener raises through emit again - the face "
                 "would be worth a failed turn, undoing the bus's one promise",
        "expect": [
            "test_emit_never_raises_even_and_especially_when_the_listener_raises",
        ],
    },
    {
        "file": _BUS,
        "old": """    if listener is None or listener is _listener:""",
        "new": """    if listener is None:""",
        "label": "uninstall(anyone) clears the slot - a window destroyed late "
                 "unlists the mascot that replaced it, the exact debt the "
                 "`listener is _listener` guard exists for",
        "expect": [
            "test_uninstall_clears_only_when_it_is_the_same_listener",
        ],
    },
    {
        "file": _BUS,
        "old": """    global _listener
    _listener = listener
""",
        "new": """    global _listener
""",
        "label": "install() keeps nothing - the mascot looks like it is "
                 "listening, producers no-op forever, and the only visible "
                 "symptom is a companion that never reacts to anything",
        "expect": [
            "test_emit_reaches_the_installed_listener",
        ],
    },

    # -- the tables: swaps that keep every membership invariant true ----------
    {
        "file": _BUS,
        "old": """    "finished": "build-ok",     # the turn answered""",
        "new": """    "finished": "build-fail",   # the turn answered""",
        "label": "an answered turn wears the concerned face - a swap to a "
                 "kind that is still a real, drawn face, so table-validity "
                 "proves nothing and only the hardcoded producer test sees it",
        "expect": [
            "test_each_named_producer_emits_its_exact_kind_and_text",
        ],
    },
    {
        "file": _BUS,
        "old": """    "error":    "build-fail",   # the turn failed""",
        "new": """    "error":    "build-ok",     # the turn failed""",
        "label": "a failed turn wears the excited face - the happy twin of "
                 "the finished swap, same shape, same hard witness",
        "expect": [
            "test_each_named_producer_emits_its_exact_kind_and_text",
        ],
    },
    {
        "file": _BUS,
        "old": """    emit(BY_NAME["granted"] if granted else BY_NAME["refused"], text)""",
        "new": """    emit(BY_NAME["granted"], text)""",
        "label": "a refusal or a timeout wears the allowed face - decide()'s "
                 "one-face-for-four-exits collapses to two happy exits",
        "expect": [
            "test_each_named_producer_emits_its_exact_kind_and_text",
        ],
    },
    {
        "file": _BUS,
        "old": """    "step":     "step",         # a tool is running - a bubble, not a face""",
        "new": """    "step":     "greet",        # a tool is running - a bubble, not a face""",
        "label": "an accredited question kind given to the step producer - "
                 "every invariant the membership test holds for a real face "
                 "is still true, and the mascot reacts instead of narrating",
        "expect": [
            "test_each_named_producer_emits_its_exact_kind_and_text",
        ],
    },

    # -- the thread hop: where the off-thread decision can turn ---------------
    {
        "file": _MASCOT,
        "old": """        self._main_thread = threading.get_ident()""",
        "new": """        self._main_thread = None""",
        "label": "the thread recorded at start() is thrown away, so every "
                 "producer routes directly - a permission decided on the tool "
                 "loop's thread touches widgets",
        "expect": [
            "test_a_producer_off_the_gtk_thread_is_hopped_onto_it",
        ],
    },
    {
        "file": _MASCOT,
        "old": """        if self._main_thread is not None and threading.get_ident() != self._main_thread:""",
        "new": """        if self._main_thread is None and threading.get_ident() != self._main_thread:""",
        "label": "the hop conditions negated - the recorded thread becomes the "
                 "unhopped answer, and a producer on the bus's real caller "
                 "reaches the widget tree on the wrong thread",
        "expect": [
            "test_a_producer_off_the_gtk_thread_is_hopped_onto_it",
        ],
    },
    {
        "file": _MASCOT,
        "old": """        if kind == "step":""",
        "new": """        if kind == "never":""",
        "label": "the one kind routed to a bubble instead of a face is left "
                 "routed to a face - a tool running, which is not a reaction, "
                 "wears a reaction",
        "expect": [
            "test_the_step_is_a_bubble_not_a_face",
        ],
    },

    # -- the wires: the five producers, at their call sites -------------------
    {
        "file": _ASK,
        "old": """        preview=None, kind: str = "greet") -> str:""",
        "new": """        preview=None, kind: str = "meeting") -> str:""",
        "label": "the default face at the one chokepoint every question "
                 "passes becomes the approval face - a question the model "
                 "chose to ask surprises instead of greets, and no other "
                 "line changes because the default is the only carrier",
        "expect": [
            "test_a_question_passes_the_face_at_the_chokepoint_it_also_has",
        ],
    },
    {
        "file": _CONV,
        "old": """        mascot_events.finished()
""",
        "new": """        pass
""",
        "label": "the success path stops sounding the rally - the turn ends "
                 "next turn's face standing on nothing",
        "expect": [
            "test_a_finished_turn_wears_the_ok_face_before_the_queue_drains",
        ],
    },
    {
        "file": _CONV,
        "old": """            mascot_events.error(str(result))
""",
        "new": """            pass
""",
        "label": "the failure path stops sounding the rally - the companion "
                 "that was concerned for a failed turn waits politely",
        "expect": [
            "test_a_failed_turn_wears_the_concerned_face_before_the_queue_drains",
        ],
    },
    {
        "file": _CONV,
        "old": """        GLib.idle_add(lambda: (mascot_events.step(step), False)[1])""",
        "new": """        mascot_events.step(step)""",
        "label": "the bubble joins the desktop-cue call it was split off from, "
                 "off the hop - emitted from AsyncBridge's thread, the "
                 "undefined behaviour the other hops exist to avoid",
        "expect": [
            "test_a_tool_run_says_what_it_is_doing_from_the_callback",
        ],
    },
]
