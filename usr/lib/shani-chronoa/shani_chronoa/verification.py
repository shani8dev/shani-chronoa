"""Post-condition verification for actuator skills.

Why this exists
---------------

A skill returns a *string*, and that string is what the assistant repeats to the
user as fact. For most skills that string is the skill's own account of what it
did - `notify.py` returns "Notification sent: <summary>." after checking a
subprocess exit code. So the claim that an action happened is produced by the
same component that performed the action.

That is the failure mode LITMUS (arXiv:2605.10779) names *Execution
Hallucination*: an agent reports a state that does not hold, and it is
invisible to any check that only reads what the agent said. Their measurement
is that frontier models still execute 40.64% of high-risk operations, and that
verbal refusal is not evidence of safety because the operation may already have
completed.

It is also why Reflexion's own ablation (arXiv:2303.11366) matters here:
self-reflection *without* an external signal scores 0.52 on HumanEval-Rust
against a 0.60 no-reflection baseline - worse than doing nothing. The paper's
conclusion is that an agent cannot determine whether its own work is correct
without an external check.

So: an effect the assistant can observe independently must be observed, not
asserted. A skill module may declare a `POST_CONDITION` - a command run *after*
the skill, whose exit status is the authority. The rule is deliberately strict
in one direction: an action with no post-condition is reported as **unverified**
rather than as success. Silence about confidence is the bug being fixed.

The LLM never sees or supplies a post-condition. It is declared by the skill
author in code, exactly like the sandbox level and the consent key, so this
cannot become a channel for the model to mark its own homework.
"""

import logging
import os
import subprocess
from enum import Enum
from typing import NamedTuple, Optional

logger = logging.getLogger(__name__)

_TIMEOUT = 15


class Verdict(Enum):
    """How much is actually known about whether an action took effect."""

    VERIFIED = "verified"
    FAILED = "failed"
    UNVERIFIED = "unverified"
    #: The action could not have been attempted as asked, and the skill says
    #: so with a reason. AgentScope's pipeline calls this a terminal
    #: `impossible` goal - it is not a failure of execution, it is a
    #: statement that the requested effect is not reachable from here.
    #:
    #: It is the third shape of negative news and it has to be a verdict
    #: rather than prose, because the other two are the only things the
    #: outcome model and the bandit can see. A skill that answers "I can't
    #: do that, ffmpeg is not installed" writes that into a result string
    #: and the string is what the model reads - which is right for the
    #: model - but the *learning* layer then sees only "the tool ran and
    #: said something", which is `unverified`, and a refusal starts to look
    #: indistinguishable from a success that nobody checked. The bandit's
    #: whole point is to stop pulling an arm that does not pay, and an arm
    #: that cannot pay for a class of request is the most useful thing it
    #: could be told.
    IMPOSSIBLE = "impossible"


#: The singleton a post-condition returns to declare impossibility. It is
#: not a bool, so it cannot be returned by accident: a check that returns
#: `True`/`False` is a yes/no about an effect, and `IMPOSSIBLE` is a
#: different question entirely ("this was never going to work"). Keeping
#: them separate in the return type is what stops a check that can only
#: answer yes/no from being read as one that can.
IMPOSSIBLE = "chronoa-impossible"


class Result(NamedTuple):
    verdict: Verdict
    evidence: str = ""

    @property
    def suffix(self) -> str:
        """The text to append to a skill's own result string."""
        if self.verdict is Verdict.VERIFIED:
            return ""
        if self.verdict is Verdict.FAILED:
            return f" (VERIFICATION FAILED: {self.evidence})"
        if self.verdict is Verdict.IMPOSSIBLE:
            return f" (impossible: {self.evidence})"
        return " (unverified - this action reports success but nothing observed it)"


FAILED_MARKER = "VERIFICATION FAILED"
IMPOSSIBLE_MARKER = "impossible:"


def verdict_from_text(text: str) -> Verdict:
    """Recover a verdict from a dispatch result string.

    The suffix above is the only place that string is produced, so recognising
    it here is not string-matching a stranger's prose - it is the reverse of a
    function defined a few lines above.

    It exists because the trigger engine dispatches through the same plain
    `-> str` interface every other caller uses, and that interface loses the
    verdict. Without this, a rule whose actuator failed its post-condition is
    indistinguishable from one that worked, and gets to sit in a cooldown
    window having done nothing.

    UNVERIFIED rather than VERIFIED when the text says nothing: an action that
    merely failed to declare a post-condition has not been shown to have
    worked, but it also has not been shown to have failed, and treating that as
    success is what this whole module exists to avoid.
    """
    if FAILED_MARKER in (text or ""):
        return Verdict.FAILED
    if IMPOSSIBLE_MARKER in (text or ""):
        return Verdict.IMPOSSIBLE
    return Verdict.UNVERIFIED


def post_condition_for(handler_module: str) -> Optional[object]:
    """The post-condition a module declares, if any.

    Either an argv list, or a callable taking the skill's arguments and
    returning True/False (or a `(ok, evidence)` pair). The callable form exists
    because a meaningful check often has to discover its own tool first - the
    clipboard skill has to ask whether this session is Wayland or X11 before it
    knows which reader to verify with, and a static argv cannot do that.

    Read off the module rather than added to the `Skill` NamedTuple, matching
    the existing `wants_by_reference` convention: extending the tuple would
    break every skill in the tree to express an optional capability.
    """
    import importlib

    try:
        module = importlib.import_module(handler_module)
    except Exception:  # noqa: BLE001 - a missing module is "no post-condition"
        return None
    declared = getattr(module, "POST_CONDITION", None)
    if callable(declared):
        return declared
    if not declared or not isinstance(declared, (list, tuple)):
        return None
    argv = [str(part) for part in declared]
    return argv or None


def _call(check, arguments: dict, tool: Optional[str]):
    """Call a post-condition with the tool name when it takes one.

    One module can hold several tools - `volume` has set_volume and set_mute,
    `clipboard` a getter beside its setter - and a check that cannot tell which
    one ran has to guess. The guess was wrong in the one module that had a
    post-condition: reading the clipboard was verified as a write of the empty
    string, and any non-empty clipboard reported the *read* as FAILED.
    """
    import inspect

    try:
        takes_tool = len(inspect.signature(check).parameters) >= 2
    except (TypeError, ValueError):
        takes_tool = False
    return check(arguments, tool) if takes_tool else check(arguments)


def verify(handler_module: str, arguments: Optional[dict] = None, tool: Optional[str] = None) -> Result:
    """Run the module's post-condition and report what it observed.

    Never raises. A verification step that can crash the action path is worse
    than no verification, so every failure mode collapses to UNVERIFIED rather
    than propagating.

    A callable post-condition may return None: "this call changed nothing I can
    check" (a status query, a dry run). That is UNVERIFIED, never FAILED -
    reporting a read as a failed write is a confident wrong answer.

    A callable post-condition may return the IMPOSSIBLE sentinel: "this action
    could not have been attempted as asked". That is IMPOSSIBLE, never FAILED -
    reporting an impossible action as a failure is a confident wrong answer.
    """
    declared = post_condition_for(handler_module)
    if declared is None:
        return Result(Verdict.UNVERIFIED, "no post-condition declared")

    if callable(declared):
        try:
            outcome = _call(declared, dict(arguments or {}), tool)
        except Exception as exc:  # noqa: BLE001 - a broken check is not a failure
            return Result(Verdict.UNVERIFIED, f"post-condition raised: {type(exc).__name__}: {exc}")
        if outcome is None:
            return Result(Verdict.UNVERIFIED, "nothing this call changed can be checked")
        if outcome is IMPOSSIBLE:
            return Result(Verdict.IMPOSSIBLE, "action impossible as asked")
        if isinstance(outcome, tuple) and len(outcome) == 2:
            ok, evidence = bool(outcome[0]), str(outcome[1])
        else:
            ok, evidence = bool(outcome), ""
        return Result(Verdict.VERIFIED if ok else Verdict.FAILED, evidence)

    try:
        completed = subprocess.run(
            declared,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            check=False,
            env={**os.environ, "CHRONOA_VERIFY_ARGS": repr(arguments or {})},
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return Result(Verdict.UNVERIFIED, f"could not run post-condition: {exc}")
    if completed.returncode == 0:
        detail = (completed.stdout or "").strip().splitlines()
        return Result(Verdict.VERIFIED, detail[-1] if detail else "")
    detail = (completed.stderr or completed.stdout or "").strip().splitlines()
    return Result(Verdict.FAILED, detail[-1] if detail else f"exit {completed.returncode}")
