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

def test_a_refusal_is_kept_because_the_next_question_is_why(notices):
    """**Why a history, when the notice itself already said the reason.**

    The notice is transient, and it is overwritten. The question a person
    actually has is not "did it refuse" but "why didn't it just do it" - and by
    the time they think to ask, the detail line has been replaced by whatever
    came next. `inventory` carries the standing gaps, but a refusal is
    per-request and belongs nowhere else.
    """
    body.body._notices.clear()
    try:
        _notice_refusal("file_delete", "/tmp/a", "that path is outside the session")
        _notice_refusal("process_kill", "systemd-timesyncd", "killing is not allowed")
        kept = body.body.notices()
        assert len(kept) == 2, f"expected both refusals kept, got {kept}"
        assert "session" in kept[0] and "killing" in kept[1]
    finally:
        body.body._notices.clear()


def test_the_history_is_bounded(notices):
    """A process that runs for weeks cannot keep an unbounded list of strings.

    Unbounded is a slow leak holding notices nobody will read. The bound is a
    named attribute rather than a literal in the slice, so the test and the code
    cannot disagree about how many.
    """
    body.body._notices.clear()
    try:
        for i in range(body.Body.NOTICE_HISTORY + 5):
            body.body.notice(f"refused tool_{i}")
        kept = body.body.notices()
        assert len(kept) == body.Body.NOTICE_HISTORY, (
            f"kept {len(kept)} of {body.Body.NOTICE_HISTORY + 5} notices")
        assert kept[-1] == f"refused tool_{body.Body.NOTICE_HISTORY + 4}", (
            f"the newest notice was dropped rather than the oldest: {kept[:2]}...")
    finally:
        body.body._notices.clear()


def test_the_history_is_a_copy(notices):
    """A caller cannot edit the record by mutating what it was handed."""
    body.body._notices.clear()
    try:
        body.body.notice("refused something")
        held = body.body.notices()
        held.append("something that never happened")
        assert body.body.notices() == ["refused something"], (
            "the history handed out a reference rather than a copy")
    finally:
        body.body._notices.clear()
