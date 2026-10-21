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
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Pango  # noqa: E402

from shani_chronoa.gui.surfaces import common  # noqa: E402
from shani_chronoa.words import plural

logger = logging.getLogger(__name__)

TITLE = "Connections"
ICON = "network-server-symbolic"
SECTION = "Phone and remote"
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
    body.set_wrap_mode(Pango.WrapMode.WORD_CHAR)  # paths have no spaces
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


def _mcp_servers_group(app: Any) -> Gtk.Widget:
    """The client half: servers whose tools Chronoa itself may use.

    The group above offers Chronoa *to* other apps; nothing let Chronoa use
    a tool another program serves. Paste servers in the same `mcpServers`
    shape Claude Desktop uses (a whole config works), save, and each server's
    tools join Chronoa's as `mcp_<server>_<tool>` - gated by "Let Chronoa use
    tools from MCP servers", so they ask first until that is on.
    """
    import json
    from gi.repository import GLib
    from shani_chronoa import mcp_client

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    group = common.group(
        "Use other MCP servers in Chronoa",
        "Tools from servers you add here appear beside Chronoa's own. Same JSON "
        "as Claude Desktop's mcpServers; a whole config can be pasted.")
    box.append(group)
    rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    box.append(rows)

    def draw_status() -> None:
        common.clear(rows)
        found = mcp_client.status()
        configured = mcp_client.servers()
        if not configured:
            rows.append(common.row("No servers yet", "Add one below and save."))
        for name in configured:
            rows.append(common.row(name, found.get(name, "not checked yet")))

    config = getattr(app, "config", None)
    current = config.get(mcp_client.SETTING, "") if config is not None else ""
    try:
        current = json.dumps(json.loads(current), indent=2) if current.strip() else ""
    except ValueError:
        pass
    editor = Gtk.TextView()
    editor.add_css_class("monospace")
    editor.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    editor.get_buffer().set_text(current or
                                 '{\n  "example": {"command": "npx", "args": ["-y", "some-mcp-server"]}\n}')
    editor.update_property([Gtk.AccessibleProperty.LABEL], ["MCP servers, as JSON"])
    frame = Gtk.Frame()
    frame.set_child(common.scrolled(editor, max_content_height=220))
    box.append(frame)
    note = Gtk.Label(label="", xalign=0.0, wrap=True)
    note.add_css_class("dim-label")
    save = Gtk.Button(label="Save and reload")
    save.add_css_class("suggested-action")
    save.set_halign(Gtk.Align.START)

    def on_save(_button) -> None:
        buffer = editor.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        try:
            if text.strip():
                json.loads(text)
        except ValueError as exc:
            note.set_label(f"Not saved - that is not valid JSON: {exc}")
            return
        if config is None:
            note.set_label("Not saved - there is no settings store here.")
            return
        config.set(mcp_client.SETTING, text.strip())
        note.set_label("Saved. Asking each server for its tools…")
        app.activate_action("reload-mcp", None)
        before = mcp_client.status()
        tries = [0]

        def poll() -> bool:
            tries[0] += 1
            if mcp_client.status() != before or tries[0] > 60:
                draw_status()
                note.set_label("Done." if mcp_client.status() else
                               "No servers answered.")
                return False
            return True

        GLib.timeout_add(500, poll)

    save.connect("clicked", on_save)
    box.append(save)
    box.append(note)
    draw_status()
    return box


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
            f"{plural(len(bridges), 'chat bridge')} running, gateways: "
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
    body.append(_mcp_servers_group(app))
    bridge_group = common.group(
        "Chat channels (Telegram, WhatsApp)",
        "A separate bridge program carries messages into Chronoa. It reads its "
        "token from its own environment, so Chronoa never holds it - which is "
        "why there is no token field here.")
    body.append(bridge_group)
    unaccepted = []
    for channel in CHANNELS:
        listening = channel in gateways
        _add(bridge_group, common.row(
            CHANNEL_NAMES[channel],
            ("Chronoa is accepting it" if listening else
             f"not accepted - add '{channel}' to Gateways in Settings, Privacy")))
        if not listening:
            unaccepted.append(channel)
    if unaccepted:
        # **One button for the group, not one per row.** The fix is the same
        # edit to the same list for every channel, so a button on each would be
        # three identical buttons leading to one page. `gateways` is written by
        # `settings_window/privacy.py`'s `_gateway_rows` and nowhere else, so
        # that page is the only place the answer can change - and this sentence
        # has been naming it with nothing to press since the panel was written.
        _add(bridge_group, common.wire_page_button(
            common.open_settings_button("Open Privacy settings",
                                        "settings:privacy"),
            app, "settings:privacy"))
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
