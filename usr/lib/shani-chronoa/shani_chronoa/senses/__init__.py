"""Sense (perception) contract and loader for the assistant.

A "skill" (see `skills/__init__.py`) is an *action*: the LLM decides to
invoke it, it returns a short result string, and the whole interaction is
discrete and user-requested. A "sense" is a *perception*: it produces a
`Percept` - a timestamped observation carrying its own lifetime, provenance
and sensitivity - which the assistant carries into the LLM's context.

Why this is a separate contract rather than another skill:

- **Lifetime.** A screen percept is worthless thirty seconds later; a memory
  percept is durable. Skills have no lifetime concept at all because their
  output is consumed immediately and discarded. `Percept.ttl_seconds`
  expresses this, so one type serves both a transient screen grab and a
  durable remembered fact.
- **Provenance and sensitivity.** A percept records *where it came from*
  (`source`) and *how private it is* (`sensitivity`). That is what lets
  `ContextBuilder` budget and redact percepts, and what makes an ambient
  sense auditable after the fact. A bare result string carries none of this.
- **Ambient as a mode, not a rewrite.** A sense that declares a
  `poll_interval` can also be polled on a schedule; one that declares `None`
  is reactive-only. Both modes produce the same `Percept` and flow through
  the same context path, so adding ambient polling later is a scheduler
  rather than a rewrite. Building ambient-first overbuilds; building
  reactive-only paints us into a rewrite.
- **Consent.** Every sense is gated by its own GSettings key *and* by
  `privacy-mode`. That per-sense surface is the point: `shani-insights`
  advertises "No OCR" as a product property, so "which perceptions may this
  assistant form?" has to be answerable per sense, not as one global switch.

Registration validation mirrors the skills loader exactly: every `Sense` is
validated before registration, and a malformed entry - from a builtin or a
user drop-in - is skipped with a warning. A bad user module can never crash
the app, the CLI, the MCP server, or the cloud fallback.

Percepts deliberately do NOT live in `Assistant._history`. They are
reassembled fresh each turn by `ContextBuilder` (see `senses/context.py`),
which is what keeps them out of `MAX_HISTORY_MESSAGES` and out of
`_trim_history()`'s turn-grouping logic entirely.
"""

import importlib
import importlib.util
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, NamedTuple, Optional, Union

logger = logging.getLogger(__name__)

# Sensitivity tiers, ordered least to most private. `ContextBuilder` uses
# these to decide what may be sent when a cloud fallback is active, and to
# rank percepts when the context budget is tight.
SENSITIVITY_PUBLIC = "public"
SENSITIVITY_PERSONAL = "personal"
SENSITIVITY_PRIVATE = "private"

_VALID_SENSITIVITY = frozenset(
    (SENSITIVITY_PUBLIC, SENSITIVITY_PERSONAL, SENSITIVITY_PRIVATE)
)


@dataclass(frozen=True)
class Percept:
    """A single observation, with its own lifetime and provenance.

    `ttl_seconds` is the load-bearing field. `None` means durable (memory);
    a number means the percept is stale that many seconds after
    `created_at` and `PerceptStore` will drop it on read. A screen capture
    and a remembered preference are the same type with different lifetimes,
    which is what lets one context path serve both.
    """

    sense: str
    kind: str
    content: str
    created_at: float
    ttl_seconds: Optional[float] = None
    source: str = ""
    sensitivity: str = SENSITIVITY_PUBLIC
    metadata: Optional[dict] = None

    def is_expired(self, now: Optional[float] = None) -> bool:
        """True once this percept is older than its TTL.

        Durable percepts (`ttl_seconds is None`) never expire.
        """
        if self.ttl_seconds is None:
            return False
        current = time.time() if now is None else now
        return (current - self.created_at) > self.ttl_seconds

    def age_seconds(self, now: Optional[float] = None) -> float:
        """Seconds since this percept was created."""
        current = time.time() if now is None else now
        return max(0.0, current - self.created_at)


class Sense(NamedTuple):
    """One perception capability.

    `run` takes the parsed arguments dict and returns either a `Percept` or
    a plain string. Returning a string is a convenience: the loader wraps it
    in a Percept carrying this sense's declared `kind`, `ttl_seconds` and
    `sensitivity`, so a simple sense does not have to restate them. Return a
    Percept directly when the perception needs its own per-call lifetime or
    provenance - e.g. OCR of one specific file should record that file as
    its `source`, not a generic "the filesystem".
    """

    name: str
    kind: str
    ttl_seconds: Optional[float]
    sensitivity: str
    schema: dict
    run: Callable[[dict], Union[str, Percept]]
    poll_interval: Optional[float] = None

    def is_ambient(self) -> bool:
        """True if this sense may be polled on a schedule."""
        return self.poll_interval is not None

    def to_percept(
        self, content: str, source: str = "", metadata: Optional[dict] = None
    ) -> Percept:
        """Wrap plain text in a Percept carrying this sense's declared shape."""
        return Percept(
            sense=self.name,
            kind=self.kind,
            content=content,
            created_at=time.time(),
            ttl_seconds=self.ttl_seconds,
            source=source,
            sensitivity=self.sensitivity,
            metadata=metadata,
        )


_USER_SENSES_DIR = Path(os.path.expanduser("~/.config/shani-chronoa/senses"))


def is_valid_schema(schema: object) -> bool:
    """Return True if `schema` is a well-formed Ollama-style function schema.

    Deliberately the same validation `skills.is_valid_schema` performs, kept
    as a separate function rather than imported: senses and skills are
    independent registries that a user can extend separately, and coupling
    them would make a change to one contract a breaking change to the other.
    """
    if not isinstance(schema, dict):
        return False
    if schema.get("type") != "function":
        return False
    function = schema.get("function")
    if not isinstance(function, dict):
        return False
    name = function.get("name")
    if not isinstance(name, str) or not name.strip():
        return False
    parameters = function.get("parameters")
    if parameters is not None and not isinstance(parameters, dict):
        return False
    if isinstance(parameters, dict):
        properties = parameters.get("properties")
        if properties is not None and not isinstance(properties, dict):
            return False
        required = parameters.get("required")
        if required is not None and not isinstance(required, list):
            return False
    return True


def _schema_function_name(schema: dict) -> str:
    """Return the function name a schema advertises, or '' if it has none."""
    function = schema.get("function")
    if isinstance(function, dict):
        name = function.get("name", "")
        return name if isinstance(name, str) else ""
    return ""


def _sense_problem(sense: object) -> str:
    """Return why `sense` is malformed, or '' if it is acceptable.

    Every rejection carries an explicit reason so a user who drops a broken
    sense into `~/.config/shani-chronoa/senses/` can tell what to fix from
    the startup log, rather than seeing a silent absence.
    """
    if not isinstance(sense, Sense):
        return "not a Sense"
    if not isinstance(sense.name, str) or not sense.name.strip():
        return "name must be a non-empty string"
    if not isinstance(sense.kind, str) or not sense.kind.strip():
        return "kind must be a non-empty string"
    if sense.sensitivity not in _VALID_SENSITIVITY:
        return (
            f"sensitivity must be one of {sorted(_VALID_SENSITIVITY)}, "
            f"got {sense.sensitivity!r}"
        )
    if sense.ttl_seconds is not None and (
        not isinstance(sense.ttl_seconds, (int, float))
        or isinstance(sense.ttl_seconds, bool)
        or sense.ttl_seconds <= 0
    ):
        return "ttl_seconds must be a positive number, or None for durable"
    if sense.poll_interval is not None and (
        not isinstance(sense.poll_interval, (int, float))
        or isinstance(sense.poll_interval, bool)
        or sense.poll_interval <= 0
    ):
        return "poll_interval must be a positive number, or None for reactive-only"
    if not is_valid_schema(sense.schema):
        return "schema is not a valid function schema"
    if _schema_function_name(sense.schema) != sense.name:
        return (
            f"schema function name {_schema_function_name(sense.schema)!r} "
            f"does not match sense name {sense.name!r}"
        )
    if not callable(sense.run):
        return "run is not callable"
    return ""


def _register(module: object, source: str, senses: dict) -> None:
    entries = getattr(module, "SENSES", None)
    if entries is None:
        if source.startswith("builtin:"):
            # store/context are library modules in this package, not sense
            # modules. Skipping them silently keeps a correct package from
            # logging on every startup; a user module lacking SENSES still
            # warns, because there that is a real mistake.
            return
        logger.warning(f"Skipping '{source}': no SENSES list (is it a sense module?)")
        return
    if not isinstance(entries, (list, tuple)):
        logger.warning(f"Skipping '{source}': SENSES must be a list of Sense entries")
        return
    for sense in entries:
        problem = _sense_problem(sense)
        if problem:
            name = getattr(sense, "name", "<unnamed>")
            logger.warning(f"Skipping malformed sense '{name}' from '{source}': {problem}")
            continue
        if sense.name in senses:
            logger.warning(
                f"Sense '{sense.name}' from '{source}' overrides an existing sense of "
                f"the same name; replacing its declaration and handler"
            )
        senses[sense.name] = sense


def _load_module_from_path(path: Path) -> object:
    spec = importlib.util.spec_from_file_location(
        f"shani_chronoa_user_sense_{path.stem}", path
    )
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:  # noqa: BLE001 - a bad drop-in must not be fatal
        logger.error(f"Failed to load sense '{path.name}': {e}")
        return None
    return module


def discover_senses() -> "dict[str, Sense]":
    """Load builtin senses, then any user-provided senses.

    Returns a mapping of sense name -> `Sense`. A user sense with the same
    name as a builtin replaces it, so the registry holds exactly one
    declaration per unique name.
    """
    found: dict[str, Sense] = {}

    pkg_dir = Path(__file__).parent
    for path in sorted(pkg_dir.glob("*.py")):
        if path.stem == "__init__":
            continue
        try:
            module = importlib.import_module(f"{__name__}.{path.stem}")
        except Exception as e:  # noqa: BLE001 - one broken sense must not be fatal
            logger.error(f"Failed to load builtin sense '{path.stem}': {e}")
            continue
        _register(module, f"builtin:{path.stem}", found)

    if _USER_SENSES_DIR.is_dir():
        for path in sorted(_USER_SENSES_DIR.glob("*.py")):
            module = _load_module_from_path(path)
            if module:
                _register(module, f"user:{path.name}", found)

    return found
