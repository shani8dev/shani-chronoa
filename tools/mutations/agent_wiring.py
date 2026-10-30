"""Mutation set for the shell's turn stream: the resolver, the answer, the wire.

The engine is `ask_bridge` + `AgentService`, and each mutation below is a
plausible way to have written it that still *looks* wired:

- a resolver installed process-wide instead of per turn (the version that was
  actually written first): the window's questions go to the shell too.
- a turn scheduled without binding its resolver: the shell's own questions fall
  through to the GTK presenter, which on a machine with no window is nowhere.
- a sink not passed to `handle`, or not given `options` on the approval: the
  panel draws a card with nothing to click, or a decision that cannot be
  matched by `permissions.parse_reply`.
- an approval left registered after its wait ended, so a late click looks like
  it answered something.
- one slot for every turn, so two turns answer each other.

Every anchor was read from the live file and `grep -c`'d once: the runner
refuses an anchor that occurs twice, and a set written from memory is how the
previous one reported a survivor with a misleading reason.
"""

TEST_FILE = "tests/test_the_agent_turn_stream.py"

_SERVICE = "usr/lib/shani-chronoa/shani_chronoa/agent_service.py"
_BRIDGE = "usr/lib/shani-chronoa/shani_chronoa/ask_bridge.py"

MUTATIONS = [
    # -- the resolver belongs to a turn -------------------------------------
    {
        "file": _SERVICE,
        "old": "            with ask_bridge.turn_resolver(sink.ask):\n",
        "new": "            if True:\n",
        "label": "**the finding, restored**: the turn runs without binding its "
                 "approval resolver",
        "expect": ["test_turn_starts_the_assistant_with_a_bound_sink"],
    },
    {
        "file": _BRIDGE,
        "old": "    token = _turn_resolver.set(fn)\n",
        "new": "    token = _turn_resolver.set(None)\n",
        "label": "binding a turn sets nothing, so the context is never bound",
        "expect": ["test_a_question_on_a_tool_thread_reaches_its_turn_and_returns_the_answer"],
    },
    {
        "file": _BRIDGE,
        "old": "    resolver = _turn_resolver.get()\n",
        "new": "    resolver = None\n",
        "label": "ask() ignores the bound turn and always uses the window",
        "expect": ["test_a_question_on_a_tool_thread_reaches_its_turn_and_returns_the_answer"],
    },
    {
        "file": _SERVICE,
        "old": "            coro = self._app.assistant.handle(text, sink=sink)\n",
        "new": "            coro = self._app.assistant.handle(text)\n",
        "label": "the turn runs without its sink, so no event reaches the shell",
        "expect": ["test_turn_starts_the_assistant_with_a_bound_sink"],
    },
    {
        "file": _BRIDGE,
        "old": "    return has_presenter() or has_agent_resolver()\n",
        "new": "    return has_presenter()\n",
        "label": "a shell-side turn is not counted as someone to ask",
        "expect": ["test_a_bound_turn_counts_as_someone_to_ask"],
    },

    # -- the answer is the option label -------------------------------------
    {
        "file": _SERVICE,
        "old": "            invocation.return_value(GLib.Variant(\"(b)\", (False,)))\n"
               "            return\n"
               "        entry[1](decision)\n",
        "new": "            invocation.return_value(GLib.Variant(\"(b)\", (False,)))\n"
               "            return\n"
               "        entry[0].set()\n",
        "label": "Approve wakes the wait but throws the decision away",
        "expect": ["test_a_question_on_a_tool_thread_reaches_its_turn_and_returns_the_answer"],
    },
    {
        "file": _SERVICE,
        "old": "                   options=[str(option) for option in options],\n",
        "new": "",
        "label": "the approval carries no options, so the panel draws no choice",
        "expect": ["test_a_question_on_a_tool_thread_reaches_its_turn_and_returns_the_answer"],
    },

    # -- an answered or expired wait is not still open ----------------------
    {
        "file": _SERVICE,
        "old": "        turn_id, decision = params.unpack()\n"
               "        with self._lock:\n"
               "            entry = self._pending.pop(turn_id, None)\n",
        "new": "        turn_id, decision = params.unpack()\n"
               "        with self._lock:\n"
               "            entry = self._pending.get(turn_id)\n",
        "label": "Approve reads the wait but never clears it, so it can be "
                 "answered twice",
        "expect": ["test_an_answered_approval_cannot_be_answered_again"],
    },
    {
        "file": _SERVICE,
        "old": "        if entry is None:\n"
               "            invocation.return_value(GLib.Variant(\"(b)\", (False,)))\n"
               "            return\n",
        "new": "        if entry is None:\n"
               "            invocation.return_value(GLib.Variant(\"(b)\", (True,)))\n"
               "            return\n",
        "label": "Approve reports success even when nothing was waiting",
        "expect": ["test_an_unanswered_approval_is_closed_and_a_late_answer_is_refused"],
    },
    {
        "file": _SERVICE,
        "old": "        done.chronoa_closed = lambda: self._service.forget(  # type: ignore[attr-defined]\n"
               "            self._turn_id, done, self._emit_closed)\n",
        "new": "        pass\n",
        "label": "a timed-out approval is never forgotten or taken down",
        "expect": ["test_an_unanswered_approval_is_closed_and_a_late_answer_is_refused"],
    },
    {
        "file": _SERVICE,
        "old": "        closed()\n",
        "new": "        pass\n",
        "label": "the wait is dropped but the card is left on screen",
        "expect": ["test_an_unanswered_approval_is_closed_and_a_late_answer_is_refused"],
    },

    # -- one turn cannot answer another -------------------------------------
    {
        "file": _SERVICE,
        "old": "            self._pending[turn_id] = (done, resolve)\n",
        "new": "            self._pending[\"turn\"] = (done, resolve)\n",
        "label": "every turn shares one approval slot",
        "expect": ["test_one_turns_approval_cannot_answer_another"],
    },
]
