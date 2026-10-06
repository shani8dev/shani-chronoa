"""A hand-typed OpenAI-compatible endpoint must be able to join the cloud chain.

`custom_provider()` existed and was unit-tested in isolation by the previous
pass, but nothing constructed a `CloudLLMChain` with it - the chain's
`__init__` accepted the arguments and `brain.py`/`model.py` never passed
them. In other words the feature was exactly as reachable as the strings
"custom-llm-base-url" were in the codebase: present, and never set.

These tests pin the three places it is now wired:

* `custom_provider()` validates the URL and keeps the `/v1` suffix (the
  client appends `/chat/completions` itself, so the base must keep it);
* a `CloudLLMChain` built with a custom base URL answers through it, end to
  end, against a real keep-alive HTTP server;
* a blank or unusable URL contributes no backend and never raises, because
  the caller is a settings field the person is typing into.
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from shani_chronoa.cloud_llm import (
    BYOK_PROVIDER_ORDER,
    CUSTOM_ID,
    DEFAULT_PROVIDER_ORDER,
    CloudLLMChain,
    custom_provider,
)

SEEN = []


class _Gateway(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        SEEN.append((self.headers.get("Authorization"), self.path))
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
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


def test_custom_provider_keeps_the_models_path(gateway):
    p = custom_provider(gateway)
    assert p is not None
    assert p.id == CUSTOM_ID
    # The client posts to base_url + /chat/completions, so the suffix a
    # router publishes must survive.
    assert p.base_url.endswith("/v1")


def test_custom_provider_adds_http_and_strips_trailing_slash():
    p = custom_provider("localhost:20128/v1/")
    assert p is not None
    assert p.base_url == "http://localhost:20128/v1"


@pytest.mark.parametrize("bad", ["", "   ", "javascript:alert(1)", "ftp://x", "http://"])
def test_an_unusable_url_is_none_not_a_crash(bad):
    assert custom_provider(bad) is None


def test_the_chain_answers_through_the_custom_endpoint(gateway):
    chain = CloudLLMChain(
        provider_ids=(CUSTOM_ID,) + DEFAULT_PROVIDER_ORDER,
        api_keys={"custom": "sk-custom"},
        custom_base_url=gateway,
        custom_model="local-model",
    )
    reply = asyncio.run(chain.chat_message([{"role": "user", "content": "hi"}]))
    assert reply["content"] == "OK"
    # The custom provider must actually have been the one asked.
    assert SEEN and SEEN[0][0] == "Bearer sk-custom"
    assert SEEN[0][1] == "/v1/chat/completions"


def test_a_blank_custom_url_adds_no_backend():
    chain = CloudLLMChain(
        provider_ids=(CUSTOM_ID,) + BYOK_PROVIDER_ORDER + DEFAULT_PROVIDER_ORDER,
        api_keys={},
        custom_base_url="",
    )
    # The anonymous free providers are always present, so the chain is
    # "available" - but none of its backends may be the custom one.
    names = [b.provider.id for b in chain._backends]
    assert CUSTOM_ID not in names
