"""The channel bridge: adapters that carry a message to the inbound gateway.

Everything here runs against fakes that say what the real thing said in
testing: an `httpx.MockTransport` for the Telegram and WhatsApp wires, a
private `Gio.TestDBus` for the gateway call, and a real loopback HTTP server
for the webhook - the shape a unit test gets wrong is the one place the
adapter could look correct and not work.
"""

import hashlib
import hmac
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

gi = pytest.importorskip("gi")  # only needed by the subprocess server strings

import httpx  # noqa: E402

from shani_chronoa import channel_bridge as cb  # noqa: E402


# ── Telegram ──────────────────────────────────────────────────────────────

def _tg_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_telegram_polls_submits_and_replies():
    seen = {"submitted": None, "sent": None}

    def wire(request):
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [
                {"update_id": 100, "message": {"text": "what time is it",
                                                "chat": {"id": 42}}},
                {"update_id": 101, "message": {"photo": [], "chat": {"id": 42}}},
            ]})
        if request.url.path.endswith("/sendMessage"):
            seen["sent"] = json.loads(request.content)
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)

    def submit(gateway, text):
        seen["submitted"] = (gateway, text)
        return "it is noon"

    adapter = cb.TelegramAdapter("tok", gateway="telegram",
                                 http=_tg_client(wire), submit_fn=submit)
    updates = adapter.get_updates()
    assert len(updates) == 2
    adapter.handle(updates[0])
    assert seen["submitted"] == ("telegram", "what time is it")
    assert seen["sent"]["chat_id"] == 42
    assert seen["sent"]["text"] == "it is noon"
    assert adapter.offset == 101
    adapter.handle(updates[1])  # a photo carries no words: acknowledged, not submitted
    assert adapter.offset == 102
    assert seen["submitted"] == ("telegram", "what time is it")


def test_telegram_offset_is_sent_so_updates_are_not_repeated():
    offsets = []

    def wire(request):
        offsets.append(request.url.params.get("offset"))
        return httpx.Response(200, json={"ok": True, "result": []})

    adapter = cb.TelegramAdapter("tok", http=_tg_client(wire),
                                 submit_fn=lambda g, t: "")
    adapter.get_updates()
    adapter.offset = 55
    adapter.get_updates()
    assert offsets == [None, "55"]


def test_telegram_a_refused_message_is_logged_not_retried_forever():
    sent = []

    def wire(request):
        if request.url.path.endswith("/sendMessage"):
            sent.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"ok": True, "result": []})

    def submit(gateway, text):
        raise cb.GatewayError("dev.shani.chronoa.Error.Refused: more than 20 messages a minute; slow down")

    adapter = cb.TelegramAdapter("tok", http=_tg_client(wire), submit_fn=submit)
    adapter.handle({"update_id": 1, "message": {"text": "hi", "chat": {"id": 7}}})
    assert sent and "slow down" in sent[0]
    assert adapter.offset == 2


def test_telegram_a_cancelled_or_failed_channel_is_not_said_as_if_it_worked():
    def submit(gateway, text):
        raise cb.GatewayError("org.freedesktop.DBus.Error.ServiceUnknown: "
                              "no such name 'dev.shani.chronoa.Gateways'")

    adapter = cb.TelegramAdapter("tok", http=_tg_client(lambda r: httpx.Response(200, json={"ok": True})),
                                 submit_fn=submit)
    handled = adapter.handle({"update_id": 3,
                              "message": {"text": "hi", "chat": {"id": 7}}})
    assert handled is True  # the update itself was processed; the channel just has no answer
    assert adapter.offset == 4


def test_telegram_errors_do_not_leak_the_token():
    def wire(request):
        # Telegram's own failure body names the bot in prose, the URL carries it too.
        return httpx.Response(401, text="Unauthorized: bottoksecret123/getUpdates is not valid")

    adapter = cb.TelegramAdapter("toksecret123", http=_tg_client(wire),
                                 submit_fn=lambda g, t: "")
    with pytest.raises(cb.GatewayError) as excinfo:
        adapter.get_updates()
    assert "toksecret123" not in str(excinfo.value)


def test_telegram_needs_a_token():
    with pytest.raises(ValueError):
        cb.TelegramAdapter("")


# ── WhatsApp ──────────────────────────────────────────────────────────────

SECRET = "appsecret"
VERIFY = "verify-me"


def _wa(submit_fn, sent):
    def wire(request):
        if request.url.path.endswith("/messages"):
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"messages": [{"id": "wamid.1"}]})
        return httpx.Response(404)

    return cb.WhatsAppAdapter("wa-token", "123456", VERIFY, SECRET,
                              submit_fn=submit_fn,
                              http=httpx.Client(transport=httpx.MockTransport(wire)))


def _signed(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def _profile(text, sender="447700900123"):
    return {"object": "whatsapp_business_account", "entry": [{
        "id": "WABA", "changes": [{"value": {
            "messaging_product": "whatsapp",
            "messages": [{"from": sender, "id": "wamid.9", "timestamp": "1",
                          "type": "text", "text": {"body": text}}],
        }, "field": "messages"}],
    }]}


def test_whatsapp_challenge_only_for_the_right_verify_token():
    adapter = _wa(lambda g, t: "", [])
    assert adapter.verify_challenge(
        {"hub.mode": "subscribe", "hub.verify_token": VERIFY,
         "hub.challenge": "CHALLENGE"}) == "CHALLENGE"
    assert adapter.verify_challenge(
        {"hub.mode": "subscribe", "hub.verify_token": "wrong",
         "hub.challenge": "CHALLENGE"}) is None
    assert adapter.verify_challenge({"hub.mode": "unsubscribe",
                                     "hub.verify_token": VERIFY}) is None


def test_whatsapp_rejects_a_body_whose_signature_does_not_check():
    adapter = _wa(lambda g, t: "reply", [])
    body = json.dumps(_profile("hi")).encode()
    assert adapter.handle_post(body, "sha256=" + "0" * 64) == 403
    assert adapter.handle_post(body, None) == 403


def test_whatsapp_extracts_text_and_replies():
    sent = []
    seen = []

    def submit(gateway, text):
        seen.append((gateway, text))
        return "the battery is at 80%"

    adapter = _wa(submit, sent)
    body = json.dumps(_profile("battery status?")).encode()
    assert adapter.handle_post(body, _signed(body)) == 200
    assert seen == [("whatsapp", "battery status?")]
    assert sent and sent[0]["to"] == "447700900123"
    assert sent[0]["text"]["body"] == "the battery is at 80%"


def test_whatsapp_skips_non_text_and_malformed_shapes():
    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"from": "1", "type": "image"},
        {"from": "1", "type": "reaction"},
        {"from": "2", "type": "text", "text": {"body": "ping"}},
    ]}}]}]}
    assert cb.extract_texts(payload) == [("2", "ping")]
    assert cb.extract_texts({}) == []
    assert cb.extract_texts({"entry": None}) == []


def test_whatsapp_needs_every_credential():
    for missing in ("token", "phone_number_id", "verify_token", "app_secret"):
        kwargs = dict(token="t", phone_number_id="p", verify_token="v", app_secret="s")
        kwargs[missing] = ""
        with pytest.raises(ValueError):
            cb.WhatsAppAdapter(**kwargs)


def test_whatsapp_webhook_over_real_loopback_http():
    """The handler half of the adapter, exercised over a real socket: a
    challenge GET and a signed POST, both against the threaded server the CLI
    starts."""
    submitted = []
    server = cb.WhatsAppAdapter(
        "wa-token", "123", VERIFY, SECRET,
        submit_fn=lambda g, t: submitted.append((g, t)) or "ok",
        http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True})))
    ).serve("127.0.0.1", 0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/?hub.mode=subscribe&hub.verify_token={VERIFY}&hub.challenge=abc123") as resp:
            assert resp.status == 200 and resp.read() == b"abc123"
        body = json.dumps(_profile("hello from a phone")).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/", data=body, method="POST",
            headers={"X-Hub-Signature-256": _signed(body), "Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
        assert submitted == [("whatsapp", "hello from a phone")]
        bad = urllib.request.Request(
            f"http://127.0.0.1:{port}/", data=body, method="POST",
            headers={"X-Hub-Signature-256": "sha256=bad"})
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(bad)
        assert excinfo.value.code == 403
    finally:
        server.shutdown()
        server.server_close()


# ── the bus call itself ───────────────────────────────────────────────────
#
# An in-process `Gio.TestDBus` cannot drive `cb.submit` (its `call_sync`
# needs the connection's main context to be iterating, and an in-process pump
# deadlocks against the app's worker-thread reply - measured). So this runs
# the real path: a real `dbus-daemon`, the real gateway object the app builds,
# and `cb.submit` calling it from a second process - the same two-process
# harness the gateway module's own docstring describes.

_SERVER = r'''
import os, sys, time
sys.path.insert(0, os.environ["CHRONOA_PKG"])
import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib
from shani_chronoa import gateway as gw  # noqa: E402 - re-imported here on purpose
received = []
registry = gw.Registry(lambda text: received.append(text) or "reply: " + text)
registry.register("whatsapp", grant="execute")
gw.export(registry)
loop = GLib.MainLoop()
print("READY", flush=True)
loop.run()
'''

_CLIENT = r'''
import os, sys
sys.path.insert(0, os.environ["CHRONOA_PKG"])
from shani_chronoa import channel_bridge as cb
print(cb.submit("whatsapp", "hi from a whatsapp"))
'''

_CLIENT_REFUSED = r'''
import os, sys
sys.path.insert(0, os.environ["CHRONOA_PKG"])
from shani_chronoa import channel_bridge as cb
try:
    cb.submit("signal", "ping", timeout_ms=5000)
except cb.GatewayError as exc:
    print("REFUSED:", exc)
'''


def _server_env(address):
    env = dict(os.environ)
    env["DBUS_SESSION_BUS_ADDRESS"] = address
    env["CHRONOA_PKG"] = str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


@pytest.fixture()
def private_bus():
    import subprocess
    proc = subprocess.Popen(
        ["dbus-daemon", "--session", "--fork", "--print-address", "--print-pid"],
        stdout=subprocess.PIPE, text=True)
    address, pid_line = proc.stdout.readline().strip(), proc.stdout.readline().strip()
    proc.wait()
    assert address and pid_line.isdigit()
    yield address
    os.kill(int(pid_line), 15)


def _start_server(address):
    import subprocess
    proc = subprocess.Popen(
        [sys.executable, "-c", _SERVER], env=_server_env(address),
        stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "READY"
    return proc


def test_submit_round_trips_through_a_real_private_bus(private_bus):
    import subprocess
    server = _start_server(private_bus)
    try:
        out = subprocess.run([sys.executable, "-c", _CLIENT],
                             env=_server_env(private_bus), text=True,
                             capture_output=True, timeout=30)
        assert "reply: hi from a whatsapp" in out.stdout, out.stderr
    finally:
        server.kill()


def test_submit_reports_a_refused_channel_by_name(private_bus):
    import subprocess
    server = _start_server(private_bus)
    try:
        out = subprocess.run([sys.executable, "-c", _CLIENT_REFUSED],
                             env=_server_env(private_bus), text=True,
                             capture_output=True, timeout=30)
        assert "no gateway called 'signal'" in out.stdout, out.stderr
    finally:
        server.kill()


# ── CLI wiring ────────────────────────────────────────────────────────────

def test_cli_refuses_to_start_telegram_without_a_token(monkeypatch, capsys):
    monkeypatch.delenv("CHRONOA_TELEGRAM_TOKEN", raising=False)
    assert cb.main(["telegram", "--once"]) == 2
    assert "CHRONOA_TELEGRAM_TOKEN" in capsys.readouterr().err


def test_cli_refuses_to_start_whatsapp_without_credentials(monkeypatch, capsys):
    for name in ("CHRONOA_WHATSAPP_TOKEN", "CHRONOA_WHATSAPP_PHONE_NUMBER_ID",
                 "CHRONOA_WHATSAPP_VERIFY_TOKEN", "CHRONOA_WHATSAPP_APP_SECRET"):
        monkeypatch.delenv(name, raising=False)
    assert cb.main(["whatsapp"]) == 2
    assert "CHRONOA_WHATSAPP" in capsys.readouterr().err
