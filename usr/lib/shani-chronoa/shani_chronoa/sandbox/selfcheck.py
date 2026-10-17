"""Ask the sandbox what it actually does, by running something inside it.

The Sandbox page shows this. **A probe, not a reading of the configuration:**
the README promises that a command cannot read `~/.ssh`, cannot reach the
network and cannot write the system, and those are claims about what happens
in the child - so the child is asked. The probe runs through the same
`SandboxExecutor`, the same `SandboxConfig` and the same profile as
`run_command`, so what it reports is what a project's tests would get.

Nothing leaves the machine: the network check lists the interfaces the child
can see (`/sys/class/net`) instead of connecting anywhere.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from typing import List, Optional

#: Private folders the probe tries to read, first one that exists on the host.
PRIVATE_DIRS = (".ssh", ".gnupg", ".local/share/keyrings", ".mozilla", "Documents")

_PROBE = r'''
import json, os, sys
out = {}
private, project = sys.argv[1], sys.argv[2]
def tried(fn):
    try:
        fn()
        return True
    except Exception:
        return False
out["private_listable"] = tried(lambda: os.listdir(private)) if private else None
def read_one():
    for name in sorted(os.listdir(private)):
        path = os.path.join(private, name)
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                fh.read(1)
            return
    raise FileNotFoundError("no file to try")
out["private_readable"] = tried(read_one) if private else None
try:
    # /proc/net/dev is this process's own network namespace; /sys/class/net
    # is whichever namespace sysfs was mounted in, which can be the host's.
    with open("/proc/net/dev") as fh:
        out["interfaces"] = sorted(line.split(":")[0].strip()
                                   for line in fh.readlines()[2:] if ":" in line)
except OSError:
    out["interfaces"] = None
def write_system():
    with open("/usr/.chronoa-sandbox-probe", "w") as fh:
        fh.write("x")
    os.unlink("/usr/.chronoa-sandbox-probe")
out["system_writable"] = tried(write_system)
def write_project():
    probe = os.path.join(project, ".chronoa-sandbox-probe")
    with open(probe, "w") as fh:
        fh.write("x")
    os.unlink(probe)
out["project_writable"] = tried(write_project) if project else None
print(json.dumps(out))
'''


@dataclass
class Check:
    name: str
    ok: Optional[bool]          # True as promised, False not, None not asked
    detail: str


@dataclass
class SelfCheck:
    ran: bool
    error: str = ""
    checks: List[Check] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return self.ran and all(c.ok is not False for c in self.checks)


def private_dir(home: Optional[str] = None) -> str:
    """The first private folder that exists here, or "" when none does."""
    home = home or os.path.expanduser("~")
    for name in PRIVATE_DIRS:
        path = os.path.join(home, name)
        if os.path.isdir(path):
            return path
    return ""


def _checks(result: dict, private: str, project: str) -> List[Check]:
    home = os.path.expanduser("~")
    short = private.replace(home, "~", 1) if private else ""
    checks = []
    if private:
        hidden = result.get("private_readable") is False
        named = result.get("private_listable") is True
        checks.append(Check(
            "Your private files cannot be read", hidden,
            (f"files in {short} could not be opened"
             + (" (their names can still be listed)" if named else ""))
            if hidden else f"a file in {short} was read from inside the sandbox"))
    else:
        checks.append(Check("Your private files cannot be read", None,
                            "no private folder here to try"))
    interfaces = result.get("interfaces")
    if interfaces is None:
        checks.append(Check("The network is cut", None,
                            "the interface list could not be read"))
    else:
        cut = [i for i in interfaces if i != "lo"] == []
        checks.append(Check(
            "The network is cut", cut,
            "only the loopback interface is visible" if cut
            else "visible: " + ", ".join(interfaces)))
    system = result.get("system_writable")
    checks.append(Check(
        "The system is read-only", system is False,
        "writing under /usr was refused" if system is False
        else "a file could be written under /usr"))
    writable = result.get("project_writable")
    if project:
        checks.append(Check(
            "The project is writable", writable is True,
            f"{project.replace(home, '~', 1)} can be changed" if writable
            else f"{project.replace(home, '~', 1)} could not be written"))
    return checks


def run(project: str = "") -> SelfCheck:
    """Run the probe the way `run_command` runs a command; never raises."""
    try:
        from shani_chronoa import tools
        from shani_chronoa.sandbox import PROFILE_FULL_ACCESS
        private = private_dir()
        config = tools._run_command_sandbox({})
        if project and project not in config.writable_dirs:
            config.writable_dirs = [project, *config.writable_dirs]
        python = shutil.which("python3") or "python3"
        code, output, _ms = tools._SANDBOX.execute(
            [python, "-c", _PROBE, private, project], config,
            tool_name="sandbox_selfcheck", profile=PROFILE_FULL_ACCESS)
    except Exception as exc:  # noqa: BLE001 - the page says why, it does not crash
        return SelfCheck(False, f"{exc.__class__.__name__}: {exc}")
    if code != 0:
        return SelfCheck(False, (output or f"exit {code}").strip()[-400:])
    try:
        result = json.loads(output.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return SelfCheck(False, "the probe answered something that was not its report: "
                         + (output or "").strip()[-200:])
    return SelfCheck(True, "", _checks(result, private, project))


def layers() -> List[Check]:
    """Which of the sandbox's layers this machine can provide (no probe child)."""
    out = []
    try:
        from shani_chronoa.sandbox import executor
        usable = executor._bwrap_usable()
        out.append(Check("bubblewrap", usable,
                         "creates the namespaces it needs" if usable
                         else ("installed, but cannot create namespaces here"
                               if shutil.which("bwrap") else "not installed")))
    except Exception as exc:  # noqa: BLE001
        out.append(Check("bubblewrap", None, f"could not be asked: {exc}"))
    try:
        from shani_chronoa.sandbox import landlock
        abi = landlock.abi_version()
        out.append(Check("Landlock", abi >= 1,
                         f"kernel ABI {abi}" if abi >= 1 else "not available in this kernel"))
    except Exception as exc:  # noqa: BLE001
        out.append(Check("Landlock", None, f"could not be asked: {exc}"))
    try:
        from shani_chronoa.sandbox import seccomp
        reason = seccomp.unavailable_reason()
        out.append(Check("seccomp", reason is None,
                         "available" if reason is None else reason))
    except Exception as exc:  # noqa: BLE001
        out.append(Check("seccomp", None, f"could not be asked: {exc}"))
    return out


def places() -> List[Check]:
    """Where else a command can run: containers and machines."""
    podman = shutil.which("podman")
    machines = shutil.which("machinectl")
    vmspawn = shutil.which("systemd-vmspawn")
    return [
        Check("Containers (podman)", bool(podman),
              "rootless, no network unless asked" if podman else "podman is not installed"),
        Check("Machines (systemd-nspawn / vmspawn)", bool(machines),
              ("machinectl" + (" and systemd-vmspawn" if vmspawn else "")) if machines
              else "machinectl is not installed"),
    ]
