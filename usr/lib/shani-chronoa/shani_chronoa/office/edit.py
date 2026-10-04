"""Changing an existing .xlsx or .docx in place, keeping everything the edit does not touch byte-for-byte."""

from __future__ import annotations


import io
import re
import zipfile
from pathlib import Path
from typing import Iterable, Optional
from xml.etree import ElementTree as ET

from .common import (  # noqa: F401
    OfficeError,
    R,
    S,
    W,
    _XML,
    _open,
    _q,
    col_letters,
    parse_ref,
)
from .read import (  # noqa: F401
    _shared_strings,
    _xlsx_sheets,
)
from .write import (  # noqa: F401
    _Strings,
    _docx_body,
    _xlsx_cell,
)


# --------------------------------------------------------------------------
# Editing an existing file
# --------------------------------------------------------------------------


def _declared(raw: bytes) -> "dict[str, str]":
    """prefix -> uri for every xmlns on the root element, as written in the file."""
    head = raw[: raw.find(b">", raw.find(b"<", raw.find(b"?>") + 2 if b"?>" in raw[:200] else 0)) + 1]
    return {m.group(1).decode(): m.group(2).decode()
            for m in re.finditer(rb'xmlns:([A-Za-z_][\w.-]*)="([^"]+)"', head)}


def _serialize(root: ET.Element, original: bytes) -> bytes:
    """ElementTree output with the original prefixes, and any declaration it dropped put back."""
    declared = _declared(original)
    for prefix, uri in declared.items():
        ET.register_namespace(prefix, uri)
    m = re.search(rb'xmlns="([^"]+)"', original[:4000])
    if m:
        ET.register_namespace("", m.group(1).decode())
    out = ET.tostring(root, encoding="UTF-8", xml_declaration=False)
    end = out.find(b">")
    tag_head = out[:end]
    missing = "".join(f' xmlns:{p}="{u}"' for p, u in declared.items()
                      if f"xmlns:{p}=".encode() not in tag_head)
    if missing:
        insert_at = end - 1 if out[end - 1:end] == b"/" else end
        out = out[:insert_at] + missing.encode() + out[insert_at:]
    return (_XML.encode() + out)


def _rewrite(path: Path, replace: "dict[str, Optional[bytes]]") -> bytes:
    """The package with some parts replaced (bytes) or removed (None), everything else byte-for-byte."""
    buf = io.BytesIO()
    with _open(path) as src, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            if info.filename in replace:
                data = replace[info.filename]
                if data is not None:
                    dst.writestr(info, data)
                continue
            dst.writestr(info, src.read(info.filename))
        existing = set(src.namelist())
        for name, data in replace.items():
            if name not in existing and data is not None:
                dst.writestr(name, data)
    return buf.getvalue()


def _drop_calc_chain(path: Path, z: zipfile.ZipFile, changes: dict) -> None:
    if "xl/calcChain.xml" not in z.namelist():
        return
    changes["xl/calcChain.xml"] = None
    ct_raw = z.read("[Content_Types].xml")
    changes["[Content_Types].xml"] = re.sub(rb'<Override[^>]*PartName="/xl/calcChain.xml"[^>]*/>', b"", ct_raw)
    rel_raw = z.read("xl/_rels/workbook.xml.rels")
    changes["xl/_rels/workbook.xml.rels"] = re.sub(rb'<Relationship[^>]*Target="[^"]*calcChain.xml"[^>]*/>', b"", rel_raw)


def set_cells(path: Path, sheet: str, values: "dict[str, object]") -> bytes:
    """New package bytes with each 'B12' -> value written to `sheet` (first sheet when empty)."""
    with _open(path) as z:
        sheets = _xlsx_sheets(z)
        if not sheets:
            raise OfficeError("this workbook has no sheets")
        match = [p for n, p in sheets if not sheet or n.lower() == sheet.lower()]
        if not match:
            raise OfficeError(f"no sheet named {sheet!r}; it has {', '.join(n for n, _ in sheets)}")
        part = match[0]
        strings = _Strings(_shared_strings(z))
        had_sst = "xl/sharedStrings.xml" in z.namelist()
        original = z.read(part)
        root = ET.fromstring(original)
        data = root.find(_q(S, "sheetData"))
        if data is None:
            raise OfficeError("that sheet has no sheetData")
        for ref, value in values.items():
            r, c = parse_ref(ref)
            ref = f"{col_letters(c)}{r}"
            rows = {int(x.get("r", 0)): x for x in data.findall(_q(S, "row"))}
            row = rows.get(r)
            if row is None:
                row = ET.Element(_q(S, "row"), {"r": str(r)})
                later = [x for k, x in sorted(rows.items()) if k > r]
                data.insert(list(data).index(later[0]) if later else len(data), row)
            cell_xml = _xlsx_cell(ref, value, strings=strings)
            old = next((x for x in row.findall(_q(S, "c")) if x.get("r") == ref), None)
            style = old.get("s") if old is not None else None
            if old is not None:
                position = list(row).index(old)
                row.remove(old)
            else:
                position = len(row)
                for i, x in enumerate(row):
                    try:
                        if x.tag == _q(S, "c") and parse_ref(x.get("r", ""))[1] > c:
                            position = i
                            break
                    except OfficeError:
                        continue
            if cell_xml:
                new = ET.fromstring(f'<x xmlns="{S}">{cell_xml}</x>')[0]
                if style:
                    new.set("s", style)
                row.insert(position, new)
            row.attrib.pop("spans", None)
        dim = root.find(_q(S, "dimension"))
        if dim is not None:
            root.remove(dim)  # optional, and a stale one is worse than none
        changes = {part: _serialize(root, original)}
        _drop_calc_chain(path, z, changes)
        if strings.added:
            if had_sst:
                # rewrite only by appending <si> entries, keeping rich text runs already there
                sst_raw = z.read("xl/sharedStrings.xml")
                sst = ET.fromstring(sst_raw)
                for text in strings.items[len(_shared_strings(z)):]:
                    si = ET.SubElement(sst, _q(S, "si"))
                    t = ET.SubElement(si, _q(S, "t"))
                    t.text = text
                    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                sst.set("uniqueCount", str(len(strings.items)))
                sst.attrib.pop("count", None)
                changes["xl/sharedStrings.xml"] = _serialize(sst, sst_raw)
            else:
                changes["xl/sharedStrings.xml"] = strings.xml().encode()
                ct = changes.get("[Content_Types].xml", z.read("[Content_Types].xml"))
                changes["[Content_Types].xml"] = ct.replace(b"</Types>", (
                    b'<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-'
                    b'officedocument.spreadsheetml.sharedStrings+xml"/></Types>'))
                rels = changes.get("xl/_rels/workbook.xml.rels", z.read("xl/_rels/workbook.xml.rels"))
                changes["xl/_rels/workbook.xml.rels"] = rels.replace(b"</Relationships>", (
                    f'<Relationship Id="rIdChronoaSst" Type="{R}/sharedStrings" '
                    'Target="sharedStrings.xml"/></Relationships>').encode())
        wb_raw = z.read("xl/workbook.xml")
        if b"fullCalcOnLoad" not in wb_raw:
            wb = ET.fromstring(wb_raw)
            calc = wb.find(_q(S, "calcPr"))
            if calc is None:
                calc = ET.SubElement(wb, _q(S, "calcPr"))
            calc.set("fullCalcOnLoad", "1")
            changes["xl/workbook.xml"] = _serialize(wb, wb_raw)
    return _rewrite(path, changes)


def docx_replace(path: Path, find: str, replacement: str) -> "tuple[bytes, int]":
    """(new package, count): replace text in the body, even across Word's run splits.

    A paragraph whose text contains `find` only across several runs is
    rewritten into its first run's formatting, which is the honest price of
    matching what a person sees rather than how Word happened to split it.
    """
    if not find:
        raise OfficeError("give the text to find")
    with _open(path) as z:
        original = z.read("word/document.xml")
    root = ET.fromstring(original)
    count = 0
    for p in root.iter(_q(W, "p")):
        texts = [t for t in p.iter(_q(W, "t"))]
        joined = "".join(t.text or "" for t in texts)
        if find not in joined:
            continue
        single = [t for t in texts if find in (t.text or "")]
        if single:
            for t in single:
                count += (t.text or "").count(find)
                t.text = t.text.replace(find, replacement)
                t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            continue
        count += joined.count(find)
        texts[0].text = joined.replace(find, replacement)
        texts[0].set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        for t in texts[1:]:
            t.text = ""
    if not count:
        return b"", 0
    return _rewrite(path, {"word/document.xml": _serialize(root, original)}), count


def docx_append(path: Path, markdown: str) -> bytes:
    """New package bytes with the Markdown appended at the end of the body, before the section settings."""
    with _open(path) as z:
        original = z.read("word/document.xml")
        names = set(z.namelist())
    root = ET.fromstring(original)
    body = root.find(_q(W, "body"))
    if body is None:
        raise OfficeError("this .docx has no body")
    xml = _docx_body(markdown)
    if "w:numId" in xml and "word/numbering.xml" not in names:
        xml = re.sub(r"<w:numPr>.*?</w:numPr>", "", xml)  # bullets need numbering.xml; keep the text
    fragment = ET.fromstring(f'<x xmlns:w="{W}">{xml}</x>')
    sect = body.find(_q(W, "sectPr"))
    at = list(body).index(sect) if sect is not None else len(body)
    for i, child in enumerate(list(fragment)):
        body.insert(at + i, child)
    return _rewrite(path, {"word/document.xml": _serialize(root, original)})


def iter_parts(path: Path) -> "Iterable[str]":
    with _open(path) as z:
        return list(z.namelist())
