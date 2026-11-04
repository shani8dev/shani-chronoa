"""Shani Link, the app: where the phone, watch and device screens went.

Chronoa's Phone, Phone link, Screen share, Devices, Wearables and Android panels
and its Connect a device window moved to shani-link (`shani-link-app`) on
2026-11-04, with the engines they drive. Chronoa keeps one way to each: the
sidebar's "Shani Link" row, and the `connect-device` action (menu, palette)
opening the app on Connect a device. The app is started as its own process -
it is a different app with no assistant in it, and a person may already have it
open (it is single-instance, so this then raises the open window).
"""
from __future__ import annotations

import logging
import shutil

logger = logging.getLogger(__name__)

COMMAND = "shani-link-app"
TITLE = "Shani Link"
SUBTITLE = "Phone, landline, watch and devices - opens the Shani Link app"


def available() -> bool:
    return shutil.which(COMMAND) is not None


def argv(page: str = "", connect: "str | None" = None) -> "list[str]":
    out = [COMMAND]
    if page:
        out.append(f"--page={page}")
    if connect is not None:
        out.append(f"--connect={connect}" if connect else "--connect")
    return out


def open_app(page: str = "", connect: "str | None" = None) -> "tuple[bool, str]":
    """Start (or raise) Shani Link; `(True, "")` or `(False, why)`."""
    if not available():
        return False, "Shani Link is not installed (the shani-link package)."
    try:
        from gi.repository import Gio
        Gio.Subprocess.new(argv(page, connect), Gio.SubprocessFlags.NONE)
    except Exception as exc:  # noqa: BLE001 - said, not raised into a click handler
        logger.warning("could not start %s", COMMAND, exc_info=True)
        return False, f"Shani Link would not start ({exc})."
    return True, ""
