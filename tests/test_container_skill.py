"""The container skill: its gate, its refusals, and the two traps it shipped.

The two regression tests here are not speculative. Both defects existed in
the first working version of the skill and were caught by running it against a
real podman, not by reading it:

1. **The transient unit ran the wrong program.** The invocation was built as
   `systemd-run ... run --rm alpine`, so the unit tried to exec the bare word
   `run` and podman was never involved. `Failed to find executable run` is
   what came back, and `rm`/`stop`/`ps` were executed as *system* binaries
   while it lasted. The test pins the shape: the program the unit execs is
   `podman`, and the subcommand follows it.

2. **A detached run died when its client exited.** With `--pipe`, the invoking
   process holds the read end of the unit's stdout, and the container's
   `conmon` inherits the write end - so when `systemd-run` returns, conmon
   takes a SIGPIPE and dies. The container it started *keeps running*
   (`podman ps` shows it Up), which is what makes it quiet: only `stop` fails,
   with "conmon exited prematurely". A 2-second existence check cannot tell
   those apart from a healthy container, so the fix is asserted structurally:
   the detached path writes the unit's output to files instead of a pipe.
"""

import shutil
import subprocess

import pytest

from shani_chronoa.skills import container


def _recorded_run(argv, **kwargs):
    recorded.append(argv)
    return subprocess.CompletedProcess(argv, 0, "", "")


recorded: "list[list[str]]" = []


@pytest.fixture(autouse=True)
def _reset():
    recorded.clear()
    yield
    recorded.clear()


def test_gate_refuses_then_opens(tmp_path, monkeypatch) -> None:
    """A refusal a user cannot undo is a refusal nobody can pass.

    Through `execute_tool_outcome`, not a direct call to `_run`: a test that
    calls the function is not a test that the skill is reachable, and this
    repo has shipped three skills that were dead for exactly that reason.
    """
    from shani_chronoa import tools
    from shani_chronoa.config import ChronoaConfig

    monkey = pytest.MonkeyPatch()
    monkey.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkey.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkey.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    (tmp_path / "config").mkdir()
    try:
        # **Shut by this test.** `container-control-enabled` is in the
        # `everyday` preset and ships on, so the refusal below was never
        # reached - it was asserting against a run that happened instead.
        ChronoaConfig().set("container-control-enabled", "false")
        refused = tools.execute_tool_outcome("container", {"action": "list"}).text
        assert "Refusing" in refused, f"the gate is open by default: {refused!r}"
        assert "container-control-enabled" in refused, (
            f"the refusal must name the switch that opens it: {refused!r}"
        )

        config = ChronoaConfig()
        config.set("container-control-enabled", "true")
        assert config.get_bool("container-control-enabled", False) is True, (
            "the grant did not take, so everything after this would be vacuous"
        )

        after = tools.execute_tool_outcome("container", {"action": "list"}).text
        assert "Refusing to run containers" not in after, (
            f"the gate is still closed after it was granted: {after!r}"
        )
    finally:
        monkey.undo()


def test_the_unit_execs_podman_not_the_bare_subcommand(monkeypatch) -> None:
    """Regression: the first version built the unit command without the binary."""
    monkeypatch.setattr(subprocess, "run", _recorded_run)
    proc, why = container._podman(["ps", "--format", "{{.Names}}"])
    assert proc is not None, why
    assert recorded, "no command was run"
    unit_argv = recorded[-1]
    # The unit's program is podman, and the subcommand follows it.
    program = unit_argv[unit_argv.index("podman")]
    assert program == "podman"
    assert unit_argv[unit_argv.index("podman") + 1:] == ["ps", "--format", "{{.Names}}"]
    assert "--pipe" in unit_argv
    assert "--unit=chronoa-container-" in " ".join(unit_argv)


def test_a_detached_run_does_not_hold_a_pipe_open(monkeypatch) -> None:
    """Regression: `--pipe` killed conmon by SIGPIPE when the client exited."""
    monkeypatch.setattr(subprocess, "run", _recorded_run)
    container._podman(["run", "-d", "nginx"], keep_output=True)
    unit_argv = recorded[-1]
    assert "--pipe" not in unit_argv, (
        "a detached run must not hold a pipe: conmon inherits the write end "
        "and dies when systemd-run returns, leaving the container unmanageable"
    )
    assert any(a.startswith("--property=StandardOutput=file:") for a in unit_argv)
    assert any(a.startswith("--property=StandardError=file:") for a in unit_argv)


def test_output_files_are_read_back_and_removed(monkeypatch, tmp_path) -> None:
    """The file-capture path must return the unit's real output, not lose it."""
    def fake_run(argv, **kwargs):
        out_path = [a for a in argv if a.startswith("--property=StandardOutput=file:")][0].split("file:", 1)[1]
        err_path = [a for a in argv if a.startswith("--property=StandardError=file:")][0].split("file:", 1)[1]
        with open(out_path, "w") as h:
            h.write("the-container-id\n")
        with open(err_path, "w") as h:
            h.write("a warning\n")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    proc, why = container._podman(["run", "-d", "nginx"], keep_output=True)
    assert proc is not None, why
    assert proc.stdout.strip() == "the-container-id"
    assert proc.stderr.strip() == "a warning"


@pytest.mark.parametrize("args", [
    {"action": "frobnicate"},
    {"action": "run"},                                    # no image
    {"action": "run", "image": "-evil"},                  # leading dash
    {"action": "run", "image": "alpine", "name": "../etc"},  # bad name
    {"action": "run", "image": "alpine", "publish": ["8080"]},   # not host:container
    {"action": "exec", "name": "web"},                    # no command
    {"action": "logs"},                                   # no name
])
def test_bad_arguments_never_reach_podman(args) -> None:
    """Every refusal must cost nothing: no unit is started to produce it.

    The gate is granted first, or the skill's own consent refusal answers and
    this test silently passes about the wrong thing - a refusal wearing a
    validation's clothes.
    """
    from shani_chronoa.config import ChronoaConfig

    ChronoaConfig().set("container-control-enabled", "true")
    called = []
    original = container._podman
    container._podman = lambda *a, **k: called.append(a) or (None, "")
    try:
        text = container._run(args)
    finally:
        container._podman = original
    assert not called, f"{args} reached podman: {called}"
    assert text and "Refusing" not in text


def test_a_string_command_is_split_into_argv(monkeypatch) -> None:
    """A model writing "echo hello" means two arguments, not one filename.

    Wrapping the string whole would make podman exec a file literally named
    "echo hello", which fails as "executable file not found" and explains
    nothing. Splitting it is what the person asked for.
    """
    from shani_chronoa.config import ChronoaConfig

    ChronoaConfig().set("container-control-enabled", "true")
    monkeypatch.setattr(subprocess, "run", _recorded_run)
    container._run({"action": "run", "image": "alpine", "command": "echo hello world"})
    assert recorded, "no command was run"
    unit_argv = recorded[-1]
    tail = unit_argv[unit_argv.index("podman") + 1:]
    assert tail == ["run", "--rm", "alpine", "echo", "hello", "world"], tail


def test_missing_tools_are_named_not_swallowed(monkeypatch) -> None:
    """No podman is 'nothing is running'; the reason is the package."""
    monkeypatch.setattr(shutil, "which", lambda name: None if name in ("podman", "systemd-run") else "/usr/bin/" + name)
    proc, why = container._podman(["ps"])
    assert proc is None
    assert "not installed" in why or "provides it" in why, why


def test_the_display_bind_is_the_testbeds_mechanism(monkeypatch, tmp_path) -> None:
    """X11 means: the socket bound in, the variable named, the toolkits pinned.

    Copied from `shani-testbed` (`lib/nspawn.sh`'s `X11_BIND`) and from
    `shani-desktop-plasma`'s session test, which drive a private Xvfb the same
    way. The pins are the point: a container has no GPU, and a Wayland-native
    client cannot be driven by an X pointer, so GTK/Qt are told to use their X11
    backends with software rendering.
    """
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(container.os.path, "isdir", lambda p: p == "/tmp/.X11-unix")
    argv, env, refusal = container._display_setup("x11")
    assert refusal == "", refusal
    assert "/tmp/.X11-unix:/tmp/.X11-unix:ro" in " ".join(argv)
    assert "DISPLAY=:0" in env
    assert "GDK_BACKEND=x11" in env
    assert "QT_QPA_PLATFORM=xcb" in env
    assert "LIBGL_ALWAYS_SOFTWARE=1" in env


def test_no_display_means_a_reason_not_a_broken_run(monkeypatch) -> None:
    monkeypatch.setenv("DISPLAY", "")
    _argv, _env, refusal = container._display_setup("x11")
    assert "no DISPLAY" in refusal or "no X11 socket" in refusal, refusal

    monkeypatch.setattr(container.os.path, "isdir", lambda p: False)
    monkeypatch.setenv("DISPLAY", ":0")
    _argv, _env, refusal = container._display_setup("x11")
    assert "no X11 socket" in refusal, refusal


def test_a_wayland_bind_keeps_the_socket_at_its_own_path(monkeypatch, tmp_path) -> None:
    """A Wayland client connects to $XDG_RUNTIME_DIR/$WAYLAND_DISPLAY, so the
    runtime directory has to be bound whole - binding a renamed socket would
    give the container a path its client never asks for."""
    runtime = tmp_path / "rt"
    runtime.mkdir()
    (runtime / "wayland-9").touch()
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-9")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    argv, env, refusal = container._display_setup("wayland")
    assert refusal == "", refusal
    assert f"{runtime}:{runtime}:ro" in " ".join(argv), " ".join(argv)
    assert "WAYLAND_DISPLAY=wayland-9" in env


def test_the_display_is_bound_before_the_image(monkeypatch) -> None:
    """A GUI app in the container must find the socket when it starts."""
    from shani_chronoa.config import ChronoaConfig

    ChronoaConfig().set("container-control-enabled", "true")
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(container.os.path, "isdir", lambda p: p == "/tmp/.X11-unix")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: (
        recorded.append(argv) or subprocess.CompletedProcess(argv, 0, "not-found\n", "")))
    container._run({"action": "run", "image": "alpine", "display": "x11",
                    "command": ["xterm"]})
    unit_argv = recorded[-1]
    podman_at = unit_argv.index("podman")
    image_at = unit_argv.index("alpine")
    bind_at = next(i for i, a in enumerate(unit_argv) if a.startswith("/tmp/.X11-unix:"))
    assert bind_at < image_at, (
        f"the display must be bound before the image is named: {unit_argv}")
    assert "DISPLAY=:0" in unit_argv
    assert unit_argv[podman_at + 1] == "run"


def test_an_x_authorization_failure_names_the_command_that_fixes_it(monkeypatch) -> None:
    from shani_chronoa.config import ChronoaConfig

    ChronoaConfig().set("container-control-enabled", "true")
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(container.os.path, "isdir", lambda p: p == "/tmp/.X11-unix")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: (
        subprocess.CompletedProcess(argv, 1, "",
                                    "Authorization required, but no authorization protocol specified")))
    text = container._run({"action": "run", "image": "xeyes", "display": "x11"})
    assert "xhost +local:" in text, (
        f"the refusal should name the one command that fixes it: {text}")


def test_running_names_tell_empty_from_unknown() -> None:
    """An empty list is an answer; None is not. Conflating them hides a stop."""
    def fake(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, "one\ntwo\n", ""), ""

    original = container._podman
    try:
        container._podman = fake
        assert container._running_names() == ["one", "two"]

        def failing(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, "", "boom"), ""

        container._podman = failing
        assert container._running_names() is None

        def broken(argv, **kwargs):
            return None, "no unit"

        container._podman = broken
        assert container._running_names() is None

        def empty(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, "\n", ""), ""

        container._podman = empty
        assert container._running_names() == []
    finally:
        container._podman = original


def test_post_condition_reads_the_container_back() -> None:
    """run wants it running; stop and rm want it gone; unreadable is None."""
    original = container._running_names
    try:
        container._running_names = lambda **k: ["web"]
        assert container._post_condition({"action": "run", "name": "web"}) == (True, "web is running")
        assert container._post_condition({"action": "stop", "name": "web"}) == (False, "web is still running")
        assert container._post_condition({"action": "rm", "name": "web"}) == (False, "web is still there")

        container._running_names = lambda **k: []
        assert container._post_condition({"action": "run", "name": "web"}) == (False, "web is not running")
        assert container._post_condition({"action": "stop", "name": "web"}) == (True, "web is stopped")
        assert container._post_condition({"action": "rm", "name": "web"}) == (True, "web is removed")

        # The empty list was the success case for stop, so this distinction is
        # the whole fix: unreadable must be UNVERIFIED, not a verdict.
        container._running_names = lambda **k: None
        assert container._post_condition({"action": "stop", "name": "web"}) is None
        assert container._post_condition({"action": "rm", "name": "web"}) is None

        # A generated name cannot be re-read from here, and a bad name must
        # not be passed to podman.
        assert container._post_condition({"action": "run"}) is None
        assert container._post_condition({"action": "stop", "name": "../etc"}) is None
    finally:
        container._running_names = original


def test_machines_are_listed_without_permission_friction(monkeypatch) -> None:
    """`machinectl list` is a podman-family read; an empty answer is empty."""
    monkeypatch.setattr(
        container, "_machinectl",
        lambda argv, timeout=30: (subprocess.CompletedProcess(argv, 0, "", ""), ""))
    assert container._do_machines({}) == "No machines are registered on this machine."

    monkeypatch.setattr(
        container, "_machinectl",
        lambda argv, timeout=30: (subprocess.CompletedProcess(
            argv, 0, "abc slot1 systemd-nspawn 192.168.1.5\n", ""), ""))
    text = container._do_machines({})
    assert "slot1" in text and "1 machine(s)" in text

    monkeypatch.setattr(
        container, "_machinectl",
        lambda argv, timeout=30: (subprocess.CompletedProcess(argv, 1, "", "polkit: denied"), ""))
    assert "unknown" in container._do_machines({})


_HAS_RUNTIMES = all(shutil.which(b) for b in ("podman", "systemd-run"))


@pytest.mark.skipif(not _HAS_RUNTIMES, reason="podman or systemd-run is not installed on this machine")
def test_a_real_container_runs_end_to_end(tmp_path, monkeypatch) -> None:
    """The one test that proves the skill does anything at all.

    Everything above is about refusals and wiring; this runs a real container
    through the real dispatch path and reads its real output back. It skips
    on a machine with no podman, and the guard can be True - this box has both
    binaries, which is the only reason it runs.
    """
    from shani_chronoa import tools
    from shani_chronoa.config import ChronoaConfig

    monkey = pytest.MonkeyPatch()
    monkey.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkey.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkey.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    (tmp_path / "config").mkdir()
    try:
        ChronoaConfig().set("container-control-enabled", "true")
        outcome = tools.execute_tool_outcome(
            "container",
            {"action": "run", "image": "alpine", "command": ["echo", "CONTAINER-ECHO-OK"]},
        )
        assert outcome.ran, "the skill never ran"
        assert "CONTAINER-ECHO-OK" in outcome.text, (
            f"the container ran but its output did not come back: {outcome.text!r}"
        )
    finally:
        monkey.undo()
