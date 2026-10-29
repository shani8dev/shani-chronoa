"""translate_text: Chronoa's local model first, and nothing labelled local that is not.

`trans` used to be tried first and reported as "Translated locally (no network)"
- translate-shell's default engine is Google Translate. These pin that the
local path is the local model only, that a `trans` on PATH is never run, and
that with no model and no web consent the reply refuses rather than sending.
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from shani_chronoa import local_llm
from shani_chronoa.skills import translate_text as tt


class _Model(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert self.path == "/v1/chat/completions"
        assert "'es'" in body["messages"][0]["content"]
        out = json.dumps({"choices": [{"message": {"content": "<think></think>Buenos días"}}]}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def _no_web(monkeypatch):
    class Cfg:
        def sense_allowed(self, s):
            return False

        def sense_allowed_reason(self, s):
            return "the web sense is off"
    monkeypatch.setattr(tt, "ChronoaConfig", Cfg)


def _trap_trans(tmp_path, monkeypatch):
    mark = tmp_path / "trans-ran"
    fake = tmp_path / "trans"
    fake.write_text(f"#!/bin/sh\ntouch {mark}\necho GOOGLE\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    return mark


def test_the_local_model_translates(tmp_path, monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), _Model)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(local_llm, "BASE_URL", f"http://127.0.0.1:{srv.server_port}/v1")
    mark = _trap_trans(tmp_path, monkeypatch)
    _no_web(monkeypatch)
    try:
        out = tt._run({"text": "Good morning", "to_language": "es"})
    finally:
        srv.shutdown()
    assert out.endswith("Buenos días") and "local model (no network)" in out, out
    assert not mark.exists(), "trans (Google Translate) was run"


def test_no_model_and_no_web_refuses_and_never_runs_trans(tmp_path, monkeypatch):
    monkeypatch.setattr(local_llm, "BASE_URL", "http://127.0.0.1:9/v1")
    mark = _trap_trans(tmp_path, monkeypatch)
    _no_web(monkeypatch)
    out = tt._run({"text": "Good morning", "to_language": "es"})
    assert "did not answer" in out and "web sense is off" in out, out
    assert "GOOGLE" not in out and not mark.exists()
