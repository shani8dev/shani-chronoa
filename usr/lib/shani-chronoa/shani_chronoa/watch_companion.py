"""Keep a MoYoung watch connected, so its own buttons work with this computer.

What a phone's Da Fit app does, done here instead:

- **Find my phone** on the watch (command 98) rings this computer, with a
  notification; it stops when the watch says so or you press Stop.
- **Music buttons** on the watch (103: play/pause, previous, next, play, pause)
  drive the player through `media_control`; **volume** (4/5) moves this
  computer's output; **reject** (3) hangs up the phone's call.
- **Camera shutter** on the watch (102) takes a photo with `take_photo`.
- **Now playing** is pushed to the watch's music screen (68/123), and the
  watch's clock is set on every connect (49).

The watch takes one connection, so holding it **locks the Da Fit app on a
phone out** - which is why this has its own switch, `watch-companion-enabled`,
off by default, on top of `bluetooth-gatt-enabled`. While it holds the watch,
every other request (the `watch` skill, the Phone panel) is served through its
socket (`moyoung.CompanionClient`) instead of opening a second connection.

All the events and their payloads are Gadgetbridge's (`MoyoungConstants`,
`MoyoungDeviceSupport.handlePacket`).
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

KEY = "watch-companion-enabled"
GATT_KEY = "bluetooth-gatt-enabled"
#: The two pushes Chronoa makes on its own initiative, each now off by default.
#: Both used to be unconditional, and they are the only traffic this companion
#: generates that the watch did not ask for:
#:
#: - now-playing: two writes (68, 123) **every five seconds** for as long as the
#:   watch is held, to fill a screen nobody asked for;
#: - weather: read on connect and again whenever the watch asks.
#:
#: The watch's own buttons are unaffected by either. See the two schema keys.
#:
#: **Neither of these reboots the watch, and an earlier version of this comment
#: blamed the weather push. That was wrong.** The control that settles it:
#: connected with **nothing sent at all** - no sync-time, no weather, no music -
#: the watch still dropped, at 150s (measured 2026-10-08). It reboots on its own.
#:
#: How it came to look causal, recorded because the mistake is easy to repeat: a
#: bisection sending each weather packet alone kept finding the link dead just
#: after a packet went out. Re-running showed the *same* 15-byte packet both
#: surviving and killing it, so the packet was never the independent variable.
#: Bounding the location label - which looked like a clean cause, 6 characters
#: surviving three times and 8+ always dying - changed nothing, because those
#: runs differed in when they were done, not in what they sent.
#:
#: The switches stay because unrequested traffic is unrequested traffic: the
#: now-playing push writes twice every five seconds to fill a screen nobody asked
#: for. Not because either was shown to end the watch's day.
NOWPLAYING_KEY = "watch-nowplaying-enabled"
WEATHER_KEY = "watch-weather-enabled"
CMD_FIND_MY_PHONE, CMD_CAMERA, CMD_PHONE_OPERATION = 98, 102, 103
#: The watch's AI-voice button. Not in Gadgetbridge; measured on the FB BGS002:
#: pressing it sends 0xF9 [01 01 00] then [01 01 01]; when the watch stops
#: listening, 0xF9 [02 00]. 01 = start, 02 = stop.
CMD_AI_VOICE = 0xF9
#: CMD_NOTIFY_WEATHER_CHANGE: the watch asking for the weather (measured: sent on connect).
CMD_WANTS_WEATHER = 100
#: CMD_NOTIFY_PHONE_OPERATION's argument -> what it asks of this computer.
OPERATIONS = {0: ("media", "toggle"), 1: ("media", "previous"), 2: ("media", "next"),
              3: ("call", "hangup"), 4: ("volume", "+"), 5: ("volume", "-"),
              6: ("media", "play"), 7: ("media", "pause")}
RING_SOUND = "/usr/share/sounds/freedesktop/stereo/phone-incoming-call.oga"
RETRY_SECONDS = 30
#: How close together two AI-voice packets have to be for the second one to be a
#: repeat rather than a new press. Measured 2026-10-08 on the FB BGS002: a press
#: and the watch giving up on its own session were 5s apart, and the give-up
#: packet then repeated every 5-10s with nobody touching the watch. 15s is above
#: both - a deliberate second press is never that fast - while still letting a
#: real press land after a pause.
VOICE_REARM_GUARD = 15.0


def _tool(name: str, arguments: dict) -> str:
    from shani_chronoa import tools
    return str(tools.execute_tool(name, arguments))


class Ringer:
    """This computer ringing for the watch's "find my phone", until stopped."""

    def __init__(self):
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def ringing(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.ringing:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="watch-find-ring", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        deadline = time.monotonic() + 120             # never ring forever
        while not self._stop.is_set() and time.monotonic() < deadline:
            try:
                proc = subprocess.Popen(["pw-play", RING_SOUND], stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL)
            except OSError:
                return
            while proc.poll() is None:
                if self._stop.wait(0.2):
                    proc.terminate()
                    break


class WatchCompanion:
    def __init__(self, config: Any, actions: Optional[Callable[[str, dict], str]] = None,
                 on_voice: Optional[Callable[[bool], None]] = None):
        self.config = config
        self.on_voice = on_voice
        self._voice_on = False
        self._voice_at = 0.0
        #: When an AI-voice packet last arrived, of either shape. The cooldown
        #: runs from here rather than from the last state change: this watch's
        #: give-up packets repeat faster than any single window, so a window
        #: restarted by each of its own packets is never reached.
        self._voice_packet_at = 0.0
        self.tool = actions or _tool
        self.ringer = Ringer()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.watch = None
        self.server: Optional[socket.socket] = None
        self.last_track = None
        self.handled: "list[tuple[int, bytes]]" = []      # events acted on, newest last (for tests/status)
        self.seen_unknown: set = set()
        self.outbox: "list[tuple[int, bytes]]" = []    # packets for the watch, sent by the holding thread
        self._weather_at = 0.0
        #: Whether the weather switch was on when the current hold began. An
        #: attribute rather than a call, because `on_event` runs on the
        #: companion's thread and re-reading GSettings per packet would be both
        #: needless traffic and a value that could change mid-packet.
        self._weather_push = False

    def _allowed(self) -> bool:
        try:
            return bool(self.config.get_bool(KEY, False)) and bool(self.config.get_bool(GATT_KEY, False))
        except Exception:  # noqa: BLE001
            return False

    def _switch(self, key: str) -> bool:
        """Read one of the two opt-in push switches, defaulting to off.

        A config that cannot answer is treated as off, like `_allowed`: a
        switch whose value is unknown must not turn a push on.
        """
        try:
            return bool(self.config.get_bool(key, False))
        except Exception:  # noqa: BLE001
            return False

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="watch-companion", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.ringer.stop()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None

    # --- the loop -----------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self._allowed():
                self._stop.wait(RETRY_SECONDS)
                continue
            try:
                from shani_chronoa.skills import watch as watch_skill
                picked = watch_skill._pick("")
                if isinstance(picked, tuple):
                    self.hold(picked[0])
            except Exception:  # noqa: BLE001 - out of range, held by a phone: try again later
                logger.info("watch companion: not connected this time", exc_info=True)
            self._stop.wait(RETRY_SECONDS)

    def hold(self, mac: str) -> None:
        """Connect, serve and listen until the watch goes away or the switch is turned off."""
        from shani_chronoa import moyoung
        # Read once per hold, not per loop: the two switches exist to keep
        # unrequested traffic off the watch, and a poll of GSettings five times a
        # second to learn a value that changes once a session is the traffic.
        now_playing = self._switch(NOWPLAYING_KEY)
        weather = self._switch(WEATHER_KEY)
        self._weather_push = weather       # read by on_event, on another thread
        with moyoung.DirectWatch(mac) as w:
            self.watch = w
            # Connecting counts as AI-voice traffic. The guard's clock started at
            # zero, so the watch's own [02 00] right after a connect was always a
            # "press" - measured 2026-10-09: the real app opened the microphone 9 s
            # after connecting, before anyone touched the watch.
            self._voice_packet_at = time.monotonic()
            w.request(moyoung.CMD_SYNC_TIME, moyoung.time_payload(), seconds=0.3)
            path = moyoung.socket_path(mac)
            try:
                os.unlink(path)
            except OSError:
                pass
            self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.server.bind(path)
            os.chmod(path, 0o600)
            self.server.listen(4)
            self.server.settimeout(0.05)
            logger.info("watch companion: holding %s%s", mac,
                       " (now-playing and weather off)" if not (now_playing or weather) else "")
            try:
                last_music = 0.0
                while not self._stop.is_set() and self._allowed():
                    if not w.alive():
                        break
                    w._pump(0.2)
                    self.handle_events(w)
                    while self.outbox:
                        command, payload = self.outbox.pop(0)
                        w.request(command, payload, seconds=0.1)
                    self.serve_one(w)
                    if (now_playing and time.monotonic() - last_music > 5):
                        last_music = time.monotonic()
                        self.push_music(w)
                    # A dropped link is NOT detectable from in here, and three
                    # attempts at it were each falsified by a live run on
                    # 2026-10-08. Recorded so the fourth is not attempted blind:
                    #
                    # - gatttool's pid: survives the drop, so a wedged session
                    #   looks held indefinitely (this is why the loop above is
                    #   the only exit).
                    # - asking the watch (`CMD_QUERY_DND`, a 3s budget, two
                    #   misses): the watch goes quiet on its own screens, so it
                    #   declared a healthy link dead and tore it down - twice,
                    #   once 13s after holding a watch that then answered
                    #   commands for another minute.
                    # - asking bluez (`bluetoothctl info ... Connected: yes`):
                    #   wrong too, for a different reason. That field describes
                    #   *bluez's* link, and gatttool holds a separate one; it
                    #   read "no" while the watch was streaming events and its
                    #   weather arrived successfully.
                    #
                    # What is left is that nothing reports the drop: gatttool
                    # neither exits nor prints "Disconnected" (the string is in
                    # the binary and did not appear), bluez says no, and the
                    # watch keeps answering commands. So this loop only leaves on
                    # the switch, an exit, or a gatttool that actually dies - and
                    # the fix belongs wherever the reboot is fixed, not in a
                    # check that reports a lie. The earlier claim that the
                    # companion "reconnects by itself after a drop" is true for
                    # a drop gatttool notices, and false for this one.
            finally:
                self.server.close()
                self.server = None
                try:
                    os.unlink(path)
                except OSError:
                    pass
                self.watch = None

    # --- the watch asks -------------------------------------------------------------

    def handle_events(self, w) -> None:
        keep = []
        for command, body in w.inbox:
            if command in (CMD_FIND_MY_PHONE, CMD_CAMERA, CMD_PHONE_OPERATION, CMD_AI_VOICE, CMD_WANTS_WEATHER):
                self.on_event(command, body)
            else:
                if (command, body) not in self.seen_unknown:
                    # Kept for whoever is waiting on a reply - and logged once, so a
                    # watch button nobody has mapped yet can be found by pressing it.
                    self.seen_unknown.add((command, body))
                    logger.info("watch companion: unrecognised packet %d %s", command, body.hex())
                keep.append((command, body))
        w.inbox[:] = keep[-50:]       # never let unclaimed packets pile up

    def on_event(self, command: int, body: bytes) -> None:
        self.handled.append((command, body))
        if command == CMD_WANTS_WEATHER:
            if not self._weather_push:
                logger.info("watch companion: the watch asked for the weather and "
                            "watch-weather-enabled is off; not answering")
                return
            if time.monotonic() - self._weather_at > 600:     # the watch asks often; the sky changes slowly
                self._weather_at = time.monotonic()
                threading.Thread(target=self.fetch_weather, name="watch-weather", daemon=True).start()
            return
        if command == CMD_AI_VOICE and body:
            # **The watch's own microphone cannot reach this computer, and that is
            # the watch's design rather than a gap here** (researched 2026-10-08).
            # Da Fit's own manual describes the button as "wake up the AI voice
            # **on your phone** through the watch **when bluetooth calling is
            # connected**" - it is a remote control for the phone's assistant,
            # not a request to listen on the watch. That matches what is
            # measurable:
            #
            # - the command is 0xF9 and is **not in Gadgetbridge's
            #   MoyoungConstants**, and Moyoung support there lists no AI-voice
            #   feature, so the protocol has no such channel;
            # - the watch advertises Audio Sink (0x110b), Handsfree (0x111e) and
            #   A2DP (0x110d) in SDP, but bluez exposes **only
            #   org.bluez.MediaControl1** (AVRCP) for it, with no audio endpoint
            #   child object, so no ALSA card is ever created and PipeWire never
            #   sees a watch microphone. Measured with nothing else connected:
            #   three `bluetoothctl connect` attempts ("Connection successful"
            #   each time - that is the LE link, not a classic one) and direct
            #   `ConnectProfile` on hfp-hands-free / hfp-head-unit / a2dp-sink,
            #   every one failing, with `/proc/asound/cards` staying at the
            #   laptop's own sofhdadsp throughout.
            #
            # So a press starts listening on **this computer's** microphone, which
            # is what Da Fit does with the phone's - not a workaround for a
            # missing watch feature.
            #
            # Two packet shapes, both measured. With a session the watch accepted,
            # a press is [01 ..] and its end [02 00]. With no app answering its
            # handshake (not known yet - Da Fit's reply is in no public source),
            # the watch shows "not connected" and every press is only [02 00], so
            # a lone [02 00] toggles.
            if body[0] == 1:
                start = True
            elif self._voice_on:
                # A [02 00] while a turn is running is that turn's own end, so it
                # is acted on immediately - the guard below guards only starts,
                # and a guarded stop would hold the microphone open until the
                # capture timed out.
                start = False
            else:
                # A lone [02 00] is the press on this firmware (the watch shows
                # "not connected" and sends nothing else), so it has to be able
                # to start a turn. But this watch also keeps sending [02 00]
                # every few seconds with nobody touching it - measured 2026-10-08,
                # a plain start/stop toggle looped the microphone for ten
                # minutes, one "No audio captured" per cycle.
                #
                # The cooldown is measured from the last AI-voice packet **of any
                # kind**, including the stops. That is the fix, not a detail:
                # measured against the real watch, a cooldown restarted by each
                # packet is never reached, because this watch repeats faster than
                # any single window (6s, 7s and 5s gaps re-armed the microphone
                # even though no single gap exceeded it). So only a packet after a
                # real quiet spell counts as a press.
                since = time.monotonic() - self._voice_packet_at
                if since < VOICE_REARM_GUARD:
                    logger.info("watch companion: ignoring a repeat AI-voice packet "
                                "(%.1fs after the last one)", since)
                    # The ignored packet still counts as traffic. Not doing this
                    # was the bug: the stamp never advanced, so the packet after
                    # an ignored one was measured against a stale time and
                    # re-armed the microphone - which is the loop, re-entered
                    # through the guard that was supposed to stop it.
                    self._voice_packet_at = time.monotonic()
                    return
                start = True
            if start != self._voice_on and self.on_voice is not None:
                self._voice_on, self._voice_at = start, time.monotonic()
                self.on_voice(start)
            self._voice_packet_at = time.monotonic()
            return
        if command == CMD_FIND_MY_PHONE:
            if body[:1] == b"\x00":
                self.ringer.start()
                self._notify("Your watch is looking for this computer", "It is ringing here.")
            else:
                self.ringer.stop()
        elif command == CMD_CAMERA:
            threading.Thread(target=lambda: self._notify(
                "Photo from your watch", self.tool("take_photo", {})), daemon=True).start()
        elif command == CMD_PHONE_OPERATION and body:
            kind, what = OPERATIONS.get(body[0], ("", ""))
            if kind == "media":
                threading.Thread(target=lambda: self.tool("media_control", {"action": what}), daemon=True).start()
            elif kind == "call":
                threading.Thread(target=lambda: self.tool("bluetooth_call", {"action": "hangup"}),
                                 daemon=True).start()
            elif kind == "volume":
                r = subprocess.run(["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", f"5%{what}"],
                                   capture_output=True, text=True, timeout=5, check=False)
                level = self.volume()
                logger.info("watch companion: volume %s -> rc %s, now %s", what, r.returncode, level)
                if level is not None and self.watch is not None:
                    # ARG_OPERATION_SEND_CURRENT_VOLUME (12): the watch's own slider, 0-16.
                    self.watch.request(CMD_PHONE_OPERATION, bytes([12, round(16 * min(level, 1.0))]),
                                       seconds=0.1)

    def fetch_weather(self) -> None:
        """Today and the week ahead, through the weather skill's own source and gates."""
        from datetime import datetime
        from shani_chronoa import moyoung
        from shani_chronoa.config import ChronoaConfig
        from shani_chronoa.skills import weather
        try:
            config = ChronoaConfig()
            if not config.sense_allowed("web"):
                logger.info("watch weather not sent: %s", config.sense_allowed_reason("web"))
                return
            where = weather._here()
            if isinstance(where, str):
                logger.info("watch weather not sent: %s", where)
                return
            lat, lon, label = where
            data = weather._get(weather.FORECAST, {
                "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "timezone": "auto", "forecast_days": 8,
                "current": "temperature_2m,weather_code",
                "daily": "temperature_2m_max,temperature_2m_min,weather_code,sunrise,sunset"})
            self.outbox += moyoung.weather_packets(data.get("current", {}), data.get("daily", {}),
                                                   "" if label == "here" else label, datetime.now())
            logger.info("watch weather queued: %s C, code %s", data.get("current", {}).get("temperature_2m"),
                        data.get("current", {}).get("weather_code"))
        except Exception:  # noqa: BLE001 - no weather is not a broken watch
            logger.info("watch weather not sent", exc_info=True)

    @staticmethod
    def volume() -> "Optional[float]":
        r = subprocess.run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], capture_output=True, text=True,
                           timeout=5, check=False)
        try:
            return float(r.stdout.split()[1])
        except (IndexError, ValueError):
            return None

    @staticmethod
    def now_playing() -> str:
        """The player's state, read directly - never through the tool dispatch.

        Polled every 5 s, and through `tools.execute_tool` that tripped the
        repeat guard after 35 calls (measured): `media_control` was then refused
        for everyone, including the watch's own music buttons.
        """
        from shani_chronoa.skills import media_control as mc
        found = mc.players()
        if found:
            playing = [p for p in found if mc._status(p) == "Playing"]
            target = (playing or found)[0]
            state = mc._status(target)
            return f"x is {state.lower()}" + (f": {mc._now_playing(target)}." if state != "Stopped" else ".")
        from shani_chronoa import phone_bluez
        phones = phone_bluez.avrcp_players()
        if phones:
            state, now = phone_bluez.avrcp_status(phones[0][0])
            return f"x is {state.lower()}" + (f": {now}." if now else ".")
        return ""

    def push_music(self, w) -> None:
        """What is playing, to the watch's music screen (68: 0 track / 1 artist; 123: playing)."""
        from shani_chronoa import moyoung
        try:
            text = self.now_playing()
        except Exception:  # noqa: BLE001
            return
        if text == self.last_track:
            return
        self.last_track = text
        playing = " is playing" in text
        title = text.split(": ", 1)[1].rstrip(".") if ": " in text else ""
        if title:
            w.request(moyoung.CMD_SET_MUSIC_INFO, b"\x00" + title[:60].encode("utf-8"), seconds=0.1)
        w.request(moyoung.CMD_SET_MUSIC_STATE, b"\x01" if playing else b"\x00", seconds=0.1)

    # --- other callers ask (the skill, the panel) -------------------------------------

    def serve_one(self, w) -> None:
        try:
            conn, _ = self.server.accept()
        except (socket.timeout, BlockingIOError, OSError):
            return
        with conn:
            conn.settimeout(5)
            data = b""
            try:
                while not data.endswith(b"\n"):
                    chunk = conn.recv(65536)
                    if not chunk:
                        return
                    data += chunk
                msg = json.loads(data)
                args = [bytes.fromhex(a["hex"]) if isinstance(a, dict) and "hex" in a else a
                        for a in msg.get("args", [])]
                method = msg.get("method")
                if method not in ("request", "read_steps_now", "measure"):
                    raise ValueError(f"unknown method {method!r}")
                conn.settimeout(None)
                result = getattr(w, method)(*args)
                if isinstance(result, (bytes, bytearray)):
                    result = {"hex": bytes(result).hex()}
                reply = {"result": result}
            except Exception as exc:  # noqa: BLE001 - said to the caller, not raised here
                reply = {"error": str(exc)[:200]}
            try:
                conn.sendall((json.dumps(reply, default=str) + "\n").encode())
            except OSError:
                pass
            self.handle_events(w)

    def _notify(self, title: str, body: str) -> None:
        try:
            subprocess.run(["notify-send", "--app-name=Shani Chronoa", title, body],
                           capture_output=True, timeout=5, check=False)
        except OSError:
            pass
