"""Which model Chronoa uses for each job, in one queryable place.

The idea is borrowed from Arch's `llm-manager` — a task->model lookup so
another program can ask "the model configured for this job" instead of
hardcoding one — but the shape here is Chronoa's own, and deliberately so.

`llm-manager` keeps a flat `~/.config/llm-manager/llm.conf` of task->model
strings. Chronoa does not need a second configuration file, because it
**already has per-task pins in its own settings layer**, each falling back to
its own hardware tier:

    text        `model`            -> HardwareProfile.get_model()
    vision      `vision-model`     -> HardwareProfile.get_vision_model()
    transcribe  (no pin)           -> HardwareProfile.get_whisper_model()

Those three exist because they are three genuinely different jobs with
genuinely different requirements, not because a generic registry wanted three
rows. A text model that cannot call functions reliably is not a candidate for
this assistant no matter how good its prose is, and a vision model that is
merely adequate on a 2 GB budget will fail outright on a screen full of text -
which is exactly what the two tier methods already encode, and what the
existing `vision-model` gsetting exists to override.

So the contribution here is the *lookup*, not a new source of truth. Anything
that adds a fourth job with its own model adds a fourth pin here, and the
fallback chain stays visible rather than being re-derived per call site.
"""

import logging
from typing import Callable, Dict, List, Optional, Tuple

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.hardware_profile import HardwareProfile

logger = logging.getLogger(__name__)

# task -> (gsettings key or None, HardwareProfile method, what the job is)
_TASKS: Dict[str, Tuple[Optional[str], str, str]] = {
    "text": ("model", "get_model",
             "chat and tool calling - this is the assistant itself"),
    "vision": ("vision-model", "get_vision_model",
               "describing what is on screen; needs a vision-capable model"),
    "transcribe": (None, "get_whisper_model",
                   "speech to text; a Whisper model, not a text model"),
}

# Whisper is not an Ollama model, so recommending or pulling it through the
# model-manager path would be wrong.
NON_OLLAMA_TASKS = frozenset({"transcribe"})


def tasks() -> List[str]:
    """Every task that can be resolved, sorted."""
    return sorted(_TASKS)


def describe(task: str) -> Optional[str]:
    """What a task is for, or None if the task is unknown."""
    entry = _TASKS.get(task)
    return entry[2] if entry else None


def resolve(task: str, config: Optional[ChronoaConfig] = None) -> Optional[str]:
    """The model Chronoa will use for `task`, and where that answer came from.

    Returns None for an unknown task rather than guessing: a typo in a task
    name should say so, not quietly return the chat model.

    The three-step order is deliberate and is the whole point of this module.
    A user who set a pin meant it, so the pin wins; failing that, the hardware
    tier's judgement applies; only if both are absent is the answer unknown.
    """
    entry = _TASKS.get(task)
    if entry is None:
        return None
    key, method, _purpose = entry

    if config is not None and key is not None:
        pinned = str(config.get(key, "") or "").strip()
        if pinned:
            return pinned

    profile = HardwareProfile()
    getter: Callable[[], str] = getattr(profile, method, None)
    if getter is None:
        logger.debug("no hardware tier method %r for task %r", method, task)
        return None
    chosen = str(getter() or "").strip()
    return chosen or None


def explain(task: str, config: Optional[ChronoaConfig] = None) -> str:
    """A human-readable answer for `task`, naming the source of the answer.

    The source matters as much as the model: "you set this" and "the hardware
    tier guessed this" call for different reactions when it turns out to be
    wrong, and collapsing them is how a bad default becomes invisible.
    """
    entry = _TASKS.get(task)
    if entry is None:
        return f"{task!r} is not a task Chronoa resolves a model for. Known: {', '.join(tasks())}."

    key, method, purpose = entry
    if config is None:
        config = ChronoaConfig()

    lines = [f"{task}: {purpose}"]
    if key is not None:
        pinned = str(config.get(key, "") or "").strip()
        if pinned:
            lines.append(f"  model: {pinned} (your 'model' setting, which wins)")
            return "\n".join(lines)
        lines.append(f"  model setting '{key}' is empty, so the hardware tier decides")

    chosen = resolve(task, config=config)
    if chosen is None:
        lines.append("  model: UNKNOWN - no pin and no tier default")
        return "\n".join(lines)

    if task in NON_OLLAMA_TASKS:
        lines.append(
            f"  model: {chosen} (hardware tier {method} - note this is a "
            "Whisper model, not an Ollama model, so it is not installed with "
            "`ollama pull`)"
        )
    else:
        lines.append(f"  model: {chosen} (hardware tier {method})")
    return "\n".join(lines)
