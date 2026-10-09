"""Entry point for the assistant's tool-calling pipeline.

Loads local system "skills" the LLM can invoke to actually get things done
(open an app, adjust volume, set a timer, check the battery, search the web)
from `shani_chronoa/skills/` - deliberately a fixed, whitelisted set of
actions rather than a generic shell-command tool, since that would let the
LLM run anything. Built-in skills live in `shani_chronoa/skills/`; a user can
add their own by dropping a module with the same contract (see
`skills/__init__.py`) into `~/.config/shani-chronoa/skills/`.

All tool execution is routed through the 5-level SandboxExecutor
(LEVEL_0_NO_EXEC → LEVEL_4_HOST_ROOT), blocking privilege escalation,
dangerous binaries, and GUI access from sandboxed levels — adapting the
pattern from sayri/adapters/sandbox/executor.py.

Arguments reach the child two ways. Small, JSON-representable ones are
JSON-interpolated into the `python3 -c` program source below, which is what
every built-in skill's arguments look like. A `bytes` argument cannot be
carried that way at all, so it is passed by reference instead: the value goes
to a private temp file, the child is handed the path as a `FileRef`, and it
runs under a fixed program with no interpolation of anything at all. A
caller can ask for the same treatment of a merely oversized argument with
`by_reference=True`; nothing else changes transport, so no existing skill
moves (`argfile.py` documents both, and the ceilings there were measured
against real subprocesses, not assumed).
"""

import importlib
import json
import logging
import threading
import time
from collections import OrderedDict

from shani_chronoa import (argfile, capabilities, config as config_mod,
                       guardrail, permissions, planmode, verification)
from shani_chronoa import outcome_model
from shani_chronoa.reaction import ReactionLayer, destructive_tools
from shani_chronoa.sandbox import SandboxConfig, SandboxExecutor, SandboxLevel
from shani_chronoa.skills import discover_skills
from shani_chronoa.tool_tracking import ToolTracker, ORIGIN_USER
from typing import Dict, NamedTuple, Tuple

logger = logging.getLogger(__name__)

TOOLS: list[dict]
_HANDLER_FNS: dict
TOOLS, _HANDLER_FNS = discover_skills()

#: Tools that run in this process instead of the sandbox subprocess, because
#: what they do cannot cross that boundary. `ask_user` has to put a question in
#: the window the user is looking at and block until they answer.
#:
#: `browse` is here so it can drive the in-app browser window the person is
#: watching, which lives on this process's GTK main thread and cannot be reached
#: from a sandbox child. It is not a shell: its in-app path is a fixed set of
#: page actions (`gui/browser_driver.py`), holds every navigation it causes to
#: the egress policy and runs no arbitrary script. And it is local *only while
#: that window can exist* - see `_runs_locally` - so with no GUI (the MCP
#: server, a headless run) its headless Chromium stays in the sandbox.
#:
#: This is a privilege list. Anything named here is unsandboxed, so it stays a
#: short explicit set rather than anything derived - a rule that decided this
#: automatically would widen the exemption without anyone reviewing it.
_LOCAL_TOOLS = frozenset({"ask_user", "browse"})


def _runs_locally(name: str) -> bool:
    """Whether `name` runs in this process right now (see `_LOCAL_TOOLS`)."""
    if name not in _LOCAL_TOOLS:
        return False
    if name == "browse":
        from shani_chronoa import browser_bridge
        return browser_bridge.has_provider()
    return True


# Module-level sandbox executor singleton
_SANDBOX = SandboxExecutor()

# Module-level tool-call audit trail singleton (last 100 calls in memory,
# full history appended to ~/.local/share/shani-chronoa/logs/tool_calls.log)
_TRACKER = ToolTracker()

logger.info(f"Loaded {len(_HANDLER_FNS)} skill(s): {', '.join(sorted(_HANDLER_FNS)) or '(none)'}")


#: Tools whose real work takes longer than the usual 30 s, named here rather
#: than guessed. The per-origin profile still caps each (300 s when a person
#: asked, 15 s for an unattended rule), so this can only lengthen up to that.
_SLOW_TOOLS = {"generate_image": 300, "photo_video": 300, "scan_document": 180, "recording": 300, "photos": 300,
               # 100 MB at a slow link; a long PDF merge; a polkit password prompt left open.
               "speed_test": 180, "pdf_pages": 150, "set_hostname": 90, "set_locale": 90,
               # A wrist measurement takes up to a minute after a ~5 s connect, and
               # `listen` accepts up to 60 s: both were killed at the 30 s default.
               "watch": 150, "bluetooth_gatt": 100, "find_device": 60}


def _get_sandbox_config(tool_name: str) -> SandboxConfig:
    """Determines the sandbox level for a given tool.

    Uses LEVEL_3_HOST_USER as default. Level 0 (no exec) is reserved
    for tools that are explicitly prohibited. Level 4 is only used
    when the tool name explicitly requires elevation.
    """
    # Tools that must never execute (LEVEL_0)
    blocked_tools: frozenset[str] = frozenset()

    # Tools that require host-root elevation (LEVEL_4)
    elevated_tools: frozenset[str] = frozenset()

    if tool_name in blocked_tools:
        return SandboxConfig(level=SandboxLevel.LEVEL_0_NO_EXEC, timeout_seconds=5)
    if tool_name in elevated_tools:
        return SandboxConfig(level=SandboxLevel.LEVEL_4_HOST_ROOT, timeout_seconds=60)
    return SandboxConfig(level=SandboxLevel.LEVEL_3_HOST_USER, timeout_seconds=_SLOW_TOOLS.get(tool_name, 30))


def _json_safe(value):
    """Coerce a decoded argument tree to types `repr` renders as Python source.

    Arguments arrive from JSON, so they are already only str/int/float/bool/
    None/list/dict - except that `default=str` used to be passed to
    `json.dumps`, which implies non-JSON types can reach here, and a skill
    argument is untrusted input from a model. Anything else becomes its `str`
    so the child program still compiles.
    """
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class DispatchResult(NamedTuple):
    """What a dispatch actually did, as data rather than as prose.

    `ran` is the field that was missing. Everything Chronoa returned was a
    string, so "the tool ran and the answer is X" and "the tool never ran" were
    the same shape, and a caller - or the model - had to infer which from
    wording. goose carries this as `is_error` on the tool response for the same
    reason.

    Read with `verdict`, it is two bits rather than one:

    - `ran=True,  VERIFIED`   - ran, and the post-condition held
    - `ran=True,  UNVERIFIED` - ran, and there is nothing to check it against
    - `ran=True,  FAILED`     - ran, and the post-condition did not hold
    - `ran=False, UNVERIFIED` - **never executed**: unknown tool, refused by
      plan mode, non-zero exit, or an exception

    The last row is the one that was previously indistinguishable from the
    second. A consent refusal still lands in row two, because the refusal
    happens inside the skill and the dispatch only sees exit 0 - making that
    structural means changing all 69 skills, so it is noted rather than faked.
    """

    text: str
    verdict: verification.Verdict
    ran: bool
    evidence: str = ""

    @property
    def is_error(self) -> bool:
        """goose's spelling: did this fail to produce an answer."""
        return not self.ran


from shani_chronoa.toolfailure import ToolFailure


def _tool_failure_result(message: str) -> "DispatchResult":
    return DispatchResult(
        f"ERROR (ran, but failed): {message}",
        verification.Verdict.FAILED, True, evidence="tool_failure")



#: Which argument names the resource a skill would touch, so a permission rule
#: can be written against a path or a unit rather than against the tool alone.
#: Skills not listed fall through with `None` and are unaffected by rules.
_RESOURCE_ARGUMENT = {
    "delete_file": "path",
    "trash_file": "path",
    "move_or_copy_file": "source",
    "write_text_file": "path",
    "read_text_file": "path",
    "find_files": "root",
    "search_file_contents": "root",
    "find_and_replace": "path",
    "extract_archive": "archive",
    "list_directory": "path",
    "manage_mount": "device",
    "control_service": "unit",
    "print_file": "path",
    "open_file": "path",
    # The file-touching skills added 2026-09-30. An entry here is what lets a
    # resource-scoped permission rule pre-filter them; without it they run.
    "edit_file": "path",
    "undo_last_change": "path",
    "get_file_info": "path",
    "directory_tree": "path",
    "find_recently_modified": "path",
    "git_inspect": "path",
    "photo_metadata": "path",
    # pdf_pages' merge takes `paths` (a list) and its output is a new file; only
    # the single-source actions name one path, so that is the one scoped here.
    "pdf_pages": "path",
    # `compare_files` is deliberately absent: it takes `path_a` AND `path_b`,
    # and this maps one skill to one argument. Registering either would let a
    # scoped rule match on half the files the call actually reads, which is
    # worse than not scoping it here at all.
}


def _resource_for(name: str, arguments: dict):
    """The path or unit this call would touch, or None if it is not scoped."""
    if not isinstance(arguments, dict):
        return None
    key = _RESOURCE_ARGUMENT.get(name)
    if not key:
        return None
    value = arguments.get(key)
    return str(value) if value else None


def _schema_for(name: str) -> "dict | None":
    """The declared parameter schema for a tool, or None if it declares none."""
    for entry in TOOLS:
        function = entry.get("function") or {}
        if function.get("name") == name:
            return function.get("parameters") or None
    return None

def _consent_key_for(name: str) -> "str | None":
    """The consent key gating this tool, or None if it is not gated.

    Read from the live tool schema rather than a second hand-kept list, so a
    skill that starts declaring a consent key is honoured the moment its
    description says so - the same reason `capabilities.gated_by()` treats the
    description as the more current of its two sources.
    """
    for entry in TOOLS:
        function = entry.get("function") or {}
        if function.get("name") == name:
            return capabilities.gated_by(name, function.get("description", ""))
    return None


# ---------------------------------------------------------------------------
# Replay at the approval boundary.
#
# Approval is exactly where a duplicate comes from. Two of the three ways a
# person can say yes in Chronoa are reachable without the window focused -
# `approvals.py`'s `notify-send --action`, and the gateway's worker-thread call -
# so a notification pressed twice, or a client that reconnects and replays its
# last action after a timeout that actually succeeded, runs the same call twice.
#
# This is the mechanism `harness-study/digital-travel-agent` uses at its own
# `confirm_booking`: derive a key from *what makes the action the same action*
# and answer a replay with the receipt rather than creating a second thing.
# `grep -rn idempot usr/ tests/` found no such thing here before this - only
# `idempotent_hint` on the MCP tool descriptions, which is a claim to a client
# and not a control.
#
# **Placed after the consent decision, not before it.** The person is asked
# again, every time, which is what `permissions.is_bypass_immune` promises for
# a destructive tool; what is skipped is the *second action*, not the second
# question. A replay that skipped the prompt would quietly undo two properties
# at once - that "allow once" means once, and that a session grant never answers
# for `delete_file`.
# ---------------------------------------------------------------------------

#: How long a completed approval stays replayable. A double-click, a retried
#: request or a reconnecting client all land within seconds; a minute later,
#: somebody asking again means it again. Deliberately short and written down as a
#: number, because a window nobody chose is a window nobody can reason about.
REPLAY_TTL_SECONDS = 60.0

#: Bounded on purpose. An unbounded map of completed actions is a leak that only
#: shows up on a long-lived background-mode daemon.
REPLAY_CAPACITY = 64

#: Only `VERIFIED` is replayable, and that is a much narrower rule than it
#: looks. `DispatchResult` cannot distinguish a skill that *ran and succeeded*
#: from one that *ran and refused* - both are `ran=True, UNVERIFIED`, and its own
#: docstring says so: "A consent refusal still lands in row two, because the
#: refusal happens inside the skill and the dispatch only sees exit 0 - making
#: that structural means changing all 69 skills, so it is noted rather than
#: faked."
#:
#: Caching the ambiguous cell would mean a retry after a refused call - after the
#: person grants the permission, or a transient condition clears - comes back
#: with the stale refusal instead of running. **A stale refusal is worse than no
#: replay protection at all**, so `VERIFIED` is the whole rule.
#:
#: What that costs, measured on this tree: **12 of the 62 gated tools** have a
#: post-condition and can therefore reach `VERIFIED` -
#: `airplane_mode`, `control_service`, `default_apps`, `delete_file`,
#: `desktop_setting`, `kill_process`, `office_document`, `print_queue`,
#: `set_hostname`, `set_locale`, `take_photo`, `toggle_wifi` - of which four are
#: also classified destructive. The other 50 are not replay-protected, and
#: `install_app`, `power_action`, `move_pointer`, `click_pointer`, `type_text`
#: and `connect_wifi` are among them. That is the honest coverage, recorded here
#: rather than implied by the feature's presence.
#:
#: What would widen it, not built here: a refusal marker in the child, as a
#: sibling to `ToolFailure.MARKER`. That is a change across the skill set, and
#: this file's own rule is that a boundary is noted rather than faked.
_REPLAYABLE_VERDICTS = (verification.Verdict.VERIFIED,)

#: Marks a result that came from the cache rather than from a run. Leading, and
#: `startswith`, so the one place that must not re-record a replay can recognise
#: it structurally. See the return in `_dispatch_inner`.
_REPLAY_NOTE_PREFIX = "[Not run again: "

_REPLAYS: "OrderedDict[tuple, tuple]" = OrderedDict()
_REPLAY_LOCK = threading.Lock()


def _replay_key(name: str, arguments, origin: str):
    """What makes two calls the same call, or None if this one is not a candidate.

    Four things are in the key and each earns its place:

    - **the tool name**, or `delete_file` and `trash_file` collide;
    - **the arguments**, canonicalised with sorted keys so `{"a":1,"b":2}` and
      `{"b":2,"a":1}` are one call rather than two. `repr` is the fallback for a
      value json cannot serialise, so an exotic argument makes the key
      conservative instead of raising - a check that raised here would be a check
      that had stopped the tool it exists to protect;
    - **the origin**, because a trigger-driven turn and a typed one reaching the
      same arguments are two different decisions. The gateway's grant is keyed on
      channel for the same reason;
    - **the consent key's state right now.** Found by running, not by reasoning:
      `tests/test_matrix_skills.py::test_set_locale_refuses_ungenerated_and_ungranted`
      failed against this feature, because an earlier test in the same file ran
      the *same* `set_locale` call with the key granted and verified, and the
      refusal test was then answered with that receipt - a test asserting "this
      is refused" reading "this is done". It is the same defect in the product
      that it is in the test: **the permission changed between the two calls**, so
      they are not the same call. A receipt must not survive the state that
      authorised it, and the state that produced it is the key's value.

    Not a candidate unless the tool is consent-gated, for two reasons. The
    finding this answers is about approval; and an ungated read answered from
    the cache would be a stale value wearing a fresh timestamp - `get_datetime`
    replayed a minute later reporting last minute's time.

    Whether the result is *cacheable* is a separate question, answered by
    `_REPLAYABLE_VERDICTS` above, and it is not this function's business. A
    config read that cannot happen yields `None`, which makes the key unique per
    call - the conservative direction, since a key nobody can reproduce is a
    replay nobody can be served.
    """
    consent_key = _consent_key_for(name)
    if consent_key is None:
        return None
    if not isinstance(arguments, dict):
        return None
    try:
        canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"),
                               default=repr)
        permitted = bool(config_mod.ChronoaConfig().get_bool(consent_key, False))
    except Exception:  # noqa: BLE001 - a key we cannot build is a call we let run
        return None
    return (name, canonical, origin, consent_key, permitted)


def _replay_get(key: tuple):
    """The receipt for a key, or None when there is none or it has expired."""
    with _REPLAY_LOCK:
        entry = _REPLAYS.get(key)
        if entry is None:
            return None
        stamped, result = entry
        # `time.monotonic()`, never the wall clock: a clock adjustment must not
        # silently extend or shorten how long an approval stays replayable. The
        # same reasoning as every other TTL in this tree.
        if time.monotonic() - stamped > REPLAY_TTL_SECONDS:
            del _REPLAYS[key]
            return None
        return result


def _replay_put(key: tuple, result: "DispatchResult") -> None:
    """Remember a completed, *verified* action. Never raises.

    A refusal, a failure and an impossible request are all deliberately left
    uncached - see `_REPLAYABLE_VERDICTS` for why the ambiguous
    `ran=True/UNVERIFIED` cell is not among them, which is the same reasoning
    that makes a probe returning nothing read as unknown rather than as a pass.
    """
    if not result.ran or result.verdict not in _REPLAYABLE_VERDICTS:
        return
    with _REPLAY_LOCK:
        _REPLAYS[key] = (time.monotonic(), result)
        while len(_REPLAYS) > REPLAY_CAPACITY:
            _REPLAYS.popitem(last=False)


def clear_replays() -> None:
    """Forget every replayable action. For tests, and for a deliberate reset."""
    with _REPLAY_LOCK:
        _REPLAYS.clear()


#: Tool -> the other tools that would plausibly have answered the same request.
#:
#: **Intentionally empty, and that is the honest state rather than a missing
#: definition.** `_advise()` used to read a name that existed nowhere in the tree,
#: so the whole branch raised `NameError` and was swallowed - see the comment at
#: the call site. The table that would fill it is a judgement about which tools
#: are rivals ("`web search` and `internet` are both ways to ask the web"), and
#: `tool_select._SYNONYMS` is a *word* -> tool map, not a tool -> tool one. It can
#: be made to yield families by prefix ("media", "media player", "media next"),
#: which is a defensible heuristic and is **not** the same claim as the table this
#: was written for - so it is left to whoever finishes the feature rather than
#: guessed at here, where a wrong guess would read as a working feature.
#:
#: Until it is filled, `known` is legitimately empty and the advice falls back to
#: "A dry run would cost nothing and say more."
ALTERNATIVES: Dict[str, Tuple[str, ...]] = {}


def _failure_hint(exc: Exception) -> str:
    """The recovery instruction for a skill that raised, by exception kind.

    assistd's `io_error_nav` (`assistd/crates/assistd-tools/src/command.rs:57-102`)
    maps an `ErrorKind` to a hint plus a runnable recovery. The map here is
    deliberately short: only kinds with an obvious next move get a hint, and
    anything unmapped says only what happened, because an invented recovery
    instruction is worse than none.
    """
    if isinstance(exc, FileNotFoundError):
        return ("Hint: the target file does not exist; call "
                "list_directory or search_file_contents to see what is there.")
    if isinstance(exc, PermissionError):
        return ("Hint: permission denied; the sandbox profile for this skill "
                "does not cover that path - try a path under the user's home.")
    if isinstance(exc, IsADirectoryError):
        return "Hint: that path is a directory; list_directory it instead."
    if isinstance(exc, ValueError):
        return "Hint: the arguments were malformed; re-call with valid JSON."
    if isinstance(exc, TimeoutError):
        return "Hint: the skill timed out; retry once, or narrow the request."
    return ""


#: How feedback the user typed is marked in a tool result. It has to be marked:
#: joined to the output with a bare " - ", the model cannot tell the person's
#: instruction from the skill's report, and a small local model will frequently
#: read it as part of what the tool said - which is the one outcome this
#: feature exists to prevent. Quoted, attributed and on its own line, it reads
#: as what it is.
_FEEDBACK_PREFIX = "The user allowed this call and added guidance: \"{}\"\n"


def _with_feedback(message: str, feedback: "str | None") -> str:
    """`message` with the user's typed guidance in front of it, if any.

    Feedback leads rather than trails because it is about the call the model
    just made, and the tool's own output is what the model should act on. It is
    also the only text here that came from a person, which is why it is quoted
    and attributed: a small model asked to summarise this output will otherwise
    report the instruction as a finding.
    """
    stripped = (feedback or "").strip()
    if not stripped:
        return message
    return _FEEDBACK_PREFIX.format(stripped) + message


#: Middleware hooks (AgentScope's onion base, `middleware/_base.py:13-66`,
#: reduced to the two edges Chronoa's monolithic dispatch actually needs).
#: A hook that returns a DispatchResult from `pre` short-circuits the call;
#: a hook may rewrite the result in `post`. Hooks compose onion-style: the
#: first registered pre runs outermost. Registration is explicit, so the
#: running set is inspectable (`hooks()`) rather than implicit in edits to
#: `_dispatch`.
_HOOKS_PRE: "list" = []
_HOOKS_POST: "list" = []


def register_hook(pre=None, post=None) -> None:
    if pre is not None:
        _HOOKS_PRE.append(pre)
    if post is not None:
        _HOOKS_POST.append(post)


def hooks() -> "tuple[tuple, tuple]":
    return (tuple(_HOOKS_PRE), tuple(_HOOKS_POST))


def _dispatch(name: str, arguments: dict, by_reference: bool = False,
              origin: str = ORIGIN_USER) -> DispatchResult:
    """Every actuator, with the hands light on for the duration.

    The light is opened before the handler lookup and closed in a `finally`, so
    a skill that raises, a sandbox refusal and a normal return all leave it off.
    Closing it in each return instead would be one forgotten return away from a
    permanently-lit "acting", which is the failure the body's deadlines exist to
    cover but which should not be *relied* on.
    """
    # Middleware pre-hooks run before any policy layer: a hook may refuse
    # (short-circuit) or stay silent. First registered, outermost.
    for pre in _HOOKS_PRE:
        try:
            short = pre(name, arguments, origin)
        except Exception as e:  # noqa: BLE001 - a broken hook must not kill calls
            logger.warning("pre-hook raised: %s", e)
            continue
        if short is not None:
            return short

    # The reaction layer sees the call BEFORE it acts, and can only turn an
    # allowed call into one that needs a person - never the reverse. It is here
    # rather than inside `_dispatch_inner` so a pattern that has grown needs a
    # second sighting before it proceeds, and it is inside `_dispatch` so every
    # path through `execute_tool` and `execute_tool_outcome` gets it.
    blocked = _reaction_refuses(name, arguments, origin)
    if blocked:
        return blocked

    # The guardrail answers a different question from the reaction layer above:
    # not "is this call wise" but "is it even well-formed". A model returning
    # `1` where a string is declared, or omitting a required argument, would
    # otherwise reach a subprocess and come back as a traceback three frames
    # deep that tells the model nothing it can use. It sits here so both public
    # entry points get it, and it can only ever *stop* a call the reaction layer
    # allowed - it never authorises anything.
    malformed = _guardrail_refuses(name, arguments)
    if malformed:
        return malformed

    # Record what the outcome model expects *before* the call, so a prediction
    # and its outcome can later be paired. Without this the loop never closes:
    # the dispatch log has the verdicts but nothing records what was expected,
    # so no prediction can be scored and no model can improve.
    #
    # Best-effort by design. A model that is absent, untrained or broken must
    # not stop a tool from running - this records when it can and says nothing
    # when it cannot.
    advice = _advise(name, arguments, origin)

    activity = _light_hands(name, arguments if isinstance(arguments, dict) else {}, origin)
    try:
        result = _dispatch_inner(name, arguments, by_reference, origin)
    finally:
        _unlight_hands(activity)
    if advice:
        # Appended, not prepended: the tool did run, and this is an addition to
        # what it produced rather than a substitute for it.
        note = getattr(result, "text", "")
        try:
            result.text = (f"{note}\n\n{advice}" if note else advice)
        except Exception:  # noqa: BLE001 - a read-only result is fine
            pass
    # Middleware post-hooks run on the finished result, outermost last so the
    # onion ordering mirrors `pre`. A hook returns the result it wants kept.
    for post in reversed(_HOOKS_POST):
        try:
            kept = post(name, arguments, result)
        except Exception as e:  # noqa: BLE001 - a broken hook must not kill calls
            logger.warning("post-hook raised: %s", e)
            continue
        if kept is not None:
            result = kept
    # Recorded last, on exactly what the caller is about to receive, so a replay
    # is answered with the same text the original produced rather than with
    # whatever the body happened to return before a post-hook rewrote it.
    #
    # A replayed result is skipped: storing its annotated text would mean the
    # *third* identical call came back with the note twice, and the fourth with
    # it three times. That is not hypothetical - it is what this did before the
    # check below was added, which is why the note leads its text rather than
    # trailing it.
    key = _replay_key(name, arguments, origin)
    if key is not None and not result.text.startswith(_REPLAY_NOTE_PREFIX):
        try:
            _replay_put(key, result)
        except Exception:  # noqa: BLE001 - remembering must never fail a call
            pass
    return result


def _advise(name, arguments, origin):
    """What the machine, the model and the bandit say - BEFORE the call.

    It used to be recorded after the call had already returned, which made
    the prediction path a filing exercise: every dispatch asked the model
    and nobody was ever told the answer. A prediction that lands afterwards
    explains a failure; one that lands first can prevent it.

    Three sources, ordered by how much each can be trusted, and the order is
    the design:

    1. **the machine** - a missing command is a fact. If magick is absent the
       route is known exactly and no model is consulted.
    2. **the outcome model** - the non-deterministic part: given a call shape
       this machine has failed before, say so.
    3. **the bandit** - which stand-in has actually succeeded here lately.

    Returns a sentence, or None. Never raises: a broken model must not stop a
    tool, and an absent one must not add noise.
    """
    record = {"tool_name": name, "origin": origin,
              "args": arguments if isinstance(arguments, dict) else {}}
    try:
        from shani_chronoa.capability import capabilities
        from shani_chronoa import learning
        cap = capabilities()
    except Exception:  # noqa: BLE001
        cap = None

    # (1) the machine: deterministic, so it is asked first and wins outright.
    if cap is not None:
        needed = learning.OutcomeModel._NEEDS.get(name)
        if needed:
            missing = [n for n in needed if n not in cap.commands]
            if missing:
                try:
                    from shani_chronoa.routes import explain
                    return explain(missing[0], cap, name)
                except Exception:  # noqa: BLE001
                    return f"{name} needs {missing[0]}.",

    # (2) the model, then (3) the bandit.
    try:
        model = _outcome_model()
        if model is None:
            return None
        probs = model.predict_proba(record)
        best = max(probs, key=probs.get)
        _record_prediction(name, arguments)
        known = []
        try:
            from shani_chronoa.learning import Bandit
            live = Bandit().arms()
            rivals = list(ALTERNATIVES.get(name, ()))
            known = [(a, live[a].wins / live[a].pulls) for a in rivals
                     if a in live and live[a].pulls >= 3]
            known = [k for k in known if k[1] > (probs.get(best) or 0.0)]
        except Exception as exc:  # noqa: BLE001 - advice must never block a tool
            # Logged, where this used to be a bare `pass`. The reason it matters:
            # this line read `_ALTERNATIVES`, a name defined nowhere in the tree,
            # so it raised `NameError` on **every** call and the bare handler
            # turned it into `known = []`. The result - never suggesting a rival
            # tool that has worked better here - looked exactly like the feature
            # being off, and nothing anywhere said otherwise. A swallowed
            # exception that reproduces a disabled feature is the one shape of
            # this bug that cannot be diagnosed from the outside.
            logger.debug("Rival-tool advice unavailable for %s: %s", name, exc)
            known = []
        if best == "failed" and (probs.get("failed") or 0) > 0.5:
            suggestion = (
                f" {known[0][0]} has worked here more often "
                f"({known[0][1]:.0%}); that may be the better path."
                if known else
                " A dry run would cost nothing and say more.")
            return (f"This has failed here before "
                    f"({probs.get('failed', 0):.0%} of recent calls)."
                    + suggestion)
        if best == "unverified" and (probs.get("unverified") or 0) > 0.9 and not known:
            return ("Nothing has ever confirmed this call did anything on "
                    "this machine. It may be fine; there is just no "
                    "evidence.")
    except Exception:  # noqa: BLE001 - advice must never block a tool
        return None
    return None


def _record_prediction(name: str, arguments) -> None:
    """Ask the outcome model what it expects, and log the answer. Never raises."""
    try:
        model = _outcome_model()
        if model is None:
            return
        record = {"tool_name": name, "args": arguments if isinstance(arguments, dict) else {}}
        outcome_model.record_prediction(name, model.predict_proba(record),
                                        model.recommend(record))
    except Exception:  # noqa: BLE001 - telemetry must never block a tool
        return


#: Loaded once, lazily, and only if a trained model has been saved. Absent is
#: The normal state is None: no model trains until the log carries enough
#: scored calls for one to be worth having.
_OUTCOME_MODEL = None
_OUTCOME_TRIED = False


def _outcome_model():
    """The trained outcome model, or None.

    **The path came from `learning.model_path()`, not from beside the
    predictions log.** These pointed at two different files - `logs/` and
    `models/` - so consolidation wrote a model that dispatch could never find.
    Every part worked: training succeeded, saving succeeded, and the prediction
    path still reported "no model". A chain whose ends disagree looks exactly
    like a chain that is not wired, which is how I spent this round convinced the
    layer was inert when it was mis-addressed.

    **A model is also checked before it is used**, not only when it is written.
    Three things disqualify it:

    - **no digest** - a file that does not say what it is, which is the state of
      every model written before signing existed;
    - **a digest that does not match** - edited, truncated, or transferred
      from somewhere that does not vouch for it;
    - **`provenance.honest` false** - a model that never beat the majority
      baseline. Loading one would mean acting on a model whose own report says
      it knows nothing.

    All three decline to None and say so in the log, which is the only honest
    outcome: predicting nothing is better than predicting from a file that
    cannot account for itself.
    """
    global _OUTCOME_MODEL, _OUTCOME_TRIED
    if _OUTCOME_TRIED:
        return _OUTCOME_MODEL
    _OUTCOME_TRIED = True
    try:
        import json

        from shani_chronoa.learning import (_feature_space_id as
                                            learning_feature_space,
                                            models_dir, verify_model)
        from shani_chronoa.outcome_model import OutcomeModel

        # **Every model keeps its own space-stamped name and nothing is moved.**
        # So the question is not "is the file there" but "which of these was
        # trained in the space this build produces" - and a model from another
        # space simply does not match, which is the whole refusal mechanism.
        # Nothing is ever deleted or renamed, so a space that comes back finds
        # its model already in place, still valid, still carrying everything it
        # learned about this machine.
        current = learning_feature_space()
        candidates = sorted(models_dir().glob("outcome*.json"))
        preferred = [c for c in candidates if c.name == f"outcome-{current}.json"]
        others = [c for c in candidates
                  if c.name != f"outcome-{current}.json"
                  and not c.name.endswith('.tmp')]
        chosen = None
        for candidate in preferred + others:
            try:
                if json.loads(candidate.read_text(encoding="utf-8")).get(
                        "feature_space") == current:
                    chosen = candidate
                    break
            except (OSError, ValueError):
                continue
        if chosen is None and candidates:
            logger.info(
                "no outcome model matches this build's feature space (%s); %d "
                "model(s) from other spaces are kept on disk and still valid "
                "if that space returns. Nothing was deleted - a space that "
                "comes back finds its model already here.", current,
                len(candidates))
            return None
        if chosen is None:
            return None
        candidate = chosen
        payload = json.loads(candidate.read_text(encoding="utf-8"))
        check = verify_model(payload)
        if not check.get("ok"):
            logger.debug("outcome model rejected: %s", check.get("reason"))
            return None
        provenance = payload.get("provenance") or {}
        if provenance.get("honest") is False:
            logger.debug(
                "outcome model rejected: its own report says it did not beat "
                "the majority baseline (%s vs %s), so it would predict from "
                "nothing", provenance.get("accuracy"), provenance.get("baseline"))
            return None
        _OUTCOME_MODEL = OutcomeModel.load(candidate)
    except Exception as exc:  # noqa: BLE001
        logger.debug("outcome model unavailable: %s: %s", type(exc).__name__, exc)
        _OUTCOME_MODEL = None
    return _OUTCOME_MODEL


#: One reaction layer per process. It is a short window of prior calls, so
#: sharing it across turns is the point: a pattern is a pattern across the
#: conversation, not within one tool call.
_REACTIONS = ReactionLayer()


def _guardrail_refuses(name: str, arguments):
    """Ask `guardrail` whether this call is well-formed. Returns a
    DispatchResult to return instead, or None to proceed.

    Never raises, for the same reason `_reaction_refuses` does not: a layer that
    cannot answer must not become the reason a tool fails. `guardrail.check` is
    a pure comparison against the skill's own schema, so a failure there means
    the guardrail is broken rather than that the call is malformed - and the
    right response to a broken guardrail is to proceed to the real permission
    layer, not to block a legitimate call.

    Unlike the reaction layer this does *not* need `origin` or `destructive`:
    it judges shape, not authority, and authority is already decided upstream.
    """
    try:
        from shani_chronoa import guardrail

        schema = next((t["function"] for t in TOOLS
                       if t.get("function", {}).get("name") == name), None)
        reason = guardrail.check(name, arguments if isinstance(arguments, dict) else {}, schema or {})
    except Exception:  # noqa: BLE001
        return None
    if reason.should_run:
        return None
    # `ran=False` again carries the meaning: the tool never executed. UNVERIFIED
    # rather than FAILED for the same reason as the reaction layer above - the
    # call did not run and break, it never ran at all.
    return DispatchResult(
        text=("Not run. " + reason.message),
        verdict=verification.Verdict.UNVERIFIED,
        ran=False,
    )


def _person_allows(question: str) -> bool:
    """Put a reaction-layer question to the person; True only on a clear yes."""
    import threading
    from shani_chronoa import ask_bridge
    if threading.current_thread() is threading.main_thread() or not ask_bridge.has_presenter():
        return False
    return ask_bridge.ask(f"{question}\n\nLet it continue?", ["Let it continue", "Stop"]) == "Let it continue"


def _reaction_refuses(name: str, arguments, origin: str):
    """Ask the reaction layer about this call. Returns a DispatchResult to
    return instead, or None to proceed.

    Never raises. A layer that cannot answer must not be the reason a tool
    fails - it is an advisory layer over a permission layer that is already
    enforcing the real rule, so the safe degradation is to let the call through
    to the gate that can actually refuse it.
    """
    try:
        decision = _REACTIONS.check(
            name,
            arguments if isinstance(arguments, dict) else {},
            origin=origin,
            destructive=name in destructive_tools(),
        )
    except Exception:  # noqa: BLE001
        return None
    if not decision.confirm:
        return None
    # The layer decides that a person is needed; this is where the person is
    # asked. It used to stop here and return "Not run" - so "needs a person"
    # never reached one, and a long task the person was watching just died
    # (measured 2026-10-08: a booking stopped at its Purchase click after 25
    # browse calls). Only a person's own request is asked about, never an
    # unattended turn, and never from the GTK main thread, which would block
    # the very dialog that has to answer.
    if origin == ORIGIN_USER and _person_allows(decision.confirm):
        try:
            _REACTIONS.confirmed(name, origin, decision.signals[0].name if decision.signals else "repeat")
        except Exception:  # noqa: BLE001
            pass
        return None
    # `ran=False` is the load-bearing field: it is what tells a caller the
    # tool never executed, rather than leaving that to be inferred from wording.
    # The verdict is UNVERIFIED rather than FAILED because nothing was checked -
    # nothing ran to be checked.
    return DispatchResult(
        text=("Not run. " + decision.confirm),
        verdict=verification.Verdict.UNVERIFIED,
        ran=False,
        evidence=decision.confirm,
    )


def _dispatch_inner(name: str, arguments: dict, by_reference: bool = False,
             origin: str = ORIGIN_USER) -> DispatchResult:
    """Execute a named tool with the given arguments, returning a short result string.

    All execution is sandboxed according to the tool's configured SandboxLevel.
    Privilege escalation (sudo/pkexec/su) is blocked for all levels except
    LEVEL_4_HOST_ROOT. Dangerous binaries (mkfs, dd, shutdown, reboot) are
    blocked by the sandbox policy. GUI access is blocked from sandboxed levels.

    `by_reference=True` passes every argument to the skill as a `FileRef` (a
    `str` path into a private temp file) instead of inlining the call's JSON
    into the child's program source. Needed for an argument the inline
    transport cannot carry: a `bytes` value always takes this path on its own,
    and this flag is how a caller asks for it deliberately rather than by
    accident. See `argfile.py`.

    `origin` records *who asked for the call* on the existing audit trail -
    `tools.tool_tracking.ORIGIN_USER` (the default, every shipped caller) or
    `ORIGIN_UNATTENDED` (an armed trigger rule that fired unprompted). It is
    one field on the record `ToolTracker` already writes; there is no second
    log. An unattended actuation that does not set this is indistinguishable
    from one the user asked for, which defeats the audit trail's purpose for
    exactly the behaviour that erodes trust.
    """
    handler = _HANDLER_FNS.get(name)
    if handler is None:
        return DispatchResult(f"Unknown tool: {name}", verification.Verdict.UNVERIFIED, False)
    if not isinstance(arguments, dict):
        # Arguments come from the LLM and are untrusted; a non-dict value
        # must not reach a skill handler (which would crash on .get()).
        logger.warning(f"Tool '{name}' called with non-dict arguments; ignoring them")
        arguments = {}

        config = _get_sandbox_config(name)
        handler_module = handler.__module__
        handler_func = handler.__name__

    # Plan mode is checked here, in the dispatch path, rather than asked for in
    # the system prompt. A prompt is a promise the model may or may not keep;
    # this is a fact, which is the only reason the mode is worth having.
    refusal = planmode.blocked_reason(name)
    if refusal:
        return DispatchResult(refusal, verification.Verdict.UNVERIFIED, False)

    # EXPLORE mode is the same fact from the permission layer: a mutating call
    # is refused before any consent question is asked, so a read-only session
    # cannot be talked out of read-only.
    explore_refusal = permissions.explore_refuses(name)
    if explore_refusal:
        return DispatchResult(explore_refusal, verification.Verdict.UNVERIFIED, False)

    # Scoped deny rules are a pre-filter here, for the same reason: a rule the
    # user set for this session should hold even if the skill forgets to look.
    # Only *deny* is enforced centrally. An allow is deliberately not - granting
    # from here would bypass the consent key the user has switched off, and the
    # whole point of those keys is that a skill cannot talk its way past them.
    # Grants are consulted by each skill's own gate instead.
    # Before any policy question is even asked. A malformed call is not a
    # permission problem, and letting one through means the failure surfaces as
    # a traceback inside a subprocess - which tells the model nothing it can
    # act on, where the reason here names the argument and what was expected.
    malformed = guardrail.check(name, arguments, _schema_for(name) or {})
    if not malformed.should_run:
        return DispatchResult(malformed.message, verification.Verdict.UNVERIFIED, False)

    scoped_resource = _resource_for(name, arguments)
    if scoped_resource is not None:
        allowed, why = permissions.permits(name, scoped_resource)
        if not allowed:
            return DispatchResult(why, verification.Verdict.UNVERIFIED, False)

    # A grant the user gave this session opens the tool's own consent key for
    # this one call, so the skill's internal gate agrees with the dispatch.
    #
    # Deliberately outside the branch above. Only 14 of the 69 tools name a
    # path or unit at all - `screenshot`, `set_wallpaper` and `web_search` have
    # no such argument - so nesting this where the scoped check lives left the
    # grant unreachable for precisely the consent-gated tools it exists to
    # serve. The first version of this had that bug and a test caught it.
    #
    # An unscoped tool is matched against "*" as its resource. A grant written
    # as `("*", allow_once)` covers every tool; a grant naming a concrete path
    # does not, which is the conservative direction - a permission to delete
    # `/home/me/Downloads` should not also open a screenshot.
    #
    # Hoisted because it is read again at execution time, and a tool with no
    # scoped resource never reaches a branch that would assign it.
    granted_key = None
    grant_target = scoped_resource if scoped_resource is not None else "*"
    # Feedback typed against the "Provide feedback" answer, carried to whichever
    # result this call produces. None on every path that never asked.
    _feedback_for_dispatch = None
    if permissions.session_grant(name, grant_target):
        granted_key = _consent_key_for(name)
    else:
        # Nothing on record, so the tool's own consent key decides. If it is
        # shut, ask the user rather than returning a refusal they cannot act
        # on - the whole point of the grant path is that a "no" is the user's
        # to give, not only the app's.
        #
        # Only asked when the key is *definitively* off. `get_bool` says
        # nothing about the other half of `sense_allowed()` - privacy mode - so
        # a key that reads true while privacy mode blocks the call must not
        # prompt. Asking there would put a question to the user whose "yes"
        # could not possibly work.
        consent_key = _consent_key_for(name)
        if (consent_key is not None
                and permissions.can_ask()
                and not config_mod.ChronoaConfig().get_bool(consent_key, False)):
            title = capabilities.tool_title(name) or name
            # **`offer_cancel=True`, wired 2026-10-06.** The option existed and
            # was never offered here, and where it *was* offered (the inbound
            # gateway) nothing acted on it - so either it is offered and honoured,
            # or it is not offered at all. It is now offered here, and
            # `assistant.py` ends the turn when `permissions.turn_cancelled()` is
            # true, so the label is true.
            granted = permissions.decide(name, scoped_resource, consent_key,
                                         describe=title.lower(),
                                         offer_cancel=True)
            if granted is None:
                return DispatchResult(
                    f"The user did not allow {name}. Nothing was done.",
                    verification.Verdict.UNVERIFIED, False)
            granted_key = consent_key
            # Consume the record `decide()` just wrote, so "allow this once"
            # means once. Without this the grant sat on the books and the *next*
            # call found it - so allowing once allowed twice, which is the exact
            # opposite of what the button said and the worst kind of consent bug
            # to ship silently. `session_grant` deletes a one-shot and leaves a
            # session grant standing, so one call covers both cases.
            permissions.session_grant(name, grant_target)
            # T1.9: the user answered "Provide feedback" instead of yes/no, so
            # text they typed comes back with the result. Consumed here, so it
            # rides this one call rather than prefixing every later one.
            _feedback_for_dispatch = permissions.consume_feedback(name, grant_target)

    # The approval above stands; the *action* may already be done. Deliberately
    # here rather than at the top of `_dispatch`: the question was asked and
    # answered again like any other time, and this is only the second execution
    # that is skipped.
    _replay = _replay_key(name, arguments, origin)
    if _replay is not None:
        already = _replay_get(_replay)
        if already is not None:
            # Said out loud rather than returned silently, and the note leads.
            # A result that appears without the action happening is
            # indistinguishable, to the model and to the person, from the tool
            # simply being fast - and the whole point of `ToolFailure` is that
            # "it ran and its effect held" is a different fact from "it did not
            # run this time". Leading also gives `_dispatch` a structural way to
            # recognise this result and *not* re-record it, which a trailing note
            # cannot: storing the annotated text would stack the note again on
            # every further replay.
            return DispatchResult(
                f"{_REPLAY_NOTE_PREFIX}this exact {name} call was approved and "
                f"completed moments ago. Its result when it ran:]\n{already.text}",
                already.verdict, already.ran)

    config = _get_sandbox_config(name)
    handler_module = handler.__module__
    handler_func = handler.__name__

    # Named, not inferred: this list runs unsandboxed, which is a privilege,
    # and a heuristic deciding it would widen without anyone reviewing it.
    if _runs_locally(name):
        # Both failure shapes below route through `_tool_failure_result`, so the
        # feedback is attached to whichever message is built *before* that
        # point rather than after it. Feedback has already been consumed by the
        # time a tool runs, so a result that drops it loses the user's guidance
        # permanently - and the guidance is most worth hearing when the thing
        # they were steering went wrong.
        try:
            local_msg = handler(arguments)
        except ToolFailure as exc:
            return _tool_failure_result(
                _with_feedback(str(exc), _feedback_for_dispatch))
        # **The marker check must stay ahead of the feedback prefix.** It is a
        # `startswith`, so a prefix written in front of the message makes it
        # miss, and a skill that *did* fail would then be reported as a plain
        # successful result - the marker exists to say "it ran and its effect
        # did not hold", and losing it inverts the one claim that matters.
        # Prefixing after the branch is chosen costs nothing and cannot.
        if isinstance(local_msg, str) and local_msg.startswith(ToolFailure.MARKER):
            return _tool_failure_result(_with_feedback(
                local_msg[len(ToolFailure.MARKER):], _feedback_for_dispatch))
        return DispatchResult(_with_feedback(local_msg, _feedback_for_dispatch),
                              verification.Verdict.UNVERIFIED, True)

    # A skill may opt into the by-reference transport for its own oversized
    # arguments (see `skills/speak.py:wants_by_reference`). Ask the module
    # before dispatch rather than guessing: the caller's `by_reference` flag
    # is for callers that know their argument is binary, and a skill that
    # declares its own ceiling gets to enforce it without every caller
    # remembering to pass the flag.
    if not by_reference:
        try:
            module = importlib.import_module(handler_module)
            wants = getattr(module, "wants_by_reference", None)
            if callable(wants) and wants(arguments):
                by_reference = True
        except Exception as e:  # noqa: BLE001 - a broken probe must not kill the call
            logger.debug(f"wants_by_reference probe for '{name}' failed: {e}")

    # Asked first because it is the only transport that can carry a bytes
    # argument; it returns None whenever the inline path below suffices.
    payload = argfile.reference_command(handler_module, handler_func, arguments, by_reference)

    if payload is not None:
        argv = payload.argv
    else:
        # Build a command string that invokes the handler in a subprocess.
        # This allows SandboxExecutor to wrap it with bwrap for sandboxed levels.
        #
        # Kept because the bug was real, not because the fix is still needed.
        # This used to be `shlex.quote`d into a command string, and the JSON
        # arguments contain double quotes of their own which the shell stripped,
        # so every call with a dict argument reached the child as
        # `_run({query: foo})` and died with `NameError: name 'query' is not
        # defined`. Only zero-argument skills worked. There is no shell between
        # here and the exec now, so each element of the argv carries its own
        # bytes and that class of stripping is structurally impossible - the
        # bug cannot come back in this form, but a new transport could
        # reintroduce the same mistake somewhere else.
        # A *Python* literal, not JSON. `json.dumps` writes `true`, `false`
        # and `null`, which are not Python: the child program still compiles,
        # because `true` parses as a name, and then dies at runtime with
        # `NameError: name 'true' is not defined`. So every skill called with a
        # boolean or null argument - `dry_run`, `overwrite`, `failed_only`,
        # `include_hidden` - failed over MCP while working perfectly in the
        # unit tests, which call the handler in-process and never serialise.
        #
        # `repr` is the right conversion for the other direction: arguments
        # arrive as JSON, so they are only ever str/int/float/bool/None/list/
        # dict, and `repr` renders every one of those as valid Python source.
        args_literal = repr(_json_safe(arguments))
        program = (
            f"from {handler_module} import {handler_func}\n"
            f"import sys\n"
            f"from shani_chronoa.toolfailure import ToolFailure\n"
            f"try:\n"
            f"    result = {handler_func}({args_literal})\n"
            f"except ToolFailure as _tf:\n"
            f"    sys.stdout.write(ToolFailure.MARKER + str(_tf)); sys.exit(0)\n"
            f"sys.stdout.write(str(result))"
        )
        argv = ["python3", "-c", program]

    if granted_key:
        # Set on the child, not on this process. The dispatcher runs many calls
        # in one process, so a value set here would be whichever call happened
        # to be in flight - exactly the class of bug where a grant meant for
        # one tool silently opens another.
        argv = ["env", f"{config_mod.CONSENT_GRANT_ENV}={granted_key}", *argv]

    try:
        exit_code, output, duration_ms = _SANDBOX.execute(argv, config)
        result = output if exit_code == 0 else f"ERROR(exit={exit_code}): {output}"
        if exit_code != 0:
            _TRACKER.record_call(name, arguments, result, duration_ms, origin=origin)
            logger.error(f"Tool '{name}' exited with code {exit_code}: {output}")
            msg = output or f"Tool '{name}' failed with exit code {exit_code}"
            msg = _with_feedback(msg, _feedback_for_dispatch)
            return DispatchResult(msg, verification.Verdict.UNVERIFIED, False)
        # The skill's own string is its account of what it did, not evidence
        # that it happened. A module that declares POST_CONDITION gets that
        # checked against real state here, and an action with no post-condition
        # says so rather than letting the caller infer success. See
        # verification.py for why this is not optional politeness.
        if isinstance(result, str) and result.startswith(ToolFailure.MARKER):
            # The child reported a ToolFailure: it ran, the effect did not
            # hold. Feed the same FAILED verdict the in-process path produces,
            # so a remote and a local failure read identically downstream.
            failure = _tool_failure_result(result[len(ToolFailure.MARKER):])
            _TRACKER.record_call(name, arguments, failure.text, duration_ms,
                                 origin=origin, verdict=failure.verdict.value)
            return failure
        try:
            checked = verification.verify(handler_module, arguments, tool=name)
        except Exception as e:  # noqa: BLE001 - verification must never kill the action
            logger.warning(f"Post-condition for '{name}' raised: {e}")
            checked = verification.Result(verification.Verdict.UNVERIFIED, str(e))
        # Recorded after verification, not before: a record written on the way
        # out cannot carry a verdict that does not exist yet, which left a
        # failed action in the audit log looking exactly like a successful one.
        # Do not move this call back up.
        _TRACKER.record_call(
            name, arguments, result, duration_ms,
            origin=origin, verdict=checked.verdict.value,
            evidence=checked.evidence,
        )
        if checked.verdict is verification.Verdict.FAILED:
            logger.error(f"Tool '{name}' reported success but verification failed: {checked.evidence}")
        # Verification is about ACTIONS: a claim that something changed. A
        # read-only tool changed nothing and its output is the observation
        # itself, so "reports success but nothing observed it" was false on
        # every one of them - appended to calculate's "4" and to the time,
        # inviting a model to doubt a correct answer. The verdict is still
        # recorded above; only the misleading text is not appended.
        from shani_chronoa.capabilities import READ_ONLY_TOOLS
        suffix = "" if (name in READ_ONLY_TOOLS and checked.verdict is verification.Verdict.UNVERIFIED) \
            else checked.suffix
        msg = output + suffix
        msg = _with_feedback(msg, _feedback_for_dispatch)
        return DispatchResult(msg, checked.verdict, True,
                             checked.evidence)
    except Exception as e:
        logger.error(f"Tool '{name}' failed: {e}")
        _TRACKER.record_call(name, arguments, f"EXCEPTION: {e}", 0.0, origin=origin)
        # Hint-bearing error line, after assistd's
        # `error_line("[error] {cmd}: {what}. {hint}: {recovery}")`
        # (`assistd/crates/assistd-tools/src/command.rs:10-52`):
        # every error tells the model its next move, because a
        # bare traceback tells a small local model nothing it
        # can act on, and the turn then spends its rounds
        # re-calling the same failing skill. The exception's
        # own text stays - it is the `what` - and a recovery
        # is appended for the kinds that have one.
        message = f"Tool '{name}' failed: {e}."
        hint = _failure_hint(e)
        if hint:
            message = f"{message} {hint}"
        msg = message
        msg = _with_feedback(msg, _feedback_for_dispatch)
        return DispatchResult(msg, verification.Verdict.UNVERIFIED, False)
    finally:
        # The payload files hold the argument values themselves, so they go
        # away whatever the child did. cleanup() is idempotent, so this also
        # covers a payload that was built but never dispatched.
        if payload is not None:
            payload.cleanup()


class ToolOutcome(NamedTuple):
    """A dispatch result with the verdict attached, for callers that act on it.

    `execute_tool` returns a bare string because that is what an LLM reads, and
    prose is the right shape for that. It is the wrong shape for a caller
    deciding whether to *record a success* - the verdict is only recoverable
    from the string by matching the marker text, and a caller that forgets is
    indistinguishable from one that never checked.
    """

    text: str
    verdict: verification.Verdict
    evidence: str = ""
    ran: bool = True

    @property
    def is_error(self) -> bool:
        """goose's spelling: did this fail to produce an answer."""
        return not self.ran


def _light_hands(name: str, arguments: dict, origin: str):
    """Tell the body an actuator is running. Never raises.

    The summary is the tool's own name plus its arguments, because "acting" with
    no detail is the one thing an indicator must not say: the question behind
    every permission prompt in the world is *what exactly are you doing*.
    """
    try:
        from shani_chronoa import body

        summary = ", ".join(f"{key}={value}" for key, value in list(arguments.items())[:3])
        return body.body.use("hands", name.replace("_", " "),
                             summary[:80],
                             deadline=body.DEFAULT_DEADLINE["hands"],
                             origin=origin)
    except Exception:                                   # noqa: BLE001
        return None


def _unlight_hands(activity) -> None:
    try:
        from shani_chronoa import body

        body.body.done(activity)
    except Exception:                                   # noqa: BLE001
        pass


def execute_tool(name: str, arguments: dict, by_reference: bool = False,
                 origin: str = ORIGIN_USER) -> str:
    """Execute a named tool, returning the short result string a model reads.

    Prose is the right shape for that - the model reads it. It is the wrong
    shape for a caller deciding whether the tool ran, which is why `_dispatch`
    exists and why `execute_tool_outcome` uses it instead of re-reading the
    string this returns.
    """
    return _dispatch(name, arguments, by_reference=by_reference,
                     origin=origin).text


def execute_tool_outcome(
    name: str,
    arguments: dict,
    by_reference: bool = False,
    origin: str = ORIGIN_USER,
) -> ToolOutcome:
    """`execute_tool`, with the verdict and whether it ran as data.

    The verdict used to be recovered from the returned string by matching a
    marker, even though the dispatch had computed it exactly one line earlier
    and thrown it away. Re-deriving it meant a change to the marker's wording
    silently turned every verdict into UNVERIFIED - which is the failure this
    class's own docstring warns about, since a caller that forgets to check
    is then indistinguishable from one that never could.
    """
    result = _dispatch(name, arguments, by_reference=by_reference, origin=origin)
    return ToolOutcome(text=result.text, verdict=result.verdict,
                       evidence=result.evidence, ran=result.ran)
