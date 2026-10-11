"""Tests for `graphics_stack` - hardware acceleration, and the two halves.

The fixtures are **captured from a real ShaniOS slot** (`nspawn`, `card1`,
`DRIVER=i915`, `PCI_ID=8086:9A49`), because the whole design is shaped by what
that machine actually did:

| what was measured | what it forces |
|---|---|
| `/sys/class/drm/card1` plus `card1-DP-1..4` | connectors are **not** adapters |
| `DRIVER=i915` in `device/uevent` | the kernel half needs no subprocess at all |
| `DISPLAY` unset -> `Error: unable to open display`, rc=255 | no session is a *reason*, not a fault |
| **`eglinfo` rc=139, three runs running, zero output, empty stderr** | a **crash** must never become a fact |
| `EGL_PLATFORM=surfaceless eglinfo` -> still rc=139 | the fallback does not rescue it |
| no Xvfb on the image | `glxinfo` cannot be made to answer in a slot |

The segfault is the reason this file exists. `subprocess` reports a child
killed by signal N as **-N**, so a check written as `if rc != 0` classifies a
crash as an ordinary error, and the natural reading of "no output" - "there is
no renderer" - is the opposite of the truth.
"""

from __future__ import annotations

import subprocess

import pytest

from shani_chronoa.skills import graphics_stack as gs

# --- captured from the real slot: /sys/class/drm ----------------------------
CARD1_UEVENT = """\
DRIVER=i915
PCI_CLASS=30000
PCI_ID=8086:9A49
PCI_SUBSYS_ID=17AA:5088
PCI_SLOT_NAME=0000:00:02.0
MODALIAS=pci:v00008086d00009A49sv000017AAsd00005088bc03sc00i00
"""

# --- captured: glxinfo -B against a real GPU session ------------------------
GLXINFO_HARDWARE = """\
OpenGL vendor string: Intel
OpenGL renderer string: Mesa Intel(R) UHD Graphics 620 (KBL GT2)
OpenGL core profile version string: 4.6 (Core Profile) Mesa 22.3.6
OpenGL version string: 4.6 (Core Profile) Mesa 22.3.6
"""

# --- captured: glxinfo -B with Mesa having fallen back to software ----------
GLXINFO_SOFTWARE = """\
OpenGL vendor string: Mesa
OpenGL renderer string: llvmpipe (LLVM 15.0.7, 256 bits)
OpenGL core profile version string: 4.5 (Core Profile) Mesa 22.3.6
"""


@pytest.fixture
def drvfs(monkeypatch):
    """Answer /sys/class/drm reads, and the realpath of the driver link."""

    def install(cards=None, connectors=("card1-DP-1", "card1-DP-2"),
                uevent=CARD1_UEVENT, driver_link="/sys/bus/pci/drivers/i915",
                uevent_fails=False):
        listing = list(cards if cards is not None else ["card1"])
        listing = listing + list(connectors)
        real = os_realpath(driver_link)
        driver_name = driver_link.rsplit("/", 1)[-1]
        monkeypatch.setattr(gs.os, "listdir",
                            lambda p: listing if p == "/sys/class/drm" else [])
        monkeypatch.setattr(gs.os.path, "realpath", lambda p: real)
        monkeypatch.setattr(gs.os.path, "basename",
                            lambda p: driver_name if "drivers" in p else p)
        if uevent_fails:
            def boom(*a, **k):
                raise OSError("no such file")
            monkeypatch.setattr("builtins.open", boom)
        else:
            # The uevent answers what the caller passed, not the captured
            # default - a fixture that always returns card1's real driver makes
            # every "unknown driver" test answer `i915` no matter what it asked
            # for, which reads as the skill being wrong when it is the fixture.
            def fake_open(p, **k):
                if "uevent" in str(p):
                    return _fake(uevent)
                return _fake("")
            monkeypatch.setattr("builtins.open", fake_open)
        return listing
    return install


def os_realpath(p):
    return p


class _fake:
    """A stand-in for an open file the code iterates line by line.

    `_adapters()` does `for line in fh` on the uevent, so a stub that only
    answers `.read()` raises `TypeError: not iterable` - which is a fixture that
    cannot model the thing it is standing in for.
    """

    def __init__(self, text):
        self._text = text

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(self._text.splitlines(True))

    def read(self):
        return self._text


@pytest.fixture
def glx(monkeypatch):
    """Answer `glxinfo` by outcome, the four shapes the slot really showed."""

    def install(rc=0, out="", err="", present=True, display=":0"):
        monkeypatch.setattr(gs.shutil, "which",
                            lambda n: "/usr/bin/glxinfo" if present else None)
        monkeypatch.setattr(gs.os, "environ",
                            dict(gs.os.environ, DISPLAY=display) if display
                            else {k: v for k, v in gs.os.environ.items()
                                  if k != "DISPLAY"})
        calls = []

        def run(argv, **kwargs):
            calls.append(list(argv))
            return subprocess.CompletedProcess(argv, rc, out, err)
        monkeypatch.setattr(gs.subprocess, "run", run)
        return calls
    return install


# --------------------------------------------------------------------------
# The crash is not an error, and neither is "no output"
# --------------------------------------------------------------------------

class TestTheCrashIsNotAGraphicsFact:

    def test_a_child_killed_by_a_signal_is_reported_as_a_crash(self, glx):
        # Measured: eglinfo rc=139 from the shell is 128+11, and subprocess
        # reports the same death as -11. A check written as `rc != 0` files this
        # under "it could not answer", and the reader then concludes there is
        # no renderer.
        import signal
        glx(rc=-signal.SIGSEGV, out="", err="")
        renderer, why = gs._glxinfo()
        assert renderer is None
        assert "CRASHED" in why
        assert "SIGSEGV" in why
        assert "not evidence about the GPU" in why

    def test_a_crash_never_becomes_the_renderer_string(self, glx):
        # The failure this skill exists to avoid: a crashing tool printed no
        # output, and "no output" was reported as "no renderer".
        import signal
        glx(rc=-signal.SIGSEGV)
        out = gs._describe()
        assert "llvmpipe" not in out
        assert "Software renderer" not in out, (
            "a crash is not evidence that rendering is software - that is the "
            "opposite of what a crash tells you")

    def test_a_crash_is_named_distinctly_from_an_ordinary_failure(self, glx):
        import signal
        glx(rc=-signal.SIGSEGV)
        crash = gs._glxinfo()[1]
        glx(rc=255, err="Error: unable to open display\n")
        refused = gs._glxinfo()[1]
        assert crash != refused, (
            "a crash and a refusal are different statements; merging them is "
            "how a crash turns into a claim")
        assert "unable to open display" in refused

    def test_an_ordinary_non_zero_exit_is_not_called_a_crash(self, glx):
        # Measured: glxinfo -B with no DISPLAY exits 255 with a message.
        glx(rc=255, err="Error: unable to open display \n")
        renderer, why = gs._glxinfo()
        assert renderer is None
        assert "CRASHED" not in why, "255 is a refusal, not a signal"

    def test_a_silent_success_is_not_reported_as_no_renderer(self, glx):
        # glxinfo ran, exited 0 and said nothing about the renderer. That is a
        # third thing again - not "there is no renderer".
        glx(rc=0, out="")
        renderer, why = gs._glxinfo()
        assert renderer is None
        assert "not the same as there being none" in why

    def test_it_says_no_display_before_running_the_command(self, glx):
        # Measured: DISPLAY unset -> "Error: unable to open display", rc=255.
        # Asking first avoids running a command whose only outcome is that.
        calls = glx(display="")
        renderer, why = gs._glxinfo()
        assert calls == [], "no DISPLAY means glxinfo cannot answer at all"
        assert "DISPLAY" in why

    def test_a_missing_glxinfo_names_the_package_pacman_owns_it_in(
            self, monkeypatch):
        monkeypatch.setattr(gs.shutil, "which", lambda n: None)
        monkeypatch.setattr(gs.os, "environ",
                            dict(gs.os.environ, DISPLAY=":0"))
        renderer, why = gs._glxinfo()
        assert renderer is None and "mesa-utils" in why
        assert "owned by" in why, (
            "the package name is measured from pacman, not guessed - a wrong "
            "one here is a reason nobody can act on")


# --------------------------------------------------------------------------
# Reading the renderer when it IS there
# --------------------------------------------------------------------------

class TestTheRenderer:

    def test_a_hardware_renderer_is_read_from_the_line_whole(self, glx):
        glx(out=GLXINFO_HARDWARE)
        renderer, why = gs._glxinfo()
        assert renderer == "Mesa Intel(R) UHD Graphics 620 (KBL GT2)"
        assert why == ""

    def test_llvmpipe_is_recognised_as_software(self, glx):
        # The answer the whole skill exists for.
        glx(out=GLXINFO_SOFTWARE)
        assert "llvmpipe" in gs._describe()
        assert "SOFTWARE renderer" in gs._describe()

    @pytest.mark.parametrize("name", ["softpipe", "swrast", "SWRAST",
                                      "Software Rasterizer"])
    def test_every_spelling_of_the_software_family_counts(self, glx, name):
        # Measured on real Mesa output these appear as `llvmpipe (LLVM ...)` or
        # bare. Requiring one exact string is how a real fallback is missed.
        glx(out=f"OpenGL renderer string: {name}\n")
        assert "SOFTWARE renderer" in gs._describe()

    def test_a_hardware_renderer_is_not_called_software(self, glx):
        glx(out=GLXINFO_HARDWARE)
        out = gs._describe()
        assert "SOFTWARE renderer" not in out
        assert "Hardware accelerated" in out

    def test_both_halves_together_say_accelerated_once(self, drvfs, glx):
        # **The case the conflation bug needs, and the one that was untested.**
        # A mutation that made the kernel driver imply acceleration survived
        # every other test, because each of them had the renderer *unknown* (no
        # glxinfo here) or software. Only this one has a healthy `i915` **and** a
        # hardware renderer - and it is also the normal case on a real desktop,
        # which is why a defect there would be invisible in a slot.
        drvfs()
        glx(out=GLXINFO_HARDWARE)
        out = gs._describe()
        assert "i915" in out
        assert "Hardware accelerated" in out
        assert "SOFTWARE renderer" not in out
        assert "not known" not in out, (
            "with both halves answered there is nothing left unknown")

    def test_a_software_renderer_under_a_healthy_driver_is_still_software(
            self, drvfs, glx):
        # The other half of that pair, and the one that actually matters: Mesa
        # fell back to llvmpipe *while* the kernel driver is perfectly good.
        # This is precisely the machine where reporting the kernel driver as the
        # answer would be the confident wrong answer.
        drvfs()
        glx(out=GLXINFO_SOFTWARE)
        out = gs._describe()
        assert "i915" in out, "the kernel half is still true and is still said"
        assert "SOFTWARE renderer" in out, (
            "a good kernel driver does not make the rendering hardware")


# --------------------------------------------------------------------------
# The two halves, and never one standing in for the other
# --------------------------------------------------------------------------

class TestSysfsIsTheKernelHalfOnly:

    def test_the_kernel_driver_is_read_without_any_subprocess(self, drvfs, glx,
                                                              monkeypatch):
        # The reason sysfs is the primary path at all: it answers with no
        # display, no Mesa and no subprocess, which is the state of every slot.
        drvfs()
        glx(present=False)
        def forbidden(*a, **k):
            raise AssertionError("sysfs must not shell out")
        monkeypatch.setattr(gs.subprocess, "run", forbidden)
        out = gs._describe()
        assert "i915" in out

    def test_a_healthy_kernel_driver_is_not_reported_as_acceleration(self, drvfs,
                                                                     glx):
        # The conflation this skill is built around. i915 present + renderer
        # unknown must NOT read as "accelerated".
        drvfs()
        glx(display="")
        out = gs._describe()
        assert "i915" in out
        assert "not known" in out
        assert "Hardware accelerated" not in out

    def test_connectors_are_not_counted_as_adapters(self, drvfs, glx):
        # Measured: the slot has card1 plus card1-DP-1..4. Counting connectors
        # reports four GPUs that do not exist.
        drvfs(connectors=("card1-DP-1", "card1-DP-2", "card1-DP-3", "card1-DP-4"))
        out = gs._describe()
        assert "card1-DP" not in out, "a connector was listed as an adapter"
        assert out.count("kernel driver:") == 1

    def test_an_unknown_kernel_driver_is_not_called_healthy(self, drvfs, glx):
        drvfs(uevent="DRIVER=simpledrm\nPCI_ID=0000:0000\n", driver_link="/x/y")
        glx(display="")
        out = gs._describe()
        assert "not known" in out
        assert "is a real driver for real hardware" not in out

    def test_no_adapter_at_all_is_reported_plainly(self, drvfs, glx, monkeypatch):
        drvfs(cards=[], connectors=())
        glx(present=False)
        out = gs._describe()
        assert "No DRM adapter" in out
        assert "card" not in out.split("No DRM adapter")[0]


# --------------------------------------------------------------------------
# Reachability
# --------------------------------------------------------------------------

class TestItIsReachable:

    def test_the_handler_is_importable_by_name(self):
        assert gs._run.__name__ == "_run"
        assert gs._run.__name__.isidentifier()

    def test_the_schema_registers_under_its_own_name(self):
        assert gs.SKILLS[0].name == "graphics_stack"
        assert gs.SCHEMA["function"]["name"] == "graphics_stack"

    def test_it_takes_no_parameters(self):
        # There is no path to ask about: the question is about this machine.
        params = gs.SCHEMA["function"]["parameters"]
        assert params["properties"] == {}

    def test_it_answers_rather_than_asking(self):
        assert "graphics" in gs._run({}).lower()