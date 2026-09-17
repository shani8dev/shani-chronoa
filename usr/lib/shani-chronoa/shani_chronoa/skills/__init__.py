"""Skill (tool) loader for the assistant's tool-calling pipeline.

A "skill" is one local system action the LLM can invoke through Ollama's
`tools` parameter - see the top-level `tools.py` module docstring for why
this is a closed, whitelisted plugin contract and not a generic shell-exec
tool. Built-in skills ship as individual modules in this package; a user can
drop additional modules with the same contract into
`~/.config/shani-chronoa/skills/` and they're picked up at startup without
touching this codebase.

Skill module contract: each module defines a module-level `SKILLS` list of
`Skill(name, schema, run)` entries. `schema` is the full Ollama tool-call
schema dict (`{"type": "function", "function": {...}}`); `run` takes the
parsed arguments dict for that call and returns a short result string.
A module can register more than one `Skill` (e.g. volume.py groups
get/set/mute together since they share a `wpctl` helper).

Registration validation: every `Skill` entry is validated before it is
registered (see `is_valid_schema`). The name must be a non-empty string,
the schema must be a well-formed function schema whose `function.name`
matches the skill name, and `run` must be callable. Malformed entries are
skipped with a warning - a bad user module can never crash the app, the
MCP server, or the cloud fallback. A user skill with the same name as an
existing skill replaces that skill's schema and handler, so `TOOLS` always
holds exactly one schema per unique name.
"""

import importlib
import importlib.util
import logging
import os
from pathlib import Path
from typing import Callable, NamedTuple

logger = logging.getLogger(__name__)


class Skill(NamedTuple):
    name: str
    schema: dict
    run: Callable[[dict], str]


_USER_SKILLS_DIR = Path(os.path.expanduser("~/.config/shani-chronoa/skills"))


def is_valid_schema(schema: object) -> bool:
    """Return True if `schema` is a well-formed Ollama-style function schema.

    A valid schema is a dict with `type == "function"` and a `function`
    member that is a dict carrying a non-empty string `name` and, if
    present, a dict-shaped `parameters` (with dict `properties` and list
    `required`). This is the single validation used at registration time
    and by the MCP/cloud translators, so a malformed entry is skipped
    deterministically instead of crashing whichever consumer reads it.
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
        return function.get("name", "")
    return ""


def _register(module: object, source: str, tools: list, handlers: dict) -> None:
    skills = getattr(module, "SKILLS", None)
    if not isinstance(skills, (list, tuple)):
        logger.warning(f"Skipping '{source}': SKILLS must be a list of Skill entries")
        return
    for skill in skills:
        if not isinstance(skill, Skill):
            logger.warning(f"Skipping malformed skill entry in '{source}': not a Skill")
            continue
        name = skill.name
        if not isinstance(name, str) or not name.strip():
            logger.warning(f"Skipping malformed skill entry in '{source}': name must be a non-empty string")
            continue
        if not is_valid_schema(skill.schema):
            logger.warning(f"Skipping malformed skill entry in '{source}': schema for '{name}' is not a valid function schema")
            continue
        function_name = _schema_function_name(skill.schema)
        if function_name != name:
            logger.warning(
                f"Skipping malformed skill entry in '{source}': schema function name '{function_name}' "
                f"does not match skill name '{name}'"
            )
            continue
        if not callable(skill.run):
            logger.warning(f"Skipping malformed skill entry in '{source}': run for '{name}' is not callable")
            continue
        if name in handlers:
            logger.warning(
                f"Skill '{name}' from '{source}' overrides an existing skill of the same name; "
                f"replacing its schema and handler"
            )
            for i, existing in enumerate(tools):
                if _schema_function_name(existing) == name:
                    tools[i] = skill.schema
                    break
        else:
            tools.append(skill.schema)
        handlers[name] = skill.run


def _load_module_from_path(path: Path) -> object:
    spec = importlib.util.spec_from_file_location(f"shani_chronoa_user_skill_{path.stem}", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        logger.error(f"Failed to load skill '{path.name}': {e}")
        return None
    return module


def discover_skills() -> "tuple[list, dict]":
    """Load built-in skills, then any user-provided skills.

    Returns (tools, handlers): `tools` is the list of schema dicts to pass
    as Ollama's `tools` parameter; `handlers` maps tool name -> callable.
    A user skill with the same name as a built-in one replaces it: the
    prior schema is replaced in place and the handler is overwritten, so
    `tools` holds exactly one schema per unique name.
    """
    tools: list = []
    handlers: dict = {}

    pkg_dir = Path(__file__).parent
    for path in sorted(pkg_dir.glob("*.py")):
        if path.stem == "__init__":
            continue
        try:
            module = importlib.import_module(f"{__name__}.{path.stem}")
        except Exception as e:
            logger.error(f"Failed to load built-in skill '{path.stem}': {e}")
            continue
        _register(module, f"builtin:{path.stem}", tools, handlers)

    if _USER_SKILLS_DIR.is_dir():
        for path in sorted(_USER_SKILLS_DIR.glob("*.py")):
            module = _load_module_from_path(path)
            if module:
                _register(module, f"user:{path.name}", tools, handlers)

    return tools, handlers
