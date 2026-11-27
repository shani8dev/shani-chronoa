"""Read-only inventory for a shell that draws Chronoa's panels itself.

The GTK app used to build each panel directly from the engine's stores. A
shell on another edition needs the same facts without importing GTK, so each
section here answers one of those panels with plain JSON. **Nothing in this
module writes, starts a model, or calls an actuator** - a shell panel is a
view, and a view that could change state would be a second, unaudited permission
path around the skill whitelist.

Every section is bounded and independently fallible: one store that cannot be
read returns `{"error": ...}` for its section, not a failed turn for every
other panel. Secret settings are never listed - `privacy` and `setup` name
consent keys and their state, never their values.
"""

from __future__ import annotations

import json
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

#: Hard cap on any one list. A panel is a summary, not a dump of the store.
LIMIT = 100


def _conversations(limit: int) -> list:
    from shani_chronoa import conversation_store

    return conversation_store.list_sessions(
        conversation_store.session_dir(), limit=limit
    )


def _models(limit: int) -> list:
    from shani_chronoa import model_choice
    from shani_chronoa.config import ChronoaConfig

    config = ChronoaConfig()
    return [
        {
            "task": task,
            "model": model_choice.resolve(task, config),
            "why": model_choice.explain(task, config),
        }
        for task in model_choice.tasks()[:limit]
    ]


def _skills(limit: int) -> list:
    from shani_chronoa import capabilities
    from shani_chronoa.skills import discover_skills

    tools, _handlers = discover_skills()
    out = []
    for schema in tools[:limit]:
        fn = schema.get("function", {}) if isinstance(schema, dict) else {}
        name = str(fn.get("name", ""))
        out.append(
            {
                "name": name,
                "description": fn.get("description", ""),
                "gate": capabilities.gated_by(name, str(fn.get("description", ""))),
            }
        )
    return out


def _routines(limit: int) -> list:
    from shani_chronoa import routines

    return [
        {"name": name, "phrases": routines.phrases(name, routine)}
        for name, routine in list(routines.load().items())[:limit]
    ]


def _triggers(limit: int) -> list:
    from shani_chronoa.triggers.rules import RuleStore

    return [
        {
            field: getattr(rule, field)
            for field in (
                "name",
                "sense",
                "match_mode",
                "substring",
                "keywords",
                "actuator",
                "arguments",
                "cooldown_seconds",
                "enabled",
                "allow_destructive",
                "last_fired_at",
            )
        }
        for rule in RuleStore().all()[:limit]
    ]


def _privacy(limit: int) -> dict:
    from shani_chronoa import egress
    from shani_chronoa.config import (
        ChronoaConfig,
        _SENSE_CONSENT_KEYS,
        granted_consent_key,
    )
    from shani_chronoa.senses import discover_senses

    config = ChronoaConfig()
    names = sorted(discover_senses())
    keys = dict(_SENSE_CONSENT_KEYS)
    return {
        "privacy_mode": config.privacy_mode,
        "granted_consent_key": granted_consent_key(),
        "senses": [
            {
                "name": name,
                "key": keys.get(name),
                "enabled": config.get_bool(keys[name], False) if name in keys else None,
            }
            for name in names[:limit]
        ],
        "recent_egress": len(egress.read_events(limit=limit)),
    }


def _memory(limit: int) -> list:
    from shani_chronoa.senses import memory

    store = memory.get_store()
    if store is None:
        return []
    return [
        {
            "content": p.content,
            "source": p.source,
            "created_at": p.created_at,
            "metadata": p.metadata,
        }
        for p in (store.durable(limit=limit) or [])
    ]


def _senses(limit: int) -> list:
    from shani_chronoa.config import ChronoaConfig, _SENSE_CONSENT_KEYS
    from shani_chronoa.senses import discover_senses

    config = ChronoaConfig()
    keys = dict(_SENSE_CONSENT_KEYS)
    return [
        {
            "name": name,
            "key": keys.get(name),
            "enabled": config.get_bool(keys[name], False) if name in keys else None,
        }
        for name in sorted(discover_senses())[:limit]
    ]


def _capabilities(limit: int) -> list:
    from shani_chronoa import capabilities

    items, error = capabilities.live_capabilities()
    if error:
        return [{"error": error}]
    return [
        {
            "tool": c.tool,
            "group": c.group,
            "label": c.label,
            "description": c.description,
            "consent_key": c.consent_key,
            "example": c.example,
        }
        for c in items[:limit]
    ]


def _status(limit: int) -> dict:
    """Which engines are ready, without starting any of them."""
    from shani_chronoa import local_llm, stt, tts
    from shani_chronoa.config import ChronoaConfig
    from shani_chronoa.engine_ops.models import answering_model

    config = ChronoaConfig()
    return {
        "llm_model": answering_model(config),
        "llm_installed": local_llm.installed(),
        "stt_available": stt.WhisperSTT().is_available(),
        "tts_engine": tts.PiperTTS().engine(),
        "privacy_mode": config.privacy_mode,
    }


_SECTIONS: dict[str, Callable[[int], object]] = {
    "conversations": _conversations,
    "models": _models,
    "skills": _skills,
    "routines": _routines,
    "triggers": _triggers,
    "privacy": _privacy,
    "memory": _memory,
    "senses": _senses,
    "capabilities": _capabilities,
    "status": _status,
}

#: The sections a shell may ask for. Fixed, so an unknown name is a refusal,
#: not an arbitrary call into the engine.
SECTIONS = tuple(sorted(_SECTIONS))


def describe(section: str, limit: Optional[int] = None) -> dict:
    """`{"section": name, "items": ...}` (or `{"error": ...}` on a failed read).

    `limit` is clamped to [1, LIMIT]; callers never get an unbounded dump.
    An unknown section is an error with the allowed names listed, never an
    empty success that a panel would render as "nothing here".
    """
    fn = _SECTIONS.get(str(section or ""))
    if fn is None:
        return {
            "section": section,
            "error": "unknown section",
            "allowed": list(SECTIONS),
        }
    try:
        cap = LIMIT if limit is None else max(1, min(int(limit), LIMIT))
        return {"section": section, "items": fn(cap)}
    except Exception as exc:  # noqa: BLE001 - one broken store is one panel's message
        logger.info("inventory section %s failed: %s", section, exc)
        return {"section": section, "error": f"{type(exc).__name__}: {exc}"}


def to_json(section: str, limit: Optional[int] = None) -> str:
    """The JSON string the D-Bus method returns."""
    return json.dumps(describe(section, limit), ensure_ascii=False, default=str)
