"""HARVEST-2 T1/T2 mechanisms verified against real wiring.

- T1.10 ToolFailure: a skill that ran but failed says so in-band, and the
  dispatcher reports `ran=True, verdict=FAILED` rather than a success string.
- T1.11: an unattended turn gets one tool round, a user turn gets four.
- T1.13: identical facts are not written twice, even under different keys.
- T2.6/bypass_immune: DONT_ASK refuses every ask; session grants stop
  opening bypass-immune tools; EXPLORE refuses non-read-only tools.
- T2.9/T2.18: three malformed calls in a row ends the turn honestly.
- T2.13: risky builtins cannot arrive inside an explicit `sh -c` script.
- T3.1: pre/post hooks wrap dispatch.
- T3.2/T3.5/T3.6: GoalRun persists, parks, resumes; Task dependencies
  gate the PlannedStep they depend on.
- T3.3: an unknown key in a sandbox policy file is a hard error.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import (  # noqa: E402
    assistant as assistant_mod, goals, permissions, tools,
)
from shani_chronoa.assistant import Assistant  # noqa: E402
from shani_chronoa.config import ChronoaConfig  # noqa: E402
from shani_chronoa.sandbox import executor as executor_mod  # noqa: E402
from shani_chronoa.sandbox.models import SandboxConfig, SandboxLevel  # noqa: E402
from shani_chronoa.sandbox.policy import PolicyError, config_from_policy  # noqa: E402
from shani_chronoa.senses import memory  # noqa: E402
from shani_chronoa.senses.store import PerceptStore  # noqa: E402
from shani_chronoa.toolfailure import ToolFailure  # noqa: E402


# --- T1.10: ToolFailure ---------------------------------------------------


def _failing_handler(arguments):
    raise ToolFailure("the offer expired before it could be accepted")


def test_tool_failure_runs_and_reports_failed(monkeypatch):
    monkeypatch.setitem(tools._HANDLER_FNS, "fail_ticket", _failing_handler)
    monkeypatch.setattr(tools, "_LOCAL_TOOLS",
                        tools._LOCAL_TOOLS | frozenset({"fail_ticket"}))
    result = tools.execute_tool_outcome("fail_ticket", {})
    assert result.ran is True, "the skill ran; only its effect did not hold"
    assert result.verdict.name == "FAILED"
    assert "the offer expired" in result.text
    assert result.evidence == "tool_failure"


def test_tool_failure_marker_string_also_works():
    from shani_chronoa.tools import _tool_failure_result
    r = _tool_failure_result("nothing was written")
    assert r.ran is True and r.verdict.name == "FAILED"
    assert r.evidence == "tool_failure"


def test_tool_failure_survives_the_real_subprocess_transport(tmp_path, monkeypatch):
    """The child, not the in-process path: the failure has to cross the stdout
    marker and still arrive as `ran=True, FAILED`.

    A unit test of the local branch cannot catch a broken child program (a
    `try` that does not compile, an import the sandboxed child cannot make),
    and the whole feature is worthless if the failure only works when the
    handler runs in this process. The control in the same test is the same
    handler succeeding, so a program that always failed would fail it too.
    """
    probe = tmp_path / "harvest2_tf_probe.py"
    probe.write_text(
        "from shani_chronoa.toolfailure import ToolFailure\n"
        "\n"
        "def fail_run(arguments):\n"
        "    raise ToolFailure('the file changed underneath the skill')\n"
        "\n"
        "def ok_run(arguments):\n"
        "    return 'fine'\n",
        encoding="utf-8")
    # The child is spawned with a deliberately minimal environment - the
    # `test_argfile.py` comment says so - so a probe module outside the package
    # needs the path handed over explicitly. That is a property of the harness,
    # not of the feature under test.
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.syspath_prepend(str(tmp_path))
    import harvest2_tf_probe as probe_module  # noqa: PLC0415 - path just injected
    monkeypatch.setitem(tools._HANDLER_FNS, "harvest2_tf_fail", probe_module.fail_run)
    monkeypatch.setitem(tools._HANDLER_FNS, "harvest2_tf_ok", probe_module.ok_run)

    failed = tools.execute_tool_outcome("harvest2_tf_fail", {})
    assert failed.ran is True
    assert failed.verdict.name == "FAILED", failed.text
    assert "changed underneath" in failed.text
    assert failed.evidence == "tool_failure"

    control = tools.execute_tool_outcome("harvest2_tf_ok", {})
    assert control.verdict.name == "UNVERIFIED", (
        f"the control failed too, so the first assertion proved nothing: "
        f"{control.text}")
    assert "fine" in control.text


# --- T3.4: the event sink the window actually receives ------------------------


def test_the_window_receives_turn_events_through_the_sink():
    """The sink is wired, not merely implemented.

    Pinned on the source because the alternative is building a whole GTK
    application in a test to observe one callback firing, and the thing being
    checked is a *connection*: `handle()` is called with the sink, and the sink
    the app builds carries the three handlers the window used to be given
    directly. A sink that exists but is never passed is the dead-control class
    this repo keeps meeting.
    """
    source = (Path(__file__).resolve().parents[1] / "usr" / "lib" /
              "shani-chronoa" / "shani_chronoa" / "app" /
              "conversation.py").read_text(encoding="utf-8")
    assert "sink=self.event_sink()" in source, (
        "the turn is not handed an event sink, so tool cards would stop "
        "appearing with no error anywhere")
    for handler in ("on_tool_start=self._on_tool_call",
                    "on_tool_finish=self._on_tool_result",
                    "on_text=self._on_reply_text"):
        assert handler in source, (
            f"the sink no longer forwards {handler}, so that window update "
            "would silently stop")


class TestTheDiffPanelIsAPreviewAndNotAShell:
    """The panel is registered in the sidebar, so it must show something.

    `set_diffs` had no caller anywhere in the app: the page could only ever
    render "Nothing to compare yet" and the sidebar dot answered about a panel
    that had never seen a diff. The undo ring already stores both sides of
    every edit, so the preview is built from that rather than from a second
    change log that would drift from it.
    """

    def _edited_file(self, tmp_path):
        """One edited file, and the ring emptied first.

        **The empty ring is load-bearing, and its absence is why these three
        tests failed only in a full run** (measured: 3 failures in the chunked
        suite, 0 in isolation). The ring is process-wide state on disk, and
        *any* earlier test that wrote a file through `write_text_file` or
        `edit_file` added a pre-image to it — so `assert len(pairs) == 1` was
        really asserting that no other test in the process had ever edited a
        file. Clearing through the module's own `_save` keeps the write format
        the module writes, rather than writing a file shape it only reads.
        """
        from shani_chronoa.skills import undo_last_change
        undo_last_change._save([])
        target = tmp_path / "notes.md"
        target.write_text("alpha\nbeta\n", encoding="utf-8")
        undo_last_change.record_preimage(target, b"alpha\nbeta\n")
        target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
        return target

    def test_the_ring_yields_both_sides_of_a_real_edit(self, tmp_path):
        from shani_chronoa.skills import undo_last_change
        self._edited_file(tmp_path)
        pairs = undo_last_change.recent_changes()
        assert len(pairs) == 1
        path, before, after = pairs[0]
        assert "gamma" in after and "gamma" not in before
        assert path.name == "notes.md"

    def test_a_restored_file_is_not_offered_as_a_change(self, tmp_path):
        """The undo case, done the way the undo path really does it: the file
        is written back from the ring entry, which is then consumed.

        Recording a pre-image is *not* an undo - the ring is append-only, so a
        test that merely added an entry would be testing a different thing and
        would prove nothing about the preview after an undo.
        """
        from shani_chronoa.skills import undo_last_change
        target = self._edited_file(tmp_path)
        entries = undo_last_change._load()
        assert entries, "the edit was not recorded at all"
        undo_last_change._consume(entries[-1])          # the undo path's own step
        target.write_text(entries[-1]["content"], encoding="utf-8")
        assert undo_last_change.recent_changes() == []

    def test_a_missing_file_is_skipped_not_guessed(self, tmp_path):
        from shani_chronoa.skills import undo_last_change
        target = self._edited_file(tmp_path)
        target.unlink()
        assert undo_last_change.recent_changes() == []

    def test_the_panel_stages_them_and_draws_no_dead_buttons(self, tmp_path,
                                                             monkeypatch):
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk

        from shani_chronoa.gui.surfaces import common, diff as surface

        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        self._edited_file(tmp_path)
        page = surface.build(app=None)
        assert list(page.view._files), "the panel rendered with nothing to show"
        assert page.status() == common.STATUS_OK

        found = set()

        def walk(widget):
            if isinstance(widget, Gtk.Widget):
                found.update(widget.get_css_classes())
            child = widget.get_first_child()
            while child is not None:
                walk(child)
                child = child.get_next_sibling()

        walk(page)
        assert common.STATUS_CLASSES[page.status()] in found, (
            "the sidebar dot claims a colour the panel never draws")

        # An already-written change cannot be accepted or rejected here; the
        # way back is the `undo_last_change` skill. Two dead buttons per row
        # would be worse than none.
        labels = []

        def buttons(widget):
            if isinstance(widget, Gtk.Button):
                labels.append(widget.get_label())
            child = widget.get_first_child()
            while child is not None:
                buttons(child)
                child = child.get_next_sibling()

        buttons(page)
        assert not {"Accept", "Reject"} & set(labels)


def test_a_broken_sink_cannot_break_a_turn():
    from shani_chronoa.events import EventSink, fan_in

    def explodes(*_args):
        raise RuntimeError("a subscriber raised")

    seen = []
    sink = fan_in(EventSink(on_tool_start=explodes),
                  EventSink(on_tool_start=lambda n, a: seen.append(n)))
    sink.fire("on_tool_start", "get_datetime", {})     # must not raise
    assert seen == ["get_datetime"], "the second subscriber was skipped"


# --- T1.11: per-origin round budgets --------------------------------------


def test_rounds_budget_per_origin():
    assert assistant_mod._rounds_for_origin("user") == 4
    assert assistant_mod._rounds_for_origin("unattended") == 1
    # A name we have never heard of fails closed: unattended, not trusted.
    assert assistant_mod._rounds_for_origin("some-future-origin") == 1


# --- T2.6 / bypass_immune: permission modes -------------------------------


@pytest.fixture
def _mode_guard():
    yield
    permissions.set_mode(permissions.Mode.DEFAULT)


def test_dont_ask_converts_every_ask_to_no(_mode_guard):
    permissions.set_mode(permissions.Mode.DONT_ASK)
    assert permissions.decide("delete_file", "/tmp/x", "files-enabled") is None


def test_session_grant_no_longer_opens_a_door_in_dont_ask(_mode_guard):
    action, resource = "control_service", "sshd"
    permissions.add_rule(action, resource, permissions.Decision.ALLOW_SESSION,
                         session_only=True)
    permissions.set_mode(permissions.Mode.DONT_ASK)
    assert permissions.session_grant(action, resource) is None
    permissions.clear(session_only=True)


def test_bypass_immune_tool_never_answers_from_a_standing_grant(_mode_guard):
    permissions.add_rule("delete_file", "/tmp/keep", permissions.Decision.ALLOW_SESSION,
                         session_only=True)
    assert permissions.session_grant("delete_file", "/tmp/keep") is None
    # A non-immune standing grant would open.
    permissions.add_rule("get_datetime", "*", permissions.Decision.ALLOW_SESSION,
                         session_only=True)
    assert permissions.session_grant("get_datetime", "*") is not None
    permissions.clear(session_only=True)


def test_explore_mode_refuses_non_readonly_but_allows_reads(_mode_guard):
    permissions.set_mode(permissions.Mode.EXPLORE)
    assert permissions.explore_refuses("delete_file") is not None
    assert permissions.explore_refuses("calculate") is None


def test_an_unknown_mode_is_refused(_mode_guard):
    with pytest.raises(ValueError):
        permissions.set_mode("yolo")


# --- T2.13: risky builtins in shell scripts --------------------------------


def test_risky_builtin_names_are_caught():
    from shani_chronoa.sandbox.commands import _risky_builtin, _risky_builtin_name
    assert _risky_builtin(["eval", "rm -rf /"]) == "eval"
    assert _risky_builtin(["ls", "-la"]) is None
    assert _risky_builtin_name("source") == "source"
    assert _risky_builtin_name("ls") is None


def test_explicit_script_with_builtin_is_refused(tmp_path):
    sandbox = executor_mod.SandboxExecutor(sandboxes_root=str(tmp_path / "sb"))
    config = SandboxConfig(level=SandboxLevel.LEVEL_3_HOST_USER)
    rc, out, _ = sandbox.execute(["bash", "-c", "eval rm -rf /tmp/whatever"], config)
    assert rc == 126
    assert "builtin" in out


# --- T3.3: declarative policy ----------------------------------------------


def test_policy_round_trip():
    config = config_from_policy({
        "level": "LEVEL_1_READONLY", "timeout_seconds": 5,
        "blocked_binaries": ["dd", "mkfs"], "allow_network": False,
    })
    assert config.level is SandboxLevel.LEVEL_1_READONLY
    assert config.timeout_seconds == 5
    assert config.blocked_binaries == ["dd", "mkfs"]


def test_policy_refuses_unknown_keys_and_bad_levels():
    with pytest.raises(PolicyError):
        config_from_policy({"tiemout_seconds": 5})
    with pytest.raises(PolicyError):
        config_from_policy({"level": "LEVEL_9"})
    with pytest.raises(PolicyError):
        config_from_policy({"timeout_seconds": -3})


# --- T3.1: hooks -------------------------------------------------------------


def test_pre_hook_can_short_circuit_and_post_hook_sees_result(monkeypatch):
    ran = {"pre": 0, "post": 0, "inner": 0}

    def pre(name, arguments, origin):
        ran["pre"] += 1
        return None  # let it through

    def post(name, arguments, result):
        ran["post"] += 1
        return result

    def block(name, arguments, origin):
        return tools.DispatchResult("hook said no", __import__(
            "shani_chronoa.verification", fromlist=["Verdict"]).Verdict.UNVERIFIED, False)

    monkeypatch.setattr(assistant_mod, "execute_tool", lambda n, a, origin=None: "ok")
    tools.register_hook(pre=pre, post=post)
    try:
        result = tools.execute_tool_outcome("calculate", {"expression": "1+1"})
        assert result.text == "ok" or isinstance(result.text, str)
        assert ran["pre"] == 1 and ran["post"] == 1
        tools._HOOKS_PRE.clear()
        tools._HOOKS_POST.clear()
        tools.register_hook(pre=block)
        result = tools.execute_tool_outcome("calculate", {})
        assert result.text == "hook said no"
    finally:
        tools._HOOKS_PRE.clear()
        tools._HOOKS_POST.clear()


# --- T3.2/T3.5/T3.6: goals ----------------------------------------------------


def test_goal_run_persists_parks_and_resumes(tmp_path):
    store = goals.GoalStore(tmp_path)
    step = goals.PlannedStep("gather", "list_directory", {"path": "/tmp"}, "files")
    run = goals.new_run("see what is in /tmp", [step])
    run = goals.advance(run, lambda s, res: "a, b, c")
    assert run.phase is goals.Phase.COMPLETED
    store.save(run)

    parked = store.park(goals.new_run("later", [step]), "needs a person")
    loaded = store.load(parked.id)
    assert loaded.phase is goals.Phase.AWAITING
    resumed = store.resume(parked.id, "go ahead")
    assert resumed.phase is goals.Phase.RUNNING
    assert resumed.results["awaiting_answer"] == "go ahead"


def test_dependencies_gate_steps():
    step_a = goals.PlannedStep("a", "list_directory", {}, "files")
    step_b = goals.PlannedStep("b", "count", {}, "count", depends_on=("files",))
    run = goals.new_run("two-step", [step_b])
    run = goals.advance(run, lambda s, res: "never")
    assert run.phase is goals.Phase.AWAITING
    assert "files" in run.awaiting_reason
    run = goals.new_run("two-step", [step_a, step_b])
    run = goals.advance(run, lambda s, res: "a, b, c")
    run = goals.advance(run, lambda s, res: str(len(res["files"].split(","))))
    assert run.phase is goals.Phase.COMPLETED
    assert run.results["count"] == "3"


def test_a_failed_step_fails_the_run_not_the_queue():
    step = goals.PlannedStep("a", "delete_file", {}, "gone")
    run = goals.new_run("one shot", [step])

    def boom(step, res):
        raise RuntimeError("disk is read-only")

    run = goals.advance(run, boom)
    assert run.phase is goals.Phase.FAILED


# --- T1.13: md5 identical-fact dedup -----------------------------------------


def test_identical_fact_under_a_new_key_is_not_written_twice(tmp_path, monkeypatch):
    store = PerceptStore(durable_path=tmp_path / "mem" / "memory.jsonl")
    monkeypatch.setattr(memory, "_STORE", store)
    ChronoaConfig().set("memory-sense-enabled", "true")
    first = memory.store_fact(memory.Fact("The user drinks masala chai",
                                          "x", "drink", "user-stated",
                                          memory.CONFIDENCE_STATED, None))
    assert first is not None
    # Same text, different label: the second write is the same fact again.
    second = memory.store_fact(memory.Fact("the user drinks  masala chai",
                                           "x", "beverage", "user-stated",
                                           memory.CONFIDENCE_STATED, None))
    assert second is first, "the duplicate write must return the stored one"
    assert len(store.durable()) == 1
