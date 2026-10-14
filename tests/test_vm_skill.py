"""The VM skill: its gate, its refusals, and the two traps it shipped.

Both defects here existed in the first working version and were caught by
driving a real QEMU through the real dispatch path, not by reading it:

1. **A comma-separated combo was sent as one key.** The handler split on `+`
   only, while the schema's own example read `ctrl-alt-f2`, so the model's
   combo arrived as a single QMP key name and QEMU answered *"Parameter 'data'
   does not accept value 'ctrl-alt-f2'"*. Both spellings split now, and the
   test pins that the events are three key-downs and three key-ups, not one
   event with a hyphenated name.

2. **The pointer could not move.** The launch attached `virtio-mouse-pci`,
   which is a *relative* device, and QMP's `input-send-event` with an `abs`
   axis is refused with *"Input handler not found for event type abs"* on
   anything without an absolute device. The launch attaches a tablet now; the
   test pins the device rather than trusting the description to remember.
"""

import shutil
import subprocess

import pytest

from shani_chronoa.skills import vm

recorded: "list[list[str]]" = []


def _grant(monkeypatch) -> None:
    """Open the gate for a test that then exercises the handlers.

    Without this the skill's own consent refusal answers first and the test
    silently asserts about the wrong thing - a refusal wearing a validation's
    clothes.
    """
    from shani_chronoa.config import ChronoaConfig

    ChronoaConfig().set("vm-control-enabled", "true")


@pytest.fixture(autouse=True)
def _reset():
    recorded.clear()
    yield
    recorded.clear()


class _FakeMachine:
    """A QMP socket double: records the rpc, answers what the test says."""

    def __init__(self, answers=None, error=None):
        self.answers = answers or {}
        self.error = error
        self.calls = []

    def rpc(self, execute, arguments=None):
        self.calls.append((execute, arguments))
        if self.error and execute in self.error:
            return {"error": {"desc": self.error[execute]}}
        return {"return": self.answers.get(execute, {})}

    def close(self):
        pass


def test_gate_refuses_then_opens(tmp_path, monkeypatch) -> None:
    """Through `execute_tool_outcome`: a test that calls the function is not
    a test that the skill is reachable, and this repo has shipped three skills
    that were dead for exactly that reason."""
    from shani_chronoa import tools
    from shani_chronoa.config import ChronoaConfig

    monkey = pytest.MonkeyPatch()
    monkey.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkey.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkey.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    (tmp_path / "config").mkdir()
    try:
        # **The shut half is established here, not inherited.** This used to rely
        # on `vm-control-enabled` shipping `false`. `cf525453` made a fresh
        # install arrive as the `everyday` preset, which enables it, so a default
        # install had the gate open and the failure read
        # *"the gate is open by default: 'No virtual machines are running...'"* -
        # the gate working correctly and the expectation being the stale thing.
        # Set first, and read back, so the refusal below cannot be vacuous.
        config = ChronoaConfig()
        config.set("vm-control-enabled", "false")
        assert config.get_bool("vm-control-enabled", True) is False, (
            "the shut half did not take, so the refusal below proves nothing")

        refused = tools.execute_tool_outcome("vm", {"action": "list"}).text
        assert "Refusing" in refused, f"the gate did not shut: {refused!r}"
        # **Either spelling.** `04316cc` made refusals name the Settings row
        # rather than the raw key; what this has to catch is a refusal that
        # names *neither*, leaving nobody able to find the switch.
        from shani_chronoa.capabilities import gate_label

        assert ("vm-control-enabled" in refused
                or gate_label("vm-control-enabled") in refused), (
                f"the refusal must name the switch that opens it, or the row "
                f"that opens it: {refused!r}")

        config.set("vm-control-enabled", "true")
        assert config.get_bool("vm-control-enabled", False) is True, (
            "the grant did not take, so everything after this would be vacuous")

        after = tools.execute_tool_outcome("vm", {"action": "list"}).text
        assert "Refusing to run virtual machines" not in after, (
            f"the gate is still closed after it was granted: {after!r}")
    finally:
        monkey.undo()


def test_a_hyphen_combo_is_several_keys_not_one(monkeypatch) -> None:
    """Regression: `ctrl-alt-f2` was sent as a single QMP key name."""
    _grant(monkeypatch)
    machine = _FakeMachine()
    monkeypatch.setattr(vm, "_connect", lambda name, timeout=20: (machine, ""))
    text = vm._run({"action": "key", "name": "probe", "keys": "ctrl-alt-f2"})
    assert "Sent ctrl-alt-f2" in text
    execute, arguments = machine.calls[-1]
    assert execute == "input-send-event"
    events = arguments["events"]
    downs = [e["data"]["key"]["data"] for e in events if e["data"]["down"]]
    ups = [e["data"]["key"]["data"] for e in events if not e["data"]["down"]]
    assert downs == ["ctrl", "alt", "f2"], downs
    # Every key that goes down comes back up, in reverse, or the guest holds
    # ctrl forever.
    assert ups == ["f2", "alt", "ctrl"], ups


def test_the_launch_attaches_an_absolute_pointer(monkeypatch) -> None:
    """Regression: a relative mouse is refused by QMP's abs axis."""
    _grant(monkeypatch)
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: (
        recorded.append(argv) or subprocess.CompletedProcess(argv, 0, "not-found\n", "")))
    vm._run({"action": "launch", "name": "probe", "memory_mb": 256, "display": "none"})
    argv = recorded[-1]
    joined = " ".join(argv)
    assert "virtio-tablet-pci" in joined, (
        f"the pointer must be an absolute device; abs input-send-event needs "
        f"one: {joined}")
    assert "virtio-keyboard-pci" in joined
    assert "virtio-mouse-pci" not in joined


def test_the_launch_unit_is_no_block_and_holds_the_socket(monkeypatch) -> None:
    """A VM must outlive the call that started it."""
    _grant(monkeypatch)
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: (
        recorded.append(argv) or subprocess.CompletedProcess(argv, 0, "not-found\n", "")))
    vm._run({"action": "launch", "name": "probe", "memory_mb": 256})
    argv = recorded[-1]
    assert argv[argv.index("systemd-run") + 1:argv.index("systemd-run") + 4] == [
        "--user", "--no-block", "--quiet"], argv


def test_uefi_asks_for_the_firmware_that_exists(monkeypatch) -> None:
    """`firmware: uefi` must attach a real OVMF image, not a hoped-for path."""
    _grant(monkeypatch)
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: (
        recorded.append(argv) or subprocess.CompletedProcess(argv, 0, "not-found\n", "")))
    vm._run({"action": "launch", "name": "probe", "memory_mb": 256,
             "firmware": "uefi", "display": "none"})
    argv = recorded[-1]
    joined = " ".join(argv)
    if vm._ovmf_path():
        assert f"file={vm._ovmf_path()}" in joined, (
            "UEFI was asked for and an OVMF image is installed, so the launch "
            f"must use it: {joined}")
    assert "if=pflash" in joined


def test_a_missing_firmware_is_refused_not_faked(monkeypatch) -> None:
    """No OVMF is 'cannot boot UEFI here', never a launch that then fails."""
    _grant(monkeypatch)
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: (
        recorded.append(argv) or subprocess.CompletedProcess(argv, 0, "not-found\n", "")))
    monkeypatch.setattr(vm, "_ovmf_path", lambda: "")
    text = vm._run({"action": "launch", "name": "probe", "memory_mb": 256, "firmware": "uefi"})
    assert "OVMF" in text and "would not boot" in text
    # The claim is that no *machine* was started; the launch path legitimately
    # asks systemd whether the name is free first.
    assert not any("qemu-system-x86_64" in " ".join(a) for a in recorded), (
        f"nothing was started for a boot that cannot happen: {recorded}")


def test_an_unanswerable_machine_names_the_tpm_wall(monkeypatch) -> None:
    """A TPM-equipped QEMU that never answers is said to be the TPM's fault."""
    _grant(monkeypatch)
    # A name of its own: the live test drives a machine called "probe", and a
    # leftover socket from it would make the *unstubbed* socket wait below
    # connect to that live machine instead of timing out.
    monkeypatch.setattr(vm, "_connect", lambda name, timeout=20: (None, "no answer"))
    monkeypatch.setattr(vm, "_launch", lambda argv: (
        subprocess.CompletedProcess(argv, 0, "", ""), ""))
    monkeypatch.setattr(vm, "_systemd", lambda argv, timeout=30: (
        subprocess.CompletedProcess(argv, 0, "not-found\n", ""), ""))
    monkeypatch.setattr(vm, "_start_tpm", lambda name: "")
    text = vm._run({"action": "launch", "name": "tpmwall", "memory_mb": 256,
                    "firmware": "bios", "tpm": True})
    assert "never answered" in text
    assert "TPM" in text, "the measured wall must be named, not folded into a guess"


@pytest.mark.parametrize("args", [
    {"action": "launch"},                                              # no name
    {"action": "launch", "name": "../etc"},                            # bad name
    {"action": "launch", "name": "p", "memory_mb": 999999},            # more than half RAM
    {"action": "launch", "name": "p", "memory_mb": "abc"},             # not a number
    {"action": "launch", "name": "p", "display": "crt"},               # not a display
    {"action": "launch", "name": "p", "iso": "/nope.iso"},             # missing file
    {"action": "key", "name": "probe"},                                # no keys
    {"action": "type", "name": "probe"},                               # no text
    {"action": "click", "name": "probe", "x": 50},                     # no y
    {"action": "click", "name": "probe", "x": 140, "y": 50},           # over 100
    {"action": "click", "name": "probe", "x": 50, "y": 50, "button": "side"},
    {"action": "screen", "name": "nosuch"},                            # nothing starts
    {"action": "status", "name": "nosuch"},
])
def test_bad_arguments_never_start_a_machine(args, monkeypatch) -> None:
    _grant(monkeypatch)
    called = []
    original = vm._launch
    vm._launch = lambda *a, **k: called.append(a) or (None, "")
    try:
        text = vm._run(args)
    finally:
        vm._launch = original
    assert not called, f"{args} started a unit: {called}"
    assert text and "Refusing" not in text


def test_a_qmp_refusal_is_reported_not_swallowed(monkeypatch) -> None:
    _grant(monkeypatch)
    machine = _FakeMachine(error={"screendump": "the guest is not running"})
    monkeypatch.setattr(vm, "_connect", lambda name, timeout=20: (machine, ""))
    text = vm._run({"action": "screen", "name": "probe"})
    assert "refused" in text and "not running" in text
    assert "Photographed" not in text


def test_the_screendump_dimensions_come_from_the_header(monkeypatch, tmp_path) -> None:
    _grant(monkeypatch)
    machine = _FakeMachine()
    monkeypatch.setattr(vm, "_connect", lambda name, timeout=20: (machine, ""))

    wrote = {}

    def fake_rpc(execute, arguments=None):
        machine.calls.append((execute, arguments))
        if execute == "screendump":
            wrote["path"] = arguments["filename"]
            with open(arguments["filename"], "wb") as h:
                h.write(b"P6\n1024 768\n255\n" + b"\x00" * 64)
        return {"return": {}}

    machine.rpc = fake_rpc
    monkeypatch.setattr(vm, "_runtime_dir", lambda: str(tmp_path))
    text = vm._run({"action": "screen", "name": "probe"})
    assert "1024x768" in text, text
    assert wrote["path"].startswith(str(tmp_path))


def test_typing_maps_shift_and_skips_the_ungivable(monkeypatch) -> None:
    _grant(monkeypatch)
    machine = _FakeMachine()
    monkeypatch.setattr(vm, "_connect", lambda name, timeout=20: (machine, ""))
    text = vm._run({"action": "type", "name": "probe", "text": "Hi!"})
    assert "Typed 3" in text and "skipped 0" not in text
    # The 'H' is the first character typed: shift down before it, shift up after.
    events = machine.calls[0][1]["events"]
    keys = [e["data"]["key"]["data"] for e in events if e["data"]["down"]]
    assert keys[0] == "shift" and keys[1] == "h", keys
    text = vm._run({"action": "type", "name": "probe", "text": "caf\u00e9"})
    assert "skipped 1" in text


def test_the_post_condition_wants_the_unit_inactive() -> None:
    original = vm._systemd
    try:
        vm._systemd = lambda argv, timeout=30: (
            subprocess.CompletedProcess(argv, 0, "active\n", ""), "")
        assert vm._post_condition({"action": "stop", "name": "probe"}) == (
            False, "probe's unit is active")
        vm._systemd = lambda argv, timeout=30: (
            subprocess.CompletedProcess(argv, 0, "inactive\n", ""), "")
        assert vm._post_condition({"action": "stop", "name": "probe"}) == (
            True, "probe's unit is inactive")
        vm._systemd = lambda argv, timeout=30: (None, "no bus")
        assert vm._post_condition({"action": "stop", "name": "probe"}) is None
        # Only stop is a promise; listing must not claim one.
        assert vm._post_condition({"action": "list"}) is None
    finally:
        vm._systemd = original


def test_the_stop_prefers_the_machines_own_quit(monkeypatch) -> None:
    _grant(monkeypatch)
    machine = _FakeMachine()
    monkeypatch.setattr(vm, "_connect", lambda name, timeout=10: (machine, ""))
    monkeypatch.setattr(vm, "_systemd", lambda argv, timeout=30: (
        subprocess.CompletedProcess(argv, 0, "inactive\n", ""), ""))
    text = vm._run({"action": "stop", "name": "probe"})
    assert ("quit", None) in machine.calls, "a reachable machine is asked to quit first"
    assert "Stopped probe at its own request" in text

    # A machine whose socket is gone is stopped by its unit instead.
    monkeypatch.setattr(vm, "_connect", lambda name, timeout=10: (None, "no socket"))
    calls = []
    monkeypatch.setattr(vm, "_systemd", lambda argv, timeout=30: (
        calls.append(argv) or (subprocess.CompletedProcess(argv, 0, "", ""), "")))
    text = vm._run({"action": "stop", "name": "probe"})
    assert any(c[:2] == ["stop", "chronoa-vm-probe.service"] for c in calls), calls
    assert "by stopping its unit" in text


_HAS_QEMU = shutil.which("qemu-system-x86_64") is not None and shutil.which("systemd-run") is not None


@pytest.mark.skipif(not _HAS_QEMU, reason="qemu-system-x86_64 or systemd-run is not installed on this machine")
def test_a_real_vm_is_driven_end_to_end(tmp_path, monkeypatch) -> None:
    """The one test that proves any of this does anything.

    Launches a real QEMU, waits for its real QMP socket, photographs its real
    firmware screen, types into it and stops it. Everything else here is about
    refusals and wiring. Skips on a machine with no QEMU, and the guard can be
    True - this box has both binaries, which is why it runs.
    """
    from shani_chronoa import tools
    from shani_chronoa.config import ChronoaConfig

    monkey = pytest.MonkeyPatch()
    monkey.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkey.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkey.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    (tmp_path / "config").mkdir()
    try:
        ChronoaConfig().set("vm-control-enabled", "true")
        started = tools.execute_tool_outcome(
            "vm", {"action": "launch", "name": "probe", "memory_mb": 256, "display": "none"})
        assert started.ran, "the skill never ran"
        assert "Started probe" in started.text, started.text

        # The screendump is real pixels: QMP writes a P6 file whose header
        # names the dimensions, so reading them back proves the round trip.
        screen = tools.execute_tool_outcome("vm", {"action": "screen", "name": "probe"})
        assert "Photographed probe's screen" in screen.text, screen.text
        path = screen.text.split(": ", 1)[1].split(" (", 1)[0]
        with open(path, "rb") as h:
            assert h.read(2) == b"P6", "the screendump is not a real PPM"

        typed = tools.execute_tool_outcome(
            "vm", {"action": "type", "name": "probe", "text": "hi"})
        assert "Typed 2 character(s)" in typed.text, typed.text

        stopped = tools.execute_tool_outcome("vm", {"action": "stop", "name": "probe"})
        assert stopped.ran
        assert "Stopped probe" in stopped.text, stopped.text
    finally:
        # A failed assertion must still tear the machine down, or every later
        # run collides with this one's name.
        try:
            subprocess.run(["systemctl", "--user", "stop", "chronoa-vm-probe.service"],
                           capture_output=True, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError):
            pass
        monkey.undo()
