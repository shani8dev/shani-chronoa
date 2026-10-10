"""`read_document`'s font report - and the two ways `pdffonts` lies quietly.

**Why a font report at all.** A PDF whose fonts are not embedded reads fine on
the machine that made it and prints wrong everywhere else: substituted metrics,
reflowed lines, glyphs from a different family. `pdftotext` - the tool this
skill already turned to for everything - extracts the words and says nothing
about the font table. So the answer to *"will this print right on another
machine?"* was structurally absent.

**Two measured traps, both from real runs here, both the shape of bug this
repository keeps recording:**

1. **`pdffonts` exits are two different contracts, and the first measurement of
   them was taken through a pipe and was wrong.** The original docstring here
   recorded *"exits 0 even when the file is not a PDF"* - that exit code
   belonged to `head`. Measured properly: a **non-PDF exits 1** with
   `Syntax Error: Couldn't find trailer dictionary`, and a **valid PDF that
   declares no fonts exits 0 with an empty table**. Both branches are real:
   the first is "this file could not be read", the second is "there are no
   fonts here" - and the empty table is never read as *all embedded*, because
   those are different facts about different files.
2. **The table is fixed-width, and `split()` got it wrong on a real file.** The
   `type` column holds `Type 1` - two words - and font *names* hold spaces
   (`DejaVu Sans Mono`), so a field index lands on a different column for
   different rows while still producing the right *number* of fields. The first
   version of this parser read `Standard` as the `emb` value and reported a
   non-embedded font as embedded. The boundaries now come from the dashed rule
   under the header - the one line whose marks are the column edges.

**A fixture lesson, third time this shape has bitten here.** The
all-embedded fixture is real captured output edited *by column position*, not
hand-typed: a hand-typed row was one space narrower through `sub`/`uni` and was
misread by the very parser it was written to test. `test_a_font_name_with_a_space_is_not_misread`
pins that case from the real format, and the all-embedded fixture is derived
from the same captured text so the two cannot drift apart.
"""

from __future__ import annotations

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa.skills import read_document as rd  # noqa: E402

#: Real `pdffonts /tmp/mini.pdf` output, captured on this machine. The
#: alignment - not the content - is the part the parser depends on.
RULE = "------------------------------------ ----------------- ---------------- --- --- --- ---------"


def _row(name, typ, enc, emb, sub, uni, oid):
    """One data row at the widths the real tool prints.

    **A calibrated generator, not hand-typed rows.** Hand-typing a row is how
    this fixture first went wrong: a row one space narrower through `sub`/`uni`
    was misread by the very parser it was written to test. The widths below
    are asserted byte-for-byte against the captured real line in
    `test_the_row_generator_matches_the_real_tool`, so the generator cannot
    drift from the format the parser reads.
    """
    return f"{name:<36} {typ:<17} {enc:<16} {emb:<3} {sub:<3} {uni:<3} {oid:>9}"


#: The captured real rows, rebuilt through the calibrated generator.
REAL_HEADER = "name                                 type              encoding         emb sub uni object ID"
REAL_TABLE = "\n".join([
    REAL_HEADER,
    RULE,
    _row("Helvetica", "Type 1", "Standard", "no", "no", "no", "4  0"),
    _row("FakeFont", "TrueType", "WinAnsi", "yes", "no", "no", "5  0"),
]) + "\n"


def _fake_binary(tmp_path, stdout="", stderr="", code=0, name="pdffonts"):
    """A stand-in `pdffonts` on PATH, like the `grim` fixture in
    test_permission_decisions.py: the skill runs in this process and reads
    PATH, so the binary has to be a real file rather than a patch."""
    bindir = tmp_path / name
    bindir.mkdir(exist_ok=True)
    script = bindir / name
    script.write_text(
        "#!/bin/sh\n"
        f'[ -n "$1" ] && printf %s {stderr!r} >&2\n'
        f"if [ -n \"$FAKE_STDOUT_FILE\" ]; then cat \"$FAKE_STDOUT_FILE\"; fi\n"
        f"exit {code}\n"
    )
    script.chmod(0o755)
    return bindir


def _all_embedded(tmp_path):
    """The captured table with every `emb` cell flipped to `yes`.

    **Rebuilt through the calibrated generator**, so the result is
    byte-aligned the way the real tool prints it. Editing a captured row in
    place produced a fixture one space narrower through `sub`/`uni`, which the
    parser then misread - the exact bug this file tests for.
    """
    text = "\n".join([
        REAL_HEADER,
        RULE,
        _row("Helvetica", "Type 1", "Standard", "yes", "no", "no", "4  0"),
        _row("FakeFont", "TrueType", "WinAnsi", "yes", "no", "no", "5  0"),
    ]) + "\n"
    path = tmp_path / "all_embedded.txt"
    path.write_text(text)
    return path


def _real_pdf(tmp_path):
    """A minimal PDF whose only font is base-14 Helvetica: never embedded.

    **Declares a font**, or `pdftotext` returns nothing at all - the fixture
    lesson recorded at the top of `tests/test_read_document.py`, and worth
    repeating rather than trusting across files.
    """
    pdf = tmp_path / "mini.pdf"
    pdf.write_bytes(_pdf_bytes(1, "Hello"))
    return pdf


def _pdf_bytes(pages, text):
    """A minimal valid PDF, from `tests/test_read_document.py`'s fixture."""
    n = int(pages)
    kids = b" ".join(b"%d 0 R" % (3 + 2 * i) for i in range(n))
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [" + kids + b"] /Count %d >>" % n]
    font_at = 3 + 2 * n
    content = b"BT /F1 24 Tf 20 100 Td (" + text.encode() + b") Tj ET"
    objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] "
                b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
                % (font_at, 4))
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


def test_a_non_embedded_font_is_named_and_counted(tmp_path, monkeypatch):
    """The real table is parsed: 1 of 2 not embedded, and it is named."""
    table = tmp_path / "real.txt"
    table.write_text(REAL_TABLE)
    bindir = _fake_binary(tmp_path)
    monkeypatch.setenv("FAKE_STDOUT_FILE", str(table))
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    lines = rd._font_lines(_real_pdf(tmp_path))
    assert lines
    assert "1 of 2 are NOT embedded" in lines[0]
    assert "Helvetica" in lines[0]


def test_all_embedded_is_not_reported_as_unknown(tmp_path, monkeypatch):
    table = _all_embedded(tmp_path)
    bindir = _fake_binary(tmp_path)
    monkeypatch.setenv("FAKE_STDOUT_FILE", str(table))
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    lines = rd._font_lines(_real_pdf(tmp_path))
    assert "all 2 are embedded" in lines[0]
    assert "NOT embedded" not in " ".join(lines)


def test_a_fontless_pdf_that_exits_zero_lists_no_fonts(tmp_path, monkeypatch):
    """**The measured case: a valid PDF declaring no fonts, rc=0, no rows.**

    This is not the same as a file that could not be read - which is the
    correction the original docstring needed, because its "rc=0 on a non-PDF"
    had been measured through a pipe and was wrong. An empty table reports
    *could not be determined*, and specifically not *all embedded*: only the
    second would be a confident answer about a file that parsed.
    """
    bindir = _fake_binary(tmp_path, code=0)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    lines = rd._font_lines(_real_pdf(tmp_path))
    assert "listed no fonts" in lines[0]
    assert "could not be determined" in lines[0]


def test_a_non_pdf_exits_one_and_is_not_an_empty_table(tmp_path, monkeypatch):
    """**The other measured contract: a non-PDF exits 1.**

    `Syntax Error: Couldn't find trailer dictionary` stderr and rc=1, so it
    takes the could-not-be-read branch and never the empty-table one. The
    assertion that matters is that the answer says *could not be read* rather
    than either of the two all-embedded-flavoured sentences - a wrong exit
    code here is how a non-PDF would be reported as a perfectly good one.
    """
    bindir = _fake_binary(tmp_path, stderr="Syntax Error: Couldn't find trailer dictionary",
                          code=1)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    lines = rd._font_lines(tmp_path / "hostname.pdf")
    assert "could not be read" in lines[0]
    assert "no fonts listed" not in lines[0]
    assert "embedded" not in lines[0]


def test_a_failing_exit_is_reported_as_a_failure(tmp_path, monkeypatch):
    bindir = _fake_binary(tmp_path, stderr="I/O Error: Couldn't open file", code=1)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    lines = rd._font_lines(tmp_path / "gone.pdf")
    assert "could not be read" in lines[0]


def test_a_missing_binary_is_unknown_and_names_its_package(tmp_path, monkeypatch):
    empty = tmp_path / "no-poppler"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    assert "not installed" in rd._font_lines(_real_pdf(tmp_path))[0]
    assert "poppler" in rd._font_lines(_real_pdf(tmp_path))[0]


def test_a_font_name_with_a_space_is_not_misread(tmp_path, monkeypatch):
    """**The negative control for the split() bug this parser had.**

    `DejaVu Sans Mono` is one name over three columns; a field-index parse
    reads `WinAnsi` as `emb`, counts a non-embedded font as embedded, and
    reports a total that still looks plausible. Both rows are built by the
    calibrated generator, so the alignment is the real tool's.
    """
    text = "\n".join([
        REAL_HEADER,
        RULE,
        _row("DejaVu Sans Mono", "TrueType", "WinAnsi", "no", "yes", "yes", "7  0"),
        _row("Helvetica", "Type 1", "Standard", "no", "no", "no", "4  0"),
    ]) + "\n"
    table = tmp_path / "spaced.txt"
    table.write_text(text)
    bindir = _fake_binary(tmp_path)
    monkeypatch.setenv("FAKE_STDOUT_FILE", str(table))
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    out = rd._font_lines(_real_pdf(tmp_path))[0]
    assert "2 of 2 are NOT embedded" in out
    assert "DejaVu Sans Mono" in out
    assert "Helvetica" in out


def test_the_row_generator_matches_the_real_tool():
    """The generator is calibrated against the real captured line.

    Without this, a future edit to the widths would silently produce fixtures
    in a shape the real tool never prints - a fixture set that tests the
    parser against its own idea of the format. Asserted as exact equality on
    both captured rows.
    """
    assert _row("Helvetica", "Type 1", "Standard", "no", "no", "no", "4  0") == \
        "Helvetica                            Type 1            Standard         no  no  no       4  0"
    assert _row("FakeFont", "TrueType", "WinAnsi", "yes", "no", "no", "5  0") == \
        "FakeFont                             TrueType          WinAnsi          yes no  no       5  0"


def test_the_column_table_comes_from_the_rule_not_the_header():
    """The header's own spacing is not the boundary source.

    A wide `name` column shifts every later header word; the dashed rule
    under it does not move, and the parser must read the rule. Asserted
    directly, because a layout change in one tool version is exactly when
    this breaks and nothing else would notice.
    """
    lines = REAL_TABLE.splitlines()
    rule = next(i for i, l in enumerate(lines) if set(l) <= {"-", " "} and l.strip())
    columns = rd._column_spans(REAL_TABLE)
    assert columns["emb"] == (72, 75)
    assert columns["name"] == (0, 36)
    assert rd._row_cells(lines[rule + 1], columns)["emb"] == "no"


def test_the_pdf_answer_carries_the_font_line(tmp_path, monkeypatch):
    """End to end through `_run`: the report is attached to the PDF answer."""
    table = tmp_path / "real.txt"
    table.write_text(REAL_TABLE)
    bindir = _fake_binary(tmp_path)
    monkeypatch.setenv("FAKE_STDOUT_FILE", str(table))
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    out = rd._run({"path": str(_real_pdf(tmp_path))})
    assert "Hello" in out                     # the text layer was still read
    assert "Fonts:" in out
    assert "NOT embedded" in out
