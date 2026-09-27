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

Records carry the destination and a byte count, never a request or response
body. A log that captured payloads would become the very leak it exists to
prevent.
"""

import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

EGRESS_DIR = Path(os.path.expanduser("~/.local/share/shani-chronoa/egress"))
EGRESS_LOG = EGRESS_DIR / "egress.jsonl"

# Loopback and the RFC 8375 / `.local` / `.localhost` namespaces, plus the
# literal default Ollama host. Anything else is treated as leaving the machine,
# because the failure mode of guessing wrong here is the bad one.
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}
_LOCAL_SUFFIXES = (".local", ".localhost", ".internal", ".localdomain")


def is_local(url: str) -> bool:
    """Whether a URL stays on this machine.

    Deliberately conservative: an unparseable or schemeless target counts as
    remote, so a malformed URL can never be mistaken for a safe one.
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
    return any(host.endswith(suffix) for suffix in _LOCAL_SUFFIXES)


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
        EGRESS_DIR.mkdir(parents=True, exist_ok=True)
        with open(EGRESS_LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict()) + "\n")
        try:
            EGRESS_LOG.chmod(0o600)
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
        lines = EGRESS_LOG.read_text(encoding="utf-8").splitlines()
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
        "log": str(EGRESS_LOG),
    }
