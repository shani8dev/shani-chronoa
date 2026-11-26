"""Which models exist and which one answers, as plain data.

Reads delegate to `llm_choice.local_first`, `local_llm` and the `cloud_llm`
provider table. Selecting the cloud needs ``confirm`` and the real gate check;
a refusal names the gate instead of claiming a switch that did not happen.
"""

from __future__ import annotations

import asyncio
import logging
import threading

from shani_chronoa import cloud_llm, config as config_mod, local_llm, llm_choice
from shani_chronoa.ollama_llm import OllamaLLM

logger = logging.getLogger(__name__)

#: A listing is a view, never a reason to wake a keyring or wait on a model
#: server. Anything slower than this is reported as "not checked", not as
#: absent and not as a blank that a panel would draw as "nothing here".
LIST_PROBE_SECONDS = 1.5
NOT_CHECKED = object()


def bounded(read, *, seconds: float = LIST_PROBE_SECONDS):
    """`read()` in a worker, or `NOT_CHECKED` if it has not answered in time.

    A keyring activation can take tens of seconds on a fresh session; the
    Agent call has to answer in a fraction of that. The worker is a daemon:
    a slow keyring cannot keep the process alive.
    """
    box = {}

    def worker():
        try:
            box["value"] = read()
        except Exception as exc:  # noqa: BLE001 - a failed probe is "not checked"
            logger.debug("bounded read failed: %s", exc)

    thread = threading.Thread(target=worker, daemon=True, name="models-list-probe")
    thread.start()
    thread.join(seconds)
    return box.get("value", NOT_CHECKED)


def _config(config=None):
    return config if config is not None else config_mod.ChronoaConfig()


def _ollama(config):
    return OllamaLLM(
        host=getattr(config, "ollama_host", "http://localhost:11434")
        or "http://localhost:11434",
        model=getattr(config, "model", "") or "qwen3:4b",
    )


def _ollama_ids(ollama) -> list[str]:
    try:
        rows = asyncio.run(ollama.list_models())
    except Exception:  # noqa: BLE001 - an unreachable Ollama has no models
        return []
    return [
        str(r.get("name", "")) for r in rows if isinstance(r, dict) and r.get("name")
    ]


def _cloud_reason(config) -> str:
    check = getattr(llm_choice, "cloud_gate_reason", None)
    if check is None:
        return "the cloud gate cannot be checked in this build"
    return str(check(config) or "")


def _cloud_current(config, keys: dict) -> str:
    if str(getattr(config, "answer_mode", "") or "").strip().lower() != "cloud":
        return ""
    order = cloud_llm.BYOK_PROVIDER_ORDER + cloud_llm.DEFAULT_PROVIDER_ORDER
    # A provider the person supplied a key for is the one they chose; only
    # when none is keyed does the free chain name the current provider.
    for pid in order:
        if keys.get(pid) and pid in cloud_llm.PROVIDERS:
            return pid
    for pid in order:
        provider = cloud_llm.PROVIDERS.get(pid)
        if provider is not None and not provider.requires_key:
            return pid
    return ""


def answering_model(config=None) -> str:
    """The model that actually answers, in words a panel can show.

    `config.model` is only an override: an empty value means "use whatever the
    hardware profile picked", so showing the setting alone left /status with
    a dash while a local Qwen answered. The same bounded choice the listing
    uses names the live engine instead, and never waits on a keyring.
    """
    cfg = _config(config)
    choice = bounded(lambda: llm_choice.local_first(_ollama(cfg), wake=False))
    if choice is NOT_CHECKED:
        return "not checked - the model servers did not answer quickly"
    _llm, how = choice
    if how == "ollama":
        return str(cfg.get("model", "") or getattr(_llm, "model", "") or "ollama")
    if how == "local":
        from shani_chronoa.hardware_profile import HardwareProfile

        return str(
            cfg.get("model", "")
            or local_llm.active()
            or HardwareProfile().get_model()
            or "local model"
        )
    return how


def list_models(config=None) -> dict:
    """``{"items": [{"engine","name","detail","installed","current"}], "how": str}``."""
    cfg = _config(config)
    ollama = _ollama(cfg)
    choice = bounded(lambda: llm_choice.local_first(ollama, wake=False))
    if choice is NOT_CHECKED:
        how = "not checked - the model servers did not answer quickly"
    else:
        _llm, how = choice

    items: list[dict] = []
    ids = bounded(lambda: _ollama_ids(ollama)) if how == "ollama" else []
    if ids is NOT_CHECKED:
        ids = []
        how = "Ollama not checked - it did not answer quickly"
    chosen = cfg.get("model", "") if hasattr(cfg, "get") else ""
    for index, name in enumerate(ids):
        current = how == "ollama" and name == (chosen or (ids[0] if ids else ""))
        items.append(
            {
                "engine": "ollama",
                "name": name,
                "detail": "Ollama",
                "installed": True,
                "current": current,
            }
        )

    installed = set(local_llm.installed())
    active = local_llm.answering_key() if how == "local" else local_llm.active()
    for spec, label, _order in local_llm.TIERS:
        items.append(
            {
                "engine": "local",
                "name": spec.key,
                "detail": str(label).split(" - ", 1)[0],
                "installed": spec.key in installed,
                "current": how == "local" and spec.key == active,
            }
        )

    keys = NOT_CHECKED
    if hasattr(cfg, "cloud_llm_api_keys"):
        keys = bounded(lambda: cfg.cloud_llm_api_keys())
    models = (
        bounded(lambda: cfg.cloud_llm_models())
        if hasattr(cfg, "cloud_llm_models")
        else {}
    )
    if models is NOT_CHECKED:
        models = {}
    current_cloud = _cloud_current(cfg, keys if keys is not NOT_CHECKED else {})
    for pid, provider in cloud_llm.PROVIDERS.items():
        if keys is NOT_CHECKED:
            ready = False
            key_note = " (key not checked - the keyring did not answer quickly)"
        else:
            ready = bool(keys.get(pid)) if provider.requires_key else True
            key_note = (
                " (needs an API key)" if provider.requires_key and not ready else ""
            )
        model = models.get(pid + "_model") or provider.default_model or "default"
        detail = provider.name + " - " + model + key_note
        items.append(
            {
                "engine": "cloud",
                "name": pid,
                "detail": detail,
                "installed": ready,
                "current": pid == current_cloud,
            }
        )
    return {"items": items, "how": how}


def use(name, *, confirm: bool = False, config=None) -> dict:
    """Choose an engine (``local``/``cloud``) or a listed model id."""
    cfg = _config(config)
    want = str(name or "").strip()
    if not want:
        return {"error": "no model named"}
    before_mode = cfg.get("answer-mode", "") if hasattr(cfg, "get") else ""

    if want == "local":
        cfg.set("answer-mode", "local")
        try:
            _llm, how = llm_choice.local_first(_ollama(cfg), wake=True)
        except Exception as exc:  # noqa: BLE001 - report the failed wake, not a lie
            how = "could not check the local model: %s" % type(exc).__name__
        return {
            "name": want,
            "engine": "local",
            "how": how,
            "changed": before_mode != "local",
        }

    if want == "cloud" or want in cloud_llm.PROVIDERS:
        if not confirm:
            return {
                "error": "confirmation required: cloud answers send prompts off this machine"
            }
        reason = _cloud_reason(cfg)
        if reason:
            return {"error": reason}
        provider = cloud_llm.PROVIDERS.get(want)
        if provider is not None and provider.requires_key:
            keys = (
                cfg.cloud_llm_api_keys() if hasattr(cfg, "cloud_llm_api_keys") else {}
            )
            if not keys.get(want):
                return {"error": "%s needs an API key" % want}
        # The wizard writes both: answer-mode alone does not open the gate.
        cfg.set("answer-mode", "cloud")
        cfg.set("cloud-fallback-enabled", "true")
        detail = "cloud via " + provider.name if provider is not None else "cloud"
        return {
            "name": want,
            "engine": "cloud",
            "how": detail,
            "changed": before_mode != "cloud",
        }

    if want in getattr(local_llm, "SPECS", {}):
        if want not in set(local_llm.installed()):
            return {"error": "%s is not installed; it is a download" % want}
        verdict = local_llm.use(want)
        if not verdict.get("promote"):
            return {
                "error": str(
                    verdict.get("why") or "the quality gate refused the switch"
                )
            }
        cfg.set("answer-mode", "local")
        return {
            "name": want,
            "engine": "local",
            "how": str(verdict.get("why") or "using " + want),
            "changed": True,
        }

    ollama = _ollama(cfg)
    if want in _ollama_ids(ollama):
        before = cfg.get("model", "") if hasattr(cfg, "get") else ""
        cfg.set("model", want)
        return {
            "name": want,
            "engine": "ollama",
            "how": "ollama",
            "changed": before != want,
        }

    return {"error": "unknown model: %s" % want}
