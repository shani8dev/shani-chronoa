"""Skill: an outline of a code project - its files and their classes and functions, most-used first.

"Give me an overview of ~/code/foo" or "where is the config loaded?" on a
project the model has never seen. Harvested from aider's repo map (rank the
definitions other files refer to, so a big repo fits a small budget) and
SWE-agent's `filemap` (a file with its bodies elided). The CLI matrix shows
neither ctags nor tree-sitter on the images, so Python is read with the
standard library's `ast` and other languages with conservative definition
patterns - shallower, said so in the output. Read-only; nothing is executed.
"""

from __future__ import annotations

import ast
import os
import re
from collections import Counter
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import Skill

MAX_FILES = 2000
MAX_BYTES = 400_000
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "build", "dist", "target", ".tox",
              ".mypy_cache", ".cache", "vendor", ".idea", ".next", "site-packages"}
_PATTERNS = {
    ".js": r"^\s*(?:export\s+)?(?:async\s+)?(?:function\s+(\w+)|class\s+(\w+)|(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?\()",
    ".ts": r"^\s*(?:export\s+)?(?:async\s+)?(?:function\s+(\w+)|class\s+(\w+)|interface\s+(\w+)|type\s+(\w+)\s*=)",
    ".go": r"^func\s+(?:\([^)]*\)\s*)?(\w+)|^type\s+(\w+)\s+(?:struct|interface)",
    ".rs": r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:fn\s+(\w+)|struct\s+(\w+)|enum\s+(\w+)|trait\s+(\w+))",
    ".sh": r"^\s*(?:function\s+)?(\w+)\s*\(\)\s*\{",
    ".c": r"^[A-Za-z_][\w\s\*]*?\b(\w+)\s*\([^;]*\)\s*\{?\s*$",
    ".java": r"^\s*(?:public|private|protected)?\s*(?:static\s+)?(?:class|interface|enum)\s+(\w+)",
}
_PATTERNS[".tsx"] = _PATTERNS[".jsx"] = _PATTERNS[".ts"]
_PATTERNS[".h"] = _PATTERNS[".cpp"] = _PATTERNS[".c"]

SCHEMA = {
    "type": "function",
    "function": {
        "name": "project_outline",
        "description": (
            "Outline a code project or one source file: files, classes and functions with line numbers, the "
            "most referenced first, so a large project fits. query focuses on names or paths containing it. "
            "Python is parsed exactly; other languages by definition patterns."
        ),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "query": {"type": "string"},
            "max_lines": {"type": "integer", "description": "Default 120."},
        }, "required": ["path"]},
    },
}


def _python_defs(text: str) -> "list[tuple[int, str]]":
    out = []
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            out.append((node.lineno, f"class {node.name}"))
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and not item.name.startswith("__"):
                    out.append((item.lineno, f"  def {item.name}({', '.join(a.arg for a in item.args.args[1:])})"))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((node.lineno, f"def {node.name}({', '.join(a.arg for a in node.args.args)})"))
    return out


def _pattern_defs(text: str, ext: str) -> "list[tuple[int, str]]":
    rx = re.compile(_PATTERNS[ext])
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        m = rx.match(line)
        if m:
            name = next((g for g in m.groups() if g), None)
            if name and name not in ("if", "for", "while", "switch", "return", "main"):
                out.append((n, line.strip()[:100]))
    return out


def _sources(root: Path) -> "list[Path]":
    if root.is_file():
        return [root]
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for f in filenames:
            p = Path(dirpath) / f
            if p.suffix in _PATTERNS or p.suffix == ".py":
                found.append(p)
                if len(found) >= MAX_FILES:
                    return found
    return sorted(found)


def _run(arguments: dict) -> str:
    try:
        root = files.resolve_in_home(arguments.get("path") or "")
    except files.PathProblem as exc:
        return str(exc)
    if not root.exists():
        return f"{root} does not exist."
    query = (arguments.get("query") or "").strip().lower()
    budget = max(20, min(int(arguments.get("max_lines") or 120), 600))
    per_file: dict = {}
    words: Counter = Counter()
    skipped = 0
    for path in _sources(root):
        try:
            if path.stat().st_size > MAX_BYTES:
                skipped += 1
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        words.update(re.findall(r"[A-Za-z_]\w{2,}", text))
        try:
            defs = _python_defs(text) if path.suffix == ".py" else _pattern_defs(text, path.suffix)
        except SyntaxError:
            defs = [(0, "(does not parse)")]
        per_file[path] = defs
    if not per_file:
        return f"No source files under {root}."

    def name_of(label: str) -> str:
        m = re.search(r"(?:class|def|fn|func|function|struct|enum|trait|interface|type)\s+(\w+)", label)
        return m.group(1) if m else ""

    def score(path) -> int:
        # how often this file's definitions are mentioned anywhere (minus the definition itself)
        return sum(max(0, words.get(name_of(d), 0) - 1) for _, d in per_file[path])

    ranked = sorted(per_file, key=lambda p: (-score(p), str(p)))
    if query:
        ranked = [p for p in ranked if query in str(p).lower() or any(query in d.lower() for _, d in per_file[p])]
    base = root if root.is_dir() else root.parent
    lines, shown = [], 0
    for path in ranked:
        defs = per_file[path]
        if query and query not in str(path).lower():
            defs = [d for d in defs if query in d[1].lower()]
        rel = path.relative_to(base) if path.is_relative_to(base) else path
        block = [f"{rel}:"] + [f"  {n}: {d}" for n, d in defs[:40]] + (
            [f"  ... {len(defs) - 40} more"] if len(defs) > 40 else [])
        if len(lines) + len(block) > budget:
            break
        lines += block
        shown += 1
    head = (f"Outline of {root} - {shown} of {len(ranked)} file(s), most-referenced first"
            + (f", matching {query!r}" if query else "")
            + (f"; {skipped} file(s) over {MAX_BYTES // 1000} kB not read" if skipped else "") + ":")
    return head + "\n" + "\n".join(lines)


SKILLS = [Skill(name="project_outline", schema=SCHEMA, run=_run)]
