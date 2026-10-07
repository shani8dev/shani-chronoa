"""The sensitive-path guard: no tool call may read or shell out to credentials
or key material.

Adapted from Maze-AI's `maze_ai/agent/safety.py` (`_SENSITIVE_PATTERNS`,
`is_sensitive_path`, `touches_sensitive_path`) to Chronoa's layouts. The two
wirings that must hold, both by execution rather than by import:

- the file skills refuse directly (same PathProblem shape as refuse_catalogue);
- the sandbox executor refuses any argv - direct or `sh -c` script - whose
  command line touches the list, because a shell skill reaches the system
  through a subprocess, not through a Python path check.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import files  # noqa: E402
from shani_chronoa.sandbox.executor import SandboxExecutor  # noqa: E402
from shani_chronoa.sandbox.models import SandboxConfig, SandboxLevel  # noqa: E402


def _level_3(**overrides) -> SandboxConfig:
    return SandboxConfig(level=SandboxLevel.LEVEL_3_HOST_USER, timeout_seconds=10, **overrides)


class TestIsSensitivePath:
    @pytest.mark.parametrize(
        "path",
        [
            "~/.ssh/id_rsa",
            "/home/user/.ssh/config",
            "/etc/shadow",
            "/etc/gshadow",
            "/etc/sudoers",
            "~/.gnupg/pubring.kbx",
            "~/.aws/credentials",
            "~/.config/dconf/user",
            "~/.git-credentials",
            "~/project/.env",
            "~/project/.env.local",
            "~/.netrc",
            "~/.password-store/something.gpg",
            "~/certs/server.pem",
            "~/.local/share/keyrings/default-login",
            "~/.bash_history",
            "~/.docker/config.json",
        ],
    )
    def test_sensitive_paths_are_named(self, path):
        assert files.is_sensitive_path(path), f"{path!r} must be treated as sensitive"

    @pytest.mark.parametrize(
        "path",
        [
            "~/Documents/notes.txt",
            "/tmp/scratch.txt",
            "~/projects/demo/main.py",
            "~/Music/song.mp3",
            # a directory whose name merely contains a sensitive word
            "~/projects/ssh-notes/readme.md",
            "~/TODO.env ",  # the regex anchors on the token, so this is fine
        ],
    )
    def test_ordinary_paths_are_allowed(self, path):
        assert not files.is_sensitive_path(path), f"{path!r} must not be refused"


class TestTouchesSensitivePath:
    def test_path_hidden_inside_a_pipeline_is_found(self):
        assert files.touches_sensitive_path("cat ~/.ssh/id_rsa | base64") != ""

    def test_command_with_no_sensitive_path_passes(self):
        assert files.touches_sensitive_path("ls ~/Documents | head") == ""

    def test_empty_command_is_safe(self):
        assert files.touches_sensitive_path("") == ""


class TestRefuseSensitive:
    def test_raises_on_sensitive_path(self):
        with pytest.raises(files.PathProblem):
            files.refuse_sensitive(Path.home() / ".ssh" / "id_rsa", "read")

    def test_allows_ordinary_path(self):
        files.refuse_sensitive(Path.home() / "Documents" / "notes.txt", "read")


class TestExecutorRefusesSensitive:
    """The default LEVEL_3 path a real skill call takes."""

    def test_direct_argv_pointing_at_key_material_is_refused(self, tmp_path):
        executor = SandboxExecutor(sandboxes_root=str(tmp_path / "sandboxes"))
        code, out, _ = executor.execute(["cat", "/etc/shadow"], _level_3())
        assert code == 126, f"expected a security refusal, got {code}: {out}"
        assert "credentials or private keys" in out

    def test_argv_with_a_sensitive_path_in_a_pipeline_is_refused(self, tmp_path):
        executor = SandboxExecutor(sandboxes_root=str(tmp_path / "sandboxes"))
        code, out, _ = executor.execute(["sh", "-c", "cat ~/.aws/credentials"], _level_3())
        assert code == 126, f"expected a security refusal, got {code}: {out}"

    def test_shell_script_is_held_to_the_same_guard(self, tmp_path):
        executor = SandboxExecutor(sandboxes_root=str(tmp_path / "sandboxes"))
        code, out, _ = executor.execute(["sh", "-c", "cat /etc/shadow"], _level_3())
        assert code == 126

    def test_ordinary_argv_still_runs(self, tmp_path):
        executor = SandboxExecutor(sandboxes_root=str(tmp_path / "sandboxes"))
        code, out, _ = executor.execute(["echo", "hello"], _level_3())
        assert code == 0
        assert "hello" in out
