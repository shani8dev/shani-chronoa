"""`refuse_catalogue` refused `/` and `~` and nothing around them.

Measured by relocating the home directory into a temp tree and recording any
`shutil.rmtree` call instead of performing it: `delete_file` with
`recursive=True` and `path=<the parent of $HOME>` called
`shutil.rmtree` on a directory that held the user's entire home directory,
and `refuse_catalogue` said nothing.

The guard tested `path == root`. An equal path is only the narrowest case of
"this path contains a protected root"; its parents are the rest, and deleting
a parent deletes the root inside it just as surely.
"""

from pathlib import Path

import pytest

from shani_chronoa import files


@pytest.fixture
def home(tmp_path, monkeypatch):
    """The home directory the suite already isolates.

    **A collision I hit with my own fixture.** The suite's autouse
    `_hermetic_env` creates `tmp_path / "home"` and points `$HOME` at it. My
    first version made its own `tmp_path / "home"` and crashed on every test
    with `FileExistsError`. So this just returns the one that already exists
    and repoints the guard at it - `PROTECTED_ROOTS` was computed at import
    time from the *real* home, which is why it must be replaced.
    """
    fake = tmp_path / "home"
    monkeypatch.setattr(files, "PROTECTED_ROOTS", [Path("/"), fake])
    return fake


class TestAncestors:
    def test_the_parent_of_home_is_refused(self, home):
        with pytest.raises(files.PathProblem):
            files.refuse_catalogue(home.parent, "delete")

    def test_the_grandparent_is_refused(self, home):
        with pytest.raises(files.PathProblem):
            files.refuse_catalogue(home.parent.parent, "delete")

    def test_home_itself_is_refused(self, home):
        with pytest.raises(files.PathProblem):
            files.refuse_catalogue(home, "delete")

    def test_root_is_refused(self, home):
        with pytest.raises(files.PathProblem):
            files.refuse_catalogue(Path("/"), "delete")


class TestInsideIsStillAllowed:
    def test_a_subdirectory_is_allowed(self, home):
        allowed = home / "Documents"
        allowed.mkdir()
        files.refuse_catalogue(allowed, "delete")  # must not raise

    def test_a_deep_descendant_is_allowed(self, home):
        deep = home / "a" / "b" / "c"
        deep.mkdir(parents=True)
        files.refuse_catalogue(deep, "delete")

    def test_a_sibling_of_home_is_allowed(self, home, tmp_path):
        sibling = tmp_path / "sibling"
        sibling.mkdir()
        files.refuse_catalogue(sibling, "delete")

    def test_the_verb_names_the_action(self, home):
        with pytest.raises(files.PathProblem, match="unpack into"):
            files.refuse_catalogue(Path("/"), "unpack into")
        with pytest.raises(files.PathProblem, match="delete"):
            files.refuse_catalogue(home, "delete")


class TestTheResolveBeforeCompare:
    def test_a_traversal_that_lands_on_a_parent_is_caught(self, home, tmp_path):
        """`/tmp/x/..` *is* a parent of the home directory, not just textually."""
        sneaky = home / ".." / ".."  # tmp_path
        resolved = sneaky.resolve()
        with pytest.raises(files.PathProblem):
            files.refuse_catalogue(resolved, "delete")
