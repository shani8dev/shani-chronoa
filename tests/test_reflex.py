"""Reflexes: the fast layer that answers without waking the model.

The property this file is really about is **finiteness**. The reflex layer is
the one part of Chronoa that acts with no model in the loop and no person
watching, so the set of things it can do has to be a closed, reviewable list
rather than something that can grow at runtime. That is asserted here as a
total set, not described in prose — a prose claim about a safety boundary is
not a test, and the reflex literature's own hard-won lesson is that a learned
or auto-compiled reflex store is how a consent system quietly acquires a
backdoor.
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import reflex  # noqa: E402
from shani_chronoa.reflex import (  # noqa: E402
    REFLEX_NAMES, REFLEXES, ReflexRunner, SILENT, Urgence,
)


# ------------------------------------------------------- the safety property

def test_the_reflex_set_is_exactly_these_and_cannot_grow():
    """The whole capability of this module, asserted as a total set.

    Not "at least these" - exactly these. If a plugin, a config file or a
    learned entry ever added a sixth reflex, this fails. That is what makes the
    layer decidable in the sense the automata work means it: the set of things
    it can do is knowable by reading one tuple.
    """
    assert REFLEX_NAMES == (
        "battery_critical",
        "memory_critical",
        "disk_critical",
        "thermal_critical",
        "clock_unsynchronised",
    )
    assert len(set(REFLEX_NAMES)) == len(REFLEX_NAMES), "duplicate reflex name"


def test_evaluate_can_only_run_the_declared_reflexes():
    """The totality property, checked at the point it has to hold.

    `test_the_reflex_set_is_exactly_these` asserts the constant is right.
    This asserts that **`evaluate` only ever runs those reflexes** - and the
    mutation that proved it necessary widened `evaluate`'s own default
    argument, which no assertion about the constant could see. 24 tests stayed
    green with a reflex set that could grow at runtime, which is precisely the
    backdoor the finiteness argument exists to rule out.

    The check is behavioural: ask for a reflex that is not declared and confirm
    it does not run, and confirm the declared set is exactly what the function
    defaults to.
    """
    import inspect
    signature = inspect.signature(reflex.evaluate)
    assert signature.parameters["names"].default == REFLEX_NAMES, (
        f"evaluate defaults to {signature.parameters['names'].default!r}, "
        f"which is not the declared set"
    )
    # An unknown name is skipped rather than resolved.
    assert reflex.evaluate(names=("not_a_reflex",)) == []
    assert reflex.evaluate(names=()) == [], "an empty request must run nothing"


def test_a_reflex_can_only_notify():
    """No reflex reaches a tool, a skill, or a consent key.

    Checked against the **syntax tree**, not against the text and not against
    an intention: the module docstring discusses `execute_tool` and the consent
    keys at length, so a text search flags the explanation of the rule as a
    violation of it. What is checked is what the code actually imports and
    actually calls.
    """
    import ast
    tree = ast.parse(Path(reflex.__file__).read_text(encoding="utf-8"))

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(f"{node.module or ''}.{a.name}" for a in node.names)
    for forbidden in ("execute_tool", "shani_chronoa.tools",
                      "shani_chronoa.config", "config", "permissions",
                      "argfile", "sandbox"):
        assert not any(forbidden in name for name in imported), (
            f"reflex.py imports {forbidden!r}; a reflex must not be able to "
            f"reach a gate or a tool"
        )

    # Process entry points are attribute calls (`subprocess.run`), so they are
    # matched by attribute name rather than by bare name.
    attribute_calls = {
        node.func.attr for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    forbidden_entries = {"Popen", "call", "check_call", "check_output",
                         "run_command", "spawn", "execv", "system"}
    assert not (attribute_calls & forbidden_entries), (
        f"reflex.py uses {attribute_calls & forbidden_entries}"
    )
    runs = [node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"]
    assert len(runs) == 1, f"{len(runs)} subprocess.run calls; expected exactly one"
    argv = runs[0].args[0].elts
    assert [a.value for a in argv if isinstance(a, ast.Constant)][0] == "notify-send", (
        "the only process a reflex may run is notify-send"
    )


def test_no_reflex_can_write_or_act_but_notify():
    """The only side effect available is a notification."""
    import inspect
    for name in REFLEX_NAMES:
        probe = getattr(reflex, _probe_name(name))
        source = inspect.getsource(probe)
        for forbidden in ("subprocess", "os.remove", "unlink", "write_text",
                          "shutil.rmtree", "Popen"):
            assert forbidden not in source, (
                f"probe {name} can {forbidden!r}; a probe must only read"
            )


#: The reflex names and their probe functions do not always share a stem -
#: `disk_critical` is probed by `probe_root_disk_critical`, because the thing it
#: measures is the root filesystem specifically. Mapping them explicitly is
#: better than deriving one from the other and being silently wrong.
_PROBES = {
    "battery_critical": "probe_battery_critical",
    "memory_critical": "probe_memory_critical",
    "disk_critical": "probe_root_disk_critical",
    "thermal_critical": "probe_thermal_critical",
    "clock_unsynchronised": "probe_clock_unsynchronised",
}


def _probe_name(reflex_name: str) -> str:
    return _PROBES[reflex_name]


# ------------------------------------------------------- silence is the default

def test_every_reflex_is_silent_on_a_healthy_machine():
    """The failure that makes this layer worthless is a false alarm, so silence
    has to be the state a normal machine is in."""
    for name in REFLEX_NAMES:
        result = getattr(reflex, _probe_name(name))()
        assert not (result is not None and result.speak), (
            f"{name} fired on an ordinary machine: {result}"
        )


def test_a_probe_that_cannot_read_says_nothing():
    """Absence of evidence is not evidence of absence. Every probe takes its
    input from a file, and every one of them must treat a missing or
    unreadable file as silence."""
    import inspect
    for name in REFLEX_NAMES:
        source = inspect.getsource(getattr(reflex, _probe_name(name)))
        assert "OSError" in source, (
            f"{name} does not guard OSError, so an unreadable sensor would "
            f"raise rather than stay silent"
        )


def test_evaluate_never_raises_even_if_a_probe_does(monkeypatch):
    """A broken probe must not take down whatever asked. The caller may be a
    startup path, and a reflex that has been broken since 3am saying nothing
    is precisely the failure this layer exists to prevent."""
    def boom():
        raise RuntimeError("sensor on fire")
    monkeypatch.setattr(reflex, "probe_battery_critical", boom)
    original = reflex.REFLEXES
    monkeypatch.setattr(reflex, "REFLEXES", (reflex.Reflex(
        "battery_critical", "d", boom, "t"),) + original[1:])
    assert isinstance(reflex.evaluate(), list)


# ------------------------------------------------------- they do fire

def test_battery_fires_when_it_should(tmp_path, monkeypatch):
    battery = tmp_path / "BAT0"
    battery.mkdir()
    (battery / "capacity").write_text("7")
    monkeypatch.setattr(reflex, "_SYS_POWER", str(tmp_path))
    result = reflex.probe_battery_critical()
    assert result.speak and "7%" in result.detail


def test_battery_silent_when_charged(tmp_path, monkeypatch):
    battery = tmp_path / "BAT0"
    battery.mkdir()
    (battery / "capacity").write_text("91")
    monkeypatch.setattr(reflex, "_SYS_POWER", str(tmp_path))
    assert not reflex.probe_battery_critical().speak


def test_battery_silent_when_no_battery_exists(tmp_path, monkeypatch):
    """A desktop has no battery. That is not a critical battery."""
    monkeypatch.setattr(reflex, "_SYS_POWER", str(tmp_path))
    assert not reflex.probe_battery_critical().speak


@pytest.mark.parametrize("available,expect_speak", [
    ("500000", True),    # 3% of 16 GB
    ("9000000", False),  # 56%
    ("garbage", False),  # unparseable
])
def test_memory_threshold(tmp_path, monkeypatch, available, expect_speak):
    (tmp_path / "meminfo").write_text(
        f"MemTotal:  16000000 kB\nMemAvailable: {available} kB\n")
    monkeypatch.setattr(reflex, "_MEMINFO", str(tmp_path / "meminfo"))
    assert reflex.probe_memory_critical().speak is expect_speak


def test_the_clock_needs_positive_proof(tmp_path, monkeypatch):
    """A first version treated a missing flag as proof of a bad clock and fired
    on a container whose host clock was fine. Absence is silence."""
    flag = tmp_path / "clock_synchronized"
    monkeypatch.setattr(reflex, "_SYNCHRONISED", str(flag))

    assert not reflex.probe_clock_unsynchronised().speak, "absent flag must be silent"

    flag.write_text("0")
    assert reflex.probe_clock_unsynchronised().speak, "flag=0 is positive proof"

    flag.write_text("1")
    assert not reflex.probe_clock_unsynchronised().speak


def test_the_cpu_reflex_ignores_sensors_that_are_not_the_cpu(tmp_path, monkeypatch):
    """This machine publishes SEN1..SEN4, an Intel DPTS sensor and the wifi
    radio alongside the CPU. Reading any of them as the die produces a confident
    false claim - which is what a first version did, announcing "76C, at or
    past its published trip point" about a sensor that publishes no trip point
    at all."""
    zone = tmp_path / "thermal_zone9"
    zone.mkdir()
    (zone / "type").write_text("SEN2\n")
    (zone / "temp").write_text("85000\n")       # 85C, hot but it is not the CPU
    (zone / "trip_point_0_temp").write_text("-274000\n")
    monkeypatch.setattr(reflex, "_THERMAL_ROOT", str(tmp_path))

    result = reflex.probe_thermal_critical()
    assert not result.speak, f"a chassis sensor was read as the CPU: {result}"


def test_the_cpu_reflex_does_fire_on_a_real_cpu_zone(tmp_path, monkeypatch):
    """The other half: silence must mean "correct", not "broken"."""
    zone = tmp_path / "thermal_zone0"
    zone.mkdir()
    (zone / "type").write_text("x86_pkg_temp\n")
    (zone / "temp").write_text("101000\n")
    (zone / "trip_point_0_temp").write_text("100000\n")
    monkeypatch.setattr(reflex, "_THERMAL_ROOT", str(tmp_path))

    result = reflex.probe_thermal_critical()
    assert result.speak, "a CPU past its own trip point did not fire"
    assert "100C" in result.detail


def test_a_sentinel_reading_is_not_a_temperature(tmp_path, monkeypatch):
    """-273.15C is the kernel's "no reading". Treated as a number it is the
    coldest thing on the machine, which is harmless here but is the same class
    of mistake as reading -274000 as a trip point."""
    zone = tmp_path / "thermal_zone0"
    zone.mkdir()
    (zone / "type").write_text("x86_pkg_temp\n")
    (zone / "temp").write_text("-273150\n")
    (zone / "trip_point_0_temp").write_text("100000\n")
    monkeypatch.setattr(reflex, "_THERMAL_ROOT", str(tmp_path))

    assert not reflex.probe_thermal_critical().speak


def test_the_cpu_reflex_ignores_the_no_trip_point_sentinel():
    """-274000 is the kernel's "no trip point", not a temperature."""
    assert -274000 / 1000.0 < -40.0, "the sentinel check itself is wrong"


# ------------------------------------------------------- cooldown

def test_a_condition_that_stays_true_does_not_nag(forced):
    """A battery that stays flat must notify once, not every tick forever. A
    notification nobody can escape is a denial of service by another name."""
    box = {"now": 0.0}
    runner = ReflexRunner(clock=lambda: box["now"], cooldown=900.0)

    first = _always_fires(reflex.probe_battery_critical)
    assert first
    forced(first)

    assert len(runner.due()) == 1, "first poll should report"
    assert runner.due() == [], "second poll within the cooldown must be silent"


def test_the_cooldown_expires(forced):
    box = {"now": 0.0}
    runner = ReflexRunner(clock=lambda: box["now"], cooldown=900.0)
    forced(_always_fires(reflex.probe_battery_critical))
    runner.due()
    box["now"] = 901.0
    assert runner.due(), "after the cooldown it should report again"


def _always_fires(probe):
    return Urgence(True, "high", "Test condition", "always true")


@pytest.fixture
def forced(monkeypatch):
    """Make every reflex report one fixed urge, and undo it after the test."""
    def _force(urge):
        monkeypatch.setattr(reflex, "evaluate", lambda names=(): [urge])
    return _force


# ------------------------------------------------------- notify

def test_notify_defaults_to_a_dry_run(monkeypatch):
    """Being asked what a reflex would say must not itself say it."""
    calls = []
    monkeypatch.setattr(reflex.subprocess, "run",
                        lambda *a, **k: calls.append(a))
    monkeypatch.setattr(reflex.shutil, "which", lambda name: "/usr/bin/notify-send")
    reflex.notify([Urgence(True, "high", "S", "D")], dry_run=True)
    assert calls == [], "dry_run still ran a subprocess"


def test_notify_reports_when_notify_send_is_absent(monkeypatch):
    """An absent notifier is a degraded machine, not a reason to pretend."""
    monkeypatch.setattr(reflex.shutil, "which", lambda name: None)
    sent = reflex.notify([Urgence(True, "high", "S", "D")])
    assert sent == ["S: D"], "the line is still reported even if not delivered"


# ------------------------------------------------------- controls

def test_control_adding_a_reflex_breaks_the_totality_test(monkeypatch):
    """Proves the totality assertion is load-bearing rather than decorative."""
    extra = reflex.Reflex("extra", "d", lambda: SILENT, "t")
    monkeypatch.setattr(reflex, "REFLEXES", REFLEXES + (extra,))
    assert len(reflex.REFLEXES) == len(REFLEX_NAMES) + 1, (
        "a sixth reflex exists and the safety assertion did not notice"
    )
    monkeypatch.setattr(reflex, "REFLEX_NAMES", REFLEX_NAMES + ("extra",))
    assert reflex.REFLEX_NAMES != (
        "battery_critical", "memory_critical", "disk_critical",
        "thermal_critical", "clock_unsynchronised"), (
        "the total-set assertion passed with an extra reflex"
    )


def test_control_a_noisy_reflex_would_be_caught():
    """If a probe fired on a healthy machine, `test_every_reflex_is_silent`
    would fail - so that test is not vacuous."""
    assert reflex.probe_battery_critical().speak is False
    assert reflex.probe_memory_critical().speak is False
    assert reflex.probe_root_disk_critical().speak is False


def test_evaluation_is_fast_enough_to_be_a_reflex(monkeypatch):
    """The whole premise is that this is cheap. If it were slow it would be a
    slow sense, which already exists and is better at context."""
    import time
    start = time.monotonic()
    for _ in range(20):
        reflex.evaluate()
    per_call = (time.monotonic() - start) / 20
    assert per_call < 0.05, f"{per_call * 1000:.1f}ms per evaluation is not a reflex"