"""Declarative sandbox policy, auditable by eye and loadable from a file.

Chronoa's per-origin profiles are constructed in Python (`profiles.py`),
which is convenient for code and useless for review: a user asking "what
does the unattended profile stop" has to read source. OpenShell's policy
schema (`openshell-policy-schema/src/lib.rs:176-235`) is the reference: a
typed document, `deny_unknown_fields` so a typo cannot silently mean "allow
everything", and an absolute floor of a version field.

The schema below is strict the same way: an unknown key is an error, not a
warning. A policy file the loader cannot fully read is a policy the machine
refuses, and it says which key it did not understand. Named profiles keep the
Python constructors; this loader is for *user-edited* files under
`~/.config/shani-chronoa/sandbox/`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Union

from .models import SandboxConfig, SandboxLevel

_ALLOWED_KEYS = frozenset({
    "name", "level", "timeout_seconds", "blocked_binaries",
    "allow_network", "isolated_dir", "version",
})

_LEVELS = {level.name: level for level in SandboxLevel}
#: Level names are upper-case in code; a policy file may write them lower,
#: so both spellings resolve to the same level.
_LEVELS.update({level.name.lower(): level for level in SandboxLevel})


class PolicyError(ValueError):
    """The policy document is not loadable: unknown keys, bad types, or a
    level name that does not exist. Loud, not a guess."""


def config_from_policy(document: dict) -> SandboxConfig:
    """Build the sandbox config from a decoded policy document.

    Every key is validated: an unknown one is a `PolicyError` naming the key,
    because `deny_unknown_fields` is what stops a misspelled `"tiemout"` from
    silently falling back to the default timeout.
    """
    if not isinstance(document, dict):
        raise PolicyError(f"policy must be a JSON object, got {type(document).__name__}")
    unknown = set(document) - _ALLOWED_KEYS
    if unknown:
        raise PolicyError(f"unknown policy keys: {sorted(unknown)}")
    level_raw = document.get("level", SandboxLevel.LEVEL_3_HOST_USER.value)
    if level_raw not in _LEVELS:
        raise PolicyError(f"unknown sandbox level: {level_raw!r} "
                          f"(one of {sorted(_LEVELS)})")
    timeout = document.get("timeout_seconds", 30)
    if not isinstance(timeout, int) or timeout <= 0:
        raise PolicyError(f"timeout_seconds must be a positive integer, got {timeout!r}")
    blocked = document.get("blocked_binaries", [])
    if not isinstance(blocked, list) or not all(isinstance(b, str) for b in blocked):
        raise PolicyError("blocked_binaries must be a list of strings")
    allow_network = document.get("allow_network")
    if allow_network is not None and not isinstance(allow_network, bool):
        raise PolicyError("allow_network must be a boolean")
    isolated_dir = document.get("isolated_dir")
    if isolated_dir is not None and not isinstance(isolated_dir, str):
        raise PolicyError("isolated_dir must be a path string")
    return SandboxConfig(
        level=_LEVELS[level_raw],
        timeout_seconds=timeout,
        blocked_binaries=list(blocked),
        allow_network=allow_network,
        isolated_dir=isolated_dir,
    )


def load_policy(path: Union[str, Path]) -> SandboxConfig:
    """Read a policy JSON file and return its config.

    A file that cannot be parsed is a PolicyError, not a crash: the caller
    decides, and the message says why the file was refused.
    """
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PolicyError(f"cannot load policy {path}: {exc}") from exc
    return config_from_policy(document)
