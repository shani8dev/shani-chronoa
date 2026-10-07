"""Connections: the two ways something other than this window reaches Chronoa.

Both were complete and had no UI at all:

- **The MCP server** (`mcp.py`, the `shani-chronoa-mcp` binary) exposes the same
  whitelisted skills to Claude Desktop, Claude Code, Cursor and any other MCP
  client. Nothing told a person it existed, whether it could run here, or what
  to put in a client's configuration.
- **The channel bridge** (`channel_bridge.py`, the `shani-chronoa-bridge` binary)
  carries Telegram and WhatsApp messages into the inbound gateway. Its tokens
  come from the bridge's own environment on purpose - Chronoa never sees them -
  so this panel does not ask for one. It says whether the gateway is listening,
  whether a bridge is running, and the exact command that starts one.

Everything here is read when the panel is built; nothing is started, stopped or
written. Starting a bridge is left to the person, because it needs their token.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, List

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa.gui.surfaces import common  # noqa: E402

logger = logging.getLogger(__name__)

TITLE = "Connections"
ICON = "network-server-symbolic"
SECTION = "Acting"
SUBTITLE = ("Other apps and chat channels that can use Chronoa, and how to "
            "connect them. Read-only: nothing here starts anything.")

MCP_BINARY = "shani-chronoa-mcp"
BRIDGE_BINARY = "shani-chronoa-bridge"
CHANNELS = ("telegram", "whatsapp")
CHANNEL_NAMES = {"telegram": "Telegram", "whatsapp": "WhatsApp"}


def _binary(name: str) -> str:
    """The installed path of one of Chronoa's own programs, or "" if absent.

    `PATH` first, then the tree this module was loaded from, so a checkout run
    in place still finds its own `usr/bin`.
    """
    found = shutil.which(name)
    if found:
        return found
    # surfaces, gui, shani_chronoa, shani-chronoa, lib, usr
    local = Path(__file__).resolve().parents[5] / "bin" / name
    return str(local) if local.exists() else ""


def _mcp_available() -> "tuple[bool, str]":
    try:
        from shani_chronoa import mcp
        return bool(mcp.is_available()), ""
    except Exception as exc:  # noqa: BLE001 - a panel must not raise
        return False, f"{type(exc).__name__}: {exc}"


def _running(binary: str) -> List[str]:
    """Command lines of running processes started from `binary`, from /proc.

    Read-only, and only this user's processes are readable - which is the right
    answer, since a bridge is run by the person whose token it holds.
    """
    out: List[str] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        words = [w.decode("utf-8", "replace") for w in argv if w]
        if any(Path(w).name == binary for w in words[:2]):
            out.append(" ".join(words[1:] if Path(words[0]).name.startswith("python") else words))
    return out


def _gateways(app: Any) -> List[str]:
    config = getattr(app, "config", None)
    try:
        raw = config.get("gateways", "") if config is not None else ""
    except Exception:  # noqa: BLE001
        raw = ""
    return [g.strip().lower() for g in str(raw or "").split(",") if g.strip()]


def _copy_block(title: str, text: str) -> Gtk.Widget:
    """A card holding text a person will paste somewhere, with a Copy button.

    Its own card under the group, not a row inside it: a non-row child added to
    a preferences group is drawn after every row, so the first version showed
    all the commands piled up below the list, none beside the row it belonged
    to, and the JSON's line breaks gone.
    """
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    card.add_css_class("card")
    for side in ("start", "end", "top", "bottom"):
        getattr(card, f"set_margin_{side}")(0 if side in ("start", "end") else 4)
    head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    for side in ("start", "end", "top"):
        getattr(head, f"set_margin_{side}")(10)
    name = Gtk.Label(label=title, xalign=0.0, hexpand=True)
    name.add_css_class("heading")
    head.append(name)
    button = Gtk.Button(label="Copy", valign=Gtk.Align.CENTER)
    button.set_tooltip_text("Copy this to the clipboard")
    button.update_property([Gtk.AccessibleProperty.LABEL], [f"Copy {title}"])

    def copy(widget: Gtk.Button) -> None:
        widget.get_clipboard().set(text)
        widget.set_label("Copied")

    button.connect("clicked", copy)
    head.append(button)
    card.append(head)
    body = Gtk.Label(label=text, xalign=0.0, selectable=True, wrap=True)
    body.set_wrap_mode(2)  # Pango.WrapMode.WORD_CHAR - paths have no spaces
    body.add_css_class("monospace")
    for side in ("start", "end", "bottom"):
        getattr(body, f"set_margin_{side}")(10)
    card.append(body)
    card._copy_text = text
    return card


def _add(group: Gtk.Widget, child: Gtk.Widget) -> None:
    adder = getattr(group, "add", None)
    (adder or group.append)(child)


def mcp_client_config(binary: str) -> str:
    """The `mcpServers` entry Claude Desktop and most MCP clients read."""
    return ('{\n  "mcpServers": {\n    "shani-chronoa": {\n'
            f'      "command": "{binary}"\n    }}\n  }}\n}}')


def build(app: Any) -> Gtk.Widget:
    page, set_content = common.surface(TITLE, SUBTITLE)
    body = common.page_body()
    for side in ("start", "end", "top", "bottom"):
        getattr(body, f"set_margin_{side}")(12)
    recorder = common.StatusRecorder()

    mcp_ok, mcp_why = _mcp_available()
    mcp_bin = _binary(MCP_BINARY)
    gateways = _gateways(app)
    bridges = _running(BRIDGE_BINARY)
    bridge_bin = _binary(BRIDGE_BINARY)

    # One status for the panel: green when the MCP server can run, which is
    # the connection that needs nothing from the person but a config line.
    # Neither being set up is a choice, so it is "off", not a fault.
    if mcp_ok and mcp_bin:
        body.append(recorder.row(
            common.STATUS_OK, "Other apps can connect over MCP",
            f"{len(bridges)} chat bridge(s) running, gateways: "
            f"{', '.join(gateways) or 'none'}"))
    else:
        body.append(recorder.row(
            common.STATUS_OFF, "The MCP server cannot run here yet",
            mcp_why or ("the 'mcp' Python package is not installed" if not mcp_ok
                        else f"{MCP_BINARY} was not found")))

    # --- MCP -----------------------------------------------------------------
    mcp_group = common.group(
        "Use Chronoa from other apps (MCP)",
        "Claude Desktop, Claude Code, Cursor and other MCP clients can call the "
        "same whitelisted skills you use here, under the same consent switches.")
    body.append(mcp_group)
    _add(mcp_group, common.row(
        "Server", "ready - add it to a client below" if mcp_ok and mcp_bin else
        ("needs the 'mcp' Python package (python-mcp)" if not mcp_ok
         else f"{MCP_BINARY} is not installed")))
    if mcp_bin:
        body.append(_copy_block("Claude Code", f"claude mcp add shani-chronoa {mcp_bin}"))
        body.append(_copy_block("Claude Desktop and other clients (config JSON)",
                                mcp_client_config(mcp_bin)))

    # --- channel bridge --------------------------------------------------------
    bridge_group = common.group(
        "Chat channels (Telegram, WhatsApp)",
        "A separate bridge program carries messages into Chronoa. It reads its "
        "token from its own environment, so Chronoa never holds it - which is "
        "why there is no token field here.")
    body.append(bridge_group)
    for channel in CHANNELS:
        listening = channel in gateways
        _add(bridge_group, common.row(
            CHANNEL_NAMES[channel],
            ("Chronoa is accepting it" if listening else
             f"not accepted - add '{channel}' to Gateways in Settings, Privacy")))
    _add(bridge_group, common.row(
        "Running bridges",
        "; ".join(bridges) if bridges else "none running for this user"))
    if bridge_bin:
        body.append(_copy_block(
            "Start the Telegram bridge",
            f"CHRONOA_TELEGRAM_TOKEN=<your bot token> {bridge_bin} telegram"))
        body.append(_copy_block(
            "Start the WhatsApp bridge",
            "CHRONOA_WHATSAPP_TOKEN=<token> CHRONOA_WHATSAPP_PHONE_NUMBER_ID=<id> "
            "CHRONOA_WHATSAPP_VERIFY_TOKEN=<verify> CHRONOA_WHATSAPP_APP_SECRET=<secret> "
            f"{bridge_bin} whatsapp"))
    else:
        _add(bridge_group, common.row("Bridge program", f"{BRIDGE_BINARY} is not installed"))

    set_content(common.scrolled(body))
    page.status = recorder.status
    page.mcp_ready = mcp_ok and bool(mcp_bin)
    page.gateways = gateways
    return page


__all__ = ["TITLE", "ICON", "SECTION", "build", "mcp_client_config"]
