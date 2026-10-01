"""One JSON GET over HTTPS for the skills that ask a public API (weather,
currency, Wikipedia, Wiktionary): a timeout, a size cap, a named User-Agent,
and an egress-log entry under the calling skill's own name - so the log says
which skill sent what, not that everything was "the weather"."""

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
