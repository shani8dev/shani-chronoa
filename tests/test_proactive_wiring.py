"""Focused tests: the five proactive modules reach the doors they were for."""

import inspect
from shani_chronoa import daemon, goals, inotify_watch, planner, reflex, rhythm, tool_tracking
from shani_chronoa.skills import manage_triggers
from shani_chronoa.triggers import sources

# setpoints -> manage_triggers

def test_setpoint_actions_are_advertised():
    assert {"setpoints", "describe-setpoint", "arm-setpoint"} <= set(manage_triggers._ACTIONS)
    text = manage_triggers._run({"action": "setpoints"})
    assert "disk-nearly-full" in text and "overheating" in text and "off until you arm" in text

def test_arm_setpoint_keeps_the_trigger_control_gate():
    text = manage_triggers._run({"action": "arm-setpoint", "setpoint": "disk-nearly-full"})
    assert "Refusing to change armed rules" in text and "trigger-control-enabled" in text

def test_arm_setpoint_uses_the_ordinary_rule_builder(monkeypatch, tmp_path):
    from shani_chronoa.triggers.rules import RuleStore
    class Config:
        def get_bool(self, key, default=False):
            return key in ("trigger-control-enabled", "storage-sense-enabled")
        def sense_allowed(self, sense):
            return sense == "storage"
        def sense_allowed_reason(self, sense):
            return "the storage sense is on"
    store = RuleStore(path=tmp_path / "rules.json")
    monkeypatch.setattr(manage_triggers, "ChronoaConfig", Config)
    monkeypatch.setattr(manage_triggers, "_open", lambda kind: (store, ""))
    text = manage_triggers._run({"action": "arm-setpoint", "setpoint": "disk-nearly-full"})
    assert "Armed 'disk-nearly-full'" in text
    assert [r.name for r in store.all()] == ["disk-nearly-full"]
    assert store.get("disk-nearly-full").actuator == "notify"

# salience -> unattended announcement path

def test_reflex_announce_scores_queues_and_holds(monkeypatch):
    reflex.clear_pending()
    monkeypatch.setattr(reflex, "_do_not_disturb", lambda: False)
    urge = reflex.Urgence(True, reflex.URGENCY_HIGH, "Battery", "8 percent")
    decided = reflex.announce([urge], rhythm_state=rhythm.Rhythm.ACTIVE, now=100.0)
    assert decided and decided[0][0] is urge and decided[0][1] in ("speak", "notify")

def test_dnd_holds_an_announcement_without_dropping_it(monkeypatch):
    reflex.clear_pending()
    monkeypatch.setattr(reflex, "_do_not_disturb", lambda: True)
    urge = reflex.Urgence(True, reflex.URGENCY_HIGH, "Battery", "8 percent")
    assert reflex.announce([urge], rhythm_state=rhythm.Rhythm.ACTIVE, now=100.0) == []
    monkeypatch.setattr(reflex, "_do_not_disturb", lambda: False)
    again = reflex.announce([], rhythm_state=rhythm.Rhythm.ACTIVE, now=101.0)
    assert [(u.subject, d) for u, d in again] == [("Battery", "notify")]
    reflex.clear_pending()

def test_a_sleeping_rhythm_holds_routine_announcements(monkeypatch):
    reflex.clear_pending()
    monkeypatch.setattr(reflex, "_do_not_disturb", lambda: False)
    urge = reflex.Urgence(True, reflex.URGENCY_LOW, "Clock", "unsynchronised")
    assert reflex.announce([urge], rhythm_state=rhythm.Rhythm.SLEEP, now=100.0) == []
    reflex.clear_pending()

def test_notify_does_not_say_held_announcements(monkeypatch):
    reflex.clear_pending()
    monkeypatch.setattr(reflex, "_do_not_disturb", lambda: True)
    monkeypatch.setattr(reflex.shutil, "which", lambda name: "/usr/bin/notify-send")
    calls = []
    monkeypatch.setattr(reflex.subprocess, "run", lambda *a, **k: calls.append(a))
    urge = reflex.Urgence(True, reflex.URGENCY_HIGH, "Battery", "8 percent")
    assert reflex.notify([urge]) == [] and calls == []
    reflex.clear_pending()

# reward -> outcome recording

def test_recorded_outcomes_carry_an_advisory_reward(tmp_path):
    t = tool_tracking.ToolTracker(log_dir=tmp_path)
    good = t.record_call("x", {}, "ok", 1.0, verdict="verified")
    bad = t.record_call("x", {}, "bad", 1.0, verdict="failed")
    approved = t.record_call("x", {}, "ok", 1.0, verdict="unverified",
                             origin=tool_tracking.ORIGIN_APPROVED)
    assert (good.reward, bad.reward, approved.reward) == (1.0, -1.0, 0.5)
    assert good.to_dict()["reward"] == 1.0

# planner -> goals.advance

def _step(name, *, priority=0, depends=()):
    return goals.PlannedStep(thought=name, tool_name="echo", tool_arguments={},
                             variable_name=name, depends_on=tuple(depends), priority=priority)

def test_advance_orders_ready_steps_by_priority():
    run = goals.GoalRun(id="g", goal="order", phase=goals.Phase.RUNNING,
                        steps=(_step("a"), _step("b", priority=5), _step("c", priority=1)))
    seen = []
    out = goals.advance(run, lambda step, results: seen.append(step.variable_name) or "ok")
    assert seen == ["b"] and out.index == 1

def test_advance_replans_a_failed_step_and_runs_the_replacement():
    calls = []
    def execute(step, results):
        calls.append(step.variable_name)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return "done"
    run = goals.GoalRun(id="g", goal="repair", phase=goals.Phase.RUNNING, steps=(_step("first"),))
    out = goals.advance(run, execute, replan=lambda failed, results: [_step("replacement")])
    assert calls == ["first", "first"]
    assert out.phase is goals.Phase.COMPLETED and out.results["first"] == "done"
    assert planner.replan_count(out) == 1

def test_advance_without_a_replan_keeps_the_old_failure_behaviour():
    def execute(step, results):
        raise RuntimeError("boom")
    run = goals.GoalRun(id="g", goal="repair", phase=goals.Phase.RUNNING, steps=(_step("first"),))
    assert goals.advance(run, execute).phase is goals.Phase.FAILED

# inotify_watch -> triggers/sources

def test_default_watcher_uses_the_backend_and_is_cached(monkeypatch, tmp_path):
    built = []
    monkeypatch.setattr(inotify_watch, "build_watcher", lambda: built.append(1) or object())
    sources._WATCHERS.clear()
    first, second = sources._default_watcher(tmp_path), sources._default_watcher(tmp_path)
    assert first is second and len(built) == 1
    sources._WATCHERS.clear()

def test_a_supplied_watcher_is_still_used_untouched(monkeypatch, tmp_path):
    class Watcher:
        def poll(self, root):
            return ["changed"]
    monkeypatch.setattr(sources, "_confine_to_home", lambda raw: (tmp_path, ""))
    signal = sources.read_watched_path(str(tmp_path), watcher=Watcher())
    assert signal.status == sources.SIGNAL_OK and "changed" in str(signal.payload)

# daemon rhythm consultation

def test_daemon_consultes_should_run_rules_with_the_tick_state():
    source = inspect.getsource(daemon.main)
    assert "monitor.tick().state" in source and "rhythm.should_run_rules(state)" in source
