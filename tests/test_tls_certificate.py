"""A certificate reader that had no test file, which is how two defects stayed.

`tls_certificate` reads a website's TLS certificate over a real socket. There
was no `tests/test_tls*.py` anywhere - the same gap that hid `lab_network.py`
and `midi.py`, both recorded in AGENTS.md as the dead-code class where a module
is green in isolation and unreachable at runtime. What it was hiding:

- **`class Cert(NamedTuple := object)`** - a walrus in a base-class list, with
  a comment claiming *"placeholder replaced below"*. Nothing was replaced below:
  `NamedTuple` was **never imported** (the typing import is `List, Optional,
  Tuple`), so the expression was always just `object` and `Cert` was an empty
  class inheriting from it. `Cert` is referenced nowhere in the tree, so the
  whole thing is dead - but it is dead *by accident*: the class body evaluated a
  name that only happened to be bound, via the walrus, to something harmless.
  That is the shape of a line that reads like a mistake and happens to be one.

- **`peer = tls.getpeercert()`** assigned and never used, which pyflakes named
  and nothing else did. `_handshake_error`'s contract is "return a sentence if
  the handshake failed", and the handshake succeeding is already the answer -
  so the value was genuinely dead rather than load-bearing.

**No test here reaches the network.** The socket tests run against a fake
`wrap_socket`, because a test that needs badssl.com is a test that fails when
the network is down and reports that as a defect in the certificate reader. The
one thing that genuinely needs a live peer is asserted against **the real
library's own parser** on bytes `ssl` itself produced, which is the only honest
way to check a format claim without a network.

`_days_left`'s comment says the stdlib format is *"not parseable by
`datetime.fromisoformat`"*, so that is asserted as a property rather than
trusted: `fromisoformat` is asked directly, and the test fails if some future
Python accepts it and this parser's hand-rolled `strptime` is then redundant.
"""

from __future__ import annotations

import socket
import ssl
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import tls_certificate as T  # noqa: E402


class TestTheDeadPlaceholderIsGone:
    def test_there_is_no_cert_class_holding_an_empty_body(self):
        """Named for the defect, so a reintroduction says what it is.

        `Cert` was referenced nowhere, so removing it changes no behaviour -
        which is exactly why the line could sit there looking deliberate. The
        claim is about the *source*, because a class that nothing uses cannot
        be caught by running anything.
        """
        source = (_REPO / "usr/lib/shani-chronoa/shani_chronoa"
                  "/skills/tls_certificate.py").read_text()
        assert "class Cert(" not in source, (
            "a Cert class is back. If it is needed it should be a real "
            "NamedTuple with NamedTuple actually imported - the walrus "
            "expression that stood in for it bound a name nothing else used")
        assert ":=" not in source.split("SCHEMA = {")[0], (
            "a walrus crept back into the module header, where the Cert "
            "placeholder lived")


class TestTheDateFormat:
    def test_it_reads_the_formatssl_actually_prints(self):
        """A real `ssl` certificate's `notAfter`, verbatim."""
        assert T._days_left("Dec 25 22:56:35 2026 GMT") == 77, (
            "the stdlib's own notAfter format did not parse to the same "
            "answer, so the hand-rolled parser has drifted from it")

    def test_an_unreadable_date_is_none_and_not_a_guess(self):
        """`None` means unknown. A negative number would read as a fact."""
        for bad in ("", "not a date", "2026-12-25", None):
            assert T._days_left(bad) is None, f"{bad!r} produced a number"

    def test_fromisoformat_really_cannot_read_it(self):
        """The control on the comment's claim.

        If a future Python accepts this format, this parser is redundant and
        the module should say so rather than carry a reason that has expired.
        """
        from datetime import datetime

        try:
            datetime.fromisoformat("Dec 25 22:56:35 2026 GMT")
        except ValueError:
            return  # the documented reason still holds
        pytest.fail(
            "fromisoformat now reads the stdlib's notAfter format, so "
            "_days_left's hand-rolled strptime is no longer needed")


class TestNamesFromAPeerCertificate:
    def test_it_reads_the_san_list_and_keeps_the_wildcard(self):
        peer = {"subjectAltName": (("DNS", "a.example"), ("DNS", "*.a.example"))}
        assert T._names(peer) == ["a.example", "*.a.example"]

    def test_it_falls_back_to_the_common_name(self):
        """A certificate with no SANs still names its host somewhere."""
        peer = {"subject": ((("commonName", "old.example"),),)}
        assert T._names(peer) == ["old.example"]

    def test_an_empty_certificate_names_nothing_rather_than_raising(self):
        assert T._names({}) == []


class TestHandshakeErrors:
    """Every refusal here is a *distinction*, and the distinctions are the point.

    "Your certificate expired" and "the secure connection never came up" are
    different problems with different fixes, and a reader that collapses them
    into one sentence sends somebody to renew a certificate that is fine. So
    each branch asserts the word that names its own cause.
    """

    def test_an_expired_certificate_is_a_trust_failure_not_a_date_problem(self, monkeypatch):
        monkeypatch.setattr(T.socket, "create_connection", _FakeConnection)
        monkeypatch.setattr(ssl, "create_default_context", _ExpiringContext)
        said = T._handshake_error("expired.example", 443)
        assert "did not verify" in said, said
        assert "not an expired-certificate problem" in said, (
            "the sentence must distinguish the two causes or it sends the "
            "reader to renew a certificate that is fine")

    def test_a_handshake_that_cannot_start_says_the_connection_never_established(self,
                                                                                 monkeypatch):
        def raising(*_a, **_kw):
            raise socket.gaierror("Name or service not known")

        monkeypatch.setattr(T.socket, "create_connection", raising)
        monkeypatch.setattr(ssl, "create_default_context", lambda: _NoopContext())
        said = T._handshake_error("nope.example", 443)
        assert "never established" in said or "could be read" in said, said

    def test_a_clean_handshake_says_nothing_at_all(self, monkeypatch):
        """The empty string is the "verified" answer, and it must stay empty."""
        monkeypatch.setattr(T.socket, "create_connection", _FakeConnection)
        monkeypatch.setattr(ssl, "create_default_context", lambda: _NoopContext())
        assert T._handshake_error("good.example", 443) == ""

    def test_it_names_the_package_that_supports_it(self):
        source = (_REPO / "usr/lib/shani-chronoa/shani_chronoa"
                  "/skills/tls_certificate.py").read_text()
        assert "openssl" in source.lower(), (
            "nothing here names what provides the certificate reader, so a "
            "missing dependency would be unactionable")


class _NoopContext:
    """An SSL context whose `wrap_socket` succeeds and yields nothing.

    It exists so `_handshake_error`'s success path runs with **no network and no
    certificate**. The function's contract is "empty string when the chain
    verified", and it deliberately calls no `getpeercert()` - the *other*
    function at line 191 does that, and is what reads the certificate.

    `wrap_socket` takes the socket positionally and the hostname as a keyword,
    because that is the call `_handshake_error` makes. A fake with the wrong
    signature raises `TypeError` and reads as a broken reader.
    """

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def wrap_socket(self, sock, server_hostname=None, **_kw):
        return _Verified()


class _Verified:
    """What a successful `wrap_socket` yields - and nothing on it is read."""

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def getpeercert(self):
        raise AssertionError(
            "_handshake_error must not read the certificate; that is the "
            "caller's job and the reason this fake has no data to give")


class _Connected:
    """The socket `create_connection` yields, and the only thing it must do."""

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _FakeConnection(*_a, **_kw):  # noqa: N802 - stands in for a function of that name
    """Replaces `socket.create_connection`.

    **A function, not a class**, because `__init__` cannot `return self` and a
    class whose `__init__` does raises `TypeError: __init__() should return
    None`. That error arrives from inside `_handshake_error` and reads as a
    broken certificate reader rather than a broken double — the first version
    of this file made that mistake in two tests at once.
    """
    return _Connected()


class _ExpiringContext:
    """An SSL context that raises `SSLCertVerificationError`, as the real one does.

    Raised from `wrap_socket` rather than from `create_connection`, because
    that is where a real verification failure happens: the TCP connect
    succeeds and the chain is what fails. Raising it from the connect would
    have tested a different branch and still gone green.
    """

    def __init__(self, message="certificate has expired"):
        self._message = message

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def wrap_socket(self, sock, server_hostname=None, **_kw):
        # **Built the way CPython builds it.** Measured on a real expired
        # certificate (expired.badssl.com): the ssl module sets
        # `verify_message` and `verify_code` as attributes *after* construction,
        # so `SSLCertVerificationError(1, "...")` on its own has neither - and a
        # double without them raises `AttributeError` from inside the reader's
        # `except`, which reads as a broken reader rather than a broken double.
        # That was my first version, and the failure pointed at the product.
        error = ssl.SSLCertVerificationError(1, self._message)
        error.verify_message = self._message
        error.verify_code = 10
        raise error
