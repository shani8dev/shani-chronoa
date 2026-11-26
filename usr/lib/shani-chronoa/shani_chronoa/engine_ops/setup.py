"""First-run setup as engine data: no GTK, one wizard implementation.

The wizard's module-level functions are the only implementation of state and
of each step. This module adds only the boundary rules a GTK window cannot
enforce: a download needs ``confirm``, failures are returned as data, and the
outcome is a stable code beside the wizard's sentence.
"""

from __future__ import annotations

#: The steps a client may ask for. Each maps to one ``setup_wizard.setup_*``.
STEPS = ("brain", "ears", "voice", "eyes", "memory", "languages", "kokoro")


def _wizard():
    """Import the wizard's pure layer lazily, through its old-path shim.

    The shim aliases the wizard's module object; the wizard's step functions
    import no GTK typelib at module scope, so the engine stays GTK-free. The
    GTK window and its widgets are built only by `build_window()`, which the
    engine never calls.
    """
    from shani_chronoa import setup_wizard

    return setup_wizard


def _config(config):
    if config is not None:
        return config
    from shani_chronoa.config import ChronoaConfig

    return ChronoaConfig()


def state(config=None) -> dict:
    """What the wizard would show, bounded so a keyring cannot hold the bus.

    The wizard's state probe reads cloud keys; on a fresh session that can
    activate the keyring and take tens of seconds. A panel gets the same three
    keys either way, with "not checked" instead of a confident "no".
    """
    from shani_chronoa.engine_ops.models import NOT_CHECKED, bounded

    wizard = _wizard()
    cfg = _config(config)
    ready = bounded(lambda: wizard.state(cfg))
    if ready is NOT_CHECKED:
        return {
            "state": {
                "detail": "not checked - the setup probes did not answer quickly"
            },
            "needs_setup": None,
            "stt_catalogue": [],
        }
    needs = bounded(lambda: wizard.needs_setup(cfg))
    catalogue = bounded(lambda: wizard.stt_catalogue())
    return {
        "state": ready,
        "needs_setup": None if needs is NOT_CHECKED else needs,
        "stt_catalogue": [] if catalogue is NOT_CHECKED else catalogue,
    }


def step(step, value="", *, confirm=False, config=None, report=None) -> dict:
    """Run one setup step. Every step downloads a model, so all need ``confirm``.

    ``value`` is the model/voice key for ``brain``, ``ears``, ``eyes``, ``voice``
    and ``kokoro``; empty for ``memory``; a comma-separated language list for
    ``languages``, where the extra token ``listen`` also enables listening.
    The existing ``model-download-enabled`` setting still applies - this
    ``confirm`` is the caller's own agreement, not a silent change to it.
    """
    name = str(step or "").strip().lower()
    if name not in STEPS:
        return {
            "error": f"unknown setup step {step!r}; choose one of: {', '.join(STEPS)}"
        }
    if not confirm:
        return {"error": f"confirmation required: {name} downloads a model"}

    text = str(value or "").strip()
    if name in ("brain", "ears", "eyes", "voice", "kokoro") and not text:
        return {"error": f"{name} needs a value (see /setup)"}

    wizard = _wizard()
    cfg = _config(config)
    try:
        if name == "brain":
            message = wizard.setup_brain(text, report=report, config=cfg)
        elif name == "ears":
            message = wizard.setup_ears(text, report=report, config=cfg)
        elif name == "voice":
            message = wizard.setup_voice(text, report=report, config=cfg)
        elif name == "eyes":
            message = wizard.setup_eyes(text, report=report, config=cfg)
        elif name == "memory":
            message = wizard.setup_memory(report=report, config=cfg)
        elif name == "kokoro":
            message = wizard.setup_kokoro(text, report=report, config=cfg)
        else:
            codes = [c.strip() for c in text.split(",") if c.strip()]
            listen = "listen" in codes
            message = wizard.setup_languages(
                [c for c in codes if c != "listen"], listen, report=report, config=cfg
            )
    except Exception as exc:  # noqa: BLE001 - nothing crosses the boundary as an exception
        return {"error": f"{name} could not finish: {exc}"}

    # The wizard answers in sentences. Its own list of unfinished phrases is
    # the one definition of "did not finish" - no second copy here.
    blocked = any(phrase in message for phrase in getattr(wizard, "_NOT_DONE", ()))
    return {
        "step": name,
        "result": "blocked" if blocked else "done",
        "message": message,
    }
