"""`vcs_status`: what state is this checkout in - git, subversion or mercurial?

`git_inspect`/`git_branch`/`git_commit` cover git, and git is the only one of
the three they cover. `shani-tools-extra` installs `subversion` and
`mercurial` **by design**, so a machine can carry a `.svn` or a `.hg` and be
told by every existing skill that it is not a repository - a confident wrong
answer about a folder that genuinely is one.

**The kind of repository is read off the directory marker, not guessed**, and
walked *upwards*, because a person names a file inside a checkout far more
often than the checkout root.

**Every output shape here was measured on a real `@blue` slot**
(subversion 1.14.5, mercurial 7.2.4), since neither tool exists on an Ubuntu
dev box:

    svn info      Path / Working Copy Root Path / URL / Relative URL /
                  Repository Root / Repository UUID / Revision / Node Kind
    hg summary    parent: -1:000000000000 tip (empty repository)
                  branch: default

**Two measured facts that shape the parse:**

- **`Revision: 0` is a real revision** - the empty state of a fresh checkout -
  and not "unset". Reading it as missing reports a brand-new working copy as
  unversioned.
- **`hg summary` prints `-1:000000000000` for a repository with no commits**,
  beside the words `(empty repository)`. The parent is not a missing value,
  and the phrase is what says so.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import vcs_status as VS  # noqa: E402

#: Real `svn info` from a working copy on `@blue`, captured verbatim.
SVN_INFO = """Path: var/tmp/chronoa-cli-formats/svnwc
Working Copy Root Path: /var/tmp/chronoa-cli-formats/svnwc
URL: file:///var/tmp/chronoa-cli-formats/svnrepo
Relative URL: ^/
Repository Root: file:///var/tmp/chronoa-cli-formats/svnrepo
Repository UUID: e9406ae1-010c-451e-8784-d6e9579fd6cb
Revision: 0
Node Kind: directory
"""

#: Real `hg summary` on an empty repository, captured verbatim.
HG_SUMMARY = """parent: -1:000000000000 tip (empty repository)
branch: default
"""

HG_SUMMARY_POPULATED = """parent: 3:abcdef012345 tip
branch: default
commit: (clean)
"""

HG_NOT_A_REPO = "abort: no repository found in '/tmp/plain' (or any parent)\n"


def _make(tmp_path, system):
    """A directory that really is a working copy of `system`.

    The marker is written to disk because the whole design reads it from
    there - a stubbed `which` alone would test nothing about detection.
    """
    root = tmp_path / "co"
    root.mkdir(parents=True, exist_ok=True)
    (root / {"git": ".git", "subversion": ".svn",
             "mercurial": ".hg"}[system]).mkdir(exist_ok=True)
    return root


@pytest.fixture
def on_path(monkeypatch):
    """Every tool present, so a test about parsing is not a test about
    installation."""
    monkeypatch.setattr(VS.shutil, "which", lambda name: f"/usr/bin/{name}")


def _stub(monkeypatch, stdout="", rc=0, stderr="", extra=None):
    """Replace `subprocess.run` with something of the **same return type**.

    My first version returned a `(stdout, stderr, rc)` tuple, which the module
    unpacks - but the real `subprocess.run` returns a `CompletedProcess`, and
    a stub with a different shape is a stub that tests the stub. `_run` itself
    does the unpacking, so the stub has to hand back the object it unpacks.
    """
    seen = {}

    def run(command, *a, **k):
        seen.setdefault("calls", []).append(command)
        if extra:
            for needle, payload in extra.items():
                if needle in command:
                    return subprocess.CompletedProcess(
                        command, rc, payload[0], payload[1])
        return subprocess.CompletedProcess(command, rc, stdout, stderr)

    monkeypatch.setattr(VS.subprocess, "run", run)
    return seen


def test_a_git_folder_is_recognised(tmp_path, monkeypatch, on_path):
    co = _make(tmp_path, "git")
    _stub(monkeypatch, "## main...origin/main\n M file.txt\n")
    out = VS._run_skill({"path": str(co)})
    assert "git working copy" in out
    assert "branch" in out


def test_detection_walks_upwards_from_a_nested_path(tmp_path, monkeypatch,
                                                     on_path):
    checkout = _make(tmp_path, "git")
    (checkout / "sub").mkdir()
    """A person names a file inside a checkout, not the checkout root, and
    "this folder is not a repository" is the wrong answer for that.
    """
    _stub(monkeypatch, "## main\n")
    out = VS._run_skill({"path": str(checkout / "sub")})
    assert "git working copy" in out


def test_a_plain_folder_is_not_a_repository(tmp_path, monkeypatch, on_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    out = VS._run_skill({"path": str(plain)})
    assert "not inside a working copy" in out
    assert "Nothing is claimed" not in out


def test_a_git_marker_is_recognised_as_a_file_too(tmp_path, monkeypatch,
                                                 on_path):
    """A worktree or submodule has `.git` as a *file* pointing at the real
    one. `exists()` covers both and the distinction is not ours to make.
    """
    root = tmp_path / "wt"
    root.mkdir()
    (root / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n")
    _stub(monkeypatch, "## main\n")
    out = VS._run_skill({"path": str(root)})
    assert "git working copy" in out


# --- subversion, against the captured `svn info` ------------------------------

def test_svn_info_is_parsed_by_key_not_by_splitting_whitespace(
        tmp_path, monkeypatch, on_path):
    """**`URL:` and `Repository Root:` hold URLs**, so a whitespace split would
    report `file:///var/tmp/...` as the path and `^/` as the repository.
    """
    root = _make(tmp_path, "subversion")
    _stub(monkeypatch, SVN_INFO)
    out = VS._run_skill({"path": str(root)})
    assert "subversion working copy" in out
    assert "URL: file:///var/tmp/chronoa-cli-formats/svnrepo" in out
    assert "Repository Root: file:///var/tmp/chronoa-cli-formats/svnrepo" in out
    assert "Relative URL: ^/" in out
    # **And a value that genuinely holds spaces**, because the measured URLs
    # do not - so a whitespace-splitting parser passes every line above. This
    # is the only assertion that separates "split on the first colon" from
    # "split on whitespace", and it is what makes the mutation that does the
    # latter fail.
    _stub(monkeypatch, SVN_INFO + "Last Changed Author: A Person With A Name\n")
    out = VS._run_skill({"path": str(root)})
    assert "A Person With A Name" in out


def test_revision_zero_is_a_revision_not_a_missing_value(
        tmp_path, monkeypatch, on_path):
    """**Measured: a fresh checkout reports `Revision: 0`.** Reading that as
    "unset" reports a brand-new working copy as unversioned.
    """
    root = _make(tmp_path, "subversion")
    _stub(monkeypatch, SVN_INFO)
    out = VS._run_skill({"path": str(root)})
    assert "Revision: 0" in out


def test_a_svn_path_that_is_not_a_working_copy_says_so(
        tmp_path, monkeypatch, on_path):
    """Measured wording: `is not a working copy`, exit 1. That is a real
    answer, so it is passed through rather than reported as a read failure.
    """
    root = _make(tmp_path, "subversion")
    _stub(monkeypatch, "", rc=1,
          stderr="svn: E155007: '/tmp/x' is not a working copy\n")
    out = VS._run_skill({"path": str(root)})
    assert "is not a working copy" in out
    assert "Nothing is claimed" in out


# --- mercurial, against the captured `hg summary` ----------------------------

def test_hg_summary_is_parsed(tmp_path, monkeypatch, on_path):
    root = _make(tmp_path, "mercurial")
    _stub(monkeypatch, HG_SUMMARY,
          extra={"root": (str(root) + "\n", "")})
    out = VS._run_skill({"path": str(root)})
    assert "mercurial working copy" in out
    assert "branch: default" in out


def test_an_empty_hg_repository_is_not_an_error(tmp_path, monkeypatch, on_path):
    """**`-1:000000000000` with `(empty repository)` is a first repository
    being correct.** Reporting the parent as missing would read as a broken
    checkout.
    """
    root = _make(tmp_path, "mercurial")
    _stub(monkeypatch, HG_SUMMARY,
          extra={"root": (str(root) + "\n", "")})
    out = VS._run_skill({"path": str(root)})
    assert "(empty repository)" in out
    assert "-1:000000000000" in out
    assert "could not report" not in out


def test_a_populated_hg_repository_is_read_too(tmp_path, monkeypatch, on_path):
    root = _make(tmp_path, "mercurial")
    _stub(monkeypatch, HG_SUMMARY_POPULATED,
          extra={"root": (str(root) + "\n", "")})
    out = VS._run_skill({"path": str(root)})
    assert "abcdef012345" in out
    assert "clean" in out


def test_hg_outside_a_repository_is_passed_through(tmp_path, monkeypatch, on_path):
    root = _make(tmp_path, "mercurial")
    _stub(monkeypatch, "", rc=255, stderr=HG_NOT_A_REPO)
    out = VS._run_skill({"path": str(root)})
    assert "no repository found" in out


# --- the boundaries -----------------------------------------------------------

def test_a_repository_whose_tool_is_missing_says_so(tmp_path, monkeypatch):
    """A `.hg` with no `hg` installed is still a mercurial checkout - the
    marker is on disk - and saying "this is not a repository" would be wrong.
    """
    root = _make(tmp_path, "mercurial")
    monkeypatch.setattr(VS.shutil, "which", lambda name: None)
    out = VS._run_skill({"path": str(root)})
    assert "mercurial working copy" in out
    assert "hg is not installed" in out
    assert "hg package" in out


def test_a_missing_path_is_refused(tmp_path):
    out = VS._run_skill({"path": str(tmp_path / "nope")})
    assert "nothing at" in out
    assert "Nothing was guessed" in out


def test_no_path_asks_for_one():
    out = VS._run_skill({})
    assert "need a folder" in out


def test_it_names_the_system_even_though_git_has_its_own_skills(
        tmp_path, monkeypatch, on_path):
    """`git_inspect` exists; this still answers, because the question that
    needs it is "which kind of repository is this", and a `.svn` folder has to
    be recognised before anything else is tried.
    """
    root = _make(tmp_path, "subversion")
    _stub(monkeypatch, SVN_INFO)
    out = VS._run_skill({"path": str(root)})
    assert "subversion" in out


def test_the_real_git_is_read_when_present(tmp_path):
    """Not a stub: a real `git init`, read through the real binary."""
    if not VS.shutil.which("git"):
        pytest.skip("git is not installed here")
    root = tmp_path / "realgit"
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    out = VS._run_skill({"path": str(root)})
    assert "git working copy" in out
    assert "branch" in out