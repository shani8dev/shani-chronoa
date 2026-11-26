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
from typing import Optional, Tuple

from shani_chronoa.redaction import redactor

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


def _config(config=None):
    """The config to read. A caller with no config gets the machine's own."""
    if config is not None:
        return config
    from shani_chronoa.config import ChronoaConfig

    return ChronoaConfig()


def _cloud():
    """The cloud engine, imported only when the cloud route is actually used."""
    from shani_chronoa import cloud_llm

    return cloud_llm


def cloud_gate_reason(config=None) -> str:
    """Why the cloud route is shut, or "" when it is open.

    Both gates must hold: privacy mode off and cloud-fallback-enabled on. This
    is the one answer both the startup fallback and an explicit choice read,
    so the two cannot disagree about whether anything may leave the machine.
    """
    cfg = _config(config)
    private = bool(getattr(cfg, "privacy_mode", False))
    fallback = bool(getattr(cfg, "cloud_fallback_enabled", False))
    if private and not fallback:
        return "privacy mode is on and cloud fallback is off"
    if private:
        return "privacy mode is on - nothing leaves this machine"
    if not fallback:
        return (
            "cloud fallback is off; turn on 'Use the free cloud gateways "
            "when no local model answers' in Settings"
        )
    return ""


def answer_mode(config=None) -> str:
    """The stored answer mode as one of "auto", "local", "cloud".

    The strip stores "local-cloud" for "start here, fall back if it cannot
    answer"; that is "auto" here. Anything unknown reads "local", the
    conservative direction the schema's own default takes.
    """
    mode = getattr(_config(config), "answer_mode", "local") or "local"
    mode = str(mode).strip().lower()
    if mode in ("local-cloud", "auto"):
        return "auto"
    if mode == "cloud":
        return "cloud"
    return "local"


def providers(config=None) -> list:
    """Readable cloud provider rows, from the registry and the person's choices.

    Never returns an API key's value: only whether one is set. Rows carry
    stable keys, so a client can draw its own sentence from them.
    """
    cfg = _config(config)
    engine = _cloud()
    keys = cfg.cloud_llm_api_keys() or {}
    models = cfg.cloud_llm_models() or {}
    rows = []
    for pid in engine.BYOK_PROVIDER_ORDER + engine.DEFAULT_PROVIDER_ORDER:
        provider = engine.PROVIDERS[pid]
        rows.append(
            {
                "id": pid,
                "name": provider.name,
                "kind": "byok" if provider.requires_key else "free",
                "requires_key": bool(provider.requires_key),
                "key_set": bool((keys.get(pid) or "").strip()),
                "model": models.get("%s_model" % (pid,), "") or provider.default_model,
            }
        )
    custom_url = cfg.get("custom-llm-base-url", "")
    custom_model = cfg.get("custom-llm-model", "")
    custom = engine.custom_provider(custom_url, custom_model)
    if custom is not None:
        rows.append(
            {
                "id": engine.CUSTOM_ID,
                "name": custom.name,
                "kind": "custom",
                "requires_key": False,
                "key_set": False,
                "model": custom_model or custom.default_model,
            }
        )
    return rows


def _cloud_chain(config) -> Tuple[Optional[object], str]:
    """Build the opted-in chain, or return a sentence saying why it cannot answer."""
    engine = _cloud()
    api_keys = config.cloud_llm_api_keys()
    # Make the sanitiser aware of the live keys: without this, redaction is a
    # silent no-op against every key in use.
    for provider_id, key_value in api_keys.items():
        redactor.register(f"cloud_llm_{provider_id}", key_value)
    cloud_models = config.cloud_llm_models()
    order = engine.BYOK_PROVIDER_ORDER + engine.DEFAULT_PROVIDER_ORDER
    custom_url = config.get("custom-llm-base-url", "")
    custom_model = config.get("custom-llm-model", "")
    if engine.custom_provider(custom_url, custom_model) is not None:
        order = (engine.CUSTOM_ID,) + order
    keys = dict(api_keys)
    keys.update(cloud_models)
    try:
        chosen = engine.CloudLLMChain(
            provider_ids=order,
            api_keys=keys,
            custom_base_url=custom_url,
            custom_model=custom_model,
        )
        if not chosen.is_available():
            return None, (
                "no cloud provider is reachable: the free gateways are "
                "rate-limited or down, and no API key is configured for "
                "any of the ones that need one"
            )
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        return None, f"could not switch to cloud: {type(exc).__name__}: {exc}"
    return chosen, "cloud"


def select_llm(what, config=None, ollama=None) -> Tuple[Optional[object], str]:
    """Choose the answering engine now: ``"local"``, ``"cloud"`` or ``"auto"``.

    Returns ``(llm, how)``. ``"local"`` wakes the local server; ``"auto"``
    takes what `local_first` finds and falls back to the cloud only when both
    gates are open; ``"cloud"`` needs the gates open and a reachable provider.
    A refusal is a sentence in ``how``, never an exception.
    """
    want = (what or "").strip().lower()
    if want not in ("local", "cloud", "auto"):
        return None, "%r is not a choice; use 'local', 'cloud' or 'auto'" % (what,)
    local = None
    local_how = ""
    if want in ("local", "auto"):
        local, local_how = local_first(ollama, wake=True)
        if want == "local" or local_how in ("ollama", "local"):
            return local, local_how
    cfg = _config(config)
    shut = cloud_gate_reason(cfg)
    if shut:
        return (local, shut) if want == "auto" else (None, shut)
    chosen, how = _cloud_chain(cfg)
    if chosen is not None:
        return chosen, how
    return (local, how) if want == "auto" else (None, how)


def cloud_fallback(config=None) -> Tuple[Optional[object], str]:
    """The startup fallback decision: an opted-in cloud chain, or why not.

    Returns ``(None, reason)`` when a gate is shut or no provider is reachable,
    so a caller can keep whatever local engine it already has. This knows
    nothing about the app's own objects - that part belongs to the client.
    """
    cfg = _config(config)
    shut = cloud_gate_reason(cfg)
    if shut:
        return None, shut
    return _cloud_chain(cfg)


#: Where an answer may come from, and the three settings each choice is: the
#: mode, whether privacy mode is on, whether the cloud may answer. One table,
#: because the GTK app and the engine's `models use` each kept their own and
#: the engine's switched to the cloud with privacy mode still on (and wrote the
#: fallback as the string "true").
EGRESS = {
    "this-machine": ("local", True, False),
    "local-answers": ("local", False, False),
    "cloud": ("cloud", False, True),
    "local-cloud": ("local-cloud", False, True),
}


def apply_egress(config, key: str) -> str:
    """Write one egress choice as all three settings together; return the mode."""
    mode, privacy, fallback = EGRESS[key]
    config.set("privacy-mode", privacy)
    config.set("cloud-fallback-enabled", fallback)
    config.set("answer-mode", mode)
    return mode
