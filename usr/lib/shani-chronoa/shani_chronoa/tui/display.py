"""Plain words for capability results: the client owns the sentence.

The engine returns stable keys and codes; this module turns them into lines a
person reads. No D-Bus, no decisions, no secrets printed - a secret row is a
presence (``<set>``), never a value.
"""

from __future__ import annotations

import sys
from typing import Optional, TextIO

#: Codes and common row keys that would read badly with an underscore in them.
LABELS = {
    "tasks": "Model for each task",
    "needs_setup": "Setup needed",
    "llm_model": "Answering model",
    "llm_installed": "Local model installed",
    "stt_available": "Speech input available",
    "tts_engine": "Speech engine",
    "privacy_mode": "Privacy mode",
    "current": "Answering now",
    "installed": "Installed",
    "proposals": "Proposals waiting",
    "facts": "Remembered facts",
    "how": "How",
    "ok": "Done",
}

TITLE_KEYS = ("title", "name", "label", "task", "tool", "model", "engine", "id")


def label(key: str) -> str:
    """A readable name for a backend key, without changing the key itself."""
    return LABELS.get(key, key.replace("_", " ").capitalize())


def _scalar(value) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if value is None:
        return "-"
    if value == "":
        return "-"
    return str(value)


def _row(item) -> str:
    if not isinstance(item, dict):
        return f"  - {_scalar(item)}"
    title_key = next((k for k in TITLE_KEYS if item.get(k) not in (None, "")), None)
    title = _scalar(item[title_key]) if title_key else "item"
    rest = ", ".join(
        f"{label(k)}: {_scalar(v)}"
        for k, v in item.items()
        if k != title_key
        and not isinstance(v, (dict, list))
        and not k.endswith("api-key")
    )
    return f"  - {title}{': ' + rest if rest else ''}"


def with_tasks(client, method: str, data):
    """/models and `chronoa models` also say which model does each task.

    The engine list (ModelsList) replaced the old /models, which showed
    Inventory("models"): the model each task uses. Both belong under one
    command, so the second is added to the first as `tasks`.
    """
    if method != "ModelsList" or not isinstance(data, dict) or data.get("error"):
        return data
    try:
        tasks = client.inventory("models")
    except Exception:  # noqa: BLE001 - the engine list still stands on its own
        return data
    items = tasks.get("items") if isinstance(tasks, dict) else None
    if isinstance(items, list):
        data = dict(data)
        data["tasks"] = items
    return data


def method_title(method: str) -> str:
    """"ModelsList" -> "Models": the words of a method name, without its verb."""
    import re

    words = re.findall(r"[A-Z][a-z0-9]*", method or "") or [method or ""]
    if len(words) > 1 and words[-1] in ("List", "Get", "Read", "Status", "Show"):
        words = words[:-1]
    return " ".join(words).capitalize() or label(method)


def lines(data: object, method: str = "") -> list[str]:
    """The human-readable lines for one capability result."""
    if isinstance(data, list):
        return [f"{label(method)} ({len(data)}):", *(_row(i) for i in data[:30])]
    if not isinstance(data, dict):
        return [f"{label(method)}: {_scalar(data)}"]
    if data.get("error"):
        return [f"error: {_scalar(data['error'])}"]

    out: list[str] = []
    for key, value in data.items():
        if key in ("value", "password") and data.get("secret") is True:
            continue
        if isinstance(value, list):
            # "items" says nothing: the command's own name heads its list
            heading = method_title(method) if key == "items" and method else label(key)
            out.append(f"{heading} ({len(value)}):")
            out.extend(_row(item) for item in value[:30])
        elif isinstance(value, dict):
            out.append(f"{label(key)}:")
            for subkey, subvalue in value.items():
                if isinstance(subvalue, (list, tuple)):
                    subvalue = ", ".join(str(v) for v in subvalue) or "none"
                out.append(f"  {label(subkey)}: {_scalar(subvalue)}")
        else:
            out.append(f"{label(key)}: {_scalar(value)}")
    return out or [f"{label(method)}: nothing here"]


def show(data: object, stream: Optional[TextIO] = None, method: str = "") -> None:
    """Print the result as plain words; one line per fact."""
    out = stream if stream is not None else sys.stdout
    for line in lines(data, method):
        print(line, file=out)
