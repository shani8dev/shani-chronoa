"""In-band tool failure: a skill ran but the effect did not hold.

crewAI's `ToolFailure` (`crewai/tools/tool_failure.py:1-50`) recognised by
dispatch: ran-but-didn't-succeed is a *result*, distinct from a crash (ran
nothing) and from a success string the learning layer would read as a win.

In its own module rather than in `tools.py` so both sides of the subprocess
transport can import it without the import graph of the dispatcher: the
sandboxed child does `from shani_chronoa.toolfailure import ToolFailure`,
which must never pull in sandbox/executor/trim imports just to raise.
"""


class ToolFailure(Exception):
    """Raise from a skill handler to report a failed effect, in-band.

    The child process that ran the skill serialises it as `MARKER +
    message` on stdout, and the dispatcher converts that exact marker into a
    `VERDICT=FAILED, ran=True` result. The stdout round-trip means a plain
    return of the marker string works too, for a skill that wants to send a
    failure without importing anything.
    """

    #: The stdout prefix the child writes in place of a raised traceback.
    #: Acts as a sentinel rather than relying on exit codes, because a
    #: non-zero exit already means "nothing ran" and this is the opposite
    #: claim: the skill ran, and its effect did not hold.
    MARKER = "__CHRONOA_TOOL_FAILURE__"

    def __str__(self) -> str:
        return self.args[0] if self.args else "the tool failed"
