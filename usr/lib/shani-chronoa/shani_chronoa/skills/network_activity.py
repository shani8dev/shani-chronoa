"""Skill: what is using my network right now?

Two existing skills bracket this question and neither reaches it.
`data_usage` answers **"how much have I used today"** (vnstat, cumulative,
yesterday's traffic included) and `bandwidth_to_host` answers **"how fast is
this one machine"** (iperf3, to a named host, needing a server on the other
end). The question in between is the everyday one:

> **"Something is using my bandwidth - which program?"**

`ifconfig`, `netstat` and `ip` all report **counters**, so they can say an
interface moved 40 MB without saying what moved it. Nothing in this package
closed that gap, and `nethogs` / `bandwhich` / `iftop` were three unused
read-only commands on the image the whole time.

**Two measurements, and they are why this is not a `nethogs` wrapper.**

**1. Interface rates need no binary and no privilege.** `/proc/net/dev` read
twice, a second apart, gives bytes per interface. Measured on a real `@blue`
slot with live traffic: `wlp0s20f3: rx=14822B tx=993686B over 2s`. That half
works everywhere, degrades nowhere, and is the floor this skill falls back to.

**2. `nethogs` reports rates but cannot name the process - on a slot.**
Measured, repeatedly, `nethogs -t -c 3` returned six lines of
`unknown TCP/0/0<TAB>0<TAB>0` while `curl` was downloading 100 MB. The reason
is the machine, not the tool: **the nspawn slot has 3 processes**, and
`nethogs` maps a socket to a program by reading `/proc/<pid>/fd`, so with no
socket-owning process visible there is nothing to name. On this dev box the
same mapping attributes **14 of 29** sockets with no privilege at all - because
that machine has processes to attribute.

So the design keeps the two halves apart and never lets one stand in for the
other:

- **rates** come from `nethogs` when it can see connections, and from
  `/proc/net/dev` sampling when it cannot - and *which one answered* is stated,
  because "0 KB/s from this process" and "I could not attribute anything" are
  different claims;
- **names** come from `port_owner`'s own inode-to-pid walk, which is the same
  `/proc` matching and is reused rather than reimplemented (its docstring
  already records why `ss -p` and `lsof` cannot do this unprivileged).

**`nethogs` has no per-process mode here, so its output is joined by
connection, not by pid.** A row's third field is nethogs' own pid column
(`/0/0` throughout the measurements above, and `unknown TCP/0/0` when it has
nothing), so the join is on the local/remote pair, and a row that cannot be
joined says so rather than borrowing a name from a different row - which is
what makes a confident-looking answer possible here.
"""

from __future__ import annotations

import re
import shutil
import signal
import subprocess
import time
from typing import Dict, List, Tuple

from shani_chronoa.skills import Skill
from shani_chronoa.skills import port_owner

_TIMEOUT = 30

#: How long `nethogs` samples. It refreshes on a fixed cadence, so a shorter
#: window can complete before it has a second reading and report everything as
#: zero - which is not "nothing is happening".
_NETHOGS_REFRESHES = 3

#: `nethogs -t`'s data line: `local-remote/proto/pid<TAB>sent<TAB>received`,
#: where the two numbers are **kilobytes**. Verified against real output with
#: `cat -A`, because the separator is a literal tab and three columns is easy to
#: assume and easy to get wrong:
#:
#:     192.168.31.202:40782-64.239.109.193:443/0/0<TAB>98.5879<TAB>1.31367
#:
#: **The pair is split on its LAST `-`, not its first, and that is measured
#: rather than reasoned.** Both halves are addresses, and an IPv6 address is
#: full of colons *and* the local half ends in `:port` - so
#: `2409:...:aa46:33018-2607:6bc0::10:443` has exactly one `-`, but a hostname
#: or any address form carrying one would break a first-dash split. What is
#: certain is that the *trailing* `/proto/pid` is a fixed shape, so the pair is
#: taken as everything before it and then split at its last `-`: nethogs prints
#: `local-remote`, and no address legitimately contains a dash.
#:
#: A first draft anchored the numeric columns with `\t...\t` and then asked for
#: `\S+?` fields on the left; that matched **nothing at all**, on any input, and
#: three of the tests still passed because they asserted on the surrounding
#: behaviour. A parser that never matches looks exactly like a quiet machine.
_NETHOGS_ROW = re.compile(
    r"^(?P<pair>\S+)/(?P<proto>[^/]+)/(?P<pid>\d+)"
    r"\t(?P<sent>[\d.]+)\t(?P<received>[\d.]+)\s*$")


def _split_pair(pair: str) -> Tuple[str, str]:
    """`local-remote` into its two halves, at the last dash."""
    local, sep, remote = pair.rpartition("-")
    if not sep:
        return pair, ""
    return local, remote

#: What nethogs prints for a row it cannot attribute. Measured: this is the
#: entire output on a machine with no visible socket owner, six lines of it.
_NETHOGS_UNKNOWN = "unknown TCP/0/0"


def _human_kb(value: float) -> str:
    """Kilobytes as something readable, at the scale that matters here."""
    if value >= 1024 * 1024:
        return f"{value / 1024 / 1024:.1f} GB"
    if value >= 1024:
        return f"{value / 1024:.1f} MB"
    if value >= 1:
        return f"{value:.0f} KB"
    return f"{value:.1f} KB"


def _interface_rates(seconds: float = 2.0) -> Tuple[Dict[str, Tuple[int, int]], str]:
    """Bytes received and sent per interface, measured over `seconds`.

    Read from `/proc/net/dev` rather than from `ip` or `ifconfig` for the same
    reason `port_owner` reads `/proc/net/tcp`: it is the kernel's own published
    data, needs no binary a minimal image might lack, and cannot refuse.

    The counter is 64-bit and wraps; the difference is taken as an unsigned
    subtraction and a negative result (a wrap, or an interface that appeared
    mid-window) is reported as unknown rather than as a huge negative rate.
    """
    def read() -> Dict[str, Tuple[int, int]]:
        out: Dict[str, Tuple[int, int]] = {}
        try:
            with open("/proc/net/dev", encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except OSError:
            raise
        for line in lines[2:]:
            name, _, rest = line.partition(":")
            if not rest:
                continue
            fields = rest.split()
            if len(fields) < 9:
                continue
            try:
                out[name.strip()] = (int(fields[0]), int(fields[8]))
            except ValueError:
                continue
        return out

    try:
        first = read()
        time.sleep(seconds)
        second = read()
    except OSError as exc:
        return {}, f"/proc/net/dev could not be read ({exc})."

    rates: Dict[str, Tuple[int, int]] = {}
    for name, (rx1, tx1) in second.items():
        if name not in first:
            continue
        rx0, tx0 = first[name]
        # `lo` doubles every packet and is not network traffic; leaving it in
        # makes a quiet machine look busy.
        if name == "lo":
            continue
        rx, tx = rx1 - rx0, tx1 - tx0
        if rx < 0 or tx < 0:
            continue
        if rx or tx:
            rates[name] = (rx, tx)
    return rates, ""


def _owners_by_pair() -> Tuple[Dict[str, port_owner.Owner], List[str]]:
    """Map `"local:port-remote:port"` to the process that owns it.

    Reuses `port_owner._read_all()` and `_attributes()` rather than walking
    `/proc` a second time: that module already does the inode-to-pid matching,
    already documents why `ss -p` cannot do it unprivileged, and already
    reports what it could not attribute. Two independent walks of the same
    tables would be two answers to one question.
    """
    sockets, _problems = port_owner._read_all()
    owners = port_owner._attributes(sockets)
    by_pair: Dict[str, port_owner.Owner] = {}
    for row in sockets:
        owner = owners.get(row.inode)
        if owner is None:
            continue
        # nethogs prints `local-remote`; port_owner carries the fields already
        # decoded, so the key is rebuilt in nethogs' spelling rather than by
        # re-parsing its text.
        #
        # **`Socket` is a flat NamedTuple** - `local`, `port`, `peer`,
        # `peer_port` - and not nested pairs. A first version indexed
        # `row.local[1]` and `row.remote[1]`, which raised `AttributeError` on
        # **every call**: the four tests that exercise the join had all stubbed
        # `_owners_by_pair` out, so none of them ever ran the code. The key is
        # now built from the real field names and asserted against nethogs'
        # own spelling by a test that does not stub anything.
        local = f"{row.local}:{row.port}" if row.local else ""
        remote = f"{row.peer}:{row.peer_port}" if row.peer else ""
        if local and remote:
            by_pair[f"{local}-{remote}"] = owner
    return by_pair, []


def _nethogs() -> Tuple[List[Tuple[str, str, float, float]], str]:
    """`(pair, remote, sent_kb, received_kb)` rows, or `([], why)`.

    Three things are measured rather than assumed here, and all three are
    reasons a naive call gives a confident wrong answer:

    - **A tool killed by a signal is not a tool that found nothing.** The
      negative return code is checked first and reported as a crash by name,
      because `nethogs` links against libpcap and a machine without the right
      capture permissions has crashed rather than refused before.
    - **Rows of `unknown TCP/0/0` are a statement about attribution, not about
      traffic.** On the slot every row is one, while traffic is genuinely
      flowing; returning them as zero-rate connections would report a busy
      machine as idle.
    - **Zero rows with rc=0 is not "nothing is happening"** - it is nethogs
      having nothing to attribute, which on this image is every time.
    """
    if shutil.which("nethogs") is None:
        return [], ("nethogs is not installed, so per-program rates could not "
                    "be read. It comes with the nethogs package.")
    argv = ["nethogs", "-t", "-c", str(_NETHOGS_REFRESHES)]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=_TIMEOUT + _NETHOGS_REFRESHES * 10,
                              check=False)
    except subprocess.TimeoutExpired:
        return [], "nethogs did not finish inside its sampling window."
    except OSError as exc:
        return [], f"nethogs could not be run ({exc})."
    if proc.returncode < 0:
        return [], (f"nethogs CRASHED (killed by signal "
                    f"{signal.Signals(-proc.returncode).name}), so nothing it "
                    f"would have said about this machine is not known.")
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        return [], (detail[-1] if detail
                    else f"nethogs exited {proc.returncode} without saying why")

    rows: List[Tuple[str, str, float, float]] = []
    unknown = 0
    for line in (proc.stdout or "").splitlines():
        if line.strip() == _NETHOGS_UNKNOWN or line.startswith("unknown "):
            unknown += 1
            continue
        match = _NETHOGS_ROW.match(line.strip())
        if not match:
            continue
        try:
            sent = float(match.group("sent"))
            received = float(match.group("received"))
        except ValueError:
            continue
        local, remote = _split_pair(match.group("pair"))
        rows.append((match.group("pair"), remote or match.group("pair"),
                     sent, received))
    if not rows:
        if unknown:
            return [], (f"nethogs ran and attributed none of its {unknown} "
                        f"connections - every row was `{_NETHOGS_UNKNOWN}`. "
                        f"That is about this machine's processes, not its "
                        f"traffic: nethogs names a program by reading "
                        f"/proc/<pid>/fd, so a machine where the socket's "
                        f"owner is not visible to it answers like this even "
                        f"while bandwidth is being used.")
        return [], "nethogs ran and reported no connections at all."
    return rows, ""


def _describe() -> str:
    lines: List[str] = []
    rates, why = _interface_rates()
    lines.append("Network right now (measured from /proc/net/dev over 2s):")
    if rates:
        for name, (rx, tx) in sorted(rates.items()):
            lines.append(f"  {name}: down {_human_kb(rx / 1024)}  "
                         f"up {_human_kb(tx / 1024)}")
    else:
        lines.append(f"  no interface moved any bytes - {why or 'this machine is idle'}")
    lines.append("")

    rows, why = _nethogs()
    if not rows:
        lines.append(f"Per-program rates: not known - {why}")
        lines.append("")
        lines.append("The interface numbers above are still real; what is "
                     "missing is which program moved them.")
        return "\n".join(lines)

    by_pair, _ = _owners_by_pair()
    lines.append("Per-program (from nethogs):")
    for pair, remote, sent, received in sorted(rows,
                                               key=lambda r: -(r[2] + r[3]))[:12]:
        owner = by_pair.get(pair)
        if owner is not None:
            who = port_owner._short_command(owner.command) or owner.name
            name = f"{owner.name} ({who})" if owner.name != who else who
        else:
            # **Not "unknown program" dressed as an answer.** The socket is
            # real and the rate is measured; what is missing is the name, and
            # a row claiming a program here would be a guess about who is
            # spending the user's bandwidth.
            name = f"unattributed socket to {remote}"
        lines.append(f"  {name}: up {_human_kb(sent)}  "
                     f"down {_human_kb(received)}")
    if any(pair not in by_pair for pair, *_ in rows):
        lines.append("")
        lines.append("Some sockets could not be attributed to a program. "
                     "Their rates are measured; the owner is not known - "
                     "a program outside your own, or one that has since "
                     "exited.")
    return "\n".join(lines)


def _run(arguments: dict) -> str:
    return _describe()


SCHEMA = {
    "type": "function",
    "function": {
        "name": "network_activity",
        "description": (
            "Report what this machine's network is doing right now, per "
            "program: which processes are sending and receiving, and how fast "
            "each interface is moving. Use for 'what is using my bandwidth', "
            "'why is the network slow', 'what is uploading', 'is something "
            "downloading right now'. Interface rates come from /proc/net/dev "
            "and always work; per-program rates need nethogs and can only "
            "name programs this user owns. Says which half it could not "
            "measure and why, and reports an unattributed socket as "
            "unattributed rather than guessing at a program. Read-only; "
            "nothing is changed or blocked."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

SKILLS = [Skill(name="network_activity", schema=SCHEMA, run=_run)]