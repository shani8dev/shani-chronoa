"""The two render gaps the work order recorded, closed with a control.

`MAIN-UI-TODO.md` carried two harness gaps side by side:

- no render target for a permission prompt, because `composer-question` built a
  bare label the way `gui/questions.py` does and the row never appeared;
- Diagnostics sections 3-5 absent from every capture, because the panel scrolls
  and the sweep only photographed its top.

Both are *absences*, and an absence is invisible to a green suite. The targets
are listed from the code (`--list states`, `--list sections`) so a sweep reads
them instead of a hand-kept list, and each test carries a negative control: the
permission check refuses a lookalike builder, and the section check refuses an
unknown name. A check that cannot refuse is not a check.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RENDER = REPO / "render_ui.py"


def _list(kind: str) -> list[str]:
    out = subprocess.run(
        [sys.executable, str(RENDER), "--list", kind],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def _drive_composer_source() -> str:
    """The real builder's source, read by AST rather than re-typed."""
    tree = ast.parse(RENDER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_drive_composer":
            return (
                ast.get_source_segment(RENDER.read_text(encoding="utf-8"), node) or ""
            )
    raise AssertionError("_drive_composer is gone, so no composer target can work")


def _draws_real_presenter(source: str) -> bool:
    """The claim the render target makes about the trust moment."""
    return "show_question(" in source and "options_with_cancel(" in source


class TestTheTrustMomentHasATarget:
    def test_the_permission_state_is_in_the_enumerator(self):
        states = _list("states")
        assert "state:composer-permission" in states, (
            "the one moment Chronoa asks before acting still has no picture"
        )
        # The older name stays: it photographs the same real question now.
        assert "state:composer-question" in states

    def test_the_target_drives_the_real_presenter(self):
        """The builder calls `show_question`, not a lookalike label."""
        source = _drive_composer_source()
        assert _draws_real_presenter(source), (
            "a composer target that draws a bare label photographs the harness, "
            "not the permission prompt a person answers"
        )
        # **Negative control.** The same predicate applied to a copy with the
        # call replaced must refuse it; otherwise it would pass on any source
        # text that merely mentions the word in a comment.
        lookalike = source.replace("show_question(", "_not_a_presenter(")
        assert not _draws_real_presenter(lookalike), (
            "the predicate could not see the call it claims to check"
        )

    def test_the_permission_question_offers_real_choices(self):
        """The options come from `permissions`, not a hand-written list."""
        sys.path.insert(0, str(REPO / "usr" / "lib" / "shani-chronoa"))
        from shani_chronoa import permissions

        request = permissions.ApprovalRequest(
            "delete_file", "~/Documents/notes.txt", "file-delete-enabled"
        )
        options = request.options_with_cancel()
        assert permissions.ALLOW_ONCE_CHOICE in options
        assert permissions.DENY_CHOICE in options
        assert request.question and "\n" in request.question, (
            "the fine print must be there for Details, or the prompt is only "
            "half the question the person is asked"
        )


class TestTheLowerDiagnosticsSectionsHaveATarget:
    def test_every_section_is_addressable(self):
        sys.path.insert(0, str(REPO / "usr" / "lib" / "shani-chronoa"))
        from shani_chronoa.gui.surfaces import diagnostics

        listed = _list("sections")
        expected = [f"section:{name}" for name in diagnostics.section_names()]
        assert listed == expected, (
            "the render sweep reads a different population than the panel "
            f"declares: {listed} against {expected}"
        )
        # The three the old capture never reached are the reason for the target.
        for name in ("screen-and-desktop", "optional-extras", "model-servers"):
            assert f"section:{name}" in listed

    def test_an_unknown_section_is_refused(self):
        """**Negative control.** A builder that returns a page for *any* name
        would let a misspelled target photograph the wrong section and pass."""
        sys.path.insert(0, str(REPO / "usr" / "lib" / "shani-chronoa"))
        from shani_chronoa.gui.surfaces import diagnostics

        assert diagnostics.build_section(None, "there-is-no-such-section") is None

    def test_a_named_section_builds_its_own_page(self):
        sys.path.insert(0, str(REPO / "usr" / "lib" / "shani-chronoa"))
        from shani_chronoa.gui.surfaces import diagnostics

        page = diagnostics.build_section(None, "model-servers")
        assert page is not None
        title = page.get_title() if hasattr(page, "get_title") else ""
        assert "Model servers" in title, (
            "the target is drawing a page other than the one it named"
        )
        assert callable(page.status), (
            "the section's own health is not exposed, so a capture cannot say "
            "whether the rows below the fold were read"
        )
