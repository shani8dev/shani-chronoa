"""Render the first page of a PDF to a pixbuf, with poppler's `pdftoppm`.

This lives apart from `gui/blocks.py` on purpose. `blocks.py` carries the "no
shell exec" boundary (`test_reply_blocks` asserts it runs nothing), so the one
place a preview needs an external converter is here, where the rule does not
apply - the artifact viewer calls this rather than shelling out itself.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_TIMEOUT = 30


def first_page_pixbuf(pdf_path: Path, max_width: int = 480, max_height: int = 640):
    """A GdkPixbuf of the PDF's first page scaled into the box, or None.

    None when poppler's `pdftoppm` is missing, the file is not a readable PDF, or
    anything in the conversion fails - the caller then offers no preview rather
    than a broken one.
    """
    if not shutil.which("pdftoppm"):
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="chronoa-pdf-") as tmp:
            prefix = Path(tmp) / "page"
            proc = subprocess.run(
                ["pdftoppm", "-png", "-f", "1", "-l", "1", "-scale-to", "640",
                 str(pdf_path), str(prefix)],
                capture_output=True, timeout=_TIMEOUT, check=False)
            pages = sorted(Path(tmp).glob("page*.png"))
            if proc.returncode != 0 or not pages:
                return None
            from gi.repository import GdkPixbuf
            return GdkPixbuf.Pixbuf.new_from_file_at_scale(
                str(pages[0]), max_width, max_height, True)
    except Exception as exc:  # noqa: BLE001 - a failed render is "no preview"
        logger.info("no pdf preview for %s: %s", pdf_path.name, exc)
        return None
