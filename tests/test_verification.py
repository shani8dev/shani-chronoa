"""Post-condition verification of actuator effects.

Grounded in two measured results rather than taste:

- LITMUS (arXiv:2605.10779) names *Execution Hallucination* - an agent
  reports a state that does not hold - and measures frontier models still
  executing 40.64% of high-risk operations.
- Reflexion (arXiv:2303.11366) shows self-reflection WITHOUT an external
  signal scores 0.52 on HumanEval-Rust against a 0.60 no-reflection
  baseline: worse than not checking at all.

Both say the same thing, so the property pinned here is that an action's
success is established by observing state, not by the actor's account of it.
"""

import importlib
import os
import subprocess
import sys

import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")
from shani_chronoa import verification  # noqa: E402
from shani_chronoa.verification import Result, Verdict  # noqa: E402

_import_module = importlib.import_module


def _module(tmp_path, name, body, monkeypatch):
    """Install a throwaway skill module and return its dotted name."""
    path = tmp_path / f"{name}.py"
    path.write_text(body)
    monkeypatch.syspath_prepend(str(tmp_path))
    # execute_tool dispatches through a child `python3 -c`, so the directory
    # has to be on PYTHONPATH for the child too, not just this interpreter.
    existing = os.environ.get("PYTHONPATH", "")
    monkeypatch.setenv(
        "PYTHONPATH", f"{tmp_path}{os.pathsep}{existing}" if existing else str(tmp_path)
    )
    return name


class TestVerdicts:
    def test_no_post_condition_is_unverified_not_success(self, tmp_path, monkeypatch):
        """The core of the fix: silence is not confirmation."""
        mod = _module(tmp_path, "npc", "def run(a):\n    return 'done'\n", monkeypatch)
        result = verification.verify(mod)
        assert result.verdict is Verdict.UNVERIFIED
        assert "unverified" in result.suffix

    def test_passing_command_verifies(self, tmp_path, monkeypatch):
        mod = _module(
            tmp_path, "okcmd", "POST_CONDITION = ['true']\ndef run(a):\n    return 'x'\n", monkeypatch
        )
        assert verification.verify(mod).verdict is Verdict.VERIFIED

    def test_failing_command_fails(self, tmp_path, monkeypatch):
        mod = _module(
            tmp_path, "badcmd", "POST_CONDITION = ['false']\ndef run(a):\n    return 'x'\n", monkeypatch
        )
        result = verification.verify(mod)
        assert result.verdict is Verdict.FAILED
        assert "VERIFICATION FAILED" in result.suffix

    def test_a_raising_post_condition_does_not_crash_the_action_path(
        self, tmp_path, monkeypatch
    ):
        mod = _module(
            tmp_path,
            "boom",
            "def _c(a):\n    raise RuntimeError('probe exploded')\n"
            "POST_CONDITION = _c\ndef run(a):\n    return 'x'\n",
            monkeypatch,
        )
        result = verification.verify(mod)
        assert result.verdict is Verdict.UNVERIFIED
        assert "RuntimeError" in result.evidence

    def test_a_missing_module_is_unverified(self):
        assert verification.verify("no.such.module.anywhere").verdict is Verdict.UNVERIFIED


class TestCallablePostConditions:
    def test_callable_receives_the_skill_arguments(self, tmp_path, monkeypatch):
        mod = _module(
            tmp_path,
            "cb",
            "def _c(a):\n    return a.get('text') == 'hello'\n"
            "POST_CONDITION = _c\ndef run(a):\n    return 'x'\n",
            monkeypatch,
        )
        assert verification.verify(mod, {"text": "hello"}).verdict is Verdict.VERIFIED
        assert verification.verify(mod, {"text": "other"}).verdict is Verdict.FAILED

    def test_callable_may_return_evidence(self, tmp_path, monkeypatch):
        mod = _module(
            tmp_path,
            "cbe",
            "def _c(a):\n    return False, 'clipboard holds the old value'\n"
            "POST_CONDITION = _c\ndef run(a):\n    return 'x'\n",
            monkeypatch,
        )
        result = verification.verify(mod)
        assert result.evidence == "clipboard holds the old value"
        assert "clipboard holds the old value" in result.suffix


class TestExecuteToolReportsTheVerdict:
    def test_an_unverifiable_skill_says_so_in_its_result(self, tmp_path, monkeypatch):
        """The whole point: the caller must not read the skill's string as fact."""
        from shani_chronoa import tools

        mod = _module(
            tmp_path, "unver", "def run(a):\n    return 'All done successfully.'\n", monkeypatch
        )
        tools._HANDLER_FNS[mod] = _import_module(mod).run
        out = tools.execute_tool(mod, {})
        assert "unverified" in out
        assert "All done successfully." in out

    def test_a_failed_post_condition_is_loud(self, tmp_path, monkeypatch):
        from shani_chronoa import tools

        mod = _module(
            tmp_path,
            "liar",
            "def _c(a):\n    return False, 'the file was never created'\n"
            "POST_CONDITION = _c\ndef run(a):\n    return 'Created the file.'\n",
            monkeypatch,
        )
        tools._HANDLER_FNS[mod] = _import_module(mod).run
        out = tools.execute_tool(mod, {})
        assert "VERIFICATION FAILED" in out
        assert "the file was never created" in out
        assert "Created the file." in out, "the skill's own claim is still shown, for contrast"


class TestTheRealClipboardRoundTrip:
    """The one production post-condition, exercised against the real display."""

    def test_write_then_read_back_agrees(self):
        pytest.importorskip("shani_chronoa.skills.clipboard")
        from shani_chronoa.skills import clipboard as skill

        if skill._detect_backend() is None:
            pytest.skip("no clipboard backend on this host")

        marker = "chronoa-postcondition-probe"
        written = skill._run_set_clipboard({"text": marker})
        assert "rror" not in written, f"the write itself failed: {written}"

        ok, evidence = skill.POST_CONDITION({"text": marker})
        assert ok, f"read-back disagreed with the write: {evidence}"
        assert evidence == "clipboard read-back matches"

    def test_a_mismatched_expectation_is_caught(self):
        pytest.importorskip("shani_chronoa.skills.clipboard")
        from shani_chronoa.skills import clipboard as skill

        if skill._detect_backend() is None:
            pytest.skip("no clipboard backend on this host")

        ok, evidence = skill.POST_CONDITION({"text": "a value that was never written"})
        assert not ok
        assert "expected" in evidence
