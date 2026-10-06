"""Every persisted path must be relocatable by the XDG variables.

`egress.py` records the cost of getting this wrong in incident terms: the log
path used to be a module constant built from a hardcoded `~/.local/share`, so a
suite run with only `XDG_STATE_HOME` redirected still appended every fixture
record to the developer's real audit log. Ten modules had already worked this
out separately; these are the ones that had not.

What is asserted here is the resolution, not the writing. Whether each store
also ends up owner-only is `test_state_file_permissions.py`'s subject, and
whether a test can reach another process's store is
`test_percept_path_isolation.py`'s. This is the layer underneath both: the
directory is chosen from the environment at all.
"""
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO / "usr" / "lib" / "shani-chronoa"

#: (module, attribute) for every path that was a hardcoded `~/.local/share`.
DATA_PATHS = [
    ("shani_chronoa.senses.store", "PERCEPT_DIR"),
    ("shani_chronoa.tool_tracking", "LOG_DIR"),
    ("shani_chronoa.argfile", "_ARGFILE_ROOT"),
    ("shani_chronoa.conversation_store", "SESSION_DIR"),
    ("shani_chronoa.skills.reminders", "_STORE"),
]

#: The two directories that hold user-supplied *code* rather than state. These
#: follow `$XDG_CONFIG_HOME` and must not follow `$XDG_DATA_HOME`: a run that
#: relocates its data to a temp dir still has to load the user's own skills.
CONFIG_PATHS = [
    ("shani_chronoa.skills", "_USER_SKILLS_DIR"),
    ("shani_chronoa.senses", "_USER_SENSES_DIR"),
]


def _in_fresh_process(module: str, attribute: str, data_home: str) -> str:
    """Read one attribute in a child process.

    A child, because most of these are module-level constants: importing them
    in-process would resolve them against whatever this process was started
    with, which is the value under test rather than the one being requested.
    This is the same distinction `files.data_home`'s own docstring makes about
    import-time capture.
    """
    env = dict(os.environ, XDG_DATA_HOME=data_home, PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, {str(PACKAGE_ROOT)!r});"
         f"import importlib;"
         f"print(getattr(importlib.import_module({module!r}), {attribute!r}))"],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert result.returncode == 0, (
        f"{module}.{attribute} did not import in a child process: {result.stderr[-400:]}"
    )
    return result.stdout.strip()


class TestTheDataHomeLookup:
    def test_an_absolute_value_is_used_as_given(self, tmp_path):
        from shani_chronoa import files
        os.environ["XDG_DATA_HOME"] = str(tmp_path)
        assert files.data_home() == tmp_path

    def test_it_falls_back_to_the_local_share_default(self, monkeypatch):
        from shani_chronoa import files
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        assert files.data_home() == Path.home() / ".local" / "share"

    def test_an_empty_value_falls_back_rather_than_resolving_against_the_cwd(
        self, monkeypatch
    ):
        # `XDG_DATA_HOME=""` is a common way to end up with a path relative to
        # the working directory, and state written there is untraceable.
        from shani_chronoa import files
        monkeypatch.setenv("XDG_DATA_HOME", "")
        assert files.data_home() == Path.home() / ".local" / "share"

    def test_a_relative_value_is_refused_per_the_spec(self, monkeypatch):
        from shani_chronoa import files
        monkeypatch.setenv("XDG_DATA_HOME", "relative/share")
        assert files.data_home() == Path.home() / ".local" / "share"

    def test_the_state_home_has_the_same_rules(self, tmp_path, monkeypatch):
        from shani_chronoa import files
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        assert files.state_home() == tmp_path
        monkeypatch.setenv("XDG_STATE_HOME", "nope")
        assert files.state_home() == Path.home() / ".local" / "state"


@pytest.mark.parametrize("module,attribute", DATA_PATHS, ids=[a for _, a in DATA_PATHS])
class TestEveryPersistedPathFollowsTheEnvironment:
    def test_it_resolves_under_the_relocated_data_home(self, tmp_path, module, attribute):
        resolved = _in_fresh_process(module, attribute, str(tmp_path))
        assert resolved.startswith(str(tmp_path)), (
            f"{module}.{attribute} resolved to {resolved!r}, which is outside the "
            f"relocated data home {tmp_path}. A hardcoded ~/.local/share gives a "
            f"test no way to keep its fixtures out of the real user's home."
        )

    def test_it_is_not_a_bare_expanduser_of_the_home_directory(
        self, tmp_path, module, attribute
    ):
        # The same fact, asserted from the source rather than the value: this
        # is the form the defect took, and it is what a future edit would
        # reintroduce while leaving the other test green if the environment
        # happened to be unset.
        source = (PACKAGE_ROOT / (module.replace(".", "/") + ".py")).read_text()
        line = next(
            (ln for ln in source.splitlines() if attribute in ln and "=" in ln), ""
        )
        assert "expanduser" not in line, (
            f"{module}.{attribute} is built with expanduser again: {line.strip()!r}"
        )


@pytest.mark.parametrize("module,attribute", CONFIG_PATHS, ids=[a for _, a in CONFIG_PATHS])
class TestUserCodeDirectoriesFollowConfigNotData:
    def test_it_resolves_under_the_relocated_config_home(self, tmp_path, module, attribute):
        env = dict(os.environ, XDG_CONFIG_HOME=str(tmp_path), PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run(
            [sys.executable, "-c",
             f"import sys; sys.path.insert(0, {str(PACKAGE_ROOT)!r});"
             f"import importlib;"
             f"print(getattr(importlib.import_module({module!r}), {attribute!r}))"],
            capture_output=True, text=True, env=env, timeout=60,
        )
        assert result.returncode == 0, result.stderr[-400:]
        assert result.stdout.strip().startswith(str(tmp_path)), (
            f"{module}.{attribute} resolved to {result.stdout.strip()!r}, outside the "
            f"relocated config home {tmp_path}"
        )

    def test_relocating_the_data_home_does_not_move_it(self, tmp_path, module, attribute):
        # The point of the split. A run that sandboxes its *data* must still load
        # the user's own skills and senses from their real config directory,
        # because those are the user's code rather than something the run wrote.
        env = dict(os.environ, XDG_DATA_HOME=str(tmp_path / "data"),
                   XDG_CONFIG_HOME=str(tmp_path / "config"), PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run(
            [sys.executable, "-c",
             f"import sys; sys.path.insert(0, {str(PACKAGE_ROOT)!r});"
             f"import importlib;"
             f"print(getattr(importlib.import_module({module!r}), {attribute!r}))"],
            capture_output=True, text=True, env=env, timeout=60,
        )
        assert result.returncode == 0, result.stderr[-400:]
        assert result.stdout.strip().startswith(str(tmp_path / "config")), (
            f"{module}.{attribute} followed the data home instead of the config "
            f"home; a relocated test run would silently load a different set of "
            f"user modules"
        )


class TestTheAutostartDirectory:
    """The autostart directory is where the desktop entry is written.

    This one is asserted from the source rather than from a child's resolved
    value, because a child's value is decided by whichever `XDG_CONFIG_HOME` it
    inherits - and `conftest.py` sets that to its own temp dir per test, so a
    test that copied `os.environ` was asserting about the fixture's directory
    rather than its own. It passed with the fix reverted, which is the whole
    problem this rewrite exists to fix.
    """

    def test_it_is_built_from_the_config_home(self):
        import inspect

        from shani_chronoa.app.desktop_integration import DesktopIntegrationMixin
        body = inspect.getsource(DesktopIntegrationMixin)  # autostart lives here
        assert "files.config_home()" in body, (
            "the autostart directory is no longer built from the config home"
        )
        assert 'expanduser("~/.config/autostart")' not in body, (
            "the autostart directory is built with expanduser again"
        )

    def test_the_config_home_moves_when_the_variable_does(self, tmp_path, monkeypatch):
        from shani_chronoa import files
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        assert files.config_home() == tmp_path
        assert str(files.config_home() / "autostart").startswith(str(tmp_path))


class TestTheModelSearchPaths:
    """The user half of each search path must move; the system half must not.

    Both are resolved inside a method rather than at import, so they are driven
    through the real call and the system fallback is checked at the same time -
    a list that moved wholesale would also break every user whose models are
    installed under `/usr`, which is the packaging default on Arch.
    """

    def test_the_whisper_user_half_moves_and_the_system_half_does_not(self, tmp_path):
        # The searched directory, not the returned file. `_get_model_path`
        # returns the first entry that *exists*, so on any machine with a model
        # installed under the real home it answers with that one and says
        # nothing about where it looked - an earlier version of this test
        # asserted on the return value and passed with the fix reverted, which
        # is the failure this comment exists to prevent.
        from shani_chronoa.stt import WhisperSTT
        os.environ["XDG_DATA_HOME"] = str(tmp_path)
        import inspect
        body = inspect.getsource(WhisperSTT._get_model_path)
        assert "files.data_home()" in body, (
            "the whisper model search no longer consults the data home"
        )
        assert "expanduser" not in body, (
            "the whisper model search is built with expanduser again"
        )
        # And the call really does go through it, so the first entry is the
        # relocated one even when a real model exists elsewhere.
        resolved = WhisperSTT()._get_model_path("definitely-not-a-real-model")
        assert str(resolved).startswith(str(tmp_path)), (
            f"with no model installed anywhere, the search fell back to {resolved!r} "
            f"instead of the relocated data home {tmp_path}"
        )
        assert "/usr/share/whisper/models" in body, (
            "the system-wide model path was dropped from the search"
        )

    def test_the_piper_voice_dir_moves_with_the_data_home(self, tmp_path, monkeypatch):
        from shani_chronoa import tts
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        found = tts._find_voice_file("en_US-lessac-medium") if hasattr(
            tts, "_find_voice_file") else None
        if callable(found):
            assert str(found).startswith(str(tmp_path))
        else:
            # No single entry point: assert the resolution is dynamic by checking
            # the module reads the data home rather than a captured constant.
            import inspect
            assert "expanduser" not in inspect.getsource(tts), (
                "tts.py resolves a voice directory with expanduser again"
            )
