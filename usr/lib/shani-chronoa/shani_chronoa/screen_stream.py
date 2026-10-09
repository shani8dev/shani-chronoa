"""Show this computer's screen on the phone, over the Bluetooth network.

The phone shares its network over Bluetooth (tethering), this computer joins it
as `bnep0`, and Chrome on the phone opens a page served on that address - so the
picture travels over Bluetooth, and nothing is reachable from Wi-Fi (the server
binds the Bluetooth address only).

- **Capture**: GNOME on Wayland lets nothing read the screen except through the
  ScreenCast portal (`org.freedesktop.portal.ScreenCast`), which asks the person
  once ("Share screen") and hands back a PipeWire stream.
- **Encode**: GStreamer `pipewiresrc` -> 640x360 at 8 fps -> VP8 at ~400 kbit/s ->
  live WebM. Sized from a measurement: the link carried 61-72 KB/s (~500 kbit/s)
  both ways through the phone, 72 ms latency.
- **Play**: Chrome plays a live WebM in a plain `<video>`, so the phone needs no
  app, and plain HTTP works (unlike Web Bluetooth, which needs HTTPS).

Measured problems on the way are in AGENTS.md: Ubuntu's udev renamed bnep0 to
enx<mac> and bluez dropped the link (fixed with a .link file, NamePolicy=keep
kernel); and the phone hangs up 25 ms after connecting unless its own
Bluetooth tethering is on.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

logger = logging.getLogger(__name__)

PORT = 8090
WIDTH, HEIGHT, FPS, BITRATE = 640, 360, 8, 400_000


def bluetooth_address() -> Optional[str]:
    """This computer's IPv4 address on a Bluetooth network (bnep*), or None."""
    out = subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True).stdout
    m = re.search(r"^\d+:\s+(bnep\d+)\s+inet\s+([\d.]+)/", out, re.M)
    return m.group(2) if m else None


# --- the portal -------------------------------------------------------------------------

class ScreenCast:
    """One portal session: `start()` -> (pipewire fd, node id). Asks the person once."""

    PORTAL = ("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop")
    IFACE = "org.freedesktop.portal.ScreenCast"

    def __init__(self):
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.sender = self.bus.get_unique_name().lstrip(":").replace(".", "_")
        self.session = ""

    def _request(self, method: str, args, timeout: int = 120) -> dict:
        """Call a portal method and wait for its Request's Response signal."""
        GLib = self.GLib
        token = "chronoa" + secrets.token_hex(4)
        path = f"/org/freedesktop/portal/desktop/request/{self.sender}/{token}"
        box, loop = {}, GLib.MainLoop()

        def on_response(_c, _s, _p, _i, _sig, params):
            code, results = params.unpack()
            box["code"], box["results"] = code, results
            loop.quit()
        sub = self.bus.signal_subscribe("org.freedesktop.portal.Desktop", "org.freedesktop.portal.Request",
                                        "Response", path, None, 0, on_response)
        try:
            options = dict(args[-1]) if args else {}
            options["handle_token"] = GLib.Variant("s", token)
            variant = GLib.Variant(self._sig(method), (*args[:-1], options))
            self.bus.call_sync(*self.PORTAL, self.IFACE, method, variant, None, 0, 10000, None)
            GLib.timeout_add_seconds(timeout, loop.quit)
            loop.run()
        finally:
            self.bus.signal_unsubscribe(sub)
        if box.get("code") != 0:
            raise RuntimeError(f"screen sharing was not allowed ({'cancelled' if box.get('code') == 1 else 'no answer' if 'code' not in box else 'failed'})")
        return box["results"]

    @staticmethod
    def _sig(method: str) -> str:
        return {"CreateSession": "(a{sv})", "SelectSources": "(oa{sv})", "Start": "(osa{sv})"}[method]

    def start(self) -> "tuple[int, int]":
        GLib = self.GLib
        res = self._request("CreateSession", [{"session_handle_token": GLib.Variant("s", "chronoa_s")}])
        self.session = res["session_handle"]
        self._request("SelectSources", [self.session, {"types": GLib.Variant("u", 1),          # monitor
                                                       "cursor_mode": GLib.Variant("u", 2),    # cursor drawn in
                                                       "multiple": GLib.Variant("b", False)}])
        res = self._request("Start", [self.session, "", {}], timeout=180)       # the person answers here
        node = res["streams"][0][0]
        fds_out = self.bus.call_with_unix_fd_list_sync(
            *self.PORTAL, self.IFACE, "OpenPipeWireRemote", GLib.Variant("(oa{sv})", (self.session, {})),
            None, 0, 10000, None, None)
        result, fdlist = fds_out[0], fds_out[1]
        fd = fdlist.get(result.unpack()[0])
        return fd, int(node)

    def close(self) -> None:
        if self.session:
            try:
                self.bus.call_sync("org.freedesktop.portal.Desktop", self.session,
                                   "org.freedesktop.portal.Session", "Close", None, None, 0, 3000, None)
            except Exception:  # noqa: BLE001
                pass
            self.session = ""


# --- encoding and serving ----------------------------------------------------------------

PAGE = """<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Chronoa screen</title><style>body{margin:0;background:#000;color:#aaa;font:14px sans-serif}
video{width:100vw;height:100vh;object-fit:contain}p{position:fixed;bottom:4px;left:8px;margin:0}</style></head>
<body><video src="/screen.webm" autoplay muted playsinline></video>
<p>This computer's screen, over Bluetooth</p></body></html>"""


def pipeline_description(fd: int, node: int) -> str:
    return (f"pipewiresrc fd={fd} path={node} do-timestamp=true keepalive-time=1000 ! "
            f"videoconvert ! videoscale ! videorate ! "
            f"video/x-raw,width={WIDTH},height={HEIGHT},framerate={FPS}/1 ! "
            f"vp8enc deadline=1 cpu-used=8 target-bitrate={BITRATE} keyframe-max-dist={FPS * 2} "
            f"end-usage=cbr ! webmmux streamable=true ! appsink name=out emit-signals=true sync=false "
            f"max-buffers=64 drop=true")


class StreamServer:
    """One viewer at a time; each viewer gets its own encoder from the shared portal stream."""

    def __init__(self, fd: int, node: int, host: str, port: int = PORT):
        self.fd, self.node, self.host, self.port = fd, node, host, port
        self.httpd: Optional[ThreadingHTTPServer] = None

    def serve(self) -> None:
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        Gst.init(None)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                logger.info("screen stream: %s - %s", self.client_address[0], fmt % args)

            def do_GET(self):
                if self.path in ("/", "/index.html"):
                    body = PAGE.encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path != "/screen.webm":
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "video/webm")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                pipe = Gst.parse_launch(pipeline_description(os.dup(owner.fd), owner.node))
                sink = pipe.get_by_name("out")
                stop = threading.Event()

                def on_sample(appsink):
                    buf = appsink.emit("pull-sample").get_buffer()
                    ok, info = buf.map(Gst.MapFlags.READ)
                    if ok:
                        try:
                            self.wfile.write(bytes(info.data))
                        except OSError:
                            stop.set()
                        finally:
                            buf.unmap(info)
                    return Gst.FlowReturn.OK
                sink.connect("new-sample", on_sample)
                pipe.set_state(Gst.State.PLAYING)
                logger.info("screen stream: streaming to %s", self.client_address[0])
                try:
                    while not stop.wait(0.5):
                        msg = pipe.get_bus().pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS)
                        if msg is not None:
                            logger.warning("screen stream: pipeline ended: %s",
                                           msg.parse_error()[0].message if msg.type == Gst.MessageType.ERROR else "EOS")
                            break
                finally:
                    pipe.set_state(Gst.State.NULL)
                    logger.info("screen stream: viewer %s left", self.client_address[0])

        ThreadingHTTPServer.allow_reuse_address = True
        self.httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        logger.info("screen stream: http://%s:%d", self.host, self.port)
        self.httpd.serve_forever()

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()


def main() -> None:
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    host = bluetooth_address()
    if host is None:
        sys.exit("No Bluetooth network: turn on Bluetooth tethering on the phone and connect to it first.")
    cast = ScreenCast()
    fd, node = cast.start()
    server = StreamServer(fd, node, host)
    threading.Thread(target=server.serve, daemon=True).start()
    print(f"Open http://{host}:{PORT} on the phone", flush=True)
    try:
        threading.Event().wait(float(sys.argv[1]) if len(sys.argv) > 1 else 1e9)
    finally:
        server.stop()
        cast.close()


if __name__ == "__main__":
    main()
