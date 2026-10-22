"""Word/Excel/PowerPoint through the stdlib `office` module and the `office_document` skill.

Each generated file is checked structurally here (zip parts, XML, read-back).
The stronger proof - that independent readers accept them - was run by hand
against python-docx, openpyxl, python-pptx and OnlyOffice's own x2t converter
(docx->odt, xlsx->ods, pptx->odp all exit 0 and keep every value); see
AGENTS.md. That x2t run is also why text cells use the shared-string table:
`test_text_cells_use_the_shared_string_table` pins it.
"""

import glob
import os
import shutil
import subprocess
import zipfile
from xml.etree import ElementTree as ET

import pytest

from shani_chronoa import office
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import office_document as od


def _find_x2t():
    """OnlyOffice's x2t converter: on PATH, or inside its flatpak, or None.

    The suite's by-hand OnlyOffice check (see the module docstring) is run
    automatically here when x2t is present, so a chart that OnlyOffice cannot
    read is caught rather than only structurally plausible.
    """
    direct = shutil.which("x2t")
    if direct:
        return direct, None
    for base in ("/var/lib/flatpak/app", os.path.expanduser("~/.local/share/flatpak/app")):
        hits = glob.glob(base + "/org.onlyoffice.desktopeditors/*/*/*/files/bin/opt/onlyoffice/desktopeditors/converter/x2t")
        if hits:
            return hits[0], os.path.dirname(hits[0])
    return None, None


def _x2t_convert(src, dst):
    """Convert with x2t; returns the completed process (exit 0 means it parsed)."""
    x2t, conv = _find_x2t()
    env = dict(os.environ)
    if conv:
        env["LD_LIBRARY_PATH"] = conv + ":" + env.get("LD_LIBRARY_PATH", "")
    return subprocess.run([x2t, str(src), str(dst)], capture_output=True, timeout=120, env=env)

MD = """# Trip plan
Some **bold** words & <angle> text.
## Packing
- passport
1. book train
| Item | Cost |
|---|---|
| Train | 1200 |
"""


def _parts_are_xml(path):
    with zipfile.ZipFile(path) as z:
        assert z.namelist()[0] == "[Content_Types].xml"
        for name in z.namelist():
            if name.endswith((".xml", ".rels")):
                ET.fromstring(z.read(name))  # raises if malformed


def test_docx_round_trip(tmp_path):
    p = tmp_path / "a.docx"
    p.write_bytes(office.make_docx(MD, "Trip"))
    _parts_are_xml(p)
    text = office.read(p)
    assert text.splitlines()[:3] == ["# Trip plan", "Some bold words & <angle> text.", "## Packing"]
    assert "- passport" in text and "- book train" in text and "| Train | 1200 |" in text


def test_xlsx_types_formulas_and_leading_zeros(tmp_path):
    p = tmp_path / "b.xlsx"
    p.write_bytes(office.make_xlsx({"Budget": [["Item", "Cost"], ["Train", 1200], ["Hotel", "4000.5"],
                                               ["Total", "=B2+B3"], ["Code", "007"]], "Two": [["x"]]}))
    _parts_are_xml(p)
    rows = office.read_xlsx_rows(p, "Budget", formulas=True)["Budget"]
    assert rows == [["Item", "Cost"], ["Train", "1200"], ["Hotel", "4000.5"], ["Total", "=B2+B3"], ["Code", "007"]]
    with zipfile.ZipFile(p) as z:
        sheet = z.read("xl/worksheets/sheet1.xml").decode()
    assert '<c r="B5" t="s"' in sheet, "a leading-zero code stays text"
    assert '<c r="B2"><v>1200</v>' in sheet.replace(' s="1"', ""), "numbers are numbers"
    assert "fullCalcOnLoad" in zipfile.ZipFile(p).read("xl/workbook.xml").decode()


def _chart_xml(path):
    with zipfile.ZipFile(path) as z:
        assert "xl/charts/chart1.xml" in z.namelist(), z.namelist()
        return z.read("xl/charts/chart1.xml").decode()


def test_xlsx_native_chart_references_cells_and_caches_their_values(tmp_path):
    """A native, editable chart: it points at the sheet's cells (stays live in
    Excel) and caches their values (a viewer draws it without recalculating)."""
    p = tmp_path / "sales.xlsx"
    rows = [["Month", "Sales"], ["Jan", 30], ["Feb", 45], ["Mar", 28], ["Apr", 60]]
    p.write_bytes(office.make_xlsx({"Sheet1": rows}, header=True,
                                   chart={"type": "bar", "title": "Monthly Sales",
                                          "categories": "A2:A5", "values": "B2:B5"}))
    _parts_are_xml(p)  # every part, including the chart and drawing, is well-formed
    chart = _chart_xml(p)
    assert "<c:barChart>" in chart
    assert "Sheet1!$A$2:$A$5" in chart and "Sheet1!$B$2:$B$5" in chart, "it must reference the cells"
    assert "<c:v>30</c:v>" in chart and "<c:v>60</c:v>" in chart, "and cache their values"
    with zipfile.ZipFile(p) as z:
        assert '<drawing r:id="rId1"/>' in z.read("xl/worksheets/sheet1.xml").decode()
        assert "xl/worksheets/_rels/sheet1.xml.rels" in z.namelist()
        ct = z.read("[Content_Types].xml").decode()
        assert "chart+xml" in ct and "drawing+xml" in ct
    # the data still reads back through our own reader with the chart present
    assert "45" in office.read(p)


def test_xlsx_chart_kinds_and_multi_series(tmp_path):
    rows = [["Q", "Sales", "Cost"], ["Q1", 30, 20], ["Q2", 45, 25], ["Q3", 28, 18]]
    line = tmp_path / "line.xlsx"
    line.write_bytes(office.make_xlsx({"Sheet1": rows},
                                      chart={"type": "line", "categories": "A2:A4", "values": "B2:B4"}))
    _parts_are_xml(line)
    assert "<c:lineChart>" in _chart_xml(line)
    multi = tmp_path / "multi.xlsx"
    multi.write_bytes(office.make_xlsx({"Sheet1": rows},
                                       chart={"type": "bar", "categories": "A2:A4",
                                              "values": ["B2:B4", "C2:C4"], "names": ["Sales", "Cost"]}))
    _parts_are_xml(multi)
    assert _chart_xml(multi).count("<c:ser>") == 2, "two value ranges make two series"
    pie = tmp_path / "pie.xlsx"
    pie.write_bytes(office.make_xlsx({"Sheet1": rows},
                                     chart={"type": "pie", "categories": "A2:A4", "values": "B2:B4"}))
    _parts_are_xml(pie)
    assert "<c:pieChart>" in _chart_xml(pie) and "<c:legend>" in _chart_xml(pie)


def test_a_bad_chart_type_is_refused_not_written(tmp_path):
    with pytest.raises(office.OfficeError):
        office.make_xlsx({"Sheet1": [["A", "B"], ["x", 1]]},
                         chart={"type": "bogus", "categories": "A2:A2", "values": "B2:B2"})


def test_pptx_chart_rides_its_own_slide_with_an_embedded_workbook(tmp_path):
    """A deck chart keeps its data in an embedded .xlsx so PowerPoint can edit it;
    that workbook must round-trip through our own reader, and the chart must cache
    the same numbers it references."""
    p = tmp_path / "deck.pptx"
    p.write_bytes(office.make_pptx(
        [{"title": "Q3", "subtitle": "demo"}],
        chart={"type": "bar", "title": "Sales", "categories": ["Jan", "Feb", "Mar"],
               "series": {"Sales": [30, 45, 28], "Cost": [20, 25, 18]}}))
    _parts_are_xml(p)  # every part, chart and embedded workbook rels included
    with zipfile.ZipFile(p) as z:
        names = z.namelist()
        assert "ppt/charts/chart1.xml" in names
        assert "ppt/embeddings/Microsoft_Excel_Worksheet1.xlsx" in names
        chart = z.read("ppt/charts/chart1.xml").decode()
        assert "<c:barChart>" in chart and chart.count("<c:ser>") == 2
        assert "externalData" in chart, "a deck chart links its embedded workbook"
        assert "<c:v>30</c:v>" in chart and "<c:v>18</c:v>" in chart
        assert 'r:id="rId2"' in z.read("ppt/slides/slide2.xml").decode()
        emb = tmp_path / "emb.xlsx"
        emb.write_bytes(z.read("ppt/embeddings/Microsoft_Excel_Worksheet1.xlsx"))
    text = office.read(emb)  # real execution on the data path
    assert "Jan" in text and "45" in text and "Cost" in text, text[:200]


def test_pptx_chart_pie_and_single_series_form(tmp_path):
    p = tmp_path / "pie.pptx"
    p.write_bytes(office.make_pptx([{"title": "x"}],
                                   chart={"type": "pie", "title": "Share", "data": [["A", 60], ["B", 40]]}))
    _parts_are_xml(p)
    with zipfile.ZipFile(p) as z:
        assert "<c:pieChart>" in z.read("ppt/charts/chart1.xml").decode()


def test_the_skill_embeds_a_chart_slide_in_a_deck(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    out = str(tmp_path / "deck.pptx")
    said = od._run({"action": "create", "path": out, "slides": [{"title": "Q3", "subtitle": "demo"}],
                    "chart": {"type": "bar", "title": "Sales", "categories": ["Jan", "Feb"],
                              "series": {"Sales": [30, 45]}}})
    assert "Created" in said, said
    assert "ppt/charts/chart1.xml" in zipfile.ZipFile(out).namelist()


@pytest.mark.skipif(_find_x2t()[0] is None, reason="OnlyOffice x2t not installed")
def test_onlyoffice_reads_the_charts_we_write(tmp_path):
    """Real-reader proof: OnlyOffice's engine converts our chart files and keeps
    the chart. A chart OnlyOffice cannot parse makes x2t exit non-zero or drops
    the chart object; both fail here."""
    xlsx = tmp_path / "chart.xlsx"
    xlsx.write_bytes(office.make_xlsx(
        {"Sheet1": [["M", "Sales"], ["Jan", 30], ["Feb", 45], ["Mar", 28]]},
        chart={"type": "bar", "title": "Sales", "categories": "A2:A4", "values": "B2:B4"}))
    ods = tmp_path / "chart.ods"
    proc = _x2t_convert(xlsx, ods)
    assert proc.returncode == 0 and ods.exists(), proc.stderr[-300:]
    with zipfile.ZipFile(ods) as z:
        obj = next((n for n in z.namelist() if n.endswith("content.xml") and n.startswith("Object")), None)
        assert obj, f"OnlyOffice dropped the chart: {z.namelist()}"
        body = z.read(obj).decode("utf-8", "replace")
        assert "chart:chart" in body and 'chart:class="chart:bar"' in body, "not read back as a bar chart"

    pptx = tmp_path / "deck.pptx"
    pptx.write_bytes(office.make_pptx(
        [{"title": "Q3", "subtitle": "demo"}],
        chart={"type": "pie", "title": "Share", "data": [["A", 60], ["B", 40]]}))
    odp = tmp_path / "deck.odp"
    proc = _x2t_convert(pptx, odp)
    assert proc.returncode == 0 and odp.exists(), proc.stderr[-300:]
    with zipfile.ZipFile(odp) as z:
        assert any(n.startswith("Object") and n.endswith("content.xml") for n in z.namelist()), \
            f"OnlyOffice dropped the deck's chart: {z.namelist()}"


def test_the_skill_embeds_a_chart_on_create(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    out = str(tmp_path / "s.xlsx")
    said = od._run({"action": "create", "path": out,
                    "rows": [["Month", "Sales"], ["Jan", 30], ["Feb", 45]],
                    "chart": {"type": "bar", "categories": "A2:A3", "values": "B2:B3"}})
    assert "Created" in said, said
    assert "xl/charts/chart1.xml" in zipfile.ZipFile(out).namelist()
    # a bad chart is a message, not a crash, and leaves no half-file claimed as good
    bad = od._run({"action": "create", "path": str(tmp_path / "bad.xlsx"),
                   "rows": [["A", "B"], ["x", 1]],
                   "chart": {"type": "nope", "categories": "A2:A2", "values": "B2:B2"}})
    assert "bar, line, pie" in bad, bad


def test_text_cells_use_the_shared_string_table(tmp_path):
    p = tmp_path / "s.xlsx"
    p.write_bytes(office.make_xlsx({"S": [["a", "b", "c"]]}))
    with zipfile.ZipFile(p) as z:
        assert "inlineStr" not in z.read("xl/worksheets/sheet1.xml").decode()
        assert z.read("xl/sharedStrings.xml").decode().count("<si>") == 3


def test_set_cells_keeps_order_style_and_adds_rows(tmp_path):
    p = tmp_path / "b.xlsx"
    p.write_bytes(office.make_xlsx({"S": [["Item", "Cost"], ["Train", 1200]]}))
    p.write_bytes(office.set_cells(p, "s", {"b2": 1500, "C1": "Paid?", "A5": "later", "A1": ""}))
    rows = office.read_xlsx_rows(p)["S"]
    assert rows[0] == ["", "Cost", "Paid?"] and rows[1][:2] == ["Train", "1500"] and rows[4][0] == "later"
    sheet = zipfile.ZipFile(p).read("xl/worksheets/sheet1.xml").decode()
    assert sheet.index('r="B1"') < sheet.index('r="C1"'), "cells stay in column order"
    assert sheet.index('r="2"') < sheet.index('r="5"')
    with pytest.raises(office.OfficeError, match="no sheet named"):
        office.set_cells(p, "Nope", {"A1": 1})
    with pytest.raises(office.OfficeError, match="not a cell reference"):
        office.set_cells(p, "", {"1A": 1})


def test_set_cells_keeps_foreign_namespace_prefixes(tmp_path):
    """mc:Ignorable names prefixes by string; renaming them makes Excel reject the file."""
    p = tmp_path / "x.xlsx"
    p.write_bytes(office.make_xlsx({"S": [["a"]]}))
    raw = zipfile.ZipFile(p).read("xl/worksheets/sheet1.xml").decode().replace(
        "<worksheet ", '<worksheet xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        'xmlns:x14ac="http://schemas.microsoft.com/office/spreadsheetml/2009/9/ac" mc:Ignorable="x14ac" ', 1)
    rebuilt = tmp_path / "y.xlsx"
    with zipfile.ZipFile(p) as src, zipfile.ZipFile(rebuilt, "w") as dst:
        for name in src.namelist():
            dst.writestr(name, raw if name == "xl/worksheets/sheet1.xml" else src.read(name))
    rebuilt.write_bytes(office.set_cells(rebuilt, "", {"B1": 2}))
    out = zipfile.ZipFile(rebuilt).read("xl/worksheets/sheet1.xml").decode()
    assert 'mc:Ignorable="x14ac"' in out and 'xmlns:x14ac="' in out and "ns0:" not in out


def test_pptx_title_and_content_slides(tmp_path):
    p = tmp_path / "c.pptx"
    p.write_bytes(office.make_pptx([{"title": "Trip", "subtitle": "Oct"}, {"title": "Plan", "bullets": ["a", "**b**"]}]))
    _parts_are_xml(p)
    assert office.read(p) == "## Slide 1: Trip\n- Oct\n\n## Slide 2: Plan\n- a\n- b"


def test_docx_replace_across_runs_and_append(tmp_path):
    p = tmp_path / "a.docx"
    p.write_bytes(office.make_docx("Pay the **Train** fare\nTrain again", "x"))
    data, n = office.docx_replace(p, "the Train", "the Bus")  # spans a plain run and a bold run
    p.write_bytes(data)
    assert n == 1 and "Pay the Bus fare" in office.read(p)
    p.write_bytes(office.docx_append(p, "## More\n- item"))
    assert office.read(p).endswith("## More\n- item")


def test_legacy_and_unknown_formats_say_what_to_do(tmp_path):
    (tmp_path / "old.doc").write_bytes(b"\xd0\xcf\x11\xe0")
    with pytest.raises(office.OfficeError, match="OnlyOffice"):
        office.read(tmp_path / "old.doc")
    (tmp_path / "bad.docx").write_bytes(b"not a zip")
    with pytest.raises(office.OfficeError, match="not a valid"):
        office.read(tmp_path / "bad.docx")


def test_rows_from_text():
    assert office.rows_from_text("a,b\n1,2") == [["a", "b"], ["1", "2"]]
    assert office.rows_from_text("| a | b |\n|---|---|\n| 1 | 2 |") == [["a", "b"], ["1", "2"]]


def test_skill_create_edit_undo(tmp_path, gsettings_env):
    import os
    from pathlib import Path
    from shani_chronoa.skills import undo_last_change
    tmp_path = Path(os.environ["HOME"]) / "Documents"
    tmp_path.mkdir()
    sheet = tmp_path / "budget.xlsx"
    out = od._run({"action": "create", "path": str(sheet), "rows": [["Item", "Cost"], ["Rent", 900]]})
    assert out.startswith("Created") and "Rent" in out
    assert "already exists" in od._run({"action": "create", "path": str(sheet), "rows": [["x"]]})
    # **The control is established here, not inherited.** This used to assert the
    # refusal on the strength of the *default*, and `cf525453` ("consent defaults:
    # a fresh install now arrives as the `everyday` preset, not as nothing") ships
    # `file-edit-enabled` **on** - it is classified destructive and carved back
    # out by the preset rule, which
    # `test_editing_files_ships_on_because_it_is_not_destructive` pins. So a
    # default install cannot produce this refusal, the line went red on 2026-10-13,
    # and it read as a gate that had stopped gating rather than as a stale
    # expectation. The gate is fine; the assumption about what it defaults to was
    # not.
    ChronoaConfig().set("file-edit-enabled", "false")
    assert "Refusing" in od._run({"action": "set_cells", "path": str(sheet), "values": {"B2": 950}})
    before = sheet.read_bytes()
    ChronoaConfig().set("file-edit-enabled", "true")
    said = od._run({"action": "set_cells", "path": str(sheet), "values": '{"B2": 950}'})
    assert "B2=950" in said
    assert od._post_condition({"action": "set_cells", "path": str(sheet)})[0] is True
    restored = undo_last_change._run({"path": str(sheet)})
    assert restored.startswith("Restored") and sheet.read_bytes() == before, restored

    doc = tmp_path / "letter.docx"
    od._run({"action": "create", "path": str(doc), "content": "Dear Sam,\nSee you Friday."})
    assert "Replaced 1" in od._run({"action": "replace_text", "path": str(doc), "find": "Friday", "replace": "Monday"})
    assert od._post_condition({"action": "replace_text", "path": str(doc), "replace": "Monday"})[0] is True
    assert "does not appear" in od._run({"action": "replace_text", "path": str(doc), "find": "zzz", "replace": "y"})
    deck = tmp_path / "talk.pptx"
    assert "Slide 2: Why" in od._run({"action": "create", "path": str(deck),
                                       "slides": [{"title": "Talk"}, {"title": "Why", "bullets": ["fast"]}]})
    assert "Slide 1: Talk" in od._run({"action": "read", "path": str(deck)})
    assert "makes .docx" in od._run({"action": "create", "path": str(tmp_path / "x.odt"), "content": "x"})
    assert "outside your home" in od._run({"action": "create", "path": "/tmp/elsewhere.docx", "content": "x"})


def _pdf_with_one_image() -> bytes:
    """A minimal PDF with one 2x2 RGB image XObject, offsets computed so poppler reads it."""
    pixels = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255])
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Resources << /XObject << /Im1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Length 12 >>\nstream\n" + pixels + b"\nendstream",
        b"<< /Length 24 >>\nstream\nq 50 0 0 50 0 0 cm /Im1 Do Q\nendstream",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


@pytest.mark.skipif(__import__("shutil").which("pdfimages") is None, reason="needs poppler")
def test_pdf_images_are_saved(tmp_path):
    import os
    from pathlib import Path
    home = Path(os.environ["HOME"])
    pdf = home / "scan.pdf"
    pdf.write_bytes(_pdf_with_one_image())
    said = od._run({"action": "pdf_images", "path": str(pdf)})
    assert "Saved 1 picture" in said, said
    assert len(list((home / "scan-images").glob("image-*.png"))) == 1
    assert "not empty" in od._run({"action": "pdf_images", "path": str(pdf)})
