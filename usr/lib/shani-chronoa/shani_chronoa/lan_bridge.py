"""Drive Chronoa from the phone, over the same Wi-Fi it is already on.

**The architecture, and why the phone never talks to Chronoa directly.**

`gateway.py` is the security boundary and it is on the **session bus** - no port,
no listener, no network. That is deliberate: a gateway is "another program on
this machine", and whatever carries a message to it (WhatsApp, Telegram, a
meeting transcript) is a separate component with its own credentials. So a phone
cannot call `Submit` and never will. What a phone can do is send a message to a
*program on this machine*, and this is that program.

So the shape is exactly `channel_bridge.py`'s - a thin client that turns one
channel's messages into `Submit(gateway, text)` calls - with one difference in
transports:

    Telegram  : chat update  -> gateway.Submit -> reply printed
    this      : KDE Connect  -> gateway.Submit -> reply pushed back as a
                                 notification on the phone

**Why the clipboard packet is the command channel.** KDE Connect has exactly one
general-purpose *text* packet that both sides speak - `kdeconnect.clipboard`,
with a `content` field - and `kdeconnect.clipboard.connect` is in both the
phone's incoming and outgoing capability lists. Using it is honest about what it
is: whatever a person copies on the phone becomes a turn, exactly as a pasted
message does in the window. It is not a covert path, because the phone shows what
it sent.

**What the phone gets back.** The reply is pushed as a `kdeconnect.notification`,
so a question asked on the phone is answered on the phone. Printing it to this
process's log would leave a person with no way to know it arrived.

**Every gate still applies.** The text enters the same turn a person's keystrokes
enter: same consent keys, same whitelist, same post-conditions, same log. This
program can no more run a skill than a typing finger can, which is the whole
argument `gateway.py` makes.
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Optional

logger = logging.getLogger(__name__)

GATEWAY_NAME = "phone"

#: Clipboard content longer than this is not a command. `gateway.MAX_TEXT` is the
#: gateway's own cap and is enforced there; this one exists so a screenshot or a
#: document somebody copied never becomes a 4000-character turn.
MAX_COMMAND = 500

POLL_SECONDS = 3.0


def build_parser():
    """The CLI, so the launcher stays a one-line wrapper."""
    import argparse
    parser = argparse.ArgumentParser(
        prog="shani-chronoa-lan-bridge",
        description="Drive Chronoa from a KDE Connect phone on the same Wi-Fi.")
    parser.add_argument("--phone", default="",
                        help="the phone's address; empty means the device seen by "
                             "'lan_phone devices'. **Not `--host`,** which in this "
                             "codebase means the address a *server* binds - six "
                             "other sites, all a loopback constant. Naming a "
                             "destination `--host` is how someone later 'fixes' "
                             "it to bind the phone's address.")
    parser.add_argument("--once", action="store_true",
                        help="handle what has arrived and exit, rather than looping")
    return parser


class LanBridge:
    """One phone's messages, turned into turns and back."""

    def __init__(self, host: str = "",
                 connection=None,
                 submit=None,
                 send=None):
        self.host = host
        self._connection = connection          # tests hand in a private bus
        self._submit = submit                 # tests replace the D-Bus call
        self._send = send                     # tests replace the notification
        self._seen: "set[int]" = set()

    # -- the gateway half ------------------------------------------------
    def submit(self, text: str) -> str:
        """`gateway.Submit("phone", text)` and the reply, or a sentence.

        **The try wraps both paths, not just the D-Bus one.** A test seam that
        raises, and a real bus that refuses, are the same event to a caller: the
        bridge must neither die nor let the phone see nothing.
        """
        try:
            if self._submit is not None:
                return self._submit(GATEWAY_NAME, text)
            from shani_chronoa import channel_bridge
            return channel_bridge.submit(GATEWAY_NAME, text,
                                         connection=self._connection)
        except Exception as exc:               # noqa: BLE001 - a sentence, not a raise
            logger.info("the gateway refused: %s", exc)
            return f"I could not ask Chronoa ({type(exc).__name__}: {exc})"

    # -- the phone half --------------------------------------------------
    def handle(self, packet: dict) -> "Optional[str]":
        """One KDE Connect packet, answered for the phone. None if not one of ours."""
        if packet.get("type") != "kdeconnect.clipboard":
            return None
        body = packet.get("body") or {}
        text = str(body.get("content") or "").strip()
        stamp = body.get("timestamp") or packet.get("id") or 0
        if stamp in self._seen:               # the phone re-sends on reconnect
            return None
        self._seen.add(stamp)
        if not text:
            return None
        if len(text) > MAX_COMMAND:
            return (f"That was {len(text)} characters; the gateway's limit for a "
                    f"message over a channel is {MAX_COMMAND}. Send it shorter.")
        if len(self._seen) > 64:
            self._seen = set(sorted(self._seen)[32:])
        logger.info("phone said: %s", text[:60])
        return self.submit(text)

    def tell(self, host: str, text: str) -> bool:
        """Push the reply to the phone as a notification.

        **Trimmed before either path**, because the limit is a fact about the
        packet and not about the transport: an over-long reply is a `ValueError`
        from the bus, a seam that gets it untrimmed leaves the real path
        untested, and a bridge that dies on a long reply is a channel that stops
        answering after its first good answer.
        """
        text = text[:2000]
        if self._send is not None:
            self._send(host, text)
            return True
        from shani_chronoa import lan_link
        link, why = lan_link._link(host, timeout=20, tcp_port=_port())
        if link is None:
            logger.info("could not reach the phone: %s", why)
            return False
        try:
            link.send({"id": int(time.time() * 1000),
                       "type": "kdeconnect.notification",
                       "body": {"appName": "Chronoa", "id": "chronoa-reply",
                                "title": "Chronoa", "text": text[:2000],
                                "time": str(int(time.time() * 1000))}})
            return True
        except Exception as exc:               # noqa: BLE001
            logger.info("the phone would not take the reply: %s", exc)
            return False
        finally:
            link.close()


UNIT = "shani-chronoa-lan-bridge.service"
SETTING = "lan-bridge-enabled"


def set_enabled(on: bool, config=None) -> "tuple[bool, str]":
    """Turn the phone bridge on or off: the gateway entry and the user unit.

    Both halves, because either alone does nothing: the unit without the
    `phone` gateway has nobody to hand text to, and the gateway without the
    unit has no phone feeding it. The gateway is added **ask-only** - an
    existing `phone:execute` grant is kept, never created here.
    """
    import subprocess
    from shani_chronoa import gateway as gateway_module
    if config is None:
        from shani_chronoa.config import ChronoaConfig
        config = ChronoaConfig()
    entries, _errors = gateway_module.parse_config(config.get("gateways", "") or "")
    kept = [(n, g) for n, g in entries if n != GATEWAY_NAME]
    mine = [(n, g) for n, g in entries if n == GATEWAY_NAME]
    if on:
        kept += mine or [(GATEWAY_NAME, "")]
    text = ", ".join(f"{n}:{g}" if g and g != "ask" else n for n, g in kept)
    config.set("gateways", text)
    config.set(SETTING, "true" if on else "false")
    try:
        proc = subprocess.run(["systemctl", "--user", "enable" if on else "disable",
                               "--now", UNIT], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    return proc.returncode == 0, (proc.stderr or "").strip()


def _port() -> int:
    from shani_chronoa import lan_link
    return int(lan_link.paired_device().get("tcpPort") or lan_link.UDP_PORT)


def main(argv: "Optional[list[str]]" = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(message)s")
    args = build_parser().parse_args(argv)
    host = args.phone
    bridge = LanBridge(host=host)

    from shani_chronoa import lan_link
    # The paired phone, not the first thing that answers a broadcast: every
    # link is pinned to it anyway (`lan_link._link`), so another device here
    # could only ever be refused.
    if not host:
        host = lan_link.paired_device().get("address", "")
    if not host:
        for entry in lan_link.listen_once(5.0):
            host = entry.get("address", "")
            break
        if not host:
            print("No phone on this network. Start it on the phone, then run this "
                  "again.", file=sys.stderr)
            return 2

    print(f"Bridge up: phone at {host}, every clipboard message becomes a turn.")
    print(f"Gateway name: {GATEWAY_NAME}. It must be in the 'gateways' setting")
    print("(Settings > Privacy > Inbound channels) - ask-only by default, so")
    print("executing needs 'phone:execute'. Until then Chronoa answers and runs")
    print("nothing, which is the same grant every other channel has.")
    print("Copy text on the phone and share it to this computer.", flush=True)

    if args.once:
        for entry in lan_link.listen_once(6.0):
            reply = bridge.handle({"type": "kdeconnect.clipboard",
                                   "id": int(time.time() * 1000),
                                   "body": {"content": str(entry), "timestamp": 0}})
        return 0

    # The steady state: one link held open, replies pushed back to the phone.
    while True:
        link, why = lan_link._link(host, timeout=30, tcp_port=_port())
        if link is None:
            logger.info("link failed (%s); retrying in 30s", why)
            time.sleep(30)
            continue
        try:
            while True:
                packet = link.reply_packet(timeout=POLL_SECONDS)
                if packet is None:
                    continue
                reply = bridge.handle(packet)
                if reply:
                    bridge.tell(host, reply)
        except Exception as exc:               # noqa: BLE001
            logger.info("the link ended (%s); reconnecting", exc)
        finally:
            link.close()


if __name__ == "__main__":
    raise SystemExit(main())
