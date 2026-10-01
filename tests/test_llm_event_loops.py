"""The Ollama client must survive the app's event loops.

The bug this pins (found by shani-testbed's chronoa-voice run): is_available()
ran check_health() on a throwaway loop, which cached an httpx.AsyncClient
bound to that loop and then closed it; the next chat ran on the app's own
loop, reused that client and failed with "Event loop is closed" - so every
voice turn died at the model call. A real HTTP server, not a mock: what
broke is the client's loop binding, which a mocked transport does not have.
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from shani_chronoa.llm import OllamaLLM


class _Ollama(BaseHTTPRequestHandler):
    # keep-alive, as Ollama does: the stale client fails on its POOLED
    # connection, so a server that closes each one cannot show the bug
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send({"version": "0.35.0"})

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self._send({"message": {"role": "assistant", "content": "four"}, "done": True})


@pytest.fixture
def ollama():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Ollama)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_a_chat_after_the_availability_check_works(ollama):
    llm = OllamaLLM(host=ollama, model="qwen2.5:0.5b")
    assert llm.is_available()
    reply = asyncio.run(llm.chat_message([{"role": "user", "content": "what is two plus two"}]))
    assert reply["content"] == "four"


def test_each_event_loop_gets_its_own_client(ollama):
    llm = OllamaLLM(host=ollama, model="m")
    for _ in range(3):   # three loops in turn, as the app's bridge and checks do
        assert asyncio.run(llm.chat_message([{"role": "user", "content": "hi"}]))["content"] == "four"
