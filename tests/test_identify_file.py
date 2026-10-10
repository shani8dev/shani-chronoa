"""`identify_file`: what is this file, really?

`get_file_info` reports `stat`. Nothing read the **bytes**, so nothing answered
"why won't this image open" when the answer is that the file named `.png` has
never been a PNG.
"""
from __future__ import annotations

import pathlib
import sys
from pathlib import Path

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import identify_file as IF  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
ZIP = b"PK\x03\x04\x14\x00\x00\x00\x00\x00" + b"\x00" * 64
ZIP_EMPTY = b"PK\x05\x06" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"\x00" * 64
GZIP = b"\x1f\x8b\x08\x00" + b"\x00" * 64
ELF = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 64
SQLITE = b"SQLite format 3\x00" + b"\x00" * 64
WAV = b"RIFF" + b"\x00\x00\x00\x00" + b"WAVE" + b"\x00" * 32
TEXT = b"just some plain text\n" * 4
BINARY = bytes(range(0, 256)) * 4


@pytest.fixture
def box(tmp_path):
    def put(name, payload):
        path = tmp_path / name
        path.write_bytes(payload)
        return path
    return put


# --- the detection -------------------------------------------------------------

@pytest.mark.parametrize("name,payload,kind", [
    ("a.png", PNG, "png"), ("b.jpg", JPEG, "jpeg"), ("c.pdf", PDF, "pdf"),
    ("d.gz", GZIP, "gzip"), ("e.elf", ELF, "elf"), ("f.zip", ZIP, "zip"),
    ("g.sqlite", SQLITE, "sqlite"), ("h.wav", WAV, "wav"),
])
def test_the_signature_is_recognised(box, name, payload, kind):
    head = payload[:IF._HEAD]
    assert IF._detect(head)[0] == kind


def test_every_zip_spelling_is_recognised():
    """`PK\\x03\\x04`, `PK\\x05\\x06` and `PK\\x07\\x08` are the three ways a zip
    begins, and the first version required **all three at once** - so a real zip
    written by Python was reported as an unrecognised format."""
    for head in (ZIP, ZIP_EMPTY, b"PK\x07\x08" + b"\x00" * 64):
        assert IF._detect(head[:IF._HEAD])[0] == "zip", head[:8]


def test_tar_is_recognised_at_offset_257():
    """tar's magic sits at 257, which is why `_HEAD` is 512 - a shorter read
    makes every archive unrecognised and nothing says why."""
    assert IF._HEAD > 257
    head = b"\x00" * 257 + b"ustar  \x00" + b"\x00" * 40
    assert IF._detect(head)[0] == "tar"


def test_a_gif_87a_and_89a_are_both_gif():
    for spelling in (b"GIF87a", b"GIF89a"):
        assert IF._detect(spelling + b"\x00" * 32)[0] == "gif"


def test_unrecognised_is_reported_as_unrecognised(box):
    """**Never as "not an image"** - a format absent from the table and a file
    that is not an image are different facts."""
    path = box("mystery.bin", BINARY)
    out = IF._run_skill({"path": str(path)})
    assert "unrecognised" in out
    assert "not the same as it being broken" in out


def test_text_without_a_signature_is_text(box):
    path = box("notes", TEXT)
    assert "text" in IF._run_skill({"path": str(path)})


# --- the mismatch this skill exists for ----------------------------------------

def test_a_mismatched_name_is_the_finding(box):
    """A JPEG named `.png` is the case that makes this skill worth having."""
    path = box("fake.png", JPEG)
    out = IF._run_skill({"path": str(path)})
    assert "JPEG" in out
    assert "name says `.png`, but its bytes say JPEG" in out
    assert "Renaming it will not fix it" in out


def test_an_agreeing_name_says_so(box):
    out = IF._run_skill({"path": str(box("real.png", PNG))})
    assert "agrees with its content" in out
    assert "but its bytes say" not in out


def test_a_binary_misnamed_as_text_is_the_dangerous_case(box):
    """A binary named `.txt` is the version of the mismatch that corrupts
    something when acted on.

    **Two payloads, because the first one only exercised half the rule.** A head
    with NULs is rejected by the NUL branch; a head with a control byte and *no*
    NUL is rejected only by the printable test. Testing only the first left the
    printable check equivalent to nothing - a mutation that deleted it stayed
    green, and the rule that was supposed to hold it was never being tested.
    """
    for payload in (b"\x00\x01\x02hello\x00\x03",
                    b"\x01\x02hello\x03there"):
        path = box("actually-binary.txt", payload)
        out = IF._run_skill({"path": str(path)})
        assert "unrecognised" in out, f"{payload!r} was called text"
        assert "text" not in out.split("**")[1], f"{payload!r} was called text"


def test_a_shared_suffix_is_not_claimed_as_a_mismatch(box):
    """`docx`, `xlsx` and `jar` are all Zip archives, so `.docx` on a zip is
    agreement and not a finding."""
    path = box("thing.docx", ZIP)
    assert "agrees with its content" in IF._run_skill({"path": str(path)})


def test_no_suffix_means_no_claim_to_check(box):
    out = IF._run_skill({"path": str(box("README", TEXT))})
    assert "no suffix" in out


# --- the states it keeps apart -------------------------------------------------

def test_an_empty_file_is_not_text(box):
    """**An empty file answered "text" on the first run.** The read succeeds
    with zero bytes and `_is_text` returns True for an empty head, so a 0-byte
    file's name was reported as if it described its contents - the very
    confusion this skill exists to catch, on the easiest case."""
    out = IF._run_skill({"path": str(box("empty.bin", b""))})
    assert "empty" in out
    assert "0 bytes" in out
    assert "contains **text**" not in out


def test_a_missing_file_is_refused(box):
    assert "does not exist" in IF._run_skill({"path": str(box)})


def test_no_path_is_a_question(tmp_path):
    assert "Which file" in IF._run_skill({"path": ""})


def test_the_head_read_is_bounded(box, monkeypatch):
    """The read is bounded - a huge file is identified from its first 512 bytes
    and the rest is never loaded. **`open` is a builtin**, so the first version
    of this test tried to patch `IF.open`, which does not exist, and reported an
    AttributeError about its own fixture rather than about the read."""
    opened = []
    real_open = open

    def watch(file, mode="r", *args, **kwargs):
        opened.append((str(file), mode))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", watch)
    head, _ = IF._read_head(box("big.png", PNG * 200))
    assert opened, "no file was opened"
    # Exactly one open, and the head is capped - not 12,800 bytes.
    assert len(head) == IF._HEAD
    assert IF._HEAD == 512


# --- one equivalent mutant, and the branch it removed --------------------------
# **Removing an explicit `if b"\x00" in head: return False` left the suite green,
# and correctly so:** NUL is not in `_PRINTABLE`, so the printable test already
# rejects any head containing one. No input exists for which the two differ.
#
# It looked like an equivalent mutant, but it was a **test gap wearing an
# equivalent mutant's clothes** - the dangerous-case fixture held only
# NUL-bearing payloads, so the printable half of the rule was never exercised
# and a mutation that deleted *it* stayed green too. With a control-byte payload
# that carries no NUL added, both halves are live and the printable test is the
# one that holds. The redundant branch was then removed rather than kept as
# documentation: two copies of one rule is two places for it to be wrong.
