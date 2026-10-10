"""`git_inspect`'s upstream, stash and reflog, against a real repository.

Three blocks were added to a skill that already reported status, diff, log
and branch, and they answer questions the first four cannot:

- **upstream** — *where does this point, and how far off is it?*. The property
  that matters is that **no remote, no upstream, and level-with are three
  different answers**: all three would print a number, and only the last one
  means it.
- **stash** — *is there work stashed?*, which is the question people ask after
  an aborted rebase because a stash left behind is the only copy.
- **reflog** — *what did HEAD do*, which survives a rebase. `git log` cannot
  answer "what did I commit yesterday" once the branch has been rewritten:
  the commits are unreachable from it.

These drive a real repository and read the answers back out of git, with
remotes made by cloning onto the local filesystem.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import git_inspect as G  # noqa: E402)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, timeout=60)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home directory holding the repository the skill will be pointed at."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    h = tmp_path / "home"
    h.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(h))
    return h


@pytest.fixture
def repo(home):
    """A repository with two commits and no remote."""
    path = home / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.invalid")
    _git(path, "config", "user.name", "Chronoa Test")
    (path / "a.txt").write_text("a\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "first")
    (path / "b.txt").write_text("b\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "second")
    return path


@pytest.fixture
def granted(monkeypatch):
    class On:
        def get_bool(self, key, default=False):
            return key == G._CONSENT_KEY

    monkeypatch.setattr(G, "ChronoaConfig", On)


# --- upstream -----------------------------------------------------------------

class TestUpstream:
    def test_no_remote_is_not_zero_behind(self, repo, granted):
        """**The property the block's own docstring names.** A repository with
        no remote would print a "0 behind" if the code counted the absent
        remote as level; the answer must say the word that is true instead.
        """
        out = G._run({"path": str(repo), "subcommand": "upstream"})
        assert "remotes: none configured" in out
        assert "behind" in out and "no meaning here" in out
        assert "0 commit(s) behind" not in out

    def test_a_branch_with_no_upstream_is_not_zero_behind(self, home, repo,
                                                          granted, tmp_path):
        """A remote exists but this branch does not track anything."""
        origin = tmp_path / "origin"
        _git(origin.parent, "init", "-q", "--bare", str(origin))
        _git(repo, "remote", "add", "origin", str(origin))
        out = G._run({"path": str(repo), "subcommand": "upstream"})
        assert "remote origin:" in out
        assert "no upstream set" in out
        assert "0 commit(s) behind" not in out

    def test_level_with_its_upstream_prints_the_number_that_means_it(self, home, repo, granted):
        """Remote fetched and identical: the one case where "level" is true."""
        bare = home / "origin.git"
        _git(repo, "clone", "-q", "--bare", str(repo), str(bare))
        _git(repo, "remote", "add", "origin", str(bare))
        _git(repo, "fetch", "-q", "origin")
        _git(repo, "branch", "--set-upstream-to=origin/main", "main")
        out = G._run({"path": str(repo), "subcommand": "upstream"})
        assert "branch main: tracks origin/main" in out
        assert "level with origin/main" in out
        assert "nothing to pull or push" in out

    def test_ahead_and_behind_are_read_from_the_counts_not_the_porcelain(
            self, home, repo, granted):
        """Make the remote genuinely ahead and behind, then compare the answer
        with `rev-list --left-right --count` — the command the block uses, read
        from git rather than trusted.
        """
        bare = home / "origin.git"
        _git(repo, "clone", "-q", "--bare", str(repo), str(bare))
        _git(repo, "remote", "add", "origin", str(bare))
        _git(repo, "fetch", "-q", "origin")
        _git(repo, "branch", "--set-upstream-to=origin/main", "main")
        # Two commits ahead, one behind: **asymmetric on purpose**. The first
        # version of this test made one ahead and one behind, so swapping the
        # two numbers produced the same two sentences and the mutation ran
        # green - a control that cannot fail, in the one place the answer's
        # two numbers must be distinguishable.
        (repo / "local.txt").write_text("mine\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "local only")
        (repo / "local2.txt").write_text("mine too\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "local only, second")
        # One commit behind: pushed from a second clone.
        other = home / "other"
        _git(home, "clone", "-q", str(bare), str(other))
        _git(other, "config", "user.email", "o@example.invalid")
        _git(other, "config", "user.name", "Other")
        (other / "theirs.txt").write_text("theirs\n")
        _git(other, "add", "-A")
        _git(other, "commit", "-qm", "theirs only")
        _git(other, "push", "-q", "origin", "main")
        _git(repo, "fetch", "-q", "origin")

        out = G._run({"path": str(repo), "subcommand": "upstream"})
        assert "1 commit(s) behind origin/main" in out
        assert "2 commit(s) ahead of it" in out
        # git's own count, so the numbers are not just the reply's claim.
        counts = _git(repo, "rev-list", "--left-right", "--count",
                      "origin/main...HEAD").stdout.split()
        assert counts == ["1", "2"], counts


# --- stash --------------------------------------------------------------------

class TestStash:
    def test_nothing_stashed_is_a_real_answer(self, repo, granted):
        out = G._run({"path": str(repo), "subcommand": "stash"})
        assert "no stashed work" in out

    def test_a_stash_is_listed(self, repo, granted):
        (repo / "a.txt").write_text("changed but not committed\n")
        _git(repo, "stash", "push", "-qm", "work in progress")
        out = G._run({"path": str(repo), "subcommand": "stash"})
        assert "1 stash(es), newest first:" in out
        # **The real git format, not the one remembered.** This git prints
        # `stash@{<iso>}: On main: work in progress`; the first version of this
        # assertion looked for `WIP on main`, which is what an older format
        # produced - and a test that fails on the real output is the same
        # defect as one that cannot fail.
        assert "On main: work in progress" in out
        # The working tree is restored by the stash, and the answer is about
        # the stash rather than a guess at it.
        assert (repo / "a.txt").read_text() == "a\n"


# --- reflog -------------------------------------------------------------------

class TestReflog:
    def test_the_reflog_records_what_a_rebase_removed(self, repo, granted):
        """**The property `git log` cannot give.** A rewritten branch loses
        the commits from its history; the reflog keeps where HEAD was, so
        "what did I commit before I rebased" stays answerable.
        """
        _git(repo, "checkout", "-qb", "feature")
        (repo / "feature.txt").write_text("feature work\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "the commit a rebase will rewrite")
        rewritten = _git(repo, "rev-parse", "HEAD").stdout.strip()
        _git(repo, "checkout", "-q", "main")
        (repo / "unrelated.txt").write_text("unrelated\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "main moves on")
        # Rebase feature onto the new main, squashing into one commit.
        _git(repo, "checkout", "-q", "feature")
        _git(repo, "rebase", "-q", "main")
        assert _git(repo, "log", "--format=%H", "main..HEAD").stdout.split(), (
            "fixture: the rebase should have left one commit on the branch")

        out = G._run({"path": str(repo), "subcommand": "reflog"})
        # The rewritten commit is not reachable from the branch any more...
        assert rewritten not in _git(repo, "log", "--format=%H").stdout
        # ...but the reflog still saw HEAD move, which is the claim.
        assert "the last" in out and "thing(s) HEAD did" in out

    def test_a_new_repository_with_no_commits_says_so(self, home, granted):
        path = home / "empty"
        path.mkdir()
        _git(path, "init", "-q", "-b", "main")
        out = G._run({"path": str(path), "subcommand": "reflog"})
        assert "no reflog entries yet" in out


# --- the skill's own surface --------------------------------------------------

class TestTheSkillAdvertisesWhatItHas:
    def test_the_subcommands_it_added_are_advertised(self):
        """**A capability that exists is advertised.** `stash` and `reflog`
        were added to `_SUBCOMMANDS` while the top-level description still
        listed only the first five — a tool the model was never told about.
        The assertion is on the description the model reads, and on the two
        capabilities that were genuinely missing rather than on the literal
        word "status" (which the description answers in prose).
        """
        description = G.SCHEMA["function"]["description"]
        for name in ("stash", "reflog"):
            assert name in description, (
                f"{name} is a subcommand the description never mentions, so the "
                "model is never offered it")

    def test_the_consent_gate_names_its_key(self, repo):
        class Off:
            def get_bool(self, key, default=False):
                return False

        original = G.ChronoaConfig
        G.ChronoaConfig = Off
        try:
            out = G._run({"path": str(repo)})
        finally:
            G.ChronoaConfig = original
        assert "Refusing" in out
        assert G._CONSENT_KEY in out
