"""llama.cpp, whisper and piper are started with **physical** cores, everywhere.

**The bug this exists to prevent, measured.** `local_llm.server_args` passed
`os.cpu_count() - 1`, which is **7** on this 4-core / 8-thread i7-1165G7.
`llama-bench`, quiet box, `Qwen3-8B-Q4_K_M`, `-r 2`:

| `-t` | 3 | **4** | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|
| decode tok/s | 3.23 | **3.73** | 3.70 | 3.62 | 2.50 | 2.32 |

`-t 8` is **1.61x slower** than `-t 4`, and the value the code actually chose
sat in the worst part of the curve - about a third of the available speed,
thrown away on every server start, for the life of the product.

This is not a private discovery. llama.cpp's own maintainer put it in
ggml-org/llama.cpp#34 (*"speed with command line config -t 4. If use -t 8,
half speed"*), the upstream performance-tuning page calls incorrect thread
settings *"the #1 cause of slow inference"*, and discussion #394 says the same.

**Six launchers had the same line**, which is what makes this a property to
assert rather than a bug to fix once:

| module | binary | how it was hidden |
|---|---|---|
| `local_llm` | llama-server | `-t cpu_count()-1` = 7 |
| `local_vision` | llama-server | same line |
| `imagegen` | stable-diffusion.cpp | same line |
| `local_embed` | llama-server | `min(4, ...)` — masked here, half the cores on a big machine |
| `recordings` | whisper.cpp | `min(4, ...)` — same |
| `wakeword` | whisper.cpp | `min(4, ...)` — same |
| `tts` | piper | `min(4, ...)` — same |

The `min(4, ...)` four looked *correct* on this box: `min(4, 7)` is 4, which
happens to be the physical count here. That is the reason the pattern is
asserted rather than the four lines fixed - a value that is right by accident on
the machine you tested on is the one that is wrong everywhere else.
"""

from __future__ import annotations

import pathlib
import re
import sys

import pytest

PKG = pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa" / "shani_chronoa"
sys.path.insert(0, str(PKG.parent))

from shani_chronoa import (  # noqa: E402
    hardware_profile,
    imagegen,
    local_embed,
    local_llm,
    local_vision,
)


def _sysfs(tmp_path, layout):
    """A synthetic `/sys/devices/system/cpu` with cpuN/topology/thread_siblings_list."""
    root = tmp_path / "cpu"
    for cpu, siblings in layout.items():
        path = root / cpu / "topology"
        path.mkdir(parents=True)
        (path / "thread_siblings_list").write_text(siblings + "\n")
    return root


class TestPhysicalCoresCountsCoresNotThreads:
    def test_it_counts_unique_sibling_groups(self, tmp_path, monkeypatch):
        """4 cores with 2 threads each = 4, not 8."""
        monkeypatch.setattr(hardware_profile, "CPU_SYSFS", str(_sysfs(tmp_path, {
            "cpu0": "0,4", "cpu1": "1,5", "cpu2": "2,6", "cpu3": "3,7"})))
        monkeypatch.setattr(hardware_profile.os, "cpu_count", lambda: 8)
        assert hardware_profile.physical_cores() == 4

    def test_a_machine_without_smt_reports_every_cpu(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hardware_profile, "CPU_SYSFS", str(_sysfs(tmp_path, {
            "cpu0": "0", "cpu1": "1", "cpu2": "2", "cpu3": "3"})))
        monkeypatch.setattr(hardware_profile.os, "cpu_count", lambda: 4)
        assert hardware_profile.physical_cores() == 4

    def test_two_threads_of_one_core_are_one_core(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hardware_profile, "CPU_SYSFS", str(_sysfs(tmp_path, {
            "cpu0": "0,1", "cpu2": "2", "cpu4": "4,5"})))
        monkeypatch.setattr(hardware_profile.os, "cpu_count", lambda: 6)
        assert hardware_profile.physical_cores() == 3

    def test_unreadable_sysfs_falls_back_to_the_thread_count(self, tmp_path, monkeypatch):
        """The fallback is the old behaviour and must not be zero.

        A `0` here becomes `-t 0`, which llama-server rejects - so a machine with
        an unreadable `/sys` would have **no server at all** rather than a slow
        one.
        """
        monkeypatch.setattr(hardware_profile, "CPU_SYSFS", str(tmp_path / "nope"))
        monkeypatch.setattr(hardware_profile.os, "cpu_count", lambda: 6)
        assert hardware_profile.physical_cores() == 6

    def test_a_machine_that_reports_no_cpus_is_still_one(self, monkeypatch):
        monkeypatch.setattr(hardware_profile, "CPU_SYSFS", "")
        monkeypatch.setattr(hardware_profile.os, "cpu_count", lambda: 0)
        assert hardware_profile.physical_cores() >= 1

    def test_it_never_exceeds_the_thread_count(self):
        """**The control.** Counting sibling groups cannot come out above the
        threads they are made of; without this the tests above could pass on a
        rule that is wrong in the other direction."""
        import os
        assert hardware_profile.physical_cores() <= max(1, os.cpu_count() or 1)

    def test_on_this_machine_it_is_the_smaller_of_the_two(self):
        """The live reading - which is the whole point of the fix. Skipped, not
        failed, on a box without SMT: there `min(4, n)` and the physical count
        are the same number and the defect is genuinely invisible."""
        import os
        if (os.cpu_count() or 1) > 1:
            assert hardware_profile.physical_cores() < os.cpu_count()


class TestEveryLauncherThatPassesThreadCountUsesIt:
    """The property, not the one line. Seven call sites had the same defect."""

    @pytest.mark.parametrize("label,call", [
        ("local_llm", lambda: local_llm.server_args(devices=[])),
        ("local_vision", lambda: local_vision.server_args(
            list(local_vision.MODELS)[0], devices=[])),
        ("imagegen", lambda: imagegen.server_args()),
        ("local_embed", lambda: local_embed.server_args()),
    ])
    def test_the_thread_flag_is_the_physical_count(self, label, call, monkeypatch):
        monkeypatch.setattr(hardware_profile, "physical_cores", lambda: 4)
        argv = call()
        assert "-t" in argv, f"{label}: no -t at all, so the count is whatever it defaults to"
        assert argv[argv.index("-t") + 1] == "4", (
            f"{label} passes -t {argv[argv.index('-t') + 1]}, not the physical core count")

    @pytest.mark.parametrize("label,call", [
        ("local_llm", lambda: local_llm.server_args(devices=[])),
        ("local_vision", lambda: local_vision.server_args(
            list(local_vision.MODELS)[0], devices=[])),
        ("local_embed", lambda: local_embed.server_args()),
    ])
    def test_a_gpu_machine_still_offloads_and_does_not_pin_threads(
            self, label, call, monkeypatch):
        """`-t` is the CPU-only path: with a GPU the layers are offloaded and the
        thread count is not this module's business, so the fix must not have
        broken the branch that does not take `-t`.

        **`imagegen` is deliberately absent**: it drives stable-diffusion.cpp's
        `sd-server`, which has no `-ngl` flag and no offload branch - its argv
        is `['-m', ..., '--listen-ip', ..., '--listen-port', ..., '--mmap',
        '-t', '4']`. Including it made this assertion fail for the right reason
        about the wrong binary.
        """
        monkeypatch.setattr(hardware_profile, "physical_cores", lambda: 4)
        argv = call()
        assert ("-t" in argv) == (argv[argv.index("-ngl") + 1] == "0"), (
            f"{label}: -t and -ngl 0 must agree - one without the other is a "
            f"launcher that is either CPU-only by accident or threaded by accident")




class TestNoCallSiteGoesBackToTheThreadCount:
    """A source-level guard, because the defect was seven copies of a line.

    The argv tests above already cover the four callable launchers. This one
    names the *pattern*, so an eighth launcher written tomorrow with the old
    line says so here rather than quietly losing a third of its speed on a
    machine nobody benchmarks.

    **It is also the only thing that can catch the `min(4, ...)` sites on this
    machine**, and that was nearly missed. Restoring `min(4, cpu_count()-1)` in
    `local_embed` leaves the argv test *green*: `min(4, 7)` is 4, which is this
    machine's physical core count, so the mutation is an exact equivalent here.
    Measured, not assumed - with the old line restored the module returned
    `-t 4` and the suite passed. On a 16-thread machine the same line is half
    the cores and the argv test would catch it, which is precisely why a check
    that only works on the machine that found the bug is not the check.
    """

    _PATTERN = re.compile(r'(-t["\']\s*,\s*str\(|num-threads=)[^)\n]*cpu_count')

    def test_no_module_derives_a_thread_count_from_cpu_count(self):
        bad = []
        for path in PKG.rglob("*.py"):
            for number, line in enumerate(path.read_text(
                    encoding="utf-8", errors="replace").splitlines(), 1):
                if self._PATTERN.search(line):
                    bad.append(f"{path.name}:{number}: {line.strip()}")
        assert not bad, (
            "a launcher derives its thread count from os.cpu_count(), which is "
            "the *thread* count and measures 1.61x slower than the physical "
            "one:\n" + "\n".join(bad))

    def test_the_guard_can_see_the_line_it_forbids(self):
        """**The control.** A pattern that has never matched is a pattern that
        cannot fail, and this one reads a tree written by other people."""
        assert self._PATTERN.search('"-t", str(max(1, (os.cpu_count() or 2) - 1))]')
        assert self._PATTERN.search('f"--num-threads={min(4, os.cpu_count() or 2)}",')
        assert not self._PATTERN.search('"-t", str(hardware_profile.physical_cores())]')

    @pytest.mark.parametrize("name", ["recordings", "wakeword", "tts"])
    def test_the_non_callable_launchers_ask_for_physical_cores(self, name):
        """whisper.cpp's `-t` and piper's `--num-threads` are the same lever on
        the same two engines, and all three used `min(4, ...)`.

        Not callable in a unit test: each builds its argv only once a real model
        file and binary exist, so the source is the only place the thread count
        can be seen from here.
        """
        text = (PKG / f"{name}.py").read_text(encoding="utf-8")
        assert "physical_cores()" in text, (
            f"{name}.py builds a thread count and does not use the measured one")


class TestOneTableOfMeasurementsNotTwo:
    def test_local_llm_re_exports_rather_than_reimplementing(self):
        """Two copies of the -t table is a table that will disagree with the
        first one - the failure this whole file is about."""
        assert local_llm.physical_cores() == hardware_profile.physical_cores()

    def test_hardware_profile_reports_physical_cores(self):
        """The class attribute is the core count the model choice is made on, so
        it must be the same number; `os.cpu_count()` there was the same defect
        wearing a different hat."""
        from shani_chronoa.hardware_profile import HardwareProfile
        assert HardwareProfile().cpu_cores == hardware_profile.physical_cores()
