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
from typing import List, NamedTuple, Optional

logger = logging.getLogger(__name__)

_TIMEOUT = 15


class Verdict(Enum):
    """How much is actually known about whether an action took effect."""

    VERIFIED = "verified"
    FAILED = "failed"
    UNVERIFIED = "unverified"


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
        return " (unverified - this action reports success but nothing observed it)"


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


def verify(handler_module: str, arguments: Optional[dict] = None) -> Result:
    """Run the module's post-condition and report what it observed.

    Never raises. A verification step that can crash the action path is worse
    than no verification, so every failure mode collapses to UNVERIFIED rather
    than propagating.
    """
    declared = post_condition_for(handler_module)
    if declared is None:
        return Result(Verdict.UNVERIFIED, "no post-condition declared")

    if callable(declared):
        try:
            outcome = declared(dict(arguments or {}))
        except Exception as exc:  # noqa: BLE001 - a broken check is not a failure
            return Result(Verdict.UNVERIFIED, f"post-condition raised: {type(exc).__name__}: {exc}")
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
