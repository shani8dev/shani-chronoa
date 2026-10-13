"""Chronoa could send a text from this person's phone without asking them.

`tools.register_hook(pre=..., post=...)` existed, documented as the way dispatch
is "reduced to the two edges Chronoa's monolithic dispatch actually needs", with
`hooks()` to make the running set inspectable. **It had zero callers in the
entire tree.** The hook mechanism was built and left dormant.

And the hook written for it was a consent gate. `skills/phone.py` says:

    Sending and calling also need the person to confirm **this** message to
    **this** recipient. A skill runs in a sandbox child with no window, so the
    question is asked by `confirm_outgoing`, a dispatcher pre-hook that runs in
    the application process (registered in `tools.py`).

That sentence is a promise. `confirm_outgoing` had no reference anywhere, so the
registration line was never written - and the approval file, the 120-second
one-shot, the `APPROVAL_ARG` scrub that refuses a model-supplied approval, and the
"child sends only what that approval says" check were all built, all reachable
from nowhere, and `send` and `call` were dispatched with **no confirmation at
all**.

**This is the same shape as the six dead ends closed earlier in the session** - a
documented route with nothing behind it - but it is the only one where the
consequence is somebody else's inbox.

Installed in `_dispatch`, not at application startup: a consent check that only
exists while a window is open is not a consent check, and the CLI, a skill run
and a scheduled task all dispatch too.

**The assertions are on refusals, not on registration.** A hook sitting in a list
proves nothing; what matters is that a send is stopped and says why.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import tools  # noqa: E402


@pytest.fixture
def phone_gate_on(monkeypatch):
    """The gate open, so what refuses is the confirmation hook and not the key.

    With `phone-messages-send-enabled` off the hook returns `None` on purpose -
    the child names that key - so a test in that state proves nothing about the
    hook at all.
    """
    import shani_chronoa.config as cfg

    original = cfg.ChronoaConfig.get_bool
    monkeypatch.setattr(
        cfg.ChronoaConfig, "get_bool",
        lambda self, k, d=False: True if "phone" in k else original(self, k, d))


def test_the_confirmation_hook_is_registered():
    """The mechanism had no callers at all, so this is the smallest claim that
    was false before."""
    tools._dispatch("nothing", {})          # installs, once

    pre, _post = tools.hooks()
    assert "confirm_outgoing" in [f.__name__ for f in pre], (
        f"the outgoing-call confirmation hook is not registered, so send and "
        f"call dispatch unconfirmed. Registered pre-hooks: {[f.__name__ for f in pre]}")


def test_an_automatic_rule_cannot_send_a_text(phone_gate_on):
    """**The consequence.** A trigger is not the person."""
    tools._dispatch("nothing", {})
    result = tools._dispatch("phone",
                             {"action": "send", "to": "+15551234567", "text": "hi"},
                             origin="trigger")

    said = getattr(result, "text", "") or ""
    assert "automatic rule" in said, (
        f"an automatic rule was able to send a text unconfirmed: {said!r}")


def test_a_person_asked_does_not_bypass_confirmation(phone_gate_on):
    """**The subtle one.** Asking in chat is not confirming on screen. The two
    cases are different refusals and both must refuse."""
    tools._dispatch("nothing", {})
    result = tools._dispatch("phone",
                             {"action": "send", "to": "+15551234567", "text": "hi"})

    said = getattr(result, "text", "") or ""
    assert "Nothing was sent" in said, (
        f"a model-initiated send reached the dialer without a confirmation: {said!r}")
    assert "confirm" in said.lower(), said


def test_the_hook_refuses_to_trust_a_model_supplied_approval(phone_gate_on):
    """`confirm_outgoing` pops `APPROVAL_ARG` before anything else: "never trust a
    model-supplied approval". Unwired, that scrub protected nothing."""
    from shani_chronoa.skills.phone import APPROVAL_ARG

    tools._dispatch("nothing", {})
    result = tools._dispatch(
        "phone",
        {"action": "send", "to": "+15551234567", "text": "hi",
         APPROVAL_ARG: {"recipient": "+15551234567", "text": "hi"}})

    said = getattr(result, "text", "") or ""
    assert "Nothing was sent" in said, (
        f"a model-supplied approval was accepted: {said!r}")


def test_reading_the_phone_is_not_blocked_by_the_hook(phone_gate_on):
    """**The control.** A hook that stops everything is as broken as one that
    stops nothing. `status` must still get through."""
    tools._dispatch("nothing", {})
    result = tools._dispatch("phone", {"action": "status"})

    said = getattr(result, "text", "") or ""
    assert "confirm" not in said.lower(), (
        f"the confirmation hook blocked a read-only action: {said!r}")
