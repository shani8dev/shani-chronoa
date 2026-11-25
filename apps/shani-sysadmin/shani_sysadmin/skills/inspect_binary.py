"""Skill: what is this binary, and can it run?

`explain_command` says what a command *does* from its man page. Nothing said
what a file *is*: a user with a program that will not start ("why does it say
`error while loading shared libraries`?") had no way to ask what architecture
the file is, what it links against, or which of those libraries is missing.

`readelf` from `elfutils` answers all three and reads nothing but the file, with
no root and no execution. `elfutils` is in `shani-tools-extra`, so the binary is
on every image by design.

**Every shape below was measured on this machine**, which is how the two traps
here were found:

- **The exit code of a non-ELF file is 1, not 0.** Measured directly rather than
  through a pipe: `readelf -h /etc/skel/.bashrc` -> rc=1, ELF -> rc=0, missing
  file -> rc=1, a one-byte file -> rc=1. Measuring that through `head` gives 0,
  because that is `head`'s status.
- **`Type:` is not enough to tell an executable from a library.** Both
  `/usr/bin/true` and `libc.so.6` report `DYN`. What separates them is the
  parenthetical - `DYN (Position-Independent Executable file)` against
  `DYN (Shared object file)`. A parser that keys on the first word reports every
  shared library on the machine as an executable.
- **A shared library can carry an INTERP segment, so "has an interpreter" is not
  a discriminator either.** `libc.so.6` on this machine really does report
  `Requesting program interpreter: /lib64/ld-linux-x86-64.so.2`, while
  `ld-linux-x86-64.so.2` and `libEGL.so.1` do not. The kind is therefore read
  from the type text and the interpreter is reported as what it is: a fact, not
  a verdict.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import Skill

_TIMEOUT = 20

#: `readelf -h` prints a `Key:  value` table. "Machine" and "Model name" hold
#: spaces, so the value is everything after the *first* colon - the same rule
#: `system_info` uses for `lscpu`.
_FIELD = re.compile(r"^\s{2}([A-Za-z][A-Za-z0-9 /()-]*?):\s{2,}(.*)$")

#: The libraries a binary names, and the interpreter it asks for, both of which
#: decide whether it can run at all on this machine.
#:
#: **Measured: readelf brackets the interpreter in the line itself**, so the
#: whole line reads
#: `[Requesting program interpreter: /lib64/ld-linux-x86-64.so.2]`. A pattern
#: ending at the first whitespace after the path keeps the closing bracket and
#: prints `loaded by /lib64/ld-linux-x86-64.so.2]` - caught by running the
#: skill, not by reading the pattern. Non-greedy up to the bracket is what
#: actually matches the tool.
_NEEDED = re.compile(r"Shared library:\s*\[([^\]]+)\]")
_INTERP = re.compile(r"Requesting program interpreter:\s*([^\]\s]+)")


def _readelf(*args: str) -> "tuple[str, str]":
    """(stdout, reason). `reason` is empty on success, never an exception."""
    try:
        proc = subprocess.run(["readelf", *args], capture_output=True,
                              text=True, timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return "", f"readelf did not answer within {_TIMEOUT}s"
    except OSError as exc:
        return "", str(exc)
    # **Measured, not assumed: a non-ELF file is rc=1.** Reading the exit status
    # through a pipe gives the pipe's status, which is how the docstring's own
    # "exits 0" claim came to be wrong for `pdffonts` in another skill.
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        return "", detail[-1] if detail else f"readelf exited {proc.returncode}"
    return proc.stdout or "", ""


def _header(path: Path) -> "tuple[dict, str]":
    text, problem = _readelf("-h", str(path))
    if problem:
        return {}, problem
    fields: "dict[str, str]" = {}
    for line in text.splitlines():
        match = _FIELD.match(line)
        if match:
            fields.setdefault(match.group(1).strip(), match.group(2).strip())
    if not fields:
        return {}, "readelf printed no header fields"
    return fields, ""


def _kind(fields: "dict[str, str]") -> str:
    """`DYN (Shared object file)` -> `shared object`.

    **The parenthetical, not the first word.** `/usr/bin/true` and
    `libc.so.6` are both `DYN`; only the text after it separates a position
    independent executable from a library, so a parser reading the first word
    calls every library on the machine an executable.
    """
    raw = fields.get("Type", "")
    inner = raw.partition("(")[2].partition(")")[0].strip()
    return inner.lower() or raw.lower() or "unknown"


def _libraries(path: Path) -> "tuple[list, str]":
    """Every `NEEDED` library, and the interpreter, as (libs, interpreter)."""
    dynamic, problem = _readelf("-d", str(path))
    if problem:
        return [], ""
    libs = _NEEDED.findall(dynamic)
    programs, _ = _readelf("-l", str(path))
    interp = _INTERP.search(programs)
    return libs, (interp.group(1) if interp else "")


def _missing(names: "list[str]") -> "list[str]":
    """Which of `names` this machine cannot find in a library path.

    Deliberately **not** `ldd`. `ldd` runs the binary's loader with the file -
    on an untrusted file that is executing it, and on any file it can hang - and
    it answers for one machine's layout rather than for the file. This looks the
    names up against the standard directories, which is a fact about the file and
    about this machine, and cannot execute anything.
    """
    roots = ["/lib", "/usr/lib", "/lib64", "/usr/lib64",
             "/usr/local/lib", "/run/ld.so.conf.d"]
    search: "list[str]" = []
    for root in roots:
        search.extend(str(p) for p in Path(root).glob("*") if p.is_dir())
    gone = []
    for name in names:
        if not any((Path(base) / name).exists() for base in search):
            gone.append(name)
    return gone


def _run(arguments: dict) -> str:
    raw = str(arguments.get("path") or "").strip()
    if not raw:
        return ("I need a file to look at. Ask about the full path to a program, "
                "for example 'what is /usr/bin/true made of'.")
    path = Path(files.expand(raw))
    if not path.is_absolute() or not path.exists():
        return f"There is no file at {path}. Nothing was guessed."

    if shutil.which("readelf") is None:
        # Through `files.tool_missing()`, not a hand-written package name. The
        # obvious name here is **wrong**: `elfutils` is a real Arch package
        # (Core, 0.196-1) and it is what `shani-tools-extra` already pulls in,
        # but the binary called `readelf` ships in **binutils** - elfutils
        # provides the `eu-*` spellings. A sentence naming elfutils sends
        # someone to install a package that does not contain the binary they
        # need, which is the same defect as wpctl->pipewire recorded in
        # AGENTS.md. The hint lives in `files._PACKAGE_HINTS` for that reason.
        # `purpose` is a noun phrase: `tool_missing` builds "Could not
        # <purpose>", so a clause here reads as "Could not what this program
        # is". Found by running it, which is the only way that shows up.
        return files.tool_missing("readelf", "read what this program is")

    fields, problem = _header(path)
    if problem:
        # rc=1 on a non-ELF file is the *common* case, not a failure to report
        # as a crash: a text file or a script is not a binary at all, and saying
        # so is the answer.
        return (f"{path.name} is not an ELF binary - readelf said "
                f"{problem!r}. That is normal for a text file, a script, or "
                "anything that is not a compiled program.")

    kind = _kind(fields)
    # **Measured: readelf's own error text already ends in a colon**, so joining
    # with ": " produced `true:; ELF64; ...` on the first real run. The `Machine`
    # field likewise is a free-standing phrase, not a clause, so the parts are
    # commas. A header that reads as broken is a header nobody trusts.
    bits = [path.name, fields.get("Class", "unknown class"),
            f"{fields.get('Machine', 'unknown machine')}, {kind}"]

    libs, interp = _libraries(path)
    if interp:
        bits.append(f"loaded by {interp}")

    lines = [", ".join(bits)]

    if libs:
        lines.append("")
        lines.append(f"It needs {len(libs)} shared librar"
                     + ("y" if len(libs) == 1 else "ies") + ":")
        gone = set(_missing(libs))
        for name in libs:
            lines.append(f"  {name}" + ("  <-- NOT FOUND on this machine"
                                        if name in gone else ""))
        if gone:
            lines.append("")
            lines.append(f"{len(gone)} of them cannot be found in this "
                         "machine's library paths, which is what produces "
                         "'error while loading shared libraries'.")
    else:
        lines.append("")
        lines.append("It links no shared libraries (statically linked, or it is "
                     "a shared object others link against).")

    return "\n".join(lines)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "inspect_binary",
        "description": (
            "What a compiled program is and whether it can run here: 32- or "
            "64-bit, which CPU architecture, whether it is an executable or a "
            "shared library, the dynamic loader it needs, and every shared "
            "library it requires - naming any that are missing from this "
            "machine, which is what 'error while loading shared libraries' "
            "means. Answers 'what architecture is this', 'what is this file', "
            "'why won't this program start'. Reads only the file; it is never "
            "executed and never run with root."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Full path to the program or library to inspect.",
                },
            },
            "required": ["path"],
        },
    },
}

SKILLS = [Skill(name="inspect_binary", schema=SCHEMA, run=_run)]