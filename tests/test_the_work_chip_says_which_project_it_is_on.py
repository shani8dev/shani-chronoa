"""The work chip says which project it is on, and what state that project is in.

**The chip already named the project's folder** - the folder name rides on
it in Build mode, so "which code is it changing" is answered without
opening anything. **The branch and the count of changed files were the
rest of it**: a coder's two next questions, and neither had an answer
anywhere on the screen.

So the chip's **tooltip** carries them, read in **one** subprocess -
`git status --short --branch` gives both, and running it twice for two
numbers is twice the wait.

**And the Diff panel became an addressable page**, so the chip's menu
has a "See what changed" row with a real target. The panel was
reachable only by holding the mouse - from the sidebar, which is
collapsed at the width the app opens at.

**Nothing is said when there is nothing to say.** A folder that is not a
checkout, a git that failed, and no project at all all answer the same
way: silence. A chip that said "0 files changed" for a folder that is not
a repository would be a confident wrong answer.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "usr", "lib", "shani-chronoa"))

from shani_chronoa import pages  # noqa: E402
from shani_chronoa.skills import vcs_status  # noqa: E402


class _Config:
    """The strip's config, answering what it reads.

    **A stub, so the strip's refresh does not warn about a missing
    config** - the warnings are noise that hides a real one.
    """

    reply_style = "ordinary"
    privacy_mode = False
    wake_word_enabled = False
    barge_in_vad_enabled = False

    def get_bool(self, _key, default=False):
        return default

    def get(self, *_a, **_k):
        return ""


class _State:
    config = _Config()

    def get(self, *_a, **_k):
        return ""


def _repo(tmp_path) -> Path:
    """A git checkout with one commit and one changed file."""
    target = tmp_path / "checkout"
    target.mkdir()
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")
    subprocess.run(["git", "init", "-q", "-b", "main", str(target)],
                   check=True, env=env, capture_output=True)
    (target / "kept.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(target), "add", "."], check=True, env=env,
                   capture_output=True)
    subprocess.run(["git", "-C", str(target), "commit", "-q", "-m", "one"],
                   check=True, env=env, capture_output=True)
    return target


class TestTheReader:
    def test_the_branch_and_the_count_come_from_one_call(self, tmp_path):
        """**The branch and the changed files**, from the same `git status`
        that names both."""
        target = _repo(tmp_path)
        assert vcs_status.git_state(target) == ("main", 0)

    def test_a_changed_file_is_counted(self, tmp_path):
        """**What a coder wants to know** is how much is uncommitted."""
        target = _repo(tmp_path)
        (target / "kept.txt").write_text("two\n", encoding="utf-8")
        (target / "new.txt").write_text("new\n", encoding="utf-8")
        branch, changed = vcs_status.git_state(target)
        assert branch == "main"
        assert changed == 2, f"{changed} changed file(s) counted"

    def test_a_folder_that_is_not_a_checkout_says_nothing(self, tmp_path):
        """**"0 files changed" for a folder that is not a repository is a
        confident wrong answer** - so the answer is silence."""
        plain = tmp_path / "plain"
        plain.mkdir()
        assert vcs_status.git_state(plain) == ("", 0)


class TestTheChipSaysIt:
    def test_the_tooltip_carries_the_branch_and_the_count(self, tmp_path, monkeypatch):
        """**The chip's tooltip** is where the project's folder already is,
        and the branch belongs beside it."""
        from shani_chronoa.gui.window import _ModeStrip

        target = _repo(tmp_path)
        (target / "kept.txt").write_text("two\n", encoding="utf-8")

        strip = _ModeStrip(_State(), _Config())
        said = strip._project_state_sentence(str(target))
        assert "main" in said, said
        assert "1 file changed" in said, said

    def test_nothing_is_said_without_a_project(self):
        """**No project, no sentence** - the chip's tooltip is about the
        work, and there is no work without a project."""
        from shani_chronoa.gui.window import _ModeStrip

        strip = _ModeStrip(_State(), _Config())
        assert strip._project_state_sentence("") == ""


class TestTheDiffPanelIsAddressable:
    def test_it_is_a_registered_page(self):
        """**Every page used to be reachable only by holding the mouse** -
        and the sidebar is collapsed at the width the app opens at."""
        assert "diff" in pages.page_ids("main"), (
            "the panel of changes is not a page, so nothing can name it")

    def test_the_chip_offers_a_door_to_it(self):
        """**The chip says what the project looks like**, so it is where the
        door to the changes belongs.

        **The row's own label and target**, read out of the menu model -
        a section's rows are not readable from it, which is why this is a
        row of its own."""
        from shani_chronoa.gui.window import _ModeStrip

        strip = _ModeStrip(_State(), _Config())
        menu = strip._work_chip.get_menu_model()
        rows = {}
        for i in range(menu.get_n_items()):
            label = menu.get_item_attribute_value(i, "label")
            action = menu.get_item_attribute_value(i, "action")
            target = menu.get_item_attribute_value(i, "target")
            rows[label.get_string() if label is not None else None] = (
                action.get_string() if action is not None else None,
                target.get_string() if target is not None else None)
        assert "See what changed" in rows, list(rows)
        assert rows["See what changed"] == ("app.show-page", "diff"), (
            f"the door points at {rows['See what changed']}")
