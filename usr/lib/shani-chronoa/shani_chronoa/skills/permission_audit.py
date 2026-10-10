"""Skill: can anyone else on this machine read my files?

`get_file_info` reports one file's mode. `security_status` reads the boot and
firewall **senses** and says nothing about the filesystem. So the question "is
anything private readable by other users" had no answer here, and it is a real
one on any shared machine.

**The raw count is not the finding, and reporting it would be the confidently
wrong answer.** Measured on this box: 3,558 of 33,407 files under `~` are
world-readable - **10.6%** - which is entirely normal for a working tree full of
source, caches and build output. A reader that led with "3,558 files are
world-readable" would send someone fixing nothing. So the answer leads with the
**rate**, and with the two things that are actually unusual:

- **world-writable files**, where another user can *replace* the content. That is
  rare and is a real risk, and it is a different question from readability.
- **files whose names suggest secrets and that are readable.**

**And a name-based secret is a guess, labelled as one.** `Chrome Passwords.csv`
may be a real export or a test fixture, and nothing here can tell which from the
mode. The answer says it is looking at names, not at contents, and never claims
a file *is* a secret.

Nothing is chmod'ed. The permissions are this user's own to change, and a skill
that fixed them silently would have changed how their machine works without
being asked.
"""

from __future__ import annotations

import os
from pathlib import Path
from stat import S_IROTH, S_IWOTH

from shani_chronoa import files
from shani_chronoa.skills import Skill

#: Above this the walk stops and says so.
_CEILING = 40000

#: **Two tables, because substring and suffix are different questions.** Measured:
#: a single fragment list matched `README.keyctl` on "`.key`" and reported it as a
#: secret-named file, which it is not. A suffix-shaped fragment must match the
#: *end* of the name; only a word-shaped one is safe as a substring.
_SENSITIVE_WORDS = (
    "password", "passwd", "credential", "secret", "wallet", "keyring",
    "token", "apikey", "api_key", "private", "id_rsa", "id_dsa",
    "id_ecdsa", "id_ed25519", ".ssh", ".gnupg",
)

_SENSITIVE_SUFFIXES = (
    ".pem", ".key", ".p12", ".pfx", ".jks", ".netrc", ".pgpass",
    ".htpasswd", ".env", ".kdbx",
)


def _human(size: float) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


def _looks_sensitive(name: str) -> str:
    """The fragment that matched, or an empty string. Name-only, and the caller
    must say so.

    **A suffix-shaped fragment matches the end of the name, never anywhere in
    it** - otherwise `.key` matches `keyctl` and `.pem` matches `pemphigus`, and
    the answer reports a secret-named file where there is none.
    """
    low = name.lower()
    for fragment in _SENSITIVE_WORDS:
        if fragment in low:
            return fragment
    for fragment in _SENSITIVE_SUFFIXES:
        if low.endswith(fragment):
            return fragment
    return ""


def _walk(top: Path, ceiling: int):
    """(totals, world-writable files, world-writable dirs, sensitive, seen,
    skipped, truncated, unentered)."""
    readable = []
    writable_files = []
    writable_dirs = []
    sensitive = []
    seen = 0
    skipped = 0
    truncated = False
    unentered: list = []

    def _onerror(exc):
        nonlocal skipped
        skipped += 1
        where = getattr(exc, "filename", None)
        if where:
            unentered.append(str(where))

    for root, dirs, names in os.walk(top, onerror=_onerror):
        # **Directories as well as files.** A world-writable *directory* lets
        # another user create or replace anything inside it, which is a wider
        # hole than any single world-writable file - and `os.walk` puts
        # directory entries in `dirs`, not `names`.
        for name in list(names) + list(dirs):
            path = Path(root) / name
            try:
                info = path.lstat()
            except OSError:
                skipped += 1
                continue
            if os.path.islink(path):
                continue
            if info.st_mode & S_IWOTH:
                if os.path.isdir(path):
                    writable_dirs.append(path)
                else:
                    writable_files.append(path)
            if not os.path.isfile(path):
                continue
            seen += 1
            if info.st_mode & S_IROTH:
                readable.append((path, info.st_size))
                hit = _looks_sensitive(name)
                if hit:
                    sensitive.append((path, hit))
            if seen >= ceiling:
                truncated = True
                break
        if truncated:
            break
    return (readable, writable_files, writable_dirs, sensitive, seen, skipped,
            truncated, unentered)


def _run_skill(arguments: dict) -> str:
    raw = str(arguments.get("path") or "").strip()
    try:
        top = files.expand(raw or "~")
        if not top.is_dir():
            return f"{top} is not a directory, so there are no permissions in it."
    except files.PathProblem as exc:
        return str(exc)

    (readable, wfiles, wdirs, sensitive, seen, skipped,
     truncated, unentered) = _walk(top, _CEILING)
    if not seen:
        return (f"No readable files under {top}. That is not the same as it "
                "being empty - a permission problem looks like this too.")

    rate = len(readable) / seen * 100 if seen else 0
    lines = [f"{seen} file(s) under {top}. "
             f"**{len(readable)} ({rate:.1f}%) are readable by any user** on "
             f"this machine."]

    # The rate first, because the count alone is noise on a working tree.
    if rate > 50:
        lines.append("")
        lines.append("That is a high proportion - most of this tree is open to "
                     "every user. It is normal for a source checkout or a cache "
                     "and worth a look for anything personal in here.")
    elif rate > 20:
        lines.append("")
        lines.append("That is common for a working tree and not by itself a "
                     "finding; the lines below are what is.")
    else:
        lines.append("")
        lines.append("That is a low proportion, so this tree is mostly closed "
                     "to other users.")

    # **World-writable is the different and rarer question.**
    lines.append("")
    lines.append(f"**{len(wfiles)} file(s) and {len(wdirs)} directory(ies) are "
                 "world-writable** - any user on this machine can *replace* "
                 "their contents, which is a wider permission than reading.")
    for path in sorted(wfiles)[:12]:
        lines.append(f"  {path}")
    if len(wfiles) > 12:
        lines.append(f"  ... {len(wfiles) - 12} more")
    for path in sorted(wdirs)[:6]:
        lines.append(f"  directory: {path}")

    # The name-based guess, labelled.
    lines.append("")
    if sensitive:
        lines.append(f"**{len(sensitive)} readable file(s) have names that "
                     "suggest secrets.** This matched the **names**, never the "
                     "contents - a file called `Chrome Passwords.csv` may be a "
                     "real export or a test fixture, and nothing here can tell "
                     "which from its permissions:")
        for path, fragment in sorted(sensitive)[:12]:
            lines.append(f"  {path}   (matched: {fragment})")
        if len(sensitive) > 12:
            lines.append(f"  ... {len(sensitive) - 12} more")
    else:
        lines.append("No readable file here has a name that suggests a secret - "
                     "on names alone, not on contents.")

    if unentered:
        lines.append("")
        lines.append(f"**{len(unentered)} directory(ies) could not be "
                     "entered**, so they are not in these figures: "
                     + ", ".join(unentered[:6]))
    if truncated:
        lines.append(f"Stopped after {seen} file(s); this is not a complete "
                     "list. Narrow the path and ask again.")
    if skipped:
        lines.append(f"({skipped} path(s) could not be read and are not "
                     "counted.)")

    lines.append("")
    lines.append("Nothing was changed. These permissions are yours to set, and "
                 "`chmod` on them is a decision for you, not for this.")
    return "\n".join(lines)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "permission_audit",
        "description": (
            "Find files and directories under a path that other users on this "
            "machine can read or write. Use for 'can anyone see my files', 'is "
            "anything private exposed', 'what is world-writable in here'. Leads "
            "with the proportion that is readable, because a raw count is noise "
            "on a working tree, and separates world-writable (replaceable by "
            "others) from world-readable. Flags files whose NAMES suggest "
            "secrets and says it is matching names, not contents. Changes "
            "nothing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": ("Directory to audit. Defaults to your "
                                         "home directory.")},
            },
        },
    },
}

SKILLS = [Skill(name="permission_audit", schema=SCHEMA, run=_run_skill)]
