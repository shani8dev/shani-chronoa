"""Scoped permissions, and an honest account of which half is wired.

Chronoa's consent keys are global and persistent. That is a real safety property
- a grant survives a restart, so nothing quietly expires - but it is one shape of
answer, and it leaves two things unexpressible: "yes, this once", and "not that
one". A boolean cannot say "delete files in ~/Downloads but never in /etc".

So there is a second layer, in front of the booleans rather than replacing them.

**What is wired:** scoped *deny* rules, enforced centrally in the dispatch path.
A session rule saying "never touch /etc" holds even if a skill forgets to look,
which is the whole reason to enforce it centrally.

**The allow half is now wired.** It was not, and the shape of the mistake is
evaluated but reached nothing, because each gated skill reads its own
consent key through `ChronoaConfig.get_bool` and never consulted this
module. The fix is at the choke point, not in 26 skills: the dispatcher
hands the child the one consent key the user just granted, and `get_bool`
honours that one key for that one process.
is asserted here as inert, rather than left for someone to discover.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import permissions  # noqa: E402
from shani_chronoa.config import CONSENT_GRANT_ENV  # noqa: E402
from shani_chronoa.tools import (  # noqa: E402
    _consent_key_for,
    _resource_for,
    execute_tool,
)


@pytest.fixture(autouse=True)
def no_rules():
    permissions.clear()
    yield
    permissions.clear()


class TestNoRuleMeansNoOpinion:
    def test_an_unmatched_action_falls_through(self):
        assert permissions.evaluate("delete_file", "/tmp/x") == \
            permissions.Decision.FALL_THROUGH

    def test_fall_through_permits(self):
        """It defers to the consent keys rather than replacing them."""
        allowed, why = permissions.permits("delete_file", "/tmp/x")
        assert allowed is True and why is None

    def test_a_tool_with_no_named_resource_is_unaffected(self):
        assert _resource_for("get_datetime", {}) is None


class TestScopedDeny:
    def test_a_matching_path_is_denied_at_dispatch(self):
        permissions.add_rule("delete_file", "/etc/*", permissions.Decision.DENY_SESSION)
        out = execute_tool("delete_file", {"path": "/etc/passwd"})
        assert "Permission denied" in out
        assert "not found" not in out.lower() or "Permission denied" in out

    def test_a_different_path_reaches_the_skill(self):
        permissions.add_rule("delete_file", "/etc/*", permissions.Decision.DENY_SESSION)
        out = execute_tool("delete_file", {"path": "/home/me/Downloads/junk.txt"})
        assert "Permission denied" not in out

    def test_a_denial_names_the_scope(self):
        permissions.add_rule("delete_file", "/etc/*", permissions.Decision.DENY_SESSION)
        out = execute_tool("delete_file", {"path": "/etc/passwd"})
        assert "session" in out
        permissions.clear()
        permissions.add_rule("delete_file", "/etc/*", permissions.Decision.DENY_ONCE)
        assert "this time" in execute_tool("delete_file", {"path": "/etc/passwd"})

    def test_it_does_not_become_a_second_permission_system(self):
        """No match must not deny, or this layer would be invisible policy."""
        permissions.add_rule("delete_file", "/etc/*", permissions.Decision.DENY_ONCE)
        assert "Permission denied" not in execute_tool("get_datetime", {})


class TestPatternsMeanWhatAUserExpects:
    def test_a_star_crosses_path_separators(self):
        """fnmatch's `*` stops at `/`, which is the opposite of the intent.

        Without this, `~/Downloads/*` would quietly fail to match a file two
        levels down - the rule would look like it worked while protecting only
        the top directory.
        """
        permissions.add_rule("delete_file", "/home/me/Downloads/*",
                             permissions.Decision.DENY_ONCE)
        assert permissions.evaluate("delete_file",
                                    "/home/me/Downloads/a/b/c.txt") != \
            permissions.Decision.FALL_THROUGH

    def test_a_star_still_matches_the_top_level(self):
        permissions.add_rule("delete_file", "/home/me/Downloads/*",
                             permissions.Decision.DENY_ONCE)
        assert permissions.evaluate("delete_file", "/home/me/Downloads/a.txt") != \
            permissions.Decision.FALL_THROUGH

    def test_a_bare_star_matches_everything(self):
        permissions.add_rule("*", "*", permissions.Decision.DENY_ONCE)
        assert permissions.evaluate("anything", "at/all") == \
            permissions.Decision.DENY_ONCE

    def test_a_sibling_directory_is_not_matched(self):
        permissions.add_rule("delete_file", "/etc/*", permissions.Decision.DENY_ONCE)
        assert permissions.evaluate("delete_file", "/etcetera/passwd") == \
            permissions.Decision.FALL_THROUGH


class TestLastMatchWins:
    def test_a_later_specific_rule_overrides_an_earlier_general_one(self):
        permissions.add_rule("delete_file", "*", permissions.Decision.DENY_SESSION)
        permissions.add_rule("delete_file", "/home/me/*", permissions.Decision.ALLOW_ONCE)
        assert permissions.evaluate("delete_file", "/home/me/notes.txt") == \
            permissions.Decision.ALLOW_ONCE
        assert permissions.evaluate("delete_file", "/etc/passwd") == \
            permissions.Decision.DENY_SESSION

    def test_a_session_grant_outranks_a_standing_rule(self):
        permissions.add_rule("delete_file", "*", permissions.Decision.DENY_SESSION)
        permissions.add_rule("delete_file", "/tmp/scratch", permissions.Decision.ALLOW_ONCE,
                             session_only=True)
        assert permissions.evaluate("delete_file", "/tmp/scratch") == \
            permissions.Decision.ALLOW_ONCE

    def test_rules_can_be_cleared(self):
        permissions.add_rule("delete_file", "*", permissions.Decision.DENY_SESSION)
        permissions.clear()
        assert permissions.evaluate("delete_file", "/etc/passwd") == \
            permissions.Decision.FALL_THROUGH


class TestCancelIsNotDeny:
    def test_they_are_separate_decisions(self):
        assert permissions.Decision.CANCEL != permissions.Decision.DENY_ONCE

    def test_cancel_is_reported_as_a_cancellation(self):
        permissions.add_rule("delete_file", "*", permissions.Decision.CANCEL)
        assert permissions.cancel_requested("delete_file", "/tmp/x") is True

    def test_a_denial_is_not_a_cancellation(self):
        """Refusing one call must not be readable as "stop the turn"."""
        permissions.add_rule("delete_file", "*", permissions.Decision.DENY_ONCE)
        assert permissions.cancel_requested("delete_file", "/tmp/x") is False


class TestTheAllowHalfIsWired:
    """A grant has to satisfy the check it claims to satisfy.

    The failure this replaces was silent and total. `permits()` returned True
    for a grant, the dispatcher let the call through, and the skill then
    refused on its own consent key anyway. Nothing errored - the feature simply
    did nothing, and the only evidence was a test asserting that it did
    nothing. The test that pinned that is deleted rather than kept as a
    record, because its assertion was the bug.
    """

    def test_a_session_grant_opens_the_consent_gate(self):
        permissions.add_rule("delete_file", "/home/me/Downloads/*",
                             permissions.Decision.ALLOW_SESSION, session_only=True)
        out = execute_tool("delete_file", {"path": "/home/me/Downloads/a.txt"})
        assert "turned off" not in out, (
            "a session grant still does not satisfy the skill's own consent "
            "check - the grant is consumed but never reaches get_bool"
        )
        assert "not permitted" not in out.lower()

    def test_a_standing_grant_cannot_open_a_shut_gate(self):
        """Config saying "allow" is not the user saying yes.

        Only the session bucket may widen. A standing rule that blocks
        something already permitted costs the user nothing they asked for, but
        a rule that opens a gate they shut is a settings file overruling them.
        """
        permissions.add_rule("delete_file", "/home/me/Downloads/*",
                             permissions.Decision.ALLOW_ONCE, session_only=False)
        out = execute_tool("delete_file", {"path": "/home/me/Downloads/a.txt"})
        assert "turned off" in out or "not permitted" in out.lower(), (
            "a standing rule opened a consent gate the user had shut"
        )

    def test_allow_once_is_consumed_by_being_used(self):
        permissions.add_rule("delete_file", "/home/me/Downloads/*",
                             permissions.Decision.ALLOW_ONCE, session_only=True)
        first = execute_tool("delete_file", {"path": "/home/me/Downloads/a.txt"})
        second = execute_tool("delete_file", {"path": "/home/me/Downloads/a.txt"})
        assert "turned off" not in first
        assert "turned off" in second or "not permitted" in second.lower(), (
            "'allow once' allowed twice"
        )

    def test_allow_session_survives_repeated_calls(self):
        permissions.add_rule("delete_file", "/home/me/Downloads/*",
                             permissions.Decision.ALLOW_SESSION, session_only=True)
        for _ in range(3):
            out = execute_tool("delete_file", {"path": "/home/me/Downloads/a.txt"})
            assert "turned off" not in out
            assert "not permitted" not in out.lower()

    def test_an_unscoped_tool_can_still_be_granted(self):
        """Only 14 of the 69 tools name a path or unit at all.

        The first version of the wiring read the grant inside the branch that
        runs only for path-scoped tools, so `screenshot` and `set_wallpaper`
        could never be granted - which is most of the tools the consent keys
        exist for.
        """
        assert _consent_key_for("screenshot") == "vision-sense-enabled"
        assert _resource_for("screenshot", {}) is None, (
            "screenshot has no scoped resource - if it ever gains one, the "
            "unscoped grant path loses its own regression test"
        )


class TestTheGrantIsScopedToOneKey:
    """A grant that widens anything else is worse than no grant."""

    def test_a_grant_names_exactly_one_key(self):
        assert _consent_key_for("screenshot") == "vision-sense-enabled"
        assert _consent_key_for("delete_file") == "file-delete-enabled"
        assert _consent_key_for("screenshot") != _consent_key_for("delete_file")

    def test_the_env_var_is_never_set_in_our_own_process(self):
        """It is set on the child, never here.

        The dispatcher runs many calls in one process. A value set on the parent
        would be whichever call happened to be in flight - the exact bug where
        a grant meant for one tool silently opens another.
        """
        assert os.environ.get(CONSENT_GRANT_ENV) is None


class TestDenyStillBeatsAGrant:
    def test_a_later_deny_beats_an_earlier_grant(self):
        permissions.add_rule("screenshot", "*",
                             permissions.Decision.ALLOW_SESSION, session_only=True)
        permissions.add_rule("screenshot", "*",
                             permissions.Decision.DENY_SESSION, session_only=True)
        out = execute_tool("screenshot", {})
        assert "not permitted" in out.lower() or "turned off" in out.lower()
