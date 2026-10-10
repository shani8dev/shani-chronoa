"""`inspect_binary`: what a compiled program is, and can it run here.

Nothing answered this. `explain_command` says what a command *does*; a user with
a program that will not start - "error while loading shared libraries" - had no
way to ask what architecture the file is, or which library is missing.

`readelf` from `elfutils` (a `shani-tools-extra` package) reads all of it without
root and **without executing the file**.

**Three facts here were measured on this machine, and two of them contradict the
obvious parse:**

- A **non-ELF file exits 1**, not 0. Measured directly; measuring it through a
  pipe returns the *pipe's* status, which is how `read_document`'s `pdffonts`
  contract came to be recorded wrongly.
- **`Type:` alone does not separate an executable from a library.** `/usr/bin/true`
  and `libc.so.6` are both `DYN`; only the parenthetical differs.
- **`readelf` brackets the interpreter in the line**, so a pattern stopping at
  whitespace keeps the `]`. Found by running the skill, not by reading it.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import inspect_binary as IB  # noqa: E402

#: Real `readelf -h /usr/bin/true`, captured on this machine.
HEADER_EXEC = """ELF Header:
  Magic:   7f 45 4c 46 02 01 01 00 00 00 00 00 00 00 00 00
  Class:                             ELF64
  Data:                              2's complement, little endian
  Version:                           1 (current)
  OS/ABI:                            UNIX - System V
  ABI Version:                       0
  Type:                              DYN (Position-Independent Executable file)
  Machine:                           Advanced Micro Devices X86-64
  Version:                           0x1
  Entry point address:               0x2db0
"""

#: Real `readelf -h /lib/x86_64-linux-gnu/libc.so.6`. **Also `DYN`** - the only
#: difference from the executable above is inside the parentheses.
HEADER_LIB = """ELF Header:
  Magic:   7f 45 4c 46 02 01 01 00 00 00 00 00 00 00 00 00
  Class:                             ELF64
  Data:                              2's complement, little endian
  Type:                              DYN (Shared object file)
  Machine:                           Advanced Micro Devices X86-64
"""

PROGRAMS_EXEC = """  INTERP         0x000318 0x0000000000000318 0x0000000000000318 0x00001c 0x00001c R   0x1
      [Requesting program interpreter: /lib64/ld-linux-x86-64.so.2]
"""


def _fake_readelf(tmp_path, monkeypatch, header=HEADER_EXEC,
                  dynamic=" 0x0000000000000001 (NEEDED)             Shared library: [libc.so.6]\n",
                  programs=PROGRAMS_EXEC, rc=0, stderr=""):
    """A stand-in `readelf` on PATH, because the parse is the thing under test
    and the real binary's output is already captured above as the fixture."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    script = bindir / "readelf"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "args = sys.argv[1:]\n"
        f"if {rc!r}:\n"
        f"    sys.stderr.write({stderr!r})\n"
        f"    raise SystemExit({rc})\n"
        f"if '-h' in args:\n"
        f"    sys.stdout.write({header!r})\n"
        f"elif '-d' in args:\n"
        f"    sys.stdout.write({dynamic!r})\n"
        f"elif '-l' in args:\n"
        f"    sys.stdout.write({programs!r})\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")


def _real_file(tmp_path, name="target"):
    """A file that actually exists.

    The skill refuses a path that does not exist *before* calling readelf,
    which is correct - so a fixture naming `/bin/true` or `/lib/libc.so.6`
    measures the existence check instead of the parse. Every test below that
    exercises the parser points at a real file.
    """
    path = tmp_path / name
    path.write_bytes(b"\x7fELF" + b"\x00" * 32)
    return path


def test_an_executable_and_a_library_are_not_the_same_answer(tmp_path, monkeypatch):
    """**Both report `Type: DYN`.** A parser keying on the first word says every
    shared library on the machine is an executable - and this machine has
    thousands.
    """
    target = _real_file(tmp_path, "somename")

    _fake_readelf(tmp_path, monkeypatch, header=HEADER_EXEC)
    exe = IB._run({"path": str(target)})
    assert "position-independent executable file" in exe

    _fake_readelf(tmp_path, monkeypatch, header=HEADER_LIB)
    lib = IB._run({"path": str(target)})
    assert "shared object file" in lib
    assert "executable" not in lib


def test_the_type_is_read_from_the_parentheses(tmp_path, monkeypatch):
    fields = {"Type": "DYN (Position-Independent Executable file)"}
    assert "executable" in IB._kind(fields)
    assert "shared object" in IB._kind({"Type": "DYN (Shared object file)"})
    # And a plain EXEC, with no parenthetical at all.
    assert "executable file" in IB._kind({"Type": "EXEC (Executable file)"})


def test_the_interpreter_loses_readelfs_own_bracket(tmp_path, monkeypatch):
    """**Measured: readelf brackets the path in the line itself.** A pattern
    stopping at whitespace prints `loaded by /lib64/...so.2]`, which the first
    real run of this skill did.
    """
    _fake_readelf(tmp_path, monkeypatch)
    out = IB._run({"path": "/bin/true"})
    assert "/lib64/ld-linux-x86-64.so.2]" not in out
    assert "loaded by /lib64/ld-linux-x86-64.so.2" in out


def test_a_machine_name_holding_spaces_is_kept_whole(tmp_path, monkeypatch):
    """The header is a `Key:  value` table and `Machine` is a phrase; splitting
    on whitespace cuts it in half.
    """
    _fake_readelf(tmp_path, monkeypatch)
    out = IB._run({"path": str(_real_file(tmp_path, "somename"))})
    assert "Advanced Micro Devices X86-64" in out


def test_a_missing_library_is_named_as_the_cause(tmp_path, monkeypatch):
    """This is the question the skill exists for: `error while loading shared
    libraries` is a fact about a *named* library being absent, so a list that
    does not say which one has not answered it.
    """
    _fake_readelf(
        tmp_path, monkeypatch,
        dynamic=" 0x1 (NEEDED)  Shared library: [libnosuchthing.so.99]\n")
    out = IB._run({"path": str(_real_file(tmp_path, "broken"))})
    assert "NOT FOUND" in out
    assert "libnosuchthing.so.99" in out
    assert "error while loading shared libraries" in out


def test_a_library_that_is_present_is_not_flagged(tmp_path, monkeypatch):
    """A control for the missing-library test: the flag must be able to be
    absent, or the previous test proves nothing.
    """
    _fake_readelf(tmp_path, monkeypatch,
                  dynamic=" 0x1 (NEEDED)  Shared library: [libc.so.6]\n")
    out = IB._run({"path": str(_real_file(tmp_path, "fine"))})
    assert "NOT FOUND" not in out


def test_a_non_elf_file_is_an_answer_not_a_failure(tmp_path, monkeypatch):
    """**Measured: rc=1, and 1 is the normal answer for a file that is not a
    binary at all.** A text file is not a broken binary, and a skill that says
    "readelf failed" reads as a fault in the skill.
    """
    target = _real_file(tmp_path, "notes.txt")
    target.write_text("just some text, not a binary\n")
    _fake_readelf(tmp_path, monkeypatch, rc=1,
                  stderr="readelf: Error: Not an ELF file - it has the wrong "
                         "magic bytes at the start\n")
    out = IB._run({"path": str(target)})
    assert "not an ELF binary" in out
    assert "normal for a text file" in out
    # **And readelf's own words must survive.** Without this line, dropping the
    # return-code check altogether still passes this test: the generic
    # "printed no header fields" fallback produces the same two sentences
    # above, so the test was satisfied by a reason that was invented rather
    # than measured. That mutation ran green once before this line existed.
    assert "magic bytes" in out


def test_a_missing_file_is_refused_before_anything_runs(tmp_path, monkeypatch):
    out = IB._run({"path": str(tmp_path / "nothing-here")})
    assert "no file at" in out
    assert "Nothing was guessed" in out


def test_no_path_is_a_question_not_a_guess():
    out = IB._run({})
    assert "need a file" in out


def test_a_missing_readelf_names_the_package(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    out = IB._run({"path": str(_real_file(tmp_path, "somename"))})
    assert "elfutils" in out
    assert "UNKNOWN" in out


def test_it_never_runs_the_file_it_inspects(tmp_path, monkeypatch):
    """**The reason this reads `readelf` and not `ldd`.** `ldd` invokes the
    file's loader on the file; on an untrusted binary that is executing it, and
    on some files it hangs. `ldd` must never appear in this module.
    """
    source = (_REPO / "usr/lib/shani-chronoa/shani_chronoa/skills"
              / "inspect_binary.py").read_text()
    assert "ldd" not in source.replace("`ldd`", "").replace("ldd`.", "")


@pytest.mark.skipif(not pathlib.Path("/usr/bin/readelf").exists(),
                    reason="readelf is not installed here")
def test_the_real_binary_is_parsed_when_present():
    """Not a stub: the real `readelf`, on this machine's own `/bin/true`."""
    fields, problem = IB._header(pathlib.Path("/bin/true"))
    assert problem == "", problem
    assert fields.get("Class") == "ELF64"
    assert "X86-64" in fields.get("Machine", ""), fields
    # And the same code path on a real shared library gives a different kind.
    lib = pathlib.Path("/lib/x86_64-linux-gnu/libc.so.6")
    if lib.exists():
        lib_fields, lib_problem = IB._header(lib)
        assert lib_problem == "", lib_problem
        assert "shared object" in IB._kind(lib_fields)
        assert IB._kind(lib_fields) != IB._kind(fields)