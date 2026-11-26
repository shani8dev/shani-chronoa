#!/usr/bin/env python3
"""Live check: the real headless engine on a private bus answers every method.

Run from the worktree root:

    python3 tools/verify_agent_capabilities.py

The child gets a throwaway HOME/XDG tree, a compiled copy of the repo's
settings schema and a copy of dconf's user file, and symlinks the installed
llm/whisper data folders so the engine sees the person's models without ever
writing to their folders. `dbus-run-session` gives it its own bus. Every
additive Agent method is called once; the script exits non-zero on a D-Bus
failure (a capability that returns {"error": ...} is a valid answer here - an
absent model or a refused consent is truthful, not a transport failure).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "usr/lib/shani-chronoa"
LAUNCHER = REPO / "usr/bin/shani-chronoa-engine"


def _owner_ok(connection) -> bool:
    from gi.repository import Gio, GLib

    from shani_chronoa.agent_contract import BUS_NAME

    try:
        reply = connection.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "GetNameOwner",
            GLib.Variant("(s)", (BUS_NAME,)),
            GLib.VariantType("(s)"),
            Gio.DBusCallFlags.NONE,
            2000,
            None,
        )
        return bool(reply.unpack()[0])
    except Exception:
        return False


def _child() -> int:
    sys.path.insert(0, str(PKG))
    from gi.repository import Gio

    from shani_chronoa.agent_capabilities import METHODS
    from shani_chronoa.agent_contract import BUS_NAME
    from shani_chronoa.tui.agent import AgentClient

    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    log = Path(os.environ["HOME"]) / "engine.log"
    handle = log.open("w")
    engine = subprocess.Popen(
        [sys.executable, str(LAUNCHER)], stdout=handle, stderr=subprocess.STDOUT
    )
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline and not _owner_ok(connection):
        if engine.poll() is not None:
            break
        time.sleep(0.1)
    if not _owner_ok(connection):
        handle.flush()
        print(
            f"engine never owned {BUS_NAME}; rc={engine.poll()}\n{log.read_text()}",
            file=sys.stderr,
        )
        return 2

    client = AgentClient()
    answers = {}
    args_for = {
        "SettingsGet": ("privacy-mode",),
        "SettingsSet": ("privacy-mode", "true", False),
        "SettingsPreset": ("chat", False),
        "SettingsEmail": ("{}", False),
        "ModelsUse": ("local", False),
        "SetupStep": ("brain", "", False),
        "ConversationsResume": ("no-such-conversation",),
        "ConversationsRename": ("no-such-conversation", "Renamed"),
        "ConversationsDelete": ("no-such-conversation", False),
        "MemoryDecide": ("none:0", "yes", False),
        "RoutinesRun": ("no-such-routine",),
        "NoticesDismiss": ("no-such-notice",),
    }
    try:
        for name, method in sorted(METHODS.items()):
            args = args_for.get(name, tuple("" for _ in method.args))
            if len(args) != len(method.args):
                raise AssertionError(
                    f"{name}: {len(args)} args for {len(method.args)} parameters"
                )
            result = client.capability(name, *args)
            answers[name] = result
            status = result.get("error") or "ok"
            print(f"{name}: {status}")
        client.inventory("status")  # the seven legacy methods still work
    finally:
        client.close()
        engine.terminate()
        try:
            engine.wait(timeout=5)
        except subprocess.TimeoutExpired:
            engine.kill()
        handle.close()
    print(f"called {len(answers)} additive methods on the real engine")
    return 0


def _parent() -> int:
    if os.environ.get("CHRONOA_LIVE_CHILD"):
        return _child()

    tmp = Path(tempfile.mkdtemp(prefix="chronoa-live-caps-"))
    home = tmp / "home"
    data = home / ".local/share"
    config = home / ".config"
    data.mkdir(parents=True)
    config.mkdir(parents=True)
    for name in ("llm", "whisper"):
        source = Path.home() / ".local/share/shani-chronoa" / name
        if source.exists():
            (data / "shani-chronoa").mkdir(parents=True, exist_ok=True)
            link = data / "shani-chronoa" / name
            if not link.exists():
                link.symlink_to(source)
    dconf = Path.home() / ".config/dconf/user"
    if dconf.exists():
        (config / "dconf").mkdir(exist_ok=True)
        shutil.copy2(dconf, config / "dconf/user")

    schema_src = REPO / "usr/share/glib-2.0/schemas"
    schema_dir = tmp / "schemas"
    schema_dir.mkdir()
    for xml in schema_src.glob("*.xml"):
        shutil.copy2(xml, schema_dir / xml.name)
    compiled = subprocess.run(
        ["glib-compile-schemas", str(schema_dir)], capture_output=True, text=True
    )
    if compiled.returncode or not (schema_dir / "gschemas.compiled").exists():
        print(f"schema compile failed: {compiled.stderr}", file=sys.stderr)
        return 2

    env = os.environ.copy()
    env.update(
        HOME=str(home),
        XDG_CONFIG_HOME=str(config),
        XDG_DATA_HOME=str(data),
        XDG_CACHE_HOME=str(tmp / "cache"),
        GSETTINGS_SCHEMA_DIR=str(schema_dir),
        # No keyring daemon on this private bus, and no STT server: a probe
        # must answer from the copied settings, not wait for a service that
        # is not there (the same guard tests/conftest.py uses).
        SHANI_CHRONOA_KEYRING="0",
        SHANI_CHRONOA_STT_SERVER="0",
        PYTHONDONTWRITEBYTECODE="1",
        CHRONOA_LIVE_CHILD="1",
    )
    env.pop("WAYLAND_DISPLAY", None)
    env.pop("DISPLAY", None)
    result = subprocess.run(
        ["dbus-run-session", "--", sys.executable, str(__file__)], env=env, timeout=180
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(_parent())
