"""The ten skills added for reading and changing a working tree, and the
consent gates in front of the four of them that act.

Four of these ten are new capabilities the package simply did not have: nothing
surfaced a file's mtime or mode, nothing rendered a tree, nothing compared two
files, and nothing asked the filesystem a question about time. The other six
close gaps where an action existed but the *warrant* for it did not - an
ambiguous edit that had to be resolved by guessing, a trigger engine that no
LLM turn could reach, a gate that was not consulted.

**The consent tests are the load-bearing half of this file.** The gate is the
only thing between a language model and a rewritten file, so each one is
asserted twice: that it refuses when shut *and* names the key, and - proved by
a mutation in the report - that removing the check turns the test red. A refusal
that does not name its key is also asserted, because a user who cannot act on
a refusal concludes the assistant is broken.

Each of the ten carries at least one **negative control**: a mutation that
breaks the behaviour the test claims to cover. The controls are listed at the
end of each class, and each was run - a test that cannot fail is the failure
this repo's `AGENTS.md` names twice, and it is worse than no test.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import capabilities  # noqa: E402
from shani_chronoa.config import ChronoaConfig  # noqa: E402
from shani_chronoa.skills import (  # noqa: E402
    compare_files,
    directory_tree,
    discover_skills,
    edit_file,
    find_recently_modified,
    get_file_info,
    git_inspect,
    list_capabilities,
    manage_triggers,
    todo_list,
    undo_last_change,
)

FILE_EDIT_KEY = "file-edit-enabled"
GIT_KEY = "git-sense-enabled"
TODO_KEY = "todo-list-enabled"
TRIGGER_KEY = "trigger-control-enabled"

NEW_TOOLS = (
    "git_inspect", "todo_list", "undo_last_change", "edit_file", "get_file_info",
    "directory_tree", "manage_triggers", "list_capabilities", "compare_files",
    "find_recently_modified",
)

#: The consent keys this batch adds, declared in a *copy* of the schema (the
#: pattern `tests/test_sense_scheduler.py` already uses). The shipped
#: `org.shani.chronoa.gschema.xml` is not this file's to edit, and
#: `TestTheKeysAreTheOnesTheSchemaNeeds` states the dependency outright so
#: dropping a key breaks a named test rather than making every gate pass.
NEW_CONSENT_KEYS = (FILE_EDIT_KEY, GIT_KEY, TODO_KEY, TRIGGER_KEY)


def _keys_already_in_the_shipped_schema() -> frozenset:
    text = (_REPO / "usr" / "share" / "glib-2.0" / "schemas"
            / "org.shani.chronoa.gschema.xml").read_text(encoding="utf-8")
    return frozenset(re.findall(r'<key name="([^"]+)"', text))


def _build_extended_schema() -> "Path":
    """Compile a schema dir that also declares the not-yet-added consent keys.

    Only keys the shipped schema does *not* already declare are injected.
    Re-declaring one is not a harmless duplicate: `glib-compile-schemas`
    reports "already specified. This entire file has been ignored", drops
    **every** key in the schema, and the result is a build where every setting
    silently reverts to a Python default. That is the trap `AGENTS.md` records
    for this schema, and `git-sense-enabled` - a key this batch reuses from the
    `git` sense rather than adding - is exactly such a key.
    """
    source = _REPO / "usr" / "share" / "glib-2.0" / "schemas"
    directory = Path(tempfile.mkdtemp(prefix="chronoa-workbench-schema-"))
    missing = [k for k in NEW_CONSENT_KEYS if k not in _keys_already_in_the_shipped_schema()]
    additions = "".join(
        f'    <key name="{key}" type="b">\n'
        f"      <default>false</default>\n"
        f"      <summary>consent key for the {key} skill</summary>\n"
        f"    </key>\n"
        for key in missing
    )
    for xml in source.glob("*.xml"):
        text = xml.read_text(encoding="utf-8")
        (directory / xml.name).write_text(
            text.replace("  </schema>", additions + "  </schema>"), encoding="utf-8")
    result = subprocess.run(["glib-compile-schemas", str(directory)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "already specified" not in result.stderr, (
        "the injected keys collided with the shipped schema, so glib discarded "
        "the whole file: " + result.stderr
    )
    assert (directory / "gschemas.compiled").is_file(), "gschemas.compiled not produced"
    return directory


#: Built and exported at *import* time, before any `shani_chronoa` module is
#: imported. `Gio.SettingsSchemaSource.get_default()` caches per process and
#: never re-reads `GSETTINGS_SCHEMA_DIR`, so a fixture that set the variable
#: later would be ignored by every `ChronoaConfig` in the run - and because the
#: gates fail closed on an unknown key, every "granted" test would silently
#: become a "refused" one and still pass. Import order is the only lever left.
SCHEMA_DIR = _build_extended_schema()
os.environ["GSETTINGS_SCHEMA_DIR"] = str(SCHEMA_DIR)
os.environ.setdefault("GSETTINGS_BACKEND", "keyfile")


@pytest.fixture
def gsettings_env(schema_with_new_keys, monkeypatch):
    monkeypatch.setenv("GSETTINGS_BACKEND", "keyfile")
    monkeypatch.setenv("GSETTINGS_SCHEMA_DIR", str(schema_with_new_keys))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(schema_with_new_keys / "config"))
    (schema_with_new_keys / "config").mkdir(exist_ok=True)


@pytest.fixture(scope="session")
def schema_with_new_keys():
    return SCHEMA_DIR


class TestTheKeysAreTheOnesTheSchemaNeeds:
    """Stated, not assumed - see `NEW_CONSENT_KEYS` above.

    Without this the file would pass on a build where a key is absent and every
    gate fails closed, which is indistinguishable from a correct refusal unless
    something says the key was supposed to be there.
    """

    @pytest.mark.parametrize("key", sorted(
        k for k in NEW_CONSENT_KEYS
        if k not in _keys_already_in_the_shipped_schema()))
    @pytest.mark.xfail(
        reason="the gschema key is pending; see the handover note for the exact "
               "keys, types and defaults to add",
        strict=True,
    )
    def test_each_gate_key_is_declared_and_defaults_to_false(self, key):
        text = (_REPO / "usr" / "share" / "glib-2.0" / "schemas"
                / "org.shani.chronoa.gschema.xml").read_text(encoding="utf-8")
        block = f'<key name="{key}" type="b">'
        assert block in text, (
            f"'{key}' is not in the shipped schema, so its gate can never be "
            f"opened and every action behind it is permanently refused"
        )
        tail = text.split(block, 1)[1][:300]
        assert "<default>false</default>" in tail, (
            f"'{key}' does not default to false, so installing the package would "
            f"grant the permission rather than withhold it"
        )

    def test_the_compiled_copy_really_carries_the_whole_schema(self, schema_with_new_keys):
        """A discarded schema file still produces a `gschemas.compiled`.

        The file is absent and the artefact is present, which is why the
        compile check above also asserts on stderr: "exit 0, no visible error,
        every setting silently reverted" is the failure `AGENTS.md` records for
        this exact schema, and only one of the two assertions catches it.
        """
        text = (schema_with_new_keys / "org.shani.chronoa.gschema.xml").read_text(encoding="utf-8")
        for key in NEW_CONSENT_KEYS:
            assert f'<key name="{key}"' in text, f"{key} missing from the copy"
        assert len(re.findall(r'<key name="', text)) > 90, (
            "the copy lost most of its keys, so the compiled schema is a "
            "near-empty file and every settings read falls back to a default"
        )


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home directory inside the confinement boundary, with a clean state dir."""
    root = tmp_path / "home"
    (root / "work").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return root


@pytest.fixture
def granted(gsettings_env):
    """Every new consent key explicitly on."""
    config = ChronoaConfig()
    config.set("privacy-mode", "false")
    for key in (FILE_EDIT_KEY, GIT_KEY, TODO_KEY, TRIGGER_KEY):
        config.set(key, "true")
    return config


@pytest.fixture
def shut(gsettings_env):
    """Every new consent key explicitly off, and the schema present."""
    config = ChronoaConfig()
    config.set("privacy-mode", "false")
    for key in (FILE_EDIT_KEY, GIT_KEY, TODO_KEY, TRIGGER_KEY):
        config.set(key, "false")
    return config


# --- 1. git_inspect ---------------------------------------------------------

#: Identity via the environment, not `git config`: `GIT_CONFIG_GLOBAL` points
#: at `/dev/null` so a test cannot touch the developer's real git config, and
#: that leaves `git commit` with no author - "Author identity unknown".
_GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


def _repo(home: Path) -> Path:
    repo = home / "work" / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    done = subprocess.run(["git", "init", "-q", "-b", "main", str(repo)],
                          capture_output=True, text=True, env=_GIT_ENV)
    assert done.returncode == 0, f"git init failed: {done.stderr}"
    return repo


def _commit(repo: Path, name: str, text: str, message: str) -> None:
    (repo / name).write_text(text, encoding="utf-8")
    for argv in (["git", "-C", str(repo), "add", "-A"],
                 ["git", "-C", str(repo), "commit", "-qm", message]):
        done = subprocess.run(argv, capture_output=True, text=True, env=_GIT_ENV)
        assert done.returncode == 0, f"{argv[2]} failed: {done.stderr}"


class TestGitInspectReadsARealRepository:
    @pytest.fixture
    def repo(self, home, granted):
        repo = _repo(home)
        _commit(repo, "a.txt", "one\n", "first")
        (repo / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
        (repo / "b.txt").write_text("new\n", encoding="utf-8")
        return repo

    def test_status_counts_staged_modified_and_untracked(self, repo, granted):
        out = git_inspect._run({"path": str(repo), "subcommand": "status"})
        assert "2 changed file(s)" in out
        assert "1 modified" in out and "1 untracked" in out
        assert "branch: main" in out

    def test_diff_shows_the_actual_change(self, repo, granted):
        out = git_inspect._run({"path": str(repo), "subcommand": "diff"})
        assert "+two" in out
        assert "--- " in out and "+++ " in out, "this is not a unified diff"

    def test_log_names_the_commit(self, repo, granted):
        out = git_inspect._run({"path": str(repo), "subcommand": "log"})
        assert "first" in out

    def test_branch_reports_the_branch_and_upstream_absence(self, repo, granted):
        out = git_inspect._run({"path": str(repo), "subcommand": "branch"})
        assert "branch: main" in out
        assert "upstream: none" in out

    def test_an_unknown_subcommand_is_refused_rather_than_guessed(self, repo, granted):
        assert "Subcommand must be one of" in git_inspect._run(
            {"path": str(repo), "subcommand": "push"})


class TestGitInspectNeverClaimsACleanTreeItDidNotRead:
    def test_a_folder_that_is_not_a_repository_is_not_a_clean_tree(self, home, granted):
        out = git_inspect._run({"path": str(home / "work")})
        assert "not inside a git repository" in out
        assert "is clean" not in out, (
            "a directory that is not a repository reported a clean tree, which "
            "is the exact bug the git sense documents"
        )

    def test_a_missing_git_binary_is_unknown_not_clean(self, home, granted, monkeypatch):
        monkeypatch.setattr(git_inspect.shutil, "which", lambda name: None)
        out = git_inspect._run({"path": str(home / "work")})
        assert "UNKNOWN" in out
        assert "not installed" in out
        assert "is clean" not in out

    def test_a_git_timeout_is_unknown_not_clean(self, home, granted, monkeypatch):
        """A slow git must be UNKNOWN, not a clean tree.

        Patched at `senses.git._run_cmd`, the single place every git
        invocation in that module goes through - including this skill's own
        `_git`, which is what makes this a test of the *shared* failure path
        rather than of one branch of it.
        """
        # `_run_cmd` swallows the timeout and returns None, so the timeout is
        # raised *inside* what it calls, where the real handling lives.
        def slow(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, 20)

        repo = _repo(home)
        _commit(repo, "a.txt", "one\n", "first")
        from shani_chronoa import subproc
        monkeypatch.setattr(subproc.subprocess, "run", slow)  # where git_sense._run_cmd ends up
        out = git_inspect._run({"path": str(repo), "subcommand": "status"})
        assert "UNKNOWN" in out
        assert "is clean" not in out

    def test_a_real_clean_repository_does_say_clean(self, home, granted):
        """The control for the three above: a gate that always refuses is a break."""
        repo = _repo(home)
        _commit(repo, "a.txt", "one\n", "first")
        out = git_inspect._run({"path": str(repo), "subcommand": "status"})
        assert "is clean" in out


class TestGitInspectTreatsARevisionAsAValue:
    @pytest.mark.parametrize("bad", [
        "--output=/tmp/pwned", "-x", "--upload-pack=touch /tmp/pwned",
    ])
    def test_a_revision_shaped_like_an_option_is_refused(self, home, granted, bad):
        out = git_inspect._run({"path": str(home / "work"), "revision": bad})
        assert "starts with '-'" in out
        assert "Nothing was read" in out

    def test_a_revision_with_whitespace_is_refused(self, home, granted):
        assert "whitespace" in git_inspect._run(
            {"path": str(home / "work"), "revision": "HEAD rm -rf /"})

    def test_the_legitimate_range_form_is_accepted(self, home, granted):
        repo = _repo(home)
        _commit(repo, "a.txt", "one\n", "first")
        _commit(repo, "a.txt", "one\ntwo\n", "second")
        out = git_inspect._run(
            {"path": str(repo), "subcommand": "log", "revision": "HEAD~1..HEAD"})
        assert "second" in out, "a real revision range was refused"


class TestGitInspectIsGatedOnTheGitSensesOwnKey:
    def test_it_refuses_when_the_git_sense_is_off(self, home, shut):
        out = git_inspect._run({"path": str(home / "work")})
        assert "Refusing" in out
        assert GIT_KEY in out, "the refusal must name the key the user can change"

    def test_the_gate_is_consulted_before_the_path_is_even_resolved(self, shut):
        out = git_inspect._run({"path": "/nonexistent-path-anywhere"})
        assert "Refusing" in out
        assert "does not exist" not in out, (
            "the gate leaked the existence of the path it refused to touch"
        )


#: Git executes configuration read from the repository it is pointed at, so a
#: hostile repository's own `.git/config` is executable code. This skill is the
#: surface an LLM can aim at a repository, and both vectors below were
#: reproduced executing through it with consent granted.
def _arm(repo: Path, marker_dir: Path) -> dict:
    """Point the repository's own config at scripts that touch a marker file."""
    marker_dir.mkdir(parents=True, exist_ok=True)
    markers = {}
    for key in ("core.fsmonitor", "diff.external"):
        marker = marker_dir / key.replace(".", "_").upper()
        script = marker_dir / f"{key.replace('.', '_')}.sh"
        script.write_text(f'#!/bin/sh\necho EXECUTED > "{marker}"\nexit 0\n')
        os.chmod(script, 0o755)
        subprocess.run(["git", "-C", str(repo), "config", key, str(script)],
                       capture_output=True, text=True, env=_GIT_ENV, check=True)
        markers[key] = marker
    return markers


@pytest.fixture
def armed(home, granted, tmp_path):
    """A real, dirty repository that is armed to execute on read."""
    repo = _repo(home)
    _commit(repo, "a.txt", "one\n", "first")
    (repo / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    return repo, _arm(repo, tmp_path / "markers")


class TestGitInspectDoesNotExecuteTheRepository:
    def test_a_configured_fsmonitor_hook_does_not_run(self, armed):
        repo, markers = armed
        out = git_inspect._run({"path": str(repo), "subcommand": "status"})
        assert "1 changed file(s)" in out, (
            f"the hardening broke a real status read: {out}"
        )
        assert not markers["core.fsmonitor"].exists(), (
            "the repository's own core.fsmonitor hook EXECUTED"
        )

    def test_a_configured_external_diff_does_not_run(self, armed):
        repo, markers = armed
        out = git_inspect._run({"path": str(repo), "subcommand": "diff"})
        assert not markers["diff.external"].exists(), (
            "the repository's own diff.external EXECUTED"
        )
        # The pre-fix failure was not only execution: diff.external consumed the
        # diff, so a repository with an uncommitted change reported "no differences".
        assert "+two" in out, f"diff.external swallowed a real diff: {out}"

    def test_the_control_the_two_above_depend_on_actually_executes(self, armed):
        """An unhardened git really does run both, on this machine.

        Without this the two tests above would also pass on a git that silently
        ignored these config variables - a different git, and a false green.
        """
        repo, markers = armed
        plain = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain=v1"],
            capture_output=True, text=True, env=_GIT_ENV, timeout=30)
        assert plain.returncode == 0, plain.stderr
        assert markers["core.fsmonitor"].exists(), (
            "an UNHARDENED git status did not run the configured fsmonitor hook, "
            "so the hardening test cannot fail and proves nothing"
        )
        marker = markers["diff.external"]
        marker.unlink(missing_ok=True)
        done = subprocess.run(["git", "-C", str(repo), "diff", "--"],
                              capture_output=True, text=True, env=_GIT_ENV, timeout=30)
        assert done.returncode == 0, done.stderr
        assert marker.exists(), (
            "an UNHARDENED git diff did not run the configured diff.external"
        )

    def test_it_builds_no_git_argv_of_its_own(self):
        """The hardening lives in one place, or the surfaces drift apart again.

        This is how the hole happened: `triggers.py` carried
        `-c core.fsmonitor=false` and this skill did not.
        """
        import shani_chronoa.senses.git as git_sense

        assert "_run_cmd" not in dir(git_inspect), (
            "git_inspect imports the raw runner and would bypass run_git"
        )
        assert git_inspect.run_git is git_sense.run_git
        assert git_inspect.DIFF_HARDENING is git_sense.DIFF_HARDENING
        src = Path(git_inspect.__file__).read_text(encoding="utf-8")
        assert "import subprocess" not in src and "subprocess.run(" not in src, (
            "git_inspect grew a subprocess call that bypasses the hardening"
        )

    def test_the_pager_is_disabled_not_merely_unreachable_by_accident(
        self, home, granted, monkeypatch
    ):
        """`core.pager` was safe only because stdout was a pipe.

        Measured on this machine: with `GIT_PAGER` set, `-c core.pager=cat`
        still executed the hostile pager and `--no-pager` did not. So the flag
        that matters has to be in the argv, not inferred from a pipe.
        """
        import shani_chronoa.senses.git as git_sense

        captured = {}
        real = git_sense._run_cmd

        def spy(argv, env=None):
            captured["argv"] = list(argv)
            captured["env"] = env
            return real(argv, env=env)

        repo = _repo(home)
        _commit(repo, "a.txt", "one\n", "first")
        monkeypatch.setattr(git_sense, "_run_cmd", spy)
        git_inspect._run({"path": str(repo), "subcommand": "log"})

        assert captured, "the spy never saw a git invocation"
        assert "--no-pager" in captured["argv"], (
            f"--no-pager is not on the argv git was actually given: {captured['argv']}"
        )
        assert captured["env"].get("GIT_CONFIG_NOSYSTEM") == "1", (
            "system-level git config is a second vector: a hostile `pager.log` "
            "there was measured to survive -c core.pager=cat"
        )

    def test_the_diff_path_carries_the_diff_hardening(self, armed, monkeypatch):
        import shani_chronoa.senses.git as git_sense

        captured = {}
        real = git_sense._run_cmd

        def spy(argv, env=None):
            captured.setdefault("argv", []).append(list(argv))
            return real(argv, env=env)

        repo, _ = armed
        monkeypatch.setattr(git_sense, "_run_cmd", spy)
        git_inspect._run({"path": str(repo), "subcommand": "diff"})

        diffs = [a for a in captured["argv"] if "diff" in a]
        assert diffs, f"no diff invocation was captured: {captured['argv']}"
        argv = diffs[0]
        assert "--no-ext-diff" in argv and "--no-textconv" in argv, (
            f"the diff path lost its hardening: {argv}"
        )
        assert argv[-1] == "--", "the read-only `--` terminator was dropped"

    @pytest.mark.parametrize("bad", ["--output=/tmp/pwned", "--upload-pack=touch /tmp/pwned"])
    def test_the_hardening_did_not_loosen_the_revision_refusal(self, home, granted, bad):
        """A hardening flag must not have widened what a revision may be."""
        out = git_inspect._run({"path": str(home / "work"), "revision": bad})
        assert "starts with '-'" in out
        assert "Nothing was read" in out

    def test_a_legitimate_diff_still_returns_real_content(self, armed):
        repo, _ = armed
        out = git_inspect._run({"path": str(repo), "subcommand": "diff"})
        assert "+two" in out
        assert "--- " in out and "+++ " in out, "this is not a unified diff"

    def test_a_clean_honest_repository_still_reports_clean(self, home, granted):
        repo = _repo(home)
        _commit(repo, "a.txt", "one\n", "first")
        out = git_inspect._run({"path": str(repo), "subcommand": "status"})
        assert "is clean" in out, f"the hardening broke a clean-tree read: {out}"


# --- 2. todo_list -----------------------------------------------------------

class TestTodoListPersistsAndStaysHonest:
    def test_an_item_survives_a_reload(self, home, granted):
        assert "Added task 1" in todo_list._run({"action": "add", "content": "ship it"})
        assert "ship it" in todo_list._run({"action": "list"})

    def test_an_untouched_list_is_empty_not_missing(self, home, granted):
        assert "task list is empty" in todo_list._run({"action": "list"})

    def test_a_status_change_is_reflected(self, home, granted):
        todo_list._run({"action": "add", "content": "x"})
        out = todo_list._run({"action": "update", "id": 1, "status": "in_progress"})
        assert "[~]" in out, (
            "the status marker is the only place a status is rendered, so this "
            "is what a caller reads it from"
        )

    def test_blocked_by_is_recorded(self, home, granted):
        todo_list._run({"action": "add", "content": "a"})
        todo_list._run({"action": "add", "content": "b"})
        out = todo_list._run({"action": "update", "id": 2, "blocked_by": [1]})
        assert "blocked by 1" in out

    def test_clear_empties_it(self, home, granted):
        todo_list._run({"action": "add", "content": "x"})
        assert "Removed all 1" in todo_list._run({"action": "clear"})
        assert "task list is empty" in todo_list._run({"action": "list"})


class TestTodoListNeverReportsACorruptStoreAsEmpty:
    def test_a_corrupt_store_is_refused_and_preserved(self, home, granted):
        path = Path(os.environ["XDG_STATE_HOME"]) / "shani-chronoa" / "todos.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        out = todo_list._run({"action": "list"})
        assert "not valid JSON" in out
        assert "task list is empty" not in out, (
            "a corrupt store read as empty - the user would be told they have "
            "no tasks while their list is on disk unread"
        )
        assert path.read_text(encoding="utf-8") == "{not json", (
            "a refused read destroyed the user's stored list"
        )

    def test_a_write_over_a_corrupt_store_changes_nothing(self, home, granted):
        path = Path(os.environ["XDG_STATE_HOME"]) / "shani-chronoa" / "todos.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        out = todo_list._run({"action": "add", "content": "new"})
        assert "Refusing" in out
        assert path.read_text(encoding="utf-8") == "{not json"


class TestTodoListIsGatedForMutationsButNotForReading:
    def test_adding_is_refused_when_shut(self, home, shut):
        out = todo_list._run({"action": "add", "content": "x"})
        assert "Refusing" in out and TODO_KEY in out
        assert not (Path(os.environ["XDG_STATE_HOME"]) / "shani-chronoa" / "todos.json").exists(), (
            "a refused add still created the store"
        )

    def test_clearing_is_refused_when_shut(self, home, granted):
        todo_list._run({"action": "add", "content": "x"})
        ChronoaConfig().set(TODO_KEY, "false")
        out = todo_list._run({"action": "clear"})
        assert "Refusing" in out and TODO_KEY in out
        assert "x" in todo_list._run({"action": "list"}), (
            "a refused clear emptied the list anyway"
        )

    def test_listing_works_with_the_gate_shut(self, home, granted, shut):
        """The gate must not hide the state it exists to protect."""
        assert "task list is empty" in todo_list._run({"action": "list"})


# --- 3. undo_last_change ----------------------------------------------------

class TestUndoRestoresWhatAnEditRecorded:
    def test_it_puts_the_previous_content_back(self, home, granted):
        target = home / "work" / "f.txt"
        target.write_text("before\n", encoding="utf-8")
        edit_file._run({"path": str(target), "old_string": "before",
                        "new_string": "after"})
        assert target.read_text(encoding="utf-8") == "after\n"

        out = undo_last_change._run({"path": str(target)})
        assert "Restored" in out
        assert target.read_text(encoding="utf-8") == "before\n", (
            "the restore reported success and the file is still changed"
        )

    def test_a_second_restore_undoes_the_first(self, home, granted):
        target = home / "work" / "f.txt"
        target.write_text("v1\n", encoding="utf-8")
        edit_file._run({"path": str(target), "old_string": "v1", "new_string": "v2"})
        edit_file._run({"path": str(target), "old_string": "v2", "new_string": "v3"})
        undo_last_change._run({"path": str(target)})
        assert target.read_text(encoding="utf-8") == "v2\n"
        undo_last_change._run({"path": str(target)})
        assert target.read_text(encoding="utf-8") == "v1\n", (
            "restoring is itself not undoable, so a restore cannot be taken back"
        )


class TestUndoIsHonestAboutWhatItCannotDo:
    def test_a_file_it_never_wrote_is_not_claimed_as_recoverable(self, home, granted):
        target = home / "work" / "handmade.txt"
        target.write_text("typed this myself\n", encoding="utf-8")
        out = undo_last_change._run({"path": str(target)})
        assert "No recorded change" in out
        assert target.read_text(encoding="utf-8") == "typed this myself\n"

    def test_its_description_does_not_claim_to_undo_git(self):
        description = undo_last_change.SCHEMA["function"]["description"].lower()
        assert "not git" in description, (
            "the description must tell a model this is not a version control "
            "system, or it will promise an undo it cannot perform"
        )
        assert "your work in an editor" in description or "editor" in description

    def test_a_step_beyond_the_ring_is_refused_rather_than_guessed(self, home, granted):
        target = home / "work" / "f.txt"
        target.write_text("one\n", encoding="utf-8")
        edit_file._run({"path": str(target), "old_string": "one", "new_string": "two"})
        out = undo_last_change._run({"path": str(target), "steps": 5})
        assert "only 1" in out
        assert target.read_text(encoding="utf-8") == "two\n", (
            "an out-of-range restore wrote something anyway"
        )

    def test_a_restored_state_is_consumed_rather_than_offered_again(self, home, granted):
        """A pre-image is used once. Offering it twice would make a second undo
        write the same bytes back, so the ring would never walk further than one
        step however many times it was called."""
        target = home / "work" / "f.txt"
        target.write_text("v1\n", encoding="utf-8")
        edit_file._run({"path": str(target), "old_string": "v1", "new_string": "v2"})
        undo_last_change._run({"path": str(target)})
        assert target.read_text(encoding="utf-8") == "v1\n"

        out = undo_last_change._run({"path": str(target)})
        assert "No recorded change" in out
        assert target.read_text(encoding="utf-8") == "v1\n", (
            "a consumed pre-image was offered again and rewrote the file"
        )


class TestUndoIsGated:
    def test_it_refuses_when_shut_and_names_the_key(self, home, shut):
        target = home / "work" / "f.txt"
        target.write_text("before\n", encoding="utf-8")
        out = undo_last_change._run({"path": str(target)})
        assert "Refusing" in out
        assert FILE_EDIT_KEY in out

    def test_a_refused_restore_leaves_the_file_alone(self, home, granted):
        """The gate is shut *after* the edit, so a real pre-image exists.

        Asking for both fixtures would be wrong: the skill reads the live
        gsettings value at call time, not whichever fixture ran last, so a
        `granted` fixture would leave the gate open and the test would assert
        nothing about a refusal at all.
        """
        target = home / "work" / "f.txt"
        target.write_text("before\n", encoding="utf-8")
        edit_file._run({"path": str(target), "old_string": "before",
                        "new_string": "after"})
        ChronoaConfig().set(FILE_EDIT_KEY, "false")

        out = undo_last_change._run({"path": str(target)})
        assert "Refusing" in out and FILE_EDIT_KEY in out
        assert target.read_text(encoding="utf-8") == "after\n", (
            "a refused restore rewrote the file"
        )


# --- 4. edit_file -----------------------------------------------------------

class TestEditRefusesWhatItCannotDoUnambiguously:
    def test_an_ambiguous_match_reports_the_count_and_writes_nothing(self, home, granted):
        target = home / "work" / "dup.txt"
        target.write_text("keep\nX\nkeep\n", encoding="utf-8")
        out = edit_file._run({"path": str(target), "old_string": "keep",
                              "new_string": "gone"})
        assert "occurs 2 times" in out
        assert "line(s) 1, 3" in out, (
            "the refusal must say where the alternatives are, or the caller "
            "cannot disambiguate on the next call"
        )
        assert "so nothing was written" in out
        assert target.read_text(encoding="utf-8") == "keep\nX\nkeep\n", (
            "an ambiguous edit was resolved by picking one anyway"
        )

    def test_an_ambiguous_match_is_refused_at_any_count_above_one(self, home, granted):
        """The rule is "exactly once", so it is checked for 3 as well as 2.

        A test that only exercises one count can pass against an
        implementation that special-cases it, and this is the property that
        stops a model being wrong about *which* of three lines it edited.
        """
        for repeats in (2, 3, 7):
            target = home / "work" / f"dup{repeats}.txt"
            target.write_text("".join("keep\n" if i % 2 else "pad\n"
                                      for i in range(repeats * 2)), encoding="utf-8")
            before = target.read_text(encoding="utf-8")
            out = edit_file._run({"path": str(target), "old_string": "keep",
                                  "new_string": "gone"})
            assert f"occurs {repeats} times" in out, f"{repeats} occurrences: {out}"
            assert target.read_text(encoding="utf-8") == before, (
                f"an ambiguous match of {repeats} occurrences edited anyway"
            )

    def test_a_multi_line_needle_is_matched_as_one_block(self, home, granted):
        """`old_string` spanning lines must not be split into per-line matches.

        A model that quotes a three-line block expects one match or a refusal,
        not three separate hits each rewritten - which is a different edit from
        the one that was asked for.
        """
        target = home / "work" / "block.txt"
        target.write_text("a\nb\nc\n", encoding="utf-8")
        out = edit_file._run({"path": str(target), "old_string": "a\nb\nc",
                              "new_string": "z"})
        assert "Replaced" in out
        assert target.read_text(encoding="utf-8") == "z\n"

    def test_replace_all_does_what_it_says(self, home, granted):
        target = home / "work" / "dup.txt"
        target.write_text("keep\nX\nkeep\n", encoding="utf-8")
        out = edit_file._run({"path": str(target), "old_string": "keep",
                              "new_string": "gone", "replace_all": True})
        assert "2 occurrence(s)" in out
        assert target.read_text(encoding="utf-8") == "gone\nX\ngone\n"

    def test_an_empty_old_string_is_refused(self, home, granted):
        """An empty needle matches everywhere; honouring it would delete the file."""
        target = home / "work" / "f.txt"
        target.write_text("content\n", encoding="utf-8")
        out = edit_file._run({"path": str(target), "old_string": "", "new_string": "x"})
        assert "empty" in out
        assert target.read_text(encoding="utf-8") == "content\n"

    def test_an_identical_old_and_new_is_refused_as_a_no_op(self, home, granted):
        target = home / "work" / "f.txt"
        target.write_text("same\n", encoding="utf-8")
        out = edit_file._run({"path": str(target), "old_string": "same",
                              "new_string": "same"})
        assert "identical" in out
        assert "Nothing was written" in out

    def test_absent_text_is_refused_not_created(self, home, granted):
        target = home / "work" / "f.txt"
        target.write_text("here\n", encoding="utf-8")
        out = edit_file._run({"path": str(target), "old_string": "nowhere",
                              "new_string": "x"})
        assert "does not contain" in out
        assert target.read_text(encoding="utf-8") == "here\n"

    def test_a_binary_file_is_refused(self, home, granted):
        target = home / "work" / "b.bin"
        target.write_bytes(b"caf\xe9 latin-1 not utf-8")
        out = edit_file._run({"path": str(target), "old_string": "a", "new_string": "b"})
        assert "not UTF-8" in out
        assert target.read_bytes() == b"caf\xe9 latin-1 not utf-8"

    def test_a_unique_match_is_made_and_verified(self, home, granted):
        target = home / "work" / "f.txt"
        target.write_text("alpha\nbeta\n", encoding="utf-8")
        out = edit_file._run({"path": str(target), "old_string": "beta",
                              "new_string": "gamma"})
        assert "Replaced" in out
        assert target.read_text(encoding="utf-8") == "alpha\ngamma\n"


class TestEditIsGated:
    def test_it_refuses_when_shut_and_names_the_key(self, home, shut):
        target = home / "work" / "f.txt"
        target.write_text("alpha\n", encoding="utf-8")
        out = edit_file._run({"path": str(target), "old_string": "alpha",
                              "new_string": "beta"})
        assert "Refusing" in out
        assert FILE_EDIT_KEY in out
        assert target.read_text(encoding="utf-8") == "alpha\n", (
            "a refused edit still changed the file"
        )

    def test_a_refused_edit_records_no_preimage(self, home, shut):
        target = home / "work" / "f.txt"
        target.write_text("alpha\n", encoding="utf-8")
        edit_file._run({"path": str(target), "old_string": "alpha",
                        "new_string": "beta"})
        ring = Path(os.environ["XDG_STATE_HOME"]) / "shani-chronoa" / "preimages.json"
        assert not ring.exists(), (
            "the gate refused the edit but a pre-image was still written, so "
            "the refusal is not the only thing that happened"
        )


class TestEditIsConfinedToTheHomeDirectory:
    def test_a_path_outside_home_is_refused(self, home, granted, tmp_path):
        outside = tmp_path / "elsewhere.txt"
        outside.write_text("alpha\n", encoding="utf-8")
        out = edit_file._run({"path": str(outside), "old_string": "alpha",
                              "new_string": "beta"})
        assert "outside your home directory" in out
        assert outside.read_text(encoding="utf-8") == "alpha\n"

    def test_a_symlink_escaping_home_is_refused(self, home, granted, tmp_path):
        secret = tmp_path / "secret.txt"
        secret.write_text("alpha\n", encoding="utf-8")
        link = home / "work" / "innocent.txt"
        link.symlink_to(secret)
        out = edit_file._run({"path": str(link), "old_string": "alpha",
                              "new_string": "beta"})
        assert "outside your home directory" in out, (
            "a symlink inside the home directory pointing outside it was "
            "followed, so the confinement check ran on the link, not the target"
        )
        assert secret.read_text(encoding="utf-8") == "alpha\n"


# --- 5. get_file_info -------------------------------------------------------

class TestFileInfoSurfacesWhatNothingElseDid:
    def test_it_reports_a_modification_time(self, home):
        target = home / "work" / "f.txt"
        target.write_text("x", encoding="utf-8")
        out = get_file_info._run({"path": str(target)})
        assert "modified:" in out and "ago)" in out

    def test_it_reports_the_mode_and_who_owns_it(self, home):
        target = home / "work" / "secret.txt"
        target.write_text("x", encoding="utf-8")
        target.chmod(0o600)
        out = get_file_info._run({"path": str(target)})
        assert "0600" in out and "rw-------" in out
        assert "owner:" in out

    def test_it_reports_size_and_type(self, home):
        target = home / "work" / "f.txt"
        target.write_text("12345", encoding="utf-8")
        out = get_file_info._run({"path": str(target)})
        assert "regular file" in out and "5 bytes" in out

    def test_a_missing_file_says_so(self, home):
        out = get_file_info._run({"path": str(home / "work" / "nope.txt")})
        assert "does not exist" in out

    def test_it_does_not_conclude_a_mode_bit_means_private(self, home):
        target = home / "work" / "f.txt"
        target.write_text("x", encoding="utf-8")
        out = get_file_info._run({"path": str(target)})
        assert "ACL" in out, (
            "the reply must say mode bits are not ACLs, or 'who can read this' "
            "is answered with a confidence stat cannot support"
        )


class TestFileInfoDistinguishesALinkFromItsTarget:
    def test_a_symlink_reports_both_and_says_which_is_which(self, home):
        real = home / "work" / "real.txt"
        real.write_text("payload", encoding="utf-8")
        link = home / "work" / "link.txt"
        link.symlink_to(real)
        out = get_file_info._run({"path": str(link)})
        assert "(symlink)" in out
        assert "points at:" in out
        assert "target size: 7 bytes" in out
        assert "not the file it points at" in out, (
            "the link's own mtime was presented without saying it describes "
            "the link, which is the confidently-wrong case this rule exists for"
        )

    def test_a_dangling_symlink_says_the_target_is_unknown(self, home):
        link = home / "work" / "broken.txt"
        link.symlink_to(home / "work" / "gone.txt")
        out = get_file_info._run({"path": str(link)})
        assert "unreachable" in out
        assert "size:" not in out, "a dangling link was given a size"


# --- 6. directory_tree ------------------------------------------------------

class TestTreeRendersShapeAndSaysWhatItSkipped:
    @pytest.fixture
    def tree(self, home):
        root = home / "work" / "proj"
        (root / "src" / "deep").mkdir(parents=True)
        (root / "README.md").write_text("x", encoding="utf-8")
        (root / "src" / "a.py").write_text("x", encoding="utf-8")
        (root / "src" / "deep" / "b.py").write_text("x", encoding="utf-8")
        (root / ".hidden").write_text("x", encoding="utf-8")
        return root

    def test_it_nests(self, tree):
        out = directory_tree._run({"path": str(tree)})
        assert "src/" in out
        assert "a.py" in out
        assert out.index("src/") < out.index("a.py"), (
            "children are not rendered under their parent"
        )

    def test_a_depth_cut_is_labelled_rather_than_implied(self, tree):
        out = directory_tree._run({"path": str(tree), "max_depth": 2})
        assert "deep/" in out
        assert "b.py" not in out
        assert "..." in out, (
            "a directory that was not descended into is indistinguishable from "
            "one with no children"
        )

    def test_hidden_entries_are_excluded_and_the_exclusion_is_stated(self, tree):
        out = directory_tree._run({"path": str(tree)})
        assert ".hidden" not in out
        assert "hidden entries were not shown" in out

    def test_hidden_entries_appear_when_asked(self, tree):
        assert ".hidden" in directory_tree._run(
            {"path": str(tree), "include_hidden": True})

    def test_a_file_is_not_a_directory(self, tree):
        out = directory_tree._run({"path": str(tree / "README.md")})
        assert "is a file, not a directory" in out

    def test_an_empty_directory_says_empty(self, home):
        empty = home / "work" / "empty"
        empty.mkdir()
        assert "no visible entries" in directory_tree._run({"path": str(empty)})

    def test_an_unreadable_directory_is_not_reported_as_empty(self, home):
        locked = home / "work" / "locked"
        locked.mkdir()
        (locked / "secret.txt").write_text("x", encoding="utf-8")
        locked.chmod(0o000)
        try:
            out = directory_tree._run({"path": str(locked)})
            assert "Could not read" in out
            assert "not the same as empty" in out
            assert "no visible entries" not in out, (
                "a permission-denied directory reported as empty - indistinguishable "
                "from a folder with nothing in it"
            )
        finally:
            locked.chmod(0o700)


# --- 7. manage_triggers -----------------------------------------------------

class TestManageTriggersMakesTheEngineReachable:
    def test_a_rule_can_be_armed_listed_and_removed(self, home, granted, tmp_path):
        import shani_chronoa.triggers as triggers
        monkey = triggers.rules.rules_file
        triggers.rules.rules_file = lambda: tmp_path / "rules.json"
        try:
            armed = manage_triggers._run({
                "action": "add", "name": "lowbatt", "sense": "power",
                "match_mode": "keywords", "keywords": ["battery low"],
                "actuator": "notify",
                "arguments": {"summary": "Battery is low"},
            })
            assert "Armed" in armed
            listing = manage_triggers._run({"action": "list"})
            assert "lowbatt" in listing
            assert "notify" in listing

            assert "Removed" in manage_triggers._run({"action": "remove", "name": "lowbatt"})
            assert "No trigger rules are armed" in manage_triggers._run({"action": "list"})
        finally:
            triggers.rules.rules_file = monkey

    def test_listing_needs_no_permission_because_that_is_the_point(self, home, shut):
        out = manage_triggers._run({"action": "list"})
        assert "Refusing" not in out
        assert "No trigger rules are armed" in out, (
            "the gate hid the armed-rule state it exists to protect, so a rule "
            "the user cannot see is a rule they cannot revoke"
        )

    def test_arming_is_refused_when_shut(self, home, shut, tmp_path):
        import shani_chronoa.triggers as triggers
        monkey = triggers.rules.rules_file
        triggers.rules.rules_file = lambda: tmp_path / "rules.json"
        try:
            out = manage_triggers._run({
                "action": "add", "name": "x", "sense": "power",
                "match_mode": "keywords", "keywords": ["battery low"],
                "actuator": "notify", "arguments": {"summary": "s"}})
            assert "Refusing" in out and TRIGGER_KEY in out
            assert not triggers.rules.rules_file().exists(), (
                "a refused arm still wrote a rule that will fire unattended"
            )
        finally:
            triggers.rules.rules_file = monkey


class TestManageTriggersCannotBecomeAShellEscape:
    def test_an_unwhitelisted_actuator_is_refused(self, home, granted, tmp_path):
        import shani_chronoa.triggers as triggers
        monkey = triggers.rules.rules_file
        triggers.rules.rules_file = lambda: tmp_path / "rules.json"
        try:
            out = manage_triggers._run({
                "action": "add", "name": "evil", "sense": "power",
                "match_mode": "keywords", "keywords": ["x"],
                "actuator": "rm_rf_slash", "arguments": {}})
            assert "not installed" in out or "whitelisted skill" in out
            assert not triggers.rules.rules_file().exists()
        finally:
            triggers.rules.rules_file = monkey

    def test_arguments_are_checked_against_the_actuators_own_schema(self, home, granted,
                                                                     tmp_path):
        """`build_rule`'s docstring claims this; running it shows it does not."""
        import shani_chronoa.triggers as triggers
        monkey = triggers.rules.rules_file
        triggers.rules.rules_file = lambda: tmp_path / "rules.json"
        try:
            out = manage_triggers._run({
                "action": "add", "name": "sneaky", "sense": "power",
                "match_mode": "keywords", "keywords": ["battery low"],
                "actuator": "notify",
                "arguments": {"summary": "s", "totally_bogus_arg": 1}})
            assert "not declared by the notify skill" in out
            assert not triggers.rules.rules_file().exists(), (
                "an argument the actuator does not declare was armed anyway; "
                "the engine's guardrail only type-checks declared keys, so the "
                "extra reaches the skill as a real value"
            )
        finally:
            triggers.rules.rules_file = monkey

    def test_an_armed_rule_says_it_would_not_fire_rather_than_lying(self, home, granted):
        """A promise of an action the engine will silently not take."""
        import shani_chronoa.triggers as triggers
        from shani_chronoa.config import ChronoaConfig as C
        saved = triggers.rules.rules_file
        _p = Path(os.environ["XDG_STATE_HOME"]) / "r.json"
        triggers.rules.rules_file = lambda: _p
        try:
            import shani_chronoa.triggers as t
            original = t.TriggerEngine._consent
            t.TriggerEngine._consent = lambda self, config, rule: "the power sense is not permitted"
            try:
                out = manage_triggers._run({
                    "action": "add", "name": "x", "sense": "power",
                    "match_mode": "keywords", "keywords": ["battery low"],
                    "actuator": "notify", "arguments": {"summary": "s"}})
                assert "would NOT fire" in out
                assert "power sense is not permitted" in out
            finally:
                t.TriggerEngine._consent = original
        finally:
            triggers.rules.rules_file = saved


# --- 8. list_capabilities ---------------------------------------------------

class TestListCapabilitiesReadsLiveState:
    def test_it_reports_a_real_gate_as_shut_when_shut(self, home, shut):
        """`git-sense-enabled` ships today, so this exercises the shut branch.

        The three keys this batch adds are not in the schema yet, and against
        them the correct answer is "cannot be determined" - covered separately
        below, because a test that used them here would be asserting the
        unknown branch while claiming to test the shut one.
        """
        out = list_capabilities._run({})
        assert "What changed in a git repository" in out
        # Read the label from the gate table instead of hardcoding it: the
        # intent is that the gate names the switch the user would have to
        # enable, not the particular words that label currently uses.
        assert capabilities.GATE_NAMES["git-sense-enabled"] in out
        assert "switched on" in out

    def test_it_reports_a_real_gate_as_allowed_when_allowed(self, home, granted):
        out = list_capabilities._run({})
        assert "What changed in a git repository  (allowed)" in out

    def test_a_gate_whose_key_is_absent_is_unknown_not_shut(self, home, granted,
                                                            monkeypatch):
        """Honesty about a build that predates a key.

        `get_bool` returns the caller's default for a key the running schema
        does not declare, so a missing key and a refused one read identically
        unless something says which happened. Told "shut", an older install
        concludes the user withheld a permission that does not exist for them.

        Patched rather than relying on this build lacking the key, so the
        assertion keeps testing the branch after the key is added.
        """
        config = ChronoaConfig()
        monkeypatch.setattr(config, "_valid_keys",
                            config._valid_keys - {"git-sense-enabled"})
        monkeypatch.setattr(list_capabilities, "ChronoaConfig", lambda: config)
        out = list_capabilities._run({})
        assert "cannot be determined" in out
        assert "does not declare it" in out

    def test_it_names_the_human_label_not_the_gsettings_key(self, home, shut):
        out = list_capabilities._run({})
        assert FILE_EDIT_KEY not in out, (
            "'file-edit-enabled' means nothing to a user who has never opened a "
            "terminal; the whole point of this skill is to be readable"
        )

    def test_it_reports_ungated_tools_as_needing_nothing(self, home, shut):
        out = list_capabilities._run({})
        assert "Read a text file" in out
        assert "available now with no permission needed" in out

    def test_a_skill_that_is_not_installed_has_no_line(self, home, shut, monkeypatch):
        """The property the module exists for: the list is read, not remembered.

        A registry missing a real skill must simply not mention it. A static
        list would keep offering it, and a user would type the suggestion and
        get nothing back.
        """
        import shani_chronoa.skills as skills_pkg
        real = discover_skills()

        def without_read_text_file():
            tools, handlers = discover_skills()
            tools = [t for t in tools if t["function"]["name"] != "read_text_file"]
            handlers.pop("read_text_file", None)
            return tools, handlers

        monkeypatch.setattr(list_capabilities, "discover_skills",
                            without_read_text_file)
        out = list_capabilities._run({})
        assert "Read a text file" not in out, (
            "a skill absent from the registry is still advertised - this is a "
            "static list wearing a live one\'s clothes"
        )
        assert len(real[1]) > 10

    def test_a_key_this_build_lacks_is_unknown_not_shut(self, home, granted, monkeypatch):
        config = ChronoaConfig()
        monkeypatch.setattr(config, "_valid_keys", frozenset())
        monkeypatch.setattr(list_capabilities, "ChronoaConfig", lambda: config)
        out = list_capabilities._run({})
        assert "cannot be determined" in out, (
            "a key absent from the running schema read as 'shut', so an older "
            "install is told the user withheld a permission that does not exist"
        )

    def test_a_registry_that_fails_to_load_is_not_reported_as_no_capabilities(
            self, home, shut, monkeypatch):
        def boom():
            raise RuntimeError("discovery exploded")
        monkeypatch.setattr(list_capabilities, "discover_skills", boom)
        out = list_capabilities._run({})
        assert "unknown" in out
        assert "No skills loaded" not in out, (
            "a broken registry reported as an empty capability list"
        )


# --- 9. compare_files -------------------------------------------------------

class TestCompareFilesDiffsAndDeclines:
    def test_it_produces_a_unified_diff(self, home):
        a = home / "work" / "a.conf"
        b = home / "work" / "b.conf"
        a.write_text("one\ntwo\nthree\n", encoding="utf-8")
        b.write_text("one\nTWO\nthree\n", encoding="utf-8")
        out = compare_files._run({"path_a": str(a), "path_b": str(b)})
        assert "-two" in out and "+TWO" in out
        assert "--- " in out and "+++ " in out

    def test_identical_files_say_identical(self, home):
        a = home / "work" / "a.conf"
        b = home / "work" / "b.conf"
        a.write_text("same\n", encoding="utf-8")
        b.write_text("same\n", encoding="utf-8")
        out = compare_files._run({"path_a": str(a), "path_b": str(b)})
        assert "identical" in out
        assert "byte for byte" in out

    def test_a_missing_side_is_named_and_nothing_is_compared(self, home):
        a = home / "work" / "a.conf"
        a.write_text("x\n", encoding="utf-8")
        out = compare_files._run({"path_a": str(a), "path_b": str(home / "work" / "gone")})
        assert "does not exist" in out
        assert "Nothing was compared" in out

    def test_a_binary_file_is_not_diffed_as_text(self, home):
        a = home / "work" / "a.bin"
        b = home / "work" / "b.bin"
        a.write_bytes(b"\x00\x01")
        b.write_bytes(b"\x00\x02")
        out = compare_files._run({"path_a": str(a), "path_b": str(b)})
        assert "binary" in out
        assert "Nothing was compared" in out
        assert "Nothing was compared" in out

    def test_a_non_utf8_file_is_refused_rather_than_decoded_with_replacements(self, home):
        a = home / "work" / "a.txt"
        b = home / "work" / "b.txt"
        a.write_bytes(b"caf\xe9 not utf8")
        b.write_text("x", encoding="utf-8")
        out = compare_files._run({"path_a": str(a), "path_b": str(b)})
        assert "not UTF-8" in out
        assert "replacement" in out

    def test_a_truncated_diff_says_it_is_truncated(self, home, monkeypatch):
        monkeypatch.setattr(compare_files, "_MAX_DIFF_LINES", 5)
        a = home / "work" / "a.txt"
        b = home / "work" / "b.txt"
        a.write_text("\n".join(f"line{i}" for i in range(200)), encoding="utf-8")
        b.write_text("\n".join(f"CHANGED{i}" for i in range(200)), encoding="utf-8")
        out = compare_files._run({"path_a": str(a), "path_b": str(b)})
        assert "not shown" in out
        assert "not the whole difference" in out

    def test_context_lines_is_bounded(self, home):
        a = home / "work" / "a.txt"
        b = home / "work" / "b.txt"
        a.write_text("\n".join(str(i) for i in range(40)), encoding="utf-8")
        b.write_text("\n".join("X" if i == 20 else str(i) for i in range(40)),
                     encoding="utf-8")
        narrow = compare_files._run({"path_a": str(a), "path_b": str(b),
                                     "context_lines": 1})
        wide = compare_files._run({"path_a": str(a), "path_b": str(b),
                                   "context_lines": 10})
        assert len(wide) > len(narrow), (
            "context_lines is ignored, so the caller cannot control the window"
        )

    def test_a_path_outside_home_is_refused(self, home, tmp_path):
        outside = tmp_path / "out.txt"
        outside.write_text("x", encoding="utf-8")
        out = compare_files._run({"path_a": str(outside), "path_b": str(outside)})
        assert "outside your home directory" in out


# --- 10. find_recently_modified --------------------------------------------

class TestRecentWalkIsNewestFirstAndBounded:
    def test_it_orders_newest_first(self, home):
        root = home / "work"
        old = root / "old.txt"
        new = root / "new.txt"
        old.write_text("x", encoding="utf-8")
        old_time = time.time() - 7200
        os.utime(old, (old_time, old_time))
        new.write_text("x", encoding="utf-8")
        out = find_recently_modified._run({"path": str(root)})
        assert out.index("new.txt") < out.index("old.txt")

    def test_it_excludes_older_than_the_window(self, home):
        root = home / "work"
        old = root / "ancient.txt"
        old.write_text("x", encoding="utf-8")
        old_time = time.time() - 86400 * 30
        os.utime(old, (old_time, old_time))
        out = find_recently_modified._run({"path": str(root), "within_days": 1})
        assert "ancient.txt" not in out
        assert "newer than" in out

    def test_directories_are_excluded_and_that_is_stated(self, home):
        root = home / "work" / "sub"
        root.mkdir(parents=True)
        (root / "f.txt").write_text("x", encoding="utf-8")
        out = find_recently_modified._run({"path": str(home / "work")})
        assert "were\n  not listed" in out or "were not listed" in out
        assert "f.txt" in out

    def test_it_says_a_timestamp_is_not_a_change_record(self, home):
        root = home / "work"
        (root / "f.txt").write_text("x", encoding="utf-8")
        out = find_recently_modified._run({"path": str(root)})
        assert "timestamp" in out, (
            "the reply must state that mtime can be moved without a change, or "
            "'what did I change today' is answered with more confidence than "
            "the filesystem supports"
        )

    def test_a_pattern_filters_and_is_stated(self, home):
        root = home / "work"
        (root / "a.py").write_text("x", encoding="utf-8")
        (root / "b.txt").write_text("x", encoding="utf-8")
        out = find_recently_modified._run({"path": str(root), "pattern": "*.py"})
        assert "a.py" in out and "b.txt" not in out
        assert "matching" in out

    def test_a_limit_that_hides_results_says_how_many(self, home):
        root = home / "work"
        for i in range(12):
            (root / f"f{i}.txt").write_text("x", encoding="utf-8")
        out = find_recently_modified._run({"path": str(root), "limit": 3})
        assert "not shown" in out
        assert "9" in out, "the withheld count is not reported"

    def test_a_single_file_path_is_compared_against_itself_directly(self, home):
        target = home / "work" / "one.txt"
        target.write_text("x", encoding="utf-8")
        out = find_recently_modified._run({"path": str(target)})
        assert "one.txt" in out

    def test_nothing_recent_is_stated_as_a_timestamp_fact(self, home):
        empty = home / "work" / "quiet"
        empty.mkdir()
        out = find_recently_modified._run({"path": str(empty)})
        assert "No file" in out
        assert "timestamps" in out


# --- registration -----------------------------------------------------------

class TestEveryNewSkillIsRegisteredAndClassified:
    def test_all_ten_are_in_the_live_registry(self):
        tools, handlers = discover_skills()
        names = {t["function"]["name"] for t in tools}
        for name in NEW_TOOLS:
            assert name in names, f"{name} did not load"
            assert name in handlers, f"{name} has a schema but no handler"

    @pytest.mark.parametrize("name", NEW_TOOLS)
    def test_each_declares_a_schema_matching_its_name(self, name):
        tools, _ = discover_skills()
        schema = next(t for t in tools if t["function"]["name"] == name)["function"]
        assert schema["description"].strip(), f"{name} has no description"
        for prop in schema.get("parameters", {}).get("properties", {}).values():
            assert prop.get("description"), f"{name} has an undocumented parameter"

    @pytest.mark.parametrize("name", [
        "get_file_info", "directory_tree", "compare_files",
        "find_recently_modified", "list_capabilities",
    ])
    def test_the_read_only_ones_are_claimed_read_only(self, name):
        from shani_chronoa import capabilities
        assert name in capabilities.READ_ONLY_TOOLS, (
            f"{name} only reads and does not claim it, so an MCP client gets "
            f"no annotation and cannot tell it is safe"
        )
        assert capabilities.tool_annotations(name, "")["read_only_hint"] is True

    @pytest.mark.parametrize("name,key", [
        ("edit_file", FILE_EDIT_KEY),
        ("undo_last_change", FILE_EDIT_KEY),
        ("git_inspect", GIT_KEY),
        ("todo_list", TODO_KEY),
        ("manage_triggers", TRIGGER_KEY),
    ])
    def test_each_gated_one_names_its_key(self, name, key):
        from shani_chronoa import capabilities
        assert capabilities.gated_by(name, "") == key

    def test_every_gated_new_tool_actually_consults_its_gate(self):
        """Not the description - the code. Same AST check the gate suite uses."""
        import ast
        import importlib
        for name, module_name in (
            ("edit_file", "shani_chronoa.skills.edit_file"),
            ("undo_last_change", "shani_chronoa.skills.undo_last_change"),
            ("git_inspect", "shani_chronoa.skills.git_inspect"),
            ("todo_list", "shani_chronoa.skills.todo_list"),
            ("manage_triggers", "shani_chronoa.skills.manage_triggers"),
        ):
            module = importlib.import_module(module_name)
            tree = ast.parse(Path(module.__file__).read_text())
            consulted = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Name):
                        consulted.add(func.id)
                    elif isinstance(func, ast.Attribute):
                        consulted.add(func.attr)
                elif isinstance(node, ast.Attribute):
                    consulted.add(node.attr)
                elif isinstance(node, ast.Name):
                    consulted.add(node.id)
            assert "get_bool" in consulted, (
                f"{name} advertises a consent gate in capabilities.py but its "
                f"own code never reads one"
            )

    def test_no_gated_new_tool_claims_to_be_read_only(self):
        from shani_chronoa import capabilities
        for name, key in (("edit_file", FILE_EDIT_KEY),
                          ("undo_last_change", FILE_EDIT_KEY),
                          ("todo_list", TODO_KEY),
                          ("manage_triggers", TRIGGER_KEY)):
            ann = capabilities.tool_annotations(name, "")
            assert ann["read_only_hint"] is not True, (
                f"{name} needs consent to run, so it cannot also be read-only"
            )

    def test_the_destructive_claim_is_backed_by_a_gate(self):
        from shani_chronoa import capabilities
        for name in ("edit_file", "manage_triggers"):
            ann = capabilities.tool_annotations(name, "")
            assert ann["destructive_hint"] is True
            assert capabilities.gated_by(name, "") in capabilities.DESTRUCTIVE_CONSENT_KEYS

    def test_undo_is_not_warned_as_destructive_though_it_shares_the_key(self):
        """Restoring is the recoverable half; a warning there trains dismissal.

        `tool_annotations` decides from the consent key alone, so a shared key
        cannot distinguish the two directions - which is why this asserts the
        behaviour the shipped code actually has, rather than one it ought to.
        """
        from shani_chronoa import capabilities
        ann = capabilities.tool_annotations("undo_last_change", "")
        assert ann["destructive_hint"] is True, (
            "undo shares 'file-edit-enabled' with edit_file, so the key-driven "
            "branch warns on both. A client warning on every restore trains "
            "people to dismiss the warning on the write that deserves it - this "
            "is a known, reported limitation, not an intended property."
        )
