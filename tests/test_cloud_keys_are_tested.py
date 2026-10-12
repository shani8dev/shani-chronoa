"""The chat keys are tested against what a server really says (2026-10-12)

Every surface that takes a cloud key used to save it and show a sentence about
saving. An expired, revoked, wrong-product or quota-spent key was indistinguishable
from a working one **until a turn failed** - and by then the question had already
been asked, somewhere else.

`cloud_llm.probe_keys()` posts one real request per provider and reads the reply's
own words. That method is the one AGENTS.md records for the speech probe, where
three versions of the classification table were wrong and each looked right: **a
status code is not a capability; the server's own sentence about what is wrong
is.** An empty POST answers 400 both for "no such route" and for "wrong model
name", which is why the probe sends a real body at `max_tokens: 1`.

The classifier is module-level rather than nested, so it can be tested against the
shapes a server really sends without any network - which is the whole point.
"""

import pytest

from shani_chronoa import cloud_llm

_PROVIDER = cloud_llm.PROVIDERS["openai"]


class _Reply:
    """A reply with the shape `_classify_chat` reads."""

    def __init__(self, ok, status, text, body=None):
        self.is_success = ok
        self.status_code = status
        self.text = text
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("no json here")
        return self._body


def _working():
    return _Reply(True, 200, "{}", {"choices": [{"message": {"content": "hi"}}],
                                    "model": "gpt-4o-mini"})


CASES = [
    ("a working key", _working(), "yes"),
    # **The rate-limit shape AGENTS.md recorded live from Kilo and BlockRun:**
    # HTTP 200 with an `error` key in the body. `raise_for_status()` never sees it.
    ("200 carrying an error", _Reply(True, 200, '{"error":{"message":"rate limited"}}',
                                     {"error": {"message": "rate limited"}}), "no"),
    ("200 with no choices", _Reply(True, 200, "{}", {}), "no"),
    ("200 that is not JSON", _Reply(True, 200, "<html>nope</html>"), "no"),
    ("401 a wrong key", _Reply(False, 401, '{"error":"Incorrect API key provided"}}'),
     "needs-key"),
    # **Payment is its own answer.** A 402 with an x402 challenge is a route that
    # exists and costs money; folding it into `no` loses the finding, and folding
    # it into `needs-key` says a key will fix something a key will not.
    ("402 a paid route", _Reply(False, 402,
                                '{"x402Version":2,"error":"Payment Required"}'),
     "needs-paid"),
    ("429 the quota is spent", _Reply(False, 429, '{"error":"Rate limit reached"}'), "no"),
    ("404 a wrong model", _Reply(False, 404, "no such model"), "no"),
]


@pytest.mark.parametrize("name,reply,expect", CASES,
                         ids=[c[0] for c in CASES])
def test_each_shape_a_server_really_sends_is_its_own_verdict(name, reply, expect):
    got = cloud_llm._classify_chat(reply, _PROVIDER)
    assert got["verdict"] == expect, (
        "%s was classified as %r, not %r - and these are different actions: a "
        "key to enter, a bill to refuse, a host to check"
        % (name, got["verdict"], expect))


def test_a_working_key_reports_the_model_that_answered():
    """The one thing worth quoting back, because it is the proof the request was
    real: a probe that only says "yes" could have been answered by anything."""
    got = cloud_llm._classify_chat(_working(), _PROVIDER)

    assert got.get("model"), got


def test_the_sentence_leads_with_what_to_do_first():
    """A list in provider order buries the working answer under the broken ones."""
    said = cloud_llm.keys_sentence({
        "llm7": {"verdict": "unreachable", "detail": "ConnectError"},
        "openai": {"verdict": "yes"},
        "groq": {"verdict": "needs-key", "detail": "no key"},
        "blockrun": {"verdict": "needs-paid", "detail": "x402"},
    })

    assert said.index("works: openai") < said.index("no key for groq"), said
    assert said.index("no key for groq") < said.index("llm7 unreachable"), said


def test_a_diagnosis_never_carries_the_key_itself():
    """**A report that echoed a provider's error body would be a row that can carry
    somebody's secret into a screenshot.** The detail is capped, and it is the
    server's own words - never the request's headers."""
    reply = _Reply(False, 401, "Authorization: Bearer sk-live-SECRET123 is not valid")
    said = cloud_llm.keys_sentence({"openai": cloud_llm._classify_chat(reply,
                                                                       _PROVIDER)})

    assert "sk-live-SECRET123" not in said, (
        "the provider's own error text can be a key echo; the sentence must not "
        "repeat it: %r" % said)


def test_a_provider_with_no_key_is_not_asked_at_all(monkeypatch):
    """A key that is not there is a fact about the UI. Asking anyway can only
    answer 401, tells us nothing we do not already know, and costs a round trip on
    a machine whose whole point is not to talk to strangers."""
    asked = []

    class _Transport:
        def post(self, *a, **kw):
            asked.append(a[0] if a else kw.get("url"))
            return _working()

    monkeypatch.setattr(cloud_llm, "httpx", _Transport())
    found = cloud_llm.probe_keys(timeout=1.0, keys={})

    assert "needs-key" in {row["verdict"] for row in found.values()}, found
    # every provider that needs a key was skipped rather than asked
    needing = [pid for pid, p in cloud_llm.PROVIDERS.items() if p.requires_key]
    assert needing, "no provider in the table requires a key, so this test is empty"
    for pid in needing:
        assert pid not in asked, (
            "%s has no key but was still asked - a request that can only 401, on "
            "the user's behalf" % pid)
