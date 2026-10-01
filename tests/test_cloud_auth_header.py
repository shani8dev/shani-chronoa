"""Keyless gateways get no Authorization header; a real key is sent as itself.

'Bearer unused' was sent to every keyless gateway, and on 2026-10-01 it was
what broke them: Kilo answered 401 'Your authentication token is invalid',
OVHcloud 403 - and both served the same request with no header at all.
A real keep-alive server records what actually arrives.
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from shani_chronoa.cloud_llm import CloudProvider, OpenAICompatibleLLM

SEEN = []


class _Gateway(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        SEEN.append(self.headers.get("Authorization"))
        body = json.dumps({"choices": [{"message": {"role": "assistant", "content": "OK"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def gateway():
    SEEN.clear()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Gateway)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


def _ask(base, key):
    llm = OpenAICompatibleLLM(CloudProvider("t", "Test", base, "m"), api_key=key)
    return asyncio.run(llm.chat_message([{"role": "user", "content": "hi"}]))


def test_a_keyless_request_carries_no_authorization_header(gateway):
    _ask(gateway, "")
    assert SEEN == [None]


def test_a_configured_key_is_sent(gateway):
    _ask(gateway, "sk-real")
    assert SEEN == ["Bearer sk-real"]
