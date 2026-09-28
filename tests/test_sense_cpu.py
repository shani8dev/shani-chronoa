"""The `cpu` sense, and the load-is-not-a-percentage trap.

The trap: on this machine `/proc/loadavg` reads around `11.70` with 8 logical
CPUs. Read as a percentage that is "1170% CPU" — a machine using eleven cores'
worth. It is not. The kernel counts *runnable and uninterruptible tasks*, not
CPU time, so the same load of 11 is saturation on one core and near-idle on
sixty-four. Every test here pins the ratio against the online CPU count, never
a percentage.

Also pinned, from real readings on this machine:

- `scaling_governor=powersave` **and**
  `energy_performance_preference=balance_performance` — two different settings,
  both present, neither is "the power setting" on its own.
- `scaling_cur_freq` is 3.4–3.47 GHz against a 4.70 GHz maximum, and moves
  between reads. It is a cached value from the last governor decision.
- `/sys/devices/system/cpu/online` is a range spec (`0-7`), not a count.
"""

import pytest

from shani_chronoa.senses import cpu


def _tree(tmp_path, monkeypatch):
    """A fake /sys/devices/system/cpu plus /proc, and point the module at it."""
    root = tmp_path / "cpu"
    proc = tmp_path / "proc"
    root.mkdir()
    proc.mkdir()
    monkeypatch.setattr(cpu, "_CPU", root)
    monkeypatch.setattr(cpu, "_LOADAVG", proc / "loadavg")
    monkeypatch.setattr(cpu, "_UPTIME", proc / "uptime")
    monkeypatch.setattr(cpu, "_MEMINFO", proc / "meminfo")
    return root, proc


def _policy(root, cpu_name, *, governor="powersave", preference="balance_performance",
            cur=None, maximum=None, prefs="default performance balance_performance"):
    path = root / cpu_name / "cpufreq"
    path.mkdir(parents=True, exist_ok=True)
    if governor is not None:
        (path / "scaling_governor").write_text(governor + "\n")
    if preference is not None:
        (path / "energy_performance_preference").write_text(preference + "\n")
    if cur is not None:
        (path / "scaling_cur_freq").write_text(f"{cur}\n")
    if maximum is not None:
        (path / "scaling_max_freq").write_text(f"{maximum}\n")
    if prefs is not None:
        (path / "energy_performance_available_preferences").write_text(prefs + "\n")
    return path


@pytest.fixture
def granted(monkeypatch):
    monkeypatch.setattr(cpu.ChronoaConfig, "sense_allowed",
                        lambda self, s: True)


class TestLoadIsNotAPercentage:
    def test_load_is_rated_against_the_online_cpu_count(self, tmp_path, monkeypatch, granted):
        """The headline case: 11.70 load on 8 CPUs, which a percentage-reading
        parser would report as 1170% CPU usage."""
        root, proc = _tree(tmp_path, monkeypatch)
        (root / "online").write_text("0-7\n")
        (root / "possible").write_text("0-7\n")
        _policy(root, "cpu0")
        (proc / "loadavg").write_text("11.70 11.45 11.27 4/1315 1613820\n")
        (proc / "meminfo").write_text("MemTotal: 33554432 kB\nMemAvailable: 25000000 kB\n")
        content = cpu._run({}).content
        assert "11.70" in content
        assert "1.46 per online CPU" in content
        load_line = [l for l in content.splitlines() if l.startswith("load average")][0]
        assert "%" not in load_line, (
            f"the load line itself renders a percentage: {load_line!r} - the "
            f"kernel never provides one"
        )
        assert "not CPU" in content and "percentage" in content

    def test_one_core_and_sixty_four_cores_are_different_machines(self, tmp_path, monkeypatch, granted):
        root, proc = _tree(tmp_path, monkeypatch)
        (root / "online").write_text("0\n")
        (proc / "loadavg").write_text("8.00 8.00 8.00 1/50 1\n")
        one_core = cpu.read_load()["1min"]
        monkeypatch.setattr(cpu, "_CPU", tmp_path / "cpu2")
        (tmp_path / "cpu2").mkdir()
        (tmp_path / "cpu2" / "online").write_text("0-63\n")
        many = cpu.read_counts()["online"]
        assert one_core == 8.0
        assert many == 64
        assert 8 / one_core == 1.0 and 8 / many == 0.125, (
            "the same load is saturation on one core and idle on sixty-four; "
            "a raw number cannot express both"
        )

    def test_the_runnable_split_is_reported(self, tmp_path, monkeypatch, granted):
        """Load of 11 from 4 runnable tasks means too many threads, not a hot
        CPU — and the split is the cheapest way to say so."""
        root, proc = _tree(tmp_path, monkeypatch)
        (root / "online").write_text("0-7\n")
        (proc / "loadavg").write_text("11.00 11.00 11.00 4/1315 1\n")
        _policy(root, "cpu0")
        assert "4 runnable of 1315 tasks" in cpu._run({}).content


class TestCpuCounts:
    def test_a_range_spec_is_expanded_not_counted(self, tmp_path, monkeypatch):
        root, _ = _tree(tmp_path, monkeypatch)
        (root / "online").write_text("0-7\n")
        (root / "possible").write_text("0-7\n")
        assert cpu.read_counts()["online"] == 8, "'0-7' counted as one CPU"

    def test_a_ragged_range_spec_is_expanded(self, tmp_path, monkeypatch):
        root, _ = _tree(tmp_path, monkeypatch)
        (root / "online").write_text("0-3,8-11\n")
        assert cpu.read_counts()["online"] == 8

    def test_hotplugged_cores_are_counted_as_offline(self, tmp_path, monkeypatch):
        root, _ = _tree(tmp_path, monkeypatch)
        (root / "online").write_text("0-3\n")
        (root / "possible").write_text("0-7\n")
        found = cpu.read_counts()
        assert (found["online"], found["possible"]) == (4, 8)

    def test_a_missing_online_file_falls_back_to_counting_directories(self, tmp_path, monkeypatch):
        root, _ = _tree(tmp_path, monkeypatch)
        for name in ("cpu0", "cpu1", "cpu2"):
            (root / name).mkdir()
        assert cpu.read_counts()["online"] == 3


class TestGovernorAndFrequency:
    def test_both_settings_are_reported_not_merged(self, tmp_path, monkeypatch, granted):
        """Measured on this machine: a governor AND an energy preference, both
        set, neither of which is 'the power setting'."""
        root, proc = _tree(tmp_path, monkeypatch)
        (root / "online").write_text("0-7\n")
        (proc / "loadavg").write_text("1.00 1.00 1.00 1/50 1\n")
        _policy(root, "cpu0", governor="powersave", preference="balance_performance")
        content = cpu._run({}).content
        assert "governor powersave" in content
        assert "energy preference balance_performance" in content

    def test_a_driver_with_only_a_governor_still_reports(self, tmp_path, monkeypatch, granted):
        root, proc = _tree(tmp_path, monkeypatch)
        (root / "online").write_text("0-3\n")
        (proc / "loadavg").write_text("1.00 1.00 1.00 1/50 1\n")
        _policy(root, "cpu0", governor="ondemand", preference=None, prefs=None)
        content = cpu._run({}).content
        assert "governor ondemand" in content
        assert "energy preference" not in content

    def test_the_cached_frequency_is_labelled_as_cached(self, tmp_path, monkeypatch, granted):
        root, proc = _tree(tmp_path, monkeypatch)
        (root / "online").write_text("0-7\n")
        (proc / "loadavg").write_text("1.00 1.00 1.00 1/50 1\n")
        _policy(root, "cpu0", cur=3400000, maximum=4700000)
        content = cpu._run({}).content
        assert "3.40 GHz" in content and "4.70 GHz" in content
        assert "cached value" in content, (
            "a value the kernel only updates on a governor decision was "
            "presented as a live measurement"
        )

    def test_a_hybrid_cpu_is_not_reduced_to_its_first_core(self, tmp_path, monkeypatch, granted):
        """A P-core/E-core laptop has a different policy per core type."""
        root, proc = _tree(tmp_path, monkeypatch)
        (root / "online").write_text("0-7\n")
        (proc / "loadavg").write_text("1.00 1.00 1.00 1/50 1\n")
        _policy(root, "cpu0", preference="performance")
        _policy(root, "cpu4", preference="power")
        content = cpu._run({}).content
        assert "2 distinct cpu policies" in content
        assert "performance" in content and "power" in content

    def test_identical_policies_are_merged(self, tmp_path, monkeypatch):
        root, _ = _tree(tmp_path, monkeypatch)
        _policy(root, "cpu0", preference="balance_power")
        _policy(root, "cpu1", preference="balance_power")
        assert len(cpu._policies()) == 1, "identical policies were not merged"


class TestMemory:
    def test_available_is_used_not_free(self, tmp_path, monkeypatch, granted):
        root, proc = _tree(tmp_path, monkeypatch)
        (root / "online").write_text("0-7\n")
        (proc / "loadavg").write_text("1.00 1.00 1.00 1/50 1\n")
        (proc / "meminfo").write_text(
            "MemTotal: 33554432 kB\nMemFree: 20000000 kB\nMemAvailable: 25000000 kB\n"
        )
        _policy(root, "cpu0")
        content = cpu._run({}).content
        assert "23.8 GiB available of 32.0 GiB" in content

    def test_swap_is_reported_when_present(self, tmp_path, monkeypatch, granted):
        root, proc = _tree(tmp_path, monkeypatch)
        (root / "online").write_text("0-7\n")
        (proc / "loadavg").write_text("1.00 1.00 1.00 1/50 1\n")
        (proc / "meminfo").write_text(
            "MemTotal: 33554432 kB\nMemAvailable: 25000000 kB\n"
            "SwapTotal: 8388608 kB\nSwapFree: 7999999 kB\n"
        )
        _policy(root, "cpu0")
        assert "swap: 0.4 GiB of 8.0 GiB in use" in cpu._run({}).content


class TestDegradeRatherThanRefuse:
    def test_unreadable_everything_is_undetermined_not_absent(self, tmp_path, monkeypatch, granted):
        root, _ = _tree(tmp_path, monkeypatch)
        result = cpu._run({})
        assert isinstance(result, str)
        assert "undetermined" in result
        # The disclaimer explains what was unreadable; it must not *claim* the
        # machine lacks a processor, which is the opposite reading.
        assert "fact about what could be read" in result

    def test_a_malformed_loadavg_is_refused(self, tmp_path, monkeypatch, granted):
        _, proc = _tree(tmp_path, monkeypatch)
        (proc / "loadavg").write_text("not numbers here\n")
        assert cpu.read_load() is None

    def test_a_truncated_loadavg_is_refused(self, tmp_path, monkeypatch):
        _, proc = _tree(tmp_path, monkeypatch)
        (proc / "loadavg").write_text("1.00\n")
        assert cpu.read_load() is None

    def test_it_refuses_when_consent_is_off(self, monkeypatch):
        monkeypatch.setattr(cpu.ChronoaConfig, "sense_allowed",
                            lambda self, s: False)
        assert isinstance(cpu._run({}), str)
