"""`polkitpolicy`: the one question in this package whose answer is a permission.

Everything else answers either *what is the state* (a sense) or *do this* (a
skill, behind a consent key). This answers **what may the person at this desk do
without being asked for a password** - which decides what the other two are worth,
because a switch that always prompts is friction and a switch nobody was ever
offered is a dead end.

Found by comparing the capability surface against the matrix's `os_surfaces`,
which carries **491 polkit actions** on a real GNOME image with nothing in
Chronoa reporting any of it. The four modules that mention polkit *use* it
(`pkexec` this, a portal there) and `privilege` answers a different question:
it reports processes *holding* a dangerous Linux capability, which is who has
power, not what the policy says about the person asking.

**Tests here are over policy files written into a temp directory**, because the
real ones differ per distribution and a test asserting a count would be a test
of the image, not of the code. The real machine is exercised too, and the two
are kept apart on purpose: `test_it_reads_this_machine_honestly` is a smoke test
that the real path works, not a claim about how many actions any given install
has.

**The three states that are easy to conflate, each asserted separately:**

- **`yes` is the only value meaning no password.** `auth_self` is *still a
  prompt* - your own password - and collapsing it into "no password needed" is how
  a machine ends up looking alarmist or careless about the same policy.
- **`no` means never allowed to anyone**, not "unknown". It was falling into the
  classifier's `other` bucket and being reported as a rule this could not
  classify: five permanently-denied actions described as five unreadable ones.
  That is the confident-unknown answer in its purest form.
- **An empty policy directory is not "you can do nothing"** but a machine where
  the question does not apply, so it is reported as UNKNOWN - the same
  `None`-versus-`[]` distinction every other sense in this package makes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.senses import polkit_policy as P  # noqa: E402

_POLICY = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE policyconfig PUBLIC
 "-//freedesktop//DTD PolicyKit Policy Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/PolicyKit/1/policyconfig.dtd">
<policyconfig>
  <vendor>Test</vendor>
  <action id="org.test.Free">
    <description>Do the free thing</description>
    <message>Authentication is required</message>
    <defaults>
      <allow_any>yes</allow_any>
      <allow_inactive>no</allow_inactive>
      <allow_active>yes</allow_active>
    </defaults>
  </action>
  <action id="org.test.OwnPassword">
    <description>Do your-own-password thing</description>
    <defaults>
      <allow_any>auth_admin</allow_any>
      <allow_inactive>auth_admin</allow_inactive>
      <allow_active>auth_self</allow_active>
    </defaults>
  </action>
  <action id="org.test.NeedsAdmin">
    <description>Do the administrator thing</description>
    <defaults>
      <allow_any>auth_admin</allow_any>
      <allow_inactive>auth_admin</allow_inactive>
      <allow_active>auth_admin_keep</allow_active>
    </defaults>
  </action>
  <action id="org.test.Never">
    <description>Never allowed at all</description>
    <defaults>
      <allow_any>no</allow_any>
      <allow_inactive>no</allow_inactive>
      <allow_active>no</allow_active>
    </defaults>
  </action>
  <action id="org.test.Something">
    <description>Do the undocumented thing</description>
    <defaults>
      <allow_any>auth_admin</allow_any>
      <allow_inactive>auth_admin</allow_inactive>
      <allow_active>some_future_value</allow_active>
    </defaults>
  </action>
</policyconfig>
"""


@pytest.fixture
def policies(tmp_path, monkeypatch):
    """Point the sense at a temp directory holding the policy above.

    `monkeypatch`, not a module global, because this patch is process-wide and a
    failure that left it behind would corrupt every later test in the run - the
    `NameError`-inside-`finally` lesson in the shape that bites.
    """
    directory = tmp_path / "actions"
    directory.mkdir()
    (directory / "org.test.policy").write_text(_POLICY)
    monkeypatch.setattr(P, "_POLICY_DIRS", (directory,))
    return directory


class TestTheThreeStatesAreKeptApart:
    def test_only_yes_means_no_password(self, policies):
        actions = {a["id"]: a for a in P.read_actions()}
        assert actions["org.test.Free"]["kind"] == "yes"
        # **auth_self is still a prompt.** It is the single most confusable value
        # in the vocabulary and the one that makes a machine look careless.
        assert actions["org.test.OwnPassword"]["kind"] == "self"
        assert actions["org.test.NeedsAdmin"]["kind"] == "admin"

    def test_no_means_never_not_unknown(self, policies):
        """`no` is an answer. It was being reported as unreadable."""
        actions = {a["id"]: a for a in P.read_actions()}
        assert actions["org.test.Never"]["kind"] == "never"

    def test_an_unrecognised_rule_is_admitted_rather_than_guessed(self, policies):
        actions = {a["id"]: a for a in P.read_actions()}
        assert actions["org.test.Something"]["kind"] == "other", (
            "an unknown polkit value must fall through to 'other' and be "
            "counted as unclassifiable, not folded into a category it might "
            "belong to - a future polkit release adding a value should widen "
            "this sense, not silently mislabel it")

    def test_the_summary_separates_all_five(self, policies):
        said = P._run({})
        assert "1 of 5 installed polkit actions need no authentication" in said, said
        assert "1 need your own password" in said, said
        assert "1 need an administrator first" in said, said
        assert "1 are never allowed to anyone" in said, said
        assert "1 use a rule this could not classify" in said, said

    def test_a_free_action_is_named_so_the_answer_is_actionable(self, policies):
        """A count is not an answer; the person asked what they can just do."""
        assert "Do the free thing" in P._run({})


class TestDetailIsHonoured:
    def test_detail_lists_every_action_with_its_rule(self, policies):
        said = P._run({"detail": True})
        # **Descriptions, not ids.** An id is the thing a machine matches on and
        # is the thing a person cannot use; the description is what "what can I
        # do" is answered with. The id is still reachable - `read_actions()`
        # returns both - so nothing is lost.
        for description in ("Do the free thing", "Do your-own-password thing",
                            "Never allowed at all"):
            assert description in said, f"{description!r} missing: {said}"
        # The rule each one carries is the answerable half.
        assert "auth_self" in said and "auth_admin_keep" in said

    def test_without_detail_the_list_is_not_included(self, policies):
        """The declared argument has to be read.

        A schema parameter no code consults is the "an option whose label
        promises something the program does not do" failure this repository
        records for the tray icon. Asserted on the heading the detailed answer
        adds, which is present only when `detail` was asked for.
        """
        assert "Every installed action" not in P._run({})
        assert "Every installed action" in P._run({"detail": True})


class TestUnreadableIsNotEmpty:
    def test_no_policy_directory_is_unknown_not_nothing(self, tmp_path, monkeypatch):
        """A machine with no policies is a different claim from one that is shut."""
        monkeypatch.setattr(P, "_POLICY_DIRS", (tmp_path / "absent",))
        assert P.read_actions() is None
        said = P._run({})
        assert said.startswith("UNKNOWN"), said
        assert "not a machine where you can do nothing" in said, (
            "the refusal has to say which of the two claims it is making")

    def test_one_unreadable_file_does_not_discard_the_rest(self, policies):
        """58 files, one malformed - the other 57 are still worth reporting."""
        (policies / "broken.policy").write_text("<policyconfig><action")
        actions = P.read_actions()
        assert actions is not None and len(actions) == 5, (
            f"one unparseable policy file took the whole answer with it: {actions}")

    def test_an_empty_policy_set_says_the_question_does_not_apply(self, tmp_path,
                                                                 monkeypatch):
        directory = tmp_path / "actions"
        directory.mkdir()
        monkeypatch.setattr(P, "_POLICY_DIRS", (directory,))
        assert P.read_actions() == [], "an empty directory must be [], not None"
        assert "does not apply" in P._run({})


class TestItReadsTheRealMachine:
    """A smoke test of the real path, deliberately not a claim about counts."""

    def test_it_reads_this_machine_honestly(self, monkeypatch):
        from shani_chronoa.senses import discover_senses

        registry = discover_senses()
        assert "polkitpolicy" in registry, (
            "the sense is not registered, so nothing about it reaches the app")
        registry.pop("polkitpolicy")  # back to the real search paths
        actions = P.read_actions()
        if actions is None:
            pytest.skip("no polkit policies on this machine (a container)")
        assert actions, "policies were found but no actions parsed out of them"
        for action in actions:
            assert action["kind"] in ("yes", "self", "admin", "never", "other"), action
        said = P._run({})
        assert said and not said.startswith("UNKNOWN"), said
