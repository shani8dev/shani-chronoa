"""An assistant that could read your repository but not record a change in it.

Of 201 skills, one touched git, and it only read: `git_inspect` reports the
working tree, the diff, recent commits, and how far the branch is from its
upstream. So "what's changed?" had an answer and "commit that" did not - the
wrong way round for an assistant on a machine someone works on.

These tests drive a **real repository** in a temporary directory. Nothing here
is a stand-in for git: the assertions that matter are that a commit exists, that
an unstaged file stays unstaged, and that the remote is asked what it has.

**Three properties are the point, and each is asserted from the other side:**

- **Only the files you name are staged.** Two changes exist, one is named, and
  the other is asserted still unstaged afterwards. Without that, a commit is a
  sweep wearing a small request - which is why `edit_file` already carries a
  separate key (`bulk-edit-enabled`) for the same reason.
- **Pushing is not bundled with committing.** Its own key, its own skill, and
  `is_bypass_immune` - the question a person would assume about a push without
  reading the source. Asserted as a property, because a comment claiming it is
  the thing this file has caught three times in other modules.
- **The gates open.** This repository has shipped two switches that could never
  be turned on (`calendar_write`, `fm_radio`), each a permanent refusal wearing
  the clothes of a permission, so the refusal is asserted *and* the grant.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import permissions  # noqa: E402
from shani_chronoa.skills import git_push as PUSH  # noqa: E402
from shani_chronoa.skills import git_write as W  # noqa: E402


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, timeout=60)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A real repository with one commit and two further modified files."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    # **Inside the home directory**, because `files.resolve_in_home` refuses a
    # path outside it - and a repository a person works on is in their home,
    # so the fixture matching that is not a convenience.
    path = home / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.invalid")
    _git(path, "config", "user.name", "Chronoa Test")
    (path / "README.md").write_text("hello\n")
    _git(path, "add", "README.md")
    _git(path, "commit", "-qm", "first")
    (path / "named.txt").write_text("one\n")
    (path / "unnamed.txt").write_text("two\n")
    return path


@pytest.fixture
def granted(monkeypatch):
    """Both git switches on, for the paths this test asserts actually act."""
    class On:
        def get_bool(self, key, default=False):
            return key in (W._CONSENT_KEY, PUSH._CONSENT_KEY)

    monkeypatch.setattr(W, "ChronoaConfig", On)
    monkeypatch.setattr(PUSH, "ChronoaConfig", On)


class TestRecordingAChange:
    def test_a_commit_lands_and_the_repository_agrees(self, repo, granted):
        out = W._run({"action": "commit", "path": str(repo),
                      "files": ["named.txt"], "message": "record one"})
        assert "Committed" in out, out
        # splitlines, not split: a commit subject contains spaces.
        subjects = _git(repo, "log", "--format=%s").stdout.splitlines()
        assert subjects[0] == "record one", (
            f"git does not have the commit the answer described: {subjects[:3]}")

    def test_only_the_named_file_is_staged(self, repo, granted):
        """The bounded-scope property, read from git rather than trusted.

        Both files are modified; one is named. If the skill swept, the other
        would go in too and the request would be a different one.
        """
        W._run({"action": "commit", "path": str(repo),
                "files": ["named.txt"], "message": "record one"})
        staged = _git(repo, "diff", "--cached", "--name-only").stdout
        assert "unnamed.txt" not in staged, (
            f"a file nobody named was committed: {staged!r}")
        committed = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout
        assert "named.txt" in committed, f"the wrong commit: {committed!r}"
        assert "README.md" not in committed, (
            "an unrelated file rode along in the commit")

    def test_no_files_named_stages_nothing(self, repo, granted):
        out = W._run({"action": "commit", "path": str(repo), "message": "everything"})
        assert "Name the files" in out, out
        assert _git(repo, "diff", "--cached", "--name-only").stdout.strip() == "", (
            "a commit request with no files staged the working tree anyway")

    def test_a_clean_file_commits_nothing(self, repo, granted):
        out = W._run({"action": "commit", "path": str(repo),
                      "files": ["README.md"], "message": "nothing changed"})
        assert "no changes" in out, out
        assert _git(repo, "log", "--format=%s").stdout.split()[0] == "first"

    def test_a_branch_is_created_and_checked_out(self, repo, granted):
        assert "Created and switched" in W._run(
            {"action": "branch", "path": str(repo), "name": "feature/x"})
        assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "feature/x"

    def test_an_existing_branch_is_switched_not_duplicated(self, repo, granted):
        W._run({"action": "branch", "path": str(repo), "name": "feature/x"})
        W._run({"action": "branch", "path": str(repo), "name": "main"})
        out = W._run({"action": "branch", "path": str(repo), "name": "feature/x"})
        assert "already existed" in out, out
        listed = [l for l in _git(repo, "branch", "--list", "feature/x").stdout.splitlines()
                  if l.strip()]
        assert len(listed) == 1, (
            "the branch was created twice")

    def test_a_branch_named_like_an_option_is_refused(self, repo, granted):
        out = W._run({"action": "branch", "path": str(repo), "name": "-D"})
        assert "Refusing" in out, out
        assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "main"

    def test_outside_a_repository_nothing_happens(self, tmp_path, granted):
        plain = Path(os.environ["HOME"]) / "not-a-repo"
        plain.mkdir()
        out = W._run({"action": "commit", "path": str(plain),
                      "files": ["x"], "message": "m"})
        assert "not inside a git repository" in out, out


class TestTheSwitchesAreReal:
    @pytest.mark.parametrize("key", ["git-write-enabled", "git-push-enabled"])
    def test_the_key_is_in_the_schema_and_starts_off(self, key):
        """The `fm_radio` / `calendar_write` defect, asserted for both."""
        root = ET.fromstring(
            (_REPO / "usr/share/glib-2.0/schemas/org.shani.chronoa.gschema.xml").read_text())
        keys = {k.get("name"): k for k in root.iter("key")}
        assert key in keys, f"{key} is not in the schema, so its gate can never open"
        default = keys[key].find("default")
        assert default is not None and default.text.strip() == "false"

    def test_the_gate_opens_when_the_switch_is_on(self, repo, tmp_path, monkeypatch):
        """The control that stops this being a permanent refusal."""
        import tempfile

        backend = Path(tempfile.mkdtemp(prefix="gs-"))
        source = _REPO / "usr" / "share" / "glib-2.0" / "schemas"
        shutil.copytree(source, backend, dirs_exist_ok=True)
        result = subprocess.run(["glib-compile-schemas", str(backend)],
                                capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
        (backend / "glib-2.0" / "settings").mkdir(parents=True, exist_ok=True)
        (backend / "glib-2.0" / "settings" / "keyfile").write_text(
            "[org.shani.chronoa]\ngit-write-enabled=true\n")
        monkeypatch.setenv("GSETTINGS_BACKEND", "keyfile")
        monkeypatch.setenv("GSETTINGS_SCHEMA_DIR", str(backend))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(backend))
        from shani_chronoa.config import ChronoaConfig

        assert ChronoaConfig().get_bool("git-write-enabled", False) is True, (
            "the control did not take, so the refusal below would prove nothing")
        out = W._run({"action": "commit", "path": str(repo),
                      "files": ["named.txt"], "message": "real gate"})
        assert "Refusing" not in out, (
            f"file-edit is on in a real keyfile backend and it still refused: {out!r}")

    def test_with_the_switch_off_the_commit_is_refused(self, repo, monkeypatch):
        class Off:
            def get_bool(self, key, default=False):
                return False

        monkeypatch.setattr(W, "ChronoaConfig", Off)
        out = W._run({"action": "commit", "path": str(repo),
                      "files": ["named.txt"], "message": "should not happen"})
        assert W._CONSENT_KEY in out and "Refusing" in out, out
        assert _git(repo, "log", "--format=%s").stdout.splitlines()[0] == "first", (
            "the commit landed while the switch was off")


class TestPushingIsNotBundledWithCommitting:
    def test_push_has_its_own_switch_and_asks_every_time(self):
        """Asserted as a property, not read from a comment."""
        assert PUSH._CONSENT_KEY == "git-push-enabled"
        assert permissions.is_bypass_immune("git_push"), (
            "a push answers from a standing grant; publishing should not")
        assert not permissions.is_bypass_immune("git_commit"), (
            "a local commit is one reset away; asking every time is friction "
            "for no property")

    def test_there_is_no_force_push_and_no_default_remote(self):
        params = PUSH._SCHEMA["function"]["parameters"]
        assert "remote" in params["required"], "a push with no named remote"
        source = (_REPO / "usr/lib/shani-chronoa/shani_chronoa/skills/git_push.py").read_text()
        for argv in ('"--force', "'--force", '"--all"', "'--all'", "--mirror"):
            assert argv not in source, f"{argv} is reachable in git_push"

    def test_a_push_reaches_the_remote_and_the_remote_is_asked(self, repo, tmp_path,
                                                               granted):
        """A real bare repo as the remote, so `ls-remote` works offline."""
        remote = Path(os.environ["HOME"]) / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)],
                       check=True, timeout=60)
        _git(repo, "remote", "add", "origin", str(remote))
        W._run({"action": "commit", "path": str(repo),
                "files": ["named.txt"], "message": "to push"})
        assert "Pushed" in PUSH._run({"path": str(repo), "remote": "origin"}), (
            "the push did not report success")
        # Ask for `main` by name: a bare repo created with `git init --bare`
        # leaves HEAD pointing at master, and pushing `main` does not move
        # it - so a bare `log` reads empty for a push that worked.
        subjects = subprocess.run(["git", "--git-dir", str(remote), "log",
                                   "--format=%s", "main"], capture_output=True,
                                  text=True, timeout=60).stdout.splitlines()
        assert subjects[0] == "to push", (
            f"the remote does not have the commit: {subjects[:3]}")
        ok, evidence = PUSH._post_condition({"path": str(repo), "remote": "origin"})
        assert ok, evidence

    def test_an_unknown_remote_is_refused_rather_than_added(self, repo, granted):
        out = PUSH._run({"path": str(repo), "remote": "upstream"})
        assert "no remote called" in out, out
        assert _git(repo, "remote").stdout.strip() == "", (
            "the refused push added the remote it invented")

    def test_no_remote_named_is_refused(self, repo, granted):
        out = PUSH._run({"path": str(repo)})
        assert "Name the remote" in out, out