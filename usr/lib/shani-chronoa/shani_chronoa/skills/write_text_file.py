"""Skill: write a text file.

Overwriting is the dangerous part, so it is the part this skill is most careful
about. Writing is a *replacement* of content, which is easy to do by accident
when a model means to append.

Honesty rules:

- Refuses to overwrite a non-empty file unless `overwrite` is explicitly true,
  and says exactly how many bytes would be lost. A model that means to append
  and does not say so gets a refusal rather than a destroyed file.
- A successful write is verified by reading the size back, because "wrote it" is
  a claim and a file that is still the old size is not a success.
- Binary detection: this will not write into a file that already holds bytes
  that are not text, without `overwrite` set.
"""

from __future__ import annotations


from shani_chronoa import files
from shani_chronoa.skills import Skill
from shani_chronoa.skills.undo_last_change import record_preimage

_MAX_BYTES = 5 * 1024 * 1024

SCHEMA = {
    "type": "function",
    "function": {
        "name": "write_text_file",
        "description": (
            "Write text to a file, creating it or replacing its contents. "
            "Refuses to replace a non-empty file unless asked to overwrite, and "
            "refuses binary data."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file to write."},
                "content": {"type": "string", "description": "The full text to write."},
                "overwrite": {
                    "type": "boolean",
                    "description": (
                        "Required to replace a file that already has content. "
                        "Defaults to false."
                    ),
                },
                "append": {
                    "type": "boolean",
                    "description": "Add to the end instead of replacing. Defaults to false.",
                },
            },
        },
    },
}


def _run(arguments: dict) -> str:
    content = arguments.get("content")
    if content is None:
        return "No content was given, so there is nothing to write."
    if not isinstance(content, str):
        return f"Content must be text, not {type(content).__name__}."
    if len(content.encode("utf-8")) > _MAX_BYTES:
        return (
            f"That content is {files.human_size(len(content.encode('utf-8')))}, "
            f"over the {files.human_size(_MAX_BYTES)} limit for one write."
        )
    try:
        target = files.resolve(arguments.get("path") or "")
    except files.PathProblem as exc:
        return str(exc)
    if target.is_dir():
        return f"Could not write {target}: it is a directory."

    overwrite = bool(arguments.get("overwrite"))
    append = bool(arguments.get("append"))
    if append and overwrite:
        return "Give either append or overwrite, not both."

    existed = target.exists()
    if existed and not append and not overwrite:
        try:
            current = target.read_bytes()
        except OSError as exc:
            return files.describe(exc, target, "inspect")
        if current:
            if b"\0" in current[:4096]:
                return (
                    f"Refusing to write text over {target}: it already holds "
                    f"binary data ({files.human_size(len(current))}). Pass "
                    f"overwrite to replace it anyway, if that is really what you want."
                )
            return (
                f"Refusing to replace {target}: it already has "
                f"{files.human_size(len(current))} of content, which would be "
                f"lost. Pass overwrite to replace it, or append to add to the end."
            )

    note = ""
    if existed:
        # A replace or append of a file that already exists is undoable, as edit_file's changes are.
        try:
            note = record_preimage(target, target.read_bytes())
        except OSError:
            note = " (no undo point: the old contents could not be read)"
    mode = "a" if append else "w"
    try:
        with open(target, mode, encoding="utf-8") as handle:
            handle.write(content)
    except OSError as exc:
        return files.describe(exc, target, "write")

    try:
        size = target.stat().st_size
    except OSError as exc:
        return f"Wrote to {target} but could not check the result: {exc}"
    verb = "Appended to" if append else ("Replaced" if existed else "Created")
    return f"{verb} {target}: {files.human_size(size)}.{note if existed else ''}"



def _post_condition(arguments: dict):
    """The filesystem after the call, read with a fresh stat - not the skill's own report."""
    content = arguments.get("content")
    if not isinstance(content, str):
        return None
    try:
        target = files.resolve(arguments.get("path") or "")
        text = target.read_text(encoding="utf-8")
    except (files.PathProblem, OSError, UnicodeDecodeError) as exc:
        return False, f"could not read the file back: {exc}"
    ok = text.endswith(content) if arguments.get("append") else text == content
    return ok, f"{target} holds {len(text)} characters" + ("" if ok else ", not what was written")


# Declared for `verification.verify`; the LLM never supplies this.
POST_CONDITION = _post_condition


SKILLS = [Skill(name="write_text_file", schema=SCHEMA, run=_run)]
