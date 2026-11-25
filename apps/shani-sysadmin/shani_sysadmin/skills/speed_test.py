"""Skill: how fast is this internet connection?

`check_internet` answers "does it work"; nothing answered "how fast". This
times a download and an upload against Cloudflare's public speed endpoints
(`speed.cloudflare.com/__down` and `/__up`, the ones its own speed test page
uses) and a handful of tiny requests for latency.

Two gates, both of which must pass:

- **privacy mode off** - it sends traffic to a third party by design;
- **`speed-test-enabled`** - it moves real data (10 MB down and 2 MB up by
  default, up to 100 MB), which on a metered or mobile connection costs money.
  A model must not be able to spend someone's data allowance on a whim.

Honesty rules: the result is one measurement to one server at one moment, and
says so; a transfer that is cut short reports what it measured over the bytes
it actually moved, with the shortfall stated; every request is in the egress log
under this skill's name with the bytes sent.
"""

from __future__ import annotations

import time

from shani_chronoa import egress
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "speed-test-enabled"
_BASE = "https://speed.cloudflare.com"
_DEFAULT_DOWN_MB, _DEFAULT_UP_MB, _MAX_MB = 10, 2, 100
_PINGS = 5
_TRANSPORT = None  # tests inject an httpx.MockTransport; nothing else sets this

SCHEMA = {
    "type": "function",
    "function": {
        "name": "speed_test",
        "description": (
            "Measure this internet connection: latency, download speed and "
            "upload speed, against Cloudflare's speed-test servers. Moves real "
            "data (default 10 MB down, 2 MB up), so it requires the "
            "'speed-test-enabled' consent key and privacy mode off. Use for "
            "'how fast is my internet', 'is my connection slow'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "download_mb": {"type": "integer",
                                "description": f"MB to download (default {_DEFAULT_DOWN_MB}, max {_MAX_MB})."},
                "upload_mb": {"type": "integer",
                              "description": f"MB to upload (default {_DEFAULT_UP_MB}, max {_MAX_MB}; 0 skips it)."},
            },
        },
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    # The key first: with it off, the refusal names the switch a person can
    # turn on. Privacy mode still refuses with the key on.
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (f"speed tests are turned off (enable '{_CONSENT_KEY}' in Settings). A "
                       f"test moves several megabytes, which costs money on a metered connection.")
    if egress.privacy_mode_enabled():
        return False, "privacy mode is on, and a speed test sends traffic to a third-party server."
    return True, ""


def _mb(arguments: dict, key: str, default: int) -> int:
    try:
        value = int(arguments.get(key) if arguments.get(key) is not None else default)
    except (TypeError, ValueError):
        value = default
    return max(0, min(value, _MAX_MB))


def _mbps(nbytes: int, seconds: float) -> float:
    return (nbytes * 8 / 1e6) / seconds if seconds > 0 else 0.0


def _client():
    import httpx
    return httpx.Client(timeout=httpx.Timeout(30.0, connect=8.0), transport=_TRANSPORT,
                        headers={"User-Agent": "ShaniChronoa/1.0 speed test"})


def measure(down_mb: int, up_mb: int) -> "dict":
    result = {"pings": [], "down": None, "up": None, "errors": []}
    with _client() as client:
        for _ in range(_PINGS):
            url = f"{_BASE}/__down?bytes=0"
            t0 = time.perf_counter()
            try:
                client.get(url).raise_for_status()
                result["pings"].append((time.perf_counter() - t0) * 1000)
            except Exception as exc:  # noqa: BLE001 - reported, not raised
                result["errors"].append(f"latency: {exc}")
                break
        if down_mb:
            want = down_mb * 1_000_000
            url = f"{_BASE}/__down?bytes={want}"
            got, t0, status = 0, time.perf_counter(), None
            try:
                with client.stream("GET", url) as r:
                    status = r.status_code
                    r.raise_for_status()
                    for chunk in r.iter_bytes():
                        got += len(chunk)
            except Exception as exc:  # noqa: BLE001
                result["errors"].append(f"download: {exc}")
            seconds = time.perf_counter() - t0
            egress.record("skill:speed_test", url, status=status, purpose="speed-test")
            if got:
                result["down"] = (got, want, seconds)
        if up_mb:
            body = b"\0" * (up_mb * 1_000_000)
            url = f"{_BASE}/__up"
            t0, status = time.perf_counter(), None
            try:
                r = client.post(url, content=body)
                status = r.status_code
                r.raise_for_status()
                result["up"] = (len(body), time.perf_counter() - t0)
            except Exception as exc:  # noqa: BLE001
                result["errors"].append(f"upload: {exc}")
            egress.record("skill:speed_test", url, method="POST", status=status,
                          bytes_out=len(body), purpose="speed-test")
    return result


def describe(result: dict) -> str:
    lines = []
    if result["pings"]:
        p = sorted(result["pings"])
        lines.append(f"Latency: {p[0]:.0f} ms best, {p[len(p) // 2]:.0f} ms median ({len(p)} requests).")
    if result["down"]:
        got, want, seconds = result["down"]
        line = f"Download: {_mbps(got, seconds):.1f} Mbit/s ({got / 1e6:.1f} MB in {seconds:.1f} s)"
        if got < want:
            line += f" - cut short at {got * 100 // want}% of the {want / 1e6:.0f} MB asked for"
        lines.append(line + ".")
    if result["up"]:
        sent, seconds = result["up"]
        lines.append(f"Upload: {_mbps(sent, seconds):.1f} Mbit/s ({sent / 1e6:.1f} MB in {seconds:.1f} s).")
    for err in result["errors"]:
        lines.append(f"Not measured - {err}")
    if not lines:
        return "Nothing could be measured."
    lines.append("One measurement, to one Cloudflare server, at this moment - Wi-Fi, other "
                 "traffic and the server's load all move it.")
    return "\n".join(lines)


def _run(arguments: dict) -> str:
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to run a speed test: {reason}"
    down, up = _mb(arguments, "download_mb", _DEFAULT_DOWN_MB), _mb(arguments, "upload_mb", _DEFAULT_UP_MB)
    try:
        return describe(measure(down, up))
    except ImportError:
        return "The speed test needs the httpx package, which is not installed."


SKILLS = [Skill(name="speed_test", schema=SCHEMA, run=_run)]
