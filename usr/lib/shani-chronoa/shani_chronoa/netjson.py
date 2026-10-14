"""One JSON GET over HTTPS for the skills that ask a public API (weather,
currency, Wikipedia, Wiktionary, maps): a timeout, a size cap, a named
User-Agent, and an egress-log entry under the calling skill's own name - so the
log says which skill sent what, not that everything was "the weather".

`get_bytes` is the same request for a body that is not JSON (an RSS feed), with
its own size cap; `get_json` is unchanged in behaviour."""

import urllib.parse
from typing import Optional

import httpx

from shani_chronoa import egress

TIMEOUT = httpx.Timeout(8.0, connect=5.0)
MAX_BYTES = 256 * 1024
USER_AGENT = "ShaniChronoa/1.0 (local-first assistant; +https://shani.dev)"


def get_json(component: str, url: str, params: Optional[dict] = None):
    full = url + ("?" + urllib.parse.urlencode(params) if params else "")
    status = None
    try:
        with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as c:
            r = c.get(full)
            status = r.status_code
            r.raise_for_status()
            if len(r.content) > MAX_BYTES:
                raise ValueError("response too large")
            return r.json()
    finally:
        egress.record(component, full, status=status, privacy_mode=egress.privacy_mode_enabled())


def get_bytes(component: str, url: str, params: Optional[dict] = None,
              max_bytes: int = MAX_BYTES, timeout=None) -> bytes:
    """The raw body of one GET, under the same timeout, User-Agent and egress
    record as `get_json`. Raises `httpx.HTTPError` or `ValueError` (too large).

    **`timeout` overrides the shared one per call, and defaults to it.** The
    shared `TIMEOUT` is 8 s, which is right for the JSON APIs and wrong for every
    provider with a different latency profile: measured today, arXiv's API answers
    a *successful* query in **14.9 s** and a degraded one in 47 s, so every
    correctly-paced call to it timed out and the search looked broken for a
    reason that had nothing to do with the request. A shared constant is only
    shared if every caller has the same budget, and these do not.
    """
    full = url + ("?" + urllib.parse.urlencode(params) if params else "")
    status = None
    with_prefix = TIMEOUT if timeout is None else httpx.Timeout(timeout, connect=5.0)
    try:
        with httpx.Client(timeout=with_prefix, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as c:
            r = c.get(full)
            status = r.status_code
            r.raise_for_status()
            if len(r.content) > max_bytes:
                raise ValueError(f"response too large ({len(r.content)} bytes, limit {max_bytes})")
            return r.content
    finally:
        egress.record(component, full, status=status, privacy_mode=egress.privacy_mode_enabled())
