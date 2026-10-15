#!/usr/bin/env python3
"""Hold a MoYoung watch and see what makes it drop - one condition per run.

    tools/watch_probe.py MAC MODE [SECONDS]

MODE is what is sent once the watch is held (then nothing, until SECONDS):

    idle            nothing at all - the control
    music-burst     a long now-playing title, fragments written back to back
                    (what `Watch.request` did)
    music-paced     the same packet, one fragment at a time with a pause
    music-single    the same packet in ONE write (needs the ATT MTU to allow it)
    music-14        a title short enough that the packet is one 20-byte write
    weather-burst   the four weather packets, burst
    weather-paced   the four weather packets, paced
    weather-single  the four weather packets, each in one write
    music-real      the music packets through `Watch.request` (what the app sends)
    weather-real    the weather packets through `Watch.request`
    short           a one-fragment packet (music state), as a single-write control

It prints one line per event with seconds since connect - "write", "drop"
(with BlueZ's Disconnected reason when it gives one) - and, after a drop, how
long until the watch advertises again: a reboot takes it off the air for
seconds; a plain link loss does not.

It uses the real transport (`moyoung.BluezWatch`), so it needs the watch not
held by the companion and not connected to a phone's Da Fit.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "lib", "shani-chronoa"))

from shani_chronoa import moyoung  # noqa: E402

TITLE = "Some Long Song Title - An Artist With A Long Name (Live)"
PACE = 0.06


def packets(mode: str):
    if mode == "music-14":
        return [(moyoung.CMD_SET_MUSIC_INFO, b"\x00" + TITLE[:14].encode("utf-8")),
                (moyoung.CMD_SET_MUSIC_STATE, b"\x01")]
    if mode.startswith("music"):
        return [(moyoung.CMD_SET_MUSIC_INFO, b"\x00" + TITLE.encode("utf-8")),
                (moyoung.CMD_SET_MUSIC_STATE, b"\x01")]
    if mode.startswith("weather"):
        daily = {"weather_code": [1, 2, 3, 61, 1, 2, 3, 0], "temperature_2m_max": [31] * 8,
                 "temperature_2m_min": [21] * 8, "sunrise": ["2026-10-14T06:30"],
                 "sunset": ["2026-10-14T18:20"]}
        return moyoung.weather_packets({"weather_code": 1, "temperature_2m": 28}, daily,
                                       "Sangli, Maharashtra", datetime.now())
    if mode == "short":
        return [(moyoung.CMD_SET_MUSIC_STATE, b"\x01")]
    return []


def advertising_again(mac: str, limit: float = 40.0) -> "float | None":
    """Seconds until the watch is seen advertising, or None within `limit`."""
    started = time.monotonic()
    proc = subprocess.Popen(["bluetoothctl", "--timeout", str(int(limit)), "scan", "le"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        for line in proc.stdout:
            if mac in line and ("NEW" in line or "RSSI" in line):
                return time.monotonic() - started
    finally:
        proc.kill()
    return None


def main() -> int:
    mac, mode = sys.argv[1], sys.argv[2]
    hold = float(sys.argv[3]) if len(sys.argv) > 3 else 60.0
    w = moyoung.BluezWatch(mac)
    reason = []
    w.__enter__()
    t0 = time.monotonic()
    say = lambda what: print(f"{time.monotonic() - t0:7.2f}s  {what}", flush=True)  # noqa: E731
    from gi.repository import Gio
    w._subs.append(w.bus.signal_subscribe(
        "org.bluez", "org.bluez.Device1", "Disconnected", w.device, None,
        Gio.DBusSignalFlags.NONE,
        lambda *a: reason.append(a[-1].unpack())))
    mtu = w._prop(w.paths[moyoung.OUT], w._CHAR, "MTU")
    say(f"held ({mode}), battery {w.battery()}%, ATT MTU {mtu}")
    for command, payload in packets(mode):
        if mode.endswith("real"):
            w.request(command, payload, seconds=0.1)
            say(f"request cmd {command} ({len(payload) + 5} B, write size {w.write_size})")
            continue
        packet = moyoung.frame(command, payload)
        size = len(packet) if mode.endswith("single") else 20
        chunks = [packet[i:i + size] for i in range(0, len(packet), size)]
        for chunk in chunks:
            w._write(chunk)
            if mode.endswith("paced"):
                w._pump(PACE)
        say(f"write cmd {command} ({len(packet)} B in {len(chunks)} fragment(s))")
        w._pump(0.5 if mode.endswith("paced") else 0.05)
    end = time.monotonic() + hold
    dropped = None
    while time.monotonic() < end:
        w._pump(0.2)
        if not w._prop(w.device, w._DEV, "Connected"):
            dropped = time.monotonic() - t0
            break
    if dropped is None:
        say(f"still connected after {hold:.0f}s - no drop")
        w.close()
        return 0
    say(f"DROP  reason={reason[-1] if reason else 'none given'}")
    w._connected = False
    w.close()
    back = advertising_again(mac)
    say("advertising again after " + (f"{back:.1f}s" if back is not None else "> 40s (not seen)"))
    return 1


if __name__ == "__main__":
    sys.exit(main())
