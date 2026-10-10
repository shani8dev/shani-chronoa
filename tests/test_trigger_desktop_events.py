"""The six desktop/device event types: screenlock, powerstate, netstate, usbplug, btconnect, schedule.

Each reader is driven against a fake of the real interface it reads (a sysfs
tree, a stub `loginctl`/`nmcli`/`bluetoothctl` on PATH, a fixed clock), and
each is held to the two properties that make polling them safe:

- **the source names the state to be told about, and only the transition INTO
  it fires.** Arming "locked" on a locked screen records a baseline; staying
  locked fires nothing more; unlocking is recorded quietly; locking again fires.
- **anything unreadable is SIGNAL_UNAVAILABLE, never a quiet state.** A desktop
  with no battery is not "above 20%", a machine with no NetworkManager is not
  "offline", and a missed calendar time while asleep is skipped, not replayed.
"""

import os
import stat
import time
from pathlib import Path

import pytest

from shani_chronoa import triggers, verification
from shani_chronoa.triggers import (
    MATCH_ANY, RETRY_RETRYABLE, SIGNAL_OK, SIGNAL_UNAVAILABLE, DurableFingerprints,
    EventEngine, EventRule, EventRuleStore,
)


# --- fakes -------------------------------------------------------------------

def _stub(bindir: Path, name: str, script: str) -> None:
    path = bindir / name
    path.write_text("#!/bin/sh\n" + script)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def bindir(tmp_path, monkeypatch):
    d = tmp_path / "bin"
    d.mkdir()
    monkeypatch.setenv("PATH", f"{d}:{os.environ.get('PATH', '')}")
    return d


def _supply(root: Path, name: str, **attrs) -> None:
    d = root / name
    d.mkdir(parents=True)
    for k, v in attrs.items():
        (d / k).write_text(f"{v}\n")


class AllowAll:
    input_control_enabled = False

    def get_bool(self, key, default=False):
        return True

    def sense_allowed(self, sense):
        return True

    def sense_allowed_reason(self, sense):
        return ""


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, actuator, arguments, **kwargs):
        self.calls.append((actuator, dict(arguments)))
        return verification.Result(verification.Verdict.VERIFIED, "it happened")


def _engine(tmp_path, recorder):
    return EventEngine(store=EventRuleStore(tmp_path / "rules.json"), config_factory=AllowAll,
                       dispatch=recorder, fingerprint_store=DurableFingerprints(tmp_path / "fp.json"))


def _arm(engine, event_type, source, **params):
    rule = EventRule(name=f"{event_type}-rule", event_type=event_type, source=source, actuator="notify",
                     arguments={"summary": "x"}, match_mode=MATCH_ANY, substring="", debounce_seconds=0.0,
                     cooldown_seconds=1.0, retry_policy=RETRY_RETRYABLE, params=params)
    engine.store().add(rule)
    return rule


# --- screenlock ----------------------------------------------------------------

@pytest.fixture
def logind(bindir, tmp_path, monkeypatch):
    state = tmp_path / "locked"
    state.write_text("no")
    monkeypatch.setenv("XDG_SESSION_ID", "7")
    _stub(bindir, "loginctl", f'echo "LockedHint=$(cat {state})"\necho "IdleHint=no"\n')
    return state


def test_screenlock_fires_on_the_transition_into_locked_only(tmp_path, logind):
    rec = Recorder()
    eng = _engine(tmp_path, rec)
    _arm(eng, "screenlock", "locked")
    eng.poll(now=1.0)                       # unlocked: baseline
    assert rec.calls == []
    logind.write_text("yes")
    eng.poll(now=2.0)                       # locked: fires
    assert len(rec.calls) == 1
    eng.poll(now=10.0)                      # still locked: nothing
    assert len(rec.calls) == 1
    logind.write_text("no")
    eng.poll(now=20.0)                      # unlocked: recorded quietly
    logind.write_text("yes")
    eng.poll(now=40.0)                      # locked again: fires again
    assert len(rec.calls) == 2


def test_arming_while_already_locked_does_not_fire(tmp_path, logind):
    logind.write_text("yes")
    rec = Recorder()
    eng = _engine(tmp_path, rec)
    _arm(eng, "screenlock", "locked")
    eng.poll(now=1.0)
    eng.poll(now=5.0)
    assert rec.calls == []


def test_screenlock_without_logind_is_unavailable(bindir, monkeypatch):
    monkeypatch.setenv("PATH", str(bindir))  # no loginctl at all
    sig = triggers.read_screenlock("locked")
    assert sig.status == SIGNAL_UNAVAILABLE and "loginctl" in sig.detail


def test_screenlock_refuses_an_unknown_source(logind):
    assert triggers.read_screenlock("sleepy").status == SIGNAL_UNAVAILABLE


# --- powerstate ----------------------------------------------------------------

@pytest.fixture
def sysfs_power(tmp_path, monkeypatch):
    root = tmp_path / "power_supply"
    root.mkdir()
    monkeypatch.setattr(triggers.desktop_sources, "POWER_SUPPLY_DIR", root)
    return root


def test_on_battery_reads_the_mains_supply(sysfs_power):
    _supply(sysfs_power, "AC", type="Mains", online=0)
    _supply(sysfs_power, "BAT0", type="Battery", capacity=55, status="Discharging")
    sig = triggers.read_powerstate("on-battery")
    assert sig.status == SIGNAL_OK and sig.event is not None
    assert triggers.read_powerstate("on-ac").event is None


def test_battery_below_threshold(sysfs_power):
    _supply(sysfs_power, "BAT0", type="Battery", capacity=15, status="Discharging")
    assert triggers.read_powerstate("battery-below:20").event is not None
    assert triggers.read_powerstate("battery-below:10").event is None
    assert triggers.read_powerstate("battery-above:10").event is not None


def test_a_peripheral_battery_is_not_the_machines(sysfs_power):
    _supply(sysfs_power, "hidpp_battery_0", type="Battery", capacity=5, status="Discharging", scope="Device")
    sig = triggers.read_powerstate("battery-below:20")
    assert sig.status == SIGNAL_UNAVAILABLE, "a mouse at 5% must not read as the laptop at 5%"


def test_a_desktop_with_no_battery_is_unavailable_not_charged(sysfs_power):
    _supply(sysfs_power, "AC", type="Mains", online=1)
    assert triggers.read_powerstate("charged").status == SIGNAL_UNAVAILABLE
    assert triggers.read_powerstate("on-ac").event is not None


# --- a UPS is BOTH a mains source and a battery source -----------------------
#
# The reason these exist: `_read_power` handled Mains, USB and Battery, so a
# machine with a UPS could not arm "when the power goes out" at all - the one rule
# a UPS is for. A UPS reports `online` (is utility power reaching it) and
# `capacity` (its own charge), so it answers both halves and needs both.


def test_a_ups_on_mains_is_on_ac(sysfs_power):
    """A UPS with utility power is `on-ac` exactly like a Mains entry."""
    _supply(sysfs_power, "ups0", type="UPS", online=1, capacity=100, status="OL")
    sig = triggers.read_powerstate("on-ac")
    assert sig.status == SIGNAL_OK and sig.event is not None
    assert triggers.read_powerstate("on-battery").event is None


def test_a_ups_that_lost_mains_is_on_battery(sysfs_power):
    """The rule a UPS is for. `online=0` means utility power is out."""
    _supply(sysfs_power, "ups0", type="UPS", online=0, capacity=88, status="OB DISCHRG")
    sig = triggers.read_powerstate("on-battery")
    assert sig.status == SIGNAL_OK and sig.event is not None
    assert triggers.read_powerstate("on-ac").event is None


def test_a_ups_charge_level_is_readable(sysfs_power):
    """`capacity` is the UPS battery's percentage, so the threshold rules work on
    it too - which is the other half a UPS answers."""
    _supply(sysfs_power, "ups0", type="UPS", online=0, capacity=18, status="OB DISCHRG")
    assert triggers.read_powerstate("battery-below:20").event is not None
    assert triggers.read_powerstate("battery-below:10").event is None
    assert triggers.read_powerstate("battery-above:10").event is not None


def test_a_peripheral_battery_and_a_ups_do_not_blur(sysfs_power):
    """A mouse at 5% must still not read as the machine at 5%, and it must not
    drag a UPS's charge down either - the levels are averaged, which is right for
    two real packs and wrong for a mouse."""
    _supply(sysfs_power, "hidpp_battery_0", type="Battery", capacity=5,
            status="Discharging", scope="Device")
    _supply(sysfs_power, "ups0", type="UPS", online=0, capacity=90, status="OB DISCHRG")
    sig = triggers.read_powerstate("battery-below:20")
    assert sig.event is None, "a mouse at 5% must not make a UPS at 90% read as low"


def test_a_ups_with_neither_online_nor_status_is_unavailable_not_mains(sysfs_power):
    """A UPS entry that reports neither `online` nor a status word says nothing
    either way, and guessing "on AC" would silence the one rule that matters.

    `online` alone is not required, because the existing `status != "Discharging"`
    fallback is a real second signal - a driver word of "OL" is the UPS saying it
    is on line. What must not happen is reading a bare `capacity` as "fine".
    """
    _supply(sysfs_power, "ups0", type="UPS", capacity=90)
    sig = triggers.read_powerstate("on-battery")
    assert sig.status == SIGNAL_UNAVAILABLE


def test_a_ups_status_word_alone_is_enough(sysfs_power):
    """`status="OL"` is the UPS saying it is on line, so it infers AC without
    `online` - and "OB DISCHRG" must infer the opposite."""
    _supply(sysfs_power, "ups0", type="UPS", capacity=90, status="OL")
    assert triggers.read_powerstate("on-ac").event is not None
    _supply(sysfs_power, "upz0", type="UPS", capacity=90, status="OB DISCHRG")
    assert triggers.read_powerstate("on-battery").event is not None


def test_battery_threshold_must_be_a_percentage(sysfs_power):
    _supply(sysfs_power, "BAT0", type="Battery", capacity=50, status="Charging")
    assert triggers.read_powerstate("battery-below:abc").status == SIGNAL_UNAVAILABLE
    assert triggers.read_powerstate("battery-below:100").status == SIGNAL_UNAVAILABLE


# --- netstate ------------------------------------------------------------------

@pytest.fixture
def nm(bindir, tmp_path):
    conn = tmp_path / "connectivity"
    conn.write_text("full")
    active = tmp_path / "active"
    active.write_text("Home\\:5G:802-11-wireless\nlo:loopback\n")
    _stub(bindir, "nmcli", f'''case "$*" in
  *general*) cat {conn} ;;
  *connection*) cat {active} ;;
esac
''')
    return conn, active


def test_netstate_online_and_offline(nm):
    conn, _ = nm
    assert triggers.read_netstate("online").event is not None
    assert triggers.read_netstate("offline").event is None
    conn.write_text("none")
    assert triggers.read_netstate("offline").event is not None


def test_netstate_unknown_connectivity_is_unavailable(nm):
    conn, _ = nm
    conn.write_text("unknown")
    assert triggers.read_netstate("offline").status == SIGNAL_UNAVAILABLE


def test_a_connection_name_with_an_escaped_colon(nm):
    assert triggers.read_netstate("connected:Home:5G").event is not None
    assert triggers.read_netstate("disconnected:Office").event is not None
    assert triggers.read_netstate("connected:Office").event is None


# --- usbplug -------------------------------------------------------------------

@pytest.fixture
def usb(tmp_path, monkeypatch):
    root = tmp_path / "usb"
    root.mkdir()
    _supply(root, "usb1", idVendor="1d6b", idProduct="0002", product="xHCI Host Controller")
    monkeypatch.setattr(triggers.desktop_sources, "USB_DEVICES_DIR", root)
    return root


def test_usbplug_ignores_the_root_hub_and_matches_by_name_or_id(usb):
    assert triggers.read_usbplug("plugged:host controller").event is None
    _supply(usb, "1-2", idVendor="0781", idProduct="5581", manufacturer="SanDisk", product="Ultra")
    assert triggers.read_usbplug("plugged:sandisk").event is not None
    assert triggers.read_usbplug("plugged:0781:5581").event is not None
    assert triggers.read_usbplug("unplugged:sandisk").event is None


def test_usbplug_fires_on_plug_in_through_the_engine(tmp_path, usb):
    rec = Recorder()
    eng = _engine(tmp_path, rec)
    _arm(eng, "usbplug", "plugged:sandisk")
    eng.poll(now=1.0)
    assert rec.calls == []
    _supply(usb, "1-2", idVendor="0781", idProduct="5581", manufacturer="SanDisk", product="Ultra")
    eng.poll(now=2.0)
    assert len(rec.calls) == 1


# --- btconnect -----------------------------------------------------------------

def test_btconnect_by_name_and_mac(bindir):
    _stub(bindir, "bluetoothctl", 'echo "Device AA:BB:CC:DD:EE:FF WH-1000XM4"\n')
    assert triggers.read_btconnect("connected:WH-1000XM4").event is not None
    assert triggers.read_btconnect("connected:aa:bb:cc:dd:ee:ff").event is not None
    assert triggers.read_btconnect("disconnected:WH-1000XM4").event is None


def test_btconnect_with_bluetoothd_down_is_unavailable(bindir):
    _stub(bindir, "bluetoothctl", "exit 1\n")
    assert triggers.read_btconnect("connected:x").status == SIGNAL_UNAVAILABLE


# --- schedule ------------------------------------------------------------------

def _at(y, mo, d, h, mi):
    return time.mktime((y, mo, d, h, mi, 0, 0, 0, -1))


@pytest.mark.parametrize("text", ["daily 08:00", "weekdays 18:30", "weekends 10:00", "mon,wed,fri 07:15",
                                  "tue-thu 09:00", "hourly :05", "every 30 minutes"])
def test_schedule_grammar(text):
    spec, why = triggers.parse_schedule(text)
    assert spec is not None, why


@pytest.mark.parametrize("text", ["daily 25:00", "every 1 minutes", "someday 08:00", "08:00", "hourly :75"])
def test_schedule_grammar_refuses(text):
    assert triggers.parse_schedule(text)[0] is None


def test_schedule_fires_once_when_the_time_arrives(tmp_path):
    rec = Recorder()
    eng = _engine(tmp_path, rec)
    _arm(eng, "schedule", "daily 08:00")
    eng.poll(now=_at(2026, 10, 1, 7, 59))   # baseline: yesterday's 08:00, long past
    assert rec.calls == []
    eng.poll(now=_at(2026, 10, 1, 8, 0) + 20)
    assert len(rec.calls) == 1
    eng.poll(now=_at(2026, 10, 1, 8, 5))     # same occurrence: nothing
    assert len(rec.calls) == 1
    eng.poll(now=_at(2026, 10, 2, 8, 1))     # the next day: fires again
    assert len(rec.calls) == 2


def test_a_time_missed_while_asleep_is_skipped_not_replayed(tmp_path):
    rec = Recorder()
    eng = _engine(tmp_path, rec)
    _arm(eng, "schedule", "daily 08:00")
    eng.poll(now=_at(2026, 10, 1, 7, 0))
    eng.poll(now=_at(2026, 10, 1, 11, 0))    # woke three hours late
    assert rec.calls == []


def test_weekdays_skip_the_weekend():
    # 2026-10-03 is a Saturday: the last weekday 18:30 before it is Friday's.
    occ = triggers.last_occurrence(triggers.parse_schedule("weekdays 18:30")[0], _at(2026, 10, 3, 19, 0))
    assert time.localtime(occ).tm_wday == 4


def test_every_type_has_a_reader():
    """No event type may fall through to the 'not a known event type' branch."""
    for event_type in triggers.EVENT_TYPES:
        rule = EventRule(name="n", event_type=event_type, source="x", actuator="notify", arguments={},
                         match_mode=MATCH_ANY, substring="", debounce_seconds=0.0, cooldown_seconds=1.0,
                         retry_policy=RETRY_RETRYABLE)
        sig = triggers.read_event_signal(rule)
        assert "is not a known event type" not in sig.detail, event_type


# --- sleepwake -----------------------------------------------------------------

def test_sleepwake_fires_once_per_resume(tmp_path, monkeypatch):
    slept = {"s": 100.0}
    monkeypatch.setattr(triggers.desktop_sources, "_suspended_seconds", lambda: slept["s"])
    rec = Recorder()
    eng = _engine(tmp_path, rec)
    _arm(eng, "sleepwake", "resumed")
    eng.poll(now=1.0)                  # baseline
    eng.poll(now=2.0)                  # awake, counter unchanged: nothing
    assert rec.calls == []
    slept["s"] = 1900.0                # a 30-minute suspend happened
    eng.poll(now=3.0)
    assert len(rec.calls) == 1
    slept["s"] = 1901.0                # clock jitter under the 5 s grain: nothing
    eng.poll(now=4.0)
    assert len(rec.calls) == 1


def test_sleepwake_without_boottime_is_unavailable(monkeypatch):
    monkeypatch.setattr(triggers.desktop_sources, "_suspended_seconds", lambda: None)
    assert triggers.read_sleepwake("resumed").status == SIGNAL_UNAVAILABLE
    assert triggers.read_sleepwake("asleep").status == SIGNAL_UNAVAILABLE


# --- audiodevice ---------------------------------------------------------------

@pytest.fixture
def pipewire(bindir, tmp_path):
    dump = tmp_path / "dump.json"
    dump.write_text('[{"info": {"props": {"media.class": "Audio/Sink", "node.description": "Built-in Speakers"}}}]')
    _stub(bindir, "pw-dump", f"cat {dump}\n")
    return dump


def test_audiodevice_added_headphones(tmp_path, pipewire):
    rec = Recorder()
    eng = _engine(tmp_path, rec)
    _arm(eng, "audiodevice", "added:headphones")
    eng.poll(now=1.0)
    assert rec.calls == []
    pipewire.write_text('[{"info": {"props": {"media.class": "Audio/Sink", "node.description": "Built-in Speakers"}}},'
                        '{"info": {"props": {"media.class": "Audio/Sink", "node.description": "WH-1000XM4 Headphones"}}},'
                        '{"info": {"props": {"media.class": "Video/Source", "node.description": "Camera headphones?"}}}]')
    eng.poll(now=2.0)
    assert len(rec.calls) == 1
    sig = triggers.read_audiodevice("any")
    assert sig.payload["snapshot"]["devices"] == ["output: Built-in Speakers", "output: WH-1000XM4 Headphones"]


def test_audiodevice_with_no_pipewire_is_unavailable(bindir):
    _stub(bindir, "pw-dump", "exit 1\n")
    assert triggers.read_audiodevice("any").status == SIGNAL_UNAVAILABLE


# --- journalmatch --------------------------------------------------------------

@pytest.fixture
def journal(bindir, tmp_path):
    out = tmp_path / "entry"
    out.write_text('{"__CURSOR": "c1", "MESSAGE": "disk full on /home", "_SYSTEMD_UNIT": "x.service"}\n')
    args = tmp_path / "args"
    _stub(bindir, "journalctl", f'printf "%s\\n" "$@" > {args}\ncat {out}\n')
    return out, args


def test_journalmatch_fires_on_a_new_match_not_an_old_one(tmp_path, journal):
    out, args = journal
    rec = Recorder()
    eng = _engine(tmp_path, rec)
    _arm(eng, "journalmatch", "disk full")
    eng.poll(now=1.0)                  # the existing match is the baseline
    eng.poll(now=2.0)
    assert rec.calls == []
    out.write_text('{"__CURSOR": "c2", "MESSAGE": "disk full again", "_SYSTEMD_UNIT": "x.service"}\n')
    eng.poll(now=3.0)
    assert len(rec.calls) == 1
    assert "--grep=disk full" in args.read_text().splitlines(), "the pattern is one argv element"


def test_journalmatch_unit_form_and_limits(journal):
    _, args = journal
    triggers.read_journalmatch("unit:sshd.service:Failed password")
    lines = args.read_text().splitlines()
    assert "-u" in lines and "sshd.service" in lines and "--grep=Failed password" in lines
    assert triggers.read_journalmatch("unit:bad name;rm:x").status == SIGNAL_UNAVAILABLE
    assert triggers.read_journalmatch("x" * 300).status == SIGNAL_UNAVAILABLE


def test_journalmatch_without_permission_is_unavailable(bindir):
    _stub(bindir, "journalctl", 'echo "No journal files were opened due to insufficient permissions." >&2\nexit 1\n')
    assert triggers.read_journalmatch("anything").status == SIGNAL_UNAVAILABLE


# --- dbusprop ------------------------------------------------------------------

DBUS_SRC = "system org.freedesktop.UPower /org/freedesktop/UPower org.freedesktop.UPower OnBattery"


def test_dbusprop_value_and_any_change(bindir, tmp_path):
    val = tmp_path / "v"
    val.write_text('{"type":"b","data":false}')
    _stub(bindir, "busctl", f"cat {val}\n")
    assert triggers.read_dbusprop(DBUS_SRC + " =true").event is None
    assert triggers.read_dbusprop(DBUS_SRC).event is not None
    val.write_text('{"type":"b","data":true}')
    assert triggers.read_dbusprop(DBUS_SRC + " =true").event is not None


@pytest.mark.parametrize("bad", [
    "system org.freedesktop.UPower /org/x;rm org.freedesktop.UPower OnBattery",
    "both org.freedesktop.UPower / org.freedesktop.UPower OnBattery",
    "system notabusname / org.freedesktop.UPower OnBattery",
    "system org.freedesktop.UPower / org.freedesktop.UPower",
])
def test_dbusprop_validates_every_token(bindir, bad):
    _stub(bindir, "busctl", "echo should-not-run; exit 3\n")
    assert triggers.read_dbusprop(bad).status == SIGNAL_UNAVAILABLE


# --- ask_first: approve from a notification ---------------------------------------

class FakeApprover:
    def __init__(self, answer):
        self.answer, self.asked = answer, []

    def available(self):
        return True

    def request(self, key, title, body, on_answer):
        self.asked.append((title, body))
        on_answer(self.answer)   # synchronous stand-in for the notification thread
        return True


def _ask_engine(tmp_path, rec, answer):
    approver = FakeApprover(answer)
    eng = EventEngine(store=EventRuleStore(tmp_path / "rules.json"), config_factory=AllowAll, dispatch=rec,
                      fingerprint_store=DurableFingerprints(tmp_path / "fp.json"), approver=approver)
    return eng, approver


@pytest.mark.parametrize("answer, runs", [("allow", 1), ("deny", 0), ("timeout", 0), ("unavailable", 0)])
def test_ask_first_runs_only_on_allow(tmp_path, logind, answer, runs):
    rec = Recorder()
    eng, approver = _ask_engine(tmp_path, rec, answer)
    rule = EventRule(name="lock-rule", event_type="screenlock", source="locked", actuator="notify",
                     arguments={"summary": "x"}, match_mode=MATCH_ANY, substring="", debounce_seconds=0.0,
                     cooldown_seconds=1.0, retry_policy=RETRY_RETRYABLE, ask_first=True)
    eng.store().add(rule)
    eng.poll(now=1.0)
    logind.write_text("yes")
    eng.poll(now=2.0)
    assert len(approver.asked) == 1 and "notify" in approver.asked[0][1]
    assert len(rec.calls) == runs
    assert eng.approval_log == [("lock-rule", answer)]


def test_ask_first_lets_a_rule_reach_a_destructive_actuator_only_with_approval():
    rule, why = triggers.build_event_rule(name="d", event_type="schedule", source="daily 08:00",
                                          actuator="delete_file", arguments={"path": "/tmp/x"},
                                          match_mode=MATCH_ANY, cooldown_seconds=1.0)
    assert rule is None and "destructive" in why
    rule, why = triggers.build_event_rule(name="d", event_type="schedule", source="daily 08:00",
                                          actuator="delete_file", arguments={"path": "/tmp/x"},
                                          match_mode=MATCH_ANY, cooldown_seconds=1.0, ask_first=True)
    assert rule is not None, why
    assert EventRule.from_dict(rule.to_dict()).ask_first is True


def test_approval_rechecks_consent_at_the_moment_of_allow(tmp_path, logind):
    class Revoked(AllowAll):
        def sense_allowed(self, sense):
            return False
    rec = Recorder()
    eng = EventEngine(store=EventRuleStore(tmp_path / "rules.json"), config_factory=Revoked, dispatch=rec,
                      fingerprint_store=DurableFingerprints(tmp_path / "fp.json"), approver=FakeApprover("allow"))
    eng.store().add(EventRule(name="r", event_type="screenlock", source="locked", actuator="notify",
                              arguments={}, match_mode=MATCH_ANY, substring="", debounce_seconds=0.0,
                              cooldown_seconds=1.0, retry_policy=RETRY_RETRYABLE, ask_first=True))
    ev = triggers.Event(kind="screenlock", subject="s", summary="locked", fingerprint="f", detail={})
    result = eng.run_approved("r", ev)
    assert rec.calls == [] and result.censored


def test_ask_parses_notify_send_answers(bindir):
    from shani_chronoa import approvals
    for printed, want in (("allow", "allow"), ("deny", "deny"), ("", "timeout")):
        _stub(bindir, "notify-send", f'echo "{printed}"\n')
        assert approvals.ask("t", "b", seconds=5) == want
    _stub(bindir, "notify-send", "exit 1\n")
    assert approvals.ask("t", "b", seconds=5) == "unavailable"
