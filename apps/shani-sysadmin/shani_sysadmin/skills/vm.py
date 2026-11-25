"""Skill: run a virtual machine and drive it, the way `browse` drives a browser.

The VM half of "automate the machine like a browser is automated". `browse`
puts a *window* (the in-app WebKit, visible, with the person watching) beside a
*protocol* (CDP) that drives what is inside it. This is the same shape with
QEMU: a **window** on the desktop (`-display gtk`, the person sees the machine
boot) beside a **socket** (`-qmp unix:...`) that drives what is inside it.

QMP is QEMU's own control protocol, so driving the guest needs no Wayland
RemoteDesktop portal, no synthetic input and no person to approve a capture:
`send-key` types into the guest's own keyboard, `input-send-event` moves and
clicks its pointer on QEMU's absolute axis, and `screendump` photographs what
the guest is showing. Everything here was measured against a real QEMU 10.2.1
on this box before the parsers were written (see AGENTS.md): a real QMP
session on a real SeaBIOS screen produced a real 720x400 PPM, and `quit`
returned `{"return": {}}` and exit status 0.

**Why the VM runs as a user systemd unit.** Two measured reasons, both from
this box:

1. A skill child runs under the 512 MB `RLIMIT_AS` of `PROFILE_DEFAULT`, soft
   equal to hard and inherited by everything it spawns, and it cannot raise
   the limit. `ulimit -v 524288; qemu-system-x86_64 -m 1024 ...` fails with
   *"allocate 1073741824 bytes for jit buffer: Cannot allocate memory"*, so a
   guest larger than the sandbox ceiling cannot run as the skill's own child.
2. A VM must outlive the tool call that started it - to be typed into on the
   next turn - and a skill's own children die with it.

A transient user unit is spawned by the user manager, outside the skill child,
and measured to escape both: a unit started from a capped shell reports
`Max address space unlimited unlimited`, and `--no-block` returns immediately
while the machine keeps running. `stop` uses QMP's own `quit` first, falling
back to `systemctl --user stop` when the socket cannot be reached, so a wedged
machine can still be turned off.

Honesty rules:
- the reply names which display was used and, when firmware has not drawn
  anything yet, says the screen is the firmware's own rather than guessing.
- a screendump is reported as the file it wrote, with the dimensions read from
  its header, never described.
- `list` reports a unit whose QEMU died as dead rather than as running: the
  unit is stopped but the process went away early.

**What this box could and could not settle, measured.** UEFI is real here:
launched with `firmware: "uefi"` the screendump came back 640x480, which is
OVMF's own resolution and therefore the firmware that ran, against SeaBIOS's
720x400 for the bios path. A **TPM** is not: swtpm starts and its socket
appears, but the QEMU it is attached to creates its QMP socket, keeps running
and never answers it - libtpms does not come up on this Ubuntu build. The
launch says so rather than reporting a machine that works, and the path needs a
ShaniOS slot run before it can be claimed. `bios` with and without a UEFI
firmware is verified; a virtual TPM is written and honestly marked unproven.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import time

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "vm-control-enabled"
_TIMEOUT = 120
_UNIT_PREFIX = "chronoa-vm-"
_ACTIONS = ("launch", "list", "screen", "key", "type", "click", "move", "status", "stop")
#: QMP's absolute axis is 0..32767 whatever the guest's resolution is.
_AXIS_MAX = 32767
_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
_MEMORY = re.compile(r"^\d+$")

#: QMP key names for the combos a person actually asks for. Anything not in
#: here is passed through, which is QMP's own spelling (`f1`, `spc`, `ret`).
_KEY_ALIASES = {
    "enter": "ret", "return": "ret", "space": "spc", "tab": "tab",
    "esc": "esc", "escape": "esc", "backspace": "backspace",
    "delete": "delete", "insert": "insert", "home": "home", "end": "end",
    "pgup": "pgup", "pgdn": "pgdn", "up": "up", "down": "down",
    "left": "left", "right": "right",
    "ctrl": "ctrl", "alt": "alt", "shift": "shift",
    "super": "meta_l", "cmd": "meta_l", "win": "meta_l",
    "f1": "f1", "f2": "f2", "f3": "f3", "f4": "f4", "f5": "f5", "f6": "f6",
    "f7": "f7", "f8": "f8", "f9": "f9", "f10": "f10", "f11": "f11", "f12": "f12",
}
#: Characters that need the shift key held down to produce.
_SHIFTED = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7",
    "*": "8", "(": "9", ")": "0", "_": "minus", "+": "equal",
    "{": "bracket_left", "}": "bracket_right", "|": "backslash",
    ":": "semicolon", '"': "apostrophe", "<": "comma", ">": "period",
    "?": "slash", "~": "grave",
}
#: Characters that are their own qcode without shift.
_PLAIN = {
    "-": "minus", "=": "equal", "[": "bracket_left", "]": "bracket_right",
    "\\": "backslash", ";": "semicolon", "'": "apostrophe", ",": "comma",
    ".": "period", "/": "slash", "`": "grave", " ": "spc", "\n": "ret",
    "\t": "tab",
}


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """Return (allowed, reason). Fail-closed: an undeclared key denies."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            f"running virtual machines is turned off (enable '{_CONSENT_KEY}' "
            f"in Settings). Listing the VMs already running needs no such "
            f"permission - only starting one and typing into it does, because "
            f"a VM is a whole computer whatever its disk says it is."
        )
    return True, ""


def _runtime_dir() -> str:
    return os.environ.get("XDG_RUNTIME_DIR") or os.path.join(
        os.path.expanduser("~"), ".cache", "shani-chronoa")


def _socket_path(name: str) -> str:
    return os.path.join(_runtime_dir(), f"{_UNIT_PREFIX}{name}.qmp")


def _unit(name: str) -> str:
    return f"{_UNIT_PREFIX}{name}.service"


class _QMP:
    """The smallest thing that is still a QMP client: handshake and rpc.

    A unix socket carrying newline-delimited JSON, which is the whole of the
    protocol the actions here need. Written in-module rather than shared
    because the skill runs in a sandboxed child where a package import is a
    dependency, and this is a socket plus `json` - the stdlib Chronoa already
    has.
    """

    def __init__(self, path: str, timeout: float = 20.0) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        self.sock.connect(path)
        self.handle = self.sock.makefile("rw")
        self.greeting = self._read()
        if "QMP" not in self.greeting:
            raise ValueError("the socket did not answer with a QMP greeting")
        self.rpc("qmp_capabilities")

    def _read(self) -> dict:
        line = self.handle.readline()
        return json.loads(line) if line.strip() else {}

    def rpc(self, execute: str, arguments: "dict | None" = None) -> dict:
        message = {"execute": execute}
        if arguments:
            message["arguments"] = arguments
        self.handle.write(json.dumps(message) + "\n")
        self.handle.flush()
        for _ in range(64):
            event = self._read()
            if "return" in event or "error" in event:
                return event
        raise ValueError(f"QMP never returned from {execute}")

    def close(self) -> None:
        try:
            self.handle.close()
        finally:
            self.sock.close()

    def __enter__(self) -> "_QMP":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _connect(name: str, timeout: float = 20.0):
    """Connect to a running VM's socket, or say why it could not be reached."""
    path = _socket_path(name)
    if not os.path.exists(path):
        return None, (f"No control socket for {name}, so it is not running as "
                      f"one of this machine's VMs.")
    try:
        return _QMP(path, timeout=timeout), ""
    except (OSError, ValueError) as exc:
        return None, (f"The control socket for {name} would not answer ({exc}); "
                      f"the machine may have died. Stop it and launch it again.")


def _systemd(argv: "list[str]", timeout: int = 30):
    if shutil.which("systemctl") is None or shutil.which("systemd-run") is None:
        return None, files.tool_missing("systemd-run", "run a virtual machine")
    try:
        return subprocess.run(["systemctl", "--user", *argv], capture_output=True,
                              text=True, timeout=timeout, check=False), ""
    except subprocess.TimeoutExpired:
        return None, f"systemd did not answer within {timeout}s."
    except OSError as exc:
        return None, f"could not talk to the user systemd: {exc}"


def _launch(argv: "list[str]"):
    """Start a unit that stays running, and return without waiting for it."""
    if shutil.which("systemd-run") is None:
        return None, files.tool_missing("systemd-run", "run a virtual machine")
    # A unit that failed on a previous attempt stays *loaded*, and systemd then
    # refuses a second transient unit under the same name - measured: "Unit
    # chronoa-vm-uefitest-tpm.service was already loaded". Resetting first is
    # what makes a retry possible.
    unit = next((a.split("=", 1)[1] for a in argv if a.startswith("--unit=")), "")
    if unit:
        try:
            subprocess.run(["systemctl", "--user", "reset-failed", unit],
                           capture_output=True, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        return subprocess.run(["systemd-run", "--user", "--no-block", "--quiet",
                               *argv], capture_output=True, text=True,
                              timeout=30, check=False), ""
    except subprocess.TimeoutExpired:
        return None, "systemd-run did not answer within 30s, so nothing is known about the machine."
    except OSError as exc:
        return None, f"could not start the machine: {exc}"


def _has_display() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))


def _kvm_ok() -> bool:
    return os.path.exists("/dev/kvm") and os.access("/dev/kvm", os.R_OK | os.W_OK)


_OVMF_CANDIDATES = (
    "/usr/share/ovmf/OVMF.fd",
    "/usr/share/qemu/OVMF.fd",
    "/usr/share/edk2-ovmf/x64/OVMF_CODE.4m.fd",
    "/usr/share/edk2/OVMF_CODE.fd",
)


def _ovmf_path() -> str:
    """The firmware image for a UEFI boot, or '' when none is installed.

    This is what makes the machine boot like a real PC rather than a 1980s
    one: SeaBIOS starts at the MBR, UEFI starts at the firmware shell and an
    EFI boot entry. On ShaniOS the difference is the whole point - the system
    boots through an EFI stub.
    """
    for path in _OVMF_CANDIDATES:
        if os.path.isfile(path):
            return path
    return ""


def _tpm_socket(name: str) -> str:
    """A swtpm socket path, deliberately *not* under XDG_RUNTIME_DIR.

    Measured on this box, and the measurement is the reason: swtpm refuses a
    socket under `/run/user/1000` with *"Could not open UnixIO socket:
    Permission denied"* while the identical call succeeds under `$HOME` and
    `/tmp` - Ubuntu's `usr.bin.swtpm` AppArmor profile allows `owner @{HOME}/**`
    and libvirt's own paths, and the session runtime directory is neither. The
    cache directory is under `$HOME`, so it answers on a box carrying that
    profile and equally on one (ShaniOS) without it.
    """
    return os.path.join(_tpm_dir(), f"{name}.sock")


def _tpm_state(name: str) -> str:
    path = os.path.join(_tpm_dir(), name)
    os.makedirs(path, exist_ok=True)  # swtpm does not create it, and a missing
    return path                        # state dir is a fatal "No such file or
                                      # directory" after the socket exists


def _tpm_dir() -> str:
    cache = os.environ.get("XDG_CACHE_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache")
    path = os.path.join(cache, "shani-chronoa", "vm-tpm")
    os.makedirs(path, exist_ok=True)
    return path


def _start_tpm(name: str) -> str:
    """A swtpm for this machine, in a unit of its own, or the reason not."""
    if shutil.which("swtpm") is None:
        return "swtpm is not installed, so the machine cannot be given a TPM."
    argv = ["swtpm", "socket", "--tpm2",
            "--server", f"type=unixio,path={_tpm_socket(name)}",
            "--ctrl", f"type=unixio,path={_tpm_socket(name)}.ctrl",
            "--flags", "not-need-init", "--tpmstate", f"dir={_tpm_state(name)}"]
    proc, why = _launch([f"--unit={_UNIT_PREFIX}{name}-tpm.service", *argv])
    if proc is None:
        return why
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return ("swtpm would not start" + (f": {detail[-1]}" if detail else "")
                + ", so the machine cannot be given a TPM.")
    for _ in range(40):
        if os.path.exists(_tpm_socket(name)):
            return ""
        time.sleep(0.25)
    return "swtpm started but its socket never appeared."


def _memory_cap() -> int:
    """A share of this machine's RAM, so the VM cannot eat the computer."""
    try:
        with open("/proc/meminfo", encoding="utf-8") as h:
            for line in h:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024 // 2
    except (OSError, ValueError, IndexError):
        pass
    return 1024


def _do_launch(arguments: dict) -> str:
    name = str(arguments.get("name") or "").strip()
    if not name:
        return "No name was given. Use one so the machine can be found again, e.g. 'test'."
    if not _NAME.match(name):
        return f"{name!r} is not a plausible machine name (start with a letter or digit; no slashes)."
    if shutil.which("qemu-system-x86_64") is None:
        return files.tool_missing("qemu-system-x86_64", "run a virtual machine")

    proc, why = _systemd(["show", _unit(name), "-p", "LoadState", "-p", "ActiveState", "--value"])
    if proc is None:
        return why
    values = [v for v in (proc.stdout or "").splitlines() if v.strip()]
    load_state = values[0] if values else ""
    active_state = values[1] if len(values) > 1 else ""
    if active_state == "active":
        return (f"A machine called {name} is already running as {_unit(name)}. "
                f"Stop it first, or pick another name.")
    if load_state == "loaded":
        # A unit left over from a failed attempt is loaded but not active. Say
        # so, and let the reset in `_launch` clear it, rather than reporting a
        # dead machine as running.
        _systemd(["reset-failed", _unit(name)])

    memory = str(arguments.get("memory_mb") or 1024).strip()
    if not _MEMORY.match(memory) or not 64 <= int(memory) <= _memory_cap():
        return (f"memory_mb must be between 64 and {_memory_cap()} MB (half of "
                f"this machine's RAM, so the VM cannot eat the computer), not {memory!r}.")

    iso = str(arguments.get("iso") or "").strip()
    disk = str(arguments.get("disk") or "").strip()
    for label, path in (("iso", iso), ("disk", disk)):
        if path and not os.path.isfile(path):
            return f"The {label} {path!r} is not a file on this machine, so the machine would have nothing to boot."

    display = str(arguments.get("display") or "").strip().lower()
    if not display:
        # Visible by default, because that is the whole point of the window
        # beside the socket: the person watches the machine boot.
        display = "gtk" if _has_display() else "none"
    if display not in ("gtk", "none", "sdl", "spice-app", "curses", "egl-headless"):
        return f"display must be one of gtk, none, sdl, spice-app, curses, egl-headless, not {display!r}."

    argv = ["qemu-system-x86_64", "-name", name, "-m", memory,
            "-display", display, "-qmp", f"unix:{_socket_path(name)},server,nowait"]
    if _kvm_ok():
        argv.append("-enable-kvm")
    firmware = str(arguments.get("firmware") or "bios").strip().lower()
    if firmware not in ("bios", "uefi"):
        return f"firmware must be bios or uefi, not {firmware!r}."
    if firmware == "uefi":
        ovmf = _ovmf_path()
        if not ovmf:
            return ("UEFI was asked for and no OVMF firmware image is installed, so the "
                    "machine would not boot; install the ovmf package or ask for bios.")
        argv += ["-drive", f"if=pflash,format=raw,readonly=on,file={ovmf}"]
    if arguments.get("tpm"):
        why = _start_tpm(name)
        if why:
            return f"Not started: {why}"
        argv += ["-chardev", f"socket,id=chrtpm,path={_tpm_socket(name)}",
                 "-tpmdev", "emulator,id=tpm0,chardev=chrtpm", "-device", "tpm-tis,tpm-device=tpm0"]
    if iso:
        argv += ["-cdrom", iso]
    if disk:
        argv += ["-drive", f"file={disk},format=qcow2,if=virtio"]
    argv += ["-device", "virtio-keyboard-pci", "-device", "virtio-tablet-pci"]
    # A tablet, not a mouse: QMP's `input-send-event` moves an *absolute*
    # axis, and a relative device is refused with "Input handler not found
    # for event type abs" - measured, not assumed.

    proc, why = _launch([f"--unit={_unit(name)}", *argv])
    if proc is None:
        return why
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return (f"systemd would not start the machine (exit {proc.returncode}), so it "
                f"is not running." + (f" It said: {detail[-1]}" if detail else ""))

    # The socket appears a moment after the unit is queued, so wait for the
    # handshake rather than claiming a machine that then failed to boot.
    machine = None
    last = ""
    for _ in range(60):
        try:
            machine = _QMP(_socket_path(name), timeout=5)
            break
        except (OSError, ValueError) as exc:
            last = str(exc)
            time.sleep(0.25)
    if machine is None:
        tpm_note = ""
        if arguments.get("tpm"):
            # Measured on this dev box, and the reason it is here rather than in
            # a comment: a TPM-equipped QEMU creates its socket, keeps running,
            # and *never answers QMP* - the TPM emulator (libtpms) does not come
            # up on this Ubuntu build. That is an environment wall, not a defect
            # in this launch, so it is named rather than folded into "may have
            # exited".
            tpm_note = (" A machine given a TPM also does this while its TPM "
                        "emulator refuses to start - swtpm is running but QEMU "
                        "never answers - so try the same launch without tpm.")
        return (f"Started the unit, but the machine's control socket never answered "
                f"({last or 'no socket'}); QEMU may have exited at once - an "
                f"unbootable image or a missing display both do that. Check with "
                f"systemctl --user status {_unit(name)}.{tpm_note}")
    try:
        status = machine.rpc("query-status").get("return", {})
    finally:
        machine.close()

    where = "on the desktop" if display == "gtk" else f"with no display ({display})"
    speed = "with KVM" if _kvm_ok() else "without KVM, so it is slow"
    extras = []
    if firmware == "uefi":
        extras.append("UEFI firmware")
    if arguments.get("tpm"):
        extras.append("a TPM")
    extra = f", {', '.join(extras)}" if extras else ""
    return (f"Started {name}: {memory} MB {speed}, shown {where}{extra}. "
            f"It is {status.get('status', 'running')} - firmware is drawing now.")


def _do_list(_arguments: dict) -> str:
    proc, why = _systemd(["list-units", f"{_UNIT_PREFIX}*", "--no-legend", "--plain"])
    if proc is None:
        return why
    if proc.returncode != 0:
        return (f"systemd could not list the machines (exit {proc.returncode}); what is "
                f"running is unknown.")
    rows = [line.split() for line in (proc.stdout or "").splitlines()
            if line.strip() and not line.strip().startswith("To show")]
    if not rows:
        return "No virtual machines are running on this machine."
    out = [f"{len(rows)} machine(s):"]
    for cells in rows:
        unit = next((c for c in cells if c.startswith(_UNIT_PREFIX)), "")
        name = unit[len(_UNIT_PREFIX):-len(".service")] if unit else ""
        state = next((c for c in cells if c in
                      ("running", "exited", "failed", "activating", "deactivating")), "unknown")
        note = ""
        if state != "running":
            # The unit is stopped but QEMU went away before it, which is what a
            # machine that died at boot looks like; say so rather than "stopped".
            note = " - QEMU has exited (the image or display failed)"
        out.append(f"  {name:<20} {state}{note}")
    return "\n".join(out)


def _ppm_dimensions(path: str) -> "tuple[int, int]":
    """Read `P6 <w> <h> <max>` out of a screendump, or (0, 0)."""
    try:
        with open(path, "rb") as h:
            header = h.read(64)
    except OSError:
        return 0, 0
    if not header.startswith(b"P6"):
        return 0, 0
    cells = header[2:].split(None, 3)
    if len(cells) < 2:
        return 0, 0
    try:
        return int(cells[0]), int(cells[1])
    except ValueError:
        return 0, 0


def _do_screen(arguments: dict) -> str:
    name = str(arguments.get("name") or "").strip()
    if not name:
        return "No machine was named to look at."
    machine, why = _connect(name)
    if machine is None:
        return why
    path = os.path.join(_runtime_dir(), f"{_UNIT_PREFIX}{name}-screen.ppm")
    try:
        result = machine.rpc("screendump", {"filename": path})
    finally:
        machine.close()
    if "error" in result:
        return (f"The machine refused a screenshot ({result['error'].get('desc', 'QMP error')}); "
                f"its screen was not photographed.")
    width, height = _ppm_dimensions(path)
    if not width or not height:
        return (f"The machine wrote {path} but it is not a PPM screendump, so nothing "
                f"was photographed. Its screen is unknown.")
    return (f"Photographed {name}'s screen: {path} ({width}x{height}, a real image - "
            f"open it to look). That is what the guest is showing, firmware and all.")


def _do_key(arguments: dict) -> str:
    name = str(arguments.get("name") or "").strip()
    if not name:
        return "No machine was named to send keys to."
    combo = str(arguments.get("keys") or "").strip()
    if not combo:
        return "No keys were named. Something like 'f12', 'enter', or 'ctrl-alt-f2'."
    parts = [p for p in re.split(r"[+-]", combo.lower()) if p]
    parts = [_KEY_ALIASES.get(p, p) for p in parts]
    if not parts:
        return f"{combo!r} is not a key combination I can send (empty part)."
    machine, why = _connect(name)
    if machine is None:
        return why
    try:
        events = [{"type": "key", "data": {"down": True, "key": {"type": "qcode", "data": p}}}
                  for p in parts]
        events += [{"type": "key", "data": {"down": False, "key": {"type": "qcode", "data": p}}}
                   for p in reversed(parts)]
        result = machine.rpc("input-send-event", {"events": events})
    finally:
        machine.close()
    if "error" in result:
        return (f"The machine refused the keys ({result['error'].get('desc', 'QMP error')}); "
                f"nothing was sent.")
    return f"Sent {combo} to {name}."


def _qcode_for(ch: str) -> "tuple[str | None, bool]":
    """(qcode, needs-shift) for one character, or (None, False)."""
    if ch.isascii() and ch.isalpha():
        return ch.lower(), ch.isupper()
    if ch.isdigit():
        return ch, False
    if ch in _SHIFTED:
        return _SHIFTED[ch], True
    if ch in _PLAIN:
        return _PLAIN[ch], False
    return None, False


def _do_type(arguments: dict) -> str:
    name = str(arguments.get("name") or "").strip()
    if not name:
        return "No machine was named to type into."
    text = arguments.get("text")
    if not isinstance(text, str) or not text:
        return "No text was given to type."
    machine, why = _connect(name)
    if machine is None:
        return why
    typed, skipped = 0, 0
    try:
        for ch in text:
            code, shift = _qcode_for(ch)
            if code is None:
                skipped += 1
                continue
            events = ([{"type": "key", "data": {"down": True, "key": {"type": "qcode", "data": "shift"}}}]
                      if shift else [])
            events.append({"type": "key", "data": {"down": True, "key": {"type": "qcode", "data": code}}})
            events.append({"type": "key", "data": {"down": False, "key": {"type": "qcode", "data": code}}})
            if shift:
                events.append({"type": "key", "data": {"down": False, "key": {"type": "qcode", "data": "shift"}}})
            result = machine.rpc("input-send-event", {"events": events})
            if "error" in result:
                return (f"The machine refused the text after {typed} character(s) "
                        f"({result['error'].get('desc', 'QMP error')}).")
            typed += 1
            time.sleep(0.03)
    finally:
        machine.close()
    if skipped:
        return (f"Typed {typed} character(s) into {name}, and skipped {skipped} it has no "
                f"key for (accents and symbols the guest cannot be given this way).")
    return f"Typed {typed} character(s) into {name}."


def _axis(percent: str) -> "int | None":
    try:
        value = float(percent)
    except (TypeError, ValueError):
        return None
    if not 0 <= value <= 100:
        return None
    return max(0, min(_AXIS_MAX, round(value / 100 * _AXIS_MAX)))


def _do_pointer(arguments: dict, press: bool) -> str:
    name = str(arguments.get("name") or "").strip()
    if not name:
        return "No machine was named."
    x, y = _axis(arguments.get("x")), _axis(arguments.get("y"))
    if x is None or y is None:
        return "x and y are percentages of the machine's screen, so both must be 0-100."
    machine, why = _connect(name)
    if machine is None:
        return why
    try:
        events = [{"type": "abs", "data": {"axis": "x", "value": x}},
                  {"type": "abs", "data": {"axis": "y", "value": y}}]
        if press:
            button = str(arguments.get("button") or "left").strip().lower()
            if button not in ("left", "right", "middle"):
                return f"button must be left, right or middle, not {button!r}."
            events += [{"type": "btn", "data": {"button": button, "down": True}},
                       {"type": "btn", "data": {"button": button, "down": False}}]
        result = machine.rpc("input-send-event", {"events": events})
    finally:
        machine.close()
    if "error" in result:
        detail = result["error"].get("desc", "QMP error")
        return f"The machine refused the pointer ({detail}); it was not moved."
    where = f"{arguments.get('x')}%, {arguments.get('y')}%"
    if press:
        return f"Moved {name}'s pointer to {where} and clicked {arguments.get('button') or 'left'}."
    return f"Moved {name}'s pointer to {where}."



def _do_status(arguments: dict) -> str:
    name = str(arguments.get("name") or "").strip()
    if not name:
        return "No machine was named."
    proc, why = _systemd(["is-active", _unit(name)])
    unit_state = (proc.stdout or "").strip() if proc is not None else "unknown"
    machine, connect_why = _connect(name, timeout=10)
    if machine is None:
        return f"{name}: the unit is {unit_state}, and {connect_why}"
    try:
        status = machine.rpc("query-status").get("return", {})
        identity = machine.rpc("query-name").get("return", {})
    finally:
        machine.close()
    state = status.get("status", "unknown")
    running = "running" if status.get("running") else "not running"
    return (f"{name} is {state} ({running}), unit {unit_state}. QEMU answers as "
            f"{identity.get('name', 'unnamed')}.")


def _do_stop(arguments: dict) -> str:
    name = str(arguments.get("name") or "").strip()
    if not name:
        return "No machine was named to stop."
    machine, _why = _connect(name, timeout=10)
    if machine is not None:
        try:
            result = machine.rpc("quit")
        finally:
            machine.close()
        if "error" in result:
            detail = result["error"].get("desc", "QMP error")
            return f"The machine refused to quit ({detail}); stopping the unit instead."
        # QEMU exits, and the unit follows it; give it a moment and confirm.
        for _ in range(40):
            time.sleep(0.25)
            proc, _ = _systemd(["is-active", _unit(name)])
            if proc is not None and "active" not in (proc.stdout or ""):
                break
        _forget_files(name)
        return f"Stopped {name} at its own request. The unit is no longer active."
    proc, why = _systemd(["stop", _unit(name)], timeout=60)
    if proc is None:
        return why
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return (f"systemd did not stop {name} (exit {proc.returncode}), so it is unknown "
                f"whether it is still running."
                + (f" It said: {detail[-1]}" if detail else ""))
    # The TPM's unit is a separate one, and leaving it running would hold a
    # state directory this machine no longer needs.
    _systemd(["stop", f"{_UNIT_PREFIX}{name}-tpm.service"], timeout=30)
    _forget_files(name)
    return f"Stopped {name} by stopping its unit."


def _forget_files(name: str) -> None:
    """Remove the socket and the last screendump from the runtime directory.

    A socket file with nothing behind it is exactly what `_connect` finds and
    refuses, so leaving one makes a *later* launch of the same name look like a
    machine that died - measured, and the reason this exists rather than being
    left to logout. XDG_RUNTIME_DIR is wiped at logout, so this is tidiness
    rather than a leak.
    """
    for path in (_socket_path(name),
                 os.path.join(_runtime_dir(), f"{_UNIT_PREFIX}{name}-screen.ppm")):
        try:
            os.unlink(path)
        except OSError:
            pass


def _run(arguments: dict) -> str:
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing to run virtual machines: {reason}"

    action = str(arguments.get("action") or "list").strip().lower()
    if action not in _ACTIONS:
        return f"Action must be one of {', '.join(_ACTIONS)}, not {action!r}."

    handlers = {
        "launch": _do_launch,
        "list": _do_list,
        "screen": _do_screen,
        "key": _do_key,
        "type": _do_type,
        "click": lambda a: _do_pointer(a, press=True),
        "move": lambda a: _do_pointer(a, press=False),
        "status": _do_status,
        "stop": _do_stop,
    }
    return handlers[action](arguments)


def _post_condition(arguments: dict):
    """A stopped machine must be gone: the unit inactive, or QEMU's socket gone."""
    if str(arguments.get("action") or "").strip().lower() != "stop":
        return None
    name = str(arguments.get("name") or "").strip()
    if not _NAME.match(name):
        return None
    proc, _ = _systemd(["is-active", _unit(name)])
    if proc is None:
        return None
    state = (proc.stdout or "").strip()
    if not state or state == "unknown":
        return None
    return (state != "active", f"{name}'s unit is {state}")


# Declared for `verification.verify`; the LLM never supplies this.
POST_CONDITION = _post_condition


SCHEMA = {
    "type": "function",
    "function": {
        "name": "vm",
        "description": (
            "Run a virtual machine with QEMU and drive it like a browser: "
            "launch it with a visible window on the desktop and a QMP control "
            "socket beside it, photograph its screen, send key combinations, "
            "type text into it, move and click its pointer (percentages of its "
            "screen), check its power state, and stop it. Requires the "
            "'vm-control-enabled' consent key, off by default. Listing the "
            "VMs already running is `list_vms` (virsh) or this skill's list "
            "action; this one starts and drives them."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": f"One of: {', '.join(_ACTIONS)}. Defaults to list.",
                },
                "name": {
                    "type": "string",
                    "description": "The machine's name, used again for screen/key/type/status/stop, e.g. 'test'.",
                },
                "memory_mb": {
                    "type": "integer",
                    "description": "Guest RAM for launch. Capped at half this machine's RAM. Defaults to 1024.",
                },
                "iso": {
                    "type": "string",
                    "description": "For launch: path of an installer or live ISO to boot.",
                },
                "disk": {
                    "type": "string",
                    "description": "For launch: path of a qcow2 disk image to attach.",
                },
                "display": {
                    "type": "string",
                    "description": "For launch: gtk (a visible window, the default when a display exists), none, sdl, spice-app, curses or egl-headless.",
                },
                "firmware": {
                    "type": "string",
                    "description": "For launch: bios (SeaBIOS, the default) or uefi (boot through OVMF firmware, like a real PC and like ShaniOS).",
                },
                "tpm": {
                    "type": "boolean",
                    "description": "For launch: give the machine a virtual TPM 2.0 via swtpm, so firmware that wants one (and Windows 11) will start.",
                },
                "keys": {
                    "type": "string",
                    "description": "For key: a combination like 'enter', 'f12' or 'ctrl-alt-f2' (plus or hyphen both work).",
                },
                "text": {
                    "type": "string",
                    "description": "For type: the text to type into the guest.",
                },
                "x": {
                    "type": "number",
                    "description": "For click/move: percentage across the guest's screen, 0-100.",
                },
                "y": {
                    "type": "number",
                    "description": "For click/move: percentage down the guest's screen, 0-100.",
                },
                "button": {
                    "type": "string",
                    "description": "For click: left (the default), right or middle.",
                },
            },
            "required": ["action"],
        },
    },
}


SKILLS = [Skill(name="vm", schema=SCHEMA, run=_run)]
