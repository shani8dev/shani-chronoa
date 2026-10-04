"""Where a fetched URL is allowed to point, and which hop actually gets read.

Three defects, all in the web fetch path, all found by running it rather than
by reading it.

**The status was never recorded.** `webtext.retrieve` declared
`status_code: Optional[int] = None` and then read `response.status_code` only
to compare it against 400 - it never assigned the local. So the `status` field
of every web event in the egress log was `None`: measured against the original
code, a 200, a 404, a 500 and a connection failure all recorded `status=None`.
Not unbound and not stale - a dead local. The request itself was always
recorded (the `finally` always runs), so the gap is not absence but a field
that carries no information, in a log whose purpose is to be checked.

**A redirect was trusted.** `retrieve` passed `follow_redirects=True`, so the
destination policy was applied to the URL it was handed and the `Location`
chain was followed on faith. Validating only the first URL is worthless as a
property, because an approved host can 302 anywhere. Measured against the
original code: a request to `https://totally-ordinary.example.com/page` that
redirected to the cloud metadata address succeeded, and its body -
`{"access_key": "..."}` - came back as `Page.text`, i.e. straight into the
model's context. Worse for the audit: the egress log recorded
`host='totally-ordinary.example.com'`, so `local`, `host` and `violation` all
described a request that was never sent. assistd's `follow_redirect()`
(`assistd-tools/src/commands/web.rs:172-183`) re-checks every hop for exactly
this reason, and refuses to leave the approved set with an actionable message
rather than a bare failure.

**There was no destination guard at all.** Measured against the original code,
`retrieve` fetched `http://169.254.169.254/`, `http://metadata.google.internal/`
and `http://10.0.0.5:8080/actuator/env` without a murmur.

**The polarity is deliberately inverted, and that is the load-bearing part.**
gemini-cli's `isAddressPrivate()` (`packages/core/src/utils/fetch.ts:290-325`)
treats loopback and RFC1918 as private and refuses them. Correct there - in a
coding CLI the only loopback address anyone means is a developer's own dev
server. Chronoa's own Ollama endpoint is `http://127.0.0.1:11434`; the product
*is* that address. Copied verbatim, that guard would stop the app working. So
the allow side is this machine and the LAN behind it, and the deny side is
link-local - the cloud metadata address, which is dangerous precisely because
it is local. `TestThePolarityIsInverted` below is the control: it is the test
that goes red if someone "fixes" the ranges to match the upstream guard.
"""

import ast
import pathlib
import sys

import httpx
import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")
from shani_chronoa import egress, webtext  # noqa: E402

HTML = "<html><title>T</title><body><p>hello there</p></body></html>"

# Fixture credentials shaped like what a real metadata service hands out. Never
# a real key - the point is that a payload came back at all.
STOLEN = {
    "role": "metadata",
    "access_key": "AKIA-EXAMPLE-NOT-REAL",
    "instance_id": "i-0deadbeef",
}


@pytest.fixture(autouse=True)
def _isolated_log(tmp_path, monkeypatch):
    monkeypatch.setattr(egress, "EGRESS_DIR", tmp_path / "egress")
    monkeypatch.setattr(egress, "EGRESS_LOG", tmp_path / "egress" / "egress.jsonl")
    yield


def _transport(handler):
    return httpx.MockTransport(handler)


# --- Task 2: the status is real ----------------------------------------------


class TestTheRecordedStatusIsReal:
    def test_a_successful_fetch_records_its_status(self):
        webtext.retrieve("https://example.com/p", transport=_transport(lambda r: httpx.Response(200, html=HTML)))
        assert egress.read_events()[0]["status"] == 200

    @pytest.mark.parametrize("code", [400, 403, 404, 418, 500, 503])
    def test_an_error_response_records_that_status(self, code):
        """The 404 case is the one that matters: a page that has moved or never
        existed left the machine, and a log that says `null` for it cannot
        answer whether anything did."""
        with pytest.raises(webtext.RetrievalError, match=f"HTTP {code}"):
            webtext.retrieve("https://example.com/gone", transport=_transport(lambda r, c=code: httpx.Response(c)))
        assert egress.read_events()[0]["status"] == code

    def test_a_connection_failure_is_recorded_with_no_status(self):
        """`None` is the honest answer here and must stay distinguishable from
        200: nothing answered, which is not the same as something answering."""
        with pytest.raises(webtext.RetrievalError):
            webtext.retrieve(
                "https://example.com/x",
                transport=_transport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused"))),
            )
        event = egress.read_events()[0]
        assert event["status"] is None
        assert event["host"] == "example.com", "a request that was sent must still be visible"

    def test_a_timeout_is_recorded_with_no_status(self):
        with pytest.raises(webtext.RetrievalError, match="timed out"):
            webtext.retrieve(
                "https://example.com/slow",
                transport=_transport(lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow"))),
            )
        assert egress.read_events()[0]["status"] is None

    def test_every_outcome_produces_exactly_one_event(self):
        """A failed fetch is visible in the log rather than absent from it."""
        for handler in (
            lambda r: httpx.Response(200, html=HTML),
            lambda r: httpx.Response(500),
            lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused")),
        ):
            egress.EGRESS_LOG.unlink(missing_ok=True)
            try:
                webtext.retrieve("https://example.com/x", transport=_transport(handler))
            except webtext.RetrievalError:
                pass
            assert len(egress.read_events()) == 1


# --- Task 3: every hop is checked, and the log names the real one -------------


class TestEveryHopIsChecked:
    def test_a_redirect_to_the_metadata_address_is_refused(self):
        """The measured exploit. Before the fix this returned the metadata
        body as page text; the redirect target is now checked before it is
        requested, so the request is never made."""
        def handler(request):
            if request.url.host == "169.254.169.254":
                raise AssertionError("the metadata service was actually contacted")
            return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data/iam/"})

        with pytest.raises(webtext.RetrievalError, match="link-local"):
            webtext.retrieve("https://ordinary.example.com/page", transport=_transport(handler))

    def test_a_redirect_to_a_metadata_hostname_is_refused(self):
        """`.internal` is a split-horizon suffix, so the allowlist says local.
        The deny list has to win, and this is the case that proves it does."""
        def handler(request):
            if request.url.host == "metadata.google.internal":
                raise AssertionError("the metadata service was actually contacted")
            return httpx.Response(302, headers={"Location": "http://metadata.google.internal/computeMetadata/v1/"})

        with pytest.raises(webtext.RetrievalError, match="metadata"):
            webtext.retrieve("https://ordinary.example.com/page", transport=_transport(handler))

    def test_a_refused_hop_still_records_the_request_that_was_made(self):
        """The attempt is the thing a user needs to see. The event names the
        URL actually requested - the first hop - not a URL never contacted."""
        def handler(request):
            return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/"})

        with pytest.raises(webtext.RetrievalError):
            webtext.retrieve("https://ordinary.example.com/page", transport=_transport(handler))
        assert egress.read_events()[0]["host"] == "ordinary.example.com"

    def test_a_refused_first_hop_is_still_recorded(self):
        """A refused destination must be visible in the log too, and the guard
        sits inside the `try` precisely so that it is: raising before the
        `try` would make the attempt invisible, which is the one thing an
        egress log cannot afford to do with a refusal."""
        def handler(request):  # pragma: no cover - must never be called
            raise AssertionError("a refused destination was actually requested")

        with pytest.raises(webtext.RetrievalError, match="link-local"):
            webtext.retrieve("http://169.254.169.254/latest/meta-data/", transport=_transport(handler))
        event = egress.read_events()[0]
        assert event["host"] == "169.254.169.254"
        assert event["status"] is None, "nothing answered, and that must not look like a 200"

    def test_a_relative_redirect_is_still_followed(self):
        """Hop re-checking must not have broken ordinary same-host navigation,
        which is the overwhelmingly common case on the real web."""
        def handler(request):
            if request.url.path == "/a":
                return httpx.Response(302, headers={"Location": "/b"})
            return httpx.Response(200, html=HTML)

        page = webtext.retrieve("https://example.com/a", transport=_transport(handler))
        assert page.text == "hello there"

    def test_a_cross_host_redirect_to_an_ordinary_public_host_is_followed(self):
        """Recorded as the host that actually answered, which is the whole
        point: `local`/`host`/`violation` must describe the request sent."""
        def handler(request):
            if request.url.host == "example.com":
                return httpx.Response(301, headers={"Location": "https://www.example.com/final"})
            return httpx.Response(200, html=HTML)

        page = webtext.retrieve("https://example.com/start", transport=_transport(handler))
        assert page.url == "https://www.example.com/final"
        event = egress.read_events()[0]
        assert event["host"] == "www.example.com", "the log named the host that was asked for"
        assert event["status"] == 200

    def test_a_redirect_to_a_non_http_scheme_is_refused(self):
        def handler(request):
            return httpx.Response(302, headers={"Location": "file:///etc/passwd"})

        with pytest.raises(webtext.RetrievalError, match="only http"):
            webtext.retrieve("https://example.com/x", transport=_transport(handler))

    def test_a_redirect_loop_is_bounded_rather_than_followed_forever(self):
        requested = []

        def handler(request):
            requested.append(str(request.url))
            return httpx.Response(302, headers={"Location": "https://example.com/loop"})

        with pytest.raises(webtext.RetrievalError, match="redirected more than"):
            webtext.retrieve("https://example.com/loop", transport=_transport(handler))
        assert len(requested) == webtext.MAX_REDIRECTS + 1, "the hop budget was not the one documented"


# --- Task 4: the polarity is inverted, on purpose ----------------------------


class TestThePolarityIsInverted:
    """The control set. If any of these fails, the guard has been aligned with
    gemini-cli's instead of Chronoa's - which breaks the product rather than
    protecting it."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:11434/api/chat",   # Chronoa's own inference endpoint
            "http://localhost:11434/",
            "http://[::1]:11434/",
            "http://[::ffff:127.0.0.1]:11434/",  # mapped loopback: is_loopback says False
            "http://192.168.1.50:11434/",        # a LAN Ollama, RFC1918
            "http://10.0.0.5:8080/",
            "http://172.16.0.1/",
            "http://[fd00::1]:11434/",           # fc00::/7 is the IPv6 LAN
            "http://[fdff::1]/",
            "http://printer.local/status",
            "http://db.internal/query",
            "https://example.com/",
            "https://api.llm7.io/v1/chat/completions",
        ],
    )
    def test_allowed_destinations_stay_allowed(self, url):
        assert egress.denied_reason(url) == "", f"{url} must be fetchable"
        egress.check_destination(url)

    @pytest.mark.parametrize(
        "url,expected",
        [
            ("http://169.254.169.254/latest/meta-data/", "link-local"),
            ("https://169.254.169.254/computeMetadata/v1/", "link-local"),
            # The gemini-cli edge at fetch.ts:311-313: a mapped address must be
            # re-checked as the IPv4 address it really is.
            ("http://[::ffff:169.254.169.254]/latest/meta-data/", "link-local"),
            ("http://[::ffff:a9fe:a9fe]/", "link-local"),
            ("http://[fe80::1]/", "link-local"),
            ("http://metadata.google.internal/computeMetadata/v1/", "metadata"),
            ("http://metadata.goog/", "metadata"),
        ],
    )
    def test_dangerous_destinations_are_refused(self, url, expected):
        reason = egress.denied_reason(url)
        assert expected in reason, f"{url} should be refused for {expected!r}, got {reason!r}"
        with pytest.raises(egress.DestinationRefused):
            egress.check_destination(url)

    def test_a_metadata_hostname_is_refused_even_though_it_is_a_local_name(self):
        """The ordering. `.internal` is on `is_local`'s allowlist, so an
        allowlist-only guard waves at the one address worth refusing."""
        assert egress.is_local("http://metadata.google.internal/") is True
        assert egress.is_on_this_network("http://metadata.google.internal/") is True
        assert egress.denied_reason("http://metadata.google.internal/") != ""

    def test_documentation_and_benchmark_ranges_are_not_refused(self):
        """Deliberate, and the reason the ranges are written out by hand.

        `ipaddress.is_private` is True for both of these on the CPython 3.12
        this repo runs, so reaching for that property instead of the explicit
        ranges would refuse two ranges where nothing dangerous lives - a false
        refusal that looks exactly like a security win in review.
        """
        assert egress.denied_reason("http://198.18.0.1/") == ""
        assert egress.denied_reason("http://[2001:db8::1]/") == ""
        assert egress.denied_reason("http://100.64.0.1/") == ""

    @pytest.mark.parametrize("url", ["", "://", "http://", "http://[oops", "not a url"])
    def test_an_address_that_cannot_be_checked_fails_closed(self, url):
        """"Could not determine" has to be a state this can represent, and it
        has to fail toward refusal - the plausible clean answer is the
        expensive kind of wrong."""
        assert egress.denied_reason(url) != "", f"{url!r} was treated as permission"
        with pytest.raises(egress.DestinationRefused):
            egress.check_destination(url)

    def test_a_check_that_raises_is_a_refusal_not_a_pass(self):
        """gemini-cli's `validateUrlDestination()` (fetch.ts:413-420) fails
        closed on exception, and so does this."""
        def _explode(_url):
            raise RuntimeError("resolver blew up")

        original = egress.denied_reason
        egress.denied_reason = _explode
        try:
            with pytest.raises(egress.DestinationRefused):
                egress.check_destination("https://example.com/")
        finally:
            egress.denied_reason = original

    def test_the_guard_is_only_wired_into_the_model_supplied_url_path(self):
        """A scope decision, pinned so it is a decision and not an oversight.

        `ollama_llm.py` and `senses/vision.py` post to an operator-configured
        `ollama-host`, which `config.ollama_host` already forces to loopback
        under privacy mode and which `senses/vision.local_endpoint()` refuses
        outright if it is not loopback. `webtext` is the one path where the URL
        is chosen by a language model that may itself have been steered by page
        content, so it is the one path the guard belongs on.
        """
        path = pathlib.Path("usr/lib/shani-chronoa/shani_chronoa")
        assert "check_destination" in (path / "webtext.py").read_text()
        for name in ("ollama_llm.py", "cloud_llm.py", "senses/vision.py"):
            assert "check_destination" not in (path / name).read_text(), (
                f"{name} was given the web destination guard; its host is not "
                f"model-supplied and the guard does not belong there"
            )

    def test_every_web_fetch_goes_through_the_guard(self):
        """Not just the first URL. A static check here because the invariant
        is about the loop, and the loop is the thing that can lose it."""
        tree = ast.parse((pathlib.Path("usr/lib/shani-chronoa/shani_chronoa") / "webtext.py").read_text())
        funcs = {
            node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        }
        fetch = funcs["_check_url"]
        calls = [
            node for node in ast.walk(fetch)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "check_destination"
        ]
        assert calls, "_check_url no longer calls check_destination"
        # And the redirect loop must route every hop through _check_url, not
        # just the initial URL - which is the entire bug this file exists for.
        retrieve = funcs["retrieve"]
        guarded_hops = [
            node for node in ast.walk(retrieve)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "_check_url"
        ]
        assert len(guarded_hops) == 1, (
            "retrieve must funnel every hop through the one _check_url call; "
            f"found {len(guarded_hops)}"
        )
        assert "follow_redirects=False" in ast.unparse(retrieve), (
            "httpx is following redirects again, so hops are trusted rather "
            "than checked"
        )
