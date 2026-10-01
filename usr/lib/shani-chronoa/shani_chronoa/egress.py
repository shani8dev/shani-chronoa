"""An auditable record of what left this machine.

Why this exists
---------------

Chronoa is local-first and says so. That claim is currently *asserted*, not
*shown*: the audit trail in `tool_tracking.py` records which skills ran, and
the consent keys record which senses may run, but nothing records an outbound
request. An application that reads the screen, the camera, the clipboard and
the microphone should be able to answer "what did you send, where, and when"
without the user having to take its word for it.

This is the defensive half of the surveillance problem. The offensive half -
feeding a third party's monitoring agent a decoy feed, or rewriting the frames
inside someone else's database - is not something this project does or should
do. What it should do is the boring, verifiable thing: keep a local log that
distinguishes a request that stayed on the machine from one that did not, and
make the answerable question cheap to ask.

Two properties matter more than completeness:

- **Local and remote are recorded differently.** The whole value is the
  distinction. A request to `127.0.0.1` and a request to a cloud LLM provider
  look identical to httpx, and the difference is the only thing a user of a
  local-first tool actually cares about.
- **A remote call while privacy mode is on is an alarm, not a log line.**
  Chronoa's own rule is that privacy mode means nothing leaves the machine.
  If that invariant is ever broken by a code path that forgot the check, the
  honest thing is to record it loudly rather than quietly append it.

  That second property was, until 2026-09-30, unreachable from the running
  app. `record()` computed `violation = privacy_mode and not local` correctly
  and `tests/test_egress.py` proved the arithmetic, but *no call site ever
  passed `privacy_mode`* - all five kept its `False` default, so the flag was
  structurally always `False` and the `logger.error` below could not fire. A
  test that calls `record(privacy_mode=True)` itself cannot catch that: it
  supplies the very value the real code never supplies.
  `privacy_mode_enabled()` is now the one reader of the setting and every
  instrumented call site passes it, so the alarm is derived from the same
  `ChronoaConfig.privacy_mode` that gates `config.ollama_host` and
  `sense_allowed()` - one answer, not several. It reads fresh on every call,
  so toggling the setting takes effect on the very next request, and it fails
  toward *on* when the setting cannot be read, because a broken settings store
  must not silently disarm the invariant.

Records carry the destination and a byte count, never a request or response
body. A log that captured payloads would become the very leak it exists to
prevent.

**Where the log's own vocabulary comes from.** `record()` is given the URL that
was *actually fetched*, not the one that was asked for. On a path that follows
redirects those are different strings, and only the second one is true: it is
the host the bytes came from, so it is the host `local`, `host` and
`violation` must be computed from. `webtext.retrieve` therefore does not hand
the redirect to httpx - it walks the hops itself so that every one of them is
destination-checked before it is requested (see `check_destination`) and so
that the URL it records is the last one it actually fetched.
"""

import ipaddress
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from shani_chronoa import files
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

_LOG_NAME = "egress.jsonl"
_DEFAULT_EGRESS_DIR = Path.home() / ".local" / "share" / "shani-chronoa" / "egress"

# Public, and a caller may pin either to somewhere else. Nothing in the app does;
# the test suite pins both, and the resolvers below give a pin priority over the
# environment so those tests keep writing to their `tmp_path`.
EGRESS_DIR = _DEFAULT_EGRESS_DIR
EGRESS_LOG = EGRESS_DIR / _LOG_NAME


def _data_home() -> Path:
    """Delegates to `files.data_home`.

    The log path used to be a module-level constant built from a hardcoded
    `~/.local/share`, so a caller or test that relocated its data - the
    documented way to sandbox a run, and the only way to stop a suite writing
    into a real user's home - had no way to be obeyed. A suite run with only
    `XDG_STATE_HOME` redirected still appended every fixture record to the real
    audit log, which is how 4,320 lines once landed there.

    Resolved per call, never at import: a module is imported once per process
    while the environment it read is not, so a constant would make the answer
    depend on collection order.
    """
    return files.data_home()


def _egress_dir() -> Path:
    """Where the log is written, decided on every call.

    A pinned `EGRESS_DIR` wins over the environment deliberately: pinning is an
    explicit request for a location, and `XDG_DATA_HOME` is a default. In the
    running app nothing is pinned, so this is the XDG lookup on every single
    write, which is the behaviour that was missing.

    `_DEFAULT_EGRESS_DIR` is only ever *compared* against, never returned, so the
    import-time evaluation it carries cannot freeze a value that depends on the
    environment; the answer is recomputed below on each call.
    """
    if EGRESS_DIR != _DEFAULT_EGRESS_DIR:
        return EGRESS_DIR
    return _data_home() / "shani-chronoa" / "egress"


def _egress_log() -> Path:
    """The log file itself, with the same pin-then-environment precedence.

    Independent of `_egress_dir()` because the two are pinned independently - a
    caller may redirect the file without moving the directory, and the tests do
    exactly that in both directions.
    """
    if EGRESS_LOG != _DEFAULT_EGRESS_DIR / _LOG_NAME:
        return EGRESS_LOG
    return _egress_dir() / _LOG_NAME

# Loopback and the RFC 8375 / `.local` / `.localhost` namespaces, plus the
# literal default Ollama host. Anything else is treated as leaving the machine,
# because the failure mode of guessing wrong here is the bad one.
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}
_LOCAL_SUFFIXES = (".local", ".localhost", ".internal", ".localdomain")

# --- the ranges the destination policy is built from -------------------------
#
# The polarity here is deliberately the *opposite* of gemini-cli's
# `isAddressPrivate()` (`packages/core/src/utils/fetch.ts:290-325`), and the
# inversion is the design rather than a detail. That guard treats loopback and
# RFC1918 as "private" and refuses them, which is right for a coding CLI -
# there, the only loopback address anyone means is a developer's own dev
# server, and refusing it is a feature. Chronoa's own Ollama endpoint is
# `http://127.0.0.1:11434`; the product *is* that address, and `llm.py` and
# `senses/vision.py` post to it on every turn. Copied verbatim, gemini-cli's
# list would stop Chronoa working.
#
# So the shape is kept and the direction is flipped: an ALLOWLIST of this
# machine and the LAN behind it (`is_on_this_network()`), with a short DENYLIST
# layered on top for the destinations that are dangerous precisely *because*
# they are local.
#
# The ranges are written out explicitly rather than taken from
# `ipaddress.is_private`, because that property is not the same list in the
# direction this guard needs. Measured on the CPython 3.12 this repo runs:
# `is_private` is True for `198.18.0.0/15` (a benchmarking range) and for
# `2001:db8::/32` (documentation) - neither hosts anything dangerous - and
# False for `100.64.0.0/10` (CGNAT), the one entry on gemini-cli's list it
# does agree with. Using it would deny two harmless ranges and miss one.

# Allow side: loopback and the LAN behind this machine.
_LOOPBACK_ADDRESSES = frozenset(
    {ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address("::1")}
)
_LAN_V4 = tuple(
    ipaddress.ip_network(cidr)
    for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
# fc00::/7 is unique-local addressing: the IPv6 counterpart of RFC1918, i.e.
# the LAN. gemini-cli blocks it because it blocks every private range; here it
# is on the allow side for the same reason RFC1918 is.
_ULA_V6 = ipaddress.ip_network("fc00::/7")

# Deny side, and deliberately only link-local.
_LINK_LOCAL_V4 = ipaddress.ip_network("169.254.0.0/16")
_LINK_LOCAL_V6 = ipaddress.ip_network("fe80::/10")
_METADATA_ADDRESS = ipaddress.ip_address("169.254.169.254")

# Names for the cloud instance metadata service, which answers on the
# link-local address above on every major cloud and hands out IAM credentials
# and instance identity to anything that asks, unauthenticated, by design.
#
# `metadata.google.internal` is the one that has to be named here: it ends in
# `.internal`, so `is_local()` - correctly, as a split-horizon name - answers
# True for it, and so would `is_on_this_network()`. A denylist that could not
# override the allowlist would wave at precisely the address worth refusing.
# The deny check therefore runs first, and that ordering is what
# `tests/test_egress_privacy_alarm.py` pins.
_METADATA_HOSTS = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
        "instance-data.ec2.internal",
    }
)


def privacy_mode_enabled() -> bool:
    """Is privacy mode on right now? Never raises, and fails toward ON.

    The single reader of `privacy-mode` for every egress call site. Not a
    second mechanism for the question: it constructs the same `ChronoaConfig`
    the rest of the app constructs and reads the same `privacy_mode` property,
    so the alarm is derived from exactly the value that gates
    `config.ollama_host`, `sense_allowed()` and the cloud fallback. A fresh
    instance per call is deliberate and matches `web_search.py`'s own note
    that a fresh `ChronoaConfig()` is built every time precisely so a toggle
    takes effect immediately; caching it here would reintroduce the stale
    read the property's docstring says it exists to avoid.

    The import is inside the function because `config` pulls in `gi`/`Gio`.
    `egress` is imported by `llm.py`, `cloud_llm.py`, `webtext.py` and
    `senses/vision.py`, and a module-level import would make the audit log
    unimportable on a system without GObject - turning "the alarm cannot be
    evaluated" into "the request cannot be made".

    The direction of the fallback is the part that matters. `record()` is
    documented never to raise, and a GSettings read fails in more ways than it
    succeeds (no compiled schema, no dconf, a locked-down keyring). If that
    degraded to "privacy mode is off" then a broken settings store would
    quietly disarm the invariant this module exists to defend, and the result
    would be indistinguishable from a clean run. A false alarm is a log line
    and a message; a missed breach is a user who believes nothing left.
    """
    try:
        from shani_chronoa.config import ChronoaConfig

        return bool(ChronoaConfig().privacy_mode)
    except Exception as exc:  # noqa: BLE001 - must not raise into a request path
        logger.warning(
            "Could not read the privacy-mode setting (%s: %s); treating it as ON for "
            "the egress alarm, so a remote request may be reported as a violation "
            "when privacy mode is actually off.",
            type(exc).__name__,
            exc,
        )
        return True


def _as_ip(host: str):
    """`host` as an ipaddress object, or None if it is a name, not an address.

    None means "this is a hostname" and is a normal answer, not a failure -
    most destinations on the open internet are names.
    """
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _unmapped(address):
    """The IPv4 address an IPv6 address really is, when it is really one.

    `::ffff:127.0.0.1` is loopback in every meaningful sense, but CPython
    reports `is_loopback` as False for it and leaves the answer in
    `ipv4_mapped`, so any range check written against the IPv4 or IPv6 side
    misses it. gemini-cli's `isAddressPrivate()` meets the same edge and
    re-checks mapped addresses (fetch.ts:311-313); `ipv4_mapped` is the stdlib
    way of doing exactly that, and unwrapping once here means neither
    `is_local()` nor the destination guard has to remember to.
    """
    return getattr(address, "ipv4_mapped", None) or address


def is_local(url: str) -> bool:
    """Whether a URL stays on this machine.

    Deliberately conservative: an unparseable or schemeless target counts as
    remote, so a malformed URL can never be mistaken for a safe one.

    That conservatism is why this is *not* the whole of the destination policy
    below, and why a bare RFC1918 literal answers False here even though
    `is_on_this_network()` answers True. The two are asking different
    questions: this one is the audit's ("did those bytes leave this machine?" -
    and a LAN IP says they did), the other is the guard's ("is this somewhere
    on my own network, or somebody else's?"). Merging them would either
    mislabel every LAN request in the audit or refuse to talk to a LAN Ollama.

    An IPv4-mapped IPv6 loopback literal does resolve True, and that is a
    genuine defect fix rather than a policy choice: `http://[::ffff:127.0.0.1]/`
    is this machine, and answering False would raise a privacy-mode violation
    against a request that never left.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if not host:
        return False
    if host in _LOCAL_HOSTS:
        return True
    if any(host.endswith(suffix) for suffix in _LOCAL_SUFFIXES):
        return True
    address = _as_ip(host)
    if address is not None and _unmapped(address) in _LOOPBACK_ADDRESSES:
        return True
    return False


class DestinationRefused(Exception):
    """A URL that must not be fetched, with a message fit to show a user."""


def is_on_this_network(url: str) -> bool:
    """Whether a destination is this machine, or the LAN behind it.

    Broader than `is_local()` and deliberately defined in terms of it rather
    than alongside it: `is_local()` already knows the loopback literals and
    the RFC 8375 / `.local` / `.internal` / `.localdomain` names, and a second
    copy of that knowledge is exactly how two classifiers come to disagree
    about whether a request left the building. So this asks `is_local()` first
    and then adds the one thing it cannot express - which numeric ranges count
    as not-an-external-destination.

    The allow side being this permissive is the inverted-polarity claim, made
    checkable: `http://127.0.0.1:11434/api/chat` is Chronoa's own inference
    endpoint and must stay reachable, and a user's Ollama on the LAN is the
    same argument one hop out.
    """
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    if is_local(url):
        return True
    address = _as_ip(host)
    if address is None:
        return False
    address = _unmapped(address)
    if address in _LOOPBACK_ADDRESSES:
        return True
    if isinstance(address, ipaddress.IPv4Address):
        return any(address in network for network in _LAN_V4)
    return address in _ULA_V6


def denied_reason(url: str) -> str:
    """Why this URL must not be fetched, or "" when it may be. Never raises.

    Returns a sentence rather than a bool so the refusal a user reads says what
    was wrong, and so "could not determine" is a value this function can
    actually return instead of collapsing into permission.
    """
    try:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError as exc:
        return f"the address could not be parsed, so it cannot be checked ({exc})"
    if not host:
        return "there is no host in it to check"

    # The deny side is consulted first, and is allowed to override the LAN
    # allowlist below. See _METADATA_HOSTS for why that ordering is the point.
    if host in _METADATA_HOSTS:
        return (
            f"{host} is a cloud instance metadata service, which hands out "
            f"credentials to anything that asks"
        )
    address = _as_ip(host)
    if address is not None:
        address = _unmapped(address)
        in_link_local = address in (
            _LINK_LOCAL_V4 if isinstance(address, ipaddress.IPv4Address) else _LINK_LOCAL_V6
        )
        if in_link_local:
            if address == _METADATA_ADDRESS:
                return (
                    "it is the cloud instance metadata address, which is link-local and "
                    "hands out IAM credentials and instance identity to anything that asks"
                )
            return (
                f"{host} is a link-local address, and on this machine link-local is where "
                f"the cloud metadata service answers (169.254.169.254)"
            )

    # Allow side, then the ordinary case: a public destination, which is what
    # a web fetch usually is and is not a reason to refuse.
    if is_on_this_network(url):
        return ""
    return ""


def check_destination(url: str) -> None:
    """Raise `DestinationRefused` unless `url` is a destination Chronoa may fetch.

    Fails closed, following gemini-cli's `validateUrlDestination()`
    (fetch.ts:413-420): an exception anywhere in the check is a refusal, not a
    pass. That is the direction this repo's own standing rule requires - a
    destination whose safety could not be determined is a destination this
    cannot say is safe, and a plausible clean answer here is the expensive
    kind of wrong.
    """
    try:
        reason = denied_reason(url)
    except Exception as exc:  # noqa: BLE001 - the whole point is to fail closed
        raise DestinationRefused(
            f"that address could not be checked, so it will not be fetched ({exc})"
        ) from exc
    if reason:
        raise DestinationRefused(reason)


@dataclass
class Event:
    """One outbound request. Metadata only - never a payload."""

    at: float
    component: str
    url: str
    host: str
    local: bool
    method: str = "GET"
    status: Optional[int] = None
    bytes_out: int = 0
    privacy_mode: bool = False
    violation: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def payload_size(obj: object) -> int:
    """Byte length of a payload, best-effort, never raising.

    Callers compute this from a payload they are about to hand to httpx, often
    from a `finally` block on the error path. An exception raised here would
    mask the original failure, so anything unserializable counts as 0 rather
    than propagating.
    """
    try:
        return len(json.dumps(obj))
    except (TypeError, ValueError, RecursionError):
        return 0


def record(
    component: str,
    url: str,
    *,
    method: str = "GET",
    status: Optional[int] = None,
    bytes_out: int = 0,
    privacy_mode: bool = False,
) -> Event:
    """Append one event to the local log. Never raises.

    A telemetry log that can crash the request it is describing is worse than
    no log, so every failure here degrades to "not recorded".
    """
    local = is_local(url)
    event = Event(
        at=time.time(),
        component=component,
        url=url,
        host=(urlsplit(url).hostname or "") if "://" in url else url,
        local=local,
        method=method.upper(),
        status=status,
        bytes_out=int(bytes_out or 0),
        privacy_mode=bool(privacy_mode),
        # Chronoa's documented invariant: privacy mode means nothing leaves.
        violation=bool(privacy_mode and not local),
    )
    try:
        directory = _egress_dir()
        log = _egress_log()
        # mkdir then chmod, not mkdir(mode=...): the mode argument is masked by
        # the process umask and silently lands permissive, which is the same
        # reasoning documented in triggers._ensure_state_dir.
        directory.mkdir(parents=True, exist_ok=True)
        try:
            directory.chmod(0o700)
        except OSError as exc:
            logger.debug("egress log directory chmod failed: %s", exc)
        line = json.dumps(event.to_dict()) + "\n"
        # Created with 0600 rather than chmod'd afterwards, so there is no window
        # in which a freshly created log is group/world readable under a
        # permissive umask. The chmod below is kept anyway, because it also
        # tightens a file left loose by an older build.
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
        try:
            log.chmod(0o600)
        except OSError:
            pass
    except (OSError, TypeError, ValueError) as exc:
        logger.debug("egress log append failed: %s", exc)
    if event.violation:
        logger.error(
            "EGRESS: %s sent a request to %s while privacy mode was ON - this breaks "
            "Chronoa's documented local-only guarantee",
            component,
            event.host,
        )
    return event


def read_events(limit: Optional[int] = None) -> list:
    """Read recorded events, newest last. A missing log is an empty one."""
    try:
        lines = _egress_log().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out[-limit:] if limit else out


def summary() -> dict:
    """The question a user actually asks: did anything leave, and where."""
    events = read_events()
    remote = [e for e in events if not e.get("local", True)]
    return {
        "total": len(events),
        "remote": len(remote),
        "local": len(events) - len(remote),
        "violations": sum(1 for e in events if e.get("violation")),
        "bytes_out": sum(int(e.get("bytes_out") or 0) for e in events),
        "remote_hosts": sorted({e.get("host", "") for e in remote}),
        "log": str(_egress_log()),
    }
