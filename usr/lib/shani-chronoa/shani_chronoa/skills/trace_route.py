"""Skill: trace the network path to a host, and say where it stops answering.

`check_internet` walks a **fixed** ladder — default route, router, 1.1.1.1, one
name, one website — and answers "is the internet working". It cannot be pointed
at a named host, because it takes no arguments at all. So the two questions a
person actually has when something specific is slow ("why is *this* slow?",
"where does the path to *this* break?") had no answer, and the only route tool
on the image (`mtr`) was unused.

**The trap this skill exists to avoid is reading `mtr` output naively.** A real
trace taken while writing this module:

      1.|-- router.local              0.0%     5    4.8   3.5   2.6   4.8   0.9
      4.|-- ???                     100.0     5    0.0   0.0   0.0   0.0   0.0
      5.|-- ???                     100.0     5    0.0   0.0   0.0   0.0   0.0
      6.|-- 2405:200:801:1d00::2f8    0.0%     5  104.5  50.0  29.7  104.5  31.7
      7.|-- ???                     100.0     5    0.0   0.0   0.0   0.0   0.0
      8.|-- 2405:200:1602:600:...     20.0%     5   31.9  68.4  31.9 100.5  28.9

Three hops show **100% loss** and traffic is plainly flowing, because hops 6
and 8 answer. Routers deprioritise ICMP aimed at their own control plane, and
some drop it outright, so a silent middle hop is a property of that router, not
evidence of a fault on the path. A tool that reported "hops 4-5 and 7 are
broken" would be confidently wrong, which is the specific thing this repo
refuses to ship.

So the summary here is built from the **last** hop, not from the worst-looking
one, and every silent hop is labelled as having not responded rather than as
down. Loss is only ever called a problem when the destination itself loses
packets, and even then the reply says what else could explain it.

Honesty rules:

- **A silent hop is `did not respond`, never `down`.** The distinction is the
  whole point.
- **The destination's own loss is reported with the alternatives beside it** —
  ICMP filtering at the far end is at least as common as a real fault.
- **`mtr` is asked which address it traced**, because a name can resolve to
  several and the one it picked is the one every number below describes.
- **A silent run says so instead of printing an empty trace**, and a missing
  `mtr` is named with the package that provides it.
- **Probes are bounded** (`-c`, and a hard timeout) so this cannot become a
  long unattended wait.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from typing import List, NamedTuple, Optional

from shani_chronoa import egress, files
from shani_chronoa.skills import Skill

logger = logging.getLogger(__name__)

#: `mtr -r -w` needs several probes before a loss percentage means anything.
#: Ten is mtr's own default and finishes in a few seconds.
_DEFAULT_PROBES = 10
_MIN_PROBES, _MAX_PROBES = 3, 50

#: An upper bound on the whole run. A trace that cannot finish in this is not
#: going to finish, and a skill that hangs is worse than one that admits it.
_TIMEOUT = 45

#: Enough for a real path, short enough that the reply is a spoken answer.
_MAX_ROWS = 30

#: `  4.|-- ???                                   100.0     5    0.0   0.0   0.0   0.0   0.0`
#: and the wide form, where the loss column may print as `100.0` without a `%`.
_ROW = re.compile(
    r"^\s*(?P<hop>\d+)\.\|--\s+(?P<host>\S+)\s+"
    r"(?P<loss>[\d.]+)\s*%?\s+(?P<snt>\d+)\s+"
    r"(?P<last>[\d.]+)\s+(?P<avg>[\d.]+)\s+(?P<best>[\d.]+)\s+(?P<wrst>[\d.]+)"
)

#: mtr resolves the traced host on its own and prints it in the `HOST:` line.
#: It is how we tell the user *which* address the numbers describe, which
#: matters as soon as a name has both an A and an AAAA record.
_HOST_LINE = re.compile(r"^HOST:\s*(?P<host>\S+)")
_RESOLVED = re.compile(r"^\s*Hosts:\s*(?P<rest>.+)$", re.MULTILINE)


class Hop(NamedTuple):
    number: int
    host: str
    loss: float
    sent: int
    last: float
    avg: float
    best: float
    worst: float

    @property
    def answered(self) -> bool:
        """Whether the router replied at all. A silent router is not a dead one."""
        return self.sent > 0 and self.loss < 100.0


SCHEMA = {
    "type": "function",
    "function": {
        "name": "trace_route",
        "description": (
            "Trace the network path to a host: every router along the way, how "
            "much of the traffic each one dropped, and how many milliseconds "
            "it took. Use when something specific is slow or unreachable, "
            "which is different from whether the internet works at all."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "host": {
                    "type": "string",
                    "description": "The hostname or IP address to trace the path to.",
                },
                "probes": {
                    "type": "integer",
                    "description": (
                        f"Packets to send to each router, which is what the loss "
                        f"percentage is computed from. Default {_DEFAULT_PROBES}, "
                        f"between {_MIN_PROBES} and {_MAX_PROBES}. More probes "
                        f"give a truer loss figure and take longer."
                    ),
                },
            },
            "required": ["host"],
        },
    },
}


def parse(text: str) -> "tuple[List[Hop], Optional[str]]":
    """Parse `mtr -r -w` output. Returns `(hops, host_that_was_traced)`."""
    hops: List[Hop] = []
    traced: Optional[str] = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("HOST:"):
            match = _HOST_LINE.match(line)
            # The local hostname is printed here too, so only take it when mtr
            # also listed resolved addresses - otherwise this is just our name.
            if match and "Hosts:" in text:
                traced = match.group("host")
            continue
        resolved = _RESOLVED.match(line)
        if resolved:
            traced = resolved.group("rest").strip()
            continue
        match = _ROW.match(line)
        if not match:
            continue
        try:
            hops.append(Hop(
                number=int(match.group("hop")),
                host=match.group("host"),
                loss=float(match.group("loss")),
                sent=int(match.group("snt")),
                last=float(match.group("last")),
                avg=float(match.group("avg")),
                best=float(match.group("best")),
                worst=float(match.group("wrst")),
            ))
        except ValueError:
            continue
    return hops, traced


def _hop_text(hop: Hop) -> str:
    where = hop.host if hop.answered else f"{hop.host} (did not respond to a probe)"
    return (f"  {hop.number:>2}. {where:38s} {hop.loss:5.1f}% lost, "
            f"avg {hop.avg:.0f} ms")


def _verdict(hops: List[Hop]) -> str:
    """What the trace actually shows, read off the destination rather than the worst hop.

    Every branch here exists because the naive reading is wrong. `mtr` can
    report 100% loss on three hops of a perfectly healthy path, so the only
    loss figure that means anything is the destination's own, and even that has
    an innocent explanation.
    """
    last = hops[-1]
    # Partial loss in the middle is a different signal from a silent hop, and
    # worth saying: a router answering 40% of probes when the destination
    # answers all of them often means the path really is congested there, which
    # the "middle hops do not matter" rule would otherwise talk away.
    partial = [hop for hop in hops[:-1] if hop.answered and hop.loss > 0]
    middle = ""
    if partial:
        worst = max(partial, key=lambda hop: hop.loss)
        # The comparison to the destination has to reflect what the destination
        # actually did. A hardcoded "while the destination answered all of them"
        # contradicts this very sentence in the branch where the destination is
        # the lossy one.
        compared = ("while the destination answered all of them"
                    if last.loss == 0 else
                    f"and the destination dropped {last.loss:.0f}%")
        middle = (
            f" Note that {len(partial)} intermediate hop(s) answered only some "
            f"probes - hop {worst.number} answered {100 - worst.loss:.0f}% of them, "
            f"{compared}. That pattern often means real congestion or "
            f"rate-limiting on that hop, so if something is slow this is where to "
            f"look; it is not the same as a silent hop."
        )
    if not last.answered:
        return (
            f"Hop {last.number} ({last.host}) never answered. That is the far end, "
            f"not a hop in the middle, so it means the destination did not reply to "
            f"these probes - it may still be perfectly reachable and simply not "
            f"answer ICMP. This trace does not show a broken path: no earlier hop "
            f"reported loss."
        )
    if last.loss > 0:
        return (
            f"The destination (hop {last.number}, {last.host}) dropped "
            f"{last.loss:.0f}% of {last.sent} probes, averaging {last.avg:.0f} ms. "
            f"That is the only figure here that indicates loss, because routers in "
            f"the middle of a path routinely discard probes aimed at their own "
            f"control plane. So the path itself is intact; if something is slow, "
            f"the far end or the link to it is where to look." + middle
        )
    note = ""
    if silent := [hop.number for hop in hops if not hop.answered]:
        note = (
            f" {len(silent)} hop(s) in the middle did not answer at all "
            f"(hop {', '.join(str(n) for n in silent)}). That is normal and is not "
            f"a fault: routers deprioritise or drop ICMP sent to themselves, and "
            f"traffic continues through them."
        )
    return (
        f"The destination (hop {last.number}, {last.host}) answered every probe, "
        f"averaging {last.avg:.0f} ms over {len(hops)} hop(s). The path is intact "
        f"end to end.{note}{middle}"
    )


def _run(arguments: dict) -> str:
    host = (arguments.get("host") or "").strip()
    if not host:
        return "No host was named, so there is no path to trace."
    # The argument goes to `mtr` as one argv element, never through a shell, but
    # a value that is not a hostname or an IP is refused by name rather than
    # handed to mtr to interpret.
    if not re.fullmatch(r"[A-Za-z0-9._:\-\[\]%]+", host):
        return (
            f"{host!r} is not a hostname or an IP address, so there is nothing to "
            f"trace to it."
        )

    probes = arguments.get("probes")
    if probes is None:
        probes = _DEFAULT_PROBES
    elif isinstance(probes, bool) or not isinstance(probes, int) \
            or not _MIN_PROBES <= probes <= _MAX_PROBES:
        return (f"'probes' must be a whole number between {_MIN_PROBES} and "
                f"{_MAX_PROBES}, not {probes!r}.")

    if shutil.which("mtr") is None:
        return files.tool_missing(
            "mtr", f"trace the network path to {host}")

    # mtr sends probes past this machine's own network, exactly as the public
    # steps of `check_internet` do, so it obeys the same switch.
    if egress.privacy_mode_enabled():
        return (
            f"Privacy mode is on, so I did not trace the path to {host} - that "
            f"would send probes beyond your network. Turn privacy mode off, or "
            f"use `check_internet`, which stays local."
        )

    try:
        done = subprocess.run(
            ["mtr", "-r", "-w", "-n", "-c", str(probes), host],
            capture_output=True, text=True, timeout=_TIMEOUT)
    except subprocess.TimeoutExpired:
        return (f"Tracing to {host} did not finish within {_TIMEOUT} seconds, so "
                f"no result is reported. Fewer probes may complete.")
    except OSError as exc:
        return f"Could not run mtr: {exc}."

    egress.record("skill:trace_route", host, method="ICMP", privacy_mode=False)

    hops, traced = parse(done.stdout)
    if not hops:
        detail = (done.stderr or done.stdout or "").strip().splitlines()
        # mtr prints its complaint on stdout in report mode, so both are offered
        # rather than reporting an empty trace with no reason attached.
        reason = detail[-1] if detail else "no output"
        return (
            f"No trace came back for {host}: {reason}. That is mtr reporting "
            f"nothing, not a finding that the host is unreachable - it may be "
            f"down, filtered, or the probe type may be blocked on this network."
        )

    shown, withheld = files.cap_list(hops, _MAX_ROWS)
    header = f"Path to {host}"
    if traced and traced != host:
        header += f" (traced {traced})"
    body = "\n".join(_hop_text(hop) for hop in shown)
    if withheld:
        # The verdict still describes every hop, so a reader is not left
        # concluding from a short list that the path ends where the list does.
        body += files.withheld_note(
            "hop", withheld, "ask again with a lower probe count for a shorter run")
    return f"{header} - {len(hops)} hop(s), {probes} probes each:\n{body}\n{_verdict(hops)}"


SKILLS = [Skill(name="trace_route", schema=SCHEMA, run=_run)]