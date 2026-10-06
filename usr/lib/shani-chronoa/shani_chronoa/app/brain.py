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
        if self.llm is None or not self.llm.is_available():
            return "LLM unavailable"
        if isinstance(self.llm, CloudLLMChain):
            return f"Ready (cloud: {self.llm.model})"
        if getattr(self.llm, "local", False):
            return f"Ready (this computer: {self.llm.model})"
        return f"Ready (Ollama: {self.llm.model})"

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

    def _open_setup(self) -> None:
        """Show the setup window; when it finishes, pick up a model or voice it installed without a restart."""
        from shani_chronoa import setup_wizard
        existing = getattr(self, "_setup_window", None)
        if existing is not None and existing.get_visible():
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
                # Started, and still not answering. Saying so beats a silent
                # False: the difference is "it is loading" and "it will not".
                logger.warning("the local model server was started but is not answering")
                return False
        self.llm = local_llm.LocalLLM()
        self._ollama_available = True
        if getattr(self, "assistant", None):
            self.assistant.llm = self.llm
        logger.info("Using the local llama.cpp server at %s", local_llm.BASE_URL)
        return True

    def _maybe_enable_cloud_fallback(self) -> None:
        """Switch self.llm to the free cloud LLM chain if both opt-in gates are open.

        Both privacy mode being off AND cloud-fallback-enabled must hold -
        this is the one path in Chronoa that sends prompts and tool-call
        arguments off the machine, so it never triggers from a single
        setting alone.
        """
        if self._ollama_available or isinstance(self.llm, CloudLLMChain):
            return
        if self.privacy.is_local_only or not self.config.cloud_fallback_enabled:
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
        if not self.window:
            return
        if self._router_fallback(text):
            return
        self.window.set_orb_state("error")
        if local_llm.installed() and local_llm.server_binary():
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
                                 "and runs it on this computer.")
        self.window.set_status("No model - opening setup")
        self._open_setup()
