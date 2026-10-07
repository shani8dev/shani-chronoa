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
# never start a real whisper-server from a test
os.environ["SHANI_CHRONOA_STT_SERVER"] = "0"

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG_DIR = REPO_ROOT / "usr/lib/shani-chronoa"
SCHEMA_SRC = REPO_ROOT / "usr/share/glib-2.0/schemas"


def _compile_repo_schema() -> str:
    """Compile the repo gschema into a directory of its own.

    Import-time, deliberately, and not a fixture: `Gio.SettingsSchemaSource
    .get_default()` memoises its answer for the life of the process —
    *including the answer "no schemas found"* — and pytest imports test
    modules during collection, before any fixture runs. One of those imports
    (`test_skill_workbench`, which builds its own extended schema) resolves a
    schema, and if that happens while GSETTINGS_SCHEMA_DIR is unset the empty
    answer is cached for the whole run: every consent key then reads back as
    the caller's own default, so `get_bool("vision-sense-enabled", True)`
    answers True, 300 tests fail on a runner that has a perfectly good schema,
    and nothing in the log says "schema". Set here instead, at the earliest
    point pytest gives us, so there is no window in which the variable is
    unset.
    """
    import tempfile

    directory = Path(tempfile.mkdtemp(prefix="chronoa-schema-"))
    for xml in SCHEMA_SRC.glob("*.xml"):
        shutil.copy2(xml, directory)
    result = subprocess.run(["glib-compile-schemas", str(directory)],
                            capture_output=True, text=True, timeout=60)
    if result.returncode != 0 or not (directory / "gschemas.compiled").is_file():
        raise RuntimeError(
            f"glib-compile-schemas failed for the repo schema: {result.stderr}")
    return str(directory)


#: The compiled schema, built once and shared: the session fixture points at
#: the same directory, so nothing changes GSETTINGS_SCHEMA_DIR mid-run.
COMPILED_SCHEMA_DIR = _compile_repo_schema()
os.environ["GSETTINGS_SCHEMA_DIR"] = COMPILED_SCHEMA_DIR
os.environ.setdefault("GSETTINGS_BACKEND", "keyfile")

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
    # Data paths are resolved per call (triggers.triggers_dir, the screenshot
    # folder, the conversation store), so pointing XDG_DATA_HOME at the test's
    # own directory is what keeps every one of them out of the real home - a
    # developer shell that exports XDG_DATA_HOME must not reach the suite.
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
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
def compiled_schema_dir():
    """The repo gschema, compiled once at conftest import (see COMPILED_SCHEMA_DIR).

    A fixture that compiled its own copy would be strictly worse than the
    import-time build: it would not exist until after collection, which is the
    window this whole mechanism exists to close.
    """
    return Path(COMPILED_SCHEMA_DIR)


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
def _trigger_and_capture_paths_are_inside_the_test(tmp_path_factory):
    """Guard, not a redirect: every data path the trigger engine and the
    screenshot skill write to must resolve inside this test's own directory.

    The redirect this replaced (`triggers.RULES_FILE` rebound to a tmp file)
    covered one of six import-time paths; the others leaked into the real home
    (armed rules, a corrupt rules file, 178 fake screenshots). Those paths are
    now resolved per call, so the per-test HOME/XDG_DATA_HOME isolates them -
    and this fails the test that would otherwise write outside it.
    """
    triggers = pytest.importorskip("shani_chronoa.triggers")
    from shani_chronoa.skills import screenshot
    for path in (triggers.rules_file(), triggers.event_rules_file(), triggers.fingerprints_file(),
                 triggers.verdicts_dir(), triggers.deadlines_dir(), Path(screenshot.output_dir())):
        assert str(path).startswith(str(tmp_path_factory.getbasetemp())), \
            f"{path} is outside pytest's temporary directory - a test would write into a real home"
    yield


@pytest.fixture(autouse=True)
def _isolate_timer_store(tmp_path_factory, monkeypatch):
    """Keep the countdown-timer store out of the developer's real state dir.

    **The root cause is gone; the fixture stays.** `skills/timer.py` used to
    resolve `_DATA` at *import* time from `$XDG_STATE_HOME`, falling back to
    `~/.local/state`, which is why a per-test `HOME` could not contain it: the
    constant was captured before any fixture ran. It now resolves per call
    through `_data_path()`, and this fixture redirects that function - which is
    a *stronger* guarantee than patching a constant, because it also covers a
    call made after the fixture's own monkeypatch would have been undone.

    Why it was needed, and is still worth guarding: a full suite run without
    isolation wrote a real `~/.local/state/shani-chronoa/timers.json` holding
    fixture data - two `pasta` labels and the `'; touch .../pwned; '` label
    from the shell-injection test. The timer tests are clean in isolation, so
    the write came from a test reaching `set_timer` through the real skill
    path; isolating only the timer test module would not have caught it.

    Same class as `_isolate_trigger_rule_store` above and
    `PerceptStore.DURABLE_FILE`, so this is autouse and repo-wide.
    """
    timer = pytest.importorskip("shani_chronoa.skills.timer")
    store = tmp_path_factory.mktemp("timers") / "shani-chronoa" / "timers.json"
    monkeypatch.setattr(timer, "_data_path", lambda: store)


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
        ("shani_chronoa.conversation_store", "SESSION_DIR"),
        ("shani_chronoa.skills.reminders", "_STORE"),
        ("shani_chronoa.skills.set_sleep_inhibit", "STATE_FILE"),
    ):
        module = pytest.importorskip(module_name)
        if hasattr(module, attribute):
            monkeypatch.setattr(
                module, attribute, tmp_path_factory.mktemp("xdg") / "state", raising=False
            )


@pytest.fixture(autouse=True)
def _no_organ_may_stay_lit(request):
    """No test may leave an organ lit behind it.

    The body register is process-global, so a test that opens an activity and
    never closes it does not fail *itself* - it fails some later test that
    happened to look at the strip. That is how a microphone light left on for
    30 seconds by the audio code was finally found, 40 files away and with no
    hint about the cause.

    Checking after every test names the culprit instead. It has to be an
    autouse fixture rather than a `pytest_sessionfinish` hook: setting
    `session.exitstatus` there is overwritten by pytest immediately afterwards,
    and `pytest.exit(1)` from that hook is swallowed too - both were verified by
    a control that leaked on purpose and still produced exit code 0.
    """
    body = pytest.importorskip("shani_chronoa.body")
    # An explicit opt-out, for the tests whose subject *is* a lit organ. It has
    # to be marked rather than inferred, so that "this test leaves an organ open
    # on purpose" is written down where someone will read it.
    if request.node.get_closest_marker("holds_organs"):
        yield
        return
    before = set(body.body.busy_organs())
    yield
    # Pulses are exempt on purpose: they report something already finished and
    # expire by themselves, so there is nothing to leave behind. Only an
    # *activity* - something in progress - can be left hanging by mistake.
    after = [activity for activity in body.body.snapshot()
             if activity.organ not in before and not activity.pulse]
    if after:
        described = ", ".join(f"{a.organ} ({a.what!r})" for a in after)
        # pytest reports a teardown failure as ERROR rather than FAILED. That is
        # fine and is not worth working around: the node id in the message is the
        # test that leaked, which is the whole point of doing this per-test
        # instead of at the end of the session.
        pytest.fail(
            f"{request.node.nodeid} left {described} lit. An organ left lit means "
            "the indicator stays on after the thing it describes has stopped, "
            "which is worse than having no indicator at all. Open it with "
            "`body.use(...)` and close it with `body.done(...)` - in a finally, "
            "so a failure closes it too.")


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "holds_organs: this test leaves a body activity open on purpose - it is "
        "the subject of the test, not a leak")
