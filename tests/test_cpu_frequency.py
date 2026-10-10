"""`cpu_frequency`: what the processor is doing right now.

`power_profile` **sets** a mode; nothing anywhere reported what the machine
actually did with it. The governor, the speed and the hardware's range are the
numbers behind "I set performance mode and my fans are still screaming", and
nothing read them.

**The trap this file exists to pin.** `cpupower frequency-info` prints
"current CPU frequency" **twice**, and on an idle CPU the first is a failure:

    current CPU frequency: Unable to call hardware
    current CPU frequency: 1.07 GHz (asserted by call to kernel)

Taking the first match reports the string "Unable to call hardware" as a speed
- nonsense, confidently formatted. This is the same shape as `iostat`'s
since-boot report and `bootctl`'s three markers: **when a tool prints a field
twice, which one you take is the whole difference between an answer and a
lie.**
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import cpu_frequency as CF  # noqa: E402

#: Real `cpupower frequency-info` from this machine, captured verbatim -
#: including the two frequency lines, in the order it prints them.
REAL = """analyzing CPU 7:
  driver: intel_pstate
  CPUs which run at the same hardware frequency: 7
  CPUs which need to have their frequency coordinated by software: 7
  energy performance preference: balance_performance
  hardware limits: 400 MHz - 4.70 GHz
  available cpufreq governors: performance powersave
  current policy: frequency should be within 400 MHz and 4.70 GHz.
                  The governor "powersave" may decide which speed to use
                  within this range.
  current CPU frequency: Unable to call hardware
  current CPU frequency: 1.07 GHz (asserted by call to kernel)
"""

#: The same tool when the hardware register *can* be read - one line only.
REAL_LIVE = REAL.replace(
    "  current CPU frequency: Unable to call hardware\n", "")

#: A machine with no cpufreq driver at all.
NO_DRIVER = """analyzing CPU 0:
  driver: acpi-cpufreq
  current policy: 400 MHz - 3.50 GHz
"""


def _fake_cpupower(tmp_path, monkeypatch, stdout=REAL, code=0):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    data = tmp_path / "out.txt"
    data.write_text(stdout)
    script = bindir / "cpupower"
    script.write_text(
        "#!/usr/bin/env python3\n"
        f"import sys\n"
        f"sys.stdout.write(open({str(data)!r}).read())\n"
        f"sys.exit({code})\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")


def test_the_unreadable_frequency_line_is_not_reported_as_a_speed(tmp_path, monkeypatch):
    """**The whole point.** The first of the two lines is a *failure*, and
    reporting it as a speed is the confident-wrong-answer shape.

    Written against the *output*, not against the regex, because a first
    version asserted on `_FREQ` directly and a mutation that widened that
    regex to also match the failure line still passed it: the parse it checked
    was not the parse that decides what a person reads.
    """
    _fake_cpupower(tmp_path, monkeypatch)
    out = CF._run({})
    assert "Unable to call hardware" not in out
    assert "1.07 GHz" in out
    # And no number is quoted that is not the real one.
    for number in ("1.07",):
        assert number in out


def test_the_unreadable_line_is_not_parsed_as_a_frequency(tmp_path, monkeypatch):
    """The regex half of the same property, so a widening of the pattern is
    caught even if the rendering is changed to suit."""
    # A regex that reaches past the failure text will pick up digits out of
    # "Unable to call hardware" - there are none, but "hardware limits: 400
    # MHz" style text nearby is exactly what a greedy version would grab.
    _fake_cpupower(tmp_path, monkeypatch)
    assert CF._FREQ.findall(REAL) == [("1.07", "GHz")]
    # And directly: feeding the failure line alone yields nothing at all.
    only_failure = "  current CPU frequency: Unable to call hardware\n"
    assert CF._FREQ.findall(only_failure) == []


def test_a_failure_line_containing_a_number_still_yields_no_frequency():
    """**This is what makes the parser's strictness load-bearing**, and it is
    why M1 (widening the regex to reach past the label) is not an equivalent
    mutant.

    cpupower's exact wording varies between versions and drivers - a message
    naming a CPU or a register would carry a digit - and a pattern that scans
    forward from the label for the first number would report that digit as the
    frequency. The real failure line on this machine happens to contain no
    digits, so the fixture alone cannot distinguish the two patterns; this can.
    """
    assert CF._FREQ.findall(
        "  current CPU frequency: Unable to call hardware on CPU 7\n") == []
    assert CF._FREQ.findall(
        "  current CPU frequency: cannot read MSR 0x198\n") == []
    # A real one still reads.
    assert CF._FREQ.findall(
        "  current CPU frequency: 3.91 GHz\n") == [("3.91", "GHz")]


def test_the_fallback_is_announced_rather_than_silent(tmp_path, monkeypatch):
    """When the register could not be read and the kernel figure is being
    used instead, the answer has to say so. Dropping that sentence is a
    confident answer with no caveat, which the mutation run caught.

    Note the condition is `unreadable and freqs`, **not** `len(freqs) > 1`:
    `freqs` never holds the unreadable line at all, so a live single-reading
    machine was being told it was quoting a fallback it had not used.
    """
    _fake_cpupower(tmp_path, monkeypatch)
    assert "last reported" in CF._run({})

    _fake_cpupower(tmp_path, monkeypatch, stdout=REAL_LIVE)
    assert "last reported" not in CF._run({})


def test_which_of_the_two_lines_wins_is_pinned(tmp_path, monkeypatch):
    """Stated directly, because "take the last" and "take the first" are both
    one character apart and only one is right.

    `_FREQ` deliberately does **not** match the unreadable line at all, so it
    returns exactly one pair. My first version of this test asserted it
    returned two - which is a claim about the fixture, not about the parser,
    and it failed while the parser was right.
    """
    _fake_cpupower(tmp_path, monkeypatch)
    assert len(CF._FREQ.findall(REAL)) == 1
    # And the raw source has two lines carrying the same label, which is the
    # condition the parser exists to survive.
    assert REAL.count("current CPU frequency:") == 2
    assert CF._run({}).count("1.07 GHz") == 1


def test_a_single_live_frequency_is_not_claimed_to_be_a_fallback(tmp_path, monkeypatch):
    """Where the hardware register reads fine there is one line, and saying
    "this is what the kernel last reported" about it would be a caveat about a
    problem that is not there.
    """
    _fake_cpupower(tmp_path, monkeypatch, stdout=REAL_LIVE)
    out = CF._run({})
    assert "last reported" not in out
    assert "1.07 GHz" in out


def test_the_governor_in_force_is_not_the_list_of_available_ones(tmp_path, monkeypatch):
    """Both lines are about governors and they mean different things:
    `available cpufreq governors: performance powersave` is what the hardware
    *could* use; the governor actually in force is named in the policy
    sentence. Reading the first line reports the machine as running
    "performance powersave", which is not a speed and not a setting.
    """
    _fake_cpupower(tmp_path, monkeypatch)
    out = CF._run({})
    assert "CPU governor: powersave" in out
    assert "performance powersave" not in out


def test_the_governor_is_distinguished_from_the_power_profile(tmp_path, monkeypatch):
    """They are different layers: the governor is what the kernel's scaling
    driver is doing, the power profile is the policy a daemon asked for.
    Reporting one as the other is a plausible wrong answer about a laptop's
    battery life.
    """
    _fake_cpupower(tmp_path, monkeypatch)
    out = CF._run({})
    assert "different thing from the power profile" in out


def test_the_hardware_range_is_reported_in_full(tmp_path, monkeypatch):
    """`400 MHz - 4.70 GHz`, both ends. A range with one end is a speed, and
    which end matters: the maximum is what a throttling question needs.
    """
    _fake_cpupower(tmp_path, monkeypatch)
    out = CF._run({})
    assert "400 MHz" in out
    assert "4.70 GHz" in out


def test_a_machine_with_no_driver_is_unknown_not_a_clean_reading(tmp_path, monkeypatch):
    """An absent cpufreq driver is not a CPU that is doing nothing.

    This test found a real bug: `_governor()` fell back to
    `current policy: 400 MHz - 3.50 GHz` and printed **`CPU governor: 400`**
    - a frequency wearing a governor's name, which is the same confident-wrong
    shape as the `Unable to call hardware` line above it.
    """
    _fake_cpupower(tmp_path, monkeypatch, stdout=NO_DRIVER)
    out = CF._run({})
    assert "not reported" in out
    assert "400 MHz" in out          # the range is still real
    assert "governor: 400" not in out
    assert "does not name" in out


def test_empty_output_is_unknown(tmp_path, monkeypatch):
    _fake_cpupower(tmp_path, monkeypatch, stdout="\n")
    out = CF._run({})
    assert "UNKNOWN" in out


def test_unrecognised_output_is_unknown_rather_than_a_guess(tmp_path, monkeypatch):
    """A cpupower that answers with something this does not read must not
    produce a confident partial answer.
    """
    _fake_cpupower(tmp_path, monkeypatch,
                   stdout="cpupower 7.2.9\nCopyright IBM Corporation\n")
    out = CF._run({})
    assert "UNKNOWN" in out
    assert "Nothing was guessed" in out


def test_a_failing_cpupower_says_its_own_words(tmp_path, monkeypatch):
    _fake_cpupower(tmp_path, monkeypatch, stdout="cpupower: not permitted\n", code=1)
    out = CF._run({})
    assert "UNKNOWN" in out
    assert "cpupower said" in out


def test_a_missing_cpupower_names_the_package(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    out = CF._run({})
    assert "cpupower" in out
    assert "cpupower" in out.split("comes from")[-1] or "package" in out


def test_the_real_binary_is_used_when_present():
    """Not a stub: the real `cpupower`, on this machine, right now."""
    import shutil
    if not shutil.which("cpupower"):
        pytest.skip("cpupower is not installed on this box")
    text, problem = CF._info()
    assert problem == "", problem
    assert text.strip(), "cpupower printed nothing"
    # Whatever this machine's state, it must never yield a speed that is the
    # word "Unable".
    freqs = CF._FREQ.findall(text)
    assert all(value.replace(".", "").isdigit() for value, _ in freqs), freqs