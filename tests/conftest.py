"""Hermetic pytest harness for shani-chronoa Phase 0 (failing-first RED tests).

Environment (recorded):
  - venv:            /tmp/chronoa-test-venv
                     (created with: python3 -m venv --system-site-packages /tmp/chronoa-test-venv)
  - deps:            /tmp/chronoa-test-venv/bin/pip install pytest httpx
  - run command:     cd <repo root> && \\
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
import tempfile
import subprocess
import sys
import textwrap
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
    # **Under `build/`, not `/tmp` - and the reason is cross-process.**
    #
    # A `/tmp/chronoa-schema-*` directory is deleted by the reclaimer below, and
    # a liveness guard *inside this process* is not enough: two concurrent runs
    # each see only their own `GSETTINGS_SCHEMA_DIR`, so whichever finishes first
    # removes the other's live schema. Measured, and not as a theory - on
    # 2026-10-12 `COMPILED_SCHEMA_DIR` for a running session pointed at a
    # directory that had already been collected, so every consent key in that
    # whole session silently read its Python default.
    #
    # `build/` is gitignored, is per-checkout rather than per-machine, and is not
    # a directory the reclaimer scans. The pid in the name keeps two runs from
    # compiling into the same place.
    directory = REPO_ROOT / "build" / f"test-schema-{os.getpid()}"
    directory.mkdir(parents=True, exist_ok=True)
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


#: Prefixes this suite is allowed to clean up. **Deliberately a list, not a
#: sweep of everything new in /tmp** - the machine's /tmp belongs to whatever else
#: is running, and a fixture that deletes directories it did not create is a
#: fixture that deletes somebody else's work.
#: **`pytest-of-` is deliberately NOT here**, and that is the second version of
#: this list. The first one included it, and the reclaimer then deleted
#: `/tmp/pytest-of-<user>` at the end of whichever session finished first -
#: including the directory the *next* run was using. Measured:
#: `FileNotFoundError: /tmp/pytest-of-shrinivaskumbhar/pytest-0`, which showed up
#: as 101 errors in whichever file ran second and as a clean pass run alone.
#:
#: That is the whole order-dependence this file was meant to end up fixing, and
#: this fixture caused it. pytest already manages its own retention (three runs
#: by default), so the honest thing is to leave its directories to it and clean
#: only what this suite makes itself.
_OWNED_TMP_PREFIXES = (
    "chronoa-", "cwa", "cwm", "gs-", "kept-", "organism-doc-",
    "ties-", "three-", "sibling-merge-",
)


@pytest.fixture(scope="session", autouse=True)
def _reclaim_the_temp_dirs_this_suite_makes():
    """Delete what the suite made in /tmp, so the next run starts from the same disk.

    **Measured, repeatedly, and it is not a small thing.** A run of the suite
    left **12 GB** in /tmp - `/tmp/ties-*` and `/tmp/three-*` at 6 GB each - and
    once a 16 GB tmpfs fills, unrelated tests fail with
    `OSError: [Errno 122] Disk quota exceeded` while copying the schema, which
    has nothing to do with the cause. It happened three times in one session, and
    each time it looked like a code failure rather than a full filesystem.

    The cause is ordinary: **14 `tempfile.mkdtemp()` call sites are not inside a
    `with`**, so nothing removes them -
    `conftest.py:68`, `test_browser.py:143`, `test_every_gate_has_a_row.py:159`,
    `test_git_write.py:168`, `test_landlock.py:170` and `:176`,
    `test_oom_score.py:336`, `test_organism_doc.py:94`,
    `test_readme_numbers.py:59`, `test_sandbox_core_dumps.py:119` and `:122`,
    `test_skill_workbench.py:92`, `test_voice_loop_live.py:107`,
    `test_windows_gnome_live.py:64`.

    Fixing fourteen call sites one at a time would leave the next one to be
    written the same way, and this suite is not short-lived. So this removes what
    was made, at the end of the session, and only what this suite made.

    **And it must never remove what THIS run is still using.** That is not
    hypothetical: the schema directory is built at conftest import with
    `prefix="chronoa-schema-"`, and `"chronoa-"` is the first entry in
    `_OWNED_TMP_PREFIXES` - so this reclaimer deleted the live compiled schema at
    the end of whichever session finished first. Measured on 2026-10-12, and the
    signature is the nastiest kind: **every consent key then silently reads its
    Python default**, because `ChronoaConfig` logs one warning
    ("GSettings schema not found - using Python defaults"), writes nothing, and
    reads False. A run with no schema at all reported **48 teardown errors and
    34 failures** and named nothing about the schema.

    Removing the prefix from the list would fix it and leak every `chronoa-schema-*`
    a dead run left behind, which is the reclaimer's actual purpose. **The guard is
    on liveness rather than on the name**: a directory this session's own
    environment still points at is never collected, whatever it is called. That
    is also the general form - the reclaimer is allowed to remove what this suite
    made, and what this suite is *still using* is not that.
    """
    import shutil

    yield

    in_use = set()
    for var in ("GSETTINGS_SCHEMA_DIR", "XDG_DATA_HOME", "XDG_STATE_HOME",
                "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR", "XDG_CACHE_HOME"):
        value = os.environ.get(var)
        if value:
            in_use.add(os.path.realpath(value))

    removed = 0
    kept = 0
    for entry in Path(tempfile.gettempdir()).iterdir():
        if not entry.is_dir() or not entry.name.startswith(_OWNED_TMP_PREFIXES):
            continue
        try:
            if os.path.realpath(entry) in in_use:
                kept += 1
                continue
            shutil.rmtree(entry, ignore_errors=True)
            removed += 1
        except OSError:
            pass
    if removed:
        print(f"\n[conftest] reclaimed {removed} temp directories from /tmp")
    if kept:
        print(f"[conftest] kept {kept} still in use by this run")


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
    """Record calls to Gio.AppInfo.launch_default_for_uri* instead of opening a browser.

    **Both forms, and the async one is why this note exists.** `gui/blocks.py`
    opens a clicked reply link with `launch_default_for_uri_async`, so the
    synchronous call this fixture used to patch left the real path uncovered: a
    test that emitted `activate-link` on a real label ran the real async
    launcher and **opened a browser on the person's desktop**, which happened
    during the 2026-10-11 link work. The async form takes a completion callback
    rather than returning, so the fake records the URI and never calls back -
    which is honest, because "it was asked for" is what a test can observe.
    """
    calls: list = []

    def _fake(uri, context):
        calls.append(uri)
        return True

    def _fake_async(uri, context=None, cancellable=None, callback=None, user_data=None):
        calls.append(uri)

    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio
    monkeypatch.setattr(Gio.AppInfo, "launch_default_for_uri", _fake)
    monkeypatch.setattr(Gio.AppInfo, "launch_default_for_uri_async", _fake_async)
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


#: Set by `tools/mutate.py` on the pytest *it* spawns. Absent means this is a
#: person's run.
MUTATION_MARKER = "CHRONOA_MUTATION_RUN"

#: Written by `tools/mutate.py` for as long as a run holds the tree, containing
#: the owner's pid. `build/` is gitignored, so this never lands in a commit.
RUN_LOCK = Path(__file__).resolve().parent.parent / "build" / "mutate" / "run.lock"


def _mutation_in_flight() -> "str | None":
    """Why this run must not read the tree, or ``None`` when it may.

    **Measured, 2026-10-12**: a mutation run rewrites each source file in place,
    restores it in a ``finally``, and holds ``run.lock`` for the duration so two
    *runs* cannot overwrite each other's restore. Nothing stopped a plain
    ``pytest`` being started beside it - which reads the mutated files and
    reports **confident failures on code that is correct**: three of them, on a
    suite that had just been green. The lock made the tree safe for its owner and
    said nothing to a reader.

    So a stale lock is not a refusal - the runner takes those over and says so -
    and the runner's own pytest is exempt through ``MUTATION_MARKER``. This
    duplicates the runner's pid-liveness check rather than importing it:
    ``tools/mutate.py`` is a script with module-level work in it, and importing
    it from ``conftest`` would run that in every session. The format being read is
    one line holding a pid, so two readers of it is a fair trade.
    """
    import os

    if os.environ.get(MUTATION_MARKER):
        return None
    try:
        owner = RUN_LOCK.read_text(encoding="utf-8").split()[0]
        pid = int(owner)
    except (OSError, IndexError, ValueError):
        return None                      # no lock, or unreadable: not a hold
    try:
        os.kill(pid, 0)
    except OSError:
        return None                      # stale - the runner takes these over
    return (
        f"a mutation run (pid {pid}) is rewriting the source tree right now, so "
        f"this suite would read mutated code and report failures on code that "
        f"is correct. Wait for it, or stop it by that pid. If you are that run, "
        f"{MUTATION_MARKER} is set and this check does not apply.")


def pytest_configure(config):
    held = _mutation_in_flight()
    if held is not None:
        # **`pytest.exit`, not a raise.** A raise here is an INTERNALERROR, which
        # reads as a broken checkout rather than as "wait for the other run".
        pytest.exit(held, returncode=3)
    config.addinivalue_line(
        "markers",
        "holds_organs: this test leaves a body activity open on purpose - it is "
        "the subject of the test, not a leak")
    config.addinivalue_line(
        "markers",
        "slow: takes minutes, not seconds, and is excluded from the default run "
        "(`pytest -m slow` to include it). The mutation harness's full "
        "inventory is what needed this: under two minutes with four sets, over "
        "ten with eleven, and a test that quietly outgrew its own timeout "
        "reports a failure about something else entirely.")


@pytest.fixture(autouse=True)
def _no_question_presenter_outlives_its_test():
    """Remove any `ask_bridge` presenter a test installed, once it is done.

    Building a `ChronoaApplication` (or anything else that registers one)
    installs a presenter, and nothing ever removed it. Every later test that
    relied on there being nobody to ask then got "the question went
    unanswered" or "the user did not allow ...", depending on order - three
    tests failed only inside the full suite (2026-10-08). A test that needs a
    presenter sets one itself.
    """
    yield
    from shani_chronoa import ask_bridge
    ask_bridge._presenter = None
    ask_bridge._text_presenter = None


#: Test files whose subject is a real, allocated GTK window. Read from the
#: source rather than a marker because these files predate any marker, and a
#: test that quietly stops being in this list stops being guarded.
_GUI_SUBJECTS = ("ChronoaWindow", "SettingsWindow", "HelpWindow", "SetupWizard",
                 "PhonePanel", "Gtk.Window(", "surface.build(")


def _has_display_backend() -> bool:
    """Whether this machine can allocate a GTK window at all.

    **`Gtk.init_check()` is not the question.** It returns True with no display
    and every widget built afterwards is unallocated - `Adw.init()` warns
    `invalid (NULL) pointer instance`, icon-theme lookups critically fail, and
    `Gtk.ApplicationWindow.__init__` raises *"Gtk couldn't be initialized"*. So
    this asks Gdk for a display, which is the thing the assertions need.

    Probed once per session: `Gtk.init_check()` has to run in the process that
    will build the window, and doing it in the pytest process leaves GTK
    initialised there, which is a side effect an autouse fixture should not have.
    """
    for key in ("DISPLAY", "WAYLAND_DISPLAY", "BROADWAY_DISPLAY"):
        if os.environ.get(key, "").strip():
            return True
    return False


def _file_needs_a_display(path) -> bool:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return False
    return any(subject in text for subject in _GUI_SUBJECTS)


@pytest.fixture(autouse=True)
def _a_test_about_a_window_needs_a_window(request):
    """Skip a window's test honestly when there is no window to build.

    **Function-scoped, and that is a real limitation**: a *module*-scoped
    fixture is set up before this one runs, so a file whose harness fixture is
    module-scoped (`test_help_gate_labels_match_settings.py`) raises there and
    pytest reports an ERROR at setup rather than a skip - which is the same
    failure as before, wearing a different word. The module-scope half is
    `_skip_window_files_without_a_display`, a `pytest_ignore_collect` hook
    below, which is the only place that can run early enough for those.

    **Nine failures of `test_window_input_and_copy.py`, twenty-eight of
    `test_tool_activity_panel.py`, and three errors of
    `test_help_gate_labels_match_settings.py` were all this, and all of them
    pre-existing** - verified by checking out the senses directory from
    `9817575`, this session's first commit, where the same counts fail. Each of
    those files runs its window in a subprocess, the subprocess could not open a
    display, it printed no result, and the fixture handed back an empty dict. So
    every assertion failed on a **key that had never been written**: `KeyError:
    'send_visible'` for a send button that works, on every display-capable
    machine, 9 of 9.

    That is an absence presented as a failure, which is the same defect as an
    absence presented as a pass - and this repository has been bitten by both.
    The failure text is the part that costs: it names the product, not the
    machine.

    **It is a skip and not a pass, and it is not a blanket skip**: only files
    whose subject is a real window, and only when no display backend exists.
    Run against a display that can allocate one, every one of those files passes
    (`test_window_input_and_copy.py` is 9 of 9 on X11), so this cannot be
    hiding a product fault behind a green run - which is the control that
    `tests/test_a_harness_that_cannot_run_says_so.py` holds on the other side.
    """
    if _has_display_backend():
        return
    path = Path(str(request.node.fspath))
    if _file_needs_a_display(path):
        pytest.skip("no display backend on this machine, so the window this "
                    "file is about cannot be built; the assertions are about a "
                    "real allocated window and there is nothing to assert")


def pytest_ignore_collect(collection_path, config):
    """Do not collect a window's tests at all when no window can be built.

    **The module-scope half of the guard above, and it exists because a
    function-scoped fixture is too late.** A module-scoped harness fixture -
    `test_help_gate_labels_match_settings.py::row_titles` is one - is set up
    before any function-scoped fixture runs, so it raised
    `RuntimeError: Gtk couldn't be initialized` and pytest reported
    `ERROR at setup of test_...`: three errors where a skip belongs, and the
    same absence still reported as a failure.

    Refusing to collect is the honest shape for a file that cannot run: there is
    no test to skip, because no test exists on this machine. The reason string
    is not swallowed - pytest prints it against the file - so a reader sees
    *why* the file contributed nothing rather than finding it absent.

    **It cannot hide a product fault**: it only ever fires when there is no
    display backend at all, and every file it drops passes in full where one
    exists. `tests/test_a_harness_that_cannot_run_says_so.py` holds that
    control from the other side, and `test_window_input_and_copy.py` is 9 of 9
    on X11.
    """
    if _has_display_backend():
        return False
    try:
        return _file_needs_a_display(collection_path)
    except (OSError, TypeError):
        return False
