"""Format routing for archives, measured on a real ShaniOS slot.

`extract_archive` reads tar and zip with Python's stdlib and delegates
everything else to `7z`. That is right for most formats and **wrong for two**,
and both wrongnesses were found by running the tools on `@blue` rather than by
reading a manual.

**Measured 2026-10-10 on `@blue`, by `slot-tests/chronoa-cli-formats.sh`:**

- The image's `7z` is **7-Zip 26.03**, and `7z i` lists `Rar`, `Rar1`, `Rar2`,
  `Rar3` and `Rar5` among its formats - so a `.rar` really is readable here,
  and the claim in `extract_archive`'s docstring is a measurement rather than
  a hope. (Arch's `p7zip` has no RAR decoder; the image ships `7zip`.)
- `7z` reads a real `.arj` created by `arj` (2 members listed) - so testing
  7z against a `.7z` was never the evidence, only 7z reading its own output.
- **`7z l -slt` returns rc=2 on a `.lzo` and on a `.lrz`** that `lzop` and
  `lrzip` had just created themselves. 7-Zip reads neither format, so both
  were refused by `extract_archive` while the tools that can open them sat
  installed in the same image.

**And the shapes of the two tools that can open them:**

    lzop -l FILE     method  compressed  uncompr.  ratio  uncompressed_name
                     LZO1X-1      21         21   100.0%  /var/tmp/...
    lzop -d -c FILE  -> the payload on stdout (21 bytes measured)

    lrzip -t FILE    "Decompressing... 100%  21.00 / 21.00"
                     **This is not a listing.** `-t` *decompresses* to measure,
                     so it prints progress and says nothing about the members.
                     Reaching for it as a "list" is the obvious move and it is
                     the wrong one.
    lrzcat FILE      -> the payload on stdout (21 bytes measured)

So `.lzo` has a real listing (`lzop -l`) and `.lrz` does not (`lrzcat` is the
only streaming reader). Both are single-stream compressors rather than
archives: each file is one compressed *file*, so "what is inside" is the
original name and its size - which is what `lzop -l` prints and what `lrzip`
does not.

**Three tools that ship here are *not* wired to anything, and one of them is
not the universal reader it looks like.** `arj`, `unar` and `lsar` all have
entries in `files._PACKAGE_HINTS` and no caller in this package - which is
worth stating here because this module is where somebody adding a format
would look first, and the obvious reach is "send it to `unar`, it does
everything".

Measured on `@blue` against a real `.arj` that `arj` had just created:

| tool | exit | read the `.arj`? |
|---|---|---|
| `7z` (bare `l`) | 0 | **yes** |
| `arj l` | 0 | yes |
| `lsar` | 0 | yes |
| **`unar -l`** | **1** | **no** |

So `unar` refuses a format `7z` opens without complaint, and it is therefore
not a safe fallback for "something 7z cannot read" - a routing rule built on
it would fail on exactly the inputs it was added for. `lsar` and `arj l` both
work, and `7z` remains the right default, which is what `NEEDS_OWN_TOOL`
above already encodes for the two formats measured against it.

**Each reader also takes a different listing flag, and none of the three
accepts `-l` except `unzip`.** This is a trap because the failure is silent:

    unzip -l FILE     ok
    7z l FILE         ok          <- a bare subcommand, not `-l`
    7za l FILE        ok
    bsdtar -tf FILE   ok          <- `-tf`, not `-l`

All three wrong spellings exit **non-zero with no output at all** - exactly
what "this archive is unreadable" looks like. A check written against the
wrong spelling reports a perfectly good reader as unable to open the file;
that happened here, in `chronoa-cli-formats.sh`, and produced three FAILs
against tools that read zip perfectly.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

#: Extensions 7-Zip was **measured** unable to open, mapped to the tool that
#: can. Both entries come from rc=2 against archives their own tools had
#: created, so this is an observation rather than a guess about which codecs a
#: given build ships.
NEEDS_OWN_TOOL = {
    ".lzo": "lzop",
    ".lzo.d": "lzop",
    ".lrz": "lrzcat",
}

#: `lzop -l` prints a fixed-width table; the name is its last column.
_LZOP_ROW = re.compile(
    r"^(LZO\w*-\d)\s+(\d+)\s+(\d+)\s+([\d.]+%)\s+(.*\S)\s*$")

_TIMEOUT = 60


def needs_own_tool(path: Path) -> bool:
    """Whether `path` is a format 7-Zip was measured unable to open."""
    name = path.name.lower()
    return any(name.endswith(extension) for extension in NEEDS_OWN_TOOL)


def _run(command: "list[str]") -> "tuple[str, str, int]":
    try:
        proc = subprocess.run(command, capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return "", f"{command[0]} did not answer within {_TIMEOUT}s", 124
    except OSError as exc:
        return "", str(exc), 127
    return proc.stdout or "", proc.stderr or "", proc.returncode


def lzop_members(path: Path) -> "tuple[list, str]":
    """([(name, size)], refusal) for a `.lzo`, via `lzop -l`.

    **A `.lzo` is one compressed file, not an archive** - lzop is a
    single-stream compressor - so there is exactly one member, named by the
    `uncompressed_name` column.
    """
    if shutil.which("lzop") is None:
        return [], ("lzop is not installed, and 7z cannot read .lzo either "
                    "(measured: rc=2 on this image)")
    out, err, rc = _run(["lzop", "-l", str(path)])
    if rc == 124:
        return [], err
    if rc != 0:
        detail = (err or out).strip().splitlines()
        return [], detail[-1] if detail else f"lzop exited {rc}"
    rows = []
    for line in out.splitlines():
        match = _LZOP_ROW.match(line.strip())
        if match:
            rows.append((match.group(5).strip(), int(match.group(3))))
    if not rows:
        return [], "lzop printed a table this does not read"
    return rows, ""


def lrzip_members(path: Path) -> "tuple[list, str]":
    """([(name, size)], refusal) for a `.lrz`.

    **`lrzip -t` is deliberately not used.** Measured: it decompresses in order
    to measure and prints `Decompressing... 100%` - it is a test, not a
    listing. The only streaming reader is `lrzcat`, which yields the payload
    rather than a member list, so the single member is named after the file
    itself and sized by the payload.
    """
    if shutil.which("lrzcat") is None:
        return [], ("lrzcat is not installed, and 7z cannot read .lrz either "
                    "(measured: rc=2 on this image)")
    out, err, rc = _run(["lrzcat", str(path)])
    if rc == 124:
        return [], err
    if rc != 0:
        detail = (err or out).strip().splitlines()
        return [], detail[-1] if detail else f"lrzcat exited {rc}"
    name = path.name
    if name.lower().endswith(".lrz"):
        name = name[:-4]
    return [(name, len(out.encode("utf-8", "replace")))], ""


def members(path: Path) -> "tuple[list, str]":
    """The right reader for `path`.

    `(members, refusal)` where each member is `(name, size)`. An empty
    refusal means the file was read. **Returns `([], "")` for any format 7-Zip
    can handle** - this module only knows about the two it cannot, and
    routing everything else here would be a second, worse guess.
    """
    name = path.name.lower()
    if name.endswith((".lzo", ".lzo.d")):
        return lzop_members(path)
    if name.endswith(".lrz"):
        return lrzip_members(path)
    return [], ""