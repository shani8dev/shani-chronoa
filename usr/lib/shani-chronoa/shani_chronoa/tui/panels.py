"""Sidebar and detail text for the full-screen TUI.

Pure formatting: no D-Bus, no engine imports, no GTK. Inventory payloads are
redacted, then rendered with the same ``display.lines()`` the slash commands
use, then sanitised and clipped for the terminal. One sanitizer comes from
``tui.canvas``; a local fallback keeps this module usable in a partial tree.
"""

from __future__ import annotations

from shani_chronoa.tui import display

try:
    from shani_chronoa.tui.canvas import sanitize as _sanitize
except Exception:  # noqa: BLE001 - canvas lands with the paint group
    import re as _re

    _CSI = _re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
    _OSC = _re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?")

    def _sanitize(text: str) -> str:
        value = _CSI.sub("", _OSC.sub("", str(text)))
        return "".join(
            ch
            for ch in value
            if ch not in "\r\n"
            and ord(ch) >= 0x20
            and ord(ch) != 0x7F
            and not 0x80 <= ord(ch) <= 0x9F
        )


#: Four sidebar groups, in frame order. Five rows each keeps a 27-row sidebar.
_GROUPS = (
    ("CONVERSATIONS", "conversations"),
    ("SKILLS", "skills"),
    ("MODELS", "models"),
    ("STATUS", "status"),
)
_GROUP_ROWS = 5
_SECRET_KEY = ("token", "secret", "password")


def redact(value):
    """Copy ``value`` with secret values replaced by ``<set>``.

    A key is secret when it ends in ``-api-key``/``_api_key`` or contains
    ``token``, ``secret`` or ``password``. A row with ``secret: true`` has its
    ``value``/``password`` hidden too. The boolean ``secret`` flag is kept so
    ``display.lines()`` still applies its own skip rule.
    """
    if isinstance(value, dict):
        secret_row = value.get("secret") is True
        out = {}
        for key, item in value.items():
            name = str(key).lower()
            if key == "secret" and isinstance(item, bool):
                out[key] = item
            elif (
                name.endswith("-api-key")
                or name.endswith("_api_key")
                or any(word in name for word in _SECRET_KEY)
                or (secret_row and name in ("value", "password"))
            ):
                out[key] = "<set>"
            else:
                out[key] = redact(item)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


def _clip(line: str, width: int) -> str:
    if width < 1:
        return ""
    line = _sanitize(line)
    return line if len(line) <= width else line[: width - 1] + "…"


def panel_lines(data, width: int, *, limit: int = 30, title: str = "") -> list[str]:
    """Bounded, sanitised ``display.lines()`` output for one payload."""
    if limit < 1:
        return []
    lines = [_sanitize(line) for line in display.lines(redact(data))]
    if title:
        lines.insert(0, _sanitize(title))
    if len(lines) > limit:
        lines = lines[: limit - 1] + ["…"]
    return [_clip(line, width) for line in lines]


def _rows(section: str, payload, width: int) -> list[str]:
    """Body rows of one Inventory payload, formatted by ``display.lines()``."""
    items = payload
    if isinstance(payload, dict) and "items" in payload:
        items = payload["items"]
    lines = display.lines(redact(items), section)
    if isinstance(items, list):
        lines = lines[1:]  # drop the "section (n):" heading
    return [_clip(line, width) for line in lines] or ["  - none"]


def sidebar_lines(loader, width: int) -> list[str]:
    """Render CONVERSATIONS / SKILLS / MODELS / STATUS, five rows each."""
    out: list[str] = []
    for index, (title, section) in enumerate(_GROUPS):
        if index:
            out.append("")
        out.append(_clip(title, width))
        entry = loader.snapshot(section)
        if not isinstance(entry, dict):
            out.append(_clip("  loading…", width))
            continue
        if entry.get("error"):
            out.append(_clip(f"  error: {entry['error']}", width))
            continue
        payload = entry.get("data")
        if payload is None:
            out.append(_clip("  loading…", width))
            continue
        out.extend(_rows(section, payload, width)[:_GROUP_ROWS])
    return out


def detail_lines(section: str, data, width: int) -> list[str]:
    """Render the detail pane for one section."""
    if data is None:
        return [_clip("  loading…", width)]
    return panel_lines(data, width, title=section.replace("_", " ").upper())
