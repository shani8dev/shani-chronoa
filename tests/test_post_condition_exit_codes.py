"""The argv post-condition's exit-code contract, and the reason it had none.

oh-my-openagent's `comment-checker-core` runs an external comment checker on
every write and edit. Its `runner.ts` reserves **exit 2** for "the check ran
and has findings", and returns *everything else* as a `failure` carrying the
exit code and the checker's stderr - documented as being there "so callers that
ignore it keep their behavior; a caller can use it to stop re-running a broken
checker" (#8850). The distinction is the entire content of that module: a
checker that could not start, and a checker that ran and found something, are
not the same event.

**Chronoa's argv post-condition had no contract at all**, and so treated every
non-zero exit as a finding. Measured through `verification.verify()` before this
change, not by reading it:

| the checker prints | exits | was reported as |
|---|---|---|
| "I could not determine whether it happened" | 3 | **FAILED**, quoting that sentence as the evidence |
| "wrapper: checker not installed" | 127 | **FAILED**, quoting it |
| - | 1 | **FAILED** |
| (binary absent entirely) | - | UNVERIFIED, correctly |

The last row is the tell: the distinction **existed at spawn** and was gone
immediately after it, one branch apart. A person reading
*"VERIFICATION FAILED: I could not determine whether it happened"* is being told
the action failed by a check that said it could not tell.

**Nothing in this repository declares the argv form** - 60 post-conditions,
every one a callable - so this defines a contract for a documented extension
point rather than changing shipped behaviour. `skills/__init__.py`'s docstring
is where a drop-in author reads it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "usr" / "lib"))

from shani_chronoa import verification  # noqa: E402
from shani_chronoa.verification import Result, Verdict  # noqa: E402


def _checker(directory: Path, name: str, body: str) -> str:
    """An executable that exits the way `body` says."""
    path = directory / name
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def _module_declaring(directory: Path, name: str, argv):
    """A throwaway module whose `POST_CONDITION` is `argv`."""
    module = type(sys)(name)
    module.POST_CONDITION = list(argv)
    sys.modules[name] = module
    return name


class TestTheCheckerCannotSpeakForTheAction:
    def test_a_checker_saying_it_could_not_tell_is_unverified(self, tmp_path):
        """**The case the change exists for, and it is the one that read worst.**

        A checker that reports its own uncertainty was having that uncertainty
        reported to the person as a failure, with its own sentence quoted as the
        evidence against the action.
        """
        checker = _checker(tmp_path, "cannot_tell",
                           "echo 'I could not determine whether it happened' >&2\n"
                           "exit 3\n")
        module = _module_declaring(tmp_path, "pc_cannot_tell", [checker])
        result = verification.verify(module)
        assert result.verdict is Verdict.UNVERIFIED, (
            f"a checker that could not answer was read as a verdict: "
            f"{result.suffix!r}")
        assert "exit 3" in result.suffix, (
            "the exit code is not named, so the person cannot tell a broken "
            f"checker from a missing one: {result.suffix!r}")
        assert "VERIFICATION FAILED" not in result.suffix

    def test_a_wrapper_whose_dependency_is_missing_is_unverified(self, tmp_path):
        """`127` is what a shell reports for a command it could not find, and it
        is the common shape: a drop-in's wrapper around a program this image does
        not have. The message says so explicitly."""
        checker = _checker(tmp_path, "wrapper",
                           "echo 'checker: command not found' >&2\nexit 127\n")
        module = _module_declaring(tmp_path, "pc_127", [checker])
        result = verification.verify(module)
        assert result.verdict is Verdict.UNVERIFIED, result.suffix
        assert "exit 127" in result.suffix
        assert "command not found" in result.suffix, (
            f"the checker's own words are dropped, which is the half that says "
            f"why: {result.suffix!r}")

    def test_exit_one_is_not_a_finding(self, tmp_path):
        """**The generic case, and the one a drop-in author will hit first.**

        `['false']` was the obvious fixture and the obvious mistake: 1 is what
        every program returns when it is unhappy, so it cannot mean "the effect
        is absent" without also swallowing every way of saying "I could not
        check".
        """
        module = _module_declaring(tmp_path, "pc_one", ["/bin/false"])
        result = verification.verify(module)
        assert result.verdict is Verdict.UNVERIFIED, (
            f"exit 1 is still being read as a finding: {result.suffix!r}")

    def test_a_binary_that_is_absent_still_reports_the_same_way(self, tmp_path):
        """The spawn-time case was already correct and must not have been broken
        by the change - two different reasons reaching a person as one word is
        the failure, so both have to be UNVERIFIED and both have to name
        themselves."""
        module = _module_declaring(tmp_path, "pc_absent",
                                   [str(tmp_path / "not-installed")])
        assert verification.verify(module).verdict is Verdict.UNVERIFIED
        checker = _checker(tmp_path, "unhappy", "exit 3\n")
        other = _module_declaring(tmp_path, "pc_unhappy", [checker])
        assert verification.verify(other).verdict is Verdict.UNVERIFIED


class TestTheContractIsTheOneItClaims:
    def test_exit_zero_verifies(self, tmp_path):
        module = _module_declaring(tmp_path, "pc_zero", ["/bin/true"])
        result = verification.verify(module)
        assert result.verdict is Verdict.VERIFIED
        assert result.suffix == "", "a verified action carries no suffix"

    def test_exit_two_is_the_declared_findings_code(self, tmp_path):
        checker = _checker(tmp_path, "finds_something",
                           "echo 'the file is not there: /tmp/a.txt' >&2\n"
                           "exit 2\n")
        module = _module_declaring(tmp_path, "pc_two", [checker])
        result = verification.verify(module)
        assert result.verdict is Verdict.FAILED
        assert "the file is not there" in result.suffix, (
            "a finding must carry its evidence, or the person is told only that "
            f"something was wrong: {result.suffix!r}")

    def test_the_two_codes_are_the_two_they_are_documented_as(self):
        """A contract nothing asserts is a comment. These are the same two codes
        `comment-checker-core` reserves, and they are checked here rather than
        trusted to the docstring."""
        assert verification._EXIT_VERIFIED == 0
        assert verification._EXIT_FINDINGS == 2
        assert verification._EXIT_FINDINGS != verification._EXIT_VERIFIED

    @pytest.mark.parametrize("code", [3, 4, 70, 126, 127, 255])
    def test_no_other_code_is_a_finding(self, tmp_path, code):
        """Every other code the checker can plausibly produce means the same
        thing, and this walks them rather than trusting one."""
        checker = _checker(tmp_path, f"code_{code}",
                           f"echo 'checker gave up' >&2\nexit {code}\n")
        module = _module_declaring(tmp_path, f"pc_{code}", [checker])
        assert verification.verify(module).verdict is Verdict.UNVERIFIED


class TestTheReasonReachesThePerson:
    """Four different reasons, which used to be one sentence.

    `suffix` rendered a fixed string for UNVERIFIED regardless of `evidence`, so
    "this module declares no post-condition", "the post-condition raised" and
    "the checker is not installed" reached a person as the same fourteen words.
    This is the module whose whole purpose is that *I could not determine* is not
    *false* - and it could not say which it meant.
    """

    def test_each_reason_is_distinguishable(self):
        reasons = [
            Result(Verdict.UNVERIFIED, "no post-condition declared").suffix,
            Result(Verdict.UNVERIFIED, "post-condition raised KeyError: 'path'").suffix,
            Result(Verdict.UNVERIFIED,
                   "could not run post-condition: [Errno 2] No such file").suffix,
        ]
        assert len(set(reasons)) == len(reasons), (
            f"three reasons, one sentence: {reasons}")

    def test_the_no_evidence_sentence_is_unchanged(self):
        """**It is quoted elsewhere in this tree**, by `capabilities.py` and
        `browse.py`, both explaining why a plain answer must not carry it - so
        this is the common case and the one that had to stay."""
        assert Result(Verdict.UNVERIFIED).suffix == (
            " (unverified - this action reports success but nothing observed it)")

    def test_the_reason_is_not_read_as_a_verdict(self, tmp_path):
        """**The hazard this change introduces, closed in the same commit.**

        `verdict_from_text` recovers a verdict from a plain result string, and it
        used to look for the marker as a bare substring. Now that a checker's own
        words are quoted back, a checker printing "VERIFICATION FAILED: the file
        was there after all" would have turned its own *unverified* verdict into
        a FAILED one on the way back through.
        """
        checker = _checker(
            tmp_path, "confused",
            "echo 'VERIFICATION FAILED: the file was there after all' >&2\n"
            "exit 3\n")
        module = _module_declaring(tmp_path, "pc_confused", [checker])
        result = verification.verify(module)
        assert result.verdict is Verdict.UNVERIFIED
        text = "did the thing" + result.suffix
        assert verification.verdict_from_text(text) is Verdict.UNVERIFIED, (
            f"a checker's prose decided the verdict of the check that read it: "
            f"{text!r}")

    def test_the_same_hazard_for_the_impossible_marker(self, tmp_path):
        """**The first version of this had no witness, and the mutation survived.**

        Reverting the `FAILED_MARKER` match to a bare substring is caught by the
        test above. Reverting `IMPOSSIBLE_MARKER` the same way was **not**,
        because every IMPOSSIBLE string this module produces already contains
        the marker with its parenthesis - so both versions pass. They are not
        equivalent, though: a checker whose own output reads "route impossible:
        no gateway" would be read as IMPOSSIBLE under the bare match, which is
        the same hazard one marker over.

        So the two markers need two witnesses, not one witness and a hope.
        """
        checker = _checker(tmp_path, "no_route",
                           "echo 'route impossible: no gateway answered' >&2\n"
                           "exit 3\n")
        module = _module_declaring(tmp_path, "pc_no_route", [checker])
        result = verification.verify(module)
        assert result.verdict is Verdict.UNVERIFIED
        text = "did the thing" + result.suffix
        assert verification.verdict_from_text(text) is Verdict.UNVERIFIED, (
            "a checker's own prose decided the verdict of the check that read "
            f"it: {text!r}")

    def test_the_markers_still_parse_back(self):
        """The parser exists for the trigger engine, which dispatches through a
        plain `-> str` and loses the verdict. Tightening the match must not cost
        it that."""
        assert verification.verdict_from_text(
            "did the thing" + Result(Verdict.FAILED, "no").suffix) is Verdict.FAILED
        assert verification.verdict_from_text(
            "cannot" + Result(Verdict.IMPOSSIBLE, "no route").suffix
        ) is Verdict.IMPOSSIBLE
        assert verification.verdict_from_text("did the thing") is Verdict.UNVERIFIED


class TestACheckerIsToldWhatItIsChecking:
    def test_the_arguments_reach_the_checker_as_documented(self, tmp_path):
        """`CHRONOA_VERIFY_ARGS` was written into the environment and **read by
        nothing, documented by nothing, in this repository or any drop-in** - a
        bare external command cannot know what it was asked to check. It is now
        stated in `skills/__init__.py`'s contract, and this is the statement's
        witness.
        """
        out = tmp_path / "args.txt"
        checker = _checker(tmp_path, "showargs",
                           f'echo "$CHRONOA_VERIFY_ARGS" > {out}\nexit 0\n')
        module = _module_declaring(tmp_path, "pc_args", [checker])
        sys.modules[module].POST_CONDITION = [checker]
        arguments = {"path": "/tmp/a.txt", "n": 3}
        result = verification.verify(module, arguments)
        assert result.verdict is Verdict.VERIFIED
        assert out.read_text(encoding="utf-8").strip() == repr(arguments), (
            "the documented channel does not carry what the contract says it "
            f"carries: {out.read_text(encoding='utf-8')!r}")

    def test_the_contract_names_the_channel_where_drop_in_authors_read(self):
        """A contract in `verification.py` is not documentation for someone
        dropping a module into `~/.config/shani-chronoa/skills/`. This is the
        only place they are told, so it has to actually be there."""
        source = (Path(__file__).resolve().parent.parent / "usr" / "lib"
                  / "shani-chronoa" / "shani_chronoa" / "skills" / "__init__.py")
        text = source.read_text(encoding="utf-8")
        assert "POST_CONDITION" in text, "the drop-in contract never mentions it"
        assert "CHRONOA_VERIFY_ARGS" in text, (
            "the channel a drop-in checker is given is still undocumented")
        for phrase in ("exit 0", "exit 2"):
            assert phrase in text, (
                f"{phrase!r} is not in the contract a drop-in author reads")
        assert "Exit 1 is not a finding" in text, (
            "the trap - that 1 is what every unhappy program returns - is the "
            "one thing a drop-in author most needs said")
