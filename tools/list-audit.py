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

NAMEISH = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*$")
CAPS = re.compile(r"[A-Z][A-Z0-9_]{3,}")
MIN_NAMES = 3

TARGETS = [
    ("shani-cassini/src", "python"),
    ("shani-deploy/scripts", "bash"),
    ("shani-chronoa/usr/lib/shani-chronoa/shani_chronoa", "python"),
    ("shani-settings/etc", "conf"),
]


def _workspace_root() -> pathlib.Path:
    """Where the sibling repos are, without asking anyone to set an env var.

    This tool lives at `<workspace>/shani-chronoa/tools/list-audit.py` and its
    targets are siblings of `shani-chronoa`, so the workspace is up the tree
    from here. The first version defaulted to the script's own directory, which
    is `tools/` - so `ROOT / "shani-chronoa/..."` never existed, every target
    was skipped by `if not base.is_dir(): continue`, and the tool printed

        scanned 0 code-level name lists across 4 repos; 0 unmeasured

    **A clean result from measuring nothing.** Read as a pass, it says every
    hand-kept list in four repos is measured, and it is the same
    absence-presented-as-a-result shape this workspace records everywhere else.
    `main` now refuses to print that verdict at all; see NOTHING WAS MEASURED
    there. Set SHANI_ROOT to override this.
    """
    here = pathlib.Path(__file__).resolve()
    for candidate in here.parents:
        if any((candidate / rel).is_dir() for rel, _ in TARGETS):
            return candidate
    return here.parent


ROOT = pathlib.Path(os.environ.get("SHANI_ROOT", str(_workspace_root())))


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
    seen_repos = []
    for rel, kind in TARGETS:
        base = ROOT / rel
        if not base.is_dir():
            continue
        seen_repos.append(rel.split("/")[0])
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
    print()
    if not seen_repos:
        # **Refuse to report a clean result from measuring nothing.** Every
        # target was skipped, so "0 unmeasured" is a fact about the paths and
        # not about the lists - and it reads exactly like a pass. The summary
        # line is not printed at all, because that line is the thing that reads
        # as a verdict: a reader skimming past the refusal below would still
        # take "0 unmeasured" for an answer. Exit 2, so a caller checking the
        # status is not told everything is fine either.
        print("NOTHING WAS MEASURED: none of the four target directories exist")
        print("under %s" % ROOT)
        print("Looked for:")
        for rel, _ in TARGETS:
            print("    %s/%s" % (ROOT, rel))
        print("This tool lives in <workspace>/shani-chronoa/tools/ and audits that")
        print("workspace's siblings; it finds them from its own location. Set")
        print("SHANI_ROOT to point it somewhere else.")
        return 2
    print("scanned %d code-level name lists across %d repos; %d unmeasured"
          % (total, len(seen_repos), len(findings)))
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
