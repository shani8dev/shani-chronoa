"""Make the conversation provider-valid before every request, not just usually.

Every backend Chronoa can be pointed at validates tool call/result pairing, and
the strictest of them reject a whole request over it: Anthropic refuses a
`tool_result` with no preceding `tool_use`, OpenAI-compatible gateways report
`No tool call found for function call output`. Ollama is the lenient one, and
that is exactly why this is dangerous here - the default backend silently
accepts a history that the cloud fallback will then refuse, so the bug only
appears once a user opts into the fallback, or once they switch models.

The history is not merely *able* to reach that state; this repo builds it
regularly. `_trim_history()` cuts at user-message boundaries, a turn can end
between the assistant message that made four calls and the four results it was
about to write, and a restored transcript is a file on disk that anything can
edit. So this is not a defensive pass over a hypothetical.

Adopted from pydantic-ai's `_clean_message_history`
(`pydantic_ai/_agent_graph.py:3279`), which is an ordered pipeline of pure
`list -> list` passes. Two are ports; the third was removed, and both the reason
and the evidence are below rather than left to be rediscovered.

1. `drop_orphaned_tool_results` REMOVES a tool result this conversation never
   asked for. It runs first so a result that cannot be paired is gone before
   anything reasons about pairing.
2. `repair_dangling_tool_calls` ADDS a synthesized result for a call that never
   got one. It runs second, once removals have settled.

**The third pass - merging adjacent same-role messages - was removed, on
purpose.** Upstream's own docstring calls it shape normalisation, and by their
own test names so is it: it neither adds nor removes content, only combines
adjacent messages a provider would accept either way. It is a token
optimisation wearing the clothes of a repair, and optimisations are not allowed
to change the shape of the record's wire copy.

Here it broke a documented invariant. `Assistant.build_messages()` promises to
preserve the message count, and `tests/test_context_compression.py` is the
behaviour of record for that: `_trim_history()` groups history into turns by
counting messages, and the `tool_call_id` correlation is one-message-per-result
by definition. An earlier version of this module ran the merge, collapsed eight
consecutive `user` messages into one, and that test went red with
`2 == 11`. Removing the pass restores it. Kept as a note for whoever looks next:
if a future backend genuinely *requires* merged turns, that has to be proved
against that backend, not inferred from a token saving.

**Synthesized results are deterministic, and that is the point.**
pydantic-ai derives a synthesized part's timestamp from the response it repairs
rather than from the clock, precisely so that repairing on every single run
yields byte-identical output and never churns a provider's prompt-cache prefix.
This module holds to the same rule and goes one step further: it introduces no
timestamp at all. Nothing in Chronoa's message path stamps a wall clock into a
message - the prompt is rebuilt from `_history`, the transient percept block and
`compression`'s elision notes, none of which carry a time - so a synthesized
result stamped with `time.time()` would be the *only* moving byte in the prompt
and would invalidate the cache prefix on every turn. If a future change adds one,
the idempotence test in `tests/test_history_repair.py` is what catches it.

Everything here is pure. No file, no clock, no network, no mutation of the
input. `_history` and the on-disk transcript keep every byte, exactly as
`compression.compress()` and `ContextBuilder.build_messages()` both do, for the
same reason: an elision or a repair here is a transport saving, never a record
that something was lost.

## What does not survive the port, and why that is not a bug

pydantic-ai carries messages as typed *parts*; Chronoa carries one dict per
message. Two consequences, each a deliberate decision rather than a gap:

- **Tool results can never be merged.** Two adjacent tool results carry two
  different `tool_call_id`s and one message has one id, so merging them would
  destroy the very correlation pass 2 exists to establish. In pydantic-ai they
  are two parts of one request and merging is a lossless reshuffle; in this
  shape it is not lossless, so it does not happen.
- **Nothing is hoisted.** The hoisting rule moves tool results ahead of
  user-facing parts *within* a request. In this shape a tool result and a user
  turn are different roles, so they are already different messages and the
  ordering constraint cannot arise.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

#: Marks a tool result this module wrote rather than one a tool actually
#: produced. Present so an operator reading `_history` or the saved transcript
#: can tell a real result from a repair, which is otherwise indistinguishable
#: and would be read as "the tool ran and said this".
SYNTHESIZED_KEY = "chronoa_synthesized"

#: Marks *why* it was synthesized. A turn that hit its budget, a turn that was
#: cancelled, and a transcript that was edited on disk are three different
#: problems, and the fix for each is different.
REASON_KEY = "chronoa_repair_reason"

#: Why each call went unexecuted, per reason. A fabricated tool output is a lie
#: the model will reason from, and "this never ran" is the only true thing
#: available - so every variant says that, names the reason, and tells the model
#: not to retry. Kept per-reason because a turn that ran out of time, a turn
#: that was cancelled, and a transcript edited on disk are three different
#: problems and only the first two have a fix in this codebase.
_REASON_PHRASE = {
    "budget": (
        "not executed: the turn ran out of its time budget partway through the "
        "calls it had asked for, so there is no output for it."
    ),
    "interrupted": (
        "not executed: the turn was interrupted before its result was recorded, "
        "so there is no output for it."
    ),
    "orphaned": (
        "not executed: no call matching it was found in the conversation, so "
        "there is no output for it."
    ),
    "limit": (
        "not executed: this turn has already used this tool's whole budget, so "
        "it was not run again. Do not call it again in this turn, and do not "
        "assume it failed - nothing is wrong with the tool itself, this turn "
        "simply stopped retrying it. Answer with what you already have, or ask "
        "the user what they would like next."
    ),
}


def _reason_phrase(reason: str) -> str:
    """`reason`'s sentence, or the neutral one for a reason never heard of.

    Falling back rather than raising: an unknown reason means a caller added a
    case, and refusing to answer the call over it would leave the history broken
    - which is the defect this module exists to remove.
    """
    return _REASON_PHRASE.get(reason, _REASON_PHRASE["orphaned"])

#: The turn-ending message appended after a repair, so the model knows the
#: state of the conversation rather than having to infer it from a result that
#: says nothing ran. Transcript-only: it is never returned to the user, and
#: `app.py` has already reported the real outcome of an interrupted turn.
CONTINUE_PROMPT = (
    "The previous turn was interrupted before it finished. Some of the tool "
    "calls it made did not run, and their results above say so. Tell the user "
    "plainly what was and was not done, and ask what they would like next "
    "rather than silently redoing the work."
)


def _is_tool_result(message: dict) -> bool:
    """Whether `message` is a locally-produced tool result.

    One role and one key, which is the whole of the contract in this message
    shape. Kept as a predicate rather than an inline `role == "tool"` so the
    three passes cannot drift apart on what counts as a result.
    """
    return message.get("role") == "tool"


def _call_ids(message: dict) -> "list[str]":
    """The tool call ids an assistant message makes, in order.

    A malformed `tool_calls` - not a list, or entries that are not dicts - is
    treated as making no calls rather than raising. The same tolerance
    `llm.py` applies to a malformed response body: a backend that returns
    something unexpected must not make the assistant unable to answer at all.
    """
    calls = message.get("tool_calls")
    if not isinstance(calls, list):
        return []
    ids: list[str] = []
    for call in calls:
        if isinstance(call, dict) and isinstance(call.get("id"), str):
            ids.append(call["id"])
    return ids


def _tool_name(call: dict) -> str:
    """A tool's name from a `tool_calls` entry, or an honest placeholder.

    The name goes into a message the model reads, so an empty string would read
    as a tool called by nothing. `unnamed_tool` is at least attributable, and
    `assistant.py` already uses the same fallback for the id it records.
    """
    function = call.get("function")
    if isinstance(function, dict):
        name = function.get("name")
        if isinstance(name, str) and name:
            return name
    return "unnamed_tool"


def synthesized_tool_result(call: dict, reason: str = "orphaned") -> dict:
    """A tool result for `call` that says, truthfully, that nothing ran.

    The two marker keys are the whole difference from a real result, and they
    are what makes this inspectable: without them a synthesized result is
    indistinguishable from a tool that returned that text, and the transcript
    becomes a record of something that did not happen.

    Carries no timestamp and no id of its own beyond the call's, so calling
    this twice on the same call produces the same dict.
    """
    content = _reason_phrase(reason)
    return {
        "role": "tool",
        "tool_call_id": call.get("id", "") if isinstance(call, dict) else "",
        "content": (
            f"Tool call `{_tool_name(call)}` was {_reason_phrase(reason)} This "
            f"message is a repair written by the assistant, not output from the "
            f"tool. Do not treat it as a result and do not repeat the call "
            f"assuming it failed - ask the user what they would like next."
        ),
        SYNTHESIZED_KEY: True,
        REASON_KEY: reason,
    }


def _iter_calls(messages: "list[dict]") -> "list[tuple[int, list[dict]]]":
    """`(message index, its tool_calls)` for every message that makes calls."""
    found = []
    for index, message in enumerate(messages):
        if message.get("role") == "assistant":
            calls = message.get("tool_calls")
            if isinstance(calls, list) and calls:
                found.append((index, [c for c in calls if isinstance(c, dict)]))
    return found


def unpaired_tool_calls(messages: "list[dict]") -> "list[dict]":
    """The tool calls in `messages` that no tool result answers.

    The question the loop has to be able to ask about its *own* history before
    it decides to stop, which is why it is a public predicate and not buried
    inside the repair. An empty result means the history is sendable as it
    stands.

    Matching is an ordered walk, not a set lookup, and the difference is not
    academic. A result that arrives *before* its call, a duplicate result, or a
    result reusing the id of an already-answered call must not be allowed to
    mark a genuinely unanswered call as answered - that is how a cancelled turn
    stays silently broken through request after request instead of being
    repaired on the first one.

    **Calls with no id are matched positionally, not by name.** This is a
    Chronoa-specific necessity, not a refinement: Ollama frequently omits `id`
    from `tool_calls` entirely, and `assistant.py` then records `""` for every
    result in the turn. Under a by-id walk every id-less call shadows the one
    before it, and an interrupted batch of four calls with two results would
    produce *five* results - inventing an answer to a call that was already
    answered. FIFO for the unnamed ones keeps the counts equal, which is the
    only thing a bare `""` can honestly be matched on.
    """
    open_named: dict[str, dict] = {}
    open_unnamed: "list[dict]" = []

    for message in messages:
        if message.get("role") == "assistant":
            calls = message.get("tool_calls")
            for call in calls if isinstance(calls, list) else ():
                if not isinstance(call, dict):
                    continue
                call_id = call.get("id")
                if isinstance(call_id, str) and call_id:
                    open_named[call_id] = call
                else:
                    open_unnamed.append(call)
        elif _is_tool_result(message):
            call_id = message.get("tool_call_id")
            if isinstance(call_id, str) and call_id:
                open_named.pop(call_id, None)
            elif open_unnamed:
                open_unnamed.pop(0)

    return [*open_named.values(), *open_unnamed]


def drop_orphaned_tool_results(messages: "list[dict]") -> "list[dict]":
    """Remove tool results that this conversation never asked for.

    **Deleting is irreversible here, so the bar for calling something an orphan
    is deliberately higher than "I could not find its call".** Three rules, each
    of which a simpler implementation gets wrong in opposite directions.

    **The result must carry a non-empty `tool_call_id`.**

    The empty-id rule is not a detail - it is the difference between this working
    and destroying the user's conversation. **Ollama routinely emits
    `tool_calls` with no `id` field at all**, and `assistant.py` then records
    `tool_call_id: ""` for every result in the turn. A naive whole-history
    id-equality scan finds no call carrying `""` and deletes *every real tool
    result in the session* while reporting nothing wrong. Verified by running
    it: one ordinary Ollama turn, one real result, deleted.

    **The conversation must contain at least one id-bearing tool call.**

    Same rule again, one level out: with no call anywhere there is nothing to
    compare a result against, so "I could not find its call" carries no
    information at all. `sessions.load()` replays whatever is on disk and skips
    only lines that will not parse, so a restored transcript whose call line was
    lost really does reach here with results and no calls - and
    `Assistant.build_messages()` must not delete them. That is the contract
    `tests/test_context_compression.py::TestThroughTheAssistant` holds of a
    history, and it is why this declines instead of guessing. `_trim_history()`
    cannot produce this shape, by contrast: it drops whole user-delimited turns,
    and `_run_turn()` records a call and its results inside one turn, so
    trimming cannot separate them. An interrupted turn does leave an assistant
    `tool_calls` message, so real repairs still run.

    **The call must have been made at or before the result.**

    A result that precedes its own call answers nothing, and that is malformed
    rather than merely unusual: Anthropic refuses a `tool_result` with no
    preceding `tool_use` and OpenAI reports `No tool call found for function
    call output` - the two failures this module's own docstring opens with.
    Judging the result by whole-history membership kept it *and* let pass 2
    answer the same call, emitting two results for one `tool_call_id` with the
    first of them answering nothing. Verified by running, on
    `[result(c1), assistant([c1]), user]`: roles out were
    `tool, assistant, tool, user`.

    Returns the input list itself when there is nothing to drop, so a
    well-formed history is provably a no-op - same list object, no allocation.
    """
    made_call_ids: set[str] = set()
    for message in messages:
        if message.get("role") == "assistant":
            made_call_ids.update(i for i in _call_ids(message) if i)
    if not made_call_ids:
        return messages

    out: list[dict] = []
    dropped: list[str] = []
    asked: set[str] = set()

    for message in messages:
        if message.get("role") == "assistant":
            asked.update(i for i in _call_ids(message) if i)
        elif _is_tool_result(message):
            call_id = message.get("tool_call_id")
            if isinstance(call_id, str) and call_id and call_id not in asked:
                dropped.append(call_id)
                continue
        out.append(message)

    if dropped:
        # Counted and reported, because a silent deletion from the wire copy is
        # indistinguishable from normal behaviour to anyone reading a log or a
        # transcript - and `build_messages()` is called several times per turn,
        # so an unlogged drop would repeat silently.
        logger.warning(
            "Dropped %d tool result(s) whose call is not in this conversation "
            "(ids: %s). If a backend here omits tool_call ids, this is a bug: "
            "report it.", len(dropped), ", ".join(sorted(set(dropped))[:5]))
    return out if dropped else messages


def repair_dangling_tool_calls(
    messages: "list[dict]",
    *,
    repair_last_response: bool = False,
) -> "list[dict]":
    """Give every unanswered tool call a result that says nothing ran.

    A call is unanswered when the ordered walk in `unpaired_tool_calls` still
    has it open at the end of the history. Each gets one synthesized result,
    inserted **immediately after the last tool result already recorded for that
    assistant message** - or immediately after the assistant message itself when
    it has none.

    Position is the entire correctness requirement. Appending to the end of the
    list would look right in a unit test and be wrong on the wire: the history
    at this point already ends with a fresh user turn, and a tool result placed
    after it is a result for nothing, which is the defect being repaired.
    Anthropic's rule is that a `tool_result` answers the `tool_use` immediately
    before it; OpenAI's is that tool messages respond to the assistant
    `tool_calls` they follow.

    `repair_last_response=False` leaves the calls of the *final* assistant
    message alone. Those are the live frontier - a turn that is still running
    will answer them itself, and repairing them would fabricate a result
    telling the model its own in-flight call did not run.
    """
    unpaired = unpaired_tool_calls(messages)
    if not unpaired:
        return messages

    # Where each assistant message's results should go, and which calls it is
    # still missing. Walking the history once keeps this O(n) rather than
    # re-scanning per message.
    call_index: dict[int, list[dict]] = {}
    for index, calls in _iter_calls(messages):
        if index == len(messages) - 1 and not repair_last_response:
            continue
        call_index[index] = calls

    missing: dict[int, list[dict]] = {}
    for call in unpaired:
        owner = _owning_index(messages, call)
        if owner is None:
            continue
        if owner not in call_index:
            # The frontier case: the last assistant message, left alone on
            # purpose. Dropping it here rather than repairing it is what
            # `repair_last_response` asked for.
            continue
        missing.setdefault(owner, []).append(call)

    if not missing:
        return messages

    out: list[dict] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        out.append(message)
        calls = missing.get(index)
        if not calls:
            index += 1
            continue
        # Copy the results this call already has, then add the ones it is
        # missing behind them. Appending the repairs immediately after the
        # assistant message instead would put them *ahead* of a real result
        # and reorder the record of a turn that was genuinely answered - the
        # first version of this did exactly that, and a test caught it.
        end = index + 1
        while end < len(messages) and _is_tool_result(messages[end]):
            out.append(messages[end])
            end += 1
        out.extend(synthesized_tool_result(call, "orphaned") for call in calls)
        index = end

    logger.info("Repaired %d unanswered tool call(s) before a model request",
                sum(len(v) for v in missing.values()))
    return out


def _owning_index(messages: "list[dict]", call: dict) -> "Optional[int]":
    """The index of the assistant message that made `call`.

    The *last* one, deliberately: a backend that reuses an id has made a new
    call, and it is the new one that is open. Answering the older call would
    put a result where the provider is not looking for it.
    """
    owner = None
    for index, calls in _iter_calls(messages):
        if call in calls:
            owner = index
    return owner





def clean_history(
    messages: "list[dict]", *, repair_last_message: bool = False
) -> "list[dict]":
    """The message list to send, made provider-valid.

    Two passes, and the order is load-bearing - **but not for the reason
    upstream gives.** Upstream says dropping an orphan first can expose a call
    that then needs a result; here it cannot, because a tool result is a whole
    message with one `tool_call_id` and so shares no id with any dangling call.
    What does make the order matter is `repair_last_response`: it means "leave
    the calls of the last assistant message alone", and repair appends messages,
    so running it first would make the frontier no longer the last message and
    tell a still-running turn that its own in-flight call did not run. Measured,
    not reasoned: 8,720 of 177,155 generated histories differ between the two
    orders, every one of them that shape. See `TestPipelineOrder`.

    **The shape-normalisation pass this used to have has been removed, and that
    is a decision rather than an omission.** Upstream merges adjacent same-role
    messages, which is a *token optimisation*, not a repair: nothing is
    unpaired before it and nothing is after it. Here it broke a documented
    invariant of `Assistant.build_messages()` - that the message count is
    preserved - which `_trim_history()`'s turn grouping and the `tool_call_id`
    correlation both depend on. An optimisation is not allowed to silently
    change the shape of the record's wire copy.

    **The contract is: already-well-formed history passes through unchanged, and
    only genuinely malformed history is touched.** That is asserted by
    `tests/test_context_compression.py::TestThroughTheAssistant`, which is the
    behaviour of record, and by `test_a_well_formed_history_is_returned_unchanged`
    here.

    Pure and idempotent: `clean_history(clean_history(m)) == clean_history(m)`
    for every `m`, and no wall clock is read. That is not tidiness, it is the
    prompt-cache-prefix guarantee - a repair that stamped the current time into
    its output would change the bytes of an otherwise identical prompt on every
    turn and defeat caching in Ollama and every cloud provider alike.
    """
    messages = drop_orphaned_tool_results(messages)
    return repair_dangling_tool_calls(
        messages, repair_last_response=repair_last_message
    )


def repair_orphans(messages: "list[dict]", reason: str) -> "list[dict]":
    """Close out every unanswered call in `messages`, for a turn that has ended.

    The turn-level counterpart to `repair_dangling_tool_calls`, and deliberately
    a separate function. A turn that stopped because it ran out of budget or
    because it was cancelled has *finished* - there is no in-flight frontier to
    leave alone, and every call it made will never be answered. Repairing here
    writes into `_history` and the transcript, so the conversation the next turn
    and the next process read is internally consistent.

    Pure, like every other function here: the caller decides whether to record
    the results or discard them.
    """
    unpaired = unpaired_tool_calls(messages)
    if not unpaired:
        return messages

    by_owner: dict[int, list[dict]] = {}
    for call in unpaired:
        owner = _owning_index(messages, call)
        if owner is not None:
            by_owner.setdefault(owner, []).append(call)

    out: list[dict] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        out.append(message)
        calls = by_owner.get(index)
        if not calls:
            index += 1
            continue
        # Same placement rule as `repair_dangling_tool_calls`, and for the same
        # reason: behind the results that already exist, never in front of them.
        end = index + 1
        while end < len(messages) and _is_tool_result(messages[end]):
            out.append(messages[end])
            end += 1
        out.extend(synthesized_tool_result(call, reason) for call in calls)
        index = end

    logger.info("Turn ended with %d unanswered tool call(s); recorded a result "
                "saying so", sum(len(v) for v in by_owner.values()))
    return out
