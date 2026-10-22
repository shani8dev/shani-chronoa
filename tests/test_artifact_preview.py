"""An artifact should be previewable without leaving the conversation.

"Open" hands the file to the desktop, which for a picture means leaving the app
and coming back to a conversation you have lost your place in. The preview
expands in place instead - and where there is nothing to show, no control is
offered rather than one that opens something unexpected.
"""

import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import GdkPixbuf, Gtk  # noqa: E402

import pytest  # noqa: E402

from shani_chronoa.gui import blocks  # noqa: E402


@pytest.fixture(autouse=True)
def _default_rendered_view():
    """The Rendered/Source choice is remembered process-wide (as harness remembers
    it per browser), so reset it before each test rather than leak one test's
    toggle into the next."""
    blocks.ArtifactBlock._shared_view["mode"] = "rendered"
    yield


def _nodes(widget):
    queue, out = deque([widget]), []
    while queue:
        node = queue.popleft()
        out.append(node)
        if hasattr(node, "get_first_child"):
            child = node.get_first_child()
            while child is not None:
                queue.append(child)
                child = child.get_next_sibling()
    return out


def _png(path, size=48):
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, size, size)
    pixbuf.fill(0x2266CCFF)
    assert pixbuf.savev(str(path), "png", [], []) is True
    return path


def _preview_button(block, name):
    """The preview toggle, found by its tooltip.

    `emit("clicked")` rather than `button.clicked()`: in GTK 4 `clicked` is a
    signal, and calling it raises `AttributeError: 'Button' object has no
    attribute 'clicked'`. Four tests said so at once.
    """
    for node in _nodes(block):
        if isinstance(node, Gtk.Button):
            tip = node.get_tooltip_text() or ""
            if tip.startswith(f"Preview {name}"):
                return node
    return None


def test_a_picture_offers_a_preview_and_a_zip_does_not(tmp_path):
    block = blocks.ArtifactBlock([_png(tmp_path / "cat.png")])
    assert _preview_button(block, "cat.png") is not None

    archive = tmp_path / "stuff.zip"
    archive.write_bytes(b"PK\\x03\\x04")
    assert _preview_button(blocks.ArtifactBlock([archive]), "stuff.zip") is None, (
        "a zip has nothing to preview, so offering the control is a lie")


def test_a_directory_offers_no_preview(tmp_path):
    folder = tmp_path / "page-images"
    folder.mkdir()
    assert blocks.ArtifactBlock([folder])._preview_kind(folder) is None


def test_a_preview_opens_and_closes_in_place(tmp_path):
    block = blocks.ArtifactBlock([_png(tmp_path / "cat.png")])
    button = _preview_button(block, "cat.png")

    button.emit("clicked")
    frames = [n for n in _nodes(block)
              if isinstance(n, Gtk.Frame) and "artifact-preview" in
              (n.get_css_classes() or [])]
    assert frames, "clicking preview showed nothing in the block"
    assert any(isinstance(n, Gtk.Image) for n in _nodes(frames[0])), (
        "a picture preview with no image in it")

    button.emit("clicked")
    frames = [n for n in _nodes(block)
              if isinstance(n, Gtk.Frame) and "artifact-preview" in
              (n.get_css_classes() or [])]
    assert not frames, "the second click did not close the preview"


def _toggle(block, label):
    for node in _nodes(block):
        if isinstance(node, Gtk.ToggleButton) and node.get_label() == label:
            return node
    return None


def test_a_plain_text_file_shows_its_source_directly(tmp_path):
    """The reason to preview inside a conversation is to copy something out."""
    doc = tmp_path / "notes.txt"
    doc.write_text("first line\nsecond line\n", encoding="utf-8")
    block = blocks.ArtifactBlock([doc])
    _preview_button(block, "notes.txt").emit("clicked")

    views = [n for n in _nodes(block) if isinstance(n, Gtk.TextView)]
    assert views, "a text preview with no text view in it"
    text = views[0].get_buffer().get_text(
        views[0].get_buffer().get_start_iter(), views[0].get_buffer().get_end_iter(), False)
    assert "first line" in text, f"preview shows {text!r}"
    # a .txt is not switchable, so there is no Rendered/Source toggle
    assert _toggle(block, "Source") is None


def test_markdown_renders_by_default_and_a_switch_reveals_its_source(tmp_path):
    """Harness-studio's Files pane shows Markdown rendered with a Source switch;
    this is the same, through the renderer the replies use."""
    doc = tmp_path / "notes.md"
    doc.write_text("# Title\n\nfirst line\nsecond line\n", encoding="utf-8")
    block = blocks.ArtifactBlock([doc])
    _preview_button(block, "notes.md").emit("clicked")

    # Rendered by default: the heading is a formatted label, no raw '# Title'.
    assert _toggle(block, "Rendered").get_active(), "did not start on Rendered"
    assert not any(isinstance(n, Gtk.TextView) for n in _nodes(block)), "source shown, not rendered"
    labels = [lbl.get_text() for n in _nodes(block) if isinstance(n, Gtk.Label)
              for lbl in [n]]
    assert any("Title" in t for t in labels) and not any(t.strip() == "# Title" for t in labels)

    # Flip to Source: now the raw Markdown text is there to copy.
    _toggle(block, "Source").emit("clicked")
    views = [n for n in _nodes(block) if isinstance(n, Gtk.TextView)]
    assert views, "the Source view has no text in it"
    buf = views[0].get_buffer()
    text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
    assert "# Title" in text and "first line" in text, text


def test_a_long_file_says_it_was_cut_rather_than_looking_complete(tmp_path):
    """The failure mode is a preview that looks like the whole file.

    Showing 200 lines of a 40,000-line log with no note reads as "this is the
    file", and the reader draws a conclusion from a truncated thing.
    """
    log = tmp_path / "huge.log"
    log.write_text("\n".join(f"line {i}" for i in range(500)) + "\n",
                   encoding="utf-8")
    block = blocks.ArtifactBlock([log])
    _preview_button(block, "huge.log").emit("clicked")

    view = next(n for n in _nodes(block) if isinstance(n, Gtk.TextView))
    buffer = view.get_buffer()
    text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
    assert "cut off at 200 lines" in text, (
        f"a truncated preview did not say so; it ends {text[-80:]!r}")
    assert "line 499" not in text, "the whole file was shown, so the cut is not exercised"


def test_an_svg_chart_previews_as_a_picture(tmp_path):
    """The chart skill writes SVG; it must show here, not only offer 'Open'."""
    from shani_chronoa import charts
    svg = tmp_path / "sales.svg"
    svg.write_text(charts.bar_chart([("Jan", 30), ("Feb", 45)], title="Sales"), encoding="utf-8")
    block = blocks.ArtifactBlock([svg])
    assert block._preview_kind(svg) == "image"
    button = _preview_button(block, "sales.svg")
    assert button is not None, "an SVG offered no preview"
    button.emit("clicked")
    assert any(isinstance(n, Gtk.Image) for n in _nodes(block)), "no picture for the SVG"


def test_an_office_document_previews_as_its_text(tmp_path):
    from shani_chronoa import office
    doc = tmp_path / "budget.xlsx"
    doc.write_bytes(office.make_xlsx({"Sheet1": [["Item", "Cost"], ["Train", 1200]]}))
    block = blocks.ArtifactBlock([doc])
    assert block._preview_kind(doc) == "office"
    _preview_button(block, "budget.xlsx").emit("clicked")
    views = [n for n in _nodes(block) if isinstance(n, Gtk.TextView)]
    assert views, "no text view for the office document"
    buf = views[0].get_buffer()
    text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
    assert "Train" in text and "1200" in text, f"office preview shows {text!r}"


def test_a_csv_previews_as_a_table_with_a_heading_row(tmp_path):
    csv = tmp_path / "scores.csv"
    csv.write_text("name,score\nAda,9\nGrace,10\n", encoding="utf-8")
    block = blocks.ArtifactBlock([csv])
    assert block._preview_kind(csv) == "table"
    _preview_button(block, "scores.csv").emit("clicked")
    grids = [n for n in _nodes(block) if isinstance(n, Gtk.Grid)]
    assert grids, "a CSV preview with no table in it"
    labels = [n for n in _nodes(grids[0]) if isinstance(n, Gtk.Label)]
    texts = [lbl.get_text() for lbl in labels]
    assert "name" in texts and "Grace" in texts, texts
    header = next(lbl for lbl in labels if lbl.get_text() == "name")
    assert "heading" in (header.get_css_classes() or []), "the header row is not marked"


def test_html_falls_back_to_source_without_a_display(tmp_path, monkeypatch):
    """An HTML page renders in a web view where one is possible; with no display
    (the test environment) it shows its source rather than crashing on a web view."""
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    page = tmp_path / "dash.html"
    page.write_text("<!doctype html><html><body><h1>Hi</h1></body></html>", encoding="utf-8")
    block = blocks.ArtifactBlock([page])
    assert _preview_button(block, "dash.html") is not None
    _preview_button(block, "dash.html").emit("clicked")
    # no web view was built, so there is no Rendered/Source toggle - just the source
    assert _toggle(block, "Rendered") is None, "a web view was attempted with no display"
    # the source is shown: a preview frame with the file's text (HTML source is a
    # markup Label through the code highlighter, plain text is a TextView)
    frames = [n for n in _nodes(block) if isinstance(n, Gtk.Frame)
              and "artifact-preview" in (n.get_css_classes() or [])]
    assert frames, "no source preview was shown for the HTML file"
    shown = [n for n in _nodes(frames[0]) if isinstance(n, (Gtk.TextView, Gtk.Label))]
    assert shown, "the HTML source preview is empty"


def test_a_code_file_preview_is_syntax_coloured(tmp_path):
    """Harness-studio's Files pane colours code; this does too, through the same
    highlighter the reply code blocks use."""
    src = tmp_path / "calc.py"
    src.write_text("def add(a, b):\n    return a + b  # sum\n", encoding="utf-8")
    block = blocks.ArtifactBlock([src])
    _preview_button(block, "calc.py").emit("clicked")
    labels = [n for n in _nodes(block) if isinstance(n, Gtk.Label) and n.get_use_markup()]
    coloured = [n for n in labels if "<span" in (n.get_label() or "")]
    assert coloured, "a .py preview had no syntax-coloured markup"
    # a plain .txt stays an ordinary text view, not highlighted
    plain = tmp_path / "n.txt"
    plain.write_text("just text\n", encoding="utf-8")
    b2 = blocks.ArtifactBlock([plain])
    _preview_button(b2, "n.txt").emit("clicked")
    assert any(isinstance(n, Gtk.TextView) for n in _nodes(b2))


def test_rows_carry_a_type_icon_and_every_name_resolves(tmp_path):
    """Each row shows a kind icon (harness's typed file list); a name that does
    not resolve would draw a broken glyph, so pin that every one exists."""
    from gi.repository import Gdk
    icons = set(blocks.ArtifactBlock._TYPE_ICONS.values()) | {
        "text-editor-symbolic", "text-x-generic-symbolic", "folder-symbolic"}
    display = Gdk.Display.get_default()
    if display is not None:
        theme = Gtk.IconTheme.get_for_display(display)
        missing = [n for n in icons if not theme.has_icon(n)]
        assert missing == [], f"artifact type icons missing from the theme: {missing}"
    # a non-image file's row has an icon image
    doc = tmp_path / "report.xlsx"
    from shani_chronoa import office
    doc.write_bytes(office.make_xlsx({"S": [["A", "B"], ["x", 1]]}))
    block = blocks.ArtifactBlock([doc])
    assert any(isinstance(n, Gtk.Image) for n in _nodes(block)), "the row has no type icon"


def test_an_image_preview_is_capped_rather_than_decoded_at_full_size(tmp_path):
    """The cost here is the decoded surface, not the width on screen."""
    big = tmp_path / "big.png"
    assert GdkPixbuf.Pixbuf.new(
        GdkPixbuf.Colorspace.RGB, False, 8, 2400, 2400).savev(
            str(big), "png", [], []) is True
    block = blocks.ArtifactBlock([big])
    _preview_button(block, "big.png").emit("clicked")
    images = [n for n in _nodes(block) if isinstance(n, Gtk.Image)]
    assert images, "no image in the preview"
    width = images[0].get_paintable().get_width()
    assert width <= 480, f"decoded at {width}px wide; the cap is 480"
