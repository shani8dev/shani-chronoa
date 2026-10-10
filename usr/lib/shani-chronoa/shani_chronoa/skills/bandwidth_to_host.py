"""Skill: how fast is this machine to *that* machine?

`speed_test` measures this machine to *the internet*. That is a different
question from the one people usually have on a home or office network: "my
laptop is slow when I copy to the NAS", "is this switch the bottleneck",
"can this WiFi carry a video call".

`iperf3` measures exactly that - a controlled transfer between two machines
over a socket you choose - and `shani-tools-network` ships it on both images.

**The server half matters and is the honest constraint.** iperf3 needs a
listener on the *other* machine; there is no way to measure a peer that is not
listening, and this skill does not pretend otherwise. What it *can* do from
one machine is:
- measure **loopback**, which is a real measurement and answers "is this
  machine's own copy/scrub path the bottleneck", and
- report the exact `iperf3 -s` command the other machine needs.

**Measured on a real `@blue` slot (iperf 3.21), the row verbatim:**

    [  5]   0.00-2.00   sec  9.53 GBytes  40.9 Gbits/sec    0            sender
    [  5]   0.00-2.00   sec  9.53 GBytes  40.9 Gbits/sec                  receiver

**The `receiver` line is the one to quote.** `sender` measures the sending
host's own view, `receiver` the receiving host's - and on any real network
the two differ, with the receiver's number being the one that reflects what
arrived. A parser that takes the first match reports the sender's figure as
the result, which is the more flattering of the two.

**And the refusal shape, measured on the same slot:**

    iperf3: error - unable to connect to server - server may have stopped     rc=1

which is a different answer from "the connection was slow", and is the one a
person hits the first time.
"""

from __future__ import annotations

import re
import shutil
import subprocess

from shani_chronoa import files
from shani_chronoa.skills import Skill

_TIMEOUT = 120

#: `[  5]   0.00-2.00   sec  9.53 GBytes  40.9 Gbits/sec    0            sender`
#: The interval carries a start-end pair, the transfer a size with a unit, and
#: the rate with its own. `sender`/`receiver` is the last token and it is what
#: selects the line, because the two disagree on any real network.
_ROW = re.compile(
    r"\[\s*\d+\]\s+([\d.]+)-([\d.]+)\s+sec\s+([\d.]+)\s+(\w+Bytes)\s+"
    r"([\d.]+)\s+(\w+bits/sec)\s+(?:\S+\s+)?(sender|receiver)\s*$")
# Group 6 is the rate, group 7 the rate's unit... and the role is group 8 in
# this pattern, which does not exist - a real `IndexError` on every successful
# measurement, caught by running the parser against the captured row rather
# than by reading it. Corrected below to the group that holds the role.

#: A whole transfer's summary, when `-t` was long enough for one to print.
_SUMMARY = re.compile(
    r"([\d.]+)\s+([\d.]+)-([\d.]+)\s+sec\s+([\d.]+)\s+(\w+Bytes)\s+"
    r"([\d.]+)\s+(\w+bits/sec)")


def _human_rate(value: str, unit: str) -> str:
    """iperf3's own number, in the unit it chose.

    **The unit is read from the text, never inferred from the number.** The
    first version compared the *value* to 1000 to choose between Gbit/s and
    Mbit/s, so iperf3's own `40.9 Gbits/sec` came back as `40.9 Mbit/s` -
    the same digits, a thousand times wrong, and it reads as a merely slow
    network rather than as a units bug.
    """
    try:
        number = float(value)
    except ValueError:
        return f"{value} {unit}"
    prefix = unit[:1].upper()
    if prefix == "G":
        return f"{number:g} Gbit/s"
    if prefix == "M":
        return f"{number:g} Mbit/s"
    if prefix == "K":
        return f"{number:g} Kbit/s"
    return f"{number:g} {unit}"


def _run(command: "list[str]") -> "tuple[str, str, int]":
    try:
        proc = subprocess.run(command, capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return "", f"iperf3 did not finish within {_TIMEOUT}s", 124
    except OSError as exc:
        return "", str(exc), 127
    return proc.stdout or "", proc.stderr or "", proc.returncode


def _measure(host: str, port: int, seconds: int) -> "tuple[str, str]":
    """(answer, reason). Empty reason means the transfer really ran."""
    out, err, rc = _run(["iperf3", "-c", host, "-p", str(port),
                         "-t", str(seconds)])
    if rc == 124:
        return "", f"the transfer did not finish within {_TIMEOUT}s"
    if rc != 0:
        # Measured wording: "iperf3: error - unable to connect to server -
        # server may have stopped", rc=1. That is a *different answer* from a
        # slow link, and conflating them would send someone looking at their
        # network when nothing is listening on the far end.
        detail = [line for line in (err or out).splitlines() if line.strip()]
        return "", (detail[-1].strip() if detail else f"iperf3 exited {rc}")

    receiver = None
    sender = None
    for line in out.splitlines():
        match = _ROW.match(line.rstrip())
        if not match:
            continue
        # **Groups, not eyeballs: 5 is the rate, 6 its unit, 7 the role.**
        # The first version passed (6, 7) - the unit and the role - so every
        # successful measurement printed "Gbits/sec receiver" as its speed.
        # Caught by running the captured row through it, not by reading it.
        row = {"rate": _human_rate(match.group(5), match.group(6))}
        if match.group(7) == "receiver":
            receiver = row
        else:
            sender = row
    summary = _SUMMARY.search(out)

    lines = []
    if summary:
        lines.append(f"Sent {summary.group(4)} {summary.group(5)} in "
                     f"{summary.group(2)}-{summary.group(3)}s.")
    if receiver:
        lines.append(f"The far end received it at **{receiver['rate']}**.")
    if sender:
        # Reported beside the receiver's figure, never instead of it.
        lines.append(f"This machine reported sending at {sender['rate']} - the "
                     "difference between the two is what the network cost.")
    if not receiver and not sender and not summary:
        return "", "iperf3 finished but printed no transfer line this reads"
    return "\n".join(lines), ""


def _run_skill(arguments: dict) -> str:
    if shutil.which("iperf3") is None:
        return files.tool_missing("iperf3", "measure how fast this machine talks to another")

    raw_host = str(arguments.get("host") or "").strip()
    host = raw_host or "127.0.0.1"
    try:
        port = int(str(arguments.get("port") or 5201))
    except ValueError:
        return f"{arguments.get('port')!r} is not a port number."
    try:
        seconds = int(str(arguments.get("seconds") or 5))
    except ValueError:
        return f"{arguments.get('seconds')!r} is not a number of seconds."
    if not 1 <= seconds <= 60:
        return f"{seconds}s is outside the 1-60s range this allows."

    loopback = host in ("127.0.0.1", "localhost", "::1")

    answer, problem = _measure(host, port, seconds)
    if problem:
        if "unable to connect" in problem or "connection refused" in problem:
            if loopback:
                return (f"Nothing is listening on {host}:{port} here. iperf3 "
                        "needs a server on the far end, so run `iperf3 -s` on "
                        f"that machine (port {port}) and ask me again.")
            return (f"Nothing is listening on {host}:{port}. iperf3 measures "
                    f"between two machines, so run `iperf3 -s -p {port}` on "
                    f"{host} and ask me again.\n\n{problem}")
        return f"The measurement did not run: {problem}"

    where = "this machine's own loopback" if loopback else f"{host}:{port}"
    return f"Measured {where} over {seconds}s.\n{answer}"


SCHEMA = {
    "type": "function",
    "function": {
        "name": "bandwidth_to_host",
        "description": (
            "Measure how fast this machine can send data to another machine "
            "over the network, using a controlled iperf3 transfer. Use for "
            "'why is my NAS slow', 'how fast is my network to the server', 'is "
            "the bottleneck the network or the disk'. Needs iperf3 running on "
            "the other machine - ask for loopback to measure this machine "
            "against itself. Read-only: it transfers data and writes nothing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "host": {
                    "type": "string",
                    "description": ("Hostname or IP of the machine to measure "
                                    "against. Defaults to 127.0.0.1, which "
                                    "measures this machine's loopback."),
                },
                "port": {"type": "integer",
                         "description": "iperf3 port on that machine. Default 5201."},
                "seconds": {"type": "integer",
                            "description": "How long to measure, 1-60. Default 5."},
            },
        },
    },
}

SKILLS = [Skill(name="bandwidth_to_host", schema=SCHEMA, run=_run_skill)]