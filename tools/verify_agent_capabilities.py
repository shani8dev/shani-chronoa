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
        status = client.inventory("status")  # the seven legacy methods still work
        print(f"status answering model: {status['items']['llm_model']}")

        # Migration ran in the engine process with a COPY of the person's
        # dconf file. Show that their non-default values landed in the
        # throwaway settings.json, and that nothing was written to their home.
        from shani_chronoa import settings_file, settings_table

        migrated = json.loads(settings_file.user_path().read_text())
        copied = {
            key: value
            for key, value in migrated.items()
            if key in settings_table.TABLE and value != settings_table.default_for(key)
        }
        assert migrated.get(settings_file.MIGRATION_KEY) is True, migrated
        print(
            f"migrated non-default values ({len(copied)}): "
            + ", ".join(f"{k}={v!r}" for k, v in sorted(copied.items())[:8])
        )

        # End-to-end: the real CLI binary against the real engine on this bus.
        cli = REPO / "usr/bin/chronoa"
        for argv in (
            ("models", "--json"),
            ("settings", "--json"),
            ("settings", "get", "privacy-mode", "--json"),
            ("settings", "set", "privacy-mode", "false"),
            ("settings", "set", "privacy-mode", "false", "--yes", "--json"),
        ):
            run = subprocess.run(
                [sys.executable, str(cli), *argv],
                capture_output=True,
                text=True,
                timeout=30,
                env=os.environ.copy(),
            )
            expected = 2 if argv[-1] == "false" else 0
            assert run.returncode == expected, (argv, run.returncode, run.stderr)
            if expected == 0:
                payload = json.loads(run.stdout.strip().splitlines()[-1])
                assert isinstance(payload, dict) and payload, (argv, payload)
            else:
                assert "--yes" in run.stderr, run.stderr
            print(f"cli {' '.join(argv)}: rc={run.returncode}")

        # /settings in a pty: the same method, driven as a person types it.
        import os as _os
        import pty
        import select

        master, slave = pty.openpty()
        import fcntl
        import struct
        import termios

        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
        term = subprocess.Popen(
            [sys.executable, str(cli)],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=os.environ.copy(),
            close_fds=True,
            start_new_session=True,
        )
        _os.close(slave)
        screen = ""
        try:
            deadline = time.monotonic() + 20
            while "chronoa>" not in screen and time.monotonic() < deadline:
                ready, _, _ = select.select([master], [], [], 0.1)
                if ready:
                    screen += _os.read(master, 65536).decode("utf-8", "replace")
            _os.write(master, b"/settings get privacy-mode\n")
            deadline = time.monotonic() + 20
            while (
                "Privacy mode" not in screen
                and "privacy mode" not in screen
                and time.monotonic() < deadline
            ):
                ready, _, _ = select.select([master], [], [], 0.1)
                if ready:
                    screen += _os.read(master, 65536).decode("utf-8", "replace")
            assert "Privacy mode" in screen or "privacy mode" in screen, screen

            # Panes and overlays against the real engine, not just the stand-in.
            def pump(seconds):
                nonlocal screen
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    ready, _, _ = select.select([master], [], [], 0.05)
                    if ready:
                        screen += _os.read(master, 65536).decode("utf-8", "replace")

            _os.write(master, b"\t")  # focus the sidebar
            pump(0.6)
            assert "focus: sidebar" in screen, screen[-2000:]
            for key in (b"\t", b"\x10", b"\x0f", b"\x12", b"\x03", b"\x1b"):
                _os.write(master, key)
                pump(0.2)
            assert "focus:" in screen
            _os.write(master, b"/quit\n")
            term.wait(timeout=5)
            print("pty /settings get privacy-mode: ok")
        finally:
            try:
                _os.close(master)
            except OSError:
                pass

        # The GTK app must still start on a private Broadway display and read
        # the same layered settings. No window ever touches the person's X/Wayland.
        broadway = subprocess.Popen(
            ["gtk4-broadwayd", ":94"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.5)
        gtk_env = os.environ.copy()
        gtk_env.update(
            GDK_BACKEND="broadway", BROADWAY_DISPLAY=":94", GSK_RENDERER="cairo"
        )
        gtk_env.pop("WAYLAND_DISPLAY", None)
        gtk_env.pop("DISPLAY", None)
        app = subprocess.Popen(
            [sys.executable, str(REPO / "usr/bin/shani-chronoa")],
            env=gtk_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        time.sleep(6)
        assert app.poll() is None, app.stderr.read().decode("utf-8", "replace")
        print("gtk app started on private Broadway :94")
        app.terminate()
        try:
            app.wait(timeout=8)
        except subprocess.TimeoutExpired:
            app.kill()
        broadway.terminate()
        try:
            broadway.wait(timeout=5)
        except subprocess.TimeoutExpired:
            broadway.kill()
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

    # A private runtime dir keeps dconf's own cache from reusing the person's
    # session bus socket; the migration must read only the copied user file.
    runtime = tmp / "runtime"
    runtime.mkdir()
    os.chmod(runtime, 0o700)

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
        XDG_RUNTIME_DIR=str(runtime),
        GSETTINGS_SCHEMA_DIR=str(schema_dir),
        # No keyring daemon on this private bus, and no STT server: a probe
        # must answer from the copied settings, not wait for a service that
        # is not there (the same guard tests/conftest.py uses).
        SHANI_CHRONOA_KEYRING=env.get("SHANI_CHRONOA_KEYRING", "0"),
        SHANI_CHRONOA_STT_SERVER=env.get("SHANI_CHRONOA_STT_SERVER", "0"),
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
