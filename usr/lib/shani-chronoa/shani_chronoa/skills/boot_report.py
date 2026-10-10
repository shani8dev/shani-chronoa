"""Skill: why is booting slow, and did the machine shut down cleanly?

Two halves:

- the `boots` sense - when the machine last booted, how often, and whether the
  previous shutdown was clean - background context until now;
- `systemd-analyze time` and `systemd-analyze blame`, which nothing in Chronoa
  called: how long firmware, loader, kernel and userspace each took, and which
  services took longest to start.

`blame` is a list of start times, not of culprits: services start in parallel,
so a slow one is not necessarily what made the boot slow. That is said in the
reply, because "the slowest unit" reads like "the cause" and often is not.

The boot-history half follows the `boots` sense's switch (on by default) - see
`sense_reading.py`; the timing half needs none.

Honesty rules: `systemd-analyze time` refuses while the boot has not finished
("Bootup is not yet finished"), and that refusal is reported as itself.
"""

from __future__ import annotations

import shutil
import subprocess

from shani_chronoa import sense_reading
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_TIMEOUT = 20
_DEFAULT_TOP = 10
_MAX_TOP = 30

SCHEMA = {
    "type": "function",
    "function": {
        "name": "boot_report",
        "description": (
            "How the last boot went: total boot time split into firmware, "
            "loader, kernel and userspace, the services that took longest to "
            "start, when the machine booted and whether it shut down cleanly "
            "before, and which EFI entry the firmware will boot by default. "
            "Use for 'why does it boot slowly', 'did it crash last time'. "
            "Boot history uses the 'boots-sense-enabled' switch. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "top": {"type": "integer",
                        "description": f"How many of the slowest services to list (default {_DEFAULT_TOP})."},
            },
        },
    },
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """This skill's sense half follows the boots sense's switch (see sense_reading.py)."""
    if config.sense_allowed("boots"):
        return True, ""
    return False, sense_reading.refusal(config, "boots")


def _analyze(*args: str) -> "tuple[str | None, str]":
    try:
        proc = subprocess.run(["systemd-analyze", *args], capture_output=True,
                              text=True, timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return None, f"did not answer within {_TIMEOUT}s"
    except OSError as exc:
        return None, str(exc)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return None, detail[-1] if detail else f"exit {proc.returncode}"
    return proc.stdout, ""


def _top(arguments: dict) -> int:
    try:
        n = int(arguments.get("top") or _DEFAULT_TOP)
    except (TypeError, ValueError):
        n = _DEFAULT_TOP
    return max(1, min(n, _MAX_TOP))


def _boot_entries() -> "list[str]":
    """The EFI boot entries, and what the firmware will actually boot next.

    **Why this belongs here.** On a blue-green machine the question *"what will
    my machine boot into if I do nothing?"* has two answers and they can
    disagree: what `shani-deploy` intends (which `snapshot_status` reports) and
    what the firmware is pointed at. Nothing read the second one. `bootctl list`
    is the only place it exists.

    **Measured on a real slot** (`chronoa-cli-formats.sh`, `@blue`, 2026-10-10).
    This systemd's `bootctl` **has no JSON output** - `--json` is refused with
    *"Unknown argument to --json= switch"* - so the prose table is what is
    parsed. The whole captured list, because the first capture was truncated and
    completing it by hand is how a remembered format ships:

            type: Boot Loader Specification Type #1 (.conf)
           title: shanios-green (Candidate) (not reported/new)
              id: shanios-green.conf
           source: /boot/efi//loader/entries/shanios-green.conf (on the EFI System Partition)
              efi: /boot/efi//EFI/shanios/shanios-green.efi

            type: Boot Loader Specification Type #1 (.conf)
           title: shanios-blue (Active) (default) (not reported/new)
              id: shanios-blue.conf
           source: /boot/efi//loader/entries/shanios-blue+3-0.conf (on the EFI System Partition)
            tries: 3 left; 0 done
              efi: /boot/efi//EFI/shanios/shanios-blue.efi

    **Three different markers, and confusing two of them is the whole danger
    here.** `(Active)` is what is running now, `(default)` is what the firmware
    boots if nothing intervenes, and `(Candidate)` is the *other* side of an
    auto-tries pair - which on this machine is green while the default is blue.
    Reading `(Candidate)` as "what boots next" would have named the wrong
    slot on the very machine it was measured on. So they are read apart, and an
    unmarked entry set is reported as such rather than defaulting to the first
    row: the list is ordered by sort-key, not by preference.

    A machine with no EFI system partition - a container, an nspawn slot, a
    BIOS boot - is reported with bootctl's own words, never as "no entries".
    """
    if shutil.which("bootctl") is None:
        return ["EFI boot entries are UNKNOWN: bootctl (systemd) is not installed, "
                "so which entry the firmware will boot cannot be read."]
    try:
        proc = subprocess.run(["bootctl", "list"], capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return [f"EFI boot entries are UNKNOWN: bootctl did not answer ({exc})."]
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return ["EFI boot entries are UNKNOWN: bootctl said "
                f"{(detail[-1] if detail else f'exit {proc.returncode}')!r}."]

    # Blank-line-separated blocks of indented `key: value` pairs. The keys are
    # right-aligned, so a line's key is the first token and its value is the
    # rest - `title` holds spaces, and taking everything after the colon is
    # what keeps them.
    entries: "list[dict]" = []
    block: "dict" = {}
    for line in (proc.stdout or "").splitlines():
        if not line.strip():
            if block:
                entries.append(block)
                block = {}
            continue
        key, sep, value = line.strip().partition(":")
        if sep and key:
            block[key.strip()] = value.strip()
    if block:
        entries.append(block)
    if not entries:
        return ["EFI boot entries are UNKNOWN: bootctl listed none, which on a real "
                "machine means the firmware's own list could not be read rather "
                "than that no operating system is installed."]

    def marked(word: str) -> "dict | None":
        return next((e for e in entries if f"({word})" in e.get("title", "")), None)

    active, default = marked("Active"), marked("default")
    out = [f"EFI boot entries ({len(entries)}):"]
    for e in entries:
        title = e.get("title") or "(no title)"
        kind = e.get("type", "").replace("Boot Loader Specification Type #1 (.conf)", "BLS entry")
        out.append(f"  {title}" + (f"  [{kind}]" if kind else ""))
    if default:
        out.append(f"The firmware will boot {default.get('title')!r} by default"
                   + (f" (the entry running now is {active.get('title')!r})."
                      if active and active is not default else "."))
    else:
        out.append("No entry is marked (default), so which one the firmware will "
                   "boot is UNKNOWN - the list order is by sort-key, not by "
                   "preference, and the first row is not the default.")
    # `tries` is auto-tries: shani-deploy arms it so a slot that fails to boot
    # falls back to the other one, and how many tries are left is exactly the
    # question someone asks after a failed boot.
    for e in entries:
        if e.get("tries"):
            out.append(f"  {e.get('title', '(no title)')}: auto-tries, {e['tries']}.")
    return out


def _run(arguments: dict) -> str:
    lines = []
    if shutil.which("systemd-analyze") is None:
        lines.append("systemd-analyze is not installed, so boot timing is UNKNOWN.")
    else:
        time_out, why = _analyze("time")
        if time_out is None:
            lines.append(f"Boot timing is UNKNOWN: systemd-analyze time said {why!r}.")
        else:
            first = time_out.strip().splitlines()
            lines.append(first[0].strip() if first else "systemd-analyze time printed nothing.")
            lines.extend(l.strip() for l in first[1:] if l.strip())
        blame, why = _analyze("blame", "--no-pager")
        n = _top(arguments)
        if blame is None:
            lines.append(f"Per-service start times are UNKNOWN: {why}.")
        else:
            rows = [l.strip() for l in blame.splitlines() if l.strip()][:n]
            lines.append(f"The {len(rows)} services that took longest to start "
                         "(they start in parallel, so the slowest is not necessarily "
                         "what delayed the boot - check the userspace total above):")
            lines.extend(f"  {row}" for row in rows)
    lines.append("")
    lines.extend(_boot_entries())
    lines.append("")
    config = ChronoaConfig()
    allowed, why = _consent(config)
    lines.append(sense_reading.reading("boots") if allowed else why)
    return "\n".join(lines)


SKILLS = [Skill(name="boot_report", schema=SCHEMA, run=_run)]
