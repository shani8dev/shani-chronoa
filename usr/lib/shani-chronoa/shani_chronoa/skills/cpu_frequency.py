"""Skill: what is the CPU actually doing right now?

`power_profile` **sets** a power mode, and the machine then does whatever the
driver decides with it. Nothing anywhere reported the result: not the governor,
not the speed, not the range the hardware allows. So "I set it to performance
and my fans are still screaming" and "I set it to balanced and it is still
slow" both had no answer - the setting was visible, the effect was not.

`cpupower frequency-info` reports all three without root, and `cpupower` is in
`shani-tools-extra`'s depends, so a current image has it.

**Checked against both matrices, because "declared" and "installed" are
different facts here.** `cpupower` appears in neither image's command list -
and `--audit-packaging` explains why: the GNOME matrix is `20260925` and the
Plasma one `20260922`, while `shani-tools-extra` was last rebuilt to `1.2-15`
on 2026-10-09. The tool's own verdict is *"the image is older than the last
change to its lists - its 'not installed' lines measure a stale image, not the
lists"*. The same 19 declared-but-absent packages include `btop`, `iotop`,
`hdparm`, `playerctl` and `cabextract`, so this is one image-lag fact rather
than a list that has drifted. Recorded here so a future reader who finds
`cpupower` missing does not conclude the package is not declared.

**Two measured traps, both found by running the real binary on this machine:**

- **It prints "current CPU frequency" TWICE, and the first one is a failure.**
  Measured here, in this order:

      current CPU frequency: Unable to call hardware
      current CPU frequency: 1.07 GHz (asserted by call to kernel)

  The first is cpupower saying it *could not read the hardware register* - the
  usual reason being that the CPU is idle and the P-state is not being sampled.
  A parser that takes the first match reports the string "Unable to call
  hardware" as a speed, which is both nonsense and confidently formatted. This
  is the same shape as `iostat`'s since-boot report and `bootctl`'s three
  markers: **when a tool prints a field twice, which one you take is the whole
  difference between an answer and a lie.**
- **The governor is lowercase and space-separated** - `available cpufreq
  governors: performance powersave` - and it is *not* the same vocabulary as
  `powerprofilesctl`'s balanced/power-saver/performance. They are different
  layers: this one is what the kernel's scaling driver is doing, the other is
  the daemon's policy. Reporting one as the other would be a plausible wrong
  answer about a laptop's battery life.
"""

from __future__ import annotations

import re
import shutil
import subprocess

from shani_chronoa import files
from shani_chronoa.skills import Skill

_TIMEOUT = 15

#: `400 MHz - 4.70 GHz`, as `hardware limits:` prints it.
_RANGE = re.compile(r"(\d+(?:\.\d+)?)\s*(\w+)\s*-\s*(\d+(?:\.\d+)?)\s*(\w+)")
#: A real frequency, and the "cannot read it" sentence that precedes it.
_FREQ = re.compile(r"current CPU frequency:\s*(\d+(?:\.\d+)?)\s*(\w+)")
_UNREADABLE = re.compile(r"current CPU frequency:\s*(Unable\b.*)$", re.M)


def _info() -> "tuple[str, str]":
    try:
        proc = subprocess.run(["cpupower", "frequency-info"],
                              capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return "", f"cpupower did not answer within {_TIMEOUT}s"
    except OSError as exc:
        return "", str(exc)
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        return "", detail[-1] if detail else f"cpupower exited {proc.returncode}"
    return proc.stdout or "", ""


def _governor(text: str) -> str:
    """The governor **in force**, which is not the same line as the available
    ones. cpupower prints `available cpufreq governors: ...` and, in the
    multi-line policy sentence, `The governor "powersave" may decide...`.

    **Only the quoted form counts.** A first version fell back to
    `current policy: 400 MHz - 3.50 GHz` and reported **`CPU governor: 400`**
    on a machine with no cpufreq driver - a frequency wearing a governor's
    name. Caught by a test, and it is the same shape as the
    `Unable to call hardware` trap this module exists to avoid.
    """
    in_force = re.search(r'The governor "([^"]+)"', text)
    return in_force.group(1) if in_force else ""


def _run(_arguments: dict) -> str:
    if shutil.which("cpupower") is None:
        return files.tool_missing("cpupower", "read what the processor is doing")

    text, problem = _info()
    if problem:
        return (f"The processor's state is UNKNOWN: cpupower said {problem!r}. "
                "Nothing was guessed.")
    if not text.strip():
        return ("The processor's state is UNKNOWN: cpupower printed nothing. "
                "On a machine with no cpufreq driver (some ARM boards) there "
                "is no governor to report, which is not the same as a CPU "
                "that is doing nothing.")

    # **Is this output a cpupower report at all?** Checked before anything is
    # parsed out of it, because `lines` can never be empty once the
    # "governor: not reported" sentence exists - so the emptiness test that
    # used to guard this could no longer fire, and a version banner would have
    # been reported as a processor state.
    if not re.search(r"hardware limits:|current CPU frequency:|current policy:"
                     r"|available cpufreq governors:", text):
        return ("The processor's state is UNKNOWN: cpupower answered with text "
                "this does not read as a processor report. Nothing was guessed.")

    lines = []
    governor = _governor(text)
    if governor:
        lines.append(f"CPU governor: {governor}")
    else:
        # Named, not omitted: an absent governor is a real state (no cpufreq
        # driver, or a driver that does not use one) and it is the difference
        # between "the CPU is idle" and "there is no governor to speak of".
        lines.append("CPU governor: not reported - this driver does not name "
                     "one, which usually means there is no cpufreq governor in "
                     "play here")

    # **The frequency is taken from the last match, never the first.** Both
    # lines carry the same label and the first is `Unable to call hardware` on
    # an idle CPU - taking it would report that sentence as a speed.
    freqs = _FREQ.findall(text)
    if freqs:
        value, unit = freqs[-1]
        lines.append(f"running at about {value} {unit}")
        # `len(freqs) > 1` is never true - `_FREQ` does not match the
        # unreadable line - so the caveat was silently dead, and a machine
        # reading the hardware register directly was told it was quoting a
        # fallback it had not used. Caught by a mutation run.
        unreadable = _UNREADABLE.search(text)
        if unreadable and freqs:
            lines.append("  (the hardware register itself could not be read, "
                         "so this is what the kernel last reported rather "
                         "than a live measurement)")
    elif _UNREADABLE.search(text):
        lines.append("running at an unknown speed - cpupower could not read "
                     "the hardware register (it usually cannot while the CPU "
                     "is idle), and there was no kernel figure to fall back "
                     "on")

    limits = _RANGE.search(text)
    if limits:
        lines.append(f"hardware range: {limits.group(1)} {limits.group(2)} "
                     f"to {limits.group(3)} {limits.group(4)}")

    lines.append("")
    lines.append("The governor is what the kernel's scaling driver is doing "
                 "with the speed; it is a different thing from the power "
                 "profile Chronoa sets, which is the policy the daemon asks "
                 "for.")
    return "\n".join(lines)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "cpu_frequency",
        "description": (
            "What the processor is doing right now: which speed governor it "
            "is in, roughly what speed it is running at, and the range its "
            "hardware allows. Use after setting a power mode, to see whether "
            "the machine actually took it up - 'why is my fan so loud', 'is "
            "my CPU being throttled', 'did performance mode do anything'. "
            "Read-only; changes nothing."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

SKILLS = [Skill(name="cpu_frequency", schema=SCHEMA, run=_run)]