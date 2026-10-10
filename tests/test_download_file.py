"""`download_file`: fetch a URL and say what actually happened.

Nothing could fetch a file. `web_search` finds pages, `browse` drives a
browser, and neither saves anything to disk - so "download that installer" had
no answer on a machine whose only other route is a browser.

**Three measured ways this reports a failure as a success**, all measured
directly rather than through a pipe (the mistake that made `readelf`'s and
`pdffonts`' real contracts wrong in this tree):

- **A 404 exits 0.** `curl -w '%{http_code}'` prints `404` and returns 0. Only
  the HTTP code distinguishes it from the file that was asked for.
- **An unresolvable name exits 6**, which *is* a failure, but for a specific
  reason that "the download failed" hides.
- **`file://` prints `000` and exits 0** - a successful-looking, zero-width
  answer for a scheme curl was never asked to handle.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import download_file as DL  # noqa: E402


def _fake_curl(tmp_path, monkeypatch, stdout="200 577 0.18 3200\n", code=0,
               stderr="", writes=None):
    """A stand-in curl. `writes` is the file body it leaves behind."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    script = bindir / "curl"
    body = repr(writes or "")
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"args = sys.argv[1:]\n"
        f"if '--output' in args:\n"
        f"    open(args[args.index('--output') + 1], 'w').write({body})\n"
        f"sys.stdout.write({stdout!r})\n"
        f"sys.stderr.write({stderr!r})\n"
        f"sys.exit({code})\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")


def test_a_404_is_not_a_download(tmp_path, monkeypatch):
    """**A 404 exits 0.** This is the whole reason the HTTP code is checked:
    an exit-status-only check saves the error page and calls it the file.
    """
    target = tmp_path / "out.html"
    _fake_curl(tmp_path, monkeypatch, stdout="404 0 0.1 0\n", code=0,
               writes="<html>404 Not Found</html>")
    out = DL._run({"url": "https://example.com/x", "destination": str(target)})
    assert "404" in out
    assert "not a file" in out
    # And no file is left claiming to be the download.
    assert not target.exists(), "the error page was saved as the file"


def test_a_failed_download_leaves_nothing_behind(tmp_path, monkeypatch):
    """A partial file at the destination is worse than none: the next run
    finds a file of the right name and believes the work is done.
    """
    target = tmp_path / "out.bin"
    _fake_curl(tmp_path, monkeypatch, stdout=" ", code=6,
               stderr="curl: (6) Could not resolve host", writes="partial")
    out = DL._run({"url": "https://nope.invalid/x", "destination": str(target)})
    assert "could not be resolved" in out
    assert not target.exists()


def test_an_empty_reply_is_not_a_file(tmp_path, monkeypatch):
    """`000` is curl's way of saying nothing came back - an unhandled scheme,
    or a failure before any reply. It is not a success code.
    """
    target = tmp_path / "out.bin"
    _fake_curl(tmp_path, monkeypatch, stdout="000 0 0 0\n", code=0, writes="")
    out = DL._run({"url": "https://example.com/x", "destination": str(target)})
    assert "no reply" in out
    assert not target.exists()


def test_an_empty_but_successful_file_is_reported_as_empty(tmp_path, monkeypatch):
    """A 200 that carries no bytes is a real answer, and it is not a failed
    download either - the distinction matters, because one is the site's
    doing and the other is ours.
    """
    target = tmp_path / "out.bin"
    _fake_curl(tmp_path, monkeypatch, stdout="200 0 0.05 0\n", code=0, writes="")
    out = DL._run({"url": "https://example.com/x", "destination": str(target)})
    assert "empty" in out
    assert "0 bytes" in out


def test_a_file_url_is_refused_before_curl_runs(tmp_path, monkeypatch):
    """**`file://` prints `000` and exits 0.** Handed one, curl reports a
    successful zero-width download and leaves an empty file - so the check is
    before the call, and the refusal names the reason.
    """
    target = tmp_path / "stolen"
    _fake_curl(tmp_path, monkeypatch, stdout="000 0 0 0\n", writes="")
    out = DL._run({"url": "file:///etc/shadow", "destination": str(target)})
    assert "only download over http or https" in out
    assert not target.exists()


def test_a_scheme_less_address_is_refused(tmp_path, monkeypatch):
    _fake_curl(tmp_path, monkeypatch)
    out = DL._run({"url": "example.com/file.txt", "destination": str(tmp_path / "x")})
    assert "http or https" in out


def test_a_real_success_reports_size_and_time(tmp_path, monkeypatch):
    target = tmp_path / "ok.html"
    _fake_curl(tmp_path, monkeypatch, stdout="200 577 0.18 3200000\n",
               writes="<html>hello</html>")
    out = DL._run({"url": "https://example.com/", "destination": str(target)})
    assert "577 bytes" in out
    assert target.read_text() == "<html>hello</html>"


def test_no_url_is_a_question_not_an_error():
    out = DL._run({})
    assert "need an address" in out


def test_a_missing_curl_names_the_package(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    out = DL._run({"url": "https://example.com/x",
                   "destination": str(tmp_path / "f")})
    assert "curl" in out
    assert "cannot" in out or "Could not" in out


def test_a_timeout_says_the_file_may_be_incomplete(tmp_path, monkeypatch):
    """A transfer that ran out of time has usually written *something*. Saying
    "saved" would be a lie and saying nothing would hide a partial file.
    """
    import subprocess
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("curl", 300)

    monkeypatch.setattr(DL.subprocess, "run", boom)
    out = DL._run({"url": "https://example.com/x",
                   "destination": str(tmp_path / "t.bin")})
    assert "incomplete" in out
    assert "did not finish" in out


@pytest.mark.skipif(not pathlib.Path("/usr/bin/curl").exists(),
                    reason="curl is not installed here")
def test_the_real_curl_is_driven_end_to_end(tmp_path):
    """Not a stub: the real `curl`, against a real local HTTP server, so the
    200 path and the failure path are both the program's own behaviour."""
    import http.server
    import threading

    served = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/missing":
                self.send_error(404)
                return
            body = b"hello from a real server"
            served["body"] = body
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        good = tmp_path / "good.txt"
        out = DL._run({"url": f"{base}/ok", "destination": str(good)})
        assert "Downloaded" in out
        assert good.read_bytes() == served["body"]

        bad = tmp_path / "bad.txt"
        missing = DL._run({"url": f"{base}/missing", "destination": str(bad)})
        assert "404" in missing
        assert not bad.exists()
    finally:
        server.shutdown()