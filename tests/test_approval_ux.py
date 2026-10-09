"""A consent prompt nobody can read is a consent prompt nobody gave.

Three stages, each existing because the one before it leaves something specific
unsaid:

1. `permission` - what is about to happen, specifically enough to judge. A user
   cannot consent to something they cannot see.
2. `always` - the *exact* patterns the answer would persist, and that they die
   with the session. A user who cannot tell what they just allowed either
   over-trusts or never uses the answer at all.
3. `reject` - an optional free-text reason, so the model can adapt instead of
   guessing.

Plus the rule that makes stage 3 safe: **dismissal is not consent**. Escape, a
timeout, a missing presenter and a value nobody recognises all take the refusal
path. opencode maps Escape to reject (`permission.tsx:406`) for the same reason
codex states its contract at `approval_overlay.rs:8` - dismissal must never
silently become "continue without information".

Every test here runs against the real dispatch path (`execute_tool_outcome`), so
a change that composes a beautiful prompt and never asks it fails.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import ask_bridge, permissions  # noqa: E402
from shani_chronoa.tools import execute_tool, execute_tool_outcome  # noqa: E402


def _presenter(answer: str, seen: list | None = None):
    def show(question, options):
        if seen is not None:
            seen.append((question, list(options)))
        done, resolve = ask_bridge.make_event()
        resolve(answer)
        return done
    return show


@pytest.fixture(autouse=True)
def clean_state():
    permissions.clear()
    ask_bridge.set_presenter(None)
    yield
    permissions.clear()
    ask_bridge.set_presenter(None)


class TestStageOneShowsWhatIsAboutToHappen:
    def test_a_scoped_action_names_the_exact_target(self):
        ask = permissions.approval_request("delete_file", "/home/me/notes.txt",
                                           "file-delete-enabled", "delete")
        assert "/home/me/notes.txt" in ask.question, (
            "a destructive action was put without its path; a user cannot "
            "consent to a delete they cannot see"
        )

    def test_an_unscoped_action_says_so_rather_than_omitting_it(self):
        ask = permissions.approval_request("kill_process", None,
                                           "process-kill-enabled",
                                           "stop a process")
        assert "no specific target" in ask.question, (
            "the sentence that omits the object reads as 'nothing in "
            "particular' rather than 'whatever it reaches'"
        )

    def test_the_consent_key_it_would_need_is_named(self):
        ask = permissions.approval_request("delete_file", "/tmp/x",
                                           "file-delete-enabled")
        assert "'file-delete-enabled'" in ask.question

    def test_the_three_stages_are_addressable_and_in_order(self):
        ask = permissions.approval_request("delete_file", "/home/me/notes.txt",
                                           "file-delete-enabled", "delete")
        names = [name for name, _body in ask.stages]
        assert names == [permissions.STAGE_PERMISSION, permissions.STAGE_ALWAYS,
                         permissions.STAGE_REJECT]
        positions = [ask.question.index(body) for _name, body in ask.stages]
        assert positions == sorted(positions), (
            "the composed prompt does not present the stages in order")

    def test_the_composed_prompt_contains_every_stage_and_nothing_else(self):
        ask = permissions.approval_request("kill_process", None,
                                           "process-kill-enabled")
        assert ask.question == "\n\n".join(b for _n, b in ask.stages)


class TestStageTwoEnumeratesTheScope:
    def test_a_narrow_grant_says_it_is_narrow(self):
        ask = permissions.approval_request("delete_file", "/home/me/notes.txt",
                                           "file-delete-enabled", "delete")
        line = " ".join(ask.always_lines)
        assert "only" in line and "/home/me/notes.txt" in line, (
            "'allow for this session' must say what it covers, not imply it"
        )

    def test_an_unscoped_grant_says_it_covers_everything(self):
        """The case the prompt exists for.

        Nine of the twelve gated tools name no target, so their grant is written
        against `*`. A user told only "allow for this session" would believe they
        had allowed one process.
        """
        ask = permissions.approval_request("kill_process", None,
                                           "process-kill-enabled")
        line = " ".join(ask.always_lines)
        assert "EVERY" in line and "*" in line, (
            f"a session grant covering every kill_process was described as {line!r}"
        )

    def test_the_lifetime_is_stated_not_implied(self):
        ask = permissions.approval_request("delete_file", "/tmp/x",
                                           "file-delete-enabled")
        text = ask.lifetime_line.lower()
        assert "until chronoa quits" in text and "disk" in text, (
            "a user must be told the grant dies with the session"
        )

    def test_the_pattern_the_prompt_names_is_the_pattern_that_is_written(self):
        """The claim and the record must not be able to drift apart.

        A prompt that says `/tmp/x` while `add_rule()` writes `*` is the exact
        defect a user cannot detect and cannot undo.
        """
        seen: list = []
        ask_bridge.set_presenter(_presenter(
            permissions.ALLOW_SESSION_CHOICE, seen))
        permissions.decide("delete_file", "/tmp/opencode/scope-probe.txt",
                           "file-delete-enabled", "delete")
        action, pattern, _decision = [
            rule for rule in permissions.rules() if rule[0] == "delete_file"][0]
        claimed = permissions.always_patterns("delete_file",
                                             "/tmp/opencode/scope-probe.txt")
        assert (action, pattern) == claimed[0], (
            f"the prompt enumerated {claimed} but the rule on record is "
            f"{(action, pattern)}"
        )
        assert "/tmp/opencode/scope-probe.txt" in seen[0][0]

    def test_an_unscoped_grant_really_does_cover_every_target(self):
        """The "EVERY" claim must be true, or the prompt is lying."""
        permissions.add_rule("kill_process", "*",
                             permissions.Decision.ALLOW_SESSION, session_only=True)
        for pid in ("1", "4242", "99999"):
            assert permissions.evaluate("kill_process", pid) == \
                permissions.Decision.ALLOW_SESSION

    def test_a_narrow_grant_really_does_not_cover_a_sibling(self, tmp_path):
        """Proven through the dispatch path, not through `permits()`.

        `permits()` returning True means "this layer has no objection", which is
        what a fall-through reports - so it cannot distinguish "granted" from
        "no rule matched". `execute_tool` can.

        `git_inspect` rather than a file-writing tool: every tool that
        writes a file (`edit_file`, `delete_file`, `office_document`,
        `undo_last_change`) carries a destructive consent key, and a
        session grant on a bypass-immune tool is ignored by design
        (T2.6), so a narrow-grant test written on one proves the
        immunity instead of the scope, and the scope has no witness
        at all. `git_inspect` is gated and read-only, so its session
        grant is honoured and the scope of that grant is observable:
        the granted tree is read, the sibling is refused by its own
        gate.
        """
        # Under $HOME, not `tmp_path`: `files` refuses a path outside the
        # user's home, and a refusal for *that* reason proves nothing about
        # which grant matched.
        home = Path(os.environ["HOME"])
        granted = home / "granted-tree"
        granted.mkdir()
        sibling = home / "other-tree"
        sibling.mkdir()
        permissions.add_rule("git_inspect", str(granted),
                             permissions.Decision.ALLOW_SESSION, session_only=True)
        assert permissions.evaluate("git_inspect", str(granted)) == \
            permissions.Decision.ALLOW_SESSION
        assert permissions.evaluate("git_inspect", str(sibling)) == \
            permissions.Decision.FALL_THROUGH, (
            "a grant naming one path matched a different path"
        )
        # A reading, not a refusal: a directory that is not a git
        # repository is git's own answer, and the only way to tell it
        # apart from the gate's refusal is that the refusal names the
        # switch. The granted path must reach the skill and come back
        # with that answer.
        allowed = execute_tool_outcome(
            "git_inspect", {"path": str(granted)})
        assert allowed.ran, f"the granted path did not reach the skill: {allowed.text}"
        assert "turned off" not in allowed.text and "Refusing" not in allowed.text, (
            f"the granted path was refused by its own gate: {allowed.text!r}"
        )
        refused = execute_tool("git_inspect", {"path": str(sibling)})
        assert "turned off" in refused or "not permitted" in refused.lower(), (
            f"the sibling read was not refused ({refused!r}); the grant "
            f"leaked past its path"
        )

    def test_a_narrow_grant_on_a_bypass_immune_tool_is_refused_not_honoured(self):
        """The other half of the same property, and the newer one.

        `delete_file` is bypass-immune: a standing grant naming one path is
        written, reads back as `ALLOW_SESSION` through `evaluate()` - so the
        layer above it looks satisfied - and is then refused at dispatch. An
        assertion on `evaluate()` alone would call this granted.
        """
        target = Path(os.environ["HOME"]) / "notes.txt"
        target.write_text("x")
        permissions.add_rule("delete_file", str(target),
                             permissions.Decision.ALLOW_SESSION, session_only=True)
        assert permissions.is_bypass_immune("delete_file")
        refused = execute_tool("delete_file", {"path": str(target)})
        assert "turned off" in refused or "not permitted" in refused.lower(), (
            f"a session grant was honoured for a bypass-immune tool ({refused!r})"
        )
        assert target.exists(), "the file was deleted by a grant that cannot apply"

    def test_a_session_grant_is_never_written_to_disk(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        before = set(tmp_path.rglob("*"))
        permissions.add_rule("delete_file", "/tmp/x",
                             permissions.Decision.ALLOW_SESSION, session_only=True)
        permissions.add_rule("kill_process", "*", permissions.Decision.CANCEL,
                             session_only=True)
        assert set(tmp_path.rglob("*")) == before, (
            "a session decision left files behind, so 'nothing is written to "
            "disk' in the prompt would be false"
        )
        permissions.clear(session_only=True)
        assert permissions.evaluate("delete_file", "/tmp/x") == \
            permissions.Decision.FALL_THROUGH, (
            "clearing the session bucket did not take the grant with it"
        )


class TestStageThreeCarriesAReason:
    def test_a_reason_round_trips(self):
        reply = permissions.parse_reply(
            permissions.encode_rejection("use the trash instead"))
        assert reply.reply == permissions.Decision.DENY_SESSION
        assert reply.message == "use the trash instead"

    def test_the_reason_is_optional(self):
        assert permissions.encode_rejection() == permissions.DENY_CHOICE
        assert permissions.parse_reply(permissions.DENY_CHOICE).message == ""

    def test_the_prompt_invites_one(self):
        ask = permissions.approval_request("delete_file", "/tmp/x",
                                           "file-delete-enabled")
        assert "reason" in ask.reject_line.lower()

    def test_the_reason_reaches_the_model_on_the_refusal(self, tmp_path):
        """The whole point of stage 3: declining is not a dead end.

        `tools.py` builds the first refusal itself, but the *next* attempt is
        answered by `permits()`, and that text is what the model reads.
        """
        target = tmp_path / "junk.txt"
        target.write_text("x")
        ask_bridge.set_presenter(_presenter(
            permissions.encode_rejection("never in /home, use the trash")))
        execute_tool_outcome("delete_file", {"path": str(target)})
        retry = execute_tool_outcome("delete_file", {"path": str(target)}).text
        assert "use the trash" in retry, (
            "the reason the user gave did not reach the model, so it has "
            "nothing to adapt to"
        )
        assert target.exists(), "the file was deleted despite the refusal"

    def test_a_reason_does_not_leak_into_another_action(self):
        ask_bridge.set_presenter(_presenter(
            permissions.encode_rejection("not on this machine")))
        permissions.decide("delete_file", "/tmp/x", "file-delete-enabled")
        assert permissions.rejection_reason("screenshot", "*") == ""

    def test_clearing_the_rules_clears_the_reasons(self):
        ask_bridge.set_presenter(_presenter(
            permissions.encode_rejection("because")))
        permissions.decide("delete_file", "/tmp/x", "file-delete-enabled")
        assert permissions.rejection_reason("delete_file", "/tmp/x") == "because"
        permissions.clear()
        assert permissions.rejection_reason("delete_file", "/tmp/x") == ""

    def test_a_reason_on_an_allow_changes_nothing(self):
        permissions.parse_reply(
            permissions.ALLOW_ONCE_CHOICE + permissions.REASON_SEPARATOR + "ok")
        assert permissions.reasons() == {}


class TestDismissalIsNeverConsent:
    """The stage that has no button."""

    @pytest.mark.parametrize("answer", [
        "",                     # dismissed, timed out, or no presenter at all
        "   ",
        "close",
        "cancelled_by_window",
        None,
        42,
        object(),
    ])
    def test_anything_unrecognised_is_a_refusal(self, answer):
        reply = permissions.parse_reply(answer)
        assert reply.reply == permissions.Decision.DENY_SESSION, (
            f"{answer!r} was read as {reply.reply!r}"
        )
        assert reply.is_refusal
        assert reply.reply not in (permissions.Decision.ALLOW_ONCE,
                                   permissions.Decision.ALLOW_SESSION)

    def test_a_dismissed_prompt_denies_rather_than_allowing(self):
        ask_bridge.set_presenter(_presenter(""))
        out = execute_tool_outcome("screenshot", {}).text
        assert "did not allow" in out
        assert "Screenshot saved" not in out

    def test_a_dismissal_is_recorded_so_it_is_not_re_asked(self):
        ask_bridge.set_presenter(_presenter(""))
        execute_tool_outcome("screenshot", {})
        assert permissions.evaluate("screenshot", "*") == \
            permissions.Decision.DENY_SESSION

    def test_the_prompt_states_that_dismissal_means_no(self):
        ask = permissions.approval_request("delete_file", "/tmp/x",
                                           "file-delete-enabled")
        assert "counts as no" in ask.escape_line and "never as" in ask.escape_line

    def test_a_timeout_is_a_refusal(self, monkeypatch):
        """A held prompt nobody answers must not resolve to an allow."""
        def hanging(question, options):
            return ask_bridge.make_event()[0]
        ask_bridge.set_presenter(hanging)
        # `decide()` passes `timeout=` explicitly, so the constant is what has
        # to be shortened - patching `ask.__defaults__` changes nothing here.
        monkeypatch.setattr(permissions, "DECISION_TIMEOUT_SECONDS", 0.05)
        assert permissions.decide("screenshot", None, "vision-sense-enabled") is None


class TestDeclineIsNotCancel:
    """codex's `Decline` versus `Cancel`/`Abort`, which are different acts."""

    def test_they_are_separate_decisions(self):
        assert permissions.Decision.CANCEL != permissions.Decision.DENY_SESSION
        assert permissions.CANCEL_CHOICE != permissions.DENY_CHOICE

    def test_a_decline_does_not_ask_to_stop_the_turn(self):
        ask_bridge.set_presenter(_presenter(permissions.DENY_CHOICE))
        permissions.decide("delete_file", "/tmp/x", "file-delete-enabled")
        assert permissions.cancel_requested("delete_file", "/tmp/x") is False, (
            "refusing one call was recorded as ending the turn, which is the "
            "conflation that makes a model retry the same thing differently"
        )

    def test_cancel_is_offered_only_when_the_caller_offers_it(self):
        seen: list = []
        ask_bridge.set_presenter(_presenter(permissions.DENY_CHOICE, seen))
        permissions.decide("delete_file", "/tmp/x", "file-delete-enabled")
        assert permissions.CANCEL_CHOICE not in seen[0][1]

        permissions.clear()
        seen.clear()
        ask_bridge.set_presenter(_presenter(permissions.DENY_CHOICE, seen))
        permissions.decide("delete_file", "/tmp/x", "file-delete-enabled",
                           offer_cancel=True)
        assert permissions.CANCEL_CHOICE in seen[0][1]

    def test_answering_cancel_writes_the_decision_cancel_requested_reads(self):
        ask_bridge.set_presenter(_presenter(permissions.CANCEL_CHOICE))
        granted = permissions.decide("delete_file", "/tmp/x",
                                     "file-delete-enabled", offer_cancel=True)
        assert granted is None, "cancel returned a grant"
        assert permissions.cancel_requested("delete_file", "/tmp/x") is True

    def test_a_cancelled_refusal_tells_the_model_not_to_try_a_variant(self):
        permissions.add_rule("kill_process", "*", permissions.Decision.CANCEL)
        refusal = permissions.permits("kill_process", "4242")[1]
        assert "Do not try a variant" in refusal, (
            "a cancel reads the same as a denial, so the model tries the same "
            "thing again in a different shape"
        )
        assert "cancelled" in refusal


class TestTheOrderCarriesMeaning:
    def test_the_refusal_is_last_and_nothing_is_the_default(self):
        # `git_inspect` rather than a destructive tool: every
        # destructive tool is bypass-immune now, so its prompt
        # offers two options, not three - and the companion test
        # below asserts that. The order (refusal last, nothing
        # the default) is the property here, and a gated
        # read-only tool carries it without the immunity.
        ask = permissions.approval_request("git_inspect", "/tmp/x",
                                           "git-sense-enabled")
        assert ask.options == [permissions.ALLOW_ONCE_CHOICE,
                               permissions.ALLOW_SESSION_CHOICE,
                               permissions.DENY_CHOICE]

    def test_a_bypass_immune_tool_offers_no_session_option_it_cannot_keep(self):
        """Two options, not three - and refusal still last.

        Offering "Allow for this session" on a tool whose session grant is
        ignored would be a promise the mechanism cannot make; a presenter that
        picked it would record a rule that does nothing.
        """
        ask = permissions.approval_request("delete_file", "/tmp/x",
                                           "file-delete-enabled")
        assert permissions.is_bypass_immune("delete_file")
        assert ask.options == [permissions.ALLOW_ONCE_CHOICE,
                               permissions.DENY_CHOICE]
        assert ask.options[-1] == permissions.DENY_CHOICE, (
            "refusal must still be last, or it stops being the default"
        )

    def test_allowing_once_still_means_once(self):
        ask_bridge.set_presenter(_presenter(permissions.ALLOW_ONCE_CHOICE))
        permissions.decide("delete_file", "/tmp/x", "file-delete-enabled")
        permissions.clear(session_only=True)
        seen: list = []
        ask_bridge.set_presenter(_presenter(permissions.ALLOW_ONCE_CHOICE, seen))
        permissions.decide("delete_file", "/tmp/x", "file-delete-enabled")
        assert seen, "a consumed one-shot grant let the next call through"
