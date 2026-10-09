"""The phone's screen on this computer, over plain Bluetooth (RFCOMM).

Android hands its screen only to an app the person allows (MediaProjection) -
no Bluetooth profile and no browser can (measured: Chrome 154 on Android 10 has
no getDisplayMedia even on a secure page; AOSP's Android 10 Bluetooth stack has
no video profile). So the phone runs Chronoa Cast (android/chronoa-cast), which
encodes the screen as H.264 and writes it to this computer over an RFCOMM
channel - no Wi-Fi, no network, no cable.

This side registers that channel with bluez's `ProfileManager1.RegisterProfile`
(no root): bluez publishes the service record and, when the phone connects,
calls `Profile1.NewConnection` with the socket. The bytes are Annex-B H.264, piped
into `ffplay` (this machine's GStreamer has no H.264 decoder; ffmpeg does).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from typing import Optional

logger = logging.getLogger(__name__)

#: Must match CastService.SERVICE in the app.
SERVICE_UUID = "5a7e0c4a-8a2b-4c1d-9e3f-c4f0a5e5c0a5"
PATH = "/dev/shani/chronoa/phone_screen"
XML = """<node><interface name="org.bluez.Profile1">
  <method name="Release"/>
  <method name="NewConnection"><arg type="o" direction="in"/><arg type="h" direction="in"/>
    <arg type="a{sv}" direction="in"/></method>
  <method name="RequestDisconnection"><arg type="o" direction="in"/></method>
</interface></node>"""


def viewer_command(title: str = "Phone screen (Bluetooth)") -> "list[str]":
    """ffplay reading raw H.264 from stdin with as little buffering as it allows."""
    return ["ffplay", "-hide_banner", "-loglevel", "warning", "-window_title", title,
            "-fflags", "nobuffer", "-flags", "low_delay", "-framedrop", "-probesize", "32",
            "-analyzeduration", "0", "-f", "h264", "-i", "-"]


class PhoneScreenReceiver:
    def __init__(self, on_bytes=None):
        self.on_bytes = on_bytes            # tests and measurement; None shows the screen
        self.bus = None
        self._loop = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self.error = ""
        self.received = 0

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="phone-screen", daemon=True)
            self._thread.start()
            self._ready.wait(10)

    def stop(self) -> None:
        loop = self._loop
        if loop is not None:
            loop.get_context().invoke_full(0, lambda: (loop.quit(), False)[1])
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._thread = None

    def _run(self) -> None:
        from gi.repository import Gio, GLib
        ctx = GLib.MainContext.new()
        ctx.push_thread_default()
        try:
            self.bus = Gio.DBusConnection.new_for_address_sync(
                Gio.dbus_address_get_for_bus_sync(Gio.BusType.SYSTEM, None),
                Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
                None, None)
            info = Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0]
            reg = self.bus.register_object(PATH, info, self._method, None, None)
            self._loop = GLib.MainLoop.new(ctx, False)
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)[:200]
            ctx.pop_thread_default()
            self._ready.set()
            return

        def registered(conn, res):
            try:
                conn.call_finish(res)
                logger.info("phone screen: Bluetooth service registered, waiting for Chronoa Cast")
            except GLib.Error as exc:
                self.error = f"bluez refused the service: {exc.message}"
                logger.warning(self.error)
            self._ready.set()
        options = {"Name": GLib.Variant("s", "Chronoa phone screen"), "Role": GLib.Variant("s", "server"),
                   "RequireAuthentication": GLib.Variant("b", True),
                   "RequireAuthorization": GLib.Variant("b", False)}
        self.bus.call("org.bluez", "/org/bluez", "org.bluez.ProfileManager1", "RegisterProfile",
                      GLib.Variant("(osa{sv})", (PATH, SERVICE_UUID, options)), None, 0, 10000, None, registered)
        try:
            self._loop.run()
        finally:
            try:
                self.bus.call_sync("org.bluez", "/org/bluez", "org.bluez.ProfileManager1", "UnregisterProfile",
                                   GLib.Variant("(o)", (PATH,)), None, 0, 3000, None)
            except Exception:  # noqa: BLE001
                pass
            self.bus.unregister_object(reg)
            ctx.pop_thread_default()

    def _method(self, _conn, _sender, _path, _iface, method, params, invocation) -> None:
        if method != "NewConnection":
            invocation.return_value(None)
            return
        fd_list = invocation.get_message().get_unix_fd_list()
        index = params.unpack()[1]
        fd = fd_list.get(index) if fd_list is not None else -1
        device = str(params.unpack()[0])
        invocation.return_value(None)
        if fd < 0:
            logger.warning("phone screen: connection without a socket from %s", device)
            return
        logger.info("phone screen: %s connected", device)
        threading.Thread(target=self._pump, args=(fd, device), name="phone-screen-pump", daemon=True).start()

    def _pump(self, fd: int, device: str) -> None:
        viewer = None
        if self.on_bytes is None:
            if not shutil.which("ffplay"):
                logger.warning("phone screen: ffplay (ffmpeg) is not installed, nothing can show it")
                os.close(fd)
                return
            viewer = subprocess.Popen(viewer_command(), stdin=subprocess.PIPE)
        try:
            while True:
                data = os.read(fd, 65536)
                if not data:
                    break
                self.received += len(data)
                if viewer is not None:
                    viewer.stdin.write(data)
                    viewer.stdin.flush()
                else:
                    self.on_bytes(data)
        except (OSError, BrokenPipeError):
            pass
        finally:
            os.close(fd)
            if viewer is not None:
                try:
                    viewer.stdin.close()
                except OSError:
                    pass
            logger.info("phone screen: %s stopped after %d bytes", device, self.received)


def main() -> None:
    import sys
    import time
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    r = PhoneScreenReceiver()
    r.start()
    if r.error:
        sys.exit(r.error)
    print("Waiting for Chronoa Cast on the phone...", flush=True)
    try:
        time.sleep(float(sys.argv[1]) if len(sys.argv) > 1 else 1e9)
    finally:
        r.stop()


if __name__ == "__main__":
    main()
