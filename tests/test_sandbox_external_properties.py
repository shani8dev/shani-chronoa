"""What stops an escape on this machine is not this repository.

`bubblewrap`'s `SECURITY.md` is the clearest statement of the division of
responsibility in sandboxing anywhere in the tree:

> bubblewrap is not a complete, ready-made sandbox with a specific security
> policy. [...] the level of protection between the sandboxed processes and the
> host system is entirely determined by the arguments passed to bubblewrap.
> Whatever program constructs the command-line arguments for bubblewrap [...] is
> responsible for defining its own security model.

Its worked example is **CVE-2017-5226**, a Flatpak app that injected keystrokes
into the parent's terminal with `ioctl(TIOCSTI)`. bubblewrap's position was that
this was a Flatpak vulnerability, not a bubblewrap one - the primitive declines
to speak for whoever builds its argv, and points at whoever builds it instead.

**Measured 2026-10-12 against `shani-chronoa`'s own sandbox**, none of the four
properties that make it safe belong to it:

| escape | what stops it today | whose decision that is |
|---|---|---|
| `ioctl(TIOCSTI)` into the user's terminal | `dev.tty.legacy_tiocsti = 0`; measured `EIO` | the kernel's sysctl |
| raw block devices, via `--dev-bind /dev /dev` (`executor.py:643`) | `brw-rw---- root:disk`, user not in `disk`; measured `EACCES` | the admin's group membership |
| device-mapper control | `crw------- root:root`; measured `EACCES` | the kernel's device mode |

None of it is visible in the code. `sandbox/seccomp.py` filters 29 syscalls and
never mentions `ioctl`; `executor.py` passes `--dev-bind /dev /dev` without a
word about what that exposes. Two ordinary pieces of desktop administration flip
it: `sysctl -w dev.tty.legacy_tiocsti=1`, and adding the user to `disk` (common
on full-disk-encryption installs).

So this file makes the dependency **tested per machine**. A box that lacks one of
these fails here, at test time, rather than at incident time.

Deliberately **not** included: a seccomp rule blocking `TIOCSTI`. The machinery
is already present - `seccomp.py` inspects `arg0` for `ptrace`'s `PTRACE_TRACEME`
carve-out - and the rule would take ten lines. It guards a call the kernel
already refuses, so it would be *counted* as protection while changing nothing,
which is precisely the mistake this file exists to prevent.
"""

import errno
import fcntl
import os
import pathlib
import pty

import pytest

def _find_repo() -> "pathlib.Path | None":
    """Walk up from this file looking for the package, so the file can be run
    from a scratch directory as well as from `tests/`. `CHRONOA_ROOT` overrides
    it, which is how the controls below point the check at a deliberately broken
    copy instead of editing the real tree."""
    import os as _os

    override = _os.environ.get("CHRONOA_ROOT")
    if override:
        candidate = pathlib.Path(override)
        return candidate if candidate.exists() else None
    for parent in [pathlib.Path(__file__).resolve().parent, *pathlib.Path(__file__).resolve().parents]:
        if (parent / "usr" / "lib" / "shani-chronoa").is_dir():
            return parent
    return None


REPO = _find_repo()
SANDBOX = (REPO / "usr" / "lib" / "shani-chronoa" / "shani_chronoa" / "sandbox"
           if REPO else None)

#: `ioctl` request on Linux; `struct winsize`-family is 0x5412's neighbour,
#: this is the terminal-injection one from CVE-2017-5226.
TIOCSTI = 0x5412


def _try_tiocsti(payload: bytes = b"probe"):
    """Attempt the real thing on a real pty of our own, and report what happened.

    Never against the user's own terminal: injected bytes land in its input queue
    and would be typed into whatever they have running. A fresh `openpty()` pair
    is the honest way to ask the kernel the question without touching anything
    that matters.
    """
    master, slave = pty.openpty()
    try:
        try:
            fcntl.ioctl(slave, TIOCSTI, payload)
            return True, "the kernel accepted it"
        except OSError as exc:
            return False, f"{exc.errno} ({errno.errorcode.get(exc.errno)})"
    finally:
        os.close(master)
        os.close(slave)


#: Module scope, because the skip message quotes it and a local would raise
#: `NameError` on exactly the machine the skip exists for.
_BLOCK_PATTERNS = ("nvme*n*", "sd*", "vd*", "xvd*", "dm-*")


def _first_block_device():
    """**Block** devices only - nvme/sd/dm.

    The first version globbed `/dev/*` and accepted any device node, so it picked
    `/dev/acpi_thermal_rel` (a 0600 char device, unreachable) and *skipped* -
    which reads as "nothing to check" rather than "raw storage is closed". A skip
    here would leave the strongest of the three properties unasserted.
    """
    for pattern in _BLOCK_PATTERNS:
        for candidate in sorted(pathlib.Path("/dev").glob(pattern)):
            try:
                if candidate.is_block_device():
                    return candidate
            except OSError:
                continue
    return None


class TestThePropertyThisRepositoryDoesNotOwn:
    def test_terminal_injection_is_refused_by_the_kernel(self):
        injected, detail = _try_tiocsti()
        assert not injected, (
            f"ioctl(TIOCSTI) was accepted on this machine ({detail}), so a "
            f"sandboxed skill that can open a pty can type into the user's "
            f"terminal. The kernel blocks this by default since 6.2; something "
            f"here has turned it back on (`dev.tty.legacy_tiocsti=1`). The "
            f"sandbox does not filter ioctl, so nothing downstream stops it."
        )

    def test_raw_block_devices_are_not_reachable_by_this_user(self):
        """`--dev-bind /dev /dev` hands the sandbox every device node on the
        host. What makes that survivable here is DAC: block devices are
        `root:disk` mode 0660 and this user is not in `disk`."""
        device = _first_block_device()
        if device is None:
            pytest.skip(f"no block device under /dev ({_BLOCK_PATTERNS!r}), so "
                        f"there is "
                        f"nothing to check on this machine")
        try:
            handle = os.open(device, os.O_RDWR)
        except OSError as exc:
            # **The property holding is a PASS.** It was a skip in the first
            # version, which meant the strongest of the three assertions never
            # asserted - and a machine with no block device at all skipped for the
            # same reason, so the two cases were indistinguishable in the output.
            assert exc.errno in (errno.EACCES, errno.EPERM), (
                f"{device} could not be opened, but not for the reason this test "
                f"claims to be measuring: {exc.strerror} (errno {exc.errno}). "
                f"The point is DAC denying group 'disk', not a broken device."
            )
            return
        os.close(handle)
        pytest.fail(
            f"{device} opened read-write. `executor.py` binds the host's whole "
            f"/dev into every sandbox, so a skill can now write to raw storage "
            f"and nothing in this repository would stop it. The DAC property has "
            f"lapsed - this user is in `disk` - not the sandbox that failed.")


class TestTheDependencyIsReal:
    def test_the_sandbox_does_not_itself_block_ioctl(self):
        """**The assertion that makes this file honest.**

        Without it, a reader could reasonably assume the tests above describe
        protections this repository enforces. They do not - the source says so
        nowhere. If someone later adds the `TIOCSTI` rule to `seccomp.py`, this
        test fails and tells them the external dependency can be retired, which
        is the moment that dependency stops being a risk.
        """
        if SANDBOX is None:
            pytest.skip("the shani-chronoa package is not reachable from here")
        source = (SANDBOX / "seccomp.py").read_text(encoding="utf-8")
        blocked = {line.split('"')[1] for line in source.splitlines()
                   if "BlockedSyscall(" in line and '"' in line}
        assert "ioctl" not in blocked, (
            f"seccomp.py now blocks ioctl ({len(blocked)} syscalls listed). The "
            f"external dependency this file exists to track is retired or "
            f"narrowed - reread this file's module docstring, measure which of "
            f"the four properties are still someone else's, and rewrite the "
            f"claim. Leaving it would mean this file keeps asserting that the "
            f"sandbox leans on the kernel for something it now enforces itself."
        )