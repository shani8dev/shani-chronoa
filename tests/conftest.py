"""Hermetic pytest harness for shani-chronoa Phase 0 (failing-first RED tests).

Environment (recorded):
  - venv:            /tmp/chronoa-test-venv
                     (created with: python3 -m venv --system-site-packages /tmp/chronoa-test-venv)
  - deps:            /tmp/chronoa-test-venv/bin/pip install pytest httpx
  - run command:     cd /home/shrinivaskumbhar/Documents/shani/shani-chronoa && \\
                       PYTHONDONTWRITEBYTECODE=1 /tmp/chronoa-test-venv/bin/python \\
                       -m pytest tests/ -v -p no:cacheprovider
                     (-p no:cacheprovider keeps pytest from writing .pytest_cache into the repo)

Hermeticity guarantees:
  - GSETTINGS_BACKEND=keyfile + GSETTINGS_SCHEMA_DIR=<temp compiled schema> +
    isolated XDG_CONFIG_HOME per test -> no dconf, no user settings, no real gsettings store.
  - HOME is isolated per test.
  - PYTHONDONTWRITEBYTECODE=1 (also set in the run command) -> no __pycache__ written.
  - No real wpctl / notify-send / browser / network / mic / speaker / D-Bus:
    fake binaries on PATH, Gio.AppInfo.launch_default_for_uri mocked,
    httpx.AsyncClient replaced with a MockTransport.
  - usr/lib/shani-chronoa is inserted into sys.path so `import shani_chronoa`
    resolves to the repo package (never an installed copy).
"""

import os
import shutil
import subprocess
import sys
import textwrap
import types
from pathlib import Path

# Disable bytecode generation BEFORE any shani_chronoa import — pytest
# imports test modules (and their dependencies) during collection, which
# would otherwise write .pyc files into usr/lib/shani-chronoa/**/__pycache__/
# and make the packaging tests (test_no_pycache_in_packaged_payload,
# test_no_bytecode_files_in_packaged_payload) fail spuriously.
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG_DIR = REPO_ROOT / "usr/lib/shani-chronoa"
SCHEMA_SRC = REPO_ROOT / "usr/share/glib-2.0/schemas"

# Make the repo package importable. No `import shani_chronoa` at module level:
# imports happen inside fixtures/tests so PYTHONDONTWRITEBYTECODE=1 is already
# in effect and no bytecode is ever written into the repo tree.
sys.path.insert(0, str(PKG_DIR))


@pytest.fixture(autouse=True)
def _hermetic_env(tmp_path, monkeypatch):
    """Isolate every test from the real user environment."""
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    xdg = tmp_path / "xdg_config"
    xdg.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))


@pytest.fixture(scope="session")
def compiled_schema_dir(tmp_path_factory):
    """Compile a copy of the repo gschema into a temp dir (no system schema store)."""
    schema_dir = tmp_path_factory.mktemp("schemas")
    for xml in SCHEMA_SRC.glob("*.xml"):
        shutil.copy2(xml, schema_dir)
    result = subprocess.run(
        ["glib-compile-schemas", str(schema_dir)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"glib-compile-schemas failed: {result.stderr}"
    assert (schema_dir / "gschemas.compiled").is_file(), "gschemas.compiled not produced"
    return schema_dir


@pytest.fixture
def gsettings_env(compiled_schema_dir, monkeypatch):
    """Point gsettings at the temp compiled schema via the keyfile backend."""
    monkeypatch.setenv("GSETTINGS_BACKEND", "keyfile")
    monkeypatch.setenv("GSETTINGS_SCHEMA_DIR", str(compiled_schema_dir))


@pytest.fixture
def chronoa_config(gsettings_env):
    from shani_chronoa.config import ChronoaConfig
    return ChronoaConfig()


@pytest.fixture
def privacy_manager(chronoa_config):
    from shani_chronoa.config import PrivacyManager
    return PrivacyManager(chronoa_config)


@pytest.fixture
def fake_wpctl(tmp_path, monkeypatch):
    """A logging fake of wpctl on PATH; records every invocation."""
    wpctl_log = tmp_path / "wpctl.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "wpctl"
    script.write_text(textwrap.dedent(f"""\
        #!/bin/sh
        echo "$@" >> "{wpctl_log}"
        case "$1" in
            get-volume) echo "Volume: 0.50 [MUTED]";;
            *) exit 0;;
        esac
    """))
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")
    return wpctl_log


@pytest.fixture
def mock_launch_uri(monkeypatch):
    """Record calls to Gio.AppInfo.launch_default_for_uri instead of opening a browser."""
    calls: list = []

    def _fake(uri, context):
        calls.append(uri)
        return True

    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio
    monkeypatch.setattr(Gio.AppInfo, "launch_default_for_uri", _fake)
    return calls


@pytest.fixture
def mock_notify_send(tmp_path, monkeypatch):
    """A logging fake of notify-send on PATH; records every invocation."""
    send_log = tmp_path / "notify-send.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "notify-send"
    script.write_text(textwrap.dedent(f"""\
        #!/bin/sh
        echo "$@" >> "{send_log}"
    """))
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")
    return send_log


@pytest.fixture
def temp_user_skills_dir(tmp_path, monkeypatch):
    """Point the skills loader at an empty temp user-skills dir."""
    skills_dir = tmp_path / "user_skills"
    skills_dir.mkdir()
    import shani_chronoa.skills as skills_mod
    monkeypatch.setattr(skills_mod, "_USER_SKILLS_DIR", skills_dir)
    return skills_dir


@pytest.fixture
def mock_httpx(monkeypatch):
    """Replace httpx.AsyncClient with a MockTransport-backed recorder (no network)."""
    import httpx
    recorded: dict = {}

    def _handler(request):
        recorded["request"] = request
        recorded["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
        )

    transport = httpx.MockTransport(_handler)

    def _factory(**kwargs):
        return httpx.AsyncClient(**kwargs, transport=transport)

    monkeypatch.setattr("shani_chronoa.cloud_llm.httpx.AsyncClient", _factory)
    return recorded


@pytest.fixture
def stubbed_app(chronoa_config):
    """A ChronoaApplication with real config/privacy but no real hardware or GTK window."""
    from unittest.mock import MagicMock

    from shani_chronoa.app import ChronoaApplication
    from shani_chronoa.config import PrivacyManager

    app = ChronoaApplication.__new__(ChronoaApplication)
    app.config = chronoa_config
    app.hardware = MagicMock()
    app.hardware.get_model.return_value = "qwen3:4b"
    app.hardware.get_whisper_model.return_value = "base"
    app.hardware.get_context_window.return_value = 4096
    app.hardware.profile = "low"
    app.privacy = PrivacyManager(chronoa_config)
    app._model_override = None
    app.stt = MagicMock()
    app.llm = MagicMock()
    app.tts = MagicMock()
    app.assistant = MagicMock()
    app.window = None
    app._settings_window = None
    app.recorder = MagicMock()
    app.player = MagicMock()
    app.barge_in_monitor = MagicMock()
    app.wakeword = MagicMock()
    app._async = MagicMock()
    app._ollama_available = False
    app._listening = False
    app._wake_word_active = False
    app._sync_autostart = lambda: None
    return app