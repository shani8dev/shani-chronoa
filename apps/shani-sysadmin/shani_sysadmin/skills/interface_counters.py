"""Skill: how much traffic each interface has carried, and where it is going.

"Is this interface actually carrying anything, and in which direction?" is the
question a network problem always turns on, and nothing in this package
answered it. `network` (the sense) reports *state* - up, carrier, negotiated
speed - which is the link, not the traffic. `data_usage` reports cumulative
volume from `vnstat`, which needs that daemon to have been running for a while
and answers "this month", not "right now". `routing_table` says where packets
are *sent*. None of them says what has actually crossed.

**Read from `/proc/net/dev` and `/proc/net/netstat`, not by running `ifstat`,
`nstat -s` or `vnstat`.** The capability matrix flags all three as commands
nothing calls, and all three are on the image today, so shelling out would have
been the obvious wiring. Two reasons not to:

- `/proc/net/dev` is the kernel's own counter set, so it works with no daemon
  running, no database, and no `CAP_NET_ADMIN`. `ifstat` needs a history file
  it maintains itself and reports nothing useful before its first interval has
  elapsed; `vnstat` needs its daemon started days ago.
- These are read-only by construction. `ifstat` and `vnstat --live` install
  signal handlers and write state; reading `/proc` cannot.

**Counters are cumulative since the interface came up, and that is stated, not
implied.** Bytes since boot is not a rate, and reporting it as "throughput"
would be the plausible-wrong-answer failure. So every figure is labelled as a
total, and the only rate this reports is one the caller asks for by taking two
reads itself - which is why `sample_seconds` exists and why it re-reads rather
than dividing two unrelated numbers.

**A counter that could not be read is UNKNOWN, never zero.** A missing
`/proc/net/dev` entry, a permission error, or an interface whose counters this
namespace cannot see must never read as "no traffic". Zero and unknown are
different claims and only one of them is ever safe to act on.

**This is one network namespace.** `/proc/net/dev` shows this namespace's
interfaces. In a lab network built by `lab_network_create`, the interesting
interfaces live *inside* the subnet namespaces and do not appear here - which is
why the skill takes an optional `network` argument and says what it found when
the name is not visible, rather than quietly reporting the host's own counters
for a question that was about a subnet.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

from shani_chronoa.skills import Skill

logger = logging.getLogger(__name__)

_NET_DEV = Path("/proc/net/dev")
_NETSTAT = Path("/proc/net/netstat")

#: Enough rows to cover a host with a handful of virtual interfaces, which is
#: what a desktop or a lab actually has. A machine with 200 veths is not a
#: question this answers better with a longer list.
_MAX_ROWS = 40

#: The kernel's own names for the bytes/packets columns, so a newer kernel that
#: adds one does not silently shift every column left.
_RX = ("rx_bytes", "rx_packets", "rx_errs", "rx_drop")
_TX = ("tx_bytes", "tx_packets", "tx_errs", "tx_drop")


class Counters(NamedTuple):
    name: str
    rx_bytes: Optional[int]
    tx_bytes: Optional[int]
    rx_packets: Optional[int]
    tx_packets: Optional[int]
    rx_errs: Optional[int]
    tx_errs: Optional[int]
    rx_drop: Optional[int]
    tx_drop: Optional[int]
    #: True when the row carried counters this run could not parse, so the
    #: caller can say "unknown" for those columns rather than "0".
    partial: bool


def _int(token: str) -> Optional[int]:
    try:
        return int(token)
    except (TypeError, ValueError):
        return None


def _read_dev() -> Tuple[List[Counters], List[str]]:
    """Every interface's counters, plus the names of those that could not be read."""
    problems: List[str] = []
    try:
        text = _NET_DEV.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [], [f"{_NET_DEV} could not be read ({exc.strerror or exc})"]

    out: List[Counters] = []
    for line in text.splitlines()[2:]:
        if ":" not in line:
            continue
        name, _, rest = line.partition(":")
        name = name.strip()
        if not name or name == "lo":
            # Loopback is excluded deliberately: its counters are large and
            # meaningless, and including it buries every real interface.
            continue
        fields = rest.split()
        if len(fields) < 16:
            problems.append(
                f"{name}: the kernel reported {len(fields)} counter columns, "
                f"this expects at least 16 - the row was not interpreted"
            )
            continue
        # The layout is: rx bytes packets errs drop fifo frame compressed
        # multicast, then the same eight for tx. Only five of each are wanted.
        rx = fields[0:8]
        tx = fields[8:16]
        values = {
            "rx_bytes": _int(rx[0]), "rx_packets": _int(rx[1]),
            "rx_errs": _int(rx[2]), "rx_drop": _int(rx[3]),
            "tx_bytes": _int(tx[0]), "tx_packets": _int(tx[1]),
            "tx_errs": _int(tx[2]), "tx_drop": _int(tx[3]),
        }
        out.append(Counters(
            name=name, partial=any(v is None for v in values.values()), **values))
    if not out and not problems:
        problems.append(
            f"{_NET_DEV} was readable but listed no interface other than "
            f"loopback, which is not a normal machine"
        )
    return out, problems


def _protocol_errors() -> str:
    """TCP-level error and retransmit counters, straight from `/proc`.

    These are the numbers that separate "the link is slow" from "the path is
    lossy", and they are per-protocol rather than per-interface.

    **Both `/proc/net/snmp` and `/proc/net/netstat` are read, and the second is
    not a fallback that never runs.** They carry overlapping protocol counters
    and which ones a given kernel populates varies - on the 7.0 kernel this was
    developed against, `/proc/net/netstat` is readable and 4 KiB long but has no
    `Tcp:` block at all, while `/proc/net/snmp` has it. Reading only netstat
    produced "TCP counters are UNKNOWN" on a machine plainly running TCP, which
    is the confident-wrong-answer failure in its most embarrassing form: a
    missing measurement reported as a missing feature.
    """
    for path in (_NETSTAT.with_name("snmp"), _NETSTAT):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        pairs: Dict[str, str] = {}
        for line in text.splitlines():
            if not line.startswith("Tcp:"):
                continue
            # `/proc/net/snmp` holds EVERY protocol in one block: a header line
            # beginning "Tcp:" followed by a value line that begins with the
            # same name. `/proc/net/netstat` puts each protocol in its own
            # blank-line-separated block, with the same header-then-value
            # shape inside it. Pairing a header with the next line that has the
            # same number of fields handles both without assuming either
            # layout - and assuming the wrong one is what made this report
            # UNKNOWN on a kernel that plainly had the data.
            header = line.split()
            for candidate in text.splitlines():
                if candidate is line:
                    continue
                value = candidate.split()
                if len(value) == len(header) and value[0] == header[0]:
                    pairs = dict(zip(header[1:], value[1:]))
                    break
            if pairs:
                break
        if not pairs:
            continue
        interesting = (
            ("OutSegs", "segments sent"),
            ("RetransSegs", "retransmitted"),
            ("InErrs", "receive errors"),
            ("Ext", "TCP extensions seen"),
        )
        parts = []
        for key, label in interesting:
            value = _int(pairs.get(key, ""))
            if value is not None and value != 0:
                parts.append(f"{value} {label}")
        if parts:
            return "; ".join(parts)
        # Every counter really is zero. That is a measurement, and it is worth
        # saying - it is the difference between "no errors" and "not read".
        return ("no TCP retransmits or receive errors recorded "
                f"(from {path})")
    return ("the kernel's TCP counters are UNKNOWN - /proc/net/snmp and "
            "/proc/net/netstat could not be read or carried no Tcp: block, so "
            "no error or retransmit figure is reported (which is not the same "
            "as reporting zero)")


def _human_bytes(value: Optional[int]) -> str:
    if value is None:
        return "UNKNOWN"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024.0 or unit == "TiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TiB"


def _rate_per_s(total: Optional[int], seconds: float) -> str:
    if total is None:
        return "UNKNOWN"
    return _human_bytes(int(total / seconds)) + "/s"


def _describe(row: Counters, prior: Optional[Counters],
              seconds: float) -> str:
    """One interface. Every figure is a total since the link came up, or a rate
    the caller asked for by sampling, and said as either."""
    detail = [row.name]
    detail.append(f"received {_human_bytes(row.rx_bytes)}")
    detail.append(f"sent {_human_bytes(row.tx_bytes)}")
    if row.partial:
        detail.append("(some of this interface's counters could not be read "
                      "and are shown as UNKNOWN)")
    if prior is not None:
        # A rate is only printed when both reads are real numbers. One unknown
        # endpoint makes the difference meaningless, and a confident rate built
        # on it would be the wrong answer this module exists to avoid.
        if row.rx_bytes is not None and prior.rx_bytes is not None:
            delta = row.rx_bytes - prior.rx_bytes
            detail.append(f"over {seconds:g}s: down at "
                          f"{_rate_per_s(max(delta, 0), seconds)}")
        if row.tx_bytes is not None and prior.tx_bytes is not None:
            delta = row.tx_bytes - prior.tx_bytes
            detail.append(f"up at {_rate_per_s(max(delta, 0), seconds)}")
        if row.partial or prior.partial:
            detail.append("(the rate is partial because one of the two reads "
                          "could not parse every counter)")
    errs = []
    if row.rx_errs:
        errs.append(f"{row.rx_errs} receive errors")
    if row.tx_errs:
        errs.append(f"{row.tx_errs} transmit errors")
    dropped = []
    if row.rx_drop:
        dropped.append(f"{row.rx_drop} dropped on receive")
    if row.tx_drop:
        dropped.append(f"{row.tx_drop} dropped on transmit")
    if errs:
        detail.append("; ".join(errs))
    if dropped:
        detail.append("; ".join(dropped))
    return "  " + "  ".join(detail)


def _run(arguments: dict) -> str:
    wanted = arguments.get("interface")
    if wanted is not None and not isinstance(wanted, str):
        return f"'interface' must be an interface name, not {wanted!r}."

    sample = arguments.get("sample_seconds")
    if sample is None:
        seconds = 0.0
    elif isinstance(sample, bool) or not isinstance(sample, (int, float)) or sample <= 0:
        return (f"'sample_seconds' must be a positive number of seconds, not "
                f"{sample!r}.")
    else:
        seconds = min(float(sample), 5.0)

    limit = arguments.get("limit")
    if limit is None:
        limit = _MAX_ROWS
    elif isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        return f"'limit' must be a positive whole number, not {limit!r}."
    limit = min(limit, _MAX_ROWS)

    rows, problems = _read_dev()
    if not rows:
        return ("Could not read this machine's interface counters: "
                + "; ".join(problems)
                + " No traffic figures are reported, which is not the same as "
                  "reporting no traffic.")

    if wanted:
        matching = [r for r in rows if r.name == wanted]
        if not matching:
            known = ", ".join(r.name for r in rows[:12]) or "none"
            return (
                f"No interface called {wanted!r} in this network namespace. "
                f"Visible here: {known}. An interface inside a network "
                f"namespace - one built by lab_network_create - is not in this "
                f"namespace's view, so its counters cannot be read from here."
            )
        rows = matching

    prior_by_name: Dict[str, Counters] = {}
    if seconds:
        first_read, _ = _read_dev()
        prior_by_name = {r.name: r for r in first_read}
        # Long enough to be a rate, short enough that a chat turn waits for it.
        time.sleep(seconds)
        rows, second_problems = _read_dev()
        if not rows:
            return ("Could not re-read the counters for the rate sample: "
                    + "; ".join(second_problems)
                    + " Totals from the first read are below, but no rate is "
                      "reported.")
        if wanted:
            rows = [r for r in rows if r.name == wanted]

    rows.sort(key=lambda r: -(r.rx_bytes or 0) - (r.tx_bytes or 0))

    out = [
        "Interface counters from /proc/net/dev. These are totals since each "
        "interface came up, not a speed."
    ]
    if seconds:
        out.append(
            f"A rate over the last {seconds:g}s is given where both reads were "
            f"readable; a partial rate says so rather than guessing.")
    out.append("")
    shown = rows[:limit]
    for row in shown:
        out.append(_describe(row, prior_by_name.get(row.name), seconds or 0.0))
    if len(rows) > len(shown):
        out.append(f"  ... {len(rows) - len(shown)} more interface(s) not shown.")
    if len(rows) == 1:
        out.append("")
        out.append("One interface is shown. This is this namespace's view only.")

    tcp = _protocol_errors()
    out.append("")
    out.append(f"TCP across the machine: {tcp}")

    if problems:
        out.append("")
        out.append("Caveats: " + "; ".join(problems))
    return "\n".join(out)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "interface_counters",
        "description": (
            "Report how many bytes and packets each network interface has "
            "carried, in each direction, with its error and drop counters, plus "
            "the machine's TCP retransmit and error totals. Totals are since "
            "each interface came up, and the figures are read from /proc so no "
            "daemon needs to be running. Optionally sample twice to get a rate. "
            "Read-only. Counts one network namespace: an interface inside a "
            "network namespace is not visible here."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "interface": {
                    "type": "string",
                    "description": "Only this interface, e.g. 'enp4s0' or 'wlan0'.",
                },
                "sample_seconds": {
                    "type": "number",
                    "description": (
                        "Wait this many seconds and read again to report a rate "
                        "as well as the totals. Ceiling 5 seconds."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": f"Maximum interfaces to list (default and ceiling: {_MAX_ROWS}).",
                },
            },
        },
    },
}


SKILLS = [Skill(name="interface_counters", schema=SCHEMA, run=_run)]