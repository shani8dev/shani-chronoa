"""XML namespaces, limits and the zip/XML helpers every part of `office` shares."""

from __future__ import annotations


import re
import zipfile
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
DRAW = "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"

READABLE = (".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp")
WRITABLE = (".docx", ".xlsx", ".pptx")
LEGACY = (".doc", ".xls", ".ppt")
#: A zip bomb guard: no part of a real office file a person edits is this big.
MAX_PART = 64 * 1024 * 1024


class OfficeError(Exception):
    """A file this module cannot read or change, with the reason in words."""


def _q(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


def _open(path: Path) -> zipfile.ZipFile:
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise OfficeError(f"{path.name} is not a valid {path.suffix} file (not a zip package)") from exc
    for info in z.infolist():
        if info.file_size > MAX_PART:
            raise OfficeError(f"{path.name} has a part of {info.file_size} bytes, too large to open safely")
    return z


def _xml(z: zipfile.ZipFile, name: str) -> Optional[ET.Element]:
    try:
        return ET.fromstring(z.read(name))
    except KeyError:
        return None
    except ET.ParseError as exc:
        raise OfficeError(f"{name} inside the file is not well-formed XML ({exc})") from exc


def _rels(z: zipfile.ZipFile, part: str) -> dict:
    """{rId: target path inside the zip} for one part's relationships."""
    folder, _, name = part.rpartition("/")
    root = _xml(z, f"{folder}/_rels/{name}.rels" if folder else f"_rels/{name}.rels")
    out = {}
    for rel in (root if root is not None else []):
        target = rel.get("Target", "")
        if rel.get("TargetMode") == "External":
            continue
        if target.startswith("/"):
            full = target[1:]
        else:
            parts = (folder.split("/") if folder else []) + target.split("/")
            stack = []
            for seg in parts:
                if seg == "..":
                    if stack:
                        stack.pop()
                elif seg and seg != ".":
                    stack.append(seg)
            full = "/".join(stack)
        out[rel.get("Id")] = full
    return out


def col_letters(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    out = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


def parse_ref(ref: str) -> "tuple[int, int]":
    """'B12' -> (row 12, column index 1); raises OfficeError for anything else."""
    m = re.fullmatch(r"\s*([A-Za-z]{1,3})([1-9][0-9]{0,6})\s*", ref or "")
    if not m:
        raise OfficeError(f"{ref!r} is not a cell reference like B12")
    col = 0
    for ch in m.group(1).upper():
        col = col * 26 + (ord(ch) - 64)
    return int(m.group(2)), col - 1
_XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
