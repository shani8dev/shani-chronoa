"""Skill: what is waiting to print, and cancel a stuck job.

`print_file` sends a job; nothing could see what happened to it afterwards. A
job stuck at the head of the queue blocks everything behind it, and "cancel
that print" is the fix people ask for. CUPS' own `lpstat` and `cancel` are on
every image that can print.

Cancelling is gated by `print-control-enabled`: a cancelled job cannot be
resumed, and on a shared printer it may not be the user's own. Listing needs no
permission.

Honesty rules: a job is cancelled only if it is in the queue *now* (a stale id
is refused rather than passed on); the queue is read again afterwards and a job
still listed is reported as not cancelled; no CUPS scheduler is reported as
"the queue could not be read", never as an empty queue.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import List

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "print-control-enabled"
_TIMEOUT = 15
_JOB_ID = re.compile(r"^[A-Za-z0-9_.@-]+-\d+$|^\d+$")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "print_queue",
        "description": (
            "Show the printers, which one is the default, the print jobs "
            "still waiting, and what this machine can print to even when "
            "nothing is configured; or cancel a waiting job by its id. Use for "
            "'what's printing', 'why isn't my printer there', 'what can I "
            "print to', 'cancel that print'. Cancelling requires the "
            "'print-control-enabled' consent key; listing does not."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "cancel", "defaults", "hold", "release"],
                           "description": ("list (default), cancel, defaults (show or set this "
                          "account's default options for a queue), hold, or "
                          "release.")},
                "job": {"type": "string", "description": "cancel: the job id, e.g. 'Office_Laser-42'."},
                "printer": {"type": "string",
                            "description": "defaults/hold/release: the queue, e.g. 'Office_Laser'."},
                "option": {"type": "string",
                           "description": ("defaults with no value shows the current ones; with a "
                                           "value it sets one, e.g. 'sides=two-sided-long-edge', "
                                           "'media=A4', 'ColorModel=RGB'.")},
            },
        },
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    if not config.get_bool(_CONSENT_KEY, False):
        # **Named for the switch, not for cancelling.** One key now guards five
        # actions, and a refusal that said "cancelling print jobs is turned off"
        # while refusing to *hold a printer* contradicts itself. "A cancelled job
        # cannot be resumed" stays: it is why cancelling is not a trivial
        # reversal, and it is still true of cancelling.
        return False, (f"print control is turned off (enable '{_CONSENT_KEY}' in "
                       f"Settings). A cancelled job cannot be resumed.")
    return True, ""


def _lpstat(*args: str) -> "str | None":
    try:
        proc = subprocess.run(["lpstat", *args], capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def queued_jobs() -> "list[dict] | None":
    out = _lpstat("-o")
    if out is None:
        return None
    jobs = []
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) >= 3:
            jobs.append({"id": parts[0], "user": parts[1], "bytes": parts[2],
                         "when": parts[3].strip() if len(parts) > 3 else ""})
    return jobs


def _matches(job: dict, wanted: str) -> bool:
    return job["id"] == wanted or (wanted.isdigit() and job["id"].rsplit("-", 1)[-1] == wanted)


def _available_devices() -> "list[str]":
    """What this machine can print to, from `lpinfo -v`.

    **Why this is a different question from the one `_list` answers.**
    `lpstat -a` lists the *configured* queues, so a printer the machine can
    reach but nobody has added yet is invisible — and "my printer isn't
    there" is the usual symptom. `lpinfo -v` asks CUPS what it can *reach*:
    every backend it has loaded, and any network printer it has discovered.

    Measured here, and the distinction it forces is real:

        network beh
        network lpd
        network ipp
        network https
        direct hp
        network http

    **Those are transports, not printers** — `network ipp` says CUPS *can*
    speak IPP, not that a printer is waiting. Saying "you can print to
    network ipp" would be the confident wrong answer this module refuses
    elsewhere, so the backends are reported as what they are, and a
    discovered printer (a real `network ipp://host/queue` line) is called out
    separately as one.
    """
    if shutil.which("lpinfo") is None:
        return ["Print destinations are UNKNOWN: lpinfo (the cups package) is "
                "not installed, so what this machine can print to cannot be listed."]
    try:
        proc = subprocess.run(["lpinfo", "-v"], capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return [f"Print destinations are UNKNOWN: lpinfo did not answer ({exc})."]
    if proc.returncode != 0:
        # **stderr first, stdout as the fallback** - the same order
        # `boot_report` uses, because a tool that writes its refusal to
        # stdout exists and a message nobody can read is no message at all.
        detail = ((proc.stderr or "") or (proc.stdout or "")).strip().splitlines()
        return ["Print destinations are UNKNOWN: lpinfo said "
                f"{(detail[-1] if detail else f'exit {proc.returncode}')!r}."]

    backends: "list[str]" = []
    discovered: "list[str]" = []
    for line in (proc.stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        kind, rest = parts[0], parts[1]
        if kind == "network" and "://" in rest:
            # A real destination, not just a transport CUPS can speak.
            discovered.append(rest)
        elif kind not in backends:
            backends.append(kind)
    if not backends:
        return ["CUPS reported no printing backends at all, so what this "
                "machine can print to is UNKNOWN."]
    out = [f"Printing backends CUPS has loaded ({len(backends)}): "
           + ", ".join(sorted(backends))
           + ". Those are transports, not printers - they say what CUPS can "
             "speak, not that anything is waiting on the other end."]
    if discovered:
        out.append(f"Discovered on the network right now ({len(discovered)}): "
                   + ", ".join(discovered[:8])
                   + ("..." if len(discovered) > 8 else ""))
    else:
        out.append("Nothing was discovered on the network, so a network printer "
                   "would have to be added by address.")
    return out


def _list() -> str:
    jobs = queued_jobs()
    if jobs is None:
        return "The print queue could not be read (is CUPS running?), so it is UNKNOWN, not empty."
    lines = []
    default = _lpstat("-d")
    if default:
        lines.append(default.strip())
    printers = _lpstat("-p")
    if printers:
        lines.extend(l.strip() for l in printers.splitlines() if l.startswith("printer"))
    if not jobs:
        lines.append("No print jobs are waiting.")
    else:
        lines.append(f"{len(jobs)} job(s) waiting:")
        lines.extend(f"  {j['id']:<24} {j['user']:<12} {j['when']}" for j in jobs)
    # **What the machine can print to, beside what is configured.** The queue
    # answers "what is waiting"; a printer that exists and has no queue yet is
    # the other half of the same question, and it used to have no answer.
    lines.append("")
    lines.extend(_available_devices())
    return "\n".join(lines)


def _run_cups(argv):
    """Run a CUPS command, or None when it could not be run at all."""
    try:
        return subprocess.run(argv, capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None


#: A CUPS option is `name=value`, no spaces. Anchored because it is handed to a
#: subprocess as one argv element - `lpoptions -o` takes exactly that, so a
#: string with a space in it is not a second option, it is a mistake.
_OPTION = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*=[^\s]+$")


def _printer_names() -> List[str]:
    """Every queue CUPS knows about, or [] when it knows of none."""
    proc = _run_cups(["lpstat", "-a"])
    if proc is None or proc.returncode != 0:
        return []
    names = []
    for line in proc.stdout.splitlines():
        name = line.split()[0] if line.split() else ""
        if name and name != "printer":
            names.append(name)
    return names


def _defaults(arguments: dict) -> str:
    """Show or set this user's default options for a queue - duplex, paper, colour.

    **`lpoptions` was unused and this is what it is for.** CUPS' `lpstat` and
    `cancel` answer "what is printing" and "stop that job"; neither can change
    a setting, so "print double-sided by default" and "print A4" had no answer at
    all on a machine where `lpoptions` is sitting in `/usr/bin`.

    **An empty `lpoptions` is not "no options are set".** Measured here with no
    printer configured: it prints nothing and **exits 0**. So the printers are
    listed first and the emptiness is reported as what it is, because "your
    defaults are empty" reads as a fact about a printer that does not exist.
    """
    printers = _printer_names()
    if not printers:
        return ("No printers are configured on this machine, so there are no "
                "default print options to show or set. Nothing was changed.")
    wanted = (arguments.get("printer") or "").strip()
    if wanted and wanted not in printers:
        return (f"There is no printer called {wanted!r}. The printers here are "
                f"{', '.join(printers)}. Nothing was changed.")
    target = wanted or printers[0]

    option = (arguments.get("option") or "").strip()
    if not option:
        proc = _run_cups(["lpoptions", "-p", target])
        if proc is None:
            return "Could not read the default options; nothing was changed."
        body = proc.stdout.strip()
        if proc.returncode != 0:
            return (f"CUPS refused to read the defaults for {target}: "
                    f"{(proc.stderr or '').strip() or 'no detail'}")
        if not body:
            return (f"No default options are set for {target}, so every job "
                    f"uses the printer's own settings. Set one with an option "
                    f"like 'sides=two-sided-long-edge', 'media=A4' or "
                    f"'ColorModel=RGB'.")
        return f"Default options for {target}:\n{body}"

    if not _OPTION.match(option):
        return (f"{option!r} is not a print option. They are name=value pairs, "
                f"e.g. 'sides=two-sided-long-edge', 'media=A4', "
                f"'ColorModel=RGB'. Nothing was changed.")
    if shutil.which("lpoptions") is None:
        return files.tool_missing("lpoptions", "set default print options")

    setproc = _run_cups(["lpoptions", "-p", target, "-o", option])
    if setproc is None:
        return "Could not set the option; nothing is known to have changed."
    # **`cupsreject`/`lpoptions` are not judged by their exit code.** Measured:
    # `cupsreject Office` on a queue that does not exist prints
    # `client-error-not-found` and **exits 0**. So a check that read the return
    # code would report success on a printer that was never there.
    after = _run_cups(["lpoptions", "-p", target])
    if setproc.returncode != 0 and setproc.stderr.strip():
        return (f"CUPS refused to set {option} on {target}: "
                f"{setproc.stderr.strip()}")
    if after is not None and after.returncode == 0 and after.stdout.strip() \
            and option.split("=", 1)[0] in after.stdout:
        return (f"Set {option} on {target} for your account (verified: it is "
                f"listed in the defaults).")
    if setproc.stderr.strip() and "not-found" in setproc.stderr:
        return (f"CUPS does not know a printer called {target}: "
                f"{setproc.stderr.strip()}")
    return (f"CUPS accepted {option} for {target} but it is not listed in the "
            f"defaults afterwards, so this is not verified.")


def _hold(arguments: dict, binary: str, verb: str) -> str:
    """`cupsreject` (hold) or `cupsaccept` (release) a queue.

    **Both live in `/usr/sbin`, not `/usr/bin`** - measured - so on a machine
    where Chronoa does not run as root they will be refused, and the refusal is
    reported as what it is rather than as "the printer is gone".
    """
    printers = _printer_names()
    wanted = (arguments.get("printer") or "").strip()
    if not wanted and len(printers) == 1:
        wanted = printers[0]
    if not wanted:
        return ("Name the printer. " + (f"The printers here are "
                f"{', '.join(printers)}." if printers
                else "No printers are configured."))
    if printers and wanted not in printers:
        return (f"There is no printer called {wanted!r}. The printers here are "
                f"{', '.join(printers)}. Nothing was changed.")
    if shutil.which(binary) is None:
        return files.tool_missing(binary, verb + " a printer")
    proc = _run_cups([binary, wanted])
    if proc is None:
        return f"Could not {verb} {wanted}; nothing is known to have changed."
    # Exit code 0 with a `not-found` on stderr is a refusal - measured above.
    err = (proc.stderr or "").strip()
    if err and ("not-found" in err or "Error" in err or "failed" in err):
        return f"CUPS refused to {verb} {wanted}: {err}"
    if proc.returncode != 0 and err:
        return f"CUPS refused to {verb} {wanted}: {err}"
    return (f"{verb.capitalize()}d {wanted}. Anything already queued will wait; "
            f"nothing new will start until it is released."
            if binary == "cupsreject" else
            f"Released {wanted}. Anything queued will start printing again.")


def _run(arguments: dict) -> str:
    if shutil.which("lpstat") is None:
        return files.tool_missing("lpstat", "read the print queue")
    action = (arguments.get("action") or "list").strip().lower()
    if action == "list":
        return _list()
    if action == "defaults":
        # Reading the defaults is a read of the machine's configuration; only
        # setting one writes anything, so only that needs consent.
        if (arguments.get("option") or "").strip():
            allowed, reason = _consent(ChronoaConfig())
            if not allowed:
                return f"Refusing to set default print options: {reason}"
        return _defaults(arguments)
    if action in ("hold", "release"):
        allowed, reason = _consent(ChronoaConfig())
        if not allowed:
            return f"Refusing to {action} a printer: {reason}"
        hold = action == "hold"
        return _hold(arguments, "cupsreject" if hold else "cupsaccept", action)
    if action != "cancel":
        return (f"Action must be one of list, cancel, defaults, hold or release, "
                f"not {action!r}.")
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to cancel: {reason}"
    wanted = (arguments.get("job") or "").strip()
    if not _JOB_ID.match(wanted):
        return f"{wanted!r} is not a print job id; list the queue to see the ids."
    jobs = queued_jobs()
    if jobs is None:
        return "The print queue could not be read, so nothing was cancelled."
    job = next((j for j in jobs if _matches(j, wanted)), None)
    if job is None:
        return f"No waiting job has the id {wanted!r} (it may already have printed), so nothing was cancelled."
    if shutil.which("cancel") is None:
        return files.tool_missing("cancel", "cancel a print job")
    try:
        proc = subprocess.run(["cancel", job["id"]], capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"cancel did not finish ({exc}); the job may still be queued."
    if proc.returncode != 0:
        return f"CUPS refused to cancel {job['id']}: {(proc.stderr or '').strip() or 'no detail'}."
    after = queued_jobs()
    if after is not None and not any(j["id"] == job["id"] for j in after):
        return f"Cancelled {job['id']} (verified: it is no longer in the queue)."
    return f"cancel accepted {job['id']}, but it is still listed in the queue, so this is not verified."


def _post_condition(arguments: dict):
    if (arguments.get("action") or "list").strip().lower() != "cancel":
        return None
    wanted = (arguments.get("job") or "").strip()
    jobs = queued_jobs()
    if not wanted or jobs is None:
        return None
    still = [j["id"] for j in jobs if _matches(j, wanted)]
    return not still, "still queued" if still else "no longer queued"


POST_CONDITION = _post_condition

SKILLS = [Skill(name="print_queue", schema=SCHEMA, run=_run)]
