"""`move_or_copy_file` in move mode was dead on every call.

    skills/move_or_copy_file.py:80
        shutil.move(str(source), str(destination),
                    overwrite=bool(arguments.get("overwrite")))
    TypeError: move() got an unexpected keyword argument 'overwrite'

**`shutil.move` is `(src, dst, copy_function=copy2)`.** There is no `overwrite`
keyword, so the move branch raised on *every* call - while the copy branch, two
lines above, worked. `except OSError` did not catch a `TypeError`, so the
traceback reached the model as the skill's entire answer and the post-condition
did not notice. The model is offered "move this file" and every such call failed.

The other two `shutil.move` call sites in this tree (`obex_receive.py:249`,
`meeting.py:212`) both call it correctly, so this was one line rather than a
pattern.

**These tests drive the real dispatch path**, because the defect was in the
subprocess the skill actually runs in: calling `_run()` directly passes, and did.

**And the fix was reverted once**, by a concurrent session committing the tree
from a state that predated it - so this file is also the thing that makes the
revert visible rather than silent.
"""
import pathlib
import sys

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.tools import execute_tool_outcome  # noqa: E402


def _call(**kwargs):
    out = execute_tool_outcome("move_or_copy_file", kwargs)
    return out.ran, out.verdict.value, out.text.strip()


class TestMoveModeWorksAtAll:
    def test_moving_a_file_really_moves_it(self, tmp_path):
        """The test the defect made impossible: before the fix this raised
        `TypeError` and returned `ran=False`."""
        src, dst = tmp_path / "a.txt", tmp_path / "b.txt"
        src.write_text("payload\n")
        ran, verdict, text = _call(mode="move", source=str(src), destination=str(dst))
        assert ran, text
        assert verdict == "verified", text
        assert dst.read_text() == "payload\n"
        assert not src.exists()

    def test_move_mode_does_not_return_a_traceback_as_its_answer(self, tmp_path):
        """The failure's actual shape. The `except OSError` around the call does
        not catch `TypeError`, so the traceback *was* the string the model read."""
        src, dst = tmp_path / "a.txt", tmp_path / "b.txt"
        src.write_text("x\n")
        _, _, text = _call(mode="move", source=str(src), destination=str(dst))
        assert "TypeError" not in text, text
        assert "unexpected keyword argument" not in text, text

    def test_copy_mode_still_works(self, tmp_path):
        """The control for the two branches above: `copy` was never broken, so if
        this ever fails the fix has broken something that worked."""
        src, dst = tmp_path / "a.txt", tmp_path / "b.txt"
        src.write_text("payload\n")
        ran, verdict, _ = _call(mode="copy", source=str(src), destination=str(dst))
        assert ran and verdict == "verified"
        assert src.exists() and dst.read_text() == "payload\n"


class TestOverwriteReplacesAndRefuses:
    """The part of the fix that can lose data.

    A directory destination is removed before `shutil.move`, because
    `shutil.move` refuses to move a directory onto an existing one. The guard
    above already established the person asked for replacement - so the question
    these tests exist to answer is whether that guard is load-bearing, and the
    answer has to be yes.
    """

    def test_a_directory_is_replaced_when_overwrite_is_asked_for(self, tmp_path):
        src, dst = tmp_path / "src", tmp_path / "dst"
        src.mkdir()
        (src / "new.txt").write_text("new\n")
        dst.mkdir()
        (dst / "old.txt").write_text("old\n")
        ran, verdict, text = _call(
            mode="move", source=str(src), destination=str(dst), overwrite=True)
        assert ran, text
        assert sorted(p.name for p in dst.iterdir()) == ["new.txt"], (
            "the destination should hold what came out of the source, not both")

    def test_a_refused_move_leaves_the_destination_untouched(self, tmp_path):
        """**The control that makes the `rmtree` above safe.**

        Same fixture, `overwrite` omitted. If the guard at
        `skills/move_or_copy_file.py:67` were ever removed - or weakened - this
        would delete a directory the person never asked to replace. The fix added
        a deletion to a destructive skill's path, so this is the test that says
        the deletion is unreachable without consent.
        """
        src, dst = tmp_path / "src", tmp_path / "dst"
        src.mkdir()
        (src / "new.txt").write_text("new\n")
        dst.mkdir()
        (dst / "precious.txt").write_text("do not lose me\n")
        ran, _, text = _call(mode="move", source=str(src), destination=str(dst))
        assert (dst / "precious.txt").exists(), text
        assert (dst / "precious.txt").read_text() == "do not lose me\n"
        assert (src / "new.txt").exists(), "the source must survive a refusal too"
        assert not ran or "Refusing" in text

    def test_a_file_is_replaced_when_overwrite_is_asked_for(self, tmp_path):
        """A file onto a file needs no `rmtree`: `os.rename` replaces it. Asserted
        because the fix branches on `destination.is_dir()`, and a branch that
        fired for files would be the other data-loss direction."""
        src, dst = tmp_path / "a.txt", tmp_path / "b.txt"
        src.write_text("new\n")
        dst.write_text("old\n")
        ran, _, text = _call(
            mode="move", source=str(src), destination=str(dst), overwrite=True)
        assert ran, text
        assert dst.read_text() == "new\n"

    def test_a_missing_source_is_refused_not_moved(self, tmp_path):
        src, dst = tmp_path / "nope.txt", tmp_path / "b.txt"
        dst.write_text("already here\n")
        _, _, text = _call(mode="move", source=str(src), destination=str(dst))
        assert "does not exist" in text or "Could not move" in text, text
        assert dst.read_text() == "already here\n"


def test_the_skill_is_advertised_with_both_modes():
    """The defect was invisible because `move` is the *default* mode, so the
    broken branch was the default branch."""
    from shani_chronoa.skills import move_or_copy_file

    entries = {e.name: e for e in move_or_copy_file.SKILLS}
    assert "move_or_copy_file" in entries
    # `Skill` is `(name, schema, run)` - the description lives inside the
    # schema's `function`, not on the entry. Asking the wrong place returns an
    # AttributeError that reads as "the skill has no description".
    text = entries["move_or_copy_file"].schema["function"]["description"]
    assert "move" in text.lower() and "copy" in text.lower()