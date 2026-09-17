"""Task-execution orchestration: LLM + local system tools.

Ties the LLM's tool-calling ability to the fixed tool whitelist in
`tools.py`, so the assistant actually performs actions (opening apps,
adjusting volume, setting timers, ...) instead of only describing them.
"""

import json
import logging
from typing import Callable, Optional

from shani_chronoa.llm import OllamaLLM
from shani_chronoa.tools import TOOLS, execute_tool

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
    """Runs one user turn through the LLM, executing any tool calls it makes."""

    def __init__(self, llm: OllamaLLM) -> None:
        self.llm = llm
        self._history: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

    def reset(self) -> None:
        """Clear conversation history back to just the system prompt."""
        self._history = self._history[:1]

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
        """
        self._history.append({"role": "user", "content": text})
        self._trim_history()

        for _ in range(MAX_TOOL_ROUNDS):
            message = await self.llm.chat_message(self._history, tools=TOOLS)
            self._history.append(message)

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
                self._history.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": result})

        # Ran out of tool rounds - ask once more for a final plain answer.
        message = await self.llm.chat_message(self._history, tools=None)
        self._history.append(message)
        return message.get("content", "")
