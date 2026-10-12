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

from shani_chronoa.gui import blocks  # noqa: E402


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


def test_a_text_preview_shows_the_file_and_is_selectable(tmp_path):
    """The reason to preview inside a conversation is to copy something out."""
    doc = tmp_path / "notes.md"
    doc.write_text("# Title\\n\\nfirst line\\nsecond line\\n", encoding="utf-8")
    block = blocks.ArtifactBlock([doc])
    _preview_button(block, "notes.md").emit("clicked")

    views = [n for n in _nodes(block) if isinstance(n, Gtk.TextView)]
    assert views, "a text preview with no text view in it"
    text = views[0].get_buffer().get_text(
        views[0].get_buffer().get_start_iter(), views[0].get_buffer().get_end_iter(), False)
    assert "first line" in text and "# Title" in text, f"preview shows {text!r}"


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
