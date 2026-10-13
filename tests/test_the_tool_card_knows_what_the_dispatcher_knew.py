"""The transcript card's "did it work" is the dispatcher's verdict, not a guess.

**Measured, before this change**, through the real dispatcher
(`tools.execute_tool_outcome`) and read back through the two places that used to
guess:

| dispatch | verdict | `ran` | what `_tool_ok` said |
|---|---|---|---|
| `create_document` (a real write) | `UNVERIFIED` | True | ok - correct |
| `delete_file`, post-condition says the effect is absent | **`FAILED`** | True | **ok - WRONG** |
| a tool that does not exist | `UNVERIFIED` | **False** | **ok - WRONG** |
| a call missing a required argument | `UNVERIFIED` | **False** | **ok - WRONG** |

The post-condition row is the one that matters: `Verdict.FAILED` is the skill's
own statement that its effect is not there, and the card was drawn saying it
happened. `DispatchResult`'s docstring names the failure this is - *"a refusal
starts to look indistinguishable from a success that nobody checked"* - and
`execute_tool_outcome` exists to carry it.

**The two alternatives were tried and are both worse**, so the seam does not
move:

- calling `execute_tool_outcome` **alongside** `execute_tool` runs every dispatch
  **twice** - a write performed twice because the caller wanted to know whether
  it worked;
- replacing one with the other moves the seam, and **59 test files patch
  `execute_tool`** to intercept a turn's tool calls. They ran the real skill
  instead of their fixture, and a turn that used to write a stub wrote a real
  file.

So `execute_tool` stays the seam and *leaves* the dispatch where
`tools.take_last_dispatch()` finds it. **A patched `execute_tool` sets nothing,
so the take answers `None`** and every caller falls back to what it did before -
which is the right answer for a call whose dispatcher nobody reached.

Every test here drives the **real seam**, not `_tool_ok` with a hand-made
string: a test that reaches past the call site to the function behind it is
testing the function, and the defect this file describes is the seam.
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]
                       / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import assistant, tools, verification  # noqa: E402


# --------------------------------------------------------------------------
# the two halves separately: the side channel, and the answer built from it


def test_the_dispatch_is_left_where_a_caller_can_take_it():
    """**One call, both facts.** This is the whole reason the change exists."""
    tools.take_last_dispatch()                     # clear any previous
    text = tools.execute_tool("get_datetime", {})

    taken = tools.take_last_dispatch()
    assert isinstance(text, str) and text
    assert taken is not None, "execute_tool returned prose and left no dispatch"
    assert taken.text == text, "the prose and the dispatch are two answers"


def test_taking_twice_answers_none_rather_than_a_stale_verdict():
    """**A stale verdict is worse than no verdict.** It is the previous tool's.

    A caller that forgets to ask, or asks twice, must not be handed a verdict
    about a different call - which on a destructive tool is a wrong answer about
    somebody's file.
    """
    tools.take_last_dispatch()
    tools.execute_tool("get_datetime", {})
    first = tools.take_last_dispatch()
    second = tools.take_last_dispatch()

    assert first is not None
    assert second is None, "the second take returned the first call's verdict"


def test_a_dispatch_that_raises_leaves_no_verdict_from_before_it(monkeypatch):
    """The slot is cleared on **entry**, so a raising call cannot inherit one.

    An unknown tool does *not* raise - it returns `"Unknown tool: ..."` - so this
    stubs `_dispatch` itself rather than guessing at a tool that might throw.
    A probe that cannot produce the failure it names proves nothing.

    **The first dispatch is deliberately left in the slot**, and not taken before
    the raising call. Taking it first empties the slot, so the assertion passes
    whether or not the entry-clear exists - which is exactly what the mutation run
    measured, and the reason this test was rewritten rather than the code.
    """
    tools.take_last_dispatch()
    tools.execute_tool("get_datetime", {})

    def boom(*a, **k):
        raise RuntimeError("the child died")

    monkeypatch.setattr(tools, "_dispatch", boom)
    with pytest.raises(RuntimeError):
        tools.execute_tool("get_datetime", {})

    stale = tools.take_last_dispatch()
    assert stale is None, (
        f"a call that raised inherited the previous call's verdict: {stale!r}")


def test_the_slot_is_per_thread_and_not_a_global():
    """**A turn dispatches on an executor thread; the panels on the GTK one.**

    A shared slot would hand one of them the other's verdict, so this fails
    closed by being per-thread rather than by luck.
    """
    import threading

    tools.take_last_dispatch()
    tools.execute_tool("get_datetime", {})

    seen = []

    def other():
        seen.append(tools.take_last_dispatch())

    t = threading.Thread(target=other)
    t.start()
    t.join()

    assert seen == [None], "another thread was handed this thread's verdict"
    # and this thread's own is untouched, because the other thread took its own
    assert tools.take_last_dispatch() is not None


# --------------------------------------------------------------------------
# the answer the card is built from, measured on real dispatches


def _ok_for(name, arguments):
    """`_tool_ok`'s answer for a real dispatch, through the real seam."""
    text, outcome = assistant._run_tool(name, arguments, "user")
    return assistant._tool_ok(text, name, outcome)


def test_a_call_whose_post_condition_says_the_effect_is_absent_is_not_ok():
    """**`FAILED` is the skill saying it did not work.** The strongest case."""
    out = tools.execute_tool_outcome(
        "delete_file", {"path": "/tmp/opencode/definitely-not-here-9f3b71"})
    assert out.verdict is verification.Verdict.FAILED, (
        f"the post-condition did not fail, so this proves nothing: {out.text!r}")
    assert out.ran is True

    assert _ok_for("delete_file", {"path": "/tmp/opencode/definitely-not-here-9f3b71"}) is False, (
        "a dispatch the post-condition refused was drawn as done")


def test_a_call_that_never_ran_is_not_ok():
    """`ran=False` is the other half of the answer, whatever the verdict says.

    An unknown tool and a missing argument both report `UNVERIFIED`, because
    there was nothing to verify - so reading the verdict alone gets these wrong.
    """
    unknown = tools.execute_tool_outcome("no_such_tool_at_all", {})
    assert unknown.ran is False
    assert _ok_for("no_such_tool_at_all", {}) is False

    missing = tools.execute_tool_outcome("create_document", {"title": "x"})
    assert missing.ran is False, f"expected a refusal: {missing.text!r}"
    assert _ok_for("create_document", {"title": "x"}) is False


def test_an_impossible_verdict_is_not_ok_either():
    """**`IMPOSSIBLE` alongside `FAILED`, and constructed on purpose.**

    `verification.py` documents the distinction - *"a callable post-condition may
    return the IMPOSSIBLE sentinel: 'this action could not have been attempted
    as asked', which is IMPOSSIBLE, never FAILED"* - and `_tool_ok` treats both
    as not-ok. **No shipped skill produces it**: none of the 256 returns the
    sentinel, so this cannot be witnessed end to end, and a test that pretended
    to would be asserting something no machine on this tree can produce.

    **`ran=True`, and that is the whole difficulty.** `DispatchResult`'s
    docstring lists four reachable states and `IMPOSSIBLE` is not among them -
    the fourth is `ran=False, UNVERIFIED`, "never executed". With `ran=False`
    this test passes whether or not `IMPOSSIBLE` is in `_tool_ok`'s pair, because
    `bool(outcome.ran)` already decides the answer: **the first version of it was
    exactly that and the mutation run reported it surviving.** `ran=True` is
    where the state is actually reachable - `_replay_put` guards with
    `not result.ran or result.verdict not in _REPLAYABLE_VERDICTS` and names
    "an impossible request" among the things it declines to cache, which is a
    caller reasoning about this combination deliberately.

    So the input is the module's own `DispatchResult`, built once, and the claim
    is the one that is at risk: that dropping `IMPOSSIBLE` from the pair is a
    behaviour change rather than a tidy-up.
    """
    impossible = tools.DispatchResult(
        "Could not be attempted as asked. (impossible: no such device)",
        verification.Verdict.IMPOSSIBLE, True, "no such device")

    assert impossible.ran is True, "the fixture must leave `ran` to decide nothing"
    assert assistant._tool_ok(impossible.text, "some_tool", impossible) is False, (
        "an action that could not be attempted was drawn as having happened")


def test_a_call_that_ran_is_ok():
    """**The control.** Without it, "not ok" passes on a dispatcher that says
    no to everything - which is what a mutation of this fix looks like."""
    assert _ok_for("get_datetime", {}) is True


def test_a_real_write_is_ok_and_declares_what_it_made():
    """**Both halves of the change, on one dispatch.**"""
    import tempfile
    target = pathlib.Path(tempfile.mkdtemp(prefix="verdict-probe-")) / "probe.md"

    text, outcome = assistant._run_tool(
        "create_document",
        {"title": "Probe", "body": "# probe\n\nhello", "path": str(target)},
        "user")

    assert assistant._tool_ok(text, "create_document", outcome) is True
    assert outcome is not None, "a real dispatch produced no verdict"
    assert str(target) in [str(a) for a in outcome.artifacts], (
        f"the dispatcher declared {outcome.artifacts!r} for a file it wrote")


# --------------------------------------------------------------------------
# the artifact reach - the defect the whole plumbing exists for


class _OneCallLLM:
    """Emits one tool call, then a plain answer - enough to drive a whole turn.

    Shaped like the call the assistant actually makes (`chat_message`, async,
    `tools=`), not like the bug: a fake shaped like the call rather than like the
    thing being called tests the bug and passes.
    """

    def __init__(self, name, arguments):
        self._name = name
        self._arguments = arguments
        self.turns = 0

    async def chat_message(self, messages, tools=None, stream=False, **kw):
        self.turns += 1
        if self.turns == 1:
            import json
            return {"role": "assistant", "tool_calls": [
                {"id": "call-1", "function": {
                    "name": self._name,
                    "arguments": json.dumps(self._arguments)}}]}
        return {"role": "assistant", "content": "done"}


def _drive_a_turn(name, arguments):
    """One real turn. Returns what the card and the sink each saw.

    **Both**, because the defect was that they were computed separately and could
    disagree - witnessed through either alone is half the claim.
    """
    import asyncio
    from shani_chronoa.events import EventSink

    card, rail = [], []

    def five(n, a, r, ok, artifacts):
        rail.append((n, str(r), ok, tuple(artifacts)))

    a = assistant.Assistant(_OneCallLLM(name, arguments))
    asyncio.run(a.handle(
        "do the thing",
        on_tool_result=lambda n, _a, r, ok: card.append((n, str(r), ok)),
        sink=EventSink(on_tool_finish=five)))
    return card, rail


def test_a_file_a_skill_made_reaches_the_transcript_through_a_real_turn():
    """**The end-to-end witness, and the one the shipped suite was missing.**

    `test_a_made_file_is_shown.py` asserts the *type* carries the field and that
    the *card* renders it; none of its tests drove the value through the seam, so
    the feature was dead end to end with every test green. This drives a whole
    turn with the real dispatcher and reads what the sink was handed.
    """
    import tempfile
    target = pathlib.Path(tempfile.mkdtemp(prefix="verdict-turn-")) / "made.md"

    card, rail = _drive_a_turn(
        "create_document",
        {"title": "Made", "body": "# made\n\nbody", "path": str(target)})

    assert card, "the turn produced no tool result at all"
    assert rail, "the sink was never fired - the artifacts have no other route"
    name, _text, ok, artifacts = rail[0]
    assert name == "create_document"
    assert ok is True, "a real write was reported as not ok"
    assert str(target) in [str(a) for a in artifacts], (
        f"no artifact reached the transcript: {artifacts!r} - the dispatcher "
        f"declared them and the loop dropped them")


def test_a_refusal_reaches_both_sites_with_the_same_answer():
    """**Two answers to one question, twenty lines apart, measured disagreeing.**

    The sink carried its *own* marker list (`("error", "refused")`) against
    `_tool_ok`'s four. A `Not run. ...` refusal is in `_tool_ok`'s list and not
    in the sink's, so before this change the card read **not ok** and the rail
    read **ok** about the same call - the exact failure the module's own
    docstring warns about, *"a refusal starts to look indistinguishable from a
    success that nobody checked"*.

    Driven through a real turn, because a test of one site cannot see the other.
    """
    card, rail = _drive_a_turn("create_document", {"title": "no body given"})

    assert card and rail, "the turn produced no tool result at all"
    assert card[0][2] is False, "the card reported a refused call as done"
    assert rail[0][2] is False, (
        "the sink reported a refused call as done while the card did not - the "
        "two are computing 'did it work' separately and can disagree")


def test_a_patched_execute_tool_falls_back_rather_than_claiming_a_verdict(monkeypatch):
    """**59 test files patch `execute_tool`, and they must keep working.**

    A patch sets nothing, so the take answers `None` - and `_tool_ok` says so
    from the prose, which is the only thing it has. The fallback is documented
    as a guess; what matters here is that it is *reached* and does not raise.
    """
    monkeypatch.setattr(assistant, "execute_tool", lambda *a, **k: "Saved /tmp/x.md")

    text, outcome = assistant._run_tool("create_document", {}, "user")
    assert text == "Saved /tmp/x.md"
    assert outcome is None, "a patched execute_tool invented a dispatch"
    assert assistant._tool_ok(text, "create_document", outcome) is True


def test_a_patched_execute_tool_is_still_honest_about_a_refusal(monkeypatch):
    """The fallback is wrong, but it is not vacuous: a refusal still reads false."""
    monkeypatch.setattr(assistant, "execute_tool",
                        lambda *a, **k: "Not run. create_document needs 'body'.")

    text, outcome = assistant._run_tool("create_document", {}, "user")
    assert outcome is None
    assert assistant._tool_ok(text, "create_document", outcome) is False