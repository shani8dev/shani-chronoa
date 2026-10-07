"""A turn from text to reply, and the saved conversations it belongs to."""

import logging
import time
from pathlib import Path

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('Gio', '2.0')
gi.require_version('Adw', '1')
from gi.repository import Gio, GLib  # type: ignore



from shani_chronoa import planmode, conversation_store, user_prompts, speech
from shani_chronoa.assistant import Assistant
from shani_chronoa.gui import AssistantState, ChronoaWindow
from shani_chronoa.senses import memory


logger = logging.getLogger(__name__)




class ConversationMixin:
    """A turn from text to reply, and the saved conversations it belongs to. - a part of ChronoaApplication, which mixes it in."""


    def _toggle_plan_mode(self, _action: Gio.SimpleAction, _param: object) -> None:
        """Enter or leave plan mode, and say which in the status bar.

        The status line is not decoration. A mode that removes capabilities
        without saying so would look like a bug the first time the assistant
        declined something the user had explicitly permitted.
        """
        now_on = planmode.set_enabled(not planmode.is_enabled())
        if self.window:
            if now_on:
                self.window.set_status(
                    "Plan mode: ON - Chronoa can read but not change anything")
            else:
                self.window.set_status(self._llm_status_text())

    def _reset_conversation(self, _action: Gio.SimpleAction, _param: object) -> None:
        """Start a new conversation. The previous one is kept and can be reopened from Conversations."""
        if self.assistant:
            conversation_store.new_session(conversation_store.session_dir())
            self.assistant.switch_session(conversation_store.active_path(conversation_store.session_dir()))
        if self.window:
            self.window.set_response("")
            # The composer too, not just the transcript. Measured: with a
            # half-typed question in the box, Ctrl+N cleared the transcript and
            # left the text sitting in the composer - so the next Enter sent a
            # question about the *previous* conversation into the new one, and
            # nothing on screen said so. `clear_input()` existed for this and had
            # no caller.
            self.window.clear_input()
            self.window.set_status("New conversation")

    def _open_conversation(self, ref: str) -> None:
        """Make `ref` the open conversation and redraw the window from its history."""
        if not self.assistant:
            return
        if ref and not conversation_store.switch(conversation_store.session_dir(), ref):
            if self.window:
                self.window.set_status("That conversation is gone")
            return
        self._follow_active_conversation(force=True)

    def _delete_conversation(self, ref: str) -> None:
        if conversation_store.delete(conversation_store.session_dir(), ref):
            self._follow_active_conversation(force=True)

    def _follow_active_conversation(self, force: bool = False) -> None:
        """If the index's open conversation is not the one in use (a skill switched it), switch."""
        if not self.assistant:
            return
        want = conversation_store.active_path(conversation_store.session_dir())
        if not force and want == self.assistant.session_path:
            return
        self.assistant.switch_session(want)
        if self.window:
            self.window.show_conversation(self.assistant.visible_turns())
            # A transcript that ends mid-turn is repaired for the model on every
            # request (`build_messages` -> `history_repair.clean_history`), so
            # without saying so here the person sees their own unanswered
            # question and no reason for it. Cleared on the next turn, since the
            # interruption happened once and is now history.
            self.window.set_status(
                getattr(self.assistant, "restored_interruption", "") or "")

    def _on_user_input(self, window: ChronoaWindow, text: str) -> None:
        """Handle typed user input: a /command is expanded and @mentions filled in first (user_prompts.py)."""
        self._voice_turn = False
        expanded, note = user_prompts.prepare(text)
        if not expanded:
            if self.window:
                self.window.set_response(note)
            return
        tool, rest = user_prompts.forced_tool(expanded)
        if tool and self.assistant:
            # '/timer 5 minutes': the person chose the tool; the model fills in the rest
            self.assistant.forced_tool = tool
            expanded = rest or expanded
            note = note or f"/{tool}"
        if note and self.window:
            self.window.set_status(f"Running {note}")
        self._submit(expanded)

    # -- Quick Ask --------------------------------------------------------
    #
    # One question, one answer, not a conversation. The window does the asking
    # (`gui/quick_ask.py`); everything that decides *how* is here, because the
    # model choice, the tool whitelist, the privacy gates and the thread this
    # runs on all live on the assistant.

    def quick_ask(self, _action: "Gio.SimpleAction | None" = None, _param: object = None) -> None:
        """Open the Quick Ask popup, creating it once and reusing it after."""
        if self._quick_ask_window is None:
            from shani_chronoa.gui.quick_ask import QuickAskWindow
            self._quick_ask_window = QuickAskWindow(
                application=self, on_ask=self._quick_ask_run, on_keep=self._quick_ask_keep)
        self._quick_ask_window.present()

    def _quick_ask_run(self, text: str, done):
        """Returns the bridge's future, so a caller (or a test) can wait for the
        answer instead of guessing."""
        """Ask through the real assistant, on the real bridge.

        A Quick Ask answer goes through exactly the same `handle()` a typed turn
        does - same tools, same gates, same history repair - with one
        difference: the question and answer are not recorded, because that is
        the entire promise of the window. The assistant is asked and its reply
        is dropped rather than kept out of the history, which is why the turn is
        run against a throwaway copy of the conversation (see below).
        """
        assistant, llm = self.assistant, self.llm
        if not (assistant and llm and llm.is_available()):
            done("No model is available to answer with.")
            return None

        # A throwaway Assistant sharing the model, so the turn cannot write to
        # the conversation: an empty transcript plus the live percepts, which is
        # what "ask without keeping" has to mean. The spare is not cached -
        # holding one would mean holding a second copy of a conversation that
        # the real one is still changing.
        spare = Assistant(llm, percept_store=assistant.percept_store,
                          context_builder=assistant.context_builder, session_path=None,
                          config=getattr(assistant, "config", None))
        return self._async.run(spare.handle(text), lambda result: done(
            result if isinstance(result, Exception) else str(result)))

    def _quick_ask_keep(self, question: str, answer: str) -> None:
        """Put a kept Quick Ask into the open conversation, as a normal turn.

        Both messages are recorded and the transcript is redrawn, so a kept
        answer is indistinguishable from one asked in the main window - which is
        the only way "keep this" can mean anything later (search, export,
        reopening the conversation).
        """
        if not (self.assistant and self.window):
            return
        self.assistant._record({"role": "user", "content": question})
        self.assistant._record({"role": "assistant", "content": answer})
        self.window.show_conversation(self.assistant.visible_turns())
        self.window.set_status("Kept in the conversation")

    def attach_regenerate(self) -> None:
        """Give the window a way to re-ask the last question.

        Only once there is something to re-ask with. With no assistant there is
        no answer to take back, so the transcript is left without the control
        rather than given a button that reports nothing.
        """
        if self.window:
            self.window.set_regenerate_handler(
                self._regenerate if self.assistant else None)

    def _regenerate(self) -> None:
        """Take back the last answer and ask the same question again.

        The question is the one the discarded answer was answering, so the user
        does not have to retype it - which is the whole point of the control, and
        the reason `drop_last_reply()` returns it rather than the caller going
        and looking.

        The redraw is from the assistant's history, not an optimistic deletion
        in the view: the tool results that turn left behind go with it, and a
        reply replaced in the file but not on screen (or the reverse) is the kind
        of half-state this repo keeps paying for.
        """
        if not (self.assistant and self.window):
            return
        question, discarded = self.assistant.drop_last_reply()
        if not question:
            self.window.set_status("There is no answer to ask again")
            return
        self.window.show_conversation(self.assistant.visible_turns())
        # The question is back on screen immediately: `drop_last_reply` took it
        # out of the history so the re-ask records it once, and a transcript
        # that blinks empty while a model thinks reads as a lost message.
        self.window.add_user_turn(question)
        self.window.set_status("Asking again")
        self._voice_turn = False
        self._submit(question)

    def _submit(self, text: str, channel: str = "") -> None:
        """Send text through the assistant's tool-calling pipeline.

        `channel` says which surface asked. The typed window passes the
        default; an inbound gateway message passes its registered name, which
        is then recorded on that turn's captured facts so a private fact a
        channel heard never leaks into another channel's recall.
        """
        logger.info(f"User input: {text}")

        if not self.assistant or not self.llm or not self.llm.is_available():
            # The local model may have come up since startup (it loads after
            # login), or be installed and simply not running: try it before
            # giving up, and say what to do instead of a bare "not available".
            if self.assistant and self._use_local_server():
                pass
            else:
                self._explain_no_model(text)
                return

        if self.window:
            self.window.set_orb_state("processing")
            self.window.set_status("Processing...")

        self._turn_marks = {"start": time.monotonic()}
        self._turn_speech = self._new_speech_queue()
        self._turn_buffer = speech.SentenceBuffer()
        self._turn_streamed = []
        self._turn_channel = channel
        self._turn_future = self._async.run(
            self.assistant.handle(text, sink=self.event_sink(),
                                  channel=channel),
            self._on_response_ready)

    def _maybe_title(self) -> None:
        """After a conversation's first exchange, ask the local model for a title (once; never over a rename)."""
        llm, assistant = getattr(self, "llm", None), getattr(self, "assistant", None)
        if not (llm and assistant and hasattr(llm, "suggest_title") and assistant.session_path):
            return
        path = Path(assistant.session_path)
        entry = conversation_store.index(path.parent)["sessions"].get(path.stem, {})
        if entry.get("named") or entry.get("titled"):
            return
        turns = assistant.visible_turns()
        asked = next((text for role, text in turns if role == "user"), "")
        answered = next((text for role, text in turns if role == "assistant"), "")
        if not (asked and answered):
            return

        def done(result: object) -> bool:
            if isinstance(result, str) and result:
                conversation_store.set_generated_title(path.parent, path.stem, result)
            return GLib.SOURCE_REMOVE
        self._async.run(llm.suggest_title(asked, answered), done)

    def _capture_memory_from_turn(self, response: str) -> None:
        """Attempt automatic memory capture from the completed turn.

        Combines the user's question and the assistant's reply into one text
        block, because a fact often spans both ("I work at X" in the reply
        answers "where do you work" in the question). The memory sense's
        `remember_from_turn` handles gating (interval, trivial, self-ref,
        provenance, covered) and returns the reason when nothing was kept.
        """
        if not self.assistant:
            return
        turns = self.assistant.visible_turns()
        asked = next((text for role, text in turns if role == "user"), "")
        if not asked:
            return
        # The assistant's reply is the text we just produced. Combining both
        # captures facts that only appear in the answer to a question the user
        # asked. If there's no assistant reply yet (error path), skip.
        combined = f"User: {asked}\nAssistant: {response}"
        _, reason = memory.remember_from_turn(
            combined, force=False, channel=getattr(self, "_turn_channel", ""))
        if reason and reason != "":
            logger.debug("Memory capture skipped: %s", reason)

    def event_sink(self):
        """The live event sink for the current turn.

        It wires the same handlers the legacy `on_tool_call`/`on_tool_result`/
        `on_text` slots used to be, so the window behaves exactly as it did
        before - but the starting point for a parallel consumer is that a sink
        is constructed here, once, not a fourth verb added to `handle()`'s
        signature. An embedder subclassing the bridge overrides this to add a
        second subscriber.
        """
        from shani_chronoa import events
        return events.EventSink(
            on_tool_start=self._on_tool_call,
            on_tool_finish=self._on_tool_result,
            on_text=self._on_reply_text,
            on_compaction=self._on_compaction,
            on_cloud_turn=self._on_cloud_turn,
        )

    def _on_compaction(self, sentence: str) -> None:
        """A context notice in the transcript, where the conversation is.

        cline renders this as a `CompactionRow` in the message list and
        OpenHands emits a `CondensationEvent`. We compressed silently for the
        life of the app, which is indistinguishable from having forgotten.
        """
        GLib.idle_add(self._add_notice_row, sentence, "compaction")

    def _on_cloud_turn(self, provider: str) -> None:
        """The one thing in this app that can send a sentence off the machine."""
        GLib.idle_add(self._add_notice_row,
                      f"This turn was answered by {provider}, not on this "
                      f"machine.", "cloud")

    def _add_notice_row(self, sentence: str, kind: str) -> bool:
        if self.window is not None:
            self.window.add_notice_row(sentence, kind)
        return GLib.SOURCE_REMOVE

    def _on_tool_call(self, name: str, _arguments: dict) -> None:
        """Assistant callback - runs on AsyncBridge's background thread."""
        GLib.idle_add(self._on_tool_call_main, name)

    def _on_tool_call_main(self, name: str) -> bool:
        """Show which skill is currently running instead of a generic status."""
        if self.window:
            self.window.set_status(f"{name.replace('_', ' ').capitalize()}...")
        return GLib.SOURCE_REMOVE

    def _on_tool_result_main(self, name: str, arguments: dict, result: str, ok: bool) -> None:
        """Put a tool card in the transcript, from AsyncBridge's thread."""
        GLib.idle_add(self._add_tool_card, name, arguments, result, ok)

    def _add_tool_card(self, name: str, arguments: dict, result: str, ok: bool) -> bool:
        if self.window:
            self.window.add_tool_call(name, arguments, result, ok)
        return GLib.SOURCE_REMOVE

    def _on_tool_result(self, name: str, arguments: dict, result: str, ok: bool) -> None:
        """Assistant callback - runs on AsyncBridge's background thread.

        `on_tool_result` arrives after the skill has run, which is the only point
        at which the arguments *and* the output exist together, so this is where
        the card is built. A card that appeared at call time and was filled in
        later would need the same information in two places.
        """
        self._on_tool_result_main(name, arguments, result, ok)

    def _on_response_ready(self, result: object) -> bool:
        """Deliver an assistant response (or error) back on the GTK main thread."""
        if isinstance(result, Exception):
            logger.error(f"Processing error: {result}")
            if self.window:
                self.window.set_response(f"Error: {result}")
                self.window.set_orb_state("error")
                self.window.set_status("Ready")
            return GLib.SOURCE_REMOVE

        response = str(result)
        self._mark("reply")
        queue = getattr(self, "_turn_speech", None)
        speaking = queue is not None
        if not speaking:
            self._log_latency()
        if self.window:
            self.window.set_response(response)
            self.window.set_state(AssistantState.SPEAKING if speaking else AssistantState.IDLE)
            self.window.set_status("")
        # A `conversations` call in this turn may have opened another one.
        self._follow_active_conversation()
        self._maybe_title()

        # Turn complete: attempt automatic memory capture from the user's question
        # and the assistant's reply. `force=False` respects the interval throttle
        # so a rapid back-and-forth doesn't fill memory with near-duplicates.
        self._capture_memory_from_turn(response)

        if speaking:
            # Streamed: speak what is left in the buffer. Not streamed (a cloud
            # model, a budget message): speak the whole reply, still sentence by
            # sentence, so the first one plays while the next is synthesised.
            if getattr(self, "_turn_streamed", None):
                queue.speak(self._turn_buffer.flush())
            else:
                queue.speak(speech.split_sentences(response))
            queue.close()

        return GLib.SOURCE_REMOVE
