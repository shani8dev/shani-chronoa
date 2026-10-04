"""Skill: read, create and edit Word, Excel and PowerPoint files (and read their OpenDocument forms).

The work is in `shani_chronoa.office` (standard library only - see there for
what is and is not supported). This is the boundary: path checks, the
"don't replace a file that exists" rule `write_text_file` already follows,
the `file-edit-enabled` consent for changing a file the user already has, a
pre-image in the undo ring before every change (so `undo_last_change` puts a
.docx or .xlsx back exactly), and a read-back after every write.

Creating a *new* file needs no consent, as with `write_text_file`: nothing
the user had is touched. Replacing or editing one does.
"""

from __future__ import annotations

import json
import shutil
import subprocess

from shani_chronoa import files, office
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill
from shani_chronoa.skills.undo_last_change import record_preimage

_CONSENT_KEY = "file-edit-enabled"
_ACTIONS = ("read", "create", "set_cells", "replace_text", "append", "pdf_images")
MAX_CHARS = 12000

SCHEMA = {
    "type": "function",
    "function": {
        "name": "office_document",
        "description": (
            "Word (.docx), Excel (.xlsx) and PowerPoint (.pptx) files; also reads .odt/.ods/.odp. "
            "read: text with headings, tables, one section per sheet or slide (sheet, cell_range like A1:D20, "
            "formulas=true to see formulas). create: a new file from content - for .docx Markdown-style text "
            "(# headings, - bullets, 1. lists, | tables |, **bold**); for .xlsx rows (list of lists), or sheets "
            "{name: rows}, or CSV/Markdown-table text in content, '=...' cells are formulas; for .pptx slides "
            "[{title, bullets[], subtitle}]. set_cells: values {'B12': 42, 'C1': 'Total'} in a sheet. "
            "replace_text / append: change a .docx. pdf_images: save a PDF's embedded pictures to a folder. "
            "Changing an existing file needs 'file-edit-enabled' and can be undone with undo_last_change."
        ),
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": list(_ACTIONS)},
            "path": {"type": "string", "description": "e.g. '~/Documents/budget.xlsx'"},
            "content": {"type": "string", "description": "create (.docx or table text) / append: the text."},
            "rows": {"type": "array", "items": {"type": "array", "items": {}}},
            "sheets": {"type": "object", "description": "create .xlsx: {sheet name: rows}."},
            "slides": {"type": "array", "items": {"type": "object"}},
            "values": {"type": "object", "description": "set_cells: {cell: value}."},
            "sheet": {"type": "string"},
            "cell_range": {"type": "string"},
            "formulas": {"type": "boolean"},
            "find": {"type": "string"},
            "replace": {"type": "string"},
            "title": {"type": "string"},
            "overwrite": {"type": "boolean", "description": "create: replace an existing file (needs file-edit-enabled)."},
            "output_dir": {"type": "string", "description": "pdf_images: where to save the pictures."},
        }, "required": ["action", "path"]},
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    if not config.get_bool(_CONSENT_KEY, False):
        return False, f"changing an existing file is turned off (enable '{_CONSENT_KEY}' in Settings)"
    return True, ""


def _maybe_json(value):
    if isinstance(value, str) and value.strip()[:1] in "[{":
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _write(target, data: bytes, existed: bool) -> str:
    note = ""
    if existed:
        note = record_preimage(target, target.read_bytes())
    tmp = target.with_name(f".{target.name}.chronoa-tmp")
    tmp.write_bytes(data)
    tmp.replace(target)
    return note


def _summary(target) -> str:
    text = office.read(target)
    return text if len(text) <= 600 else text[:600] + " ..."


def _pdf_images(arguments: dict) -> str:
    try:
        source = files.resolve(arguments.get("path") or "")
        out_dir = files.resolve_in_home(arguments.get("output_dir") or str(source.with_suffix("")) + "-images")
    except files.PathProblem as exc:
        return str(exc)
    if source.suffix.lower() != ".pdf" or not source.is_file():
        return f"{source} is not a PDF file."
    if shutil.which("pdfimages") is None:
        return "pdfimages (poppler) is not installed."
    out_dir.mkdir(parents=True, exist_ok=True)
    if any(out_dir.iterdir()):
        return f"{out_dir} is not empty; give an empty or new folder."
    proc = subprocess.run(["pdfimages", "-png", str(source), str(out_dir / "image")],
                          capture_output=True, text=True, timeout=120, check=False)
    saved = sorted(out_dir.glob("image-*.png"))
    if proc.returncode != 0:
        return f"pdfimages failed: {(proc.stderr or '').strip()[:200]}"
    return f"Saved {len(saved)} picture(s) from {source.name} to {out_dir}." if saved else \
        f"{source.name} has no embedded pictures (text and drawings are not pictures)."


def _run(arguments: dict) -> str:
    action = (arguments.get("action") or "").strip().lower()
    if action not in _ACTIONS:
        return f"action must be one of {', '.join(_ACTIONS)}."
    if action == "pdf_images":
        return _pdf_images(arguments)
    try:
        # Reading may look anywhere a person can; writing stays inside home, as edit_file and undo do.
        target = (files.resolve(arguments.get("path") or "") if action == "read"
                  else files.resolve_in_home(arguments.get("path") or ""))
        if action != "read":
            files.refuse_catalogue(target, "change")
    except files.PathProblem as exc:
        return str(exc)
    ext = target.suffix.lower()
    try:
        if action == "read":
            if not target.is_file():
                return f"{target} does not exist."
            text = office.read(target, arguments.get("sheet") or "", arguments.get("cell_range") or "",
                               bool(arguments.get("formulas")))
            if len(text) > MAX_CHARS:
                text = text[:MAX_CHARS] + f"\n... (cut at {MAX_CHARS} characters; ask for a sheet, a cell_range, or a part)"
            return text or f"{target.name} has no text."

        if action == "create":
            if ext not in office.WRITABLE:
                return f"create makes {', '.join(office.WRITABLE)} files, not {ext or 'that'}."
            existed = target.exists()
            if existed:
                if not arguments.get("overwrite"):
                    return f"{target} already exists; give another name, or overwrite=true to replace it."
                allowed, reason = _consent(ChronoaConfig())
                if not allowed:
                    return f"Refusing to replace {target.name}: {reason}."
            title = arguments.get("title") or target.stem
            if ext == ".docx":
                content = arguments.get("content") or ""
                if not content.strip():
                    return "Give the document's text in content."
                data = office.make_docx(content, title)
            elif ext == ".xlsx":
                sheets = _maybe_json(arguments.get("sheets"))
                rows = _maybe_json(arguments.get("rows"))
                if isinstance(sheets, dict) and sheets:
                    data = office.make_xlsx(sheets)
                elif isinstance(rows, list) and rows:
                    data = office.make_xlsx({arguments.get("sheet") or "Sheet1": rows})
                elif (arguments.get("content") or "").strip():
                    data = office.make_xlsx({arguments.get("sheet") or "Sheet1":
                                             office.rows_from_text(arguments["content"])})
                else:
                    return "Give rows, sheets, or table text in content."
            else:
                slides = _maybe_json(arguments.get("slides"))
                if not isinstance(slides, list) or not slides:
                    return "Give slides as a list of {title, bullets}."
                data = office.make_pptx(slides, title)
            target.parent.mkdir(parents=True, exist_ok=True)
            note = _write(target, data, existed)
            return f"{'Replaced' if existed else 'Created'} {target}. It reads back as:\n{_summary(target)}{note}"

        # the edits: an existing file, with consent and a pre-image
        if not target.is_file():
            return f"{target} does not exist."
        allowed, reason = _consent(ChronoaConfig())
        if not allowed:
            return f"Refusing to change {target.name}: {reason}."
        if action == "set_cells":
            if ext != ".xlsx":
                return "set_cells works on .xlsx files."
            values = _maybe_json(arguments.get("values"))
            if not isinstance(values, dict) or not values:
                return "Give values as {cell: value}, e.g. {'B12': 42}."
            data = office.set_cells(target, arguments.get("sheet") or "", values)
            note = _write(target, data, True)
            sheet = arguments.get("sheet") or ""
            got = office.read_xlsx_rows(target, sheet, formulas=True)
            rows = next(iter(got.values()))
            checks = []
            for ref in values:
                r, c = office.parse_ref(ref)
                checks.append(f"{ref.upper()}={rows[r - 1][c] if r <= len(rows) and c < len(rows[r - 1]) else ''}")
            return f"Updated {target.name}: {', '.join(checks)} (read back).{note}"
        if ext != ".docx":
            return f"{action} works on .docx files."
        if action == "replace_text":
            data, count = office.docx_replace(target, arguments.get("find") or "", arguments.get("replace") or "")
            if not count:
                return f"{arguments.get('find')!r} does not appear in {target.name}; nothing changed."
            note = _write(target, data, True)
            return f"Replaced {count} occurrence(s) in {target.name}.{note}"
        content = arguments.get("content") or ""
        if not content.strip():
            return "Give the text to append in content."
        note = _write(target, office.docx_append(target, content), True)
        return f"Appended to {target.name}. It now ends with:\n{office.read(target)[-400:]}{note}"
    except office.OfficeError as exc:
        return f"Could not do that: {exc}."


def _post_condition(arguments: dict):
    action = (arguments.get("action") or "").lower()
    if action not in ("create", "set_cells", "replace_text", "append"):
        return None
    try:
        target = files.resolve(arguments.get("path") or "")
        text = office.read(target, formulas=True)
    except (files.PathProblem, office.OfficeError, OSError):
        return None
    if action == "replace_text":
        want = arguments.get("replace") or ""
        if not want:
            return None
        return want in text, f"{target.name} reads back with the new text" if want in text else f"{target.name} lacks it"
    return bool(text), f"{target.name} opens and reads back ({len(text)} characters)"


POST_CONDITION = _post_condition

SKILLS = [Skill(name="office_document", schema=SCHEMA, run=_run)]
