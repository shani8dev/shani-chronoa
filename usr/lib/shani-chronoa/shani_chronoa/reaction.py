"""Noticing a tool pattern that is wrong *as a pattern*, not as a single call.

Chronoa's permission layer is exact and stateless: each call is judged on its
own arguments against policy, and the answer is right every time. That design
is deliberate and this module does not weaken it. What it cannot see is
**sequence** — and some of the worst outcomes arrive one legal call at a time:

- a loop of `delete_file` across a whole directory, each path individually
  permitted, because the *intent* was to remove a project;
- twenty `read_text_file` calls in a turn, which is either a runaway loop or an
  exfiltration sweep, and is never what a person meant;
- the same destructive call repeated because a model is stuck.

None of those trips a per-call gate, because each one is genuinely allowed. So
this layer sits **after** the decision and **before** the action, and it only
ever does one thing: recognise a pattern, and require the *user* to confirm it
rather than proceeding on the model's say-so.

**It refuses by escalating, never by allowing.** Every signal here can only turn
an allowed call into one that needs a person. None of them can turn a refused
call into an allowed one — that authority stays entirely with the consent keys,
so this cannot become a way around them.

**The signals are deliberately crude and state them.** A threshold count is not
an understanding of what the user is doing. It is a tripwire, and a tripwire
that reports itself as a tripwire is honest in a way a clever classifier is not.
Each is a pure function of a short window of prior calls, so its behaviour is
predictable and testable, and none of them looks at content.

**A pattern that keeps repeating is itself a signal.** If the user confirms the
same pattern five times in a row, the likely problem is the model, not the
request, and the note says so rather than asking a sixth time.

**Origin matters and is read, not inferred.** A rule armed by a trigger runs
with no one watching, so an unattended burst is held to a stricter bound than
the same burst during a conversation where a person is present and can say no.
"""

from __future__ import annotations

import time
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence

from shani_chronoa.tool_tracking import ORIGIN_UNATTENDED, ORIGIN_USER

#: How many calls of the same tool inside the window before the pattern is
#: worth a person's attention. Generous enough that ordinary multi-step work
#: never trips it: reading ten files in a turn is normal, reading a hundred is
#: not.
_REPEAT_LIMIT = 25

#: How many DISTINCT targets one mutating tool may touch in the window. The
#: limit is on distinctness rather than count, because deleting forty files is
#: ordinary and deleting forty files in forty different directories to a person
#: is a cleanup, not a deletion.
_FANOUT_LIMIT = 12

#: Window length. Long enough to cover one turn including a slow tool, short
#: enough that last week's calls do not condemn today's.
_WINDOW_SECONDS = 300.0

#: Confirmations of the same pattern before the tool points at the loop.
_REPEAT_ESCALATION = 5


class Signal(NamedTuple):
    """One recognised pattern."""

    name: str
    detail: str


class Decision(NamedTuple):
    """What the reaction layer decided. `confirm` carries the question to ask."""

    confirm: Optional[str] = None
    signals: Sequence[Signal] = ()


_ALLOW = Decision()


def _origin_key(origin: str) -> str:
    return ORIGIN_UNATTENDED if origin == ORIGIN_UNATTENDED else ORIGIN_USER


class ReactionLayer:
    """Watches a short window of calls and asks a person when a pattern warrants it.

    One instance per assistant. The window is in memory and is not persisted: a
    restart clearing it is correct behaviour, because a new process has not
    inherited any of the intent that would have built a pattern.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        #: (origin, tool, target) -> [timestamps]
        self._calls: Dict[tuple, List[float]] = {}
        #: (origin, pattern) -> how many times a person has confirmed it
        self._confirmed: Dict[tuple, int] = {}

    # --- state ---------------------------------------------------------------

    def _prune(self, now: float) -> None:
        cutoff = now - _WINDOW_SECONDS
        for key, stamps in list(self._calls.items()):
            fresh = [t for t in stamps if t >= cutoff]
            if fresh:
                self._calls[key] = fresh
            else:
                del self._calls[key]

    def _count(self, origin: str, tool: str) -> int:
        now = self._clock()
        self._prune(now)
        return sum(len(v) for k, v in self._calls.items()
                   if k[0] == _origin_key(origin) and k[1] == tool)

    def _targets(self, origin: str, tool: str) -> set:
        self._prune(self._clock())
        return {k[2] for k in self._calls
                if k[0] == _origin_key(origin) and k[1] == tool}

    # --- signals -------------------------------------------------------------

    def _signals(self, origin: str, tool: str, target: str,
                 destructive: bool) -> List[Signal]:
        found: List[Signal] = []
        count = self._count(origin, tool)
        if count >= _REPEAT_LIMIT:
            found.append(Signal(
                "repeat",
                f"{tool} has already run {count} times in the last "
                f"{_WINDOW_SECONDS / 60:.0f} minutes. Each call was allowed on "
                f"its own; the pattern is what needs a person."))
        if destructive:
            targets = self._targets(origin, tool)
            if len(targets) >= _FANOUT_LIMIT:
                found.append(Signal(
                    "fanout",
                    f"{tool} has now touched {len(targets)} different targets "
                    f"in the last {_WINDOW_SECONDS / 60:.0f} minutes. If this "
                    f"is one job, saying so is faster than answering each time."))
        if origin == ORIGIN_UNATTENDED and count >= _REPEAT_LIMIT // 2:
            found.append(Signal(
                "unattended-burst",
                f"{tool} has run {count} times with nobody watching. An "
                f"automatic rule repeating this often is more likely stuck "
                f"than intended."))
        return found

    # --- the check -----------------------------------------------------------

    def check(self, tool: str, arguments: dict, origin: str = ORIGIN_USER,
              destructive: bool = False) -> Decision:
        """Judge a call. Returns a `confirm` question, or an allow.

        The caller asks the question and calls `confirmed()` on the answer.
        This never decides on the model's behalf - it only decides whether a
        person is needed.
        """
        target = _target_of(arguments)
        signals = self._signals(origin, tool, target, destructive)
        if not signals:
            self._record(origin, tool, target)
            return _ALLOW

        key = (_origin_key(origin), tool, signals[0].name)
        times = self._confirmed.get(key, 0)
        if times >= _REPEAT_ESCALATION:
            # Still asked, but the question changes: five confirmations of the
            # same pattern points at the model repeating itself, and the honest
            # thing to say is so.
            return Decision(
                confirm=(
                    f"You have approved {tool} repeating {times} times now. "
                    f"That usually means the request is not getting anywhere "
                    f"rather than that you want it this many times. Run "
                    f"{tool} once yourself, or rephrase what you want."),
                signals=signals)

        self._record(origin, tool, target)
        parts = [signals[0].detail]
        if len(signals) > 1:
            parts.append(signals[1].detail)
        parts.append("This is a pattern check, not a permission: each of "
                     "these calls was allowed on its own.")
        return Decision(confirm=" ".join(parts), signals=signals)

    def confirmed(self, tool: str, origin: str = ORIGIN_USER,
                  pattern: str = "repeat") -> None:
        """Record that a person approved this pattern. `check` is called again
        by the caller on the next call, so the count lives here."""
        key = (_origin_key(origin), tool, pattern)
        self._confirmed[key] = self._confirmed.get(key, 0) + 1

    def _record(self, origin: str, tool: str, target: str) -> None:
        key = (_origin_key(origin), tool, target)
        self._calls.setdefault(key, []).append(self._clock())

    def reset(self) -> None:
        self._calls.clear()
        self._confirmed.clear()


def _target_of(arguments: dict) -> str:
    """What a call acts on, for the fan-out count.

    Prefers the path-like argument because that is what distinguishes forty
    files from forty directories. Falls back to an empty string, which counts as
    one target - so a tool with no path argument is judged on count alone,
    which is the right degradation.
    """
    if not isinstance(arguments, dict):
        return ""
    for key in ("path", "file", "target", "directory", "name", "source",
                "destination", "url", "host"):
        value = arguments.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def destructive_tools() -> frozenset:
    """The tool names this treats as acting destructively.

    A set rather than a flag on the call, so a new destructive skill is
    fan-out-checked the moment it is named here and not silently exempted by a
    caller forgetting an argument.
    """
    return frozenset({
        "delete_file", "trash_file", "move_or_copy_file", "write_text_file",
        "edit_file", "create_directory", "empty_trash", "kill_process",
        "control_service", "remove_file", "replace_file",
    })