"""Reflexes: the fast layer that answers without waking the model.

Chronoa has two speeds today. A **sense** polls something and puts a percept in
context, and the model decides what it means - which costs a turn, and on the
hardware this project targets (a 0.6B model on a CPU) that is seconds. A
**skill** is called by the model, so it is the same cost again.

That leaves a gap this module fills, and the gap is not speed for its own sake.
It is that **when the model is not loaded, neither path answers at all.** A
laptop that is idle has no model resident; a machine whose model failed to load,
or whose provider is unreachable, or which the user has deliberately paused, has
no way to say "battery is at 8%". Something has to notice that without asking
a question. A reflex is that something.

**The design follows the reflex-arc literature, and takes its safety gate
seriously.** Reflex Fabric and pincher both describe a learned reflex store: a
decision that succeeded N times is compiled into a fast path. That is a good
idea for latency and a bad one for a system whose central safety property is a
set of consent keys — because a learned reflex that fires without asking is a
consent bypass that nobody wrote down and nobody reviewed. So the learning is
inverted here:

- **The reflex set is fixed in this file.** Not learned, not compiled, not
  imported. It is a finite table, which is what makes it checkable: a test can
  assert the *total* set, so a reflex cannot be added by anything at runtime.
- **A reflex can only notify.** It has no path to change a file, run a command,
  or call a skill. The strongest thing one can do is `notify-send`. Anything
  that alters the machine is a skill, and a skill is gated by a consent key.
- **A reflex never consumes a consent key, so it cannot consume one either.**
  There is no code path from here to `execute_tool`.

That is the reflex-arc idea with the learned half removed, and it is a real
reduction in capability. It buys one thing: **the table is finite and therefore
decidable**, which is the property the automata work argues for and the reason
a system like this should prefer a reflex layer over a heuristic. You can read
every reflex, and a test can prove the set is exactly this list.

**Every reflex is a pure function of one cheap read** - a `sysfs` file, a
`statvfs`, a line of `/proc`. No subprocess, no network, no LLM. That is what
makes them fast, and it also means each one can be tested against a real file
rather than a mock.

**A reflex that cannot read its input fires nothing.** An unreadable sensor is
not a healthy reading. The failure direction here is toward silence, because a
reflex that guesses is a reflex that can be wrong at a time when nothing is
watching.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence

logger = logging.getLogger(__name__)

#: Re-notify at most this often for the same reflex. Without it a reflex that
#: stays true - a battery that stays flat - would notify every tick forever,
#: and a notification nobody can escape is a denial of service by another name.
_COOLDOWN_SECONDS = 900.0

_SYS_POWER = "/sys/class/power_supply"
_MEMINFO = "/proc/meminfo"
_SYNCHRONISED = "/run/systemd/timesync/clock_synchronized"
#: Read root for the thermal sensors. A module constant so a test can point it
#: at a fixture directory; the probes are otherwise pure functions of the real
#: /sys.
_THERMAL_ROOT = "/sys/class/thermal"


class Reflex(NamedTuple):
    """One reflex. `name` is its identity in the log and in `--now`."""

    name: str
    #: What this is for, in one sentence. Shown in the CLI and in the settings
    #: row, so it cannot be a private note.
    description: str
    #: The condition. Returns None when it cannot be judged, and a
    #: `(subject, urgency)` pair when it can. None must never fire.
    probe: Callable[[], Optional[tuple]]
    #: Below this, do not say anything at all. URGENCY_LOW.
    threshold_note: str


URGENCY_LOW = "low"
URGENCY_HIGH = "high"


class Urgence(NamedTuple):
    """What a reflex decided: whether to speak, and how loudly."""

    speak: bool
    urgency: str = URGENCY_LOW
    subject: str = ""
    detail: str = ""


SILENT = Urgence(False)


def _read_int(path: Path) -> Optional[int]:
    """One integer out of a sysfs/procfs file, or None.

    None means "could not be read", which every caller treats as silence. It is
    not zero, and it is not a healthy reading.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    try:
        return int(text.split()[0])
    except (ValueError, IndexError):
        return None


# ---------------------------------------------------------------- the reflexes

def _battery_percent() -> Optional[int]:
    for entry in Path(_SYS_POWER).glob("*/capacity"):
        try:
            return int(entry.read_text().strip())
        except (OSError, ValueError):
            continue
    return None


def probe_battery_critical() -> Urgence:
    """A battery low enough that the next thing that happens is the machine dying."""
    try:
        percent = _battery_percent()
    except OSError:
        # The helper guards its own reads, but the guard belongs HERE too: a
        # probe that cannot judge must be silent on its own account, not
        # because of a detail of a function it calls.
        return SILENT
    if percent is None:
        return SILENT
    if percent <= 10:
        return Urgence(True, URGENCY_HIGH, "Battery critical",
                       f"{percent}% remaining")
    return SILENT


def probe_memory_critical() -> Urgence:
    """Available memory below what a desktop needs to keep drawing."""
    text = None
    try:
        text = Path(_MEMINFO).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return SILENT
    available = None
    total = None
    for line in text.splitlines():
        if line.startswith("MemAvailable:"):
            try:
                available = int(line.split()[1])
            except (ValueError, IndexError):
                available = None
        elif line.startswith("MemTotal:"):
            try:
                total = int(line.split()[1])
            except (ValueError, IndexError):
                total = None
    if available is None or not total:
        return SILENT
    if available / total <= 0.05:
        megabytes = available // 1024
        return Urgence(True, URGENCY_HIGH, "Memory nearly exhausted",
                       f"{megabytes} MiB available of {total // 1024} MiB")
    return SILENT


def probe_root_disk_critical() -> Urgence:
    """The root filesystem with less room left than a swapfile plus slack."""
    try:
        stats = os.statvfs("/")
    except OSError:
        # Cannot judge the filesystem - silent, not "assume it is fine" and not
        # "assume it is full".
        return SILENT
    free = stats.f_bavail * stats.f_frsize
    total = stats.f_blocks * stats.f_frsize
    if not total:
        return SILENT
    if free < 512 * 1024 * 1024:
        return Urgence(True, URGENCY_HIGH, "Root filesystem nearly full",
                       f"{free // (1024 * 1024)} MiB free of "
                       f"{total // (1024 * 1024)} MiB")
    return SILENT


def probe_clock_unsynchronised() -> Urgence:
    """A clock that has never been synchronised makes every recorded time a guess.

    Read from the kernel's own flag rather than by asking `timedatectl`, so this
    costs nothing and needs no service to be running.

    **An absent flag is silence, not evidence.** A first version treated a
    missing `/run/systemd/timesync/clock_synchronized` as "no synchronisation
    state, so the clock is probably wrong" and fired on this development
    machine - which is a container, where the timesync service does not run and
    the host's clock is perfectly fine. That is the confident-wrong-answer
    failure this project keeps recording: absence of evidence reported as
    evidence of absence, with a notification attached. It now needs positive
    proof that the flag exists and says the clock is unsynchronised.
    """
    path = Path(_SYNCHRONISED)
    try:
        flag = path.read_text(encoding="utf-8").strip()
    except OSError:
        # Cannot tell. Say nothing rather than guess - and the distinction is
        # real: a desktop with timesync disabled and a container with no
        # timesync both land here, and only the first is a problem.
        return SILENT
    if flag == "1":
        return SILENT
    return Urgence(True, URGENCY_LOW, "Clock unsynchronised",
                   "the kernel reports the system clock has never been "
                   "synchronised, so every recorded time is a local guess")


def probe_thermal_critical() -> Urgence:
    """A CPU hot enough to have started dropping its own clock.

    The trip point is read from the sensor itself rather than hard-coded,
    because it differs by machine and a hard-coded number is either too high
    (never fires) or too low (always fires) on someone else's hardware.
    """
    # -274000 is the kernel's "no trip point", not a temperature. A first
    # version fell back to a fixed 95C whenever that number could not be parsed,
    # which then reported "76C, at or past its published trip point" on a
    # machine whose CPU was at 76C and nowhere near throttling - a confident
    # claim about a threshold that did not exist.
    #
    # Only zones that publish a REAL trip point are considered, and only a trip
    # point whose type is actually a threshold. Sensors named SEN1..SEN4, the
    # Intel DPTF sensors, and the wireless radio each publish their own
    # temperature and none of them is the CPU die.
    hottest = None
    hottest_zone = ""
    for zone in Path(_THERMAL_ROOT).glob("thermal_zone*"):
        try:
            value = int((zone / "temp").read_text().strip()) / 1000.0
        except (OSError, ValueError):
            continue
        if not (-40.0 < value < 150.0):
            # Sentinel readings such as -273.15 mean "no reading".
            continue
        try:
            kind = (zone / "type").read_text().strip()
        except OSError:
            kind = zone.name
        # Only CPU-class zones. Anything else is a sensor on the chassis.
        if not any(token in kind.lower() for token in
                   ("cpu", "pkg", "tdie", "tctl", "coretemp", "k10temp",
                    "zenpower", "soc")):
            continue
        try:
            limit = int((zone / "trip_point_0_temp").read_text().strip()) / 1000.0
        except (OSError, ValueError):
            continue
        if limit <= -40.0:
            continue  # the kernel's "no trip point"
        if value >= limit and (hottest is None or value > hottest):
            hottest = value
            hottest_zone = kind
            hottest_limit = limit
    if hottest is None:
        return SILENT
    return Urgence(True, URGENCY_HIGH, f"CPU hot ({hottest_zone})",
                   f"{hottest:.0f}C, at or past its {hottest_limit:.0f}C "
                   f"trip point")


#: The whole reflex set. **This list is the entire capability of this module.**
#:
#: It is a module-level constant on purpose. A test asserts this exact tuple, so
#: anything that added a reflex at runtime - a plugin, a config file, a learned
#: entry - would fail rather than quietly widen what an unaided machine can do.
#: That is the decidability property, enforced rather than asserted in prose.
REFLEXES: tuple = (
    Reflex("battery_critical", "Warn when the battery is nearly flat",
           probe_battery_critical, "10% or less"),
    Reflex("memory_critical", "Warn when the machine is nearly out of memory",
           probe_memory_critical, "5% or less available"),
    Reflex("disk_critical", "Warn when the root filesystem is nearly full",
           probe_root_disk_critical, "under 512 MiB free"),
    Reflex("thermal_critical", "Warn when the CPU is at or past its trip point",
           probe_thermal_critical, "at the sensor's own published trip point"),
    Reflex("clock_unsynchronised",
           "Warn when the system clock has never been synchronised",
           probe_clock_unsynchronised, "never synchronised"),
)

REFLEX_NAMES = tuple(r.name for r in REFLEXES)


def evaluate(names: Sequence[str] = REFLEX_NAMES) -> List[Urgence]:
    """Run the named reflexes. Never raises.

    A probe that raises is a bug in the probe, and a bug here must not take the
    caller down - the caller may be a startup path. It becomes silence, and it
    is logged, because a reflex that has been broken since 3am and has said
    nothing is exactly the failure this module exists to prevent.
    """
    by_name = {r.name: r for r in REFLEXES}
    out: List[Urgence] = []
    for name in names:
        reflex = by_name.get(name)
        if reflex is None:
            continue
        try:
            result = reflex.probe()
        except Exception as exc:  # noqa: BLE001 - a reflex must not break the caller
            logger.error("reflex %s raised and stayed silent: %s: %s",
                         name, type(exc).__name__, exc, exc_info=True)
            continue
        if result is not None and result.speak:
            out.append(result)
    return out


def notify(urgences: Sequence[Urgence], dry_run: bool = False) -> List[str]:
    """Show what a reflex set would say, and optionally actually say it.

    `dry_run` is the default for every caller that is not the scheduler, so the
    honest report is never a side effect of being asked what would happen.
    """
    sent: List[str] = []
    for urge in urgences:
        line = f"{urge.subject}: {urge.detail}"
        sent.append(line)
        if dry_run:
            continue
        if shutil.which("notify-send") is None:
            logger.debug("reflex wanted to notify but notify-send is absent")
            continue
        subprocess.run(
            ["notify-send", "--urgency=" + urge.urgency,
             "Chronoa", urge.subject, urge.detail],
            capture_output=True, text=True, timeout=10, check=False)
    return sent


class ReflexRunner:
    """Tracks cooldowns so a condition that stays true does not nag."""

    def __init__(self, names: Sequence[str] = REFLEX_NAMES,
                 clock: Callable[[], float] = __import__("time").monotonic,
                 cooldown: float = _COOLDOWN_SECONDS) -> None:
        self._names = tuple(names)
        #: How often `consolidate()` may actually retrain. The gate inside
        #: `consolidate_if_due` is cheaper than this interval, not more
        #: expensive; this is the outer bound so a busy log cannot turn a reflex
        #: tick into a training loop.
        self._consolidate_every = 1800.0
        self._last_consolidated: Optional[float] = None
        self._clock = clock
        self._cooldown = cooldown
        self._last: Dict[str, float] = {}

    def consolidate(self) -> object:
        """Fold the day's experience into a model. Runs on this timer, not on a
        call.

        **This is what makes the learning chain live.** The reflex tick is the
        one thing guaranteed to run for the life of the process, and the learning
        layer has no other scheduler of its own. Without this,
        `tools._outcome_model()` loaded a file nothing wrote, so nothing was ever
        predicted, so nothing was ever logged to learn from - the whole layer was
        inert rather than broken.

        `consolidate_if_due()` is cheap when there is nothing to do: one `stat`
        on the log and one read of the model's provenance. It returns what it
        decided either way, and a refusal - "the training split has no example of
        verified" - is the most useful thing it can say until post-conditions
        have been recording for long enough.
        """
        if self._last_consolidated is None:
            self._last_consolidated = self._clock()
        if (self._clock() - self._last_consolidated) < self._consolidate_every:
            return None
        self._last_consolidated = self._clock()
        try:
            from shani_chronoa.learning import consolidate_if_due
            return consolidate_if_due()
        except Exception:  # noqa: BLE001 - consolidation must never stop a tick
            return None

    def due(self) -> List[Urgence]:
        """Reflexes that want to speak and are not inside their cooldown.

        **The expiry is checked here, not left to `expire()` being called.**
        A first version only skipped a subject that appeared in `self._last`,
        which meant a condition that cleared and came back was never reported
        again - the cooldown became permanent, silently, for the life of the
        process. `expire()` existed to do this and nothing called it, so the
        tick had to do it.
        """
        now = self._clock()
        self.expire(now)
        out = [urge for urge in evaluate(self._names)
               if urge.subject not in self._last]
        self._mark(now, out)
        return out

    def _mark(self, now: float, urges: Sequence[Urgence]) -> None:
        for urge in urges:
            self._last[urge.subject] = now

    def expire(self, now: Optional[float] = None) -> None:
        """Forget cooldowns older than the window, so a condition that clears
        and returns is reported again."""
        current = self._clock() if now is None else now
        for subject, when in list(self._last.items()):
            if current - when >= self._cooldown:
                del self._last[subject]