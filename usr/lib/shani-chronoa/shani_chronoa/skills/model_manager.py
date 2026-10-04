"""Model recommendation and installation, against the local Ollama.

`HardwareProfile.get_model()` picks a model from a two-way hardware tier and
never checks anything: not what is installed, not whether the answer fits, not
whether the model it names is even present. On a 31 GB machine it selects the
same ~2.5 GB `qwen3:4b` a 16 GB machine gets and says nothing.

This closes the loop from the other end. `recommend_model` answers "what would
actually suit this machine", sized against real *available* memory rather
than total. `install_model` pulls one, but only when the user has actually
asked for that model by name.

**Why installation is gated behind an explicit name.** Pulling a model writes
gigabytes to disk and needs the network. An assistant that decided on its own
which few gigabytes to fetch - or that treated "recommend something" as
"fetch something" - would be spending a real resource on a guess. So the two
actions are separate: recommend produces a list, install takes a name that came
from the user or from a recommendation the user agreed to, and there is no
path where one implies the other.

**The catalogue is a small, honest table, not a live registry.** These are
well-known Qwen3 and Llama dense sizes with roughly-known footprints. The table
is explicit that the sizes are approximate - a model that "fits" here means
"is the right order of magnitude for the available memory", and the real check
is loading it. Reporting a precise-looking number from a hardcoded table would
reproduce the very sin this module exists to fix.
"""

import json
import logging
import urllib.error
import urllib.request

from shani_chronoa.skills import Skill

from shani_chronoa.senses.modelfit import _HEADROOM_FRACTION, _meminfo, installed_models

logger = logging.getLogger(__name__)

_PULL_URL = "http://127.0.0.1:11434/api/pull"
_PULL_TIMEOUT = 1800.0  # a large model is a long download
_TAGS_TIMEOUT = 8.0

# (model, params, approx on-disk GB, tool-calling note, what it is good for)
# Sizes are the commonly published Q4_K_M-ish figures and are approximate by
# nature; the honest claim is order of magnitude, not bytes.
_CATALOGUE = [
    ("qwen3:1.7b", 1.7, 1.1, True, "fastest; the current low tier"),
    ("qwen3:4b", 4.0, 2.5, True, "the current default; reliable tool calls"),
    ("qwen3:8b", 8.2, 5.2, True, "noticeably better reasoning, still quick"),
    ("qwen3:14b", 14.8, 9.0, True, "strong general work; wants headroom"),
    ("qwen3:30b-a3b", 30.5, 18.0, True, "MoE: 30B quality at 3B active, slow to load"),
    ("llama3.1:8b", 8.0, 4.9, True, "solid general alternative"),
    ("llama3.3:70b", 70.6, 43.0, True, "best local quality; needs a big machine"),
]

SCHEMA = {
    "type": "function",
    "function": {
        "name": "recommend_model",
        "description": (
            "Recommend local language models that fit this machine's currently "
            "available memory, largest first, marking which support reliable "
            "tool calling. Reports available (not total) memory and the budget "
            "used. Does not download anything."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "How many suggestions to return. Default 4.",
                }
            },
        },
    },
}

INSTALL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "install_model",
        "description": (
            "Download a named model into the local Ollama. Takes an explicit "
            "model name and never picks one by itself - call recommend_model "
            "first and install only what the user agreed to. Warns rather "
            "than refuses when the model looks larger than available memory. "
            "This downloads gigabytes and needs the network."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Exact model name, e.g. qwen3:8b."},
            },
            "required": ["name"],
        },
    },
}


def _budget_mb() -> int:
    mem = _meminfo()
    return int(mem.get("available_mb", 0) * _HEADROOM_FRACTION)


def _fits(budget_mb: int, approx_gb: float) -> bool:
    return approx_gb * 1024 <= budget_mb


def recommend(arguments: dict) -> str:
    budget = _budget_mb()
    mem = _meminfo()
    try:
        limit = max(1, int(arguments.get("limit") or 4))
    except (TypeError, ValueError):
        limit = 4

    fits = [c for c in _CATALOGUE if _fits(budget, c[2])]
    lines = [
        f"{mem.get('available_mb', 0)} MB available of {mem.get('total_mb', 0)} MB total; "
        f"budget {budget} MB ({int(_HEADROOM_FRACTION * 100)}% of available)"
    ]
    if not fits:
        lines.append(
            f"No model in the catalogue fits that budget - even the smallest "
            f"needs ~1.1 GB and only {budget // 1024} GB is available. Free some "
            "memory, or use the cloud fallback, which is a separate opt-in."
        )
        return "\n".join(lines)

    lines.append(f"{len(fits)} of {len(_CATALOGUE)} catalogue models fit, largest first:")
    for name, params, approx, tools, note in fits[:limit]:
        lines.append(
            f"  {name} - {params}B, ~{approx} GB, "
            f"tool calling {'yes' if tools else 'NO'}: {note}"
        )
    lines.append(
        "Sizes are approximate published figures, not measured here; 'fits' means "
        "the right order of magnitude for the budget. The real test is loading it."
    )
    return "\n".join(lines)


def install(arguments: dict) -> str:
    name = str(arguments.get("name") or "").strip()
    if not name:
        return (
            "No model name given. This skill does not choose one for you - run "
            "recommend_model, and install only a model you have agreed to."
        )
    if not _plausible_name(name):
        return (
            f"{name!r} is not a valid Ollama model name. Expected something "
            "like qwen3:8b (name:tag, lowercase, digits, dashes, dots)."
        )

    existing = installed_models()
    if existing is not None:
        if any(m["name"] == name or m["name"].split(":")[0] == name.split(":")[0]
               for m in existing):
            return f"{name} is already installed. Nothing to do."

    known = next((c for c in _CATALOGUE if c[0].split(":")[0] == name.split(":")[0]), None)
    budget = _budget_mb()
    warning = ""
    if known and not _fits(budget, known[2]):
        warning = (
            f"WARNING: {name} is about {known[2]} GB and the budget is "
            f"{budget // 1024} GB. It may fail to load, or force swapping. "
            "Continuing anyway as asked.\n"
        )

    body = json.dumps({"model": name, "stream": False}).encode("utf-8")
    request = urllib.request.Request(
        _PULL_URL, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    logger.info("pulling model %s", name)
    try:
        with urllib.request.urlopen(request, timeout=_PULL_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return f"{warning}Could not download {name}: Ollama returned HTTP {exc.code} {exc.reason}. Nothing was installed."
    except (urllib.error.URLError, OSError) as exc:
        return (
            f"{warning}Could not reach Ollama at {_PULL_URL} ({exc}). "
            "Is the ollama service running? Nothing was installed."
        )
    except (ValueError, json.JSONDecodeError) as exc:
        return f"{warning}Ollama's reply for {name} was not readable JSON ({exc}). The download state is unknown."

    if payload.get("error"):
        return f"{warning}Ollama refused to pull {name}: {payload['error']}. Nothing was installed."

    return (
        f"{warning}Downloaded {name} into the local Ollama. "
        f"It will be used the next time the assistant selects it - the current "
        f"configured model is set separately and is not changed by installing."
    )


def _plausible_name(name: str) -> bool:
    """Reject anything that is not a bare Ollama model reference.

    The name goes into a JSON body, not a shell, so this is about not asking
    Ollama for something absurd rather than about injection - but a model name
    is the one thing here that a user or a model supplies freely, so it is
    worth a shape check.
    """
    if len(name) > 200 or "/" in name and name.count("/") > 2:
        return False
    base = name.split(":")[0]
    return bool(base) and all(
        c.islower() or c.isdigit() or c in "-._" for c in base
    )


_RECOMMEND = Skill(name="recommend_model", schema=SCHEMA, run=recommend)
_INSTALL = Skill(name="install_model", schema=INSTALL_SCHEMA, run=install)

SKILLS = [_RECOMMEND, _INSTALL]
