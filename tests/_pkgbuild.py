"""Read a `PKGBUILD` dependency array without tripping over its own comments.

**Every naive parser of this file is wrong, and each was wrong here.** Arch's
`PKGBUILD` is a shell script: a `depends=(...)` array is a *quoted list with
comments in it*, and any parser that looks for the next `)` stops at the first
one inside a comment.

Measured on `shani-pkgbuilds/shani-chronoa/PKGBUILD`, whose `depends` block
carries this comment:

    # expand, factor, prime_factors, is_prime, linear solve) and bc runs the
    # exact rational arithmetic Matrix.solve cannot do ...

`pkgbuild.split("depends=(", 1)[1].split(")", 1)[0]` therefore returns the eleven
lines above and nothing after them - so `whisper-cpp`, `llama-cpp`,
`espeak-ng`, `libsecret`, `nmap` and every other dependency declared after that
point read as *absent*. The test using it reported
**"whisper-cpp is no longer declared anywhere, so a default install has no
speech input"** about a package that declares it in plain sight. A parser that
truncates produces a confident wrong answer, which is the failure this repo
keeps having.

So this reads a *shell array*: a quoted string is a dependency, a `#` starts a
comment that runs to end of line, and a bare `)` at the start of a line (after
whitespace) ends the array. Nothing here shells out, and nothing is evaluated.

The second thing this owns is **where the manifest is**. The in-repo
`PKGBUILD` was deleted - it had drifted and nothing built from it - so a test
that reads `REPO_ROOT/PKGBUILD` fails with `FileNotFoundError` on a dependency
question rather than an answer. The shipping Arch manifest is in the sibling
`shani-pkgbuilds` repo, and `arch_pkgbuild()` resolves that once, by
construction, so a caller cannot invent a path.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List

__all__ = ["arch_pkgbuild", "array", "depends", "optdepends", "package_missing"]

#: `depends=(`, `optdepends=(`, `makedepends=('git')` - any quoted array.
#: MULTILINE, because a PKGBUILD is a shell script that opens with comments and
#: a `^` without it only ever matches at byte zero of the file.
_ARRAY = re.compile(r"^(\w+)=\s*\(", re.M)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def arch_pkgbuild() -> Path:
    """The shipping Arch manifest, which lives in the sibling pkgbuilds repo."""
    path = _repo_root() / "shani-pkgbuilds" / "shani-chronoa" / "PKGBUILD"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} does not exist. The in-repo PKGBUILD was removed on "
            "2026-09-30 because nothing built from it and it had drifted; the "
            "shipping manifest is the one in shani-pkgbuilds.")
    return path


def array(text: str, name: str) -> List[str]:
    """The entries of `name=(...)`, comments stripped.

    Scans character by character and tracks three things a regex cannot: quote
    state, comment state, and whether a `)` is the one that ends the array. A
    `)` inside a comment or inside a quoted name is not the end, which is the
    difference between reading this file and truncating it mid-array. Works for
    `depends=(python)` on one line as well as a 40-line block.
    """
    # Every array in the file, not just the first: `optdepends=()` appears above
    # `depends=()` in plenty of manifests, and stopping at the first match made
    # a caller asking about `depends` get an empty list - the same confident
    # wrong answer this file exists to stop.
    for match in _ARRAY.finditer(text):
        if match.group(1) != name:
            continue
        return _entries_from(text, match.end())
    return []


def _entries_from(text: str, index: int) -> List[str]:
    entries: List[str] = []
    pending = ""
    quote = None
    while index < len(text):
        char = text[index]
        if quote:
            if char == quote:
                quote = None
                entries.append(pending)
                pending = ""
            else:
                pending += char
            index += 1
            continue
        if char == "#":
            # A comment runs to end of line; an entry being built is finished.
            if pending:
                entries.append(pending)
                pending = ""
            newline = text.find("\n", index)
            if newline == -1:
                break
            index = newline + 1
            continue
        if char in "'\"":
            quote = char
            index += 1
            continue
        if char == ")":
            if pending:
                entries.append(pending)
            return entries
        if char in " \t\n":
            if pending:
                entries.append(pending)
                pending = ""
            index += 1
            continue
        pending += char
        index += 1
    if pending:
        entries.append(pending)
    return entries


def depends(text: str) -> List[str]:
    """`depends=(...)` only - never `optdepends`, which means something else.

    The distinction is the whole question several tests ask: `whisper-cpp` as a
    hard dependency is correct, and the same string in `optdepends` would tell a
    user to install something they already have.
    """
    return array(text, "depends")


def optdepends(text: str) -> List[str]:
    """`optdepends=(...)`, as bare names.

    The `pkg: description` form is split at the colon so a caller comparing
    against a binary's package name is not handed "whisper-cpp: speech".
    """
    out = []
    for entry in array(text, "optdepends"):
        out.append(entry.split(":", 1)[0].strip())
    return out


def package_missing(name: str, text: str) -> bool:
    """Whether `name` is declared as a hard dependency."""
    return name not in depends(text)