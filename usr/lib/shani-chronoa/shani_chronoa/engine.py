"""Chronoa's engine with no window: one object the shell can own.

The GTK app builds `ChronoaApplication` (and its mixins) and exports
`AgentService` from `do_activate`. A shell edition has no GTK, so it builds
this instead: the same model, tool loop, conversation store and D-Bus agent,
with nothing from `shani_chronoa.gui`, `settings_window` or `app` imported at
runtime. The shell draws its own surfaces and calls the agent over the bus.

`llm` is injectable so a harness can drive a whole turn with the stand-in
model used by the repo's other tests, without a real Ollama server.
"""

from __future__ import annotations

import logging
from typing import Optional

from gi.repository import GLib

from shani_chronoa import conversation_store
from shani_chronoa.agent_service import AgentService
from shani_chronoa.asyncbridge import AsyncBridge
from shani_chronoa.assistant import Assistant
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.hardware_profile import HardwareProfile
from shani_chronoa.ollama_llm import OllamaLLM
from shani_chronoa.senses.context import ContextBuilder
from shani_chronoa.senses.store import PerceptStore

logger = logging.getLogger(__name__)


class Engine:
    """The headless twin of the GTK app's component set.

    Only the pieces a turn and a shell need are constructed here. Audio,
    wake word and the trigger poller stay out: they are window/daemon
    concerns, and a shell that wants them starts the daemon as it already
    does on other editions.
    """

    def __init__(
        self, llm=None, config: Optional[ChronoaConfig] = None, session_path=None
    ) -> None:
        self.config = config or ChronoaConfig()
        self.hardware = HardwareProfile()
        persisted = self.config.hardware_profile
        if persisted in ("low", "medium", "high", "gpu"):
            self.hardware.profile = persisted
        model = self.config.model or self.hardware.get_model()
        if llm is None:
            # Ollama first, else the local llama.cpp server - the same choice
            # the GTK app makes, which this engine did not: it asked Ollama
            # alone and could not answer on a machine running a local model.
            from shani_chronoa.llm_choice import local_first

            llm, self.llm_how = local_first(OllamaLLM(
                host=self.config.ollama_host,
                model=model,
                context_window=self.hardware.get_context_window(),
            ))
        else:
            self.llm_how = "given"
        self.llm = llm
        self._async = AsyncBridge()
        self.percept_store = PerceptStore()
        self.context = ContextBuilder(
            consent=lambda sense: self.config.sense_allowed(sense)
        )
        self.assistant = Assistant(
            self.llm,
            percept_store=self.percept_store,
            context_builder=self.context,
            session_path=session_path
            or conversation_store.active_path(conversation_store.session_dir()),
            config=self.config,
        )
        self.agent = AgentService(self)

    def start(self) -> bool:
        """Own the Agent name, unless a GTK desktop app already has it.

        The desktop app is the Agent on GNOME/Plasma/COSMIC: it has the
        microphone, the window and the person's conversation. The engine is
        the Agent on a headless session, or when the desktop app is not
        running; it yields instead of fighting over one bus name.
        """
        return self.agent.export(yield_to_owner=True)

    def stop(self) -> None:
        self.agent.unexport()
        self._async.shutdown()

    def run(self) -> int:
        """Start and block on the GLib loop until SIGTERM/SIGINT."""
        import signal

        loop = GLib.MainLoop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            # PyGObject calls the callback with the signal number as its one
            # argument; MainLoop.quit takes none, and passing it directly
            # raised TypeError on every SIGTERM instead of stopping the loop.
            GLib.unix_signal_add(
                GLib.PRIORITY_DEFAULT, sig, lambda *_signal: loop.quit()
            )
        self.start()
        try:
            loop.run()
        finally:
            self.stop()
        return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    return Engine().run()


if __name__ == "__main__":
    raise SystemExit(main())
