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
# never the real session keyring (API keys otherwise go to Secret Service)
os.environ["SHANI_CHRONOA_KEYRING"] = "0"

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
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    # `$HOME/.config`, not a sibling directory. A user drop-in - a sense or a
    # skill - is looked up under `$XDG_CONFIG_HOME`, and so is the keyfile
    # store the consent gates are written to. Pointing the two at different
    # directories makes a test write its drop-in where the code will not look
    # and its consent grant where nothing reads it, and the failure looks like
    # the sense being broken rather than the fixture disagreeing with itself.
    xdg = home / ".config"
    xdg.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))


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


@pytest.fixture(scope="session", autouse=True)
def _schema_visible_from_the_first_test(compiled_schema_dir):
    """Make the repo schema resolvable before *any* test constructs a config.

    `Gio.SettingsSchemaSource.get_default()` memoises its answer for the life of
    the process, including the answer "no schemas found". So the first test that
    builds a `ChronoaConfig` without `gsettings_env` - which is most of them -
    resolves it with no `GSETTINGS_SCHEMA_DIR` set, caches the empty result, and
    every later `ChronoaConfig()` in the run silently falls back to hardcoded
    Python defaults. Measured: a lookup that returns None with no schema dir keeps
    returning None after the variable is set correctly.

    The damage is confined to whichever tests happen to run after the first
    config construction, which is why it is invisible in isolation and why it has
    survived: 7 tests in `test_config_gsettings.py` pass alone and fail in a full
    run on an unmodified tree (verified against a pristine `git archive HEAD`).

    This is a real fix rather than a workaround - the alternative is that a
    setting is readable in production and unreadable under test, which is the
    same class of lie this repo keeps recording. `GSETTINGS_BACKEND` is set here
    too, because without a keyfile backend the writes have nowhere to go and
    there is no session bus to fall back to.
    """
    os.environ["GSETTINGS_SCHEMA_DIR"] = str(compiled_schema_dir)
    os.environ["GSETTINGS_BACKEND"] = "keyfile"


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

@pytest.fixture(autouse=True)
def _isolate_trigger_rule_store(tmp_path_factory, monkeypatch):
    """Keep the armed-rule store out of the developer's real state dir.

    `triggers.RULES_FILE` is a module-level constant resolved from `$HOME` at
    *import* time, so a per-test `monkeypatch.setenv("HOME", tmp_path)` lands
    too late to move it - the path was captured when `triggers.py` was first
    imported, pointing at the real home. The consequence was that running the
    suite wrote armed actuator rules (`doorbell` -> notify, `click` ->
    move_pointer) into `~/.local/share/shani-chronoa/triggers/rules.json`,
    where they would later fire unattended.

    This is the same class of bug `PerceptStore.DURABLE_FILE` already caused
    in this repo, so the guard is autouse and repo-wide rather than patched
    into the one test module that happened to trip it.
    """
    triggers = pytest.importorskip("shani_chronoa.triggers")
    monkeypatch.setattr(
        triggers, "RULES_FILE", tmp_path_factory.mktemp("rules") / "rules.json", raising=False
    )


@pytest.fixture(autouse=True)
def _isolate_timer_store(tmp_path_factory, monkeypatch):
    """Keep the countdown-timer store out of the developer's real state dir.

    `skills/timer.py` resolves `_DATA` at *import* time from `$XDG_STATE_HOME`,
    falling back to `~/.local/state`. Two reasons the per-test `HOME` above is
    not enough: the constant is captured before any fixture runs, and
    `XDG_STATE_HOME` is not set by any fixture at all.

    Observed here: a full suite run without the variable set wrote a real
    `~/.local/state/shani-chronoa/timers.json` holding fixture data - two
    `pasta` labels and the `'; touch /tmp/pytest-of-.../pwned; '` label from
    the shell-injection test. The timer tests are clean in isolation, so the
    write comes from a test that reaches `set_timer` through the real skill
    path; isolating only the timer test module would not have caught it.

    Same class as `_isolate_trigger_rule_store` above and
    `PerceptStore.DURABLE_FILE`, so this is autouse and repo-wide.
    """
    timer = pytest.importorskip("shani_chronoa.skills.timer")
    monkeypatch.setattr(
        timer, "_DATA",
        tmp_path_factory.mktemp("timers") / "shani-chronoa" / "timers.json",
        raising=False,
    )


@pytest.fixture(autouse=True)
def _isolate_percept_store(tmp_path_factory, monkeypatch):
    """Keep the percept store out of the developer's real data directory.

    `store.PERCEPT_DIR` is built from `os.path.expanduser("~/.local/share/...")`
    at *import* time, so it captures the real `$HOME` before any fixture runs,
    and it does not read `XDG_DATA_HOME` - verified: exporting that variable
    changes nothing here. `DURABLE_FILE` and `LIVE_FILE` are derived from it at
    import time too, and `PerceptStore.__init__` reads those globals, so the
    per-test `HOME` above cannot reach them either.

    The consequence is a live view written into a real user's
    `~/.local/share/shani-chronoa/percepts/live.json` on every `add()`, from
    any test that constructs `PerceptStore()` with no arguments -
    `test_vision_sense.py` and `test_sense_scheduler.py` both do. It happened
    twice: that file was rewritten at 01:19 and again at 01:57 by test runs,
    holding a snapshot whose content was a verbatim match for a vision fixture
    in `test_vision_sense.py`.

    Patching the module global is not a substitute for the per-instance rule
    this repo's own `test_percept_path_isolation.py` pins; it is the second
    layer, for the tests that legitimately pass no paths at all. Both are
    needed: the global stops `PerceptStore()` leaking, the instance rule stops
    `PerceptStore(durable_path=...)` leaking.

    Same class as `_isolate_trigger_rule_store` and `_isolate_timer_store`
    above, so this is autouse and repo-wide rather than added to the one test
    module that happened to trip it.
    """
    store = pytest.importorskip("shani_chronoa.senses.store")
    percept_dir = tmp_path_factory.mktemp("percepts")
    monkeypatch.setattr(store, "PERCEPT_DIR", percept_dir, raising=False)
    monkeypatch.setattr(store, "DURABLE_FILE", percept_dir / "memory.jsonl", raising=False)
    monkeypatch.setattr(store, "LIVE_FILE", percept_dir / "live.json", raising=False)


@pytest.fixture(autouse=True)
def _isolate_xdg_data_home(tmp_path_factory, monkeypatch):
    """Point every XDG data path at a temp dir, so the suite is self-consistent.

    Seven modules resolved their persisted paths from a hardcoded
    `~/.local/share` and have just been changed to honour `$XDG_DATA_HOME`. Two
    tests then failed, and both were the tests being wrong rather than the
    change: they set a fake `$HOME` and asserted against `<home>/.local/share`,
    which is only where the files landed while nothing consulted the variable.
    A developer running the suite with `XDG_DATA_HOME` set - the documented way
    to sandbox a run - got a red suite from correct code.

    Redirecting the variable is the honest repair rather than weakening either
    assertion. These constants are captured at *import*, before any fixture
    runs, so monkeypatching the module attribute is what actually moves them;
    setting the variable alone would arrive too late, which is the same trap
    `_isolate_trigger_rule_store` above documents.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path_factory.mktemp("xdg-data")))
    for module_name, attribute in (
        ("shani_chronoa.senses.store", "PERCEPT_DIR"),
        ("shani_chronoa.tool_tracking", "LOG_DIR"),
        ("shani_chronoa.argfile", "_ARGFILE_ROOT"),
        ("shani_chronoa.sessions", "SESSION_DIR"),
        ("shani_chronoa.skills.add_reminder", "_STORE"),
        ("shani_chronoa.skills.set_sleep_inhibit", "STATE_FILE"),
    ):
        module = pytest.importorskip(module_name)
        if hasattr(module, attribute):
            monkeypatch.setattr(
                module, attribute, tmp_path_factory.mktemp("xdg") / "state", raising=False
            )
