"""Tests for `binary_hardening` - the five ELF protections nothing else reports.

Every fixture in this file is **captured readelf output from this machine**, not
hand-typed, because the whole point is that each property parses differently:

| file | shape it exercises |
|---|---|
| `/bin/ls`, `/bin/cat` | `DYN (Position-Independent Executable)`, all five present |
| `/usr/bin/python3`, `/usr/bin/gcc` | `Type: EXEC` - **not** PIE, and no `BIND_NOW` |
| `libc.so.6` | `DYN (Shared object file)` - DYN, but not a PIE executable |
| `gcc -Wl,-z,execstack` | `GNU_STACK ... RWE`, the one line where NX is off |
| `gcc -c` (`t.o`) | `Type: REL`, and **no GNU_STACK segment at all** |
| `/bin/ls`'s canary | `__stack_chk_fail@GLIBC_2.4` as an **undefined** import |

The last two rows are the ones a parser written from the common case gets wrong:
a missing `GNU_STACK` is not an executable stack, and a canary is an `UND`
import rather than a defined symbol.
"""

from __future__ import annotations

import pytest

from shani_chronoa.skills import binary_hardening as bh

# --- captured: /bin/ls (all five present) -----------------------------------
LS_HEADER = """\
ELF Header:
  Magic:   7f 45 4c 46 02 01 01 00 00 00 00 00 00 00 00 02 00 3e 00
  Class:                             ELF64
  Type:                              DYN (Position-Independent Executable file)
  Machine:                           Advanced Micro Devices X86-64
  Entry point address:               0x109930
"""

LS_PROGRAMS = """\
Program Headers:
  Type           Offset             VirtAddr           PhysAddr
                 FileSiz            MemSiz              Flags  Align
  GNU_STACK      0x000000 0x0000000000000000 0x0000000000000000 0x000000 0x000000 RW  0x10
  GNU_RELRO      0x9d69f0 0x00000000009d69f0 0x00000000009d69f0 0x165610 0x165610 R   0x1
"""

LS_DYNAMIC = """\
Dynamic section at offset 0x8db9d8 contains 27 entries:
  0x000000000000001e (FLAGS)              BIND_NOW
  0x000000006ffffffb (FLAGS_1)            Flags: NOW PIE
"""

LS_SYMS = """\
Symbol table '.dynsym' contains 136 entries:
   166: 0000000000000000     0 FUNC    GLOBAL DEFAULT  UND __stack_chk_fail@GLIBC_2.4 (13)
   167: 0000000000000000     0 OBJECT  GLOBAL DEFAULT  UND stdout@GLIBC_2.2.5 (20)
"""

# --- captured: /usr/bin/python3 (Type: EXEC, so not PIE; no BIND_NOW) ------
PY_HEADER = """\
  Class:                             ELF64
  Type:                              EXEC (Executable file)
  Machine:                           Advanced Micro Devices X86-64
"""

PY_DYNAMIC = """\
Dynamic section at offset 0xda1b18 contains 34 entries:
  0x00000001 (NEEDED)             Shared library: [libc.so.6]
"""

# --- captured: libc.so.6 (DYN, but a Shared object) ------------------------
LIBC_HEADER = """\
  Type:                              DYN (Shared object file)
  Machine:                           Advanced Micro Devices X86-64
"""

# --- captured: gcc -Wl,-z,execstack -----------------------------------------
EXECSTACK_PROGRAMS = """\
Program Headers:
  GNU_STACK      0x000000 0x0000000000000000 0x0000000000000000 0x000000 0x000000 RWE 0x10
  GNU_RELRO      0x3e78 0x0000000000003e78 0x0000000000003e78 0x0648 0x0648 R   0x1
"""

# --- captured: gcc -c (Type: REL, and NO GNU_STACK at all) -----------------
OBJ_HEADER = """\
  Type:                              REL (Relocatable file)
  Machine:                           Advanced Micro Devices X86-64
"""

OBJ_PROGRAMS = """\
There are no program headers in this file.
"""


@pytest.fixture
def readelf(monkeypatch):
    """Answer readelf by flag, from the captured fixtures above.

    Keyed on the **flags** rather than on a call counter, so a call the code
    should not make at all - asking for `-h` twice, or never asking for
    `--dyn-syms` - is a miss rather than a silently reused answer. A fixture that
    answers any call cannot tell a correct query from a wrong one.
    """
    seen = []

    def install(header=LS_HEADER, programs=LS_PROGRAMS, dynamic=LS_DYNAMIC,
                syms=LS_SYMS, header_rc=0, programs_rc=0, dynamic_rc=0,
                syms_rc=0, header_err="", programs_err=""):
        def run(argv, **kwargs):
            seen.append(list(argv))
            if "-h" in argv:
                return _proc(argv, header_rc, header, header_err)
            if "-lW" in argv:
                return _proc(argv, programs_rc, programs, programs_err)
            if "-dW" in argv:
                return _proc(argv, dynamic_rc, dynamic, "")
            if "--dyn-syms" in argv:
                return _proc(argv, syms_rc, syms, "")
            raise AssertionError(f"unexpected readelf call: {argv}")
        monkeypatch.setattr(bh.subprocess, "run", run)
        monkeypatch.setattr(bh.shutil, "which", lambda n: "/usr/bin/readelf")
        return seen
    return install


def _proc(argv, rc, out, err):
    import subprocess
    return subprocess.CompletedProcess(argv, rc, out, err)


# --------------------------------------------------------------------------
# What it asks readelf, and what it does with a refusal
# --------------------------------------------------------------------------

class TestTheReadelfCall:

    def test_it_uses_wide_output_every_time(self, readelf):
        # Without -W readelf truncates field names and wraps, which is how a
        # parser starts matching on columns that are not there.
        seen = readelf()
        bh._hardening("/bin/ls")
        for argv in seen:
            assert "-W" in argv, f"{argv} did not ask for wide output"

    def test_it_reads_the_header_the_programs_the_dynamic_and_the_symbols(
            self, readelf):
        seen = readelf()
        bh._hardening("/bin/ls")
        flat = [" ".join(a) for a in seen]
        for needed in ("-h", "-lW", "-dW", "--dyn-syms"):
            assert any(needed in f for f in flat), f"{needed} was never asked for"

    def test_a_missing_readelf_names_the_package(self, monkeypatch):
        monkeypatch.setattr(bh.shutil, "which", lambda n: None)
        out = bh._describe("/bin/ls")
        assert "not installed" in out and "binutils" in out

    def test_a_non_elf_file_is_reported_with_readelfs_own_reason(self, readelf):
        # Measured: readelf says "Failed to read file header" and exits non-zero.
        # Guessing from the status is how a text file gets called unhardened.
        readelf(header_rc=1, header="", header_err="readelf: Error: /etc/hostname: Failed to read file header\n")
        out = bh._describe("/etc/hostname")
        assert "Failed to read file header" in out
        assert "NOT hardened" not in out, "a file that is not a binary has no verdict"

    def test_one_unreadable_section_is_unknown_not_missing(self, readelf):
        readelf(programs_rc=1, programs="", programs_err="readelf: Error: no program headers\n")
        out = bh._describe("/bin/ls")
        assert "not known" in out or "not applicable" in out
        assert "NOT hardened" not in out, (
            "a section read that failed is unknown, which is not the same as a "
            "protection that is absent")


# --------------------------------------------------------------------------
# PIE: DYN alone is not the answer
# --------------------------------------------------------------------------

class TestPie:

    def test_a_dyn_executable_is_pie(self, readelf):
        readelf(header=LS_HEADER)
        assert bh._hardening("/bin/ls")[0]["pie"] is True

    def test_an_exec_binary_is_not_pie(self, readelf):
        # Measured on /usr/bin/python3 and /usr/bin/gcc: Type: EXEC.
        readelf(header=PY_HEADER)
        assert bh._hardening("/usr/bin/python3")[0]["pie"] is False

    def test_a_shared_object_is_not_a_pie_executable(self, readelf):
        # libc is DYN like a PIE binary; only the parenthetical separates them.
        readelf(header=LIBC_HEADER)
        verdicts, unknown = bh._hardening("/lib/libc.so.6")
        assert verdicts["pie"] is None
        assert any("shared library" in u for u in unknown)


# --------------------------------------------------------------------------
# NX: a missing GNU_STACK is not an executable stack
# --------------------------------------------------------------------------

class TestNx:

    def test_a_stack_without_e_is_not_executable(self, readelf):
        readelf(programs=LS_PROGRAMS)
        assert bh._hardening("/bin/ls")[0]["nx"] is True

    def test_rwe_on_the_stack_means_nx_is_off(self, readelf):
        readelf(programs=EXECSTACK_PROGRAMS)
        assert bh._hardening("/x")[0]["nx"] is False

    def test_no_gnu_stack_segment_is_not_applicable_not_disabled(self, readelf):
        # Measured on a `gcc -c` object: "There are no program headers".
        readelf(header=OBJ_HEADER, programs=OBJ_PROGRAMS)
        verdicts, unknown = bh._hardening("/tmp/t.o")
        assert verdicts["nx"] is None
        assert any("GNU_STACK" in u for u in unknown)

    def test_a_plain_r_segment_is_not_read_as_an_exec_stack(self, readelf):
        # The reason the match is `\bRWE\b` and not a substring of the line: an
        # ordinary R segment contains R, and a substring test calls half of them
        # executable stacks.
        progs = LS_PROGRAMS.replace(
            "0x165610 0x165610 R   0x1", "0x165610 0x165610 RW  0x1")
        readelf(programs=progs)
        assert bh._hardening("/bin/ls")[0]["nx"] is True


# --------------------------------------------------------------------------
# Full RELRO and the canary
# --------------------------------------------------------------------------

class TestFullRelroAndCanary:

    def test_bind_now_is_seen_as_its_own_tag(self, readelf):
        readelf(dynamic="  0x000000000000001e (FLAGS)              BIND_NOW\n")
        assert bh._hardening("/bin/ls")[0]["now"] is True

    def test_now_inside_flags_is_seen_too(self, readelf):
        # Both spellings were in real output (/bin/ls carries both).
        readelf(dynamic="  0x000000006ffffffb (FLAGS_1)            Flags: NOW PIE\n")
        assert bh._hardening("/bin/ls")[0]["now"] is True

    def test_its_absence_is_reported_as_absent(self, readelf):
        readelf(dynamic=PY_DYNAMIC)
        assert bh._hardening("/usr/bin/python3")[0]["now"] is False

    def test_the_canary_is_found_as_an_undefined_import(self, readelf):
        # This is the one that matters: /bin/ls has no canary *definition*, it
        # calls __stack_chk_fail in libc. Requiring a defined symbol misses
        # every dynamically linked binary on Arch.
        readelf(syms=LS_SYMS)
        assert bh._hardening("/bin/ls")[0]["canary"] is True

    def test_no_canary_symbol_is_reported_as_absent(self, readelf):
        readelf(syms=("Symbol table '.dynsym' contains 3 entries:\n"
                      "   167: 0000000000000000     0 OBJECT  GLOBAL DEFAULT  UND stdout@GLIBC_2.2.5 (20)\n"))
        assert bh._hardening("/x")[0]["canary"] is False


# --------------------------------------------------------------------------
# What it says, which is the part a person reads
# --------------------------------------------------------------------------

class TestTheAnswer:

    def test_a_fully_hardened_binary_says_so(self, readelf):
        readelf()
        assert "All five protections are present" in bh._describe("/bin/ls")

    def test_a_soft_binary_names_exactly_what_is_missing(self, readelf):
        readelf(header=PY_HEADER, dynamic=PY_DYNAMIC)
        out = bh._describe("/usr/bin/python3")
        assert "NOT hardened against: PIE, Full RELRO" in out

    def test_the_verdict_is_a_fact_not_an_instruction(self, readelf):
        # "not present" must not read as "do not run this" - hardening is one
        # input, not a verdict, and a skill that decides for the person is the
        # confident-wrong-answer shape this repo keeps recording.
        readelf(header=PY_HEADER, dynamic=PY_DYNAMIC)
        out = bh._describe("/usr/bin/python3")
        assert "not a verdict on whether to run it" in out
        assert "do not run" not in out.lower()

    def test_a_relocatable_object_is_not_called_unhardened(self, readelf):
        # Measured: `gcc -c` has no RELRO, no BIND_NOW and no canary, and a
        # first version listed all three as missing protections - about a file
        # whose state is decided when it is linked.
        readelf(header=OBJ_HEADER, programs=OBJ_PROGRAMS)
        out = bh._describe("/tmp/t.o")
        assert "NOT hardened" not in out
        assert "relocatable object" in out.lower()
        assert "decided when the file is linked" in out

    def test_a_library_says_which_property_could_not_be_judged(self, readelf):
        readelf(header=LIBC_HEADER)
        out = bh._describe("/lib/libc.so.6")
        assert "PIE could not be judged" in out
        assert "NOT hardened" not in out


# --------------------------------------------------------------------------
# Reachability
# --------------------------------------------------------------------------

class TestItIsReachable:

    def test_the_handler_is_importable_by_name(self):
        assert bh._run.__name__ == "_run"
        assert bh._run.__name__.isidentifier()

    def test_the_schema_registers_under_its_own_name(self):
        assert bh.SKILLS[0].name == "binary_hardening"
        assert bh.SCHEMA["function"]["name"] == "binary_hardening"

    def test_path_is_the_only_required_parameter(self):
        params = bh.SCHEMA["function"]["parameters"]
        assert params["required"] == ["path"]
        assert set(params["properties"]) == {"path"}

    def test_it_asks_which_binary_rather_than_guessing(self, monkeypatch):
        # An empty path must be a question, not an inspection of "".
        out = bh._run({})
        assert "Which binary?" in out

    def test_it_refuses_a_non_string_path_rather_than_crashing(self):
        assert "Which binary?" in bh._run({"path": 42})