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

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from shani_chronoa.llm import OllamaLLM
from shani_chronoa import sessions
from shani_chronoa.senses.context import ContextBuilder
from shani_chronoa.tools import TOOLS, execute_tool

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

# _history grew unboundedly across a session before this - combined with a
# small num_ctx that was previously hardcoded, a long session would
# silently truncate context (Ollama drops rather than errors) well before
# anyone noticed. Caps total messages kept, trimmed only at user-message
# boundaries so a tool_call is never separated from its tool-result reply -
# some OpenAI-compatible backends reject a conversation with a dangling
# tool_calls/tool message.
MAX_HISTORY_MESSAGES = 40


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
        restored = ([m for m in sessions.load(session_path)
                     if m.get("role") != "system"] if session_path else [])
        if restored:
            self._history = self._saved + restored
            logger.info("Restored %d message(s) from the saved conversation", len(restored))

    def reset(self) -> None:
        """Clear conversation history back to just the system prompt.

        Also removes the saved transcript. Leaving it would mean the next
        process to start reads back a conversation the user just discarded.
        """
        self._history = self._saved[:]
        if self._session_path is not None:
            sessions.clear(self._session_path)

    def _record(self, message: dict) -> None:
        """Add a message to the conversation and to the saved transcript.

        Every history append goes through here, so a crash cannot leave a gap:
        the transcript is written as the turn happens, which is the only moment
        worth having it.
        """
        self._history.append(message)
        if self._session_path is not None and message.get("role") != "system":
            sessions.append(message, self._session_path)

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
        """
        if self.percept_store is None:
            return list(self._history)
        return self.context_builder.build_messages(self._history, self.active_percepts())

    def _trim_history(self) -> None:
        """Drop the oldest complete turns once history grows past the cap."""
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

    async def handle(self, text: str, on_tool_call: Optional[Callable[[str, dict], None]] = None) -> str:
        """Process one user utterance, executing tool calls, return the reply text.

        `on_tool_call(name, arguments)`, if given, fires just before each
        tool executes - lets a caller surface "what is it doing right now"
        (e.g. "Open application...") instead of a generic "Processing...".
        Runs synchronously on whatever thread `handle()` itself runs on
        (the caller's async loop, not necessarily the GTK main thread).

        Each request in the tool loop calls `build_messages()` again rather
        than reusing one list, so a percept added mid-turn - or expired
        mid-turn - is reflected in the very next request without being
        recorded in the conversation.
        """
        self._record({"role": "user", "content": text})
        self._trim_history()

        for _ in range(MAX_TOOL_ROUNDS):
            message = await self.llm.chat_message(self.build_messages(), tools=TOOLS)
            self._record(message)

            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                return message.get("content", "")

            for call in tool_calls:
                function = call.get("function", {})
                name = function.get("name", "")
                arguments = function.get("arguments", {})
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                logger.info(f"Tool call: {name}({arguments})")
                if on_tool_call:
                    try:
                        on_tool_call(name, arguments)
                    except Exception as e:
                        logger.error(f"on_tool_call callback failed: {e}")
                result = execute_tool(name, arguments)
                # tool_call_id correlates this result back to the specific
                # tool_calls entry that requested it - required by the
                # actual OpenAI spec (tolerated without it by the free
                # gateways tested so far) and by Anthropic's translation in
                # cloud_llm.py, which can't build a valid tool_result block
                # without it. Empty string for backends (e.g. Ollama) that
                # don't emit an id on their tool_calls at all.
                self._record({"role": "tool", "tool_call_id": call.get("id", ""), "content": result})

        # Ran out of tool rounds - ask once more for a final plain answer.
        message = await self.llm.chat_message(self.build_messages(), tools=None)
        self._record(message)
        return message.get("content", "")
