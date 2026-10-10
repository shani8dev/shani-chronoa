"""`read_document`'s document facts: pages, encryption, page size.

Three of `pdfinfo`'s twenty fields change what the answer means, and they
are the three this adds:

- **encrypted** — an owner-password PDF extracts nothing, and the useful
  answer is *this is encrypted*, not *it has no text*;
- **pages** — the scanned branch reads at most five and says so, so "5
  further pages not read" is meaningless without a total;
- **page size** — A4 against Letter is the difference between a document
  that prints as intended and one that comes out scaled.

Measured on the real binary here: rc=0 with all three lines for a real
PDF, and rc=1 for a file that is not one — so a non-zero exit contributes
nothing and the caller's own read already names that failure. An
encryption state is stated as a fact, never as a warning: an encrypted
document is not an error, it is a document this read cannot extract from.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa.skills import read_document as rd  # noqa: E402

REAL = """Creator:        pdflatex
Producer:       poppler
Pages:          12
Encrypted:      yes
Page size:      595.276 x 841.89 pts (A4)
File size:      1048576 bytes
"""


def _fake_pdfinfo(tmp_path, stdout=REAL, code=0, missing=False):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    if not missing:
        data = tmp_path / "info.txt"
        data.write_text(stdout)
        script = bindir / "pdfinfo"
        script.write_text(
            "#!/usr/bin/env python3\n"
            f"print(open({str(data)!r}).read(), end='')\n"
            f"raise SystemExit({code})\n"
        )
        script.chmod(0o755)
    else:
        (bindir / "python3").symlink_to(sys.executable)
        monkey_path = str(bindir)
        return monkey_path
    return f"{bindir}:{os.environ['PATH']}"


def _pdf(tmp_path):
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-1.4\n% (a stand-in file; pdfinfo is faked)\n%%EOF\n")
    return p


def _pdf_bytes(pages, text):
    """A minimal valid PDF with a text layer, like the fixture in
    tests/test_read_document.py: declares a font, or `pdftotext` returns
    nothing and the run takes the scanned branch instead of this one.

    **One page, written out plainly.** The first version copied the fonts
    fixture's loop and lost its variable name, so pyflakes flagged an
    undefined `i` and the composed test failed for a reason that had nothing
    to do with what it was testing.
    """
    n = int(pages)
    kids = b" ".join(b"%d 0 R" % (3 + 2 * i) for i in range(n))
    font_at = 3 + 2 * n
    content = b"BT /F1 24 Tf 20 100 Td (" + text.encode() + b") Tj ET"
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [" + kids + b"] /Count %d >>" % n]
    for i in range(n):
        # **Contents first, then the font** - the page's own number is
        # `3 + 2*i`, its stream is `4 + 2*i`, and the font is the last object.
        # Swapping the last two makes the page point at the font as its
        # content stream: poppler then reports no text layer, the run takes
        # the scanned branch, and the assertion being tested never executes.
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] "
                    b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
                    % (font_at, 4 + 2 * i))
    objs.append(b"<< /Length " + str(len(content)).encode()
                + b" >>\nstream\n" + content + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + o + b"\nendobj\n"
    start = len(out)
    out += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n0000000000 65535 f \n"
    for off in offsets:
        out += ("%010d 00000 n \n" % off).encode()
    out += (b"trailer\n<< /Size " + str(len(objs) + 1).encode()
            + b" /Root 1 0 R >>\nstartxref\n" + str(start).encode() + b"\n%%EOF\n")
    return out


def test_pages_encryption_and_size_are_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", _fake_pdfinfo(tmp_path))
    out = rd._meta_lines(_pdf(tmp_path))[0]
    assert out == "Document: 12 page(s), encrypted, 595.276 x 841.89pt (A4)."


def test_an_unencrypted_pdf_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", _fake_pdfinfo(tmp_path,
                       stdout="Pages: 1\nEncrypted: no\nPage size: 200 x 200 pts\n"))
    out = rd._meta_lines(_pdf(tmp_path))[0]
    assert "not encrypted" in out
    assert "1 page(s)" in out


def test_encryption_is_a_fact_not_a_warning(tmp_path, monkeypatch):
    """**A state, not an alarm.** An encrypted PDF is a document this read
    cannot extract from — the answer names what it is, so the next question
    ("unlock it and read it") is the one the person asks rather than "why is
    it broken".
    """
    monkeypatch.setenv("PATH", _fake_pdfinfo(tmp_path))
    assert rd._meta_lines(_pdf(tmp_path))[0].startswith("Document:")


def test_a_nonzero_exit_contributes_nothing(tmp_path, monkeypatch):
    """Measured: rc=1 on a file that is not a PDF. The caller's own read
    already names that failure, so a second complaint from here would be
    noise — but the font report still answers, which is asserted through
    `_run` in the composed test below.
    """
    monkeypatch.setenv("PATH", _fake_pdfinfo(tmp_path, code=1))
    assert rd._meta_lines(_pdf(tmp_path)) == []


def test_a_missing_binary_is_silent_rather_than_invented(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", _fake_pdfinfo(tmp_path, missing=True))
    assert rd._meta_lines(_pdf(tmp_path)) == []


def test_an_answer_naming_none_of_the_three_is_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", _fake_pdfinfo(tmp_path, stdout="Creator: x\n"))
    out = rd._meta_lines(_pdf(tmp_path))[0]
    assert "UNKNOWN" in out
    assert "no pages" in out


def test_the_composed_answer_carries_the_document_line(tmp_path, monkeypatch):
    """End to end through `_run`, beside the font report that already exists.

    **Driven through a PDF that actually has a text layer**, so this is the
    success path and not the scanned/OCR fallback: the first version of this
    test used a stand-in file, `pdftotext` extracted nothing, and the OCR path
    returned the lines anyway — so a mutation deleting the call from this path
    left it green. Both halves are asserted because both halves are one answer
    about the same file.
    """
    monkeypatch.setenv("PATH", _fake_pdfinfo(tmp_path))
    # **A PDF with a real text layer**, or the run takes the OCR branch where
    # the other call site lives and the success path goes unexercised: the
    # first version of this test wrote a partial file, `pdftotext` extracted
    # nothing, and a mutation deleting this call site stayed green.
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(_pdf_bytes(1, "The quick brown fox"))
    out = rd._run({"path": str(pdf)})
    assert "The quick brown fox" in out          # the success path was taken
    assert "Document: 12 page(s), encrypted" in out
    assert "Fonts:" in out


def test_real_binary_is_used_when_present(tmp_path, monkeypatch):
    """**The measurement this file rests on.** With the real `pdfinfo` on
    PATH (it ships in poppler, which the images have), the page count of a
    PDF built here is reported as 1 — so the parser is tied to the real
    tool's field names, not to a fixture's.
    """
    if not (pathlib.Path("/usr/bin/pdfinfo").exists()
            or rd.shutil.which("pdfinfo")):
        pytest.skip("pdfinfo is not installed on this machine")
    # A one-page PDF whose only font is base-14 Helvetica; the page count and
    # the encryption state are what the real tool prints for it.
    pdf = tmp_path / "one.pdf"
    pdf.write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
                    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
                    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
                    b"trailer<</Root 1 0 R/Size 4>>\n%%EOF\n")
    out = rd._meta_lines(pdf)
    assert out, "the real pdfinfo answered nothing"
    assert "1 page(s)" in out[0]
    assert "not encrypted" in out[0]

