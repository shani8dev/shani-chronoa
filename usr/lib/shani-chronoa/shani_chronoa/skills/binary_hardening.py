"""Skill: is this binary hardened?

`inspect_binary` answers the questions a broken executable raises - which
architecture, which libraries, which interpreter. None of those is the question
someone asks about a program they are about to run:

> **"Is this binary hardened?"**

Nothing in this tree answered it. `security_status` reports the machine's own
posture and `disk_health` its disks; a per-file verdict about one executable is a
different question, and the one that decides whether an unknown download is worth
running at all. The answer is five properties, each of which turns a class of
exploit off:

| property | what it stops |
|---|---|
| **PIE** | code being loaded at a fixed address, so an overwrite is predictable |
| **RELRO** | the GOT being writable after startup, so a write primitive redirects a call |
| **NOW** (full RELRO) | lazy binding, so those relocations are all done before main |
| **stack canary** | a plain stack overflow running past the return address |
| **NX** (no-execute stack) | code being run off the stack at all |

**Every one of them is read out of the real `readelf` output on this machine,
because every one of them has a case that parses differently and a parser
written from the common case is wrong on the rest.** Measured here:

- `/bin/ls`, `/bin/cat` - PIE + RELRO + NOW + canary, the hardened picture;
- `/usr/bin/python3`, `/usr/bin/gcc` - **`Type: EXEC`**, so not PIE, and no
  `BIND_NOW`; a DYN shared object and a PIE executable are *both* `DYN` in the
  header, and the parenthesised text is the only thing that separates them;
- `libc.so.6` - `DYN (Shared object file)`, which is not a PIE executable at
  all, so "is it PIE" has to ask the question in those words;
- a `gcc -Wl,-z,execstack` build - `GNU_STACK ... RWE`, the one line where NX is
  off, and it is a *flag* column rather than a segment name;
- a `REL` object file - **no `GNU_STACK` segment at all**, so "no GNU_STACK" is
  not "NX is off", it is "there is nothing to execute from yet".

**Nonexistent and disabled are kept apart, because they are opposite claims.**
No `GNU_STACK` on a relocatable object means the question does not apply until
it is linked; a `GNU_STACK` line without the `E` flag means the stack is marked
non-executable. Reporting either as "NX off" would be a confident wrong answer
about a file the next link step changes on its own.

**The stack canary is read from the *dynamic* symbol table, and the symbol is
`UND`.** Measured: `/bin/ls` carries `__stack_chk_fail@GLIBC_2.4` as an
**undefined** import, because the check itself lives in libc - a binary that
protects its stack *calls out* for the canary. Reading `.symtab` instead (the
obvious choice, and the one that also catches `__stack_chk_fail` when it is
statically linked) misses every dynamically linked one, which on Arch is
essentially all of them.

Read-only: `readelf` prints, nothing is written and nothing is loaded.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import List, Optional, Tuple

from shani_chronoa.skills import Skill

_TIMEOUT = 60

#: `readelf -h`'s Type line. The header word is what matters and the parenthesised
#: text disambiguates the two `DYN` cases - measured: a PIE executable and a
#: shared object are both `DYN`, and only the parenthetical tells them apart.
_TYPE = re.compile(r"^\s*Type:\s*(\S+)\s*(?:\(([^)]*)\))?", re.M)

#: The dynamic symbol table's canary. `UND` is the normal state, so no filter on
#: the binding is applied - requiring a *defined* symbol here is what makes a
#: parser miss every dynamically linked binary.
_CANARY = re.compile(r"__stack_chk_fail")

#: `RWE` on GNU_STACK means the stack is writable AND executable, so NX is off.
#: Matched as a flag column (`\bRWE\b`), not as a substring of the whole line,
#: because an `R` or an `E` alone appears in ordinary segments and a substring
#: test over the line would call half of them exec-stack.
_EXECSTACK = re.compile(r"\bRWE\b")

#: A judgement, and the reason for it, per property. `None` means "not
#: applicable", which is deliberately distinct from `False`.
YES = True
NO = False
NA = None


def _run_readelf(path: str, *flags: str) -> Tuple[Optional[str], str]:
    """readelf's stdout, or `(None, why)`. `-W` is used everywhere for wide
    output: without it readelf truncates long field names and wraps lines, which
    is how a parser starts matching on columns that are not there."""
    if shutil.which("readelf") is None:
        return None, ("readelf is not installed, so the binary could not be "
                      "inspected. It comes with the binutils package.")
    try:
        proc = subprocess.run(["readelf", "-W", *flags, path],
                              capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return None, f"readelf did not finish within {_TIMEOUT}s."
    except OSError as exc:
        return None, f"readelf could not be run ({exc})."
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        # readelf's own wording names the reason - "not an ELF dynamic
        # executable file" - and guessing from the status is how a text file
        # gets reported as an unhardened binary.
        return None, (detail[-1] if detail else f"readelf exited {proc.returncode}")
    return proc.stdout or "", ""


def _hardening(path: str) -> Tuple[dict, List[str]]:
    """The five properties as `(verdict, reason)` pairs, plus what is unknown."""
    out: dict = {}
    unknown: List[str] = []

    header, why = _run_readelf(path, "-h")
    if header is None:
        return {}, [why]
    m = _TYPE.search(header)
    if not m:
        out["pie"] = NA
        unknown.append("the ELF type could not be read, so PIE is not known")
        etype = ""
    else:
        etype, detail = m.group(1), (m.group(2) or "")
        # A shared object is DYN but is not a PIE *executable*; saying so is the
        # difference between an answer and a wrong one about a library.
        if etype == "REL":
            out["pie"] = NA
            unknown.append("this is a relocatable object, not something that "
                           "is executed on its own")
        elif "Shared object" in detail:
            out["pie"] = NA
            unknown.append("this is a shared library, not an executable")
        else:
            out["pie"] = etype == "DYN"

    programs, why = _run_readelf(path, "-lW")
    if programs is None:
        unknown.append(f"the program headers could not be read ({why})")
        out["nx"] = NA
        out["relro"] = NA
    else:
        stack_line = next((ln for ln in programs.splitlines()
                           if "GNU_STACK" in ln), None)
        if stack_line is None:
            out["nx"] = NA
            unknown.append("no GNU_STACK segment, so there is no stack to mark "
                           "yet - it appears when the file is linked")
        else:
            out["nx"] = not _EXECSTACK.search(stack_line)
        out["relro"] = "GNU_RELRO" in programs

    dynamic, why = _run_readelf(path, "-dW")
    if dynamic is None:
        # No dynamic section at all is a normal state for a static binary, and
        # a *different* one from "lazy binding is on".
        unknown.append(f"the dynamic section could not be read ({why}), so full "
                       "RELRO (BIND_NOW) is not known")
        out["now"] = NA
    else:
        # NOW can appear as its own tag or inside FLAGS/FLAGS_1; both were seen
        # in real output and either means the same thing.
        out["now"] = bool(re.search(r"\bBIND_NOW\b", dynamic)
                          or re.search(r"Flags:[^\n]*\bNOW\b", dynamic))

    dynsyms, why = _run_readelf(path, "--dyn-syms")
    if dynsyms is None:
        unknown.append(f"the dynamic symbols could not be read ({why}), so a "
                       "stack canary is not known")
        out["canary"] = NA
    else:
        out["canary"] = bool(_CANARY.search(dynsyms))

    return out, unknown


_LABELS = [
    ("pie", "PIE", "loaded at a random address (ASLR works on it)"),
    ("relro", "RELRO", "the GOT is made read-only after startup"),
    ("now", "Full RELRO", "no lazy binding - relocations finish before main"),
    ("canary", "Stack canary", "a stack overflow hits a canary before the return address"),
    ("nx", "No-execute stack", "the stack is not executable"),
]


def _describe(path: str) -> str:
    verdicts, unknown = _hardening(path)
    if not verdicts:
        return (f"Could not inspect {path}: {unknown[0]}")

    lines = [f"{path}"]
    # For a relocatable object the five properties are all "not applicable yet"
    # rather than a mix of yes and no, and listing "NO  RELRO" beside
    # "not applicable here" for the same file is the mixed reading a reader
    # cannot act on. `_hardening` records the ELF type, so this asks about what
    # it already read rather than re-reading the header.
    relocatable = any("relocatable object" in u for u in unknown)
    if relocatable:
        lines.append("  A relocatable object: none of the five applies until "
                     "this is linked into an executable.")
        for _, name, _ in _LABELS:
            lines.append(f"  --   {name} - decided when the file is linked")
        return "\n".join(lines)
    for key, name, why_good in _LABELS:
        v = verdicts.get(key, NA)
        if v is YES:
            lines.append(f"  yes  {name} - {why_good}")
        elif v is NO:
            lines.append(f"  NO   {name} - not present")
        else:
            lines.append(f"  --   {name} - not applicable here")

    # A relocatable object is not something that is executed, so "it has no
    # canary" is a category error rather than a finding: the properties belong
    # to the file the linker produces, not to this one. Measured: `gcc -c` gave
    # a `.o` with no RELRO, no BIND_NOW and no canary, and a first version listed
    # all three under "NOT hardened against" - a confident wrong answer about a
    # file whose state changes the moment it is linked.
    relocatable = "relocatable object" in " ".join(unknown)
    off = [name for key, name, _ in _LABELS
           if verdicts.get(key) is NO and not relocatable]
    na = [name for key, name, _ in _LABELS if verdicts.get(key) is NA]
    unknown = [u for u in unknown if not any(n in u for n in na)]

    if relocatable:
        lines.append("")
        lines.append("This is a relocatable object, so none of the five has a "
                     "meaning yet: they belong to the executable the linker "
                     "produces from it. Ask again about that file.")
    elif off:
        lines.append("")
        lines.append(f"NOT hardened against: {', '.join(off)}."
                     " A binary without these is still a normal program; this "
                     "is a fact about it, not a verdict on whether to run it.")
    elif not na:
        lines.append("")
        lines.append("All five protections are present.")
    else:
        lines.append("")
        lines.append("No protection is missing, but "
                     f"{', '.join(na)} could not be judged here.")
    for note in unknown:
        lines.append(f"  note: {note}")
    return "\n".join(lines)


def _run(arguments: dict) -> str:
    path = (arguments or {}).get("path", "")
    if not isinstance(path, str) or not path.strip():
        return ("Which binary? Give the path to an executable or a shared "
                "library and I will say which of the five protections it has.")
    return _describe(path.strip())


SCHEMA = {
    "type": "function",
    "function": {
        "name": "binary_hardening",
        "description": (
            "Report whether a binary is hardened: PIE, RELRO, full RELRO "
            "(BIND_NOW), stack canary and no-execute stack, each with what it "
            "stops. Use for 'is /path/to/this safe to run', 'is this binary "
            "hardened', 'does this have a stack canary', 'is this ASLR "
            "enabled'. Works on executables and shared libraries alike, and says "
            "'not applicable' where a property genuinely has no answer rather "
            "than reporting it as missing. Read-only; nothing is loaded or "
            "written."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": ("The binary or shared library to inspect, "
                                    "e.g. '/usr/bin/ls' or './myapp'."),
                },
            },
            "required": ["path"],
        },
    },
}

SKILLS = [Skill(name="binary_hardening", schema=SCHEMA, run=_run)]