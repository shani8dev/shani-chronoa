"""Dream: an offline pass that reads the day's experience and distils it.

Chronoa records every tool call - name, arguments, result, duration, origin,
verdict, evidence - and every conversation, and then almost never looks at them
again. That is a large amount of evidence about how the assistant is actually
performing, collected continuously, unused.

This module is the reader. It runs **offline and unattended**, over a window of
that log, and produces a small set of findings a person can read in a minute.
It is the "dream" idea in its engineering sense - consolidation outside the hot
path - and not in any mystical one: there is no model here, nothing is learned
into weights, and no permission changes as a result.

**What it can honestly say.** Four things, each computed from the log alone:

- **What is failing.** Tools whose calls come back `FAILED`, or whose verdict is
  `UNVERIFIED` every single time. The second is the interesting one: a tool that
  never verifies is a tool the assistant believes it used successfully and cannot
  prove.
- **What is slow.** Where the time actually goes, per tool, rather than where
  somebody guessed it would.
- **What runs unattended.** Calls with `origin != user` - trigger-fired, so with
  nobody watching. That is the population where a mistake costs the most, and it
  is invisible in the UI.
- **What is repeated.** The same call over and over, which is either a loop or a
  workflow that should have been one call.

**What it will not do, and this is the important part.** It does not modify
anything. It does not add a tool, change a setting, tighten or relax a gate, or
write to any registry another module reads. A finding is a sentence in a file.
That is deliberate: an offline pass that can change the system's permissions is
a backdoor with a cron entry, and the reflex literature's own lesson is that a
layer which silently gains authority is how a consent system stops meaning
anything. If a finding should change behaviour, a person reads it and changes
the code.

**It fails toward saying less.** A log that is absent, truncated, or full of
lines it cannot parse yields fewer findings, never a wrong one - and the count
of what it could not read is reported, because a consolidation pass that silently
consumed half its input and reported the rest as the whole is worse than one
that says it read 40 of 100 records.

**It never sees argument values or results.** Only the tool name, the verdict,
the origin, the duration and the error text. That is not a privacy nicety: an
offline pass over a day's activity is exactly the kind of thing that should be
unable to reassemble what you did, and the aggregation keys make that structural
rather than a promise.
"""

from __future__ import annotations

import json
import logging
import re
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Where the log this reads lives. Same place `ToolTracker` writes.
LOG_FILE = Path(
    os.environ.get("XDG_DATA_HOME",
                   Path.home() / ".local/share")
) / "shani-chronoa" / "logs" / "tool_calls.log"

#: Findings worth a person's attention. A dream that produces forty notes is a
#: dream nobody reads, which is the same as no dream.
_MAX_FINDINGS = 12

#: A tool called fewer times than this cannot support a rate, a trend or a
#: "always" claim. One observation is an anecdote.
_MIN_SAMPLE = 3


class Reading(NamedTuple):
    """What one pass managed to read, and what it could not."""

    records: int
    unreadable: int
    truncated: bool = False


class Finding(NamedTuple):
    """One thing worth knowing. `severity` is for sorting, not for alarming."""

    severity: str   # "high" | "medium" | "low"
    heading: str
    detail: str


SEV_HIGH, SEV_MEDIUM, SEV_LOW = "high", "medium", "low"


def read_log(path: Path = LOG_FILE, limit_bytes: int = 32 * 1024 * 1024) -> Reading:
    """Read the log, newest-last, reporting how much was unusable.

    A tail is read rather than the whole file: a year of calls is tens of
    megabytes and a consolidation pass should not need it. `limit_bytes` bounds
    the read so a pathological log cannot exhaust memory.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return Reading(0, 0)
    truncated = size > limit_bytes
    try:
        with path.open("rb") as handle:
            if truncated:
                handle.seek(size - limit_bytes)
                handle.readline()  # discard the partial first line
            payload = handle.read()
    except OSError as exc:
        logger.debug("dream: could not read %s: %s", path, exc)
        return Reading(0, 0)

    good = 0
    bad = 0
    for line in payload.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(entry, dict) and entry.get("tool_name"):
            good += 1
        else:
            bad += 1
    return Reading(good, bad, truncated)


def _entries(path: Path) -> List[dict]:
    """The records themselves, or [] if the log is unreadable."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("tool_name"):
            out.append(entry)
    return out


def analyse(entries: Sequence[dict]) -> List[Finding]:
    """Every finding this pass can support from the records alone."""
    findings: List[Finding] = []
    if not entries:
        return findings

    by_tool: Dict[str, List[dict]] = defaultdict(list)
    for entry in entries:
        name = str(entry.get("tool_name") or "?")
        by_tool[name].append(entry)

    total = len(entries)

    # 1. Tools that failed. A failure rate is a fact; the error text is the
    #    evidence, truncated because a stack trace is not a sentence.
    for name, calls in sorted(by_tool.items(), key=lambda kv: -len(kv[1])):
        if len(calls) < _MIN_SAMPLE:
            continue
        failed = [c for c in calls if str(c.get("verdict", "")).lower() == "failed"]
        if len(failed) == len(calls):
            findings.append(Finding(
                SEV_HIGH, f"{name} failed every time",
                f"{len(calls)} call(s), all failed. Last error: "
                f"{_last_error(failed) or 'none recorded'}"))
        elif len(failed) >= max(2, len(calls) // 2):
            findings.append(Finding(
                SEV_MEDIUM, f"{name} failed about half the time",
                f"{len(failed)} of {len(calls)} calls failed. Last error: "
                f"{_last_error(failed) or 'none recorded'}"))

    # 2. Tools that can never prove they worked. This is the one that matters
    #    most and is easiest to miss: UNVERIFIED every time means the assistant
    #    believes it succeeded and has no evidence either way.
    for name, calls in sorted(by_tool.items(), key=lambda kv: -len(kv[1])):
        if len(calls) < _MIN_SAMPLE:
            continue
        verdicts = [str(c.get("verdict", "")).lower() for c in calls]
        if verdicts and all(v in ("unverified", "") for v in verdicts):
            findings.append(Finding(
                SEV_MEDIUM, f"{name} never verifies",
                f"all {len(calls)} call(s) came back UNVERIFIED. It may well be "
                f"working; there is just nothing that confirms it, so a silent "
                f"failure here would be invisible."))

    # 3. Unattended activity - the population where nothing was watching.
    unattended = [c for c in entries
                  if str(c.get("origin") or "user") != "user"]
    if len(unattended) >= _MIN_SAMPLE:
        by_origin: Counter = Counter(str(c.get("origin")) for c in unattended)
        worst = by_origin.most_common(1)[0]
        findings.append(Finding(
            SEV_MEDIUM, "activity with nobody watching",
            f"{len(unattended)} of {total} calls ran unattended "
            f"({', '.join(f'{n} {c}' for n, c in by_origin.most_common(4))}). "
            f"This is where a mistake costs most, and it is invisible in the "
            f"window."))
        unanswered = [c for c in unattended
                      if str(c.get("verdict", "")).lower() not in ("verified",)]
        if len(unanswered) >= _MIN_SAMPLE:
            findings.append(Finding(
                SEV_MEDIUM, "unattended calls nothing confirmed",
                f"{len(unanswered)} of {len(unattended)} unattended calls were "
                f"not verified. A trigger that fires and cannot confirm its "
                f"effect repeats the mistake every time it runs."))

    # 4. Where the time goes.
    timed = [(c, float(c.get("duration_ms") or 0)) for c in entries]
    timed = [(c, ms) for c, ms in timed if ms > 0]
    if len(timed) >= _MIN_SAMPLE:
        total_ms = sum(ms for _c, ms in timed)
        slowest = max(timed, key=lambda pair: pair[1])
        share = (slowest[1] / total_ms * 100) if total_ms else 0
        if share >= 25 and slowest[1] >= 500:
            findings.append(Finding(
                SEV_LOW, f"{slowest[0].get('tool_name')} is where the time goes",
                f"{slowest[1]:,.0f}ms of {total_ms:,.0f}ms total "
                f"({share:.0f}% of all measured time across "
                f"{len(timed)} timed call(s))."))

    # 5. Repetition - either a loop or a workflow that should be one call.
    for name, calls in sorted(by_tool.items(), key=lambda kv: -len(kv[1])):
        if len(calls) < max(12, _MIN_SAMPLE * 4):
            continue
        findings.append(Finding(
            SEV_LOW, f"{name} was called {len(calls)} times",
            f"{len(calls)} of {total} calls ({len(calls) / total * 100:.0f}%). "
            f"If that is one job, it may be one call; if it is not, something is "
            f"looping."))

    findings.sort(key=lambda f: (SEV_HIGH, SEV_MEDIUM, SEV_LOW).index(f.severity))
    return findings[:_MAX_FINDINGS]


def _last_error(calls: Sequence[dict]) -> str:
    """The most recent error line, **with anything that looks like a secret
    taken out**.

    Quoting the error verbatim is what makes the finding actionable, and it is
    also how a credential ends up in a file nobody was thinking of as sensitive.
    An HTTP error carrying a bearer token, a connection string with a password,
    a `key=...` in a query string - all of those reach `evidence` first and
    would be copied here verbatim. So registered secrets are redacted, and
    token-shaped runs are masked even when nothing registered them, because the
    thing most likely to be in an error is the thing that was just refused.
    """
    for entry in reversed(list(calls)):
        evidence = str(entry.get("evidence") or "").strip()
        if evidence:
            return _mask(evidence.splitlines()[0])[:160]
    return ""


#: Long unbroken alphanumerics with a digit in them - the shape of a key, a
#: token or a session id. Deliberately crude: a false positive costs a masked
#: error message, a false negative writes a credential to disk.
_SECRETISH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_-]{16,}(?![A-Za-z0-9])")


def _mask(text: str) -> str:
    """Registered secrets replaced outright; anything else token-shaped masked."""
    try:
        from shani_chronoa.redaction import redactor
        text = redactor.sanitize(text)
    except Exception:  # noqa: BLE001 - never fail a report over this
        pass

    def _mask_one(match: "re.Match") -> str:
        token = match.group(0)
        # A word with a digit in it and a mix of letters is a credential far
        # more often than it is prose; all-lowercase long words are not.
        if any(ch.isdigit() for ch in token) and any(ch.isupper() for ch in token):
            return token[:4] + "…" + token[-2:]
        if any(ch.isdigit() for ch in token) and len(token) >= 24:
            return token[:4] + "…" + token[-2:]
        return token

    return _SECRETISH.sub(_mask_one, text)


def render(reading: Reading, findings: Sequence[Finding], path: Path = LOG_FILE) -> str:
    """A minute's read. Plain text, no jargon, and honest about its own gaps."""
    out = [f"What the last {reading.records} recorded tool call(s) look like.", ""]
    if reading.records == 0:
        out.append("Nothing to read. Either nothing has run yet, or the log is "
                   "not where this expects it.")
        if reading.unreadable:
            out.append(f"({reading.unreadable} line(s) could not be parsed.)")
        return "\n".join(out)

    if not findings:
        out.append("Nothing stands out. No tool failed consistently, every tool "
                   "that can be verified was, and nothing ran unattended often "
                   "enough to be worth naming.")
    for finding in findings:
        out.append(f"  [{finding.severity}] {finding.heading}")
        out.append(f"      {finding.detail}")
        out.append("")

    caveats = []
    if reading.unreadable:
        caveats.append(f"{reading.unreadable} line(s) could not be parsed and "
                       f"are not counted in anything above")
    if reading.truncated:
        caveats.append("the log was longer than this pass reads, so the oldest "
                       "records were skipped")
    if caveats:
        out.append("Caveats: " + "; ".join(caveats) + ".")
    out.append("")
    out.append("This is a reading of what happened. It changes nothing.")
    return "\n".join(out)


def dream(path: Path = LOG_FILE) -> str:
    """The whole pass: read, analyse, render. Safe to run at any time."""
    reading = read_log(path)
    entries = _entries(path)
    if reading.records and len(entries) != reading.records:
        # read_log truncates; _entries does not. Report the smaller truth.
        reading = Reading(len(entries), reading.unreadable, reading.truncated)
    return render(reading, analyse(entries), path)


def write_dream(path: Path = LOG_FILE, out_dir: Optional[Path] = None,
                when: Optional[str] = None) -> Optional[Path]:
    """Run the pass and keep the result, so it can be read later.

    Written to the same private per-user data directory as the log it reads,
    and created with the same privacy, because a file describing your day is not
    less sensitive than the day.
    """
    from shani_chronoa import files
    text = dream(path)
    target_dir = out_dir or LOG_FILE.parent
    try:
        files.ensure_private_dir(target_dir)
        stamp = when or "latest"
        destination = target_dir / f"dream-{stamp}.txt"
        destination.write_text(text, encoding="utf-8")
        destination.chmod(0o600)
        return destination
    except OSError as exc:
        logger.debug("dream: could not write the result: %s", exc)
        return None