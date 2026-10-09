"""Replacing a file's contents wore no gate, while editing one did.

`write_text_file` is ungated, and that is right for most of what it does:
creating a file the machine does not have is the documented precedent
`convert_document` and `audio_output` follow. But it also takes an
`overwrite` argument, and that branch **replaces what a file already
says** - the same act `edit_file` exists for, which needs
`file-edit-enabled` and asks first every time.

So the narrow skill was gated and the general one was not: "change one
line in this file" needed a switch, and "replace this file wholesale"
needed nothing. The schema invites exactly that argument, because
`overwrite` reads as a detail of the same request rather than as a
different permission.

**Only that branch is gated, deliberately.** The key means "let Chronoa
edit your files", and somebody who wants new files written but no
existing file changed should have precisely that - so a first write and
an append are untouched, and both are asserted.

The control matters more than the refusal: this file has twice shipped a
switch that could never be turned on (`calendar_write`, `fm_radio`), each
a permanent refusal wearing the clothes of a permission. The gate is
therefore asserted to *open*, through the same keyfile backend the
sandboxed child reads - `GSETTINGS_BACKEND=memory` is per-process and a
grant made in the test would be invisible to the skill.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import write_text_file as W  # noqa: E402


def _compile_schemas(directory: Path) -> None:
    source = _REPO / "usr" / "share" / "glib-2.0" / "schemas"
    shutil.copytree(source, directory, dirs_exist_ok=True)
    subprocess.run(["glib-compile-schemas", str(directory)], check=True)


def _keyfile(backend: Path, **settings) -> None:
    """A gsettings keyfile whose group is the *schema id*, not the id's path."""
    settings_dir = backend / "glib-2.0" / "settings"
    settings_dir.mkdir(parents=True, exist_ok=True)
    lines = "\n".join(f"{k}={'true' if v else 'false'}" for k, v in settings.items())
    (settings_dir / "keyfile").write_text(f"[org.shani.chronoa]\n{lines}\n")


@pytest.fixture
def editing_granted(tmp_path, monkeypatch):
    """`file-edit-enabled` genuinely on, read through a real keyfile backend."""
    backend = tmp_path / "gsettings"
    _compile_schemas(backend)
    _keyfile(backend, **{"file-edit-enabled": True})
    monkeypatch.setenv("GSETTINGS_BACKEND", "keyfile")
    monkeypatch.setenv("GSETTINGS_SCHEMA_DIR", str(backend))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(backend))
    from shani_chronoa.config import ChronoaConfig
    assert ChronoaConfig().get_bool("file-edit-enabled", False) is True, (
        "the control did not take, so every refusal below would pass anyway")


@pytest.fixture
def editing_off(tmp_path, monkeypatch):
    backend = tmp_path / "gsettings"
    _compile_schemas(backend)
    _keyfile(backend, **{"file-edit-enabled": False})
    monkeypatch.setenv("GSETTINGS_BACKEND", "keyfile")
    monkeypatch.setenv("GSETTINGS_SCHEMA_DIR", str(backend))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(backend))
    from shani_chronoa.config import ChronoaConfig
    assert ChronoaConfig().get_bool("file-edit-enabled", False) is False


@pytest.fixture
def target(tmp_path, monkeypatch):
    """An existing file with content in it, under the test's home."""
    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / "existing.txt"
    path.write_text("precious content")
    return path


class TestReplacingAFileIsAnEdit:
    def test_it_refuses_and_names_the_switch(self, editing_off, target):
        out = W._run({"path": str(target), "content": "gone", "overwrite": True})
        assert "file-edit-enabled" in out, (
            f"the refusal does not name the switch, so it cannot be acted on: {out!r}")
        assert "Refusing" in out or "refusing" in out

    def test_nothing_was_written(self, editing_off, target):
        """The refusal is only worth having if the bytes are still there."""
        W._run({"path": str(target), "content": "gone", "overwrite": True})
        assert target.read_text() == "precious content", (
            "the content was destroyed by a refused overwrite")

    def test_the_gate_opens_when_the_switch_is_on(self, editing_granted, target):
        """The control that stops this being a permanent refusal.

        Two switches in this repository have shipped that could never be
        turned on, and both read exactly like a working permission.
        """
        out = W._run({"path": str(target), "content": "replaced", "overwrite": True})
        assert "Refusing" not in out and "refusing" not in out, (
            f"file-edit-enabled is on and it still refused: {out!r}")
        assert target.read_text() == "replaced"


class TestTheNonDestructiveBranchesStillNeedNothing:
    """Gating the whole skill would be over-broad, and would be wrong.

    The key means "let Chronoa edit your files". Somebody who wants files
    created but never changed should be able to say yes to that and still
    have the assistant hand them a text file.
    """

    def test_writing_a_new_file_needs_no_permission(self, editing_off, tmp_path):
        fresh = tmp_path / "fresh.txt"
        out = W._run({"path": str(fresh), "content": "hello"})
        assert "Refusing" not in out and "refusing" not in out, (
            f"creating a new file was refused: {out!r}")
        assert fresh.read_text() == "hello"

    def test_appending_needs_no_permission(self, editing_off, target):
        """Append destroys nothing, so it is not the act `edit_file` gates."""
        out = W._run({"path": str(target), "content": " and more", "append": True})
        assert "Refusing" not in out and "refusing" not in out, (
            f"appending was refused: {out!r}")
        assert "precious content" in target.read_text()

    def test_its_own_overwrite_refusals_still_come_first(self, editing_off, target):
        """An ungated refusal must not start needing a switch to explain itself.

        The file already holds content and the caller did not ask to replace
        it, so the answer is about the content - which is the more useful one,
        and available to someone who has file editing switched off.
        """
        out = W._run({"path": str(target), "content": "gone"})
        assert "would be" in out and "lost" in out, (
            f"the plain content warning changed: {out!r}")
        assert "file-edit-enabled" not in out, (
            "a refusal that needs no switch started naming one")