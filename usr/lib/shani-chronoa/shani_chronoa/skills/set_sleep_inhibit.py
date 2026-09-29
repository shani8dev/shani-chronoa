"""Skill: hold the machine awake for a bounded time, and report what is holding it.

  The question this answers is "keep it awake while I do the thing" - a long install, a
  backup, a test run, a render. `set_screensaver` changes the *idle timeout* to get there,
  which is a standing preference that outlives the task and has to be changed back. That
  is the wrong tool for "just for now": forget to undo it and the machine blanks at twelve
  minutes for the rest of the week, with nothing in the setting saying why.

  This is the other approach and the better one for a bounded task: take a sleep lock for
  a stated number of seconds and let it lapse by itself.

Adopted from qwen-code's `services/sleepInhibitor.ts`, the one harness in the survey with a
real implementation of this. What carried over, and what did not:

  - **The inhibited command is the timer.** `systemd-inhibit ... sleep <seconds>` holds the
    lock exactly as long as that child runs, so the bound is enforced by process lifetime
    rather than by anything in this process that can be interrupted. qwen-code relies on the
    same mechanism with `sleep infinity` to scope its hold to a child's lifetime; here the
    duration *is* the bound, which is what makes the failure mode safe. If Chronoa is
    killed, crashes, or the machine reboots, the lock goes with the child. A hold that can
    outlive the process that asked for it is a machine that will not sleep tonight, and no
    setting anywhere would say why.
  - **`--mode=block --what=sleep`,** not `--what=idle`. Inhibit sleep and not the idle timer:
    a brief unattended moment should still blank, and a build should not be holding a laptop
    awake against a closed lid.
  - **A sanitised `--why`.** The reason is model-supplied text going onto a command line.
    C0 control characters and DEL become spaces, so no newline can separate this argument
    from another one, and the length is capped. qwen-code sanitises for the same reason.
  - **A headless session is a no-op,** not a failure. There is no user to protect, and
    reporting a successful hold on a machine nobody is sitting at would be a lie about an
    action that changes nothing.

One thing from qwen-code deliberately **not** carried over: it passes `--no-ask-password` so
polkit cannot raise a password dialog because the assistant decided to run something. That
is the right instinct, but this systemd rejects the flag outright - `systemd-inhibit:
unrecognized option '--no-ask-password'`, and it is absent from `--help`. Treating that as a
hard refusal, which is what qwen-code's shape invites, makes the whole skill refuse to work
on the machine it was written for. Measured here instead: taking a sleep lock as a session
user needs no privilege and raises no dialog, so the flag is used when the system has it and
its absence is not treated as a failure. Where it is missing the report says so rather than
implying the guarantee was had.

Honesty rules:

  - **A missing `systemd-inhibit` is UNKNOWN, not "off."** So is a list that cannot be read,
    or a machine not running systemd. Only a list actually read is a status.
  - **The machine is usually already held by something else,** and saying "nothing is holding
    sleep" on a desktop would be false - GNOME Shell, NetworkManager and UPower all take
    sleep inhibitors routinely. Status reports what it found, and separates ours from theirs.
  - **Released means verified.** After signalling the child, `--list` is read again.
    Signalling a process is not releasing a lock, and the two disagree in the cases that
    matter.
  - **The bound is stated every time,** in both directions: what was asked for and when it
    will lapse on its own. A hold with no stated expiry reads to the user as permanent.
  - **Reporting needs no consent; holding does.** It gates on its own key rather than sharing
    `idle-timeout-enabled`, because a bounded hold for the next ten minutes is a different
    decision from a permanent change to the machine's idle policy.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "sleep-inhibit-enabled"
_TIMEOUT = 20

#: What we hand `systemd-inhibit` as the inhibited command. It is also the timer: the lock
#: is held while this runs, so the bound needs no timer of our own to be trustworthy.
_INHIBITED_COMMAND = "sleep"

#: Long enough for a package build, short enough that a mistake is not a week. A bound on
#: damage, not on ambition; anything longer is several holds.
_MAX_SECONDS = 3600
_MIN_SECONDS = 30

#: Matches every character that could break out of `--why=<value>`: C0 controls, DEL, and
#: newline. See qwen-code's `sanitizeInhibitorReason`.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

_MAX_REASON_LENGTH = 120
_DEFAULT_REASON = "Chronoa is holding it awake for a task in progress"

#: Our own `--who`, so a hold is identifiable in `systemd-inhibit --list` and in the power
#: panel. Attribution is the difference between a user understanding why their machine is
#: awake and them filing a bug about it.
_WHO = "Shani Chronoa"

#: Where the hold is recorded. Written to rather than kept in a module global because the
#: skill is reached through several processes - the assistant, the MCP server, the settings
#: window - and a global would be a hold that release could not see from another one.
_STATE_FILE = Path(
    os.path.expanduser("~/.local/share/shani-chronoa/sleep-inhibit.json"))


def _sanitize_reason(reason: str) -> str:
    """Reduce model-supplied text to something safe to place on a command line.

    Control characters and DEL become spaces, so no newline can separate this argument from
    another one. An empty or all-control reason is replaced rather than passed empty, because
    `systemd-inhibit --why=` is a reason-less lock in a list whose entire job is to explain
    itself.
    """
    cleaned = _CONTROL_CHARS.sub(" ", reason).strip()[:_MAX_REASON_LENGTH].strip()
    return cleaned or _DEFAULT_REASON


def _supports_no_ask_password() -> bool:
    """Whether this `systemd-inhibit` accepts `--no-ask-password`.

    Added in systemd v230, and absent from this machine. Probed with `--help` rather than a
    version comparison, because the flag actually existing is a better witness than a version
    string, and because an old systemd is not an error - just one without the flag.
    """
    try:
        result = subprocess.run(
            ["systemd-inhibit", "--help"],
            capture_output=True, text=True, timeout=_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return "--no-ask-password" in (result.stdout + result.stderr)


def _headless() -> bool:
    """True when there is no local user session to keep awake.

    From qwen-code, which skips inhibition under headless SSH for the same reason. Holding a
    machine awake that nobody is using changes nothing while hiding that nothing needed
    changing.
    """
    return bool(os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"))


def _read_state() -> "dict | None":
    """The hold we are tracking, or None. Never raises: state is a convenience, not a
    requirement - `--list` is the real witness."""
    try:
        return json.loads(_STATE_FILE.read_text())
    except (OSError, ValueError):
        return None


def _write_state(state: "dict | None") -> None:
    try:
        if state is None:
            _STATE_FILE.unlink(missing_ok=True)
            return
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(state))
    except OSError:
        # Losing the record costs us the ability to release early by pid, not the ability to
        # hold or to report. The bound still ends itself.
        pass


def _list_inhibitors() -> "tuple[bool, str]":
    """Read what is currently holding a sleep inhibitor. Returns (readable, output)."""
    try:
        result = subprocess.run(
            ["systemd-inhibit", "--list"],
            capture_output=True, text=True, timeout=_TIMEOUT, check=False)
    except OSError as exc:
        return False, f"systemd-inhibit could not be run ({exc})"
    except subprocess.SubprocessError as exc:
        return False, f"listing inhibitors timed out ({exc})"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return False, (f"systemd-inhibit --list exited {result.returncode}"
                       + (f": {detail[0]}" if detail else ""))
    return True, result.stdout


def _our_rows(stdout: str) -> "list[str]":
    """Rows of `systemd-inhibit --list` that are ours.

    The output is a fixed-width table with one line per inhibitor and no blank lines between
    them, so this matches lines rather than splitting on paragraphs. Matching on `_WHO` is
    safe because it is a string we chose, not a column we parsed.
    """
    return [line.strip() for line in stdout.splitlines() if _WHO in line and line.strip()]


def _other_rows(stdout: str) -> "list[str]":
    """The rows that are not ours, minus the header."""
    return [line.rstrip() for line in stdout.splitlines()
            if line.strip() and _WHO not in line and not line.strip().startswith("WHO")]


def _describe_ours(rows: "list[str]") -> str:
    return "\n".join(rows)


def _status() -> str:
    readable, out = _list_inhibitors()
    if not readable:
        return (f"Whether anything is holding sleep is UNKNOWN - {out}. That is not the "
                "same as nothing holding it.")
    ours = _our_rows(out)
    others = _other_rows(out)
    state = _read_state()
    expiry = ""
    if state and state.get("until"):
        expiry = f" Tracked hold lapses at {state['until']}."

    if ours:
        return (f"Chronoa IS holding sleep right now:\n\n{_describe_ours(ours)}\n"
                f"{expiry}\n\nIt lapses on its own when the bounded sleep holding it "
                "expires - nothing needs undoing, and killing Chronoa or rebooting "
                "releases it immediately.")
    tracked = ""
    if state and state.get("pid"):
        tracked = (f"\n\nA previous hold was recorded (pid {state['pid']}) but is not in "
                   "the list, so it has already lapsed.")
    if others:
        return (f"Chronoa is not holding sleep.{tracked} Something else is:\n\n"
                f"{chr(10).join(others)}\n\nThose are normal on a running desktop; this "
                "skill only reports inhibitors visible to this user, so one held by root "
                "or another session may not appear.")
    return (f"Chronoa is not holding sleep and nothing else in this user's list is "
            f"either.{tracked}\n\nThe machine will sleep on its usual schedule.")


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            f"holding the machine awake is turned off (enable '{_CONSENT_KEY}' in "
            "Settings). Reporting what is holding sleep right now needs no such "
            "permission. It has its own key rather than sharing "
            "'idle-timeout-enabled', because a bounded hold for the next few minutes is "
            "a different decision from changing the machine's idle policy."
        )
    return True, ""


def _hold(reason: str, seconds: int) -> str:
    if _headless():
        return ("Not held: this is a headless session, so there is no user to keep awake. "
                "Holding sleep here would change nothing while implying it had.")
    if shutil.which("systemd-inhibit") is None:
        return ("Sleep was NOT held: systemd-inhibit is not installed, so this machine "
                "cannot take a sleep lock this way. That is not the same as a hold that "
                "expired.")
    if seconds < _MIN_SECONDS:
        return f"Refused: {seconds}s is below the {_MIN_SECONDS}s minimum."
    if seconds > _MAX_SECONDS:
        return (f"Refused: {seconds}s is over the {_MAX_SECONDS}s maximum. An inhibition "
                "that can outlive the task that asked for it is a machine that will not "
                "sleep, and no setting would say why. Ask again, or take several holds.")

    args = ["systemd-inhibit", "--what=sleep", "--mode=block"]
    # Used when available, not required. See the module docstring: this systemd rejects the
    # flag, and a session-user hold needs no privilege, so refusing over it would disable the
    # skill on the machine it was written for.
    if _supports_no_ask_password():
        args.append("--no-ask-password")
    args += [f"--who={_WHO}", f"--why={_sanitize_reason(reason)}",
             _INHIBITED_COMMAND, str(seconds)]

    try:
        child = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
    except OSError as exc:
        return f"Sleep was NOT held: systemd-inhibit could not be started ({exc})."

    time.sleep(0.3)
    if child.poll() is not None:
        detail = ""
        if child.stderr is not None:
            try:
                detail = (child.stderr.read() or b"").decode(errors="replace").strip()
            except OSError:
                detail = ""
        first = detail.splitlines()[0] if detail else "no diagnostic"
        return (f"Sleep was NOT held: systemd-inhibit exited immediately ({first}). "
                "The machine is unchanged.")

    # The pid systemd-inhibit reports in --list is the inhibited command's, not the one we
    # spawned - it forks. Ours is the process group, and signalling it takes the lock with it.
    until = time.strftime("%H:%M:%S", time.localtime(time.time() + seconds))
    recorded = _sanitize_reason(reason)
    _write_state({"pid": child.pid, "until": until, "seconds": seconds,
                  "reason": recorded, "who": _WHO})

    readable, out = _list_inhibitors()
    ours = _our_rows(out) if readable else []
    note = ""
    if not _supports_no_ask_password():
        note = ("\n\nThis systemd-inhibit has no --no-ask-password, so polkit could in "
                "principle prompt. Taking a sleep lock as the session user needs no "
                "privilege and none was requested, so no dialog was raised.")
    if not readable:
        note += (f"\n\n--list could not be read to confirm ({out}), so this is reported "
                 "from having started the process only.")
    elif not ours:
        note += ("\n\nThe lock was not visible in --list a moment after starting, so the "
                 "hold may not have taken effect.")
    # The *recorded* reason, not the one asked for. If sanitising changed it, saying so
    # matters: a report that echoed the caller's text would claim a reason was recorded that
    # was not the reason systemd-inhibit holds.
    changed = "" if recorded == reason.strip() else (
        "  (control characters were removed and it was capped, so this is what "
        "systemd-inhibit actually holds)")
    return (f"Machine held awake until {until}, for {seconds}s.\n\n"
            f"Reason recorded: {recorded!r}\n{changed}\n"
            + (f"\nConfirmed by systemd-inhibit:\n{_describe_ours(ours)}\n" if ours else "")
            + note
            + "\n\nIt lapses on its own at that time - nothing needs undoing, and killing "
            "Chronoa or rebooting releases it immediately.")


def _release() -> str:
    state = _read_state()
    if not state or not state.get("pid"):
        readable, out = _list_inhibitors()
        if readable and not _our_rows(out):
            return "Nothing of ours is holding sleep, so nothing was released."
        return ("No hold of ours was recorded, so nothing was signalled. If "
                "systemd-inhibit still lists one, it is not from this skill.")

    pid = int(state["pid"])
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except ProcessLookupError:
        _write_state(None)
        return ("That hold had already lapsed - the process is gone - so there was "
                "nothing to release and the record is now cleared.")
    except (PermissionError, OSError) as exc:
        return (f"Could not signal the hold (pid {pid}): {exc}. It may still be active, "
                "and it will still lapse on its own when the bounded sleep ends.")

    time.sleep(0.3)
    readable, out = _list_inhibitors()
    _write_state(None)
    if not readable:
        return (f"Signalled the hold (pid {pid}), but systemd-inhibit could not be read to "
                f"confirm ({out}). The bound still ends on its own.")
    if _our_rows(out):
        return ("Signalled the hold, but systemd-inhibit still lists it. The machine may "
                "still be held awake; it will lapse when the bounded sleep ends regardless.")
    return ("Released. systemd-inhibit no longer lists a hold from Chronoa, confirmed by "
            "reading it back.")


SCHEMA = {
    "type": "function",
    "function": {
        "name": "set_sleep_inhibit",
        "description": (
            "Report what is currently holding the machine awake, or take a bounded hold so "
            "it does not sleep for a while, or release ours. Use for 'keep it awake while "
            "I...' - a long install, a backup, a test run. Scoped and self-lapsing; to "
            "change the machine's idle timeout permanently use set_screensaver instead. "
            "Gated by 'sleep-inhibit-enabled' consent key; reporting needs no such "
            "permission."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "'status' to report, 'hold' to take one, 'release' to "
                                   "drop ours. Defaults to status.",
                    "enum": ["status", "hold", "release"],
                },
                "reason": {
                    "type": "string",
                    "description": (
                        "Why the machine should stay awake; shown in systemd-inhibit's list "
                        "and the power panel. Only used by 'hold'."
                    ),
                },
                "seconds": {
                    "type": "integer",
                    "description": (
                        f"How long to hold, {_MIN_SECONDS}-{_MAX_SECONDS}. Only used by "
                        "'hold'. The hold always ends on its own at that point, even if "
                        "Chronoa is killed or the machine rebooted."
                    ),
                },
            },
            "required": [],
        },
    },
}


def _run(args: dict) -> str:
    action = args.get("action") or "status"
    if action == "status":
        return _status()

    allowed, reason_text = _consent(ChronoaConfig())
    if not allowed:
        return reason_text

    if action == "release":
        return _release()
    if action == "hold":
        seconds = args.get("seconds")
        if not isinstance(seconds, int) or isinstance(seconds, bool):
            return f"'seconds' must be a whole number of seconds; got {seconds!r}."
        reason = args.get("reason") or _DEFAULT_REASON
        if not isinstance(reason, str):
            return f"'reason' must be text; got {type(reason).__name__}."
        return _hold(reason, seconds)
    return f"Unknown action {action!r}."


SKILLS = [Skill(name="set_sleep_inhibit", schema=SCHEMA, run=_run)]
