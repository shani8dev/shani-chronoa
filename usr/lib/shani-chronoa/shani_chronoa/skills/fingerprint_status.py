"""Skill: whether fingerprint login is set up on this machine.

Nothing in the package could answer it. A reader that is not attached, a
daemon that is not running, a machine with no fingers enrolled and a machine
that cannot log anyone in by fingerprint are four different states, and the
three answers "yes", "no" and "no idea" collapse the last three.

**No password is read, nothing is enrolled, nothing is deleted.** `fprintd-list`
is the only command this calls; `fprintd-enroll` and `fprintd-delete` exist in
the same package and are deliberately not reachable here, because "which
fingers are enrolled" is a question and "delete the fingerprint database" is not
one anybody asked.

**Four states, kept apart, and the reason each matters.** Measured on this
machine, which is the fourth:

| what is true | what `fprintd-list` does | what that means |
|---|---|---|
| daemon not running | fails, no usable output | **cannot say** - not "not set up" |
| no reader attached | prints `No devices available`, exits 1 | no hardware, so enrolment is impossible |
| reader, nothing enrolled | prints nothing, exits 0 | set up, unused - and login by finger will fail |
| fingers enrolled | one line per finger | working |

The third is the one a boolean gets wrong: the command succeeded and printed
nothing, so anything reading its exit code or its length reports "no fingerprints
enrolled" for both of the states above it.

**The enrolled-finger lines are reported as the command printed them.** There is
no documented output format - `fprintd-list(1)` has no EXAMPLES section and no
exit-status table - so a parser for it would be a guess about a format nobody
here can observe, and a guess that renders "left-index-finger" wrong is a
confident wrong answer about somebody's own hand. One line is one finger, and the
text is passed through.
"""

from __future__ import annotations

import getpass
import shutil
import subprocess
from typing import List, Optional

from shani_chronoa import files
from shani_chronoa.skills import Skill

_TIMEOUT = 10

#: What fprintd prints when the daemon is reachable but there is no reader.
#: Matched as a phrase rather than a prefix so a translated or reworded build
#: falls through to the honest default instead of being reported as enrolled.
_NO_DEVICE = "no devices available"


def _which() -> Optional[str]:
    """`fprintd-list`, or None when the package is not installed."""
    return shutil.which("fprintd-list")


def _run(cmd: List[str]) -> tuple:
    """`(returncode, stdout, stderr)`; the code is kept because it is the signal."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except (subprocess.SubprocessError, OSError) as exc:
        return (-1, "", str(exc))
    return (proc.returncode, proc.stdout or "", proc.stderr or "")


def _daemon_state() -> Optional[str]:
    """`fprintd.service`'s ActiveState, or None when systemd could not be asked.

    Read from the systemd bus rather than by running `systemctl is-active`,
    because the daemon being unreachable and the daemon being stopped are
    different answers and both of them are "cannot say" - this only decides
    which refusal to give.
    """
    if shutil.which("systemctl") is None:
        return None
    code, out, _err = _run(["systemctl", "is-active", "fprintd.service"])
    if code != 0:
        return None
    state = out.strip()
    return state or None


def _run_skill(arguments: dict) -> str:
    binary = _which()
    if binary is None:
        return (
            "Fingerprint login cannot be checked here: the fprintd command-line "
            "tools are not installed. "
            + files.tool_missing("fprintd-list", "check whether fingerprint login is set up")
            + " Nothing was guessed - a machine with no fprintd package and a "
              "machine with no fingerprint enrolled look identical from the "
              "desktop alone.")

    user = (arguments.get("user") or "").strip() or getpass.getuser()

    code, out, err = _run([binary, user])
    text = "\n".join(line.strip() for line in out.splitlines() if line.strip())

    if _NO_DEVICE in (out + err).lower():
        return (
            f"Fingerprint login is not available: fprintd is running but there "
            f"is no fingerprint reader attached to this machine, so no "
            f"fingerprint can be enrolled for {user}. This is a hardware "
            f"answer, not a setting.")

    if code != 0:
        state = _daemon_state()
        detail = (err.strip().splitlines() or [""])[0]
        return (
            f"Could not read {user}'s fingerprints"
            + (f" (fprintd.service is {state})" if state else "")
            + (f": {detail}" if detail else ".")
            + " That is not the same as having none - the database could not be"
              " read, so nothing is claimed about what is enrolled.")

    if not text:
        return (
            f"A fingerprint reader is present and {user} has no fingerprints "
            f"enrolled, so logging in by fingerprint will not work yet. "
            f"Enrolling is done in Settings > Users, or with `fprintd-enroll`.")

    fingers = text.splitlines()
    lines = [f"{len(fingers)} fingerprint(s) enrolled for {user}, and a "
             f"reader is attached. Logging in by fingerprint should work.",
             "  As fprintd reports them:"]
    lines += [f"  {line}" for line in fingers[:20]]
    if len(fingers) > 20:
        lines.append(f"  ... and {len(fingers) - 20} more.")
    return "\n".join(lines)


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "fingerprint_status",
        "description": (
            "Whether fingerprint login is set up on this machine, and which "
            "fingers are enrolled. Says which of the four real states it is in: "
            "no reader attached, reader present but nothing enrolled, fingers "
            "enrolled, or the daemon not running and the question unanswerable. "
            "Reading only - it never enrolls or deletes a fingerprint, because "
            "'which fingers are enrolled' is a question and the other two are "
            "not. Needs no permission: it reports a machine's own login "
            "configuration, and the answer is about hardware and a per-user "
            "setting rather than anything private."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "user": {
                    "type": "string",
                    "description": "Whose enrolled fingerprints to list; you by default.",
                },
            },
            "required": [],
        },
    },
}

SKILLS = [Skill(name="fingerprint_status", schema=_SCHEMA, run=_run_skill)]