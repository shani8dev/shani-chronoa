"""Reading .docx, .xlsx, .pptx, .odt, .ods and .odp into light Markdown text (and spreadsheet rows)."""

from __future__ import annotations


import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .common import (  # noqa: F401
    A,
    DRAW,
    LEGACY,
    OFFICE,
    OfficeError,
    P,
    R,
    READABLE,
    S,
    TABLE,
    TEXT,
    W,
    _open,
    _q,
    _rels,
    _xml,
    col_letters,
    parse_ref,
)


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def _w_text(p: ET.Element) -> str:
    out = []
    for node in p.iter():
        if node.tag == _q(W, "t"):
            out.append(node.text or "")
        elif node.tag == _q(W, "tab"):
            out.append("\t")
        elif node.tag in (_q(W, "br"), _q(W, "cr")):
            out.append("\n")
    return "".join(out)


def _docx_blocks(z: zipfile.ZipFile) -> "list[str]":
    doc = _xml(z, "word/document.xml")
    if doc is None:
        raise OfficeError("this .docx has no word/document.xml")
    body = doc.find(_q(W, "body"))
    lines: list[str] = []
    for child in (body if body is not None else []):
        if child.tag == _q(W, "p"):
            text = _w_text(child)
            style = child.find(f"{_q(W, 'pPr')}/{_q(W, 'pStyle')}")
            sval = (style.get(_q(W, "val")) if style is not None else "") or ""
            heading = re.match(r"(?i)heading\s*([1-9])$", sval)
            if sval.lower() == "title" and text:
                lines.append(f"# {text}")
            elif heading and text:
                lines.append("#" * min(6, int(heading.group(1)) + (0 if lines else 0)) + f" {text}")
            elif child.find(f"{_q(W, 'pPr')}/{_q(W, 'numPr')}") is not None and text:
                lines.append(f"- {text}")
            else:
                lines.append(text)
        elif child.tag == _q(W, "tbl"):
            for row in child.iter(_q(W, "tr")):
                cells = [" ".join(_w_text(p) for p in tc.iter(_q(W, "p"))).strip()
                         for tc in row.findall(_q(W, "tc"))]
                lines.append("| " + " | ".join(cells) + " |")
            lines.append("")
    return lines


def _xlsx_sheets(z: zipfile.ZipFile) -> "list[tuple[str, str]]":
    wb = _xml(z, "xl/workbook.xml")
    if wb is None:
        raise OfficeError("this .xlsx has no xl/workbook.xml")
    rels = _rels(z, "xl/workbook.xml")
    out = []
    for sheet in wb.iter(_q(S, "sheet")):
        target = rels.get(sheet.get(_q(R, "id")))
        if target:
            out.append((sheet.get("name") or target, target))
    return out


def _shared_strings(z: zipfile.ZipFile) -> "list[str]":
    sst = _xml(z, "xl/sharedStrings.xml")
    if sst is None:
        return []
    return ["".join(t.text or "" for t in si.iter(_q(S, "t"))) for si in sst.findall(_q(S, "si"))]


def _cell_value(c: ET.Element, strings: "list[str]") -> str:
    kind = c.get("t", "n")
    v = c.find(_q(S, "v"))
    if kind == "inlineStr":
        return "".join(t.text or "" for t in c.iter(_q(S, "t")))
    if v is None or v.text is None:
        f = c.find(_q(S, "f"))
        return f"={f.text}" if f is not None and f.text else ""
    if kind == "s":
        try:
            return strings[int(v.text)]
        except (ValueError, IndexError):
            return ""
    if kind == "b":
        return "TRUE" if v.text == "1" else "FALSE"
    return v.text


def read_xlsx_rows(path: Path, sheet: str = "", formulas: bool = False) -> "dict[str, list[list[str]]]":
    """{sheet name: rows} with every row padded to the sheet's width; formulas as '=...' when asked."""
    with _open(path) as z:
        strings = _shared_strings(z)
        out = {}
        for name, part in _xlsx_sheets(z):
            if sheet and name.lower() != sheet.lower():
                continue
            root = _xml(z, part)
            grid: dict[int, dict[int, str]] = {}
            for row in (root.iter(_q(S, "row")) if root is not None else []):
                for c in row.findall(_q(S, "c")):
                    try:
                        r, col = parse_ref(c.get("r", ""))
                    except OfficeError:
                        continue
                    f = c.find(_q(S, "f"))
                    value = f"={f.text}" if formulas and f is not None and f.text else _cell_value(c, strings)
                    if value != "":
                        grid.setdefault(r, {})[col] = value
            width = max((max(cols) + 1 for cols in grid.values() if cols), default=0)
            rows = [[grid.get(r, {}).get(cidx, "") for cidx in range(width)]
                    for r in range(1, (max(grid) if grid else 0) + 1)]
            out[name] = rows
        if sheet and not out:
            raise OfficeError(f"no sheet named {sheet!r}")
        return out


def _xlsx_blocks(path: Path, sheet: str = "", cell_range: str = "", formulas: bool = False) -> "list[str]":
    lines = []
    for name, rows in read_xlsx_rows(path, sheet, formulas).items():
        r0, c0, r1, c1 = 1, 0, len(rows), max((len(r) for r in rows), default=0) - 1
        if cell_range:
            a, _, b = cell_range.partition(":")
            (r0, c0), (r1, c1) = parse_ref(a), parse_ref(b or a)
        width = max((len(r) for r in rows), default=0)
        lines.append(f"## Sheet {name} ({len(rows)} rows x {width} columns)")
        if not rows:
            lines.append("(empty)")
            continue
        cols = range(c0, c1 + 1)
        lines.append("| row | " + " | ".join(col_letters(c) for c in cols) + " |")
        for r in range(r0, min(r1, len(rows)) + 1):
            row = rows[r - 1]
            lines.append(f"| {r} | " + " | ".join(row[c] if c < len(row) else "" for c in cols) + " |")
        lines.append("")
    return lines


def _a_paragraphs(node: ET.Element) -> "list[str]":
    return ["".join(t.text or "" for t in p.iter(_q(A, "t"))) for p in node.iter(_q(A, "p"))]


def _pptx_blocks(z: zipfile.ZipFile) -> "list[str]":
    pres = _xml(z, "ppt/presentation.xml")
    if pres is None:
        raise OfficeError("this .pptx has no ppt/presentation.xml")
    rels = _rels(z, "ppt/presentation.xml")
    lines = []
    for number, sld in enumerate(pres.iter(_q(P, "sldId")), 1):
        part = rels.get(sld.get(_q(R, "id")))
        root = _xml(z, part) if part else None
        if root is None:
            continue
        title, body = "", []
        for sp in root.iter(_q(P, "sp")):
            ph = sp.find(f".//{_q(P, 'ph')}")
            texts = [t for t in _a_paragraphs(sp) if t.strip()]
            if ph is not None and ph.get("type") in ("title", "ctrTitle") and not title:
                title = " ".join(texts)
            else:
                body += texts
        lines.append(f"## Slide {number}: {title or '(no title)'}")
        lines += [f"- {t}" for t in body]
        notes_part = next((p for p in _rels(z, part).values() if "notesSlide" in p), None)
        if notes_part:
            notes_root = _xml(z, notes_part)
            notes = [t for t in (_a_paragraphs(notes_root) if notes_root is not None else []) if t.strip()]
            if notes:
                lines.append("Notes: " + " ".join(notes))
        lines.append("")
    return lines


def _odf_text(node: ET.Element) -> str:
    out = []
    if node.text:
        out.append(node.text)
    for child in node:
        if child.tag == _q(TEXT, "s"):
            out.append(" " * int(child.get(_q(TEXT, "c"), "1")))
        elif child.tag == _q(TEXT, "tab"):
            out.append("\t")
        elif child.tag == _q(TEXT, "line-break"):
            out.append("\n")
        else:
            out.append(_odf_text(child))
        if child.tail:
            out.append(child.tail)
    return "".join(out)


def _odf_blocks(z: zipfile.ZipFile) -> "list[str]":
    content = _xml(z, "content.xml")
    if content is None:
        raise OfficeError("this OpenDocument file has no content.xml")
    lines: list[str] = []

    def walk(node: ET.Element, in_list: bool = False) -> None:
        for child in node:
            tag = child.tag
            if tag == _q(TEXT, "h"):
                level = int(child.get(_q(TEXT, "outline-level"), "1") or 1)
                lines.append("#" * min(6, level) + " " + _odf_text(child))
            elif tag == _q(TEXT, "p"):
                text = _odf_text(child)
                lines.append(f"- {text}" if in_list and text else text)
            elif tag == _q(TEXT, "list"):
                walk(child, True)
            elif tag == _q(TABLE, "table"):
                lines.append(f"## Table {child.get(_q(TABLE, 'name'), '')}".rstrip())
                for row in child.iter(_q(TABLE, "table-row")):
                    cells = []
                    for cell in row:
                        if cell.tag not in (_q(TABLE, "table-cell"), _q(TABLE, "covered-table-cell")):
                            continue
                        text = " ".join(_odf_text(p) for p in cell.iter(_q(TEXT, "p")))
                        if not text:  # a value or formula with no cached display text
                            boolean = cell.get(_q(OFFICE, "boolean-value"))
                            formula = cell.get(_q(TABLE, "formula"))
                            text = (("TRUE" if boolean in ("1", "true") else "FALSE") if boolean is not None
                                    else cell.get(_q(OFFICE, "value")) or cell.get(_q(OFFICE, "date-value"))
                                    or (formula.split(":", 1)[-1] if formula else ""))
                        repeat = min(int(cell.get(_q(TABLE, "number-columns-repeated"), "1") or 1), 64)
                        cells += [text] * repeat
                    while cells and not cells[-1]:
                        cells.pop()
                    if cells:
                        lines.append("| " + " | ".join(cells) + " |")
                lines.append("")
            elif tag == _q(DRAW, "page"):
                lines.append(f"## Slide: {child.get(_q(DRAW, 'name'), '')}")
                walk(child)
                lines.append("")
            else:
                walk(child, in_list)

    body = content.find(_q(OFFICE, "body"))
    walk(body if body is not None else content)
    return lines


def read(path: Path, sheet: str = "", cell_range: str = "", formulas: bool = False) -> str:
    """The document's text as light Markdown: headings, bullets, tables, one section per sheet or slide."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext in LEGACY:
        raise OfficeError(f"{ext} is the old binary format; open it in OnlyOffice and save it as "
                          f"{ext}x to work with it here")
    if ext not in READABLE:
        raise OfficeError(f"{ext or 'this'} is not a document type this reads ({', '.join(READABLE)})")
    if ext == ".xlsx":
        lines = _xlsx_blocks(path, sheet, cell_range, formulas)
    else:
        with _open(path) as z:
            lines = {".docx": _docx_blocks, ".pptx": _pptx_blocks}.get(ext, _odf_blocks)(z)
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
