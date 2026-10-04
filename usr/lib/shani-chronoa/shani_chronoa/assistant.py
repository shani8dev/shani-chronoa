"""Task-execution orchestration: LLM + local system tools.

Ties the LLM's tool-calling ability to the fixed tool whitelist in
`tools.py`, so the assistant actually performs actions (opening apps,
adjusting volume, setting timers, ...) instead of only describing them.

Also the point where *perception* enters the conversation. A `Percept`
(see `senses/__init__.py`) is a transient observation produced by a sense -
what is on the screen right now, a fact the user asked to be remembered - and
it reaches the LLM as a short transient system message rebuilt on every
single request by `build_messages()`. It is never appended to `_history`,
for the reasons `senses/context.py` spells out in full and repeats here
because this is the one place the mistake is tempting: `_trim_history()`
preserves `_history[0]` and drops the rest, so a percept parked at
`_history[1]` would be silently deleted by the very next trim, and
`MAX_HISTORY_MESSAGES` would cap how much perception could ever be carried
inside the conversation budget that is supposed to belong to the
conversation. Rebuilding per call also means a percept expires with its own
TTL instead of surviving for the session.

`percept_store` is optional and defaults to `None`: with no store,
`build_messages()` returns a plain copy of `_history` and every prompt is
byte-for-byte what it was before the senses layer existed.
"""
from shani_chronoa import provenance

import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from shani_chronoa.ollama_llm import OllamaLLM
from shani_chronoa.loops import LOOP_THRESHOLD, LoopDetector
from shani_chronoa import compression, history_repair, conversation_store, user_prompts
from shani_chronoa.senses.context import ContextBuilder
from shani_chronoa.tools import TOOLS, execute_tool
from shani_chronoa.tool_select import select_tools

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps the runtime import out
    from shani_chronoa.senses import Percept
    from shani_chronoa.senses.store import PerceptStore

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are Chronoa, a privacy-first local voice assistant running on Shanios. "
    "When the user asks you to do something you have a tool for - opening an "
    "app, adjusting volume, setting a timer, checking the battery, searching "
    "the web - call the tool instead of just describing what to do. "
    "Keep spoken replies short and conversational."
)

MAX_TOOL_ROUNDS = 4

#: How many times one tool may be called **in a single round**, by name.
#:
#: `MAX_TOOL_ROUNDS` bounds *rounds*, and `LoopDetector` bounds *identical
#: arguments in a row*. Neither bounds a single tool called many times with
#: different arguments inside one round, which is what a model does when it is
#: failing and rephrasing: `web_search("how to prune systemd timers")`,
#: `web_search("prune systemd timers")`, `web_search("systemd timer cleanup")`...
#: Every one of those is a different call by `call_key`, so the loop detector
#: never sees a run and the turn spends its budget on one tool while the tool it
#: also needed never gets reached.
#:
#: **Per round, not per turn, and the tests of record are why.** A per-turn
#: per-name count cannot go below 16 without breaking
#: `tests/test_turn_budget_inside_tool_loop.py`, which pins a turn at 4 rounds
#: of 4 `ask_user` calls with *distinct* arguments - all sixteen legitimate, all
#: of them questions a person is entitled to be asked. A budget that refuses the
#: eleventh question is not a retry budget, it is a broken assistant. Per round
#: bounds the thing that is actually unbounded (one round, many calls) without
#: capping a conversation's total.
#:
#: **At `LOOP_THRESHOLD`, not below it, and that is load-bearing in both
#: directions.** Below the loop threshold the per-tool budget would fire first
#: and steal the turn's ending: `tests/test_loops.py` requires the *loop
#: detector's* explanation for a run of identical calls, and with a budget of 2
#: the detector never reached 5 and the user got a bare refusal instead of
#: "5 calls in a row with identical arguments". So the two bounds are ordered -
#: the specific one (same call, repeated) is judged by the mechanism that can
#: explain it, and the general one (same tool, rephrased) is caught by this.
DEFAULT_TOOL_ATTEMPTS_PER_ROUND = LOOP_THRESHOLD

#: Per-tool overrides for `DEFAULT_TOOL_ATTEMPTS_PER_ROUND`, by tool name.
#:
#: Empty on purpose. A skill that is legitimately called several times in one
#: round - a timer set and then checked in the same breath - needs a higher
#: number, and that is a decision about *that skill's* semantics belonging to
#: whoever writes the skill, not a guess made here. The dictionary exists so
#: adding one is a one-line change rather than an edit to the loop.
TOOL_ATTEMPT_OVERRIDES: dict[str, int] = {}

#: Wall-clock ceiling for one turn. `MAX_TOOL_ROUNDS` bounds how many times the
#: model may act, not how long any of those may take - four rounds against a
#: 4B model on a cold start, or one slow tool on a spinning disk, is minutes
#: with nothing watching it. A turn that cannot finish is a turn the user
#: cannot interrupt, because the UI has no idea it is still working.
#:
#: Generous on purpose: this is a backstop against a run that is not going to
#: end, not a target. Exceeding it stops the turn and says so.
MAX_TURN_SECONDS = 300.0

# _history grew unboundedly across a session before this - combined with a
# small num_ctx that was previously hardcoded, a long session would
# silently truncate context (Ollama drops rather than errors) well before
# anyone noticed. Caps total messages kept, trimmed only at user-message
# boundaries so a tool_call is never separated from its tool-result reply -
# some OpenAI-compatible backends reject a conversation with a dangling
# tool_calls/tool message.
MAX_HISTORY_MESSAGES = 40


def _seconds(value: float) -> str:
    """A duration a person can read, without rounding it into a different number.

    `{x:.0f}` turned a 0.6 second budget into "1 seconds" - wrong, and
    ungrammatical besides. Whole seconds stay whole; anything else keeps a
    decimal, because a message about time that misstates the time is worse than
    no message.
    """
    if value >= 10:
        return f"{int(round(value))} seconds"
    return f"{value:.1f} seconds"


class Assistant:
    """Runs one user turn through the LLM, executing any tool calls it makes.

    `percept_store`, if given, is the read side of the senses layer: a
    `PerceptStore` whose `active()` returns what is currently being perceived.
    It is read fresh on every request rather than cached, so a percept that
    expires between two requests of the same tool loop simply stops appearing.
    """

    def __init__(
        self,
        llm: OllamaLLM,
        percept_store: "Optional[PerceptStore]" = None,
        context_builder: Optional[ContextBuilder] = None,
        session_path: "Optional[Path]" = None,
    ) -> None:
        self.llm = llm
        self._history: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        # Public, not underscore-private: a caller (app.py, a test, a future
        # sense producer inside the GUI) needs to be able to add a percept to
        # the store this assistant reads from, and the CLI writes to the same
        # durable file. Renaming it later would silently break both.
        self.percept_store = percept_store
        self.context_builder = context_builder if context_builder is not None else ContextBuilder()
        # Restore the transcript before the first request, so a restart is a
        # pause rather than an amnesia. `_saved` is the system prompt this
        # Assistant starts from, so reset() returns to exactly that.
        # None means *no* transcript, not "the default one". A default here
        # would let any Assistant built without the argument - a test, a script,
        # an embedder - read and overwrite the real user's conversation, which
        # is the first version's actual behaviour and nobody would have chosen it.
        self._session_path = session_path
        self._saved = self._history[:1]
        restored = ([m for m in conversation_store.load(session_path)
                     if m.get("role") != "system"] if session_path else [])
        if restored:
            self._history = self._saved + restored
            logger.info("Restored %d message(s) from the saved conversation", len(restored))

    @property
    def session_path(self) -> "Optional[Path]":
        return self._session_path

    def switch_session(self, path: "Optional[Path]") -> int:
        """Continue a different saved conversation: history becomes that file's messages.

        Returns how many messages were restored. The previous conversation is
        left on disk untouched - switching is not resetting.
        """
        self._session_path = path
        restored = ([m for m in conversation_store.load(path) if m.get("role") != "system"] if path else [])
        self._history = self._saved + restored
        return len(restored)

    def visible_turns(self) -> "list[tuple[str, str]]":
        """(role, text) for the user and assistant messages, to redraw a window after a switch."""
        out = []
        for m in self._history:
            if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str) and m["content"].strip():
                out.append((m["role"], m["content"]))
        return out

    def reset(self) -> None:
        """Clear conversation history back to just the system prompt.

        Also removes the saved transcript. Leaving it would mean the next
        process to start reads back a conversation the user just discarded.
        """
        self._history = self._saved[:]
        if self._session_path is not None:
            conversation_store.clear(self._session_path)

    def drop_last_reply(self) -> "tuple[str, str]":
        """Take back the assistant's last answer, and say what to ask again.

        Returns `(user_text, previous_answer)`, or `("", "")` when there is
        nothing to take back. The user text is returned rather than looked up by
        the caller because the caller is a button: the only question that makes
        sense to re-ask is the one this answer was an answer to.

        Everything from the last user message onwards goes, not just the last
        assistant message. A turn that ran tools left tool results behind it,
        and a tool result is not a conversation - keeping it would put a
        transcript entry with no question above it into the next request.

        The question goes with them, deliberately: the caller re-asks it through
        the ordinary send path, and `handle()` records the question itself. Left
        behind, a regenerate would record it twice and every later turn would
        see the same question asked again with its own answer missing. The
        window puts the question back on screen while the new answer is
        computed, which is the part the user actually sees.

        The saved transcript is rewritten, not appended to: a process restarted
        after a regenerate must not restore the answer the user threw away.
        `_history` is trimmed the same way `close_interrupted_turn` repairs it,
        so what is on disk and what is in memory cannot disagree.
        """
        if len(self._history) < 2:
            return "", ""
        end = len(self._history)
        while end > 1 and self._history[end - 1].get("role") != "user":
            end -= 1
        if end == 1:
            return "", ""
        question = self._history[end - 1].get("content")
        previous = next((m.get("content") for m in reversed(self._history[end:])
                         if m.get("role") == "assistant" and isinstance(m.get("content"), str)), "")
        del self._history[end - 1:]
        if self._session_path is not None:
            conversation_store.rewrite(
                [m for m in self._history if m.get("role") != "system"], self._session_path)
        return (question if isinstance(question, str) else ""), (previous or "")

    @staticmethod
    def _tool_source(call: dict) -> str:
        """Where a tool's output came from, for the provenance fence.

        Derived from the tool's own name rather than trusted from the call, and
        defaults to `tool` - so an unrecognised skill's output is still fenced as
        untrusted. A new skill therefore cannot accidentally arrive unfenced by
        forgetting to declare itself here.
        """
        function = call.get("function") or {}
        name = str(function.get("name") or "")
        if name.startswith(("web_", "browse", "search")):
            return "web"
        # A listing is file content too: filenames come from the filesystem, not
        # from the user, and a file can be named `ignore previous
        # instructions.txt`. So anything that reports what is *in* a directory
        # is fenced as file content as well.
        if name.startswith(("read_", "open_", "find_", "search_file",
                            "search_", "directory_", "list_directory",
                            "list_files", "compare_", "json_", "office_",
                            "extract_", "analyze_", "disk_usage",
                            "get_file_info", "find_recently_modified",
                            "transcript", "recordings", "photo")):
            return "file"
        return "tool"

    def _record(self, message: dict) -> None:
        """Add a message to the conversation and to the saved transcript.

        Every history append goes through here, so a crash cannot leave a gap:
        the transcript is written as the turn happens, which is the only moment
        worth having it.
        """
        self._history.append(message)
        if self._session_path is not None and message.get("role") != "system":
            conversation_store.append(message, self._session_path)

    def active_percepts(self) -> "list[Percept]":
        """Every percept still within its lifetime, or [] if sensing is off.

        A store failure degrades to "no perception" rather than raising: a
        corrupt percept file must not make the assistant unable to answer at
        all. `PerceptStore` already degrades internally, so this is the
        belt-and-braces layer for a substituted store.
        """
        if self.percept_store is None:
            return []
        try:
            return self.percept_store.active()
        except Exception as e:  # noqa: BLE001 - perception is optional context
            logger.error(f"Could not read percepts: {e}")
            return []

    def build_messages(self) -> "list[dict]":
        """The message list to send for the current turn.

        Returns a *copy* of `_history` with the live percept block inserted
        directly after the system prompt. `_history` itself is never mutated,
        so percepts are absent from the conversation transcript, are not
        counted against `MAX_HISTORY_MESSAGES`, and are not subject to
        `_trim_history()`'s turn grouping. With no percept store, or with a
        store holding nothing live, the returned list is content-identical to
        `_history` - this is the no-op path and it must stay free of any
        extra message, empty system turn or placeholder text.

        The history is then repaired (`history_repair.clean_history`) and
        compressed (`compression.compress`). Both are the same
        copy-not-mutate contract as the percept insertion above, and for the
        same reason: `_history` and the transcript keep every byte, so a repair
        or an elision here is a transport saving and never a record that
        something was lost.

        **Repair runs before compression, and the order is not arbitrary.**
        Compression decides what to elide by position - everything before the
        last `KEEP_RECENT_MESSAGES` is old. A repair *adds* a message, so
        compressing first would count a repair it was about to make against the
        protected window, and an old result that the repair had just made
        current would be elided on the strength of a position the repair
        invalidated.

        Repair is placed here rather than in `ollama_llm.py` because it is a property
        of the *conversation*, not of one backend: every backend gets it,
        including the cloud fallbacks, and `_history` is the only thing that
        knows the whole turn.
        """
        messages = self.context_builder.build_messages(
            self._history, self.active_percepts()) if self.percept_store is not None \
            else list(self._history)
        messages = history_repair.clean_history(messages)

        # A turn that did not answer all of its tool calls explains itself here,
        # once, on the next request - and then stops. It is deliberately not in
        # `_history`, so it is not a thing the assistant is recorded as having
        # said, and deliberately cleared below so it cannot be re-sent to a
        # later turn as though it were new. Without it the model opens the next
        # turn with results saying nothing ran and no frame for why, which is
        # how an interrupted turn turns into a confident wrong answer.
        # The user's rules file, re-read each turn, never recorded (user_prompts.py).
        rules = user_prompts.rules_message()
        if rules:
            messages = [messages[0], rules, *messages[1:]]

        notice = getattr(self, "_continue_notice", None)
        if notice:
            messages = [{**messages[0]},
                        {"role": "system", "content": notice},
                        *messages[1:]]
            self._continue_notice = None

        return compression.compress(messages)

    def close_interrupted_turn(self, reason: str) -> "list[dict]":
        """Record a result for every tool call this turn will never answer.

        Called on every way a turn can end without finishing its batch: the time
        budget running out mid-batch, a `CancelledError` from
        `AsyncBridge.cancel_pending()` at shutdown, or a backend that died
        mid-turn. Without this the assistant message asking for four calls stays
        in `_history` with one result, and **every later request carries that** -
        so one interrupted turn poisons the rest of the session rather than
        costing the user one turn.

        This is goose's `handle_interrupted_messages`
        (`crates/goose-cli/src/session/mod.rs:1646`) in the ~40 lines it needs
        here. qwen-code spends roughly 500 lines of Goal machinery on the same
        problem; the whole of it is the two actions below, and the rest of that
        machinery is session state Chronoa has no use for.

        Both results are recorded, not just returned. The synthesized results go
        through `_record` so they reach the on-disk transcript, which is what
        makes the repair survive the process - a turn interrupted by a shutdown
        is repaired in the transcript and therefore already correct when the
        next process reads it back.

        The trailing assistant turn is **not** recorded in `_history`. It is a
        turn that never happened: no model produced it and no user saw it. It
        goes into the *next* request only, so the model opens that turn knowing
        the state of the conversation instead of inferring it from results that
        say nothing ran, and the transcript stays a record of what was actually
        said. `app.py` has already reported the real outcome to the user by
        this point, so nothing is hidden from them.

        Returns the repair messages, for the caller to log or assert on.
        """
        repaired = history_repair.repair_orphans(self._history, reason)
        added = repaired[len(self._history):]
        for message in added:
            self._record(message)
        if added:
            self._continue_notice = (
                history_repair.CONTINUE_PROMPT
                + " "
                + " ".join(
                    str(m.get("content", "")) for m in added
                )
            )
        return added

    def _trim_history(self) -> None:
        if len(self._history) <= MAX_HISTORY_MESSAGES:
            return
        system = self._history[0]
        turns: list[list[dict]] = []
        for msg in self._history[1:]:
            if msg.get("role") == "user" or not turns:
                turns.append([msg])
            else:
                turns[-1].append(msg)
        while len(turns) > 1 and sum(len(t) for t in turns) + 1 > MAX_HISTORY_MESSAGES:
            turns.pop(0)
        self._history = [system] + [msg for turn in turns for msg in turn]

    def _over_budget(self, deadline: float) -> "str | None":
        """An honest stop, or None while there is time left.

        Returning the message rather than raising is deliberate: the turn's
        contract is to return an answer, and a string that says the budget ran
        out is a truthful answer to "what happened" - where an exception would
        leave the window showing a spinner with no explanation.
        """
        left = deadline - time.monotonic()
        if left > 0:
            return None
        spent = time.monotonic() - getattr(self, "_turn_started", deadline)
        self._turn_deadline = None
        self._turn_overran = True
        logger.warning("Turn exceeded its %s budget after %s",
                       _seconds(MAX_TURN_SECONDS), _seconds(spent))
        return (
            f"This turn ran out of time: it had {_seconds(MAX_TURN_SECONDS)} "
            f"and used about {_seconds(spent)} of them without reaching an answer. "
            f"Nothing further was done. Ask for something narrower, or raise "
            f"MAX_TURN_SECONDS in the source if this was not a stuck turn."
        )

    def _note_model_call(self, seconds: float) -> None:
        """Record a model call's duration and running totals for the turn.

        Counts the call, times it, and picks up whatever token usage the
        backend reported. The token counts were being discarded by every backend
        - Ollama counts them beside the message, the cloud providers send a
        `usage` block, and all three were thrown away. See `usage.py`.
        """
        self._model_calls = getattr(self, "_model_calls", 0) + 1
        self._model_seconds = getattr(self, "_model_seconds", 0.0) + seconds

        # Token counts, if the backend reported any. `Usage.__add__` is what
        # keeps this honest: a sum of a priced and an unpriced call comes back
        # unpriced rather than quietly reporting only the part that was known.
        reported = getattr(self.llm, "last_usage", None)
        if reported is not None:
            self._usage = getattr(self, "_usage", None)
            self._usage = reported if self._usage is None else self._usage + reported

        logger.debug("Model call %d took %.2fs (turn total %.2fs over %d calls)",
                     self._model_calls, seconds, self._model_seconds,
                     self._model_calls)

    def turn_stats(self) -> dict:
        """What this turn has cost so far in time and calls.

        Deliberately not a money figure. When the cloud fallback is on, per-call
        cost depends on the provider and the model, and a wrong number invented
        here would be worse than none - so the token counts and durations are
        reported and the pricing is left to whoever knows it.
        """
        return {
            "model_calls": getattr(self, "_model_calls", 0),
            "usage": (getattr(self, "_usage", None).as_dict()
                      if getattr(self, "_usage", None) is not None else None),
            "model_seconds": round(getattr(self, "_model_seconds", 0.0), 3),
            "wall_seconds": round(
                time.monotonic() - getattr(self, "_turn_started", time.monotonic()), 3),
            "budget_seconds": MAX_TURN_SECONDS,
            "over_budget": bool(getattr(self, "_turn_overran", False)),
        }

    # Not done: keeping a conversation's tools in a stable, appending order for
    # the prompt cache. Measured on both images (2026-10-02, Qwen3-0.6B, CPU):
    # first word 16.7/9.3/11.0 s with it against 3.3/6.5/5.3 s without. Qwen's
    # template puts tools inside the system message, ahead of the conversation,
    # so an appended tool still invalidates everything after it, and the
    # longer tool list costs more prefill on every miss.

    #: A tool the person named for this turn ('/timer 5 minutes', user_prompts.forced_tool):
    #: the first round offers only it and asks the model for it. Cleared after the turn.
    forced_tool: str = ""

    async def _ask(self, messages, tools, on_text):
        """One model call: streamed when the backend can and a caller wants the words as they come."""
        if on_text is not None and getattr(self.llm, "stream_supported", False):
            return await self.llm.chat_message_stream(messages, tools=tools, on_text=on_text)
        return await self.llm.chat_message(messages, tools=tools)

    async def handle(self, text: str, on_tool_call: Optional[Callable[[str, dict], None]] = None,
                     on_text: Optional[Callable[[str], None]] = None,
                     on_tool_result: Optional[Callable[[str, dict, str, bool], None]] = None) -> str:
        """Process one user utterance, executing tool calls, return the reply text.

        `on_tool_call(name, arguments)`, if given, fires just before each
        tool executes - lets a caller surface "what is it doing right now"
        (e.g. "Open application...") instead of a generic "Processing...".
        `on_tool_result(name, arguments, result, ok)` fires after, with what the
        skill returned. It is a separate callback because the two answer
        different questions: one is "what is about to happen", the other is "what
        ran, with what arguments, and what came back" - the only record that
        makes a surprising action explicable afterwards.
        Runs synchronously on whatever thread `handle()` itself runs on
        (the caller's async loop, not necessarily the GTK main thread).

        Bounded by `MAX_TURN_SECONDS` in wall-clock time as well as by
        `MAX_TOOL_ROUNDS` in rounds. When the budget runs out the turn stops and
        says so, rather than returning a partial answer that looks complete.

        Each request in the tool loop calls `build_messages()` again rather
        than reusing one list, so a percept added mid-turn - or expired
        mid-turn - is reflected in the very next request without being
        recorded in the conversation.

        Every way this turn can end without answering all of its tool calls
        routes through `close_interrupted_turn()`, so `_history` and the saved
        transcript never hold a call with no result. That matters more here than
        the shape of this method suggests: barge-in and `AsyncBridge`'s
        cancellation make an interrupted turn ordinary rather than exotic, and an
        unrepaired one is not confined to the turn it damaged.
        """
        self._record({"role": "user", "content": text})
        self._trim_history()

        # The clock starts here, not at process start: a turn's budget is about
        # how long *this* turn may take, and an idle assistant is not late.
        deadline = time.monotonic() + MAX_TURN_SECONDS
        self._turn_deadline = deadline
        self._turn_started = time.monotonic()
        self._turn_overran = False
        self._usage = None
        # Per-tool attempt counts, reset per *round*. See
        # `DEFAULT_TOOL_ATTEMPTS_PER_ROUND` for why not per turn: the tests of
        # record pin sixteen legitimate `ask_user` calls in one turn.
        attempts: dict[str, int] = {}
        # One detector per turn. Not carried across turns: a conversation may legitimately
        # make the same call at the start of one turn and the start of the next, and only a
        # run *within* a turn means the turn is failing to make progress.
        loop = LoopDetector()

        try:
            return await self._run_turn(
                text, on_tool_call, deadline, loop, attempts, on_text, on_tool_result)
        except BaseException:
            # One guard for the whole turn, and it is here rather than at each
            # site because the sites are easy to forget: a model call that
            # raises, a tool that raises, a callback that raises. `CancelledError`
            # is a `BaseException`, so an `except Exception` would miss the most
            # common interruption this app has - the user closing the window
            # during a tool call - and the history would stay broken exactly
            # then.
            #
            # Repairing before the exception propagates is the whole point. The
            # turn is lost either way; the history it leaves behind is not. It
            # propagates rather than being swallowed because `app.py` reports it
            # to the user, and a turn that quietly returned an answer instead
            # would be a lie about what happened.
            #
            # `close_interrupted_turn` is a no-op for a turn with nothing
            # unpaired, so the ordinary completed path and the budget stop
            # (which repairs explicitly, for a truthful reason) both pass
            # through here unchanged.
            self.close_interrupted_turn("interrupted")
            raise

    async def _run_turn(
        self,
        text: str,
        on_tool_call: "Optional[Callable[[str, dict], None]]",
        deadline: float,
        loop: LoopDetector,
        attempts: "dict[str, int]",
        on_text: "Optional[Callable[[str], None]]" = None,
        on_tool_result: "Optional[Callable[[str, dict, str, bool], None]]" = None,
    ) -> str:
        """The tool loop itself. `handle()` owns the budget, the detectors and
        the repair-on-exit; this is the part that would otherwise be a 90-line
        `try` block wrapped around everything."""
        # Only the schemas this request could use are sent - all 80 were
        # ~11,800 tokens against an 8192-token window (see tool_select). Tools
        # already called this turn stay, so a follow-up round keeps them.
        in_use: set = set()
        named, self.forced_tool = self.forced_tool, ""  # this turn only
        for _ in range(MAX_TOOL_ROUNDS):
            over = self._over_budget(deadline)
            if over:
                self.close_interrupted_turn("budget")
                return over
            started = time.monotonic()
            forced = named if not in_use else ""
            sent = [t for t in TOOLS if t["function"]["name"] == forced] if forced else \
                select_tools(text, TOOLS, in_use=in_use)
            self.llm.required_tool = forced if sent else ""
            try:
                message = await self._ask(self.build_messages(), sent, on_text)
            finally:
                self.llm.required_tool = ""
            self._note_model_call(time.monotonic() - started)
            self._record(message)

            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                return message.get("content", "")

            attempts.clear()
            in_use.update((c.get("function") or {}).get("name", "") for c in tool_calls)
            for call in tool_calls:
                # Checked here as well as between model calls, because a single
                # tool call can outlast the whole budget. `ask_user` blocks for up
                # to `ask_bridge.DEFAULT_TIMEOUT_SECONDS` waiting for a user who
                # may not be there, and that wait happens inside this loop, where
                # nothing else was watching. Without this, one turn could block for
                # hours: 4 rounds x 10 asking calls x 180s, while the 300s turn
                # budget sat unused because it was only ever consulted between model
                # calls. `assistd` bounds the same thing from the other side with a
                # 120s prompt timeout and a cap on pending confirmations.
                over = self._over_budget(deadline)
                if over:
                    self.close_interrupted_turn("budget")
                    return over
                function = call.get("function", {})
                name = function.get("name", "")
                arguments = function.get("arguments", {})
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments) if arguments.strip() else {}
                    except json.JSONDecodeError as exc:
                        # Never run a tool with arguments the model did not
                        # give: running it with {} did whatever the defaults
                        # do (sayri hands a small model its own error back
                        # instead). The model sees why, with the call's id.
                        self._record({"role": "tool", "tool_call_id": call.get("id", ""), "content":
                                      f"ERROR: the arguments for {name} were not valid JSON ({exc.msg} at "
                                      f"position {exc.pos}); nothing ran. Call it again with valid JSON."})
                        continue
                if not isinstance(arguments, dict):
                    self._record({"role": "tool", "tool_call_id": call.get("id", ""), "content":
                                  f"ERROR: the arguments for {name} must be a JSON object; nothing ran."})
                    continue
                logger.info(f"Tool call: {name}({arguments})")
                # Counted *after* the call is dispatched below, so the number in the
                # message is the number that actually ran. Checking first would stop the
                # turn before the Nth call, and then claim N calls in a row when only
                # N-1 had happened.
                loop.repeat(name, arguments)
                if on_tool_call:
                    try:
                        on_tool_call(name, arguments)
                    except Exception as e:
                        logger.error(f"on_tool_call callback failed: {e}")

                # One tool's budget is its own. Refused in-band with the call's
                # own `tool_call_id` rather than by raising or by ending the
                # turn, which is agno's `create_tool_call_limit_error_result`
                # (`libs/agno/agno/models/base.py:2217`) applied to a per-tool
                # budget instead of a per-run one. The reasons this is in-band
                # rather than an exception: an exception here leaves the turn
                # half-cleaned and the user reading a traceback, and a synthetic
                # result carrying the original id is the one shape every backend
                # already knows how to accept. The message tells the model not to
                # retry, so the loop then ends on its own with a conversation
                # that is still valid.
                budget = TOOL_ATTEMPT_OVERRIDES.get(
                    name, DEFAULT_TOOL_ATTEMPTS_PER_ROUND)
                if attempts.get(name, 0) >= budget:
                    logger.info(
                        "Refusing %s: already called %d time(s) this turn, and its "
                        "budget is %d", name, attempts[name], budget)
                    self._record(history_repair.synthesized_tool_result(
                        call, "limit"))
                    continue

                attempts[name] = attempts.get(name, 0) + 1
                result = execute_tool(name, arguments)
                if on_tool_result:
                    # `ok` is decided here rather than left to the callback to
                    # guess: a card that says "done" over a failed skill is
                    # exactly the confident wrong answer this repo keeps paying
                    # for, and the executor already encodes the verdict in the
                    # text it returns.
                    ok = not str(result).lstrip().lower().startswith(("error", "refused"))
                    try:
                        on_tool_result(name, arguments, str(result), ok)
                    except Exception as e:
                        logger.error(f"on_tool_result callback failed: {e}")
                # tool_call_id correlates this result back to the specific
                # tool_calls entry that requested it - required by the
                # actual OpenAI spec (tolerated without it by the free
                # gateways tested so far) and by Anthropic's translation in
                # cloud_llm.py, which can't build a valid tool_result block
                # without it. Empty string for backends (e.g. Ollama) that
                # don't emit an id on their tool_calls at all.
                # A tool's output is the one thing in this loop that the model
                # reads but the user never typed, and a tool that reads a file
                # or fetches a page returns whatever that file or page said -
                # including text shaped like an instruction. So it is marked as
                # data here, at the point it enters the conversation, rather
                # than relying on the model to notice. See provenance.py for
                # what this does and does not claim.
                self._record({"role": "tool", "tool_call_id": call.get("id", ""),
                              "content": provenance.fence(
                                  result, Assistant._tool_source(call)).text})
                if loop.stopped:
                    self._turn_deadline = None
                    return loop.explain()

        # Ran out of tool rounds - ask once more for a final plain answer.
        over = self._over_budget(deadline)
        if over:
            self.close_interrupted_turn("budget")
            return over
        started = time.monotonic()
        message = await self._ask(self.build_messages(), None, on_text)
        self._note_model_call(time.monotonic() - started)
        self._record(message)
        self._turn_deadline = None
        return message.get("content", "")
