"""project_outline (ast + patterns, ranked by references) and git_inspect's since filter."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from shani_chronoa.skills import project_outline as po


def test_outline_ranks_the_used_module_first_and_reads_python_exactly():
    proj = Path(os.environ["HOME"]) / "proj"
    (proj / "node_modules").mkdir(parents=True)
    (proj / "core.py").write_text("class Store:\n    def load(self, path):\n        pass\n\ndef helper(x):\n    return x\n")
    (proj / "app.py").write_text("from core import Store, helper\nStore().load('a'); Store(); helper(1)\ndef main():\n    pass\n")
    (proj / "web.ts").write_text("export function render(x) {}\nexport class View {}\n")
    (proj / "node_modules" / "junk.js").write_text("function ignored() {}\n")
    (proj / "broken.py").write_text("def (:\n")
    out = po._run({"path": str(proj)})
    lines = out.splitlines()
    assert lines[1] == "core.py:", out
    assert "  1: class Store" in out and "  2:   def load(path)" in out and "  5: def helper(x)" in out
    assert "web.ts:" in out and "export function render" in out
    assert "ignored" not in out and "(does not parse)" in out
    focused = po._run({"path": str(proj), "query": "render"})
    assert "web.ts" in focused and "core.py" not in focused
    assert "outside your home" in po._run({"path": "/usr"})


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
