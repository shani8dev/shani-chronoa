"""The reply-card "That was wrong" popover had a test and no picture.

`tests/test_reply_feedback.py` drives the choices through `_build_verdict_popover`
but never maps them: the one moment a person tells Chronoa that an answer was
wrong had no render target, so nothing had ever shown whether the prompt, the
optional-correction entry and the two buttons read as real choices on screen.

The permission-prompt gap in `tests/test_render_targets_for_trust_and_diagnostics.py`
is the pattern followed here: the state is read out of `--list states`, the
builder is checked against the *real* method rather than a lookalike, and each
check carries a negative control that can refuse the lookalike. The focused
capture test goes one step further and renders the target on a private Broadway
display: its negative control is that a capture which reports `NO NODE` is not
a picture, so the assertion can fail when the popover cannot be photographed.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RENDER = REPO / "render_ui.py"
sys.path.insert(0, str(REPO / "usr" / "lib" / "shani-chronoa"))


def _list_states() -> list[str]:
    out = subprocess.run(
        [sys.executable, str(RENDER), "--list", "states"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def _builder_source(name: str) -> str:
    """The builder's source, read by AST rather than re-typed."""
    source = RENDER.read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"{name} is gone, so no verdict target can work")


def _drives_real_popover(source: str) -> bool:
    """The claim the render target makes about the verdict moment."""
    return "_build_verdict_popover(" in source and "add_assistant_turn(" in source


class TestTheVerdictPopoverHasATarget:
    def test_the_state_is_in_the_enumerator(self):
        states = _list_states()
        assert "state:composer-verdict" in states, (
            "the moment a person judges a reply still has no picture"
        )
        # The trust target stays: this one sits beside it in the same family.
        assert "state:composer-permission" in states

    def test_the_target_drives_the_real_popover(self):
        """The builder calls `_build_verdict_popover`, not a hand-made box."""
        source = _builder_source("_verdict_popover_state")
        assert _drives_real_popover(source), (
            "a target that assembles the labels itself photographs the harness, "
            "not the popover a person presses"
        )
        # **Negative control.** The same predicate applied to a copy with the
        # call replaced must refuse it; otherwise it would pass on any source
        # text that merely mentions the method in a comment.
        lookalike = source.replace("_build_verdict_popover(", "_not_a_popover(")
        assert "_not_a_popover(" in lookalike, "the lookalike replacement did not apply"
        assert not _drives_real_popover(lookalike), (
            "the predicate could not see the call it claims to check"
        )


def test_the_verdict_popover_renders_without_no_node(tmp_path):
    """Capture the real target; `NO NODE` is the negative control.

    A file appearing is not a render - a blank PNG is a valid file - but `NO NODE`
    is the renderer saying it could not get a node at all. The popover's content
    box is mapped in a real window, so this assertion can fail: a builder that
    returned a detached widget, or a lookalike with nothing to paint, would
    produce `NO NODE` and turn this test red.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _broadway import start_display

    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        pytest.skip("no XDG_RUNTIME_DIR, so no headless display")
    try:
        number, proc = start_display(runtime, 2620, 2640)
    except RuntimeError as exc:
        pytest.skip(str(exc))
    try:
        out = tmp_path / "verdict.png"
        env = dict(os.environ)
        env.update(
            GDK_BACKEND="broadway",
            BROADWAY_DISPLAY=f":{number}",
            PYTHONDONTWRITEBYTECODE="1",
            GSETTINGS_BACKEND="memory",
            XDG_DATA_HOME=str(tmp_path / "data"),
            XDG_STATE_HOME=str(tmp_path / "state"),
            XDG_CONFIG_HOME=str(tmp_path / "config"),
        )
        env.pop("DISPLAY", None)
        env.pop("WAYLAND_DISPLAY", None)
        done = subprocess.run(
            [
                sys.executable,
                str(RENDER),
                "state:composer-verdict",
                str(out),
                "640",
                "480",
            ],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        # The builder's own caption proves the popover was built; a bare
        # "no NO NODE" passes on any window, including one that never drove
        # the verdict button at all (the composer-question defect).
        assert "STATE: verdict popover:" in done.stdout, (
            "the composer builder ran instead of the verdict builder, so the "
            f"picture is of the window, not the popover:\n{done.stdout[-900:]}"
        )
        assert "NO NODE" not in done.stdout, (
            "the verdict popover produced no node, so the render target never "
            f"photographed it:\n{done.stdout[-900:]}\n{done.stderr[-900:]}"
        )
        assert done.returncode == 0, done.stderr[-900:]
        assert out.exists() and out.stat().st_size > 1000, (
            "no picture was written for the verdict popover"
        )
    finally:
        proc.terminate()
        proc.wait(timeout=10)
