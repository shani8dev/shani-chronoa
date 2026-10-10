"""`resolve_conflict`: the three sides git keeps, against a real conflict.

Git stores all three versions of an unmerged path in the index — stage 1 the
common ancestor, stage 2 yours, stage 3 theirs — so *"which side is mine?"*
is readable rather than a judgement. This file drives a **real conflicted
repository** and reads the answers back out of git, not out of the reply.

The properties that matter, each asserted from git's own state:

- **all three stages are shown and labelled**, in the module's own words
  ("the common ancestor (base)", "YOUR side (ours)", "THEIR side"), because a
  reply that prints two blobs and calls them base and theirs is the confident
  wrong answer this skill exists to prevent;
- **`ours` and `theirs` take the side they name** and leave nothing unmerged —
  verified by `git ls-files -u` returning empty, not by the reply saying so;
- **`abort` really aborts**, and a second abort says there was nothing to
  abort rather than reporting damage twice;
- **two switches gate it**: reading follows `git-sense-enabled`, changing the
  working tree needs `git-write-enabled`, and each refusal is asserted *and*
  the grant — this repository has shipped switches that could never be turned
  on, twice;
- **there is no automatic merge.** No reply may claim a judgement was made.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import resolve_conflict as R  # noqa: E402


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, timeout=60)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repository with a real, unresolved merge conflict in `notes.md`.

    Both sides change the same line, so the conflict is a content conflict with
    all three stages present — the shape `_unmerged` reads.
    """
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    path = home / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.invalid")
    _git(path, "config", "user.name", "Chronoa Test")
    (path / "notes.md").write_text("line one\nline two\nline three\n")
    (path / "other.md").write_text("untouched\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "base")
    _git(path, "checkout", "-qb", "theirs")
    (path / "notes.md").write_text("line one\nTHEIRS two\nline three\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "theirs")
    _git(path, "checkout", "-q", "main")
    (path / "notes.md").write_text("line one\nOURS two\nline three\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "ours")
    merged = _git(path, "merge", "theirs")
    assert merged.returncode != 0, f"fixture: the merge did not conflict: {merged.stdout}"
    return path


@pytest.fixture
def two_conflicts(tmp_path, monkeypatch, repo):
    """`repo`, plus a second conflicted file from a second merge.

    **One merge that conflicts on two files.** The first version of this
    fixture unstaged the first conflict, created a second file on a second
    branch and merged that too — and git fast-forwarded the second file into
    a clean state, so the "the other file was swept" assertion had nothing to
    sweep and passed vacuously.
    """
    # Take one side of the existing conflict so the tree is clean enough to
    # commit a second change, then rebuild a conflict across two files.
    _git(repo, "checkout", "--ours", "--", "notes.md")
    _git(repo, "add", "--", "notes.md")
    _git(repo, "commit", "-qm", "settle the first conflict")
    _git(repo, "checkout", "-qb", "both")
    (repo / "notes.md").write_text("line one\nTHEIRS two\nline three\n")
    (repo / "second.md").write_text("THEIRS\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "theirs, both files")
    _git(repo, "checkout", "-q", "main")
    # **Both sides must change again.** With main's `notes.md` identical to the
    # merge base, git takes `theirs` for it cleanly and the second conflict is
    # one file, not two - measured, in a scratch repository, before this line.
    (repo / "notes.md").write_text("line one\nOURS AGAIN two\nline three\n")
    (repo / "second.md").write_text("OURS\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ours, both files")
    assert _git(repo, "merge", "both").returncode != 0, (
        "fixture: the second merge did not conflict")
    return repo


@pytest.fixture
def granted(monkeypatch):
    """Both git switches on, for the paths that have to act for real."""
    class On:
        def get_bool(self, key, default=False):
            return key in (R._READ_KEY, R._WRITE_KEY)

    monkeypatch.setattr(R, "ChronoaConfig", On)


def test_show_prints_all_three_stages_labelled(repo, granted):
    out = R._run({"action": "show", "path": str(repo)})
    # The operation git is in the middle of, and the file, before anything else.
    assert "merge in progress" in out
    assert "notes.md" in out
    # All three stages, each with the label the module promises.
    for stage, label in ((1, "the common ancestor (base)"),
                         (2, "YOUR side (ours)"),
                         (3, "THEIR side (theirs)")):
        assert f"stage {stage} - {label}" in out, out
    # The actual content of each side, read from the index rather than guessed.
    assert "line two" in out          # base
    assert "OURS two" in out
    assert "THEIRS two" in out
    # And the claim it must not make.
    assert "No automatic merge was attempted" in out


def test_ours_takes_our_side_and_clears_the_conflict(repo, granted):
    out = R._run({"action": "ours", "path": str(repo)})
    assert "Took YOUR side" in out
    assert "notes.md" in out
    # **Read back from git, not from the reply.** A reply that reports success
    # on a file still unmerged is the shape this module exists to refuse.
    assert _git(repo, "ls-files", "-u").stdout.strip() == ""
    assert "OURS two" in (repo / "notes.md").read_text()
    assert "THEIRS two" not in (repo / "notes.md").read_text()


def test_theirs_takes_their_side(repo, granted):
    out = R._run({"action": "theirs", "path": str(repo)})
    assert "Took THEIR side" in out
    assert _git(repo, "ls-files", "-u").stdout.strip() == ""
    assert "THEIRS two" in (repo / "notes.md").read_text()


def test_a_named_file_is_taken_and_the_rest_left(two_conflicts, granted):
    """Bounded scope: naming one conflicted file must not settle the other.

    Same property `git_write` carries for staging — a bounded request is not a
    sweep. Verified from git's index, not from the reply's word "second.md".
    """
    before = {line.split("\t", 1)[1] for line in
              _git(two_conflicts, "ls-files", "-u").stdout.splitlines()}
    assert before == {"notes.md", "second.md"}, (
        f"fixture: both files should be unmerged, saw {before}")

    out = R._run({"action": "ours", "path": str(two_conflicts), "paths": "second.md"})
    assert "second.md" in out
    still = {line.split("\t", 1)[1] for line in
             _git(two_conflicts, "ls-files", "-u").stdout.splitlines()}
    assert still == {"notes.md"}, (
        "a request naming one file settled the other as well")
    assert "OURS\n" == (two_conflicts / "second.md").read_text()


def test_ours_on_a_file_that_is_not_conflicted_changes_nothing(repo, granted):
    out = R._run({"action": "ours", "path": str(repo), "paths": "other.md"})
    assert "not a conflicted file" in out
    assert _git(repo, "ls-files", "-u").stdout.strip(), (
        "the untouched conflict was resolved by a request that should have refused")


def test_mark_resolves_a_file_edited_by_hand(repo, granted):
    """The resolution that has no side: the person writes the answer."""
    (repo / "notes.md").write_text("line one\nHAND WRITTEN\nline three\n")
    out = R._run({"action": "mark", "path": str(repo), "paths": "notes.md"})
    assert "Marked" in out
    assert "Notes" not in out                      # no invented judgement
    assert _git(repo, "ls-files", "-u").stdout.strip() == ""
    assert "HAND WRITTEN" in (repo / "notes.md").read_text()


def test_mark_without_a_name_asks_rather_than_guesses(repo, granted):
    out = R._run({"action": "mark", "path": str(repo)})
    assert "Name the file" in out
    assert "notes.md" in out
    assert _git(repo, "ls-files", "-u").stdout.strip(), "an unnamed mark changed the index"


def test_abort_abandons_the_merge(repo, granted):
    out = R._run({"action": "abort", "path": str(repo)})
    assert "Aborted the merge" in out
    assert _git(repo, "ls-files", "-u").stdout.strip() == ""
    assert "OURS two" in (repo / "notes.md").read_text()   # back to before it started
    assert "THEIRS two" not in (repo / "notes.md").read_text()


def test_a_second_abort_says_there_was_nothing_to_abort(repo, granted):
    """**Not damage reported twice.** The operation really is gone, so the
    answer must say so — a repeat that claimed an abort again would be a
    confident answer about a repository that is already clean."""
    R._run({"action": "abort", "path": str(repo)})
    out = R._run({"action": "abort", "path": str(repo)})
    assert "no merge, rebase, cherry-pick or revert in progress" in out
    assert "nothing to abort" in out


def test_reading_needs_the_git_sense(monkeypatch, repo):
    class Off:
        def get_bool(self, key, default=False):
            return False

    monkeypatch.setattr(R, "ChronoaConfig", Off)
    out = R._run({"action": "show", "path": str(repo)})
    assert "Refusing" in out
    assert R._READ_KEY in out          # the key is named, so the refusal is actionable
    # Nothing was read *or* changed by the refusal.
    assert _git(repo, "ls-files", "-u").stdout.strip(), "the refusal touched the index"


def test_changing_the_tree_needs_the_write_switch(monkeypatch, repo):
    """**The escalation.** Reading is granted and changing is not: the switch
    that differs between the two is the write one, and a reply that accepted
    `ours` with it off would be the two-key split failing silently."""
    class ReadOnly:
        def get_bool(self, key, default=False):
            return key == R._READ_KEY

    monkeypatch.setattr(R, "ChronoaConfig", ReadOnly)
    # Reading still works.
    assert "merge in progress" in R._run({"action": "show", "path": str(repo)})
    out = R._run({"action": "ours", "path": str(repo)})
    assert "Refusing" in out
    assert R._WRITE_KEY in out
    assert _git(repo, "ls-files", "-u").stdout.strip(), (
        "ours acted with the write switch off")


def test_a_repository_that_is_not_one_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    plain = tmp_path / "plain"
    plain.mkdir()
    out = R._run({"action": "show", "path": str(plain)})
    assert "not inside a git repository" in out


def test_a_clean_repository_has_nothing_to_resolve(tmp_path, monkeypatch, granted):
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    path = home / "clean"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.invalid")
    _git(path, "config", "user.name", "Chronoa Test")
    (path / "a.txt").write_text("a\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "only")
    out = R._run({"action": "show", "path": str(path)})
    assert "Nothing is conflicted" in out
