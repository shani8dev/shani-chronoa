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
import sys

import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")
from shani_chronoa import verification  # noqa: E402
from shani_chronoa.verification import Verdict  # noqa: E402

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
        """**The fixture used to be `['false']`, and exit 1 is not a finding.**

        The argv form had no exit-code contract, so every non-zero exit meant
        "the effect is absent" - and a checker using 1 for "I could not tell"
        had its own uncertainty reported to the person as a failure. Exit 2 is
        now the declared code for "the check ran and the effect is not there",
        so this uses a command that exits 2. **The claim is unchanged; what the
        fixture expresses is** - which is the difference between fixing a test to
        match a new contract and weakening one.
        """
        says_absent = tmp_path / "saysabsent"
        says_absent.write_text(
            "#!/bin/sh\necho 'the file is not there' >&2\nexit 2\n",
            encoding="utf-8")
        says_absent.chmod(0o755)
        mod = _module(
            tmp_path, "badcmd",
            f"POST_CONDITION = [{str(says_absent)!r}]\ndef run(a):\n    return 'x'\n",
            monkeypatch,
        )
        result = verification.verify(mod)
        assert result.verdict is Verdict.FAILED
        assert "VERIFICATION FAILED" in result.suffix
        assert "the file is not there" in result.suffix, (
            "the checker's own sentence is the evidence and it is not quoted: "
            f"{result.suffix!r}")

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


class TestTheClipboardArgvIsPerBackend:
    """One argv for two backends is how the Wayland path broke.

    All three call sites built `[tool, "-selection", "clipboard", "-o"]` -
    xclip's form - for whichever tool `_detect_backend` returned. On **Wayland**
    that ran `wl-paste -selection clipboard -o`, and `wl-paste --help` has no
    `-o` and no `-selection`; it takes `--no-newline` / `--type`. Measured on a
    real Wayland session: *"invalid option -- 'o'"*, so `get_clipboard` reported
    "The clipboard is empty." and the post-condition then verified a read it had
    just failed - a failed round trip for a write that had succeeded.

    Wayland is ShaniOS's own session type, so this was not an edge case. The
    `TestTheRealClipboardRoundTrip` pair above catches it only where the tools
    are installed; these pin the argv itself, which is the part that was wrong
    and the part a refactor would get wrong again.
    """

    WAYLAND = ("wayland", "/usr/bin/wl-copy", "/usr/bin/wl-paste")
    X11 = ("x11", "/usr/bin/xclip", "/usr/bin/xclip")

    def test_the_wayland_read_does_not_carry_an_xclip_flag(self):
        from shani_chronoa.skills import clipboard as skill
        argv = skill._read_argv(self.WAYLAND)
        assert argv == ["/usr/bin/wl-paste", "--no-newline"], argv
        assert "-o" not in argv and "-selection" not in argv, argv

    def test_the_wayland_write_does_not_carry_an_xclip_flag(self):
        from shani_chronoa.skills import clipboard as skill
        argv = skill._write_argv(self.WAYLAND)
        assert argv == ["/usr/bin/wl-copy", "--type", "text/plain;charset=utf-8"], argv
        assert "-selection" not in argv, argv

    def test_the_x11_forms_are_unchanged(self):
        """The X11 pair is what the single shared form was correct for."""
        from shani_chronoa.skills import clipboard as skill
        assert skill._read_argv(self.X11) == ["/usr/bin/xclip", "-selection",
                                              "clipboard", "-o"]
        assert skill._write_argv(self.X11) == ["/usr/bin/xclip", "-selection",
                                               "clipboard"]

    def test_a_session_nobody_declared_falls_back_to_the_x11_form(self):
        """Fail toward the form that at least has a chance, not toward silence."""
        from shani_chronoa.skills import clipboard as skill
        unknown = ("", "/usr/bin/tool", "/usr/bin/tool")
        assert skill._read_argv(unknown) == ["/usr/bin/tool", "-selection",
                                             "clipboard", "-o"]
        assert skill._write_argv(unknown) == ["/usr/bin/tool", "-selection",
                                              "clipboard"]

    def test_the_two_backends_never_share_an_argv(self):
        """The property that was violated: one argv covering both backends."""
        from shani_chronoa.skills import clipboard as skill
        assert skill._read_argv(self.WAYLAND) != skill._read_argv(self.X11)
        assert skill._write_argv(self.WAYLAND) != skill._write_argv(self.X11)
