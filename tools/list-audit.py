#!/usr/bin/env python3
"""Audit hand-kept lists of names: which are measured, and which are not.

A hand-kept list of names - config keys, driver names, paths, states - has to be
updated in the same commit as the thing it describes. The failure is silent and
always the same direction: the list is missing something, so a real thing is
refused or hidden.

This does not judge whether each list is correct - that needs the real artifact.
It answers the cheaper question that decides where to look: which of these lists
does no test mention? A list no test names is a list whose staleness nothing
would notice.

That is how the NUT driver list was found: 48 hand-kept names, tests that used
them, and no measurement against the 73 drivers the real package ships.
"""
from __future__ import annotations

import ast
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(os.environ.get("SHANI_ROOT",
                              str(pathlib.Path(__file__).resolve().parent)))

TARGETS = [
    ("shani-cassini/src", "python"),
    ("shani-deploy/scripts", "bash"),
    ("shani-chronoa/usr/lib/shani-chronoa/shani_chronoa", "python"),
    ("shani-settings/etc", "conf"),
]

NAMEISH = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*$")
CAPS = re.compile(r"[A-Z][A-Z0-9_]{3,}")
MIN_NAMES = 3


def _names_from(values):
    out = []
    for elt in values:
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            if NAMEISH.match(elt.value) and len(elt.value) > 1:
                out.append(elt.value)
        elif isinstance(elt, ast.Tuple):
            for sub in elt.elts:
                if (isinstance(sub, ast.Constant)
                        and isinstance(sub.value, str)
                        and NAMEISH.match(sub.value)):
                    out.append(sub.value)
    return out


def python_lists(path):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError):
        return
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if not targets:
            continue
        if isinstance(node.value, ast.Dict):
            values = ([k for k in node.value.keys if k is not None]
                      + [v for v in node.value.values if v is not None])
        else:
            values = list(node.value.elts)
        names = _names_from(values)
        if len(set(names)) >= MIN_NAMES:
            yield targets[0], sorted(set(names)), node.lineno


def bash_lists(path):
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    pattern = re.compile(r'^[ \t]*(?:local[ \t]+)?([A-Z][A-Z0-9_]+)="([^"]{12,})"',
                         re.M)
    for m in pattern.finditer(text):
        names = [w for w in m.group(2).split() if NAMEISH.match(w)]
        if len(set(names)) >= MIN_NAMES:
            yield m.group(1), sorted(set(names)), text[:m.start()].count("\n") + 1


def conf_keys(path):
    keys = set()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    for line in lines:
        s = line.strip()
        if not s or s.startswith(("#", ";")):
            continue
        m = re.match(r"^([A-Za-z][A-Za-z0-9_-]*)[ \t=]", s)
        if m:
            keys.add(m.group(1))
    if len(keys) >= MIN_NAMES:
        yield path.name, sorted(keys), 1


def mentioned_tokens(root, repo):
    """Every ALL-CAPS token any test file in `repo` mentions."""
    chunks = []
    for t in sorted((root / repo).glob("tests/**/*")):
        if t.is_file() and t.suffix in (".py", ".sh"):
            try:
                chunks.append(t.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    return {t.lstrip("_") for t in CAPS.findall("\n".join(chunks))}


def main():
    print("hand-kept name-list audit")
    print("=" * 74)
    print("UNMEASURED = no test file in its own repo names the constant. That does")
    print("not prove the list is wrong; it proves nothing would notice if it were.")
    print()
    total = 0
    findings = []
    conf_seen = []
    for rel, kind in TARGETS:
        base = ROOT / rel
        if not base.is_dir():
            continue
        repo = rel.split("/")[0]
        mentioned = mentioned_tokens(ROOT, repo)
        lister = {"python": python_lists, "bash": bash_lists,
                  "conf": conf_keys}[kind]
        for path in sorted(base.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if kind == "python" and path.suffix != ".py":
                continue
            if kind == "bash" and path.suffix != ".sh":
                continue
            if kind == "conf" and path.parent == base:
                continue
            for name, names, lineno in lister(path):
                bucket = "conf-data" if kind == "conf" else "code"
                if bucket == "conf-data":
                    conf_seen.append(path)
                    continue
                total += 1
                if name.lstrip("_") not in mentioned:
                    findings.append((repo, str(path.relative_to(ROOT)),
                                     name, lineno, len(names)))
    print("scanned %d code-level name lists across %d repos; %d unmeasured"
          % (total, len(TARGETS), len(findings)))
    print("(%d config files also scanned as data - excluded from the code total)"
          % len(conf_seen))
    print()
    if findings:
        print("  %-17s %-42s %-24s line  names" % ("repo", "file", "constant"))
        print("  " + "-" * 94)
        for repo, f, name, lineno, n in sorted(findings, key=lambda x: -x[4]):
            print("  %-17s %-42s %-24s %4d  %d" % (repo, f, name, lineno, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
