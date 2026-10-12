"""A harvest nothing points at is lost work, and this directory is not in git.

`harness-study/` holds the third-party studies. It is **not a git repository** -
it was created with `git clone` per repo but nothing tracks the folder itself - so
a harvest exists on one disk until that disk is reused. `AGENTS.md` is the index
that tells a reader the study happened and what came of it.

Measured 2026-10-12: **four of twelve were named nowhere in `AGENTS.md`**, one of
them a 286-line deep pass over edge0, and the whole set was invisible from the
file that claims to be the fast path. The other direction was already clean - every
harvest `AGENTS.md` *did* name exists - so the gap was one-sided.

This asserts **both** directions. A scan that only checks "is every file named"
looks like it works on a directory the reader cannot see; a scan that only checks
"does every name resolve" is vacuous the moment the names are all correct. Each
half can fail alone, and each has a control below that proves it.
"""

import os
import pathlib
import re

import pytest

#: `AGENTS.md`'s own naming, so a file the repo does not call a harvest is not
#: swept in as one.
_NAME = re.compile(r"HARVEST-[A-Za-z0-9-]+\.md")

#: Overridable so the controls below can point the check at a tree they built,
#: rather than at the real `harness-study/` - which is shared, and must not be
#: written to by a test.
_DIR_VAR = "CHRONOA_HARVEST_DIR"


def _harvest_dir() -> "pathlib.Path | None":
    override = os.environ.get(_DIR_VAR)
    if override:
        candidate = pathlib.Path(override)
        return candidate if candidate.is_dir() else None
    beside = pathlib.Path(__file__).resolve().parent.parent.parent / "harness-study"
    return beside if beside.is_dir() else None


def _on_disk(directory: pathlib.Path) -> "set[str]":
    return {path.name for path in directory.glob("HARVEST-*.md")}


def _named_in_agents_md() -> "set[str]":
    """Every harvest name the file mentions, wherever it mentions it.

    Read as text rather than parsed: the index is prose and a table, and a
    mention inside a sentence counts exactly as much as a table row - the point
    is that a reader can find the file, not that a structure is well formed.
    """
    agents = pathlib.Path(__file__).resolve().parent.parent / "AGENTS.md"
    return set(_NAME.findall(agents.read_text(encoding="utf-8")))


@pytest.fixture
def harvested():
    """The live directory, or a skip that says why there is nothing to check.

    **A skip is not coverage**, so the reason names the actual condition: this
    directory ships with no git repository, so a fresh clone of this repo has no
    `harness-study/` and neither direction is decidable there.
    """
    directory = _harvest_dir()
    if directory is None:
        pytest.skip(
            f"no harness-study directory beside the repository (or via "
            f"{_DIR_VAR}); these studies are not version-controlled, so a clone "
            f"of this repo alone cannot carry them and neither direction of the "
            f"index check is decidable"
        )
    return directory


class TestTheIndexAndTheDirectoryAgree:
    def test_every_harvest_file_is_named_in_agents_md(self, harvested):
        """The direction that was broken: a study nobody can find."""
        on_disk, named = _on_disk(harvested), _named_in_agents_md()
        assert on_disk, (
            f"{harvested} matched no HARVEST-*.md, so this check would pass on "
            f"an empty directory - which is the shape of a check that cannot fail"
        )
        missing = sorted(on_disk - named)
        assert not missing, (
            f"{len(missing)} harvest file(s) are named nowhere in AGENTS.md: "
            f"{', '.join(missing)}. `harness-study/` is not in git, so a study "
            f"nothing points at exists on one disk until that disk is reused."
        )

    def test_every_harvest_named_in_agents_md_exists(self, harvested):
        """The other direction, so the index cannot name a study that is gone."""
        named, on_disk = _named_in_agents_md(), _on_disk(harvested)
        assert named, (
            "AGENTS.md names no harvest at all, so this check would pass on an "
            "index that had lost every entry"
        )
        absent = sorted(named - on_disk)
        assert not absent, (
            f"AGENTS.md names {', '.join(absent)}, which do(es) not exist in "
            f"{harvested}. A pointer to a missing file is worse than none: a "
            f"reader follows it and finds nothing."
        )


class TestTheseChecksCanFail:
    """Both halves above, driven against trees built for the purpose.

    Kept in this file rather than run by hand, because a control that lives in a
    shell history is lost with the shell - which is how eight mutation sets went
    missing in one earlier session.
    """

    def test_an_unindexed_harvest_is_caught(self, tmp_path, monkeypatch):
        directory = tmp_path / "harness-study"
        directory.mkdir()
        (directory / "HARVEST-ALPHA.md").write_text("# alpha\n", encoding="utf-8")
        monkeypatch.setenv(_DIR_VAR, str(directory))
        # `AGENTS.md` names no `HARVEST-ALPHA`, so this must be the violation.
        with pytest.raises(AssertionError) as caught:
            TestTheIndexAndTheDirectoryAgree().test_every_harvest_file_is_named_in_agents_md(
                harvested=directory)
        assert "HARVEST-ALPHA.md" in str(caught.value), caught.value

    def test_a_pointer_to_a_missing_harvest_is_caught(self, tmp_path, monkeypatch):
        """The live `AGENTS.md` names HARVEST-OIU.md, so an empty directory is
        enough to make the other half fire - which is also the proof that the
        half is not reading the same list the first half writes."""
        directory = tmp_path / "harness-study"
        directory.mkdir()
        monkeypatch.setenv(_DIR_VAR, str(directory))
        with pytest.raises(AssertionError) as caught:
            TestTheIndexAndTheDirectoryAgree().test_every_harvest_named_in_agents_md_exists(
                harvested=directory)
        assert "HARVEST-OIU.md" in str(caught.value), caught.value

    def test_an_empty_directory_is_refused_rather_than_passed(self, tmp_path, monkeypatch):
        """**A directory that matches nothing satisfies every "is everything
        named?" question.** That is the shape of a check that cannot fail, and it
        is why `test_every_harvest_file_is_named_in_agents_md` asserts `on_disk`
        is non-empty before it compares anything."""
        directory = tmp_path / "harness-study"
        directory.mkdir()
        monkeypatch.setenv(_DIR_VAR, str(directory))
        with pytest.raises(AssertionError, match="matched no HARVEST"):
            TestTheIndexAndTheDirectoryAgree().test_every_harvest_file_is_named_in_agents_md(
                harvested=directory)