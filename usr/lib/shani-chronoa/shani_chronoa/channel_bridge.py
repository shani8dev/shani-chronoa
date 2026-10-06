"""The channel bridge: thin adapters that feed the inbound gateway.

`gateway.py` is the part that has to exist before any channel can be written
safely: one D-Bus method, `Submit(gateway, text)`, and nothing else. This
module is the other half - adapters that *carry* a message to that method and
print the reply. Telegram and WhatsApp are the two implemented channels;
every other one (Signal, Matrix, a meeting transcript) is expected to be the
same shape: connect, call `Submit`, print the reply.

Two properties this module does not have, on purpose:

- **It is not Chronoa.** The bridge is a separate process with its own
  credentials. Chronoa never sees a Telegram or WhatsApp token; the tokens
  come from the environment of the bridge process, and a reply that mentions
  one is redacted before it is logged.
- **It cannot lower a gate.** The text it submits goes through the window's
  own `_submit`, so the same consent keys, the same whitelist, and the same
  post-conditions apply. A channel that could widen its own access is not a
  channel, it is a remote shell.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, List, Optional, Tuple

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from shani_chronoa import gateway as gw  # noqa: E402

logger = logging.getLogger(__name__)

#: Refusals arrive as D-Bus errors; this is what the bus says when the app is
#: not running or the channel was never configured. Kept as a type so callers
#: can tell "Chronoa said no" apart from "the network broke".
class GatewayError(Exception):
    """A message the gateway refused, or the bus could not deliver."""


def submit(gateway: str, text: str, *,
           connection: Optional[Gio.DBusConnection] = None,
           timeout_ms: int = 130_000) -> str:
    """Call `Submit(gateway, text)` and return the assistant's reply.

    `connection=` exists so a test can hand this a private bus; the default is
    the session bus, resolved on first use (and cached by GLib, like every
    other session-bus connection in this codebase - see `gateway.py`'s own
    docstring for why tests must pass one explicitly).

    The timeout is deliberately longer than `permissions.DECISION_TIMEOUT_SECONDS`
    for an ask-grant channel that a person is still reading: the gateway waits
    for an approval, and a client that times out first would leave Chronoa
    running an approved turn for nobody.
    """
    try:
        bus = connection or Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(
            gw.BUS_NAME, gw.OBJECT_PATH, gw.INTERFACE, "Submit",
            GLib.Variant("(ss)", (gateway, text)), GLib.VariantType("(s)"),
            Gio.DBusCallFlags.NONE, timeout_ms, None)
    except GLib.Error as exc:
        raise GatewayError(str(exc)) from exc
    return reply.unpack()[0]


def _redact(text: str, *secrets: Optional[str]) -> str:
    """Keep tokens out of the log. An adapter that logs a URL logs a token:
    Telegram's is in the path, so an error message carrying the URL carries
    the token."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


# ── Telegram ──────────────────────────────────────────────────────────────

class TelegramAdapter:
    """Long-polling Telegram bot. Each text message becomes a `Submit`.

    Uses the Bot API's `getUpdates`, so no public URL is needed - the bridge
    dials out, like every other local-first thing this ecosystem ships. The
    reply the gateway returns for a message is sent back to the same chat.

    `http=` takes an `httpx.Client` (a `MockTransport` one in tests), so the
    wire is exercisable without a token. `offset` advances per handled update
    and lives in memory: restart the bridge and Telegram re-delivers at most
    the last batch, which the gateway's per-minute rate cap bounds.
    """

    def __init__(self, token: str, gateway: str = "telegram", *,
                 api_base: str = "https://api.telegram.org",
                 submit_fn: Callable[[str, str], str] = submit,
                 http=None) -> None:
        if not token:
            raise ValueError("a Telegram bot token is required "
                             "(CHRONOA_TELEGRAM_TOKEN)")
        import httpx

        self._token = token
        self._gateway = gateway
        self._api = api_base.rstrip("/")
        self._submit = submit_fn
        self._http = http or httpx.Client(timeout=40.0)
        self.offset: Optional[int] = None

    def _url(self, method: str) -> str:
        return f"{self._api}/bot{self._token}/{method}"

    def get_updates(self, timeout: int = 30) -> List[dict]:
        """One long poll. `timeout` is Telegram's own, not ours: the HTTP
        timeout above it must be larger, or every idle minute reads as an
        error."""
        try:
            params = {"timeout": timeout, "allowed_updates": '["message"]'}
            if self.offset is not None:
                params["offset"] = self.offset
            resp = self._http.get(self._url("getUpdates"), params=params)
            body = resp.json()
        except Exception as exc:  # noqa: BLE001 - a poll must not kill the bridge
            raise GatewayError(_redact(str(exc), self._token)) from exc
        if not body.get("ok"):
            raise GatewayError(_redact(str(body.get("description", body)), self._token))
        return body.get("result", [])

    def send(self, chat_id, text: str) -> None:
        try:
            self._http.post(self._url("sendMessage"),
                            json={"chat_id": chat_id, "text": text})
        except Exception as exc:  # noqa: BLE001 - one bad send must not kill polling
            logger.warning("telegram sendMessage failed: %s",
                           _redact(str(exc), self._token))

    def handle(self, update: dict) -> bool:
        """Feed one update to the gateway. True when it carried text that was
        submitted (the only thing this bridge claims to do). Non-text updates
        are acknowledged by advancing the offset, not by a reply."""
        message = update.get("message") or {}
        text = message.get("text")
        chat = (message.get("chat") or {}).get("id")
        self.offset = update["update_id"] + 1
        if not text or chat is None:
            return False
        try:
            reply = self._submit(self._gateway, text)
        except GatewayError as exc:
            logger.info("the gateway refused %r: %s", self._gateway, exc)
            reply = str(exc)[:500] if "Refused" in str(exc) else ""
        if reply:
            self.send(chat, reply)
        return True

    def run(self, poll_timeout: int = 30,
            stop: Optional[Callable[[], bool]] = None) -> None:
        logger.info("telegram bridge polling as %r", self._gateway)
        while stop is None or not stop():
            for update in self.get_updates(timeout=poll_timeout):
                self.handle(update)


# ── WhatsApp (Cloud API webhook) ──────────────────────────────────────────

def _verify_signature(body: bytes, header: Optional[str], app_secret: str) -> bool:
    """`X-Hub-Signature-256` is `sha256=<hmac hex>` keyed with the app secret.
    Any body that does not check is not from Meta, and fails closed."""
    if not header or not header.startswith("sha256=") or not app_secret:
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):])


def extract_texts(payload: dict) -> List[Tuple[str, str]]:
    """Pull `(sender, text)` out of a webhook payload.

    The payload nests `entry[].changes[].value.messages[]`, and a message can
    be an image, a reaction, a status - only `type == "text"` carries words.
    Anything the shape does not describe is skipped, never guessed at: the
    first thing a new person messages is very rarely a text."""
    out: List[Tuple[str, str]] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            for message in value.get("messages") or []:
                if message.get("type") != "text":
                    continue
                body = (message.get("text") or {}).get("body")
                sender = message.get("from")
                if body and sender:
                    out.append((sender, body))
    return out


class WhatsAppAdapter:
    """Meta's Cloud API, webhook side.

    Two requests matter. `GET` is the one-time verification Meta sends when
    the webhook URL is configured; it must echo `hub.challenge` only when
    `hub.verify_token` matches. `POST` carries messages, and every body is
    checked against the app secret before it is believed.

    This is the one adapter that listens on a port - Meta calls *in*, rather
    than the bridge polling out. That is the provider's design, not Chronoa's:
    Chronoa itself still exports nothing but the session-bus method.
    """

    def __init__(self, token: str, phone_number_id: str, verify_token: str,
                 app_secret: str, gateway: str = "whatsapp", *,
                 api_base: str = "https://graph.facebook.com/v21.0",
                 submit_fn: Callable[[str, str], str] = submit,
                 http=None) -> None:
        for name, value in (("token", token), ("phone_number_id", phone_number_id),
                            ("verify_token", verify_token), ("app_secret", app_secret)):
            if not value:
                raise ValueError(f"whatsapp {name} is required "
                                 f"(see the CHRONOA_WHATSAPP_* environment variables)")
        import httpx

        self._token = token
        self._phone_id = phone_number_id
        self._verify_token = verify_token
        self._app_secret = app_secret
        self._gateway = gateway
        self._api = api_base.rstrip("/")
        self._submit = submit_fn
        self._http = http or httpx.Client(timeout=30.0)

    def verify_challenge(self, query: Dict[str, str]) -> Optional[str]:
        if (query.get("hub.mode") == "subscribe"
                and hmac.compare_digest(query.get("hub.verify_token", ""),
                                        self._verify_token)):
            return query.get("hub.challenge", "")
        return None

    def send(self, to: str, text: str) -> None:
        try:
            self._http.post(
                f"{self._api}/{self._phone_id}/messages",
                headers={"Authorization": f"Bearer {self._token}"},
                json={"messaging_product": "whatsapp", "to": to,
                      "type": "text", "text": {"body": text}})
        except Exception as exc:  # noqa: BLE001
            logger.warning("whatsapp send failed: %s",
                           _redact(str(exc), self._token))

    def handle_post(self, body: bytes, signature: Optional[str]) -> int:
        """Verify, extract, submit, reply. Returns the HTTP status to send:
        200 acknowledges (including "nothing to do"), 403 rejects an
        unverifiable body. Meta retries anything but 200, so rejecting is the
        only way a forged body stops being retried."""
        if not _verify_signature(body, signature, self._app_secret):
            logger.warning("rejected a webhook body whose signature did not check")
            return 403
        try:
            payload = json.loads(body)
        except ValueError:
            return 400
        for sender, text in extract_texts(payload):
            try:
                reply = self._submit(self._gateway, text)
            except GatewayError as exc:
                logger.info("the gateway refused %r: %s", self._gateway, exc)
                reply = "" if "Refused" not in str(exc) else str(exc)[:500]
            if reply:
                self.send(sender, reply)
        return 200

    def handler_class(self):
        adapter = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - stdlib hook name
                query = dict(urllib.parse.parse_qsl(
                    urllib.parse.urlparse(self.path).query))
                challenge = adapter.verify_challenge(query)
                if challenge is None:
                    self.send_error(403)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(challenge.encode())

            def do_POST(self):  # noqa: N802
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self.send_error(411)
                    return
                body = self.rfile.read(min(length, 1_000_000))
                status = adapter.handle_post(
                    body, self.headers.get("X-Hub-Signature-256"))
                self.send_response(status)
                self.end_headers()

            def log_message(self, fmt, *args):  # keep tokens out of stderr
                logger.info("whatsapp webhook: " + fmt, *args)

        return _Handler

    def serve(self, host: str = DEFAULT_HOST, port: int = 8765) -> ThreadingHTTPServer:
        server = ThreadingHTTPServer((host, port), self.handler_class())
        logger.info("whatsapp bridge listening on http://%s:%d as %r",
                    host, port, self._gateway)
        return server


# ── CLI ───────────────────────────────────────────────────────────────────

#: The webhook listens on loopback by default. Meta calls *in*, so this is
#: the one adapter with a port - but a port on this machine is not a port on
#: the internet, and the default keeps it that way.
DEFAULT_HOST = "127.0.0.1"


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="shani-chronoa-bridge",
        description="Carry messages between a chat channel and Chronoa's "
                    "inbound gateway (Submit on the session bus). Chronoa "
                    "never sees a token; the same consent keys, whitelist "
                    "and rate bound apply to every message.")
    sub = parser.add_subparsers(dest="channel", required=True)

    tg = sub.add_parser("telegram", help="poll a Telegram bot's updates")
    tg.add_argument("--gateway", default="telegram",
                    help="channel name as it appears in Chronoa's 'gateways' setting")
    tg.add_argument("--once", action="store_true",
                    help="one poll and exit, for a smoke check")
    tg.add_argument("--poll-timeout", type=int, default=30)

    wa = sub.add_parser("whatsapp", help="serve the WhatsApp Cloud API webhook")
    wa.add_argument("--gateway", default="whatsapp")
    wa.add_argument("--host", default=DEFAULT_HOST)
    wa.add_argument("--port", type=int, default=8765)

    args = parser.parse_args(argv)

    if args.channel == "telegram":
        token = _env("CHRONOA_TELEGRAM_TOKEN")
        if not token:
            print("CHRONOA_TELEGRAM_TOKEN is not set - the bridge has no "
                  "credentials and will not start.", file=__import__("sys").stderr)
            return 2
        adapter = TelegramAdapter(token, gateway=args.gateway)
        if args.once:
            for update in adapter.get_updates(timeout=args.poll_timeout):
                adapter.handle(update)
            return 0
        try:
            adapter.run(poll_timeout=args.poll_timeout)
        except GatewayError as exc:
            print(f"telegram bridge stopped: {exc}", file=__import__("sys").stderr)
            return 1
        return 0

    try:
        adapter = WhatsAppAdapter(
            token=_env("CHRONOA_WHATSAPP_TOKEN"),
            phone_number_id=_env("CHRONOA_WHATSAPP_PHONE_NUMBER_ID"),
            verify_token=_env("CHRONOA_WHATSAPP_VERIFY_TOKEN"),
            app_secret=_env("CHRONOA_WHATSAPP_APP_SECRET"),
            gateway=args.gateway)
    except ValueError as exc:
        print(str(exc), file=__import__("sys").stderr)
        return 2
    adapter.serve(args.host, args.port).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
