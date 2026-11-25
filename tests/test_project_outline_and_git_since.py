"""git_inspect's since filter. The project_outline half moved with the skill."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_git_log_since():
    from shani_chronoa.config import ChronoaConfig
    from shani_chronoa.skills import git_inspect
    ChronoaConfig().set("git-sense-enabled", "true")
    repo = Path(os.environ["HOME"]) / "repo"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@x"}
    run = lambda *a, **kw: subprocess.run(["git", *a], cwd=repo, env={**env, **kw}, check=True, capture_output=True)
    run("init", "-q")
    (repo / "a").write_text("1"); run("add", "a")
    run("commit", "-qm", "old", GIT_AUTHOR_DATE="2020-01-01T00:00:00", GIT_COMMITTER_DATE="2020-01-01T00:00:00")
    (repo / "a").write_text("2"); run("commit", "-qam", "new")
    out = git_inspect._run({"path": str(repo), "subcommand": "log", "since": "3 days ago"})
    assert "new" in out and " old" not in out, out
    assert "No commits" in git_inspect._run({"path": str(repo), "subcommand": "log", "since": "2099-01-01"})
    assert "since must be" in git_inspect._run({"path": str(repo), "subcommand": "log", "since": "--output=/tmp/x"})
