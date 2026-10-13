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
            # no caller. A test may stub the window without this method; guard
            # honestly so the real implementation is exercised when present.
            #
            # **Moved, not destroyed.** The window now holds a draft per
            # conversation, so this switches to the new conversation's (empty)
            # draft instead of wiping the composer - the misfire above is gone
            # because the text belongs to the conversation it was written for,
            # and going back finds it where it was left. `clear_input()` is kept
            # as the fallback for a window that does not know about
            # conversations at all, which is every stand-in in the tests.
            if hasattr(self.window, "set_conversation"):
                self.window.set_conversation(
                    Path(conversation_store.active_path(
                        conversation_store.session_dir())).stem)
            elif hasattr(self.window, "clear_input"):
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
            # **And tell the window which conversation that is**, so the
            # composer goes with it. `show_conversation` only carries the turns;
            # the composer is one widget and a draft belongs to a conversation,
            # so without this a half-typed question followed the person across
            # and Enter put it into the conversation they had just opened.
            # `session_path.stem` is the same reference `_open_conversation`
            # takes, so the two cannot disagree about which is which.
            if hasattr(self.window, "set_conversation"):
                self.window.set_conversation(Path(want).stem if want else "")
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
            # Same rule for rewind: with no assistant there is no history to
            # take turns out of, so the gesture is not attached at all.
            self.window.set_rewind_handler(
                self._rewind_history if self.assistant else None)

    def _rewind_history(self, _window, text: str) -> bool:
        """The window's `rewind-history` signal, answered by the assistant.

        Returns whether the model actually gave the turns up, and the window
        refuses to cut the transcript when this says no. The two have to agree:
        a window showing fewer turns than the model remembers answers the next
        question as though the abandoned ones had happened.
        """
        if not self.assistant:
            return False
        return self.assistant.rewind_to_user_text(text)

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

    @property
    def _turn_in_flight(self) -> bool:
        """Whether a turn is running right now.

        A property over one attribute rather than the attribute itself, because
        this is a `Mixin` mixed into `Application` and nothing initialises it -
        the existing `_turn_*` state is the same, assigned on first use. Reading
        it before any turn has run must not raise, or the first message a person
        ever typed would fail on a boolean that was never set.
        """
        return getattr(self, "_turn_running", False)

    @_turn_in_flight.setter
    def _turn_in_flight(self, running: bool) -> None:
        self._turn_running = running

    @property
    def _turn_queue(self) -> list:
        """Messages typed while a turn was running, oldest first.

        A property for the same reason as `_turn_in_flight`: a `Mixin` has no
        `__init__` to hang the list on, and a queue that only exists after the
        first collision is a queue that cannot report "nothing is waiting".
        """
        queue = getattr(self, "_turn_pending", None)
        if queue is None:
            queue = []
            self._turn_pending = queue
        return queue

    def _turn_finished(self) -> None:
        """A turn is over - whether it answered or failed. Start the next.

        **Called from both exits of `_on_response_ready`.** The failure path
        returns early, so a turn that raised would otherwise strand every
        message queued behind it - and that is exactly the moment a person is
        watching hardest and would take a dead queue for the app hanging.

        **Cancelling does not come through here at all:** `AsyncBridge` drops a
        `CancelledError` without invoking the callback, so Stop cannot drain the
        queue by cancelling. It clears `_turn_in_flight` instead, and the next
        message typed starts immediately - behind anything still queued, since
        `_submit` only runs now when the queue is empty of running work.
        """
        self._turn_in_flight = False
        if self._turn_queue:
            text, channel = self._turn_queue.pop(0)
            logger.info(f"Starting the next of {len(self._turn_queue) + 1} queued")
            self._start_turn(text, channel)

    def _submit(self, text: str, channel: str = "") -> None:
        """Send text through the assistant's tool-calling pipeline.

        `channel` says which surface asked. The typed window passes the
        default; an inbound gateway message passes its registered name, which
        is then recorded on that turn's captured facts so a private fact a
        channel heard never leaks into another channel's recall.

        **A message sent while a turn is running is queued, not run beside it.**
        There is no lock anywhere on this path - not here, not in
        `assistant.handle`, and the composer is never disabled - so a second
        send used to start a second `handle()` against the same assistant while
        the first was still going. Measured: peak **2 concurrent `handle()`
        calls** on one `Assistant`, both turns mutating the same `_history`.

        Worse than the history, because it is the part a person sees. Every one
        of `_turn_marks`, `_turn_speech`, `_turn_buffer`, `_turn_streamed` and
        `_turn_future` is **reassigned** by the second turn, so the running
        turn's streamed text was written into the *second* turn's speech queue
        and its reply landed inside the second turn's bubble - two answers
        interleaved into one. Stop reads `_turn_future`, so it stopped whichever
        turn was submitted **last**, which is the one that had not started. And
        the first turn's elapsed clock restarted, so the wait grew by nothing and
        the reading under it went backwards.

        Queuing is the fix because **the message is already in the transcript** -
        `window._submit_input` adds the turn before it emits `user-input` - so
        refusing it would leave a question on screen that Chronoa never answers,
        which is worse than either alternative. Running it beside the first is
        what caused this. The queue is FIFO and drains from `_on_response_ready`
        on **both** exits, so a follow-up typed mid-answer is answered next, in
        the order it was asked.
        """
        logger.info(f"User input: {text}")

        if self._turn_in_flight or self._turn_queue:
            self._turn_queue.append((text, channel))
            if self._turn_in_flight:
                if self.window:
                    waiting = len(self._turn_queue)
                    self.window.set_status(
                        f"Answering that first - {waiting} message"
                        f"{'s' if waiting != 1 else ''} waiting")
                logger.info(f"Queued behind the running turn: {text!r}")
                return
            # **A turn was stopped with messages still waiting.** Nothing is
            # running, so this message cannot jump the queue - it goes to the
            # back and the *oldest* waiting one starts, which is the order the
            # transcript already shows. Without this, stopping twice and then
            # typing answers the new message first and answers the two older
            # ones never.
            self._turn_finished()
            return
        self._start_turn(text, channel)

    def _start_turn(self, text: str, channel: str = "") -> None:
        """Begin one turn, unconditionally. `_submit` decides *whether* now."""

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

        self._turn_in_flight = True
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

    #: The running tool's clock. `None` when nothing is running.
    _tool_clock = None
    #: Bumped each time a tool starts; a tick whose token no longer matches is
    #: stale and removes itself, which is what keeps a slow tool's last tick from
    #: landing on the *next* tool's status. Same shape as the transcript's
    #: `_follow_generation`.
    _tool_clock_token = 0
    _clock_token_at_start = -1
    _tool_clock_name = ""
    _tool_clock_started = 0.0

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
        """Show which skill is currently running instead of a generic status.

        **And how long it has been.** A 40-second `generate_image` showed a
        status line that said "Generate image..." and never changed, so the only
        question a person could answer - *is it working or has it hung?* - had no
        answer in it. A number that moves is the whole fix, and it is why this
        ticks rather than adding a placeholder card: the card is deliberately
        built at result time, when the arguments and the output exist together,
        and a placeholder would undo that on the most visible surface in the app.

        The clock is a `GLib` timeout on the main thread, like every other UI
        update here, and each tick re-checks `self._tool_clock_token` so a tool
        that has already finished cannot overwrite the next one's status.
        """
        self._stop_tool_clock()
        if not self.window:
            return GLib.SOURCE_REMOVE
        self._tool_clock_token += 1
        # **Recorded after the bump.** The tick is stale unless the token still
        # matches, so writing the "current" value before the increment made every
        # first tick stale on arrival - measured: the status never left
        # "Generate image..." no matter how long the tool ran.
        self._clock_token_at_start = self._tool_clock_token
        self._tool_clock_name = name
        self._tool_clock_started = time.monotonic()
        self.window.set_status(self._tool_clock_text(0))
        self._tool_clock = GLib.timeout_add_seconds(
            1, self._on_tool_clock_tick)
        return GLib.SOURCE_REMOVE

    def _tool_clock_text(self, seconds: float) -> str:
        said = self._tool_clock_name.replace("_", " ").capitalize()
        return f"{said}... {int(seconds)}s" if seconds >= 1 else f"{said}..."

    def _on_tool_clock_tick(self) -> bool:
        """One tick. Returns False - i.e. removes itself - if it is stale."""
        if self._tool_clock_token != self._clock_token_at_start:
            return GLib.SOURCE_REMOVE
        if self.window:
            self.window.set_status(
                self._tool_clock_text(time.monotonic() - self._tool_clock_started))
        return GLib.SOURCE_CONTINUE

    def _stop_tool_clock(self) -> None:
        """Stop ticking, so a finished tool's clock cannot outlive it.

        Bumping the token is what invalidates a tick that is already queued: the
        timeout may have fired before `source_remove` reached it.
        """
        if self._tool_clock is not None:
            GLib.source_remove(self._tool_clock)
            self._tool_clock = None
        self._tool_clock_token += 1

    def _on_tool_result_main(self, name: str, arguments: dict, result: str,
                             ok: bool, artifacts: tuple = ()) -> None:
        """Put a tool card in the transcript, from AsyncBridge's thread."""
        # First, so a clock still ticking cannot overwrite the status the card
        # is about to leave behind.
        self._stop_tool_clock()
        GLib.idle_add(self._add_tool_card, name, arguments, result, ok,
                      artifacts)

    def _add_tool_card(self, name: str, arguments: dict, result: str,
                       ok: bool, artifacts: tuple = ()) -> bool:
        if self.window:
            self.window.add_tool_call(name, arguments, result, ok, artifacts)
        return GLib.SOURCE_REMOVE

    def _on_tool_result(self, name: str, arguments: dict, result: str,
                        ok: bool, artifacts: tuple = ()) -> None:
        """Assistant callback - runs on AsyncBridge's background thread.

        `on_tool_result` arrives after the skill has run, which is the only point
        at which the arguments *and* the output exist together, so this is where
        the card is built. A card that appeared at call time and was filled in
        later would need the same information in two places.
        """
        self._on_tool_result_main(name, arguments, result, ok, artifacts)

    def _on_response_ready(self, result: object) -> bool:
        """Deliver an assistant response (or error) back on the GTK main thread."""
        if isinstance(result, Exception):
            logger.error(f"Processing error: {result}")
            if self.window:
                # **A failure is not an answer.** This went through
                # `set_response`, which builds the assistant's turn - so a
                # Python exception appeared in the transcript **in the
                # assistant's own bubble**, styled as its reply, and read as
                # Chronoa having said `Error: <repr>`. It was also the only
                # thing on screen that could not be regenerated, because it
                # looked like a completed answer.
                #
                # `add_notice_row` is the same seam the compaction and cloud
                # notes use, and its own docstring says why: *"a note about the
                # turn, not something the assistant said."* The kind is `error`
                # rather than `compaction` so it is not dimmed - a failure has to
                # be louder than the housekeeping it now sits beside.
                self.window.add_notice_row(
                    f"Chronoa could not finish that turn. {result}",
                    "error")
                self.window.set_orb_state("error")
                self.window.set_status("Ready")
            self._turn_finished()
            return GLib.SOURCE_REMOVE

        response = str(result)
        self._mark("reply")
        queue = getattr(self, "_turn_speech", None)
        # A queue that was stopped mid-turn has already reported "done", so
        # nothing would ever clear SPEAKING: the orb sat on "speaking" in
        # silence for minutes (measured 2026-10-08, after a spoken answer).
        speaking = queue is not None and not getattr(queue, "stopped", False)
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

        self._turn_finished()
        return GLib.SOURCE_REMOVE
