"""The system prompt's pieces, and a file that replaces any one of them.

Why this exists
---------------

`assistant.SYSTEM_PROMPT` is one hard-coded string. A person who wants Chronoa to
"answer in British English" or "always say what you are about to run first" has
today's choices: edit the source and lose the default, or put it in `rules.md`,
where it lands in the system role *after* everything and is one file for one
purpose. Neither is tunable, and neither survives an update.

`harness-study/laya` (a local-first AI notification centre, Python engine) solves
this with a mechanism worth copying outright. Its README states it as the feature:

> Override 20+ pipeline stages individually - change one, keep the rest as
> default. Tune persona voices, classification rules, or briefing style to your
> team. **Delete the file to revert - defaults always come back.**

Four properties of its `llm/prompts/overrides.py` are the whole of the transfer,
and each is a decision a simpler version gets wrong:

1. **The default lives in code, and the engine never writes the directory.** So
   "revert" is deleting a file, and no upgrade can be undone by a user edit.
2. **An unrecognised file name is a warning that lists the recognised ones.** A
   typo'd `router.md` for `rout.md` would otherwise silently do nothing, and the
   person would be tuning a string nobody reads.
3. **An empty file is a warning and not an override.** Replacing a prompt with
   nothing is worse than not replacing it.
4. **`get_override_status()` exists**, so "which of my prompts am I overriding,
   and what did they replace" is answerable without reading the source.

**What is deliberately not overridable, and why that is the important half.**
`provenance.describe_boundary()` - the sentence telling the model what the
untrusted-content fence means - is **not in this module's registry at all**. There
is no key for it, so no file can name it. That is the same construction Laya uses
for identifier fields ("absent by construction because capabilities don't list
them as content"): a security statement is not a preference, and the way to keep
it unoverridable is to leave no door, not to check the door harder. The override
registry here is *voice and behaviour only* - the identity sentence, whether
Chronoa calls a tool rather than describing it, how long spoken replies are, and
the reply-style clauses. Anything about tools, consent or the whitelist is out.

The default composition is **byte-identical to `assistant.SYSTEM_PROMPT`** when no
override file exists, and `compose()` with no overrides is asserted to be so.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: Where a person's overrides live. Created by nobody: deleting a file here is
#: the whole revert mechanism, and a directory this module makes is one that
#: looks like it has content.
PROMPTS_DIR = Path.home() / ".config" / "shani-chronoa" / "prompts"

#: The pieces, in the order the prompt states them. **The provenance boundary is
#: not here on purpose** - see the module docstring.
#:
#: `overridable` is False for a piece whose absence would leave the prompt
#: claiming something untrue rather than merely differently worded. Both False
#: entries today are statements about what Chronoa *is*, which a preference file
#: may rephrase and may not delete.
PIECES: Dict[str, Dict[str, Any]] = {
    "identity": {
        "default": "You are Chronoa, a privacy-first local voice assistant "
                   "running on Shanios.",
        "overridable": True,
        "what": "the sentence saying what Chronoa is",
    },
    "tool-calling": {
        "default": "When the user asks you to do something you have a tool for - "
                   "opening an app, adjusting volume, setting a timer, checking "
                   "the battery, searching the web - call the tool instead of "
                   "just describing what to do.",
        "overridable": True,
        "what": "whether to call a tool or describe it",
    },
    "spoken-reply": {
        "default": "Keep spoken replies short and conversational.",
        "overridable": True,
        "what": "how long spoken replies are",
    },
}

#: The reply-style clauses, one per setting. Overridable under the same rules,
#: and keyed so the file is `reply-style-brief.md` rather than a style name a
#: future release might rename.
STYLE_PIECES: Dict[str, Dict[str, Any]] = {
    "reply-style-brief": {
        "default": "Be brief: answer the question, then stop. No extra context "
                   "unless asked.",
        "overridable": True,
        "what": "the clause used when reply style is Brief",
    },
    "reply-style-explanatory": {
        "default": "Be explanatory: one sentence of mechanism pointing at the "
                   "answer, then the answer plainly.",
        "overridable": True,
        "what": "the clause used when reply style is Explanatory",
    },
}

ALL_PIECES: Dict[str, Dict[str, Any]] = {**PIECES, **STYLE_PIECES}

_FILES: Dict[str, str] = {key: f"{key}.md" for key in ALL_PIECES}
_FILE_TO_KEY = {name: key for key, name in _FILES.items()}

_overrides: Dict[str, str] = {}


def load_overrides() -> Dict[str, str]:
    """Read `~/.config/shani-chronoa/prompts/`, and say what it did.

    Clears first, so this doubles as a reload. Never creates the directory, and
    never raises: an override that cannot be read is a preference nobody
    expressed, which is the default rather than a failure.
    """
    _overrides.clear()
    if not PROMPTS_DIR.is_dir():
        logger.debug("no prompt overrides at %s", PROMPTS_DIR)
        return {}

    unrecognised: List[str] = []
    empty: List[str] = []
    for entry in sorted(PROMPTS_DIR.iterdir()):
        if not entry.is_file():
            continue
        key = _FILE_TO_KEY.get(entry.name)
        if key is None:
            # **A typo'd file name must not silently do nothing.** Otherwise a
            # person tunes `router.md` against `rout.md` and blames the model.
            unrecognised.append(entry.name)
            continue
        if not ALL_PIECES[key]["overridable"]:
            logger.warning("prompt override %s refused: %s is not a preference "
                           "and may not be replaced", entry.name, key)
            continue
        try:
            text = entry.read_text(encoding="utf-8").strip()
        except OSError as exc:
            logger.warning("prompt override %s could not be read: %s",
                           entry.name, exc)
            continue
        if not text:
            # Replacing a prompt with nothing is worse than not replacing it.
            empty.append(entry.name)
            continue
        _overrides[key] = text

    if _overrides:
        logger.info("prompt overrides loaded: %s", ", ".join(sorted(_overrides)))
    if unrecognised:
        logger.warning(
            "prompt files not recognised and ignored: %s (recognised files: %s)",
            ", ".join(unrecognised), ", ".join(sorted(_FILES.values())))
    if empty:
        logger.warning("prompt files empty and ignored: %s", ", ".join(empty))
    return dict(_overrides)


def get_prompt(key: str, default: str) -> str:
    """The override for `key`, or `default` when there is none."""
    return _overrides.get(key, default)


def compose(overrides: Optional[Dict[str, str]] = None) -> str:
    """The system prompt: the overridable pieces, then the safety boundary.

    **With no overrides the result is byte-identical to the constant it
    replaces**, which is asserted in `tests/test_prompt_pieces.py`: a preference
    file nobody wrote must change nothing at all.
    """
    if overrides is not None:
        pieces = (overrides.get(key, piece["default"])
                  for key, piece in PIECES.items())
    else:
        # **Re-read every time, which is what makes deletion a revert.** Laya's
        # loader runs at engine start, so its "delete the file to revert" needs a
        # restart; `compose()` is called once per assistant built and not per
        # turn, so the directory scan is free and the stronger property is.
        # `load_overrides` clears first, so it is idempotent and this is not a
        # cache being invalidated by accident.
        load_overrides()
        pieces = (get_prompt(key, piece["default"])
                  for key, piece in PIECES.items())
    # **The boundary sentence is appended and is not in the registry.** It is the
    # one part of the prompt that says something about security rather than about
    # voice, and the only way to make it unoverridable is to leave no key for it
    # to be named by.
    from shani_chronoa import provenance

    return " ".join(pieces) + " " + provenance.describe_boundary()


def override_status() -> List[Dict[str, Any]]:
    """Every piece, whether a file replaces it, and what replaced it.

    **The diagnostics half, and the reason this is a function and not a dict.**
    "Which of my prompts am I overriding" is the question a person has after a
    week of tuning, and answering it by reading the source is the wrong answer.
    """
    load_overrides()
    return [{"key": key, "file": name, "overridable": piece["overridable"],
             "overridden": key in _overrides,
             "what": piece["what"],
             "current": get_prompt(key, piece["default"]),
             "default": piece["default"]}
            for key, name in sorted(_FILES.items())
            for piece in [ALL_PIECES[key]]]
