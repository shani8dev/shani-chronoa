"""Which model answers: the choice the engine makes, with no GTK.

The GTK app made this choice in `ui_gtk/app/brain.py` (`_use_local_server`,
`_maybe_enable_cloud_fallback`, `select_llm`): Ollama first, else the local
llama.cpp server (`local_llm`), started when it is not answering. The headless
engine copied only the first step, so with no GTK app it asked Ollama alone and
answered "Ollama server not available" on a machine whose local model was
installed and running (found by driving `chronoa -p` against the engine with
the person's own model folder).

`local_first` is that decision as a plain function. The engine uses it now;
the GTK app's wrapper (window status, waiting off the main loop, presence)
moves onto it next, with the cloud fallback and `answer-mode` (see
`shani-shell/docs/SHARED-ARCHITECTURE.md`, rule 2).
"""

from __future__ import annotations

import logging
from typing import Tuple

logger = logging.getLogger(__name__)


def local_first(ollama, *, wake: bool = True) -> Tuple[object, str]:
    """The model to answer with, and how it was chosen.

    Returns ``(llm, how)``: ``"ollama"`` when Ollama answers; ``"local"`` for
    the local llama.cpp server (started first when ``wake`` and it is not
    answering; it may still be loading, which its own requests report);
    otherwise ``ollama`` unchanged and a sentence saying why nothing local can
    answer, so the caller can say so instead of claiming to be ready.
    """
    try:
        if ollama.is_available():
            return ollama, "ollama"
    except Exception:  # noqa: BLE001 - an unreachable Ollama is the case handled below
        logger.debug("Ollama probe failed", exc_info=True)
    from shani_chronoa import local_llm

    if not local_llm.is_up():
        if not wake:
            return ollama, "the local model server is not running"
        if not local_llm.installed():
            return ollama, "no local model is installed"
        problem = local_llm.start_service()
        if problem:
            logger.warning("could not start the local model server: %s", problem)
            return ollama, f"could not start the local model server: {problem}"
        logger.info("started the local model server; it may still be loading")
    logger.info("answering with the local llama.cpp server at %s", local_llm.BASE_URL)
    return local_llm.LocalLLM(), "local"
