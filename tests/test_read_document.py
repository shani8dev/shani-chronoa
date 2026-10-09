"""`read_document`: a PDF with no text layer is read as a picture, not refused.

`pdftotext` on a scanned document returns the form feeds between pages and
nothing else - **measured, 2 bytes for a 2-page scan** - so the old answer was
*"has no readable text (a scanned PDF needs to be read as a picture)"*, naming
something this package could not do. Both halves were already installed and
already depended on: `pdftoppm` ships in **poppler**, the same package as
`pdftotext`, and tesseract is the branch right below.

**A fixture lesson that cost a false regression here.** The first version of the
"a normal PDF still uses pdftotext" test built its PDF without a `/Font`
resource, so poppler printed `Syntax Error: Unknown font tag 'F1'` and
**returned nothing** - indistinguishable from a scan. The skill then did exactly
what it is supposed to do and the test called that a regression. A hand-built
PDF fixture that declares no font is a *broken text layer*, not a control; both
fixtures here declare one, and the difference is asserted rather than assumed.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa.skills import read_document as rd  # noqa: E402


def _pdf(pages, text=None):
    """A minimal valid PDF. **Declares a font**, or `pdftotext` returns nothing."""
    n = int(pages)          # a page COUNT, not a list of pages
    kids = b" ".join(b"%d 0 R" % (3 + 2 * i) for i in range(n))
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [" + kids + b"] /Count %d >>" % n]
    font_at = 3 + 2 * n
    for i in range(n):
        content = (b"BT /F1 24 Tf 20 100 Td (%s) Tj ET" % text) if text \
            else b"0 0 1 RG 20 20 120 120 re f"
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


@pytest.fixture
def scan(tmp_path):
    """A text-less 2-page PDF, plus one with a real text layer for contrast."""
    (tmp_path / "scan.pdf").write_bytes(_pdf(2))
    (tmp_path / "text.pdf").write_bytes(_pdf(1, text=b"Hello from a real text layer"))
    return tmp_path


@pytest.fixture
def tools_on(monkeypatch, tmp_path):
    """`pdftoppm` present or not; `tesseract` a stateful stand-in or absent."""
    fake = tmp_path / "bin"
    fake.mkdir(exist_ok=True)
    log = tmp_path / "ocr.log"
    script = fake / "tesseract"
    script.write_text(
        "#!/bin/sh\n"
        "img=\"\"\n"
        "for a in \"$@\"; do case \"$a\" in *.png) img=\"$a\";; esac; done\n"
        "echo \"called $(basename \"$img\") $(wc -c < \"$img\")\" >> " + str(log) + "\n"
        "echo \"words from $img\"\n")
    script.chmod(0o755)
    state = {"tesseract": True, "pdftoppm": True}
    real_which = shutil_which = __import__("shutil").which

    def _which(name):
        if name == "tesseract":
            return str(script) if state["tesseract"] else None
        if name == "pdftoppm":
            return "/usr/bin/pdftoppm" if state["pdftoppm"] else None
        return real_which(name)

    # **PATH too, not just `which`.** `_tesseract_argv` builds the argv as a
    # bare `"tesseract"`, which the subprocess resolves through PATH - so a
    # stand-in that only answers `which` is never actually executed, and the
    # run fails with `[Errno 2] No such file or directory: 'tesseract'`.
    monkeypatch.setenv("PATH", str(fake) + os.pathsep + os.environ["PATH"])
    monkeypatch.setattr(rd.shutil, "which", _which)
    state["log"] = log
    state["calls"] = lambda: (log.read_text().splitlines() if log.exists() else [])
    return state


# ── the dead end that used to be here ───────────────────────────────────────

def test_a_pdf_with_a_text_layer_still_uses_pdftotext(scan, tools_on):
    """The control for everything below: a real text layer must not be OCR'd."""
    out = rd._run({"path": str(scan / "text.pdf")})
    assert "Hello from a real text layer" in out, out
    assert tools_on["calls"]() == [], "tesseract was run on a PDF that had text"
    assert "no text layer" not in out, out


def test_a_scan_is_read_as_a_picture_and_says_so(scan, tools_on):
    out = rd._run({"path": str(scan / "scan.pdf")})
    assert "no text layer" in out, out
    assert "as 200 DPI pictures" in out, out
    assert tools_on["calls"] != (), "tesseract was never called on a scan"
    # Both pages really were rendered, not one.
    assert len(tools_on["calls"]()) == 2, tools_on["calls"]()


def test_the_answer_says_how_it_was_read_because_it_matters(scan, tools_on):
    """Text recognised from a picture is not a text layer.

    A summary built from OCR is more likely to get a number or a name wrong, and
    the caller cannot tell that from the output unless the output says so.
    """
    out = rd._run({"path": str(scan / "scan.pdf")})
    assert "more likely to be wrong" in out, out
    assert "pictures" in out


def test_a_page_range_is_honoured(scan, tools_on):
    rd._run({"path": str(scan / "scan.pdf"), "first_page": 2, "last_page": 2})
    calls = tools_on["calls"]()
    assert len(calls) == 1, calls
    assert "page-2.png" in calls[0], calls


def test_the_page_cap_is_enforced_and_reported(tmp_path, tools_on):
    """OCR is seconds per page, and a scanned book is one request."""
    (tmp_path / "book.pdf").write_bytes(_pdf(8))
    out = rd._run({"path": str(tmp_path / "book.pdf")})
    assert len(tools_on["calls"]()) == 5, tools_on["calls"]()
    assert "further page(s) not read" in out, out
    assert "3 further page(s)" in out, out


# ── the two honest refusals ─────────────────────────────────────────────────

def test_a_missing_tesseract_names_the_package(scan, tools_on):
    tools_on["tesseract"] = False
    out = rd._run({"path": str(scan / "scan.pdf")})
    assert "tesseract" in out, out
    assert "Nothing was guessed" in out, out
    # The old wording pointed at a dead end; this one names what is missing.
    assert "needs to be read as a picture." not in out


def test_a_missing_pdftoppm_names_it_too(scan, tools_on):
    tools_on["pdftoppm"] = False
    out = rd._run({"path": str(scan / "scan.pdf")})
    assert "pdftoppm" in out, out
    assert "poppler" in out, out


def test_a_refusal_is_never_a_guess_about_the_document(scan, tools_on):
    """No OCR available must not read as "the document is empty"."""
    tools_on["tesseract"] = False
    out = rd._run({"path": str(scan / "scan.pdf")})
    assert "no readable text" not in out, out


# ── the temporary directory, which is the user's document ───────────────────

def test_the_rendered_pages_are_kept_and_where_they_are(scan, tools_on,
                                                         tmp_path, monkeypatch):
    """They are the artefact, and the answer has to say where they went.

    The first version rendered into a temp dir and deleted it in a `finally`.
    Its two cleanup tests then went **vacuously green** after the change - they
    globbed `/tmp/chronoa-pdf-*`, which nothing creates any more, so "nothing
    before and nothing after" was true of a run that had done nothing. Replaced
    with the property that is actually wanted.
    """
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(rd.pathlib.Path, "home", classmethod(lambda cls: home))

    out = rd._run({"path": str(scan / "scan.pdf")})
    kept = home / "Documents" / "Scans" / "scan-pages"
    assert kept.is_dir(), f"no page images kept at {kept}"
    assert sorted(x.name for x in kept.glob("*.png")) == ["page-1.png", "page-2.png"]
    assert str(kept) in out, f"the answer does not say where the pages went:\n{out}"


def test_the_pages_are_kept_even_when_reading_fails(scan, tools_on, tmp_path,
                                                    monkeypatch):
    """The failure case is where the images matter most.

    A missing language pack is a real state on the Plasma image, and then the
    rendered pages are the only thing produced - which is what another OCR, or
    the person themselves, can still read.
    """
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(rd.pathlib.Path, "home", classmethod(lambda cls: home))
    real_run = rd.subprocess.run

    def _run(cmd, **k):
        if cmd and cmd[0] == "tesseract":
            return type("R", (), {"returncode": 1, "stdout": "",
                                  "stderr": "Failed loading language 'eng'\n"})()
        return real_run(cmd, **k)

    monkeypatch.setattr(rd.subprocess, "run", _run)
    out = rd._run({"path": str(scan / "scan.pdf")})
    kept = home / "Documents" / "Scans" / "scan-pages"
    assert kept.is_dir(), "a failed OCR threw away the rendered pages"
    assert len(list(kept.glob("*.png"))) == 2
    assert "tesseract failed" in out, out


def test_a_render_failure_says_so_rather_than_reporting_an_empty_document(
        scan, tools_on, monkeypatch):
    def _fail(cmd, **k):
        if cmd[0] == "pdftoppm":
            class R:
                returncode = 1
                stderr = "Syntax Error: bad thing\n"
            return R()
        raise AssertionError("pdftotext should have run first")
    real_run = rd.subprocess.run
    monkeypatch.setattr(rd.subprocess, "run",
                        lambda cmd, **k: _fail(cmd) if cmd[0] == "pdftoppm"
                        else real_run(cmd, **k))
    out = rd._run({"path": str(scan / "scan.pdf")})
    assert "Could not render" in out, out
    assert "bad thing" in out, out

def test_the_image_branch_goes_through_the_shared_argv_builder(scan, tools_on,
                                                               monkeypatch):
    """The refactor's own regression, and the reason a mutation survived.

    `_tesseract_argv` was extracted from the image branch so the scanned-PDF
    branch could share it rather than duplicating the language and tessdata
    logic. Nothing then tested that the image branch still went through it, so
    replacing the call with a hardcoded `-l eng` left the file green twice over
    - and on a machine where only English is installed the two argv lists are
    **byte-identical**, so asserting on the arguments could never have caught it.
    The invariant is structural: one definition, so a language setting cannot
    come to apply to a PDF and not to a picture.
    """
    sentinel = ["tesseract", "SENTINEL", "-", "-l", "zz"]
    monkeypatch.setattr(rd, "_tesseract_argv", lambda image: list(sentinel))
    seen = {}
    real_run = rd.subprocess.run

    def _run(cmd, **k):
        if cmd and cmd[0] == "tesseract":
            seen["argv"] = list(cmd)
            return type("R", (), {"returncode": 0, "stdout": "text", "stderr": ""})()
        return real_run(cmd, **k)

    monkeypatch.setattr(rd.subprocess, "run", _run)
    (scan / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    rd._run({"path": str(scan / "pic.png")})
    assert seen.get("argv") == sentinel, seen.get("argv")


def test_a_tesseract_failure_is_not_reported_as_a_blank_page(scan, tools_on,
                                                             monkeypatch):
    """A non-zero exit is a failure, not a page with nothing written on it.

    Measured on the Plasma image: `tesseract --list-langs` answers `afr osd`
    (no English), and `-l eng` then exits **1** with empty stdout and
    "Failed loading language 'eng'". Reporting that as "no text on the page"
    describes a document full of words as blank - and it is the failure this
    whole path exists to avoid, reintroduced one line above where the page loop
    appended nothing and fell through to the empty result.
    """
    real_run = rd.subprocess.run

    def _run(cmd, **k):
        if cmd and cmd[0] == "tesseract":
            return type("R", (), {
                "returncode": 1, "stdout": "",
                "stderr": ("Error opening data file /usr/share/tessdata/eng."
                           "traineddata Failed loading language 'eng' "
                           "Tesseract couldn't load any languages!\n")})()
        return real_run(cmd, **k)

    monkeypatch.setattr(rd.subprocess, "run", _run)
    out = rd._run({"path": str(scan / "scan.pdf")})
    assert "tesseract failed" in out, out
    assert "missing language pack" in out, out
    assert "found no text" not in out, out


# One mutation is equivalent and is kept here rather than hidden: deleting the
# sentence "The page images are kept in <dir>." from the provenance note leaves
# this file green, because the trailing line names the directory on every path
# (`... further page(s) not read, and already rendered in <dir>` or
# `(Page images kept in <dir>.)`). The property worth holding is "the pages are
# kept and the answer says where", and both sentences serve it; testing the
# note specifically would pin wording, not behaviour.
