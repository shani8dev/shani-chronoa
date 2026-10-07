"""What Chronoa did and may do: session approvals and the live tool-activity log."""

import json
import logging
from datetime import datetime
from typing import NamedTuple, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from shani_chronoa import capabilities, files, permissions, tool_tracking
from shani_chronoa.tool_tracking import ORIGIN_UNATTENDED
from shani_chronoa.verification import Verdict


logger = logging.getLogger(__name__)

#: A believable value for each resource argument, so the sample prompt on
#: screen reads like something a user would recognise rather than a placeholder.
#: Read by argument *name*, so a new scoped tool gets one without an edit.
_EXAMPLE_TARGETS = {
    "path": "/home/you/notes.txt",
    "unit": "nginx.service",
    "device": "/dev/sdb1",
}


# -- tool activity: the audit trail, made readable ----------------------------

#: A record's `verdict`, and the words it is allowed to have on screen.
#:
#: `None` is the one that gets mistreated. It means the call never reached
#: verification at all - a non-zero exit, or an exception - which is not the same
#: claim as "this did not take effect", and a panel that renders it as a failure
#: is telling the user something was checked and did not work when in fact
#: nothing checked it. `tests/test_tool_tracking.py` pins that distinction, and
#: pins the harder case behind it: two records whose `result` strings are
#: byte-identical with opposite verdicts.
#:
#: Keyed on `verification.Verdict`'s own values rather than on a second set of
#: literals, so a verdict added to that enum has to be answered here rather than
#: falling through to a confident-looking default.
_VERDICT_WORDS = {
    Verdict.VERIFIED.value: "verified to have taken effect",
    Verdict.FAILED.value: "verified not to have taken effect",
    Verdict.UNVERIFIED.value: "reported done, with nothing observing it",
    None: "never reached verification",
}

#: Rows built at most. A settings panel holding three hundred rows is a list
#: nobody scrolls, and the log behind this is unbounded by design, so the cap is
#: disclosed with `files.withheld_note` rather than applied silently - a list that
#: is quietly shortened reads as a complete one.
_TOOL_ROW_LIMIT = 40

#: Lines of the on-disk log read for history, and the ceiling on bytes read to
#: get them. That log is append-only and unbounded - 13.5 MB measured on the
#: machine this was written for - and this read happens on a GTK callback every
#: time the window is shown, so `read_text()` on it is not an option. Reading
#: forwards from a byte near the end costs the same whatever the file weighs, and
#: a log bigger than the cap is disclosed rather than quietly summarised.
_TOOL_LOG_TAIL_LINES = 400
_TOOL_LOG_TAIL_BYTES = 512 * 1024

#: Characters of any one free-text field in a row. `result` is the action's own
#: prose and `evidence` is a post-condition's own prose - either can be a whole
#: paragraph - so both are cut here and kept whole in the row's tooltip.
_TOOL_TEXT_CHARS = 90


def _verdict_words(verdict: Optional[str]) -> str:
    """The phrase for one verdict, including the ones we do not recognise."""
    if verdict in _VERDICT_WORDS:
        return _VERDICT_WORDS[verdict]
    return f"carries verdict {verdict!r}, which this panel does not recognise"


def _verdict_value(value) -> Optional[str]:
    """A verdict as a hashable str-or-None, whatever shape it arrived in.

    `None` stays `None` rather than becoming `""`, because `""` is a value the
    reader would be told to believe somebody wrote down.
    """
    if value is None:
        return None
    return value if isinstance(value, str) else repr(value)


def _text(value) -> str:
    """`value` as a string, or its `repr` when it is not one."""
    if value is None:
        return ""
    return value if isinstance(value, str) else repr(value)


def _number(value) -> Optional[float]:
    """`value` as a float, or None when it cannot be read as one.

    None rather than zero: this panel states durations, and "0 ms" for a
    duration nobody recorded is a measurement that was never taken.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _when(value) -> Optional[datetime]:
    """A recorded timestamp as a datetime, or None if it cannot be read.

    `None` rather than "now" - the one thing this panel must never invent is a
    time, because every ordering here and every "how long ago" follows from it. An
    offset-less ISO timestamp is read as local time, which is what it means.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _flatten(value) -> str:
    """`value` with every run of whitespace collapsed to one space.

    Newlines reach this window from a skill's own return value and from a
    post-condition's own message, and a raw newline in an `Adw.ActionRow`
    subtitle wraps at a width nobody chose. `split()` rather than `replace()`
    because evidence routinely carries an indented traceback, and half of that
    indentation is noise on a row.
    """
    return " ".join(_text(value).split())


def _clip(text: str, limit: int) -> str:
    """`text` cut to `limit` characters, marked when it was.

    A limit of zero or less means no cap, following `files.cap_list()`: every
    caller here passes a positive constant, and a zero that silently truncated
    would be the more dangerous of the two surprises. The mark is not
    decoration - it is what tells a reader the row is not showing everything, and
    the tooltip is where the rest is.
    """
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _args_digest(args, limit: int = _TOOL_TEXT_CHARS) -> str:
    """A call's arguments as one readable line, or "" when there are none.

    JSON for the same reason `ToolTracker.export_csv()` uses it: a logged
    argument can be bytes or a nested structure, and `str()` on one of those puts
    a Python repr in the middle of interface copy. `sort_keys` because two runs of
    the same call should read the same way.
    """
    if not args:
        return ""
    try:
        encoded = json.dumps(args, default=repr, sort_keys=True)
    except (TypeError, ValueError):
        encoded = repr(args)
    return _clip(_flatten(encoded), limit)


class _Call(NamedTuple):
    """One tool call as this panel needs it, from either source.

    The live tracker hands out `ToolCallRecord` objects and the log hands out
    plain dicts, so both are normalised into this here rather than in the
    renderer. One shape means one rendering path - a second path is where the two
    sources would quietly start disagreeing about the same event.
    """

    tool_name: str
    args: object
    result: object
    duration_ms: Optional[float]
    when: Optional[datetime]
    origin: str
    verdict: Optional[str]
    evidence: str
    #: True when this process recorded it. Only ever false for history, and the
    #: panel says so, because "shown here" and "done by the window you are
    #: looking at" are different claims.
    live: bool


class _LogTail(NamedTuple):
    """What could be read from the on-disk log, and what could not.

    Four separate fields rather than one value, because this panel has to tell a
    log that is not there from one it cannot read from one that is empty from one
    full of lines that are not records - and a user acts on those four
    differently. `readers of conversation_store.load` hit the same ambiguity: it swallows
    `OSError` at debug level and answers `[]`, so an empty list genuinely cannot
    say which of the two happened.
    """

    exists: bool
    size: int
    lines: list
    #: Started mid-file, so the log is larger than what was read.
    cut: bool
    #: "" unless the read itself failed.
    unreadable: str


def _log_tail(path) -> _LogTail:
    """The newest lines of the on-disk log, without reading all of it."""
    try:
        if not path.exists():
            return _LogTail(False, 0, [], False, "")
        size = path.stat().st_size
    except OSError as exc:
        # `exists()` can raise as well as answer False: a log directory the user
        # cannot search is not an absent log.
        return _LogTail(True, 0, [], False, str(exc))
    start = max(0, size - _TOOL_LOG_TAIL_BYTES)
    try:
        with path.open("rb") as handle:
            if start:
                handle.seek(start)
            blob = handle.read(_TOOL_LOG_TAIL_BYTES)
    except OSError as exc:
        return _LogTail(True, size, [], start > 0, str(exc))
    text = blob.decode("utf-8", "replace")
    if start:
        # Whatever was cut off the front is a fragment of a record, not a record.
        # Keeping it would put a guaranteed parse failure in front of every read
        # of a log larger than the cap, and this panel would then report a
        # corrupt log on a perfectly healthy one.
        text = text.split("\n", 1)[1] if "\n" in text else ""
    lines = list(files.iter_lines(text))
    if len(lines) > _TOOL_LOG_TAIL_LINES:
        lines = lines[-_TOOL_LOG_TAIL_LINES:]
    return _LogTail(True, size, lines, start > 0, "")


def _live_calls() -> tuple:
    """The in-memory ring, newest first, and why it is empty if it could not be read.

    `tools._TRACKER` and never a `ToolTracker()` of our own: the ring buffer is
    per-instance, so a fresh one answers `[]` however full the real one is, and
    this panel would then claim no action had ever been taken by a process that
    had just taken some. The singleton is the only object holding what
    `execute_tool()` recorded.
    """
    try:
        from shani_chronoa import tools
        records = list(tools._TRACKER.get_calls())
    except Exception as exc:  # noqa: BLE001 - an unreadable ring is not a blank panel
        logger.debug("tool activity: the live tracker is unreadable: %s", exc)
        return [], str(exc)
    return [
        _Call(
            tool_name=_text(record.tool_name),
            args=record.args,
            result=record.result,
            duration_ms=_number(record.duration_ms),
            when=record.timestamp if isinstance(record.timestamp, datetime) else None,
            origin=_text(record.origin),
            verdict=_verdict_value(record.verdict),
            evidence=_text(record.evidence),
            live=True,
        )
        for record in records
    ], ""


def _calls_from_log(lines) -> tuple:
    """Records from JSONL log lines, and how many lines were not records.

    One `to_log_dict()` per line, appended by a single write, so the only lines
    that ought to fail are a partial final line from a process killed mid-append.
    They are counted rather than dropped: a log that has stopped being readable
    and a log that is empty mean very different things, and this panel is the
    only place either is visible.

    A line that parses as JSON but is not a record is rejected for the same
    reason. `to_log_dict()` writes all eight keys every time, so a line without
    them was not written by `record_call()` - and reading the absent `verdict`
    as `None` would then display it as "never reached verification", which is a
    claim about an action rather than about a line of text. Two keys are checked
    rather than all eight so a writer that adds a field later is not turned into
    unreadable history.
    """
    calls, rejected = [], 0
    for line in lines:
        try:
            raw = json.loads(line)
        except ValueError:
            rejected += 1
            continue
        if not isinstance(raw, dict) or not {"tool_name", "verdict"} <= raw.keys():
            rejected += 1
            continue
        calls.append(_Call(
            tool_name=_text(raw.get("tool_name")),
            args=raw.get("args"),
            result=raw.get("result"),
            duration_ms=_number(raw.get("duration_ms")),
            when=_when(raw.get("timestamp")),
            origin=_text(raw.get("origin")),
            verdict=_verdict_value(raw.get("verdict")),
            evidence=_text(raw.get("evidence")),
            live=False,
        ))
    return calls, rejected


def _call_key(call: _Call) -> tuple:
    """What says "the same call, from two sources".

    Every `record_call()` appends to the ring *and* to the log, so the newest
    calls arrive twice and one event must be shown once - a duplicated row is
    indistinguishable from two actions that really happened, which is the one
    failure this panel must not have.

    Keyed on the timestamp, the tool and the duration rather than on the
    serialised line, because the log *caps* argument values: `_loggable()`
    replaces anything past 4096 characters of JSON with a stand-in, so a byte
    comparison would fail to match exactly the calls carrying a large payload -
    the ones most worth showing once. The deliberate cost is that two distinct
    calls to one tool, in the same microsecond, taking the same number of
    milliseconds collapse into a single row. That is rarer than a call with a
    large argument, so it is the trade this makes.
    """
    when = call.when.isoformat() if call.when else ""
    return (when, call.tool_name, call.duration_ms)


def _merge_calls(live, history) -> list:
    """Live and historical calls together, newest first, overlap removed.

    Live first on purpose: where the two sources disagree - and for a capped
    argument they can, because the ring holds the real value and the log holds a
    stand-in - the copy that keeps the argument is the one shown.
    """
    merged, seen = [], set()
    for call in list(live) + list(history):
        key = _call_key(call)
        if key in seen:
            continue
        seen.add(key)
        merged.append(call)
    # `.timestamp()` rather than the datetimes themselves: a hand-written log
    # line can carry an offset-less timestamp, and sorting that against the aware
    # timestamps the tracker writes raises TypeError - inside a GTK callback,
    # where nothing reports it and the panel just quietly stops updating.
    merged.sort(key=lambda c: (c.when is None, -(c.when.timestamp() if c.when else 0.0)))
    return merged


class ActivityPage:
    """What Chronoa did and may do: session approvals and the live tool-activity log. - a part of SettingsWindow, which mixes it in."""


    # -- the approval policy, as a view rather than a second dialog -----------

    def _build_approvals(self, page) -> None:
        """Show what an approval question is and what answering it would permit.

        Every row here is either read live from `permissions.py` or generated
        from the same constants the runtime prompt is composed from, so this
        page cannot describe a policy the app does not enforce. The only control
        is one that revokes - forgetting this session's answers - because a
        user who has over-trusted needs the un-grant and has no other way to
        reach it; there is deliberately no switch here that *grants* anything,
        since every grant in this app belongs to a consent-key row above and a
        second path to one would be two switches disagreeing.
        """
        self._section_family = "approvals"
        self._approvals_page = page
        group = self._group(
            page, "Approvals",
            "What happens when Chronoa needs a permission it does not have. The "
            "question appears in the main window, not here.",
        )
        self._info_row(group, "What a question looks like",
                       self._example_request().question)

        # Two questions, not one, and they have different answers. `can_ask()` is
        # whether anybody is *there*; `allows_prompting()` is whether asking is a
        # thing that may happen in the mode we are in right now. DONT_ASK refuses
        # the question on the spot whether or not someone is watching, so reading
        # only `can_ask()` said "Chronoa will ask before running a gated action"
        # on a machine set to never ask anything - which is the panel describing
        # a policy the app does not enforce.
        asking = permissions.can_ask()
        prompting = permissions.allows_prompting()
        if not prompting:
            who = ("No questions are asked in the current permission mode. A gated "
                   "action is refused rather than asked about, whatever is on "
                   "screen.")
        elif asking:
            who = ("Somebody is listening - Chronoa will ask before running a "
                   "gated action.")
        else:
            who = ("Nobody is listening. A gated action is refused rather than "
                   "asked about, and the refusal names the switch that would "
                   "allow it. This is the state a headless run and a trigger "
                   "rule that fires unprompted are always in.")
        self._info_row(group, "Who can answer", who)

        self._info_row(
            group, "How long a question waits",
            f"{int(permissions.DECISION_TIMEOUT_SECONDS)} seconds, then it is "
            "treated as no. An unanswered question is never an allow.",
        )

        for action, key in sorted(capabilities.GATED.items()):
            title = capabilities.tool_title(action)
            self._info_row(group, title,
                           self._scope_sentence(action, key))
            # A consent-key name ('hwmon-sense-enabled') in the subtitle would
            # keep this row matching a sense search, which would leave the Ap
            # page visible for searches like 'hwmon' that are meant to locate
            # the physical switch, not a policy explanation. Use the row's title
            # for search matching of these static explanatory rows.
            group._needle_extra[-1] = (group._needle_extra[-1][0], title.lower())

        self._forget_group = self._group(
            page, "Answers given this session",
            "Grants and refusals recorded while Chronoa has been running. All of "
            "them die when it quits; none of them is written to disk.",
        )
        self._approvals_state = None
        self._render_session_answers()

    def _example_request(self) -> permissions.ApprovalRequest:
        """A real composed prompt, built from a capability that actually exists.

        Picked from `capabilities.GATED` and `tools._RESOURCE_ARGUMENT` rather
        than written out, so the sample on screen is the prompt a gated tool
        would really produce - including the fact that a scoped one enumerates
        one target while an unscoped one does not.
        """
        for action, key in sorted(capabilities.GATED.items()):
            argument = self._resource_argument(action)
            if argument is None:
                continue
            return permissions.approval_request(
                action, _EXAMPLE_TARGETS.get(argument, f"a {argument}"),
                key, capabilities.tool_title(action).lower())
        return permissions.approval_request(
            next(iter(sorted(capabilities.GATED)), "an action"), None,
            "a permission")

    def _scope_sentence(self, action: str, key: str) -> str:
        """What 'allow for this session' would cover, for one capability.

        Read from `permissions.always_patterns()` rather than described here, so
        the pattern named on screen is the pattern `add_rule()` writes.
        """
        patterns = permissions.always_patterns(action, None)
        wildcard = patterns[0][1]
        target = self._resource_argument(action)
        if target is None:
            return (f"Needs '{key}'. If you allow it for the session it covers "
                    f"every {action} until Chronoa quits, because this action "
                    f"names no specific target.")
        return (f"Needs '{key}'. If you allow it for the session it is written "
                f"against '{target}' - a {target} value with a wildcard in it "
                f"would widen the grant, and '{wildcard}' would widen it to "
                f"everything.")

    def _resource_argument(self, action: str):
        """Which argument names this tool's target, or None if it names none.

        Read from `tools._RESOURCE_ARGUMENT` because that is the table the
        dispatch path actually uses to build the grant, so a hand-kept copy here
        would drift from the permission it describes.
        """
        try:
            from shani_chronoa import tools
            return tools._RESOURCE_ARGUMENT.get(action)
        except Exception:  # noqa: BLE001 - an unreadable table is not a blank page
            return None

    def _session_state(self):
        return (tuple(permissions.rules()), permissions.can_ask())

    def _render_session_answers(self) -> None:
        group = self._forget_group
        for row in getattr(group, "_rows", []):
            group.remove(row)
        group._needle_extra = []
        rows = []
        rules = permissions.rules()
        said = permissions.reasons()

        if not rules:
            self._info_row(group, "Nothing has been allowed or refused yet",
                           "The first time Chronoa needs a permission, it will "
                           "ask rather than refuse.")
            rows.append(group._needle_extra[-1][0])
        for action, pattern, decision in rules:
            verdict = {
                permissions.Decision.ALLOW_ONCE: "allowed once",
                permissions.Decision.ALLOW_SESSION: "allowed for the session",
                permissions.Decision.DENY_ONCE: "refused",
                permissions.Decision.DENY_SESSION: "refused",
                permissions.Decision.CANCEL: "refused, and the turn was stopped",
            }.get(decision, decision)
            scope = f"on {pattern}" if pattern != "*" else "on any target"
            reason = said.get((action, pattern), "")
            row = self._info_row(
                group, f"{action} {scope} - {verdict}",
                f"You said: {reason}" if reason else
                f"Pattern on record: {action} / {pattern}",
            )
            rows.append(row)

        if rules:
            forget = Adw.ActionRow(
                title="Forget these answers",
                subtitle=f"Forgets the {len(rules)} answer(s) above so the next "
                         "time asks again. It does not change any switch.",
                activatable=True,
            )
            forget.update_property(
                [Gtk.AccessibleProperty.LABEL],
                ["Forget every permission answer given this session"],
            )
            forget.connect("activated", self._on_forget_answers, list(rules))
            group.add(forget)
            group._needle_extra.append(
                (forget, "forget answers revoke clear".lower()))
            rows.append(forget)

        group._rows = rows
        self._approvals_state = self._session_state()

    def _on_forget_answers(self, _row, rules: list) -> None:
        dialog = Adw.AlertDialog(
            heading="Forget these answers?",
            body=("Chronoa will ask again about:\n\n"
                  + "\n".join(
                      f"  •  {action} on {pattern}"
                      for action, pattern, _decision in rules)
                  + "\n\nNo switch changes. Nothing was written to disk, so this "
                    "only forgets what is in memory right now."),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("forget", "Forget them")
        dialog.set_response_appearance("forget",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_forget_response)
        dialog.present(self.get_root() or self)
        self._forget_dialog = dialog

    def _on_forget_response(self, _dialog, response: str) -> None:
        if response != "forget":
            return
        permissions.clear(session_only=True)
        self._render_session_answers()

    # -- tool activity, as a view of the audit trail ---------------------------

    def _build_tool_activity(self, page) -> None:
        """What Chronoa has actually run, and whether it took effect.

        The live session plus a capped tail of the on-disk log, rebuilt when
        either moved. There is deliberately no consent gate here and none is
        added: `privacy-mode` governs what leaves this machine, and this reads a
        file the app itself wrote on this machine, in this account, for the same
        purpose that file already exists. Whether a user may *read their own*
        history is a product decision, not a bug fix - and a gate here would be a
        second kind of refusal that the window's own docstring says it has none
        of.
        """
        self._section_family = "tool-activity"
        self._tool_group = self._group(
            page, "Tool activity",
            "Every action Chronoa has run, newest first, led by whether it took "
            "effect rather than by what the action claimed. \"Never reached "
            "verification\" is not a failure verdict: the action exited non-zero "
            "or raised before anything could check it. \"Unattended\" means an "
            "armed rule fired it without being asked. Long text is shortened "
            "here; hover a row for all of it.",
        )
        self._tool_group.add_css_class("tool-activity-group")
        self._tool_state = None
        self._render_tool_activity()

    def _tool_activity_state(self):
        """Enough to notice a change, cheap enough for a visibility callback.

        No file is opened to ask: a 13.5 MB log read on every show/hide of a
        window is the thing this avoids. The tracker's count is `-1` when it
        cannot be read rather than `0`, because "the ring is empty" and "the ring
        is unreadable" need different copy and would otherwise compare equal and
        never re-render.
        """
        try:
            from shani_chronoa import tools
            live = len(tools._TRACKER.get_calls())
        except Exception:  # noqa: BLE001 - an unreadable ring is not a blank panel
            live = -1
        try:
            stat = tool_tracking.LOG_FILE.stat()
            log = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            log = None
        return (live, log)

    def _tool_activity_view(self):
        """(calls newest first, notes) for the panel.

        Every note here is a state this panel can genuinely *tell apart*. The
        tempting version - "no tool calls recorded" whenever the list is empty -
        is a confident wrong answer in three of the four ways it can be reached: no
        log, an unreadable log, and a log full of lines that are not records are
        three different problems and the user acts on them differently. The
        fourth, an empty ring, is genuinely ambiguous - the ring is also empty on
        a machine that ran actions in an earlier process - so that copy says what
        it cannot know rather than claiming nothing has happened.
        """
        live, live_problem = _live_calls()
        tail = _log_tail(tool_tracking.LOG_FILE)
        history, rejected = _calls_from_log(tail.lines)
        calls = _merge_calls(live, history)
        notes = []

        if live_problem:
            notes.append((
                "This process's own calls are not visible",
                f"The in-memory record could not be read ({live_problem}). "
                f"Everything below comes from the log file instead, so an action "
                f"this process takes while this window is open may not appear in "
                f"it.",
            ))
        if tail.unreadable:
            notes.append((
                "The log is there, and could not be read",
                f"{tail.unreadable}. That is a read failure rather than an "
                f"absence of calls, and nothing from earlier runs is shown.",
            ))
        elif not tail.exists:
            notes.append((
                "No log file on disk",
                f"There is nothing at {tool_tracking.LOG_FILE}, so this panel "
                f"cannot say whether any action has been taken: a run that ended "
                f"before this window opened leaves nothing behind to read."
                + ("" if live else " The log may never have been created, or it "
                   "may have been moved or deleted - neither is visible from "
                   "here."),
            ))
        elif not tail.lines:
            notes.append((
                "The log is there and empty",
                "Zero bytes. Either nothing has been recorded since it was "
                "created or it was truncated, and a zero-length file cannot tell "
                "those two apart.",
            ))
        elif not history:
            notes.append((
                "The log holds lines, and none is a record",
                f"{len(tail.lines)} line(s) read and not one parsed as a tool "
                f"call. A partial final line from a process killed mid-write is "
                f"the ordinary cause; this panel does not assume that.",
            ))

        if calls:
            shown, withheld = files.cap_list(calls, _TOOL_ROW_LIMIT)
            if withheld:
                notes.append((
                    "This is not the whole trail",
                    files.withheld_note(
                        "earlier call", withheld,
                        "The log file on disk holds all of them; this panel reads "
                        "and lists a bounded tail of it."),
                ))
            if tail.cut:
                notes.append((
                    "Only the end of the log was read",
                    f"The log weighs {files.human_size(tail.size)} and the newest "
                    f"{files.human_size(_TOOL_LOG_TAIL_BYTES)} of it were read, so "
                    f"older calls are neither shown nor counted.",
                ))
            if rejected:
                notes.append((
                    "Some log lines could not be read",
                    f"{rejected} of the last {len(tail.lines)} line(s) did not "
                    f"parse as a record and are not counted here.",
                ))
            if not live and not live_problem:
                notes.append((
                    "None of these were recorded by this process",
                    "Every row below was read from the log file, so none of it is "
                    "something this window just did.",
                ))
            calls = shown
        return calls, notes

    def _tool_call_row(self, group, call: _Call):
        """One call, led by its verdict rather than by its result.

        The order is the whole point and it is not a style preference. `result`
        is the action's own account of what it did, so for an action that did not
        take effect it reads "All done successfully." beside `verdict="failed"` -
        `tests/test_tool_tracking.py` pins two records with a byte-identical
        result and opposite verdicts for exactly that reason. Leading with the
        result would draw those two rows identically, which is the one thing an
        audit trail must not do.
        """
        verdict = _verdict_words(call.verdict)
        title = f"{_clip(_flatten(call.tool_name), 48)} - {verdict}"
        if call.origin == ORIGIN_UNATTENDED:
            # The origin field exists so a user-initiated action and an armed rule
            # firing unprompted are told apart; dropping it here would throw away
            # the only reason that field was added.
            title += f" ({ORIGIN_UNATTENDED})"

        bits = [call.when.astimezone().strftime("%H:%M:%S") if call.when
                else "time unreadable"]
        bits.append(f"{call.duration_ms:.0f} ms" if call.duration_ms is not None
                    else "duration unreadable")
        digest = _args_digest(call.args)
        if digest:
            bits.append(f"args {digest}")
        said = _clip(_flatten(call.result), _TOOL_TEXT_CHARS)
        if said:
            bits.append(f'said "{said}"')
        seen = _clip(_flatten(call.evidence), _TOOL_TEXT_CHARS)
        if seen:
            bits.append(f'observed "{seen}"')

        row = Adw.ActionRow(title="")
        # Before any text goes in. Adw parses a row's title and subtitle as Pango
        # markup, and what lands here is a skill's prose and a post-condition's
        # prose - verified on the installed libadwaita 1.5 that an unescaped `&`
        # raises a Gtk-WARNING and leaves the label rendering *empty*, so the row
        # silently loses its whole text rather than raising. Escaping was the
        # other option; not parsing is one call and cannot be half-done.
        row.set_use_markup(False)
        row.set_title(title)
        row.set_subtitle(" - ".join(bits))
        row.add_css_class("tool-call-row")
        row.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [f"{title}, {('run by an armed rule' if call.origin == ORIGIN_UNATTENDED else 'asked for')}"],
        )
        tip = " | ".join(filter(None, [
            f"args: {_args_digest(call.args, 0)}" if call.args else "",
            f"result: {_flatten(call.result)}" if _text(call.result) else "",
            f"evidence: {_flatten(call.evidence)}" if call.evidence else "",
            f"verdict on record: {call.verdict!r}",
            f"origin on record: {call.origin!r}",
        ]))
        if tip:
            row.set_tooltip_text(tip)
        group.add(row)
        # The needle carries the verdict wording, the argument names and the tool
        # name, so someone who remembers what they asked for, or the argument they
        # passed, finds the row by either.
        group._needle_extra.append((row, f"{title} {digest} {call.evidence}".lower()))
        return row

    def _render_tool_activity(self) -> None:
        """Rebuild the panel's rows, as `_render_session_answers` does."""
        group = self._tool_group
        for row in getattr(group, "_rows", []):
            group.remove(row)
        group._needle_extra = []
        calls, notes = self._tool_activity_view()
        rows = [self._info_row(group, title, subtitle) for title, subtitle in notes]
        for call in calls:
            rows.append(self._tool_call_row(group, call))
        group._rows = rows
        self._tool_state = self._tool_activity_state()
