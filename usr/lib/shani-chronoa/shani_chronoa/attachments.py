"""Files attached to a message - dropped on the window or added with the
paperclip - and how the assistant hears about them.

The model cannot see a file; it can only be told where one is. So each
attached file is described by its absolute path, kind and size in a short
block after the user's words, and the tools that act on files (edit_image,
convert_media, read_document, read_text_file, ...) take it from there. Only
the path is shared - nothing is read, uploaded or copied by attaching.
"""

import mimetypes
from pathlib import Path
from typing import Iterable, List, Tuple

MAX_FILES = 20


def kind(path: Path) -> str:
    mime, _ = mimetypes.guess_type(path.name)
    if not mime:
        return "file"
    top = mime.split("/")[0]
    return {"image": "image", "audio": "audio", "video": "video", "text": "text"}.get(top, mime)


def size(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def accept(paths: Iterable, current: List[Path]) -> List[Path]:
    """`current` plus the regular files among `paths`, no duplicates, at most MAX_FILES."""
    out = list(current)
    for p in paths:
        p = Path(p).expanduser()
        try:
            p = p.resolve()
        except OSError:
            continue
        if p.is_file() and p not in out and len(out) < MAX_FILES:
            out.append(p)
    return out


def block(paths: List[Path]) -> str:
    """What the assistant is told, after the user's words; '' with nothing attached."""
    if not paths:
        return ""
    lines = []
    for p in paths:
        try:
            lines.append(f"- {p} ({kind(p)}, {size(p.stat().st_size)})")
        except OSError:
            lines.append(f"- {p} (no longer readable)")
    return ("\n\n[Attached files - refer to them by these paths when using tools:\n"
            + "\n".join(lines) + "]")


def take(paths: List[Path]) -> Tuple[List[str], str]:
    """(names to show in the transcript, block to send) for one message."""
    return [p.name for p in paths], block(paths)
