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
    ("shani_chronoa.sessions", "SESSION_DIR"),
    ("shani_chronoa.skills.add_reminder", "_STORE"),
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
