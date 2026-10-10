"""`compression_savings`: how much btrfs compression is saving, measured not guessed.

On an immutable btrfs system with compression on, this is the most useful
number about storage: it is why a ~20 GB install fits in the slot it boots
from, and why deleting a big file can free less than its size. Nothing in
Chronoa answered it.

**Every assertion here is anchored to a real slot measurement**
(`shani-testbed` `chronoa-cli-formats.sh`, `@blue`, 2026-10-10) whose captured
output is the fixture below:

```
Processed 14 files, 15 regular extents (15 refs), 8 inline.
Type       Perc     Disk Usage   Uncompressed Referenced
TOTAL        9%      145K         1.4M         1.4M
zstd         9%      145K         1.4M         1.4M
```

**Two of those measurements corrected the implementation**, which is the reason
they are in the fixture rather than summarised:

- **`compsize /` answers "Not btrfs"** - nspawn's root is an overlay, so a
  single-path skill would report "this is not btrfs" about a machine whose root
  is btrfs. The mounts ShaniOS's own fstab documents are measured instead, and
  the first that answers is the answer.
- **`compsize` on an empty directory answers `No files.` and exits 1**, which is
  an answer and not a failure. Reading a non-zero exit as "could not measure"
  would be a confident wrong answer about an empty `@home`.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import sense_reading  # noqa: E402
from shani_chronoa.skills import compression_savings as CS  # noqa: E402

#: The captured table, verbatim.
CAPTURED = """Processed 14 files, 15 regular extents (15 refs), 8 inline.
Type       Perc     Disk Usage   Uncompressed Referenced
TOTAL        9%      145K         1.4M         1.4M
zstd         9%      145K         1.4M         1.4M
"""

_READ_KEY = "filesystems-sense-enabled"


@pytest.fixture
def granted(monkeypatch):
    """The filesystems sense on: this skill reports that sense's subject."""
    class On:
        def sense_allowed(self, name):
            return name == "filesystems"

        def refusal(self, config, name):
            return f"the {name} sense is off"

    monkeypatch.setattr(CS, "ChronoaConfig", On)


@pytest.fixture
def refused(monkeypatch):
    class Off:
        def sense_allowed(self, name):
            return False

        def sense_allowed_reason(self, name):
            return f"'{name}-sense-enabled' is off"

    monkeypatch.setattr(CS, "ChronoaConfig", Off)
    return sense_reading


@pytest.fixture
def fake_compsize(tmp_path, monkeypatch):
    """A stand-in `compsize` answering from a file, per mount.

    **Rewritten in place, not captured when installed** - the same fixture
    defect that made an earlier "refuses X" test pass vacuously.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    answers = tmp_path / "answers"
    answers.mkdir(exist_ok=True)

    def _install(**mounts):
        for name, text in mounts.items():
            safe = name.strip("/").replace("/", "_") or "_root"
            (answers / safe).write_text(text)
        script = bindir / "compsize"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib, sys\n"
            f"answers = pathlib.Path({str(answers)!r})\n"
            "target = sys.argv[1]\n"
            f"f = answers / (target.strip('/').replace('/', '_') or '_root')\n"
            "if not f.exists():\n"
            "    print(f'ERROR: {target}: Not btrfs (or SEARCH_V2 unsupported).', file=sys.stderr)\n"
            "    raise SystemExit(1)\n"
            "print(f.read_text(), end='')\n"
            "raise SystemExit(0)\n"
        )
        script.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    return _install


def test_a_real_table_reports_the_fraction_and_the_compressor(granted, tmp_path,
                                                              monkeypatch):
    """The whole answer, read from the captured table: the percentage, both
    sizes, and which compressor did it.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    data = tmp_path / "table.txt"
    data.write_text(CAPTURED)
    script = bindir / "compsize"
    script.write_text(
        "#!/usr/bin/env python3\n"
        f"print(open({str(data)!r}).read(), end='')\n"
        "raise SystemExit(0)\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.setattr(CS, "_MOUNTS", ("/var/cache",))
    out = CS._run({})
    assert "/var/cache: TOTAL occupies 9% of 1.4M uncompressed" in out
    assert "compressed with zstd" in out


def test_the_filesystem_sense_gates_it(refused, fake_compsize):
    fake_compsize()
    out = CS._run({})
    assert "filesystems" in out
    assert "sense" in out          # the refusal names the sense it follows


def test_a_directory_with_nothing_in_it_is_not_a_failure(granted, tmp_path,
                                                         monkeypatch):
    """**The measured correction.** `No files.` with exit 1 is compsize's own
    answer for an empty subvolume, and reporting it as UNKNOWN would be a
    confident wrong answer about the machine.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "compsize"
    script.write_text(
        "#!/bin/sh\n"
        "echo 'No files.' >&2\n"
        "exit 1\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    # /data is empty in a fresh slot; /var/cache answers, so the empty one is
    # skipped and the answer still comes from the next mount.
    monkeypatch.setattr(CS, "_MOUNTS", ("/data", "/var/cache"))
    monkeypatch.setattr(CS, "_run_compsize",
                        lambda p: ("", "No files.") if p == "/data" else
                        ("Processed 3 files.\nType Perc Disk Usage Uncompressed Referenced\n"
                         "TOTAL 40% 100M 250M 250M\nzstd 40% 100M 250M 250M\n", ""))
    out = CS._run({})
    assert "TOTAL occupies 40%" in out


def test_a_non_btrfs_mount_is_skipped_not_reported_as_the_answer(granted, tmp_path,
                                                                 monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "compsize"
    script.write_text(
        "#!/bin/sh\n"
        "echo 'ERROR: /: Not btrfs (or SEARCH_V2 unsupported).' >&2\n"
        "exit 1\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.setattr(CS, "_MOUNTS", ("/",))
    out = CS._run({})
    assert "UNKNOWN" in out
    assert "not btrfs here" in out


def test_a_missing_binary_names_its_package(granted, tmp_path, monkeypatch):
    empty = tmp_path / "no-compsize"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    out = CS._run({})
    assert "compsize" in out and "btrfs-progs" in out
    assert "UNKNOWN" in out


def test_a_timeout_is_reported_as_unknown(granted, tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "compsize"
    script.write_text("#!/bin/sh\nsleep 300\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.setattr(CS, "_MOUNTS", ("/var/cache",))
    monkeypatch.setattr(CS, "_TIMEOUT", 1)
    out = CS._run({})
    assert "UNKNOWN" in out
