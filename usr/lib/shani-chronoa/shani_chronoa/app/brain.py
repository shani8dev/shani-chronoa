"""Which language model answers: Ollama, the local llama.cpp server, or - only when opted in - the cloud; setup when there is none."""

import logging
import threading

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('Gio', '2.0')
gi.require_version('Adw', '1')
from gi.repository import Gio, GLib  # type: ignore



from shani_chronoa.cloud_llm import (
    BYOK_PROVIDER_ORDER,
    CUSTOM_ID,
    DEFAULT_PROVIDER_ORDER,
    CloudLLMChain,
    custom_provider,
)
from shani_chronoa.redaction import redactor


logger = logging.getLogger(__name__)




class BrainMixin:
    """Which language model answers: Ollama, the local llama.cpp server, or - only when opted in - the cloud; setup when there is none. - a part of ChronoaApplication, which mixes it in."""


    def _llm_status_text(self) -> str:
        """Describe which LLM backend is active - nothing else surfaced this before.

        `self.llm` is never actually None when Ollama is unavailable - it's
        still a live (just non-functional) OllamaLLM instance, same as the
        `not self.llm.is_available()` check `_submit()` already uses - so
        this must check availability explicitly, not just "is it set".
        """
        # **Where answers come from, not "Ready" again.** The state line above
        # this one already says Ready, so the line read "Ready" twice
        # ("Ready (this computer: local)"), and "LLM" is a word the window uses
        # nowhere else.
        if self.llm is None or not self.llm.is_available():
            return "No model is answering yet"
        if isinstance(self.llm, CloudLLMChain):
            # **The provider that answered, not the chain's membership.**
            # `CloudLLMChain.model` is every backend's model joined - measured
            # on a real chain: "default, kilo-auto/free, cohere/north-mini-code"
            # - which is a list of what *could* answer, printed as if it were
            # what did. `last_provider` is the one that did, by display name.
            provider = str(getattr(self.llm, "last_provider", "") or "").strip()
            if provider:
                return f"Answering from the cloud: {provider}"
            return "Answering from the cloud"
        if getattr(self.llm, "local", False):
            # "local" is the provider's placeholder when no model file is
            # chosen, not a model anyone could recognise.
            if self.llm.model in ("", "local"):
                return "Answering on this computer"
            return f"Answering on this computer: {self.llm.model}"
        return f"Answering through Ollama: {self.llm.model}"

    def _toggle_privacy(self, _action: Gio.SimpleAction, _param: object) -> None:
        """Toggle privacy mode."""
        if self.privacy.is_local_only:
            self.privacy.disable_local_mode()
            if self.window:
                self.window.set_status("Privacy: OFF")
            # Only relevant now that privacy is off: pick up the cloud
            # fallback immediately if Ollama was unavailable at startup,
            # rather than waiting for a restart.
            if not self._ollama_available:
                self._maybe_enable_cloud_fallback()
        else:
            self.privacy.enable_local_mode()
            if self.window:
                self.window.set_status("Privacy: ON (Local Only)")
            # Privacy just turned back on - drop the cloud LLM immediately
            # if that's what's active. Re-enabling privacy mode should stop
            # sending prompts off the machine right away, not just at the
            # next restart.
            if isinstance(self.llm, CloudLLMChain):
                logger.info("Privacy mode re-enabled - dropping cloud LLM fallback")
                self.llm = None
                if self.assistant:
                    self.assistant.llm = None

    def _setup_needed(self) -> bool:
        from shani_chronoa import setup_wizard
        try:
            return setup_wizard.needs_setup(self.config)
        except Exception:  # noqa: BLE001 - a probe failing must not stop the app opening
            logger.exception("could not tell whether setup is needed")
            return False

    def _open_setup(self, page: str = "") -> None:
        """Show the setup window - on `page` if given - and, when it finishes,
        pick up a model or voice it installed without a restart.

        **Registered with `pages`**, so `show-page setup:<tag>` and this are one
        window. They were two: this one was never noted, so `pages.show` built a
        second wizard with no `on_finished`, and a model installed from it was
        not picked up until a restart.
        """
        # **Not during teardown.** A queued "offer setup once" idle can be
        # drained inside `do_shutdown`, and building a window against an
        # application that has begun shutting down segfaults the interpreter.
        # Declining costs nothing: a fresh install that quits before the wizard
        # appeared gets it on the next launch, because `_setup_needed()` is
        # unchanged by this.
        if getattr(self, "_shutting_down", False):
            logger.info("not opening setup while the application shuts down")
            return
        from shani_chronoa import setup_wizard
        existing = getattr(self, "_setup_window", None)
        if existing is not None and existing.get_visible():
            if page and hasattr(existing, "show_page"):
                existing.show_page(page)
            existing.present()
            return

        def finished() -> None:
            if not self._ollama_available:
                self._use_local_server()
            from shani_chronoa.tts import PiperTTS
            self.tts = PiperTTS(voice=self.config.piper_voice)
            self.tts.rate = self.config.get_double("speech-rate", 1.0) or 1.0
            self.stt = self._build_stt()
            if self.window:
                self.window.set_status(self._llm_status_text())
        self._setup_window = setup_wizard.build_window(self, self.config, on_finished=finished)
        from shani_chronoa import pages
        pages.note_window("setup", self._setup_window)
        if page and hasattr(self._setup_window, "show_page"):
            self._setup_window.show_page(page)
        self._setup_window.present()

    def _use_local_server(self, wake: bool = True) -> bool:
        """Use llama.cpp's llama-server (local_llm.py) when Ollama is absent; still on this machine.

        Sets `_ollama_available` too, because every caller treats that flag as
        "a local model answers" - which is the promise that keeps the cloud
        fallback from engaging while a local model is running.

        **`wake=True` starts the service when it is not already answering**, which
        it did not before. That is what makes `presence.py` honest: releasing the
        model is only a state if something brings it back, and with a bare
        `is_up()` check a released model was simply gone - a question would have
        gone to the cloud fallback or nowhere at all. With a cold start, "let go
        of the memory" and "still here" stop being opposites.

        `wake=False` is for the callers that must not start anything - checking
        whether a model is present is not a reason to load one.
        """
        from shani_chronoa import local_llm
        from shani_chronoa.presence import Presence
        if not local_llm.is_up():
            # Only Drowsy means "load it again". Sleeping means do not, and
            # reading a *presence* rather than the `_can_answer` bool is the
            # difference: a bool cannot tell "no model installed" from "asleep
            # on purpose", and a first version compared the bool to an enum,
            # which is never true and so quietly disabled the whole guard.
            if not wake or getattr(self, "_presence", Presence.ACTIVE) is Presence.SLEEPING:
                return False
            logger.info("llama-server is not answering; starting it for this question")
            problem = local_llm.start_service()
            if problem:
                logger.warning("could not start the local model server: %s", problem)
                return False
            if not local_llm.is_up():
                # Started, and still loading: `systemctl restart` returns before
                # the model is in memory, so one `is_up()` straight after it is
                # false on every cold start. The caller waits for it off the main
                # thread (`_wait_for_local_server`) instead of treating "loading"
                # as "will not" - which is what dropped the question on the floor
                # in a testbed agent run (2026-10-23): "started but is not
                # answering", logged in the same second the start was issued.
                logger.info("the local model server was started and is still loading")
                self._local_server_loading = True
                return False
        self._local_server_loading = False
        self.llm = local_llm.LocalLLM()
        self._ollama_available = True
        if getattr(self, "assistant", None):
            self.assistant.llm = self.llm
        logger.info("Using the local llama.cpp server at %s", local_llm.BASE_URL)
        return True

    def _wait_for_local_server(self, then, otherwise, timeout: float = 180.0) -> None:
        """Wait for a just-started local model to answer, without blocking the window.

        Polls `local_llm.is_up()` on a worker thread (each probe can take up to
        1.5 s, which on the main loop is a frozen window and an accessibility
        bus that stops answering) and calls `then()` once it answers, or
        `otherwise()` after `timeout` seconds - both on the main loop.
        """
        import time as _time
        from shani_chronoa import local_llm
        if getattr(self, "window", None):
            self.window.set_status("Starting the local model - the first answer takes a moment")

        def done(ok: bool) -> bool:
            self._local_server_loading = False
            if ok and self._use_local_server(wake=False):
                then()
            else:
                logger.warning("the local model did not answer within %.0f s of starting", timeout)
                otherwise()
            return False

        def worker() -> None:
            deadline = _time.monotonic() + timeout
            while _time.monotonic() < deadline:
                if local_llm.is_up():
                    GLib.idle_add(done, True)
                    return
                _time.sleep(1.0)
            GLib.idle_add(done, False)

        threading.Thread(target=worker, daemon=True, name="local-llm-wait").start()

    def _maybe_enable_cloud_fallback(self) -> None:
        """Switch self.llm to the free cloud LLM chain if both opt-in gates are open.

        Both privacy mode being off AND cloud-fallback-enabled must hold -
        this is the one path in Chronoa that sends prompts and tool-call
        arguments off the machine, so it never triggers from a single
        setting alone.
        """
        if self._ollama_available or isinstance(self.llm, CloudLLMChain):
            return
        shut = self._cloud_gate_reason()
        if shut:
            # **Name every backend and its own reason.** This said only "no
            # Ollama", on a machine where Ollama was never the default and
            # llama.cpp is - so a person reading the log could not tell that the
            # local backend it *does* use was the thing that was missing. The
            # whole point of this line is that it is the answer to "why is
            # nothing answering?", and it was answering a question about the
            # wrong program.
            try:
                from shani_chronoa import local_llm
                binary = local_llm.server_binary()
                if not binary:
                    local = "llama.cpp is not installed"
                elif local_llm.active():
                    local = f"llama.cpp has {local_llm.active()} but is not answering"
                else:
                    local = "llama.cpp is installed but has no model yet"
            except Exception as exc:  # noqa: BLE001 - a report must not raise
                local = f"llama.cpp could not be checked ({type(exc).__name__})"
            gates = []
            if self.privacy.is_local_only:
                gates.append("privacy mode is on")
            if not self.config.cloud_fallback_enabled:
                gates.append("cloud fallback is off")
            logger.info(
                "LLM unavailable - %s; Ollama is not running; no cloud model (%s)",
                local, " and ".join(gates),
            )
            return
        # BYOK providers (Anthropic/OpenAI/Google/Groq) go first when a key
        # is configured for them - presumably far more capable/reliable
        # than the anonymous free chain, which stays as the fallback of
        # last resort. CloudLLMChain itself skips any BYOK provider left
        # without a key, so listing all of them here is harmless.
        api_keys = self.config.cloud_llm_api_keys()
        # Make the sanitizer/sandbox-env-injection aware of the actual live
        # keys (stored in GSettings by design, not the vault - see
        # ChronoaConfig.cloud_llm_api_keys()'s threat-model note) so
        # redactor.sanitize() can actually redact them.
        # Without this the vault's cache stays empty and sanitization is a
        # silent no-op against every real key in use.
        for provider_id, key_value in api_keys.items():
            redactor.register(f"cloud_llm_{provider_id}", key_value)
        cloud_models = self.config.cloud_llm_models()
        provider_order = BYOK_PROVIDER_ORDER + DEFAULT_PROVIDER_ORDER
        # A hand-typed endpoint joins the chain ahead of the anonymous free
        # providers: the person who wrote an address down has usually written it
        # down because it is the one they trust to answer (their own router, a
        # gateway they pay for). It is still gated by the same two switches as
        # everything else here - reaching it is not a way around privacy mode.
        custom_url = self.config.get("custom-llm-base-url", "")
        custom_model = self.config.get("custom-llm-model", "")
        if custom_provider(custom_url, custom_model) is not None:
            provider_order = (CUSTOM_ID,) + provider_order
        # **The chosen model travels with the key.** `cloud_llm_models()` returns
        # only what a person has actually picked, so the key names in the dict the
        # chain reads ("<pid>_model") line up and an untouched install is
        # unchanged. Without this, a model picked in the setup wizard was stored
        # and then read by nothing.
        api_keys = dict(api_keys)
        api_keys.update(cloud_models)
        cloud_llm = CloudLLMChain(provider_ids=provider_order, api_keys=api_keys,
                                  custom_base_url=custom_url, custom_model=custom_model)
        if not cloud_llm.is_available():
            return
        active = [p for p in provider_order
                  if api_keys.get(p) or p in DEFAULT_PROVIDER_ORDER or p == CUSTOM_ID]
        logger.warning(
            f"Falling back to cloud LLM providers ({', '.join(active)}) - privacy mode is "
            "off and cloud-fallback-enabled is set. Prompts will leave this machine."
        )
        self.llm = cloud_llm
        if self.assistant:
            self.assistant.llm = cloud_llm
        # The composer's "Answers with" row names the engine; it was drawn
        # before this one existed and kept saying "No model" (measured in the
        # demo recorder, 2026-10-15), so it is redrawn now.
        refresh = getattr(self, "_refresh_strip", None)
        if callable(refresh) and getattr(self, "window", None) is not None:
            GLib.idle_add(lambda: (refresh(), False)[1])

    def _cloud_gate_reason(self) -> str:
        """Why the cloud route is shut, or "" when it is open.

        **One answer, read by both paths.** The startup fallback had this inline,
        interleaved with the log line it existed to produce - so a *choice* to use
        the cloud could not ask the same question, and would have had to
        re-derive it. Re-deriving is how the two came to disagree, which is the
        lesson `model.py`'s `_selected_engine` records from the other side.
        """
        if self.privacy.is_local_only and not self.config.cloud_fallback_enabled:
            return "privacy mode is on and cloud fallback is off"
        if self.privacy.is_local_only:
            return "privacy mode is on - nothing leaves this machine"
        if not self.config.cloud_fallback_enabled:
            return ("cloud fallback is off; turn on 'Use the free cloud gateways "
                    "when no local model answers' in Settings")
        return ""

    def select_llm(self, what: str) -> str:
        """Switch the answering engine now: ``"local"`` or ``"cloud"``.

        "" on success, or the sentence naming which gate shut.

        **This did not exist, and it is why the Models panel could not offer a
        cloud model.** `_maybe_enable_cloud_fallback` runs once at startup and
        returns early when `isinstance(self.llm, CloudLLMChain)` - so a machine
        that started on llama.cpp and was switched to the cloud *by the toggle*
        had no way back except restarting, and a machine that started on the cloud
        never reconsidered at all. The toggle flips the fallback on and off; it
        cannot answer "use this one instead".

        `self.assistant.llm` is set as well as `self.llm` because the assistant
        holds its own reference and reads that one on every request - which is the
        same pair `_toggle_cloud_fallback` already has to keep in step.
        """
        want = (what or "").strip().lower()
        if want not in ("local", "cloud"):
            return "%r is not a choice; use 'local' or 'cloud'" % (what,)
        reason = self._cloud_gate_reason()
        if want == "cloud" and reason:
            return reason
        try:
            if want == "local":
                from shani_chronoa import local_llm

                chosen = local_llm.LocalLLM()
            else:
                api_keys = self.config.cloud_llm_api_keys()
                # Registered with redactor here for the same reason the startup
                # path does it: a key that reaches the sanitiser's cache is a key
                # that cannot leak into a prompt, and skipping it makes that
                # redaction a silent no-op.
                for provider_id, key_value in api_keys.items():
                    redactor.register(f"cloud_llm_{provider_id}", key_value)
                from shani_chronoa.cloud_llm import (
                    BYOK_PROVIDER_ORDER, CUSTOM_ID, DEFAULT_PROVIDER_ORDER,
                    CloudLLMChain)
                from shani_chronoa.cloud_llm import custom_provider

                custom_url = self.config.get("custom-llm-base-url", "")
                custom_model = self.config.get("custom-llm-model", "")
                order = BYOK_PROVIDER_ORDER + DEFAULT_PROVIDER_ORDER
                if custom_provider(custom_url, custom_model) is not None:
                    order = (CUSTOM_ID,) + order
                chosen = CloudLLMChain(
                    provider_ids=order,
                    api_keys={**api_keys, **self.config.cloud_llm_models()},
                    custom_base_url=custom_url,
                    custom_model=custom_model)
                if not chosen.is_available():
                    return ("no cloud provider is reachable: the free gateways are "
                            "rate-limited or down, and no API key is configured for "
                            "any of the ones that need one")
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            return f"could not switch to {want}: {type(exc).__name__}: {exc}"

        self.llm = chosen
        if self.assistant:
            self.assistant.llm = chosen
        # The composer's "Answers with" row names the engine; it was drawn
        # before this one existed and kept saying "No model" (measured in the
        # demo recorder, 2026-10-15), so it is redrawn now.
        refresh = getattr(self, "_refresh_strip", None)
        if callable(refresh) and getattr(self, "window", None) is not None:
            GLib.idle_add(lambda: (refresh(), False)[1])
        logger.warning("answering engine switched to %s (%s)", want,
                       type(chosen).__name__)
        return ""

    def current_llm_kind(self) -> str:
        """``"cloud"``, ``"local"``, or ``"none"`` - which engine is holding the
        turn, read from the object rather than from a setting.

        A setting cannot answer this: the engine is *selected*, and the selection
        is re-decided at startup and by the fallback, so `cloud-fallback-enabled`
        being true says nothing about what is answering now.
        """
        from shani_chronoa.cloud_llm import CloudLLMChain

        if self.llm is None:
            return "none"
        return "cloud" if isinstance(self.llm, CloudLLMChain) else "local"

    def _toggle_cloud_fallback(self, _action: Gio.SimpleAction, _param: object) -> None:
        """Toggle the free-cloud-LLM opt-in and react immediately, same as toggle-privacy."""
        new_value = not self.config.cloud_fallback_enabled
        self.config.set("cloud-fallback-enabled", "true" if new_value else "false")
        if new_value:
            self._maybe_enable_cloud_fallback()
        elif isinstance(self.llm, CloudLLMChain):
            logger.info("Cloud fallback disabled - dropping cloud LLM")
            self.llm = None
            if self.assistant:
                self.assistant.llm = None
        if self.window:
            self.window.set_status(f"Cloud fallback: {'ON' if new_value else 'OFF'}")

    def _router_fallback(self, text: str) -> bool:
        """One turn from the distilled router, when no model answers at all.

        **A prior, not a brain, and it is labelled as one.** The router names a
        skill; it cannot fill in arguments, so it may only reach a skill that
        declares none - running one with `{}` means running its defaults, which
        is the rule `assistant.py` already refuses to break for a model. The
        call goes through `execute_tool_outcome`, so the whitelist, the consent
        key, the sandbox and the post-condition all apply as they always do.

        Returns False when there is nothing to offer, which is the common case
        and the honest one: a router that has not separated anything should not
        be made to guess on a person's behalf.
        """
        from shani_chronoa import distill, tools
        from shani_chronoa.tool_tracking import ORIGIN_USER
        proposal = distill.fallback(text)
        if not proposal:
            return False
        name = str(proposal["tool"])
        outcome = tools.execute_tool_outcome(name, {}, origin=ORIGIN_USER)
        if self.window:
            # No `add_user_turn`: both the typed path and the spoken one have
            # already put this request on screen before `_submit` is reached,
            # and adding it again here showed the person their own words twice.
            self.window.set_response(
                f"{outcome.text}\n\n(No language model is installed, so a "
                f"distilled router picked `{name}` - {proposal['reason']}. "
                f"Runner-up was `{proposal['runner_up']}`.)")
            self.window.set_status(f"Routed by the distilled student: {name}")
            self.window.set_orb_state("idle")
        logger.info("no model answering; distilled router picked %s", name)
        return True

    def _explain_no_model(self, text: str) -> None:
        """No model answers: the router if it can be sure, the installed model, or setup - never just "not available"."""
        from shani_chronoa import local_llm
        from shani_chronoa.presence import Presence
        if not self.window:
            return
        if self._router_fallback(text):
            return
        self.window.set_orb_state("error")
        if local_llm.installed() and local_llm.server_binary():
            # **A person who chose Asleep is not a person waiting for a cold
            # start.** This branch started the service unconditionally, and it is
            # the one path that defeated `_use_local_server`'s own guard: the
            # guard returns False *precisely so that nothing is started*, and
            # then this ran and started it. Measured with a model installed and a
            # stub window - the service was started and the response read
            # "Starting the local model". So "Asleep" was unchoosable *and*, had
            # it been chosen, would have been ignored here.
            if getattr(self, "_presence", Presence.ACTIVE) is Presence.SLEEPING:
                self.window.set_orb_state("error")
                self.window.set_response(
                    "The model is asleep, so nothing was started. Press "
                    "\"Wake up\" in Settings > Models > Right now to load it, "
                    "then send your question again.")
                self.window.set_status("The model is asleep")
                # **What you typed is not consumed by a refusal.**
                self.window.restore_last_sent(text)
                return
            self.window.set_response("Starting the local model - this takes a few seconds the first time. "
                                     "Your question will be answered when it is ready.")
            self.window.set_status("Starting the local model...")

            def start_and_retry() -> None:
                import time
                problem = local_llm.start_service()
                deadline = time.monotonic() + 120
                while not problem and time.monotonic() < deadline and not local_llm.is_up():
                    time.sleep(1.0)
                GLib.idle_add(lambda: (self._submit(text) if local_llm.is_up() else
                                       self.window.set_response(f"The local model did not start: "
                                                                f"{problem or 'it is still loading'}. Open Settings > "
                                                                f"Set up Chronoa again to check it."), False)[1])
            threading.Thread(target=start_and_retry, daemon=True).start()
            return
        self.window.set_response("Chronoa has no language model yet. Setup downloads one (about 1 GB) "
                                 "and runs it on this computer. Your question is back in the box - "
                                 "send it again once the model is ready.")
        self.window.set_status("No model - opening setup")
        # **The first message anyone ever types was being eaten here.**
        #
        # The branch above, for a model that is installed but not running, is
        # careful: it starts the service and **re-submits the same `text`**
        # afterwards. This branch, the one every fresh install takes, dropped it
        # on the floor - and `window._submit_input` had already cleared the
        # composer and the draft before emitting, so the question was gone from
        # the one place it was written. The transcript kept the question and
        # never the answer, so after a 1 GB download the person who typed the
        # very first thing they ever asked has to reconstruct it from memory.
        #
        # **Back in the composer, not re-sent automatically.** A model download
        # takes minutes; answering a question typed that long ago without being
        # asked again is its own surprise. `set_input_text` is the existing
        # "show this in the box" half of "send this", kept separate from
        # `submit_text` precisely so a caller can want the first without the
        # second - and the text is where it was typed, so Enter is all it takes.
        #
        # `restore_last_sent` rather than `set_input_text`, because the chips go
        # too: `take_attachments` clears them on the way out, so restoring the
        # sentence alone would put back half the message and leave three files
        # silently gone - and a promise the screen does not back is the defect
        # this whole review is about, not a fix for it.
        self.window.restore_last_sent(text)
        self._open_setup()
