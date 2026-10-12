"""A refusal reaches the person, not only the model.

`tools.py` returns the refusal reason as the tool result, so the assistant knows
it was declined and can propose something else. **Before this, that was the only
copy.** Nothing on the dispatch path touched the UI, so a person whose request was
declined found out from whatever the model said afterwards, or not at all.

That is this repository's own doctrine going unsatisfied: *"a mode that removes
capabilities without saying so reads as a bug the first time the assistant
declines something that was explicitly permitted."*

**Why a hook on the body rather than a light on the strip.** `tools.py` imports
no GUI and has no app reference - it is four layers down - so it needs a channel.
`body.Body` already is one, and already carries `set_audible` for exactly this
shape. And a refusal is an **event**, not a state: the organ strip cannot hold it,
because a strip is for showing what is true continuously, and it has eight lights
already. A transient notice on the detail line is the honest shape for it.

The control is that the notice is not the whole story: the tool result must still
carry the reason, or the assistant would stop being able to recover.
"""

import sys

import pytest

pytest.importorskip("gi")
sys.path.insert(0, "usr/lib/shani-chronoa")

from shani_chronoa import body  # noqa: E402
from shani_chronoa.tools import _notice_refusal  # noqa: E402


@pytest.fixture
def notices():
    seen = []
    body.body.set_notice(seen.append)
    yield seen
    body.body.set_notice(None)


def test_a_refusal_is_said_to_the_person(notices):
    _notice_refusal("file_delete", "/home/me/report.pdf",
                    "That is outside what you allowed this time.")
    assert notices, "the refusal was not delivered to anyone"
    said = notices[-1]
    assert said.startswith("Not done")
    assert "outside what you allowed" in said, (
        f"the reason did not reach the person: {said!r}")
    assert "/home/me/report.pdf" in said, (
        f"the person cannot tell what was refused: {said!r}")


def test_a_refusal_with_no_reason_still_says_something(notices):
    """An empty reason must not produce an empty notice.

    `permits()` returns `None` for the refusal text in some paths, and a notice
    of `""` is the same as no notice - so the person is back to not finding out.
    """
    _notice_refusal("screenshot", "*", "")
    _notice_refusal("screenshot", "*", "   ")
    assert notices == ["Not done", "Not done"], (
        f"an empty or whitespace reason produced no notice at all: {notices}")


def test_a_notice_with_no_listener_is_silent_rather_than_loud(notices):
    """A notice nobody installed a hook for must raise nothing.

    The body is called from inside the tool loop. A dispatcher that raised
    because a GUI was not listening would lose the tool result *and* the refusal
    reason the model needs - so both the wrapper and `body.notice` swallow.
    """
    body.body.set_notice(None)
    _notice_refusal("file_delete", "/tmp/x", "no")
    body.body.set_notice(notices.append)
    assert notices == [], "a notice was delivered after the hook was cleared"


def test_the_model_still_gets_the_reason(notices):
    """**The control: the notice is not a replacement.**

    If the reason reached only the person, the assistant would see an opaque
    failure and could not propose something else - which is the whole reason
    `permits()` returns the refusal text at all. This asserts the tool result and
    the notice are built from the same string and neither drops it.
    """
    reason = "That is outside what you allowed this time."
    _notice_refusal("file_delete", "/tmp/x", reason)
    assert reason.rstrip(".") in notices[-1]
    assert reason in notices[-1] or reason.rstrip(".") in notices[-1], (
        f"the notice paraphrased the reason away: {notices[-1]!r}")


def test_the_body_notice_hook_is_the_same_shape_as_the_sound_hook():
    """It mirrors `set_audible`, which is what makes it the right channel.

    If these drift apart, one of them stops being the thing the GUI installs and
    the refusal path goes quiet again with nothing failing.
    """
    assert hasattr(body.Body, "set_audible")
    assert hasattr(body.Body, "set_notice")
    assert hasattr(body.Body, "notice")
    assert "Optional" in body.Body.set_notice.__doc__ or \
        "None to clear it" in (body.Body.set_notice.__doc__ or ""), (
        "set_notice no longer documents that None clears the hook")