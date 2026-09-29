"""Scoped permissions, and an honest account of which half is wired.

Chronoa's consent keys are global and persistent. That is a real safety property
- a grant survives a restart, so nothing quietly expires - but it is one shape of
answer, and it leaves two things unexpressible: "yes, this once", and "not that
one". A boolean cannot say "delete files in ~/Downloads but never in /etc".

So there is a second layer, in front of the booleans rather than replacing them.

**What is wired:** scoped *deny* rules, enforced centrally in the dispatch path.
A session rule saying "never touch /etc" holds even if a skill forgets to look,
which is the whole reason to enforce it centrally.

**What is not wired:** the allow decisions. `ALLOW_ONCE` and `ALLOW_SESSION` are
evaluated and returned, but a granted call still reaches the skill, whose own
`_consent()` refuses because the gsettings key is off. Making "allow once" real
means each of the 26 gated skills must consult this module - so the inert half
is asserted here as inert, rather than left for someone to discover.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import permissions  # noqa: E402
from shani_chronoa.tools import _resource_for, execute_tool  # noqa: E402


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


class TestTheAllowHalfIsInertAndSaysSo:
    """Recorded as a test so it cannot be quietly believed to work."""

    def test_a_grant_does_not_bypass_the_consent_key(self):
        permissions.add_rule("delete_file", "/home/me/Downloads/*",
                             permissions.Decision.ALLOW_SESSION)
        out = execute_tool("delete_file", {"path": "/home/me/Downloads/a.txt"})
        assert "Permission denied" not in out
        assert "turned off" in out or "not permitted" in out.lower(), (
            "a session grant now appears to satisfy a skill's consent check. If "
            "that is intended, update this test and the module docstring - the "
            "gated skills must consult permissions for it to be true"
        )
