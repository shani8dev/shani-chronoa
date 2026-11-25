"""Skill: what junk, temporary or leftover files are in here?

`find_files` answers what a *name* matches, `stale_files` answers what is big
and untouched, and neither answers the question a person asks when they open a
folder they have not tidied in a year: **which of these are leftovers** - the
`.tmp` from a crash, the `.DS_Store` a Mac dropped on the way past, the `~$`
lock PowerPoint never cleaned up, the core dump nobody ever deleted.

**The name is the evidence, and a name is a guess.** Nothing here opens a file
to decide whether it is junk except the first eighteen bytes of one named like
a core dump, so a file reaches this list because of what it is called and for
no other reason. That is wrong often enough to matter, and two of the ways it
is wrong are measured rather than argued:

- **`pubring.kbx~` is not junk.** GnuPG writes it as the backup of the
  keyring, and it is the only copy of that keyring. A `*~` name rule reports
  it, and it is the most consequential thing in `~/.gnupg`.
- **`core` is a name, not a dump.** On the machine this was written on, a walk
  of `$HOME` found **76** paths named `core` or `core.<something>`: 50 files,
  every one a `core.js` / `core.ts` / `core.cjs` / `core.d.ts` module or its
  source map, and 26 directories or links. **Zero** are core dumps. So the
  core-dump rule here does not stop at the name: it reads the ELF header and
  reports a file only when `e_type` is `ET_CORE`, and it states how many name
  matches the bytes disagreed with.

So the answer prints its own rules, labels the guess beside every file, and
**deletes, moves and changes nothing**. A leftover is sometimes the only record
of something; which of these mattered is a question for a person, not for a
name.

Symlinks are skipped, because a link and its target are one file and counting
both double-counts the size (`space_by_type` measured exactly that). A dangling
link is `broken_symlinks`' question rather than this one's, so it is neither
dropped silently nor borrowed.
"""

from __future__ import annotations

import os
import stat
import struct
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import Skill

#: The order the answer reports families in, each with the label used in the
#: listing and the **name rules** printed with the result. The rules are shown
#: because they are a judgement: a reader who thinks `.tmp` is meaningless on
#: their machine needs to be able to see that this is the whole basis for the
#: answer, not to take it on trust.
_FAMILIES = (
    ("editor", "editor/save leftovers",
     "*.tmp, *.swp, *.swo, *.kate-swp, *~"),
    ("lock", "lock and atomic-save leftovers",
     ".#*, ~$*, .goutputstream-*"),
    ("os", "OS metadata",
     ".DS_Store, Thumbs.db, desktop.ini"),
    ("download", "unfinished downloads",
     "*.crdownload, *.part, *.filepart"),
    ("merge", "merge leftovers",
     "*.orig, *.rej"),
    ("core", "core dumps (bytes checked)",
     "core, core.*"),
)
_LABELS = {key: label for key, label, _rules in _FAMILIES}

#: Vim's swap files, Kate's, and the editor- and crash-agnostic `*.tmp`.
_EDITOR_SUFFIXES = (".tmp", ".swp", ".swo", ".kate-swp")

#: Emacs/Gvim lock files, Microsoft Office owner files (`~$name.docx`), and
#: GNOME's atomic-save leftovers (`.goutputstream-XXXXXX`, which is what
#: Text Editor leaves behind when a save is interrupted).
_LOCK_PREFIXES = (".#", "~$", ".goutputstream-")

#: Exact names, matched case-insensitively - a Mac writes `.DS_Store` and
#: Windows writes `Thumbs.db` / `desktop.ini`, and both spell in either case.
_EXACT_NAMES = frozenset({".ds_store", "thumbs.db", "desktop.ini"})

#: Chrome's `.crdownload`, wget/aria2's `.part`, Firefox's `.filepart`.
_DOWNLOAD_SUFFIXES = (".crdownload", ".part", ".filepart")

#: What `patch` and `git merge` leave when a hunk does not apply cleanly.
_MERGE_SUFFIXES = (".orig", ".rej")

#: A core dump is a name (the kernel's default `core_pattern` is plain `core`)
#: plus bytes. The name alone is measured at **0 of 50** on this box, so the
#: bytes are what decide.
_ET_CORE = 4

#: `e_ident` is 16 bytes and `e_type` follows at offset 16, little- or
#: big-endian per `EI_DATA` (`e_ident[5]`). Eighteen bytes is the whole read.
_ELF_BYTES = 18

#: Above this the walk stops and says so. Junk is rare, so this is the bound
#: on how much tree is examined rather than on how much junk is found.
_CEILING = 60000

_DEFAULT_LIMIT = 25
_LIMIT_MAX = 200


def _family(name: str) -> "str | None":
    """Which junk family a *name* claims, or None.

    One file, one family: the first rule that matches wins, so nothing is
    counted twice and the per-family counts add up to the total.
    """
    low = name.lower()
    if low.endswith(_EDITOR_SUFFIXES) or name.endswith("~"):
        return "editor"
    if name.startswith(_LOCK_PREFIXES):
        return "lock"
    if low in _EXACT_NAMES:
        return "os"
    if low.endswith(_DOWNLOAD_SUFFIXES):
        return "download"
    if low.endswith(_MERGE_SUFFIXES):
        return "merge"
    if name == "core" or name.startswith("core."):
        return "core"
    return None


def _core_verdict(path: Path) -> "bool | None":
    """Is a file named like a core dump actually one?

    Returns True (an ELF whose `e_type` is `ET_CORE`), False (a regular file
    that is not one), or None (**it could not be read**). None is deliberately
    not False: a file this cannot open is not a file with no junk in it, and
    folding the two together would shrink the answer silently - the
    absence-shaped green this package keeps refusing.

    `ET_CORE` is the published ELF value. This box cannot measure it directly:
    `/proc/sys/kernel/core_pattern` pipes to apport, so no core file is ever
    written to disk here and `/var/lib/systemd/coredump` is empty. The *parse*
    was measured against real ELF files on this box - `/bin/ls` and
    `libc.so.6` read `ET_DYN` (3), `/usr/bin/python3` `ET_EXEC` (2) - so the
    offsets and endianness are checked and only the constant is taken from the
    specification.
    """
    try:
        with open(path, "rb") as handle:
            head = handle.read(_ELF_BYTES)
    except OSError:
        return None
    if len(head) < _ELF_BYTES or not head.startswith(b"\x7fELF"):
        return False
    endian = "<" if head[5] == 1 else ">"
    (e_type,) = struct.unpack(endian + "H", head[16:18])
    return e_type == _ET_CORE


def _walk(top: Path, ceiling: int):
    """(junk, examined, unreadable, truncated, unentered, named_not_files,
    named_not_dumps).

    **`os.walk`'s error callback is where an unentered directory shows up.**
    Swallowing it reports "nothing here" for a tree this never looked inside;
    `stale_files` measured a mode-000 directory vanishing that way and its
    answer reporting 10 MiB for a tree holding 50.

    `named_not_files` is every path whose *name* matched but which is a
    directory or a link rather than a plain file. Measured on this box that is
    the largest single group - 26 of them, mostly directories called `core` in
    JavaScript/TypeScript trees - so counting them keeps the exclusion visible
    instead of silently deciding they are not junk.
    """
    junk: list = []
    examined = 0
    unreadable = 0
    truncated = False
    unentered: list = []
    named_not_files = 0
    named_not_dumps = 0

    def _onerror(exc):
        nonlocal unreadable
        unreadable += 1
        where = getattr(exc, "filename", None)
        if where:
            unentered.append(str(where))

    for root, dirnames, names in os.walk(top, onerror=_onerror):
        here = Path(root)
        for dirname in dirnames:
            if _family(dirname) is not None:
                named_not_files += 1
        for name in names:
            if examined >= ceiling:
                truncated = True
                break
            family = _family(name)
            if family is None:
                examined += 1
                continue
            path = here / name
            try:
                info = path.lstat()
            except OSError:
                # A path that cannot even be stat'ed is not a path with no
                # junk in it.
                unreadable += 1
                continue
            examined += 1
            # **lstat, not stat**, so a link is seen as a link. A link and its
            # target are one file; counting both counts the size twice.
            if not stat.S_ISREG(info.st_mode):
                named_not_files += 1
                continue
            if family == "core":
                verdict = _core_verdict(path)
                if verdict is None:
                    unreadable += 1
                    continue
                if not verdict:
                    named_not_dumps += 1
                    continue
            junk.append((path, info.st_size, family))
        if truncated:
            break
    junk.sort(key=lambda item: -item[1])
    return (junk, examined, unreadable, truncated, unentered,
            named_not_files, named_not_dumps)


def _walk_notes(examined: int, truncated: bool, unentered: list,
                unreadable: int, named_not_files: int) -> list:
    """The lines that say how far the walk got, and what it could not see."""
    lines = []
    if truncated:
        lines.append(f"Stopped after {examined} file(s); this is not a "
                     "complete list. Narrow the path and ask again.")
    if named_not_files:
        lines.append(f"({named_not_files} path(s) matched a name above but "
                     "are directories or links rather than files, so they "
                     "are not listed.)")
    if unentered:
        lines.append("")
        lines.append(f"**{len(unentered)} directory(ies) could not be "
                     "entered**, so their files are not in these figures: "
                     + ", ".join(unentered[:10])
                     + (f" (+{len(unentered) - 10} more)"
                        if len(unentered) > 10 else ""))
    if unreadable:
        lines.append(f"({unreadable} path(s) could not be read, so they are "
                     "not in these figures - a file this cannot read is not a "
                     "file that does not exist.)")
    return lines


def _guess_lines() -> list:
    """The caveat that this is a name and nothing more, and the two measured
    ways the name is wrong."""
    return [
        "",
        "**A name is a guess, and it is the only thing that was checked.** No "
        "file was opened to decide this - the first bytes of a core-dump "
        "candidate are the one exception - so every file above is here "
        "because of what it is called. On the machine this was written on "
        "that guess is demonstrably wrong in both directions: "
        "`~/.gnupg/pubring.kbx~` is GnuPG's backup of the keyring, and the "
        "only copy of it, while 50 files there are named `core` and not one is "
        "a core dump.",
        "",
        "**Nothing was deleted, moved or changed.** A leftover is sometimes "
        "the only record of something, and which of these mattered is a "
        "question for a person rather than for a name.",
    ]


def _core_lines(dumps: int, not_dumps: int) -> list:
    """The core-dump paragraph, or nothing when no name matched.

    Emitted in the **empty** answer as well as the full one: a file named
    `core` that the bytes say is not a dump is information either way, and an
    answer that just says "nothing found" loses it.
    """
    named = dumps + not_dumps
    if not named:
        return []
    lines = [""]
    if dumps:
        lines.append(f"Core dumps: {dumps} of the {named} file(s) named like "
                     "one are ELF core files (listed above); the bytes "
                     f"disagreed with the name on the other {named - dumps}.")
    else:
        # **Not "0 of 50 ... the other 50 are not"**, which reads as two
        # different groups. With no dump found the honest sentence is that the
        # name was wrong every time - and that is the measured case here (50
        # files named `core.*` on this box, none a dump).
        lines.append(f"Core dumps: none of the {named} file(s) named like one "
                     "is an ELF core file, so the bytes disagreed with the "
                     "name in every case.")
    lines.append("`core.js` and a directory called `core` are why this family "
                 "is decided by its bytes rather than by its name.")
    return lines


def _run_skill(arguments: dict) -> str:
    raw = str(arguments.get("path") or "").strip()
    try:
        top = files.expand(raw or "~")
        if not top.is_dir():
            return f"{top} is not a directory, so there is nothing to look for."
    except files.PathProblem as exc:
        return str(exc)

    # The default is for an absent argument and for nothing else: `0 or 25`
    # would silently turn "show me nothing" into the default.
    if arguments.get("limit") in (None, ""):
        limit = _DEFAULT_LIMIT
    else:
        try:
            limit = int(arguments["limit"])
        except (TypeError, ValueError):
            return f"{arguments['limit']!r} is not a count."
        limit = max(1, min(limit, _LIMIT_MAX))

    (junk, examined, unreadable, truncated, unentered,
     named_not_files, named_not_dumps) = _walk(top, _CEILING)

    if not junk:
        lines = [f"No file under {top} carries a name this associates with "
                 "temporary or leftover files."]
        lines.append("")
        lines.append(f"That is a statement about the {examined} file(s) "
                     "examined and about names only - not a promise the tree "
                     "is clean. A leftover called `notes.txt` is invisible to "
                     "this, and a name is never proof of anything.")
        lines.extend(_guess_lines())
        lines.extend(_core_lines(0, named_not_dumps))
        lines.append("")
        lines.append("The name rules, so you can disagree with them:")
        for _key, label, rules in _FAMILIES:
            lines.append(f"  {label}: {rules}")
        lines.extend(_walk_notes(examined, truncated, unentered, unreadable,
                                 named_not_files))
        return "\n".join(lines)

    total = sum(size for _path, size, _family in junk)
    counts: dict = {}
    for _path, _size, family in junk:
        counts[family] = counts.get(family, 0) + 1

    lines = [f"{len(junk)} file(s) under {top} carry a name this associates "
             f"with temporary or leftover files - {files.human_size(total)} "
             "together."]
    tally = ", ".join(f"{label} {counts[key]}"
                      for key, label, _rules in _FAMILIES if counts.get(key))
    if tally:
        lines.append(f"  {tally}")
    lines.append("")

    shown, withheld = files.cap_list(junk, limit)
    for path, size, family in shown:
        lines.append(f"- {files.human_size(size):>10}  {path}  "
                     f"[{_LABELS[family]}]")
    note = files.withheld_note("file", withheld,
                               "Raise limit, or narrow the path.")
    if note:
        lines.append(f"  {note}")

    lines.extend(_guess_lines())
    lines.extend(_core_lines(counts.get("core", 0), named_not_dumps))

    lines.append("")
    lines.append("The name rules, so you can disagree with them:")
    for _key, label, rules in _FAMILIES:
        lines.append(f"  {label}: {rules}")

    lines.extend(_walk_notes(examined, truncated, unentered, unreadable,
                             named_not_files))
    return "\n".join(lines)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "temp_files",
        "description": (
            "Find junk, temporary and leftover files by NAME - editor swap and "
            "lock files (*.tmp, *.swp, *~, .#*, ~$*, .goutputstream-*), OS "
            "metadata (.DS_Store, Thumbs.db, desktop.ini), unfinished "
            "downloads (*.crdownload, *.part, *.filepart), merge leftovers "
            "(*.orig, *.rej) and core dumps. Use for 'what junk is in here', "
            "'what can I clean up', 'what temporary files do I have'. A name "
            "is a guess and the answer says so: nothing is deleted, moved or "
            "changed, and a file this cannot read is reported rather than "
            "counted as clean. Core dumps are confirmed by reading the ELF "
            "header, because a name like `core` is usually a source directory "
            "or a JavaScript module. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": ("Directory to search. Defaults to "
                                         "your home directory.")},
                "limit": {"type": "integer",
                          "description": ("How many to show, 1-200. Default "
                                          "25.")},
            },
        },
    },
}

SKILLS = [Skill(name="temp_files", schema=SCHEMA, run=_run_skill)]
