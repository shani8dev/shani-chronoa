"""Every app action is registered, and one that fails to register is not silent.

**This test exists because eight actions were silently dead.** `_create_actions()`
called `self._answer_mode()` to set the `answer-mode` action's initial state, and
the method did not exist - so every startup raised
`AttributeError: 'ChronoaApplication' object has no attribute '_answer_mode'`.
`GApplication` swallows an exception out of `do_startup`, so nothing said so, and
action registration stopped at that line. Measured, the actions registered after
it never registered at all:

    answer-mode, toggle-barge-in-vad, toggle-model-download,
    download-speech-model, toggle-debug, stop-speaking, open-settings, setup

which is the gear button and Ctrl+, opening nothing, the setup wizard action dead,
and stop-speaking doing nothing. **Every launch, silently, with a green suite** -
because four test files each called `_create_actions()` directly and asserted
their *own* action, and the three whose action came after the failure point were
failing in files nothing had run recently.

The scattered guards were not wrong, they were just shaped like the bug: one per
feature, each blind to the other seven. This is the one guard that is shaped like
the *failure* rather than like any single action.

**How it works, and why it needs no display.** The real constructor runs (an
uninitialised GObject raises from `add_action`, which would fail this test for the
wrong reason), `add_action` is replaced with a recorder, and `_create_actions()` is
driven once. The assertion is then on the complete set, so a registration that
dies partway through leaves a hole in the set rather than a traceback nobody sees.

Adding an action is a deliberate act: this test fails and names what is missing,
which is the point. It is not a list that rots quietly - it is one that has to be
edited on purpose, by someone who has just added an action.
"""

from __future__ import annotations

import pathlib
import sys

PKG = pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"

#: Every action `ChronoaApplication._create_actions()` registers, as of the commit
#: that added this test. The two D-Bus-only names from AGENTS.md's GAction audit
#: (`show-page`, `show-window`) are in here: they are registered like every other
#: action and reached over the bus, which is why nothing in the tree names them.
EXPECTED = [
    # 2026-10-15: a question answered from its notification; the top-bar
    # indicator's state (read-only); the composer's speaker toggle.
    "answer-question", "status", "speak-replies",
    "answer-ambient", "answer-cloud-model", "answer-egress", "answer-mode",
    "answer-model",
    "answer-permission", "answer-presence", "answer-rules",
    "delete-conversation", "dictate", "download-speech-model",
    "find-in-conversation", "hands-free", "open-browser", "open-settings",
    "quick-ask", "quit", "reply-style", "reset-conversation", "setup",
    "show-body", "show-page", "show-window", "stop-speaking",
    "switch-conversation", "thinking", "answer-plan", "choose-project", "clear-project", "connect-device", "transcribe-file", "setup-page", "reload-mcp", "toggle-auto-start", "toggle-barge-in-vad",
    "toggle-cloud-fallback", "toggle-debug", "toggle-listening",
    "toggle-model-download", "toggle-plan-mode", "toggle-privacy",
    "toggle-rail", "toggle-sidebar", "toggle-start-hidden", "toggle-wake-word",
]


def _registered_actions():
    sys.path.insert(0, str(PKG))
    from shani_chronoa.app.application import ChronoaApplication

    application = ChronoaApplication()
    added = []
    application.add_action = lambda action, *a, **k: added.append(action.get_name())
    application._create_actions()
    return added


class TestEveryActionIsRegistered:

    def test_the_whole_set_is_registered(self):
        added = _registered_actions()
        missing = sorted(set(EXPECTED) - set(added))
        assert not missing, (
            f"these actions are never registered, so nothing can fire them: "
            f"{missing}. A registration that dies partway through leaves exactly "
            f"this hole - `GApplication` swallows the exception out of do_startup "
            f"and the app carries on without them.")

    def test_nothing_unexpected_arrives_quietly(self):
        """The other direction, and it is not a formality: an action registered by
        a *different* path than `_create_actions` would show up here, and one that
        is added without this list being edited means a feature landed whose
        trigger nobody pinned."""
        added = _registered_actions()
        extra = sorted(set(added) - set(EXPECTED))
        assert not extra, (
            f"new actions registered but not in EXPECTED: {extra}. Add them to "
            f"the list deliberately - that is the act this test is asking for.")

    def test_the_set_does_not_shrink_without_an_edit(self):
        """A third possibility: registration succeeds but registers *fewer* than
        the list says, because a line was removed. The count is the assertion that
        a hole is not silently tolerated."""
        added = _registered_actions()
        assert len(added) == len(EXPECTED), (
            f"{len(added)} actions registered, EXPECTED names {len(EXPECTED)} - "
            f"a registration path changed shape")

    def test_the_eight_that_died_are_registered_first(self):
        """**The specific regression.** Each of these is registered after the
        line that used to fail, so each was silently missing. Naming them means a
        future failure at that point names the eight rather than a diff."""
        added = set(_registered_actions())
        for name in ("answer-mode", "toggle-barge-in-vad", "toggle-model-download",
                     "download-speech-model", "toggle-debug", "stop-speaking",
                     "open-settings", "setup"):
            assert name in added, f"{name} is registered after the line that failed before"
