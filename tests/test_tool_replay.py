"""Replay at the approval boundary (`tools.py`).

A duplicate action is born at the approval, not at the tool: two of the three
ways a person can say yes are reachable without the window focused, so a
notification pressed twice or a client that reconnects and replays its last
action runs the same destructive call twice.

These tests drive the **real skills** through `execute_tool_outcome` and count
what actually happened, because the thing being protected is a side effect on
the filesystem. A stub handler would not have caught the defect this found: the
first version cached `ran=True, UNVERIFIED`, which in Chronoa is the cell a
skill's *own* consent refusal lands in - so the first replay of a call that had
been refused handed back the stale refusal, and a later legitimate retry after
the permission was granted could never run. `_REPLAYABLE_VERDICTS` is the narrow
rule that avoids it; `test_a_refusal_is_never_cached` is why.

The keyfile gsettings backend is not incidental. `GSETTINGS_BACKEND=memory` is
per-process, so a grant made in the parent is invisible to the sandboxed child
that runs the skill - the refusal reads as "turned off" however the parent was
configured. The group is `[org.shani.chronoa]`, the schema id, because a
`[org/shani/chronoa]` group is silently ignored and every gate stays shut.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SRC = _REPO / "usr" / "lib" / "shani-chronoa"

#: The destructive tools with a post-condition, i.e. the ones that can reach
#: `VERIFIED` and therefore can be replay-protected. Kept as a list because the
#: coverage is the claim: if a post-condition is added elsewhere the coverage
#: widens, and this test should be the thing that says so rather than the thing
#: that quietly stops being true.
COVERED = ["airplane_mode", "control_service", "default_apps", "delete_file",
           "desktop_setting", "kill_process", "office_document", "print_queue",
           "set_hostname", "set_locale", "take_photo", "toggle_wifi"]


def _compile_schemas(directory: Path) -> None:
    source = _REPO / "usr" / "share" / "glib-2.0" / "schemas"
    shutil.copytree(source, directory, dirs_exist_ok=True)
    subprocess.run(["glib-compile-schemas", str(directory)], check=True)


def _keyfile(backend: Path, **settings) -> None:
    """A gsettings keyfile whose group is the *schema id*, not the id's path."""
    settings_dir = backend / "glib-2.0" / "settings"
    settings_dir.mkdir(parents=True, exist_ok=True)
    lines = "\n".join(f"{k}={'true' if v else 'false'}" for k, v in settings.items())
    (settings_dir / "keyfile").write_text(f"[org.shani.chronoa]\n{lines}\n")


def _isolate(monkeypatch, tools) -> None:
    """Leave nothing behind for the rest of the process.

    Both of these are module-level globals shared by every later test:

    - `_REACTIONS` counts calls per `(origin, tool)` over a five-minute window,
      and this file makes a dozen `delete_file` calls on purpose. Without a reset
      the next test that uses `delete_file` finds the repeat guard already
      tripped and reads *"has now touched 12 different targets"* - which is
      exactly what happened the first time this ran beside another file, and it
      looks like that file's bug.
    - `ask_bridge._presenter` is written directly here, because the fixture
      needs nobody to answer. Set through `monkeypatch` so it is put back; the
      AGENTS.md note about this global is that nothing clears it, which is true
      of the app and not true of a test that uses monkeypatch.
    """
    from shani_chronoa import ask_bridge
    monkeypatch.setattr(ask_bridge, "_presenter", None)
    tools._REACTIONS.reset()


@pytest.fixture
def granted(tmp_path, monkeypatch):
    """A real backend with `file-delete-enabled` genuinely on, for the child too."""
    backend = tmp_path / "gsettings"
    _compile_schemas(backend)
    _keyfile(backend, **{"file-delete-enabled": True, "file-edit-enabled": True,
                         "sound-control-enabled": True})
    monkeypatch.setenv("GSETTINGS_BACKEND", "keyfile")
    monkeypatch.setenv("GSETTINGS_SCHEMA_DIR", str(backend))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(backend))
    from shani_chronoa import config as config_mod, tools
    assert config_mod.ChronoaConfig().get_bool("file-delete-enabled", False) is True, (
        "the probe's own grant did not take, so everything below would measure "
        "a refusal rather than the thing under test")
    tools.clear_replays()
    _isolate(monkeypatch, tools)
    yield tools
    tools.clear_replays()
    tools._REACTIONS.reset()


@pytest.fixture
def shut(tmp_path, monkeypatch):
    """The same backend with deletion genuinely off, in the child as well."""
    backend = tmp_path / "gsettings"
    _compile_schemas(backend)
    _keyfile(backend, **{"file-delete-enabled": False})
    monkeypatch.setenv("GSETTINGS_BACKEND", "keyfile")
    monkeypatch.setenv("GSETTINGS_SCHEMA_DIR", str(backend))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(backend))
    from shani_chronoa import config as config_mod, tools
    assert config_mod.ChronoaConfig().get_bool("file-delete-enabled", False) is False
    tools.clear_replays()
    _isolate(monkeypatch, tools)
    yield tools
    tools.clear_replays()
    tools._REACTIONS.reset()


class TestTheSecondApprovalDoesNotDoTheThingTwice:
    def test_an_approved_delete_runs_once_and_answers_with_the_receipt(
            self, granted, tmp_path):
        target = tmp_path / "doomed.txt"
        target.write_text("x\n", encoding="utf-8")
        args = {"path": str(target)}

        first = granted.execute_tool_outcome("delete_file", dict(args))
        assert first.verdict.value == "verified", (
            f"the first delete did not verify, so this test measures nothing: "
            f"{first.verdict} {first.text!r}")
        assert not target.exists()

        second = granted.execute_tool_outcome("delete_file", dict(args))
        assert "[Not run again:" in second.text, (
            f"the replay ran the delete a second time: {second.text!r}")
        # The earlier result is still there: a replay is an answer, not a blank.
        assert first.text in second.text

    def test_the_note_never_stacks(self, granted, tmp_path):
        """The defect this shape of note invites, caught by the fifth call.

        Storing the *annotated* text would mean call three came back with the
        note twice and call four with it three times - a receipt that grows with
        every replay and reads like the tool ran that many times.
        """
        target = tmp_path / "doomed.txt"
        target.write_text("x\n", encoding="utf-8")
        granted.execute_tool_outcome("delete_file", {"path": str(target)})
        for call in range(5):
            text = granted.execute_tool_outcome(
                "delete_file", {"path": str(target)}).text
            assert text.count("[Not run again:") == 1, (
                f"call {call + 2} carries the note {text.count('[Not run again:')} "
                f"times: {text[:160]!r}")

    def test_a_different_path_is_a_different_action(self, granted, tmp_path):
        one, two = tmp_path / "one.txt", tmp_path / "two.txt"
        one.write_text("a\n", encoding="utf-8")
        two.write_text("b\n", encoding="utf-8")
        granted.execute_tool_outcome("delete_file", {"path": str(one)})
        result = granted.execute_tool_outcome("delete_file", {"path": str(two)})
        assert not two.exists(), (
            "a second, genuinely different deletion was swallowed as a replay")
        assert "[Not run again:" not in result.text

    def test_argument_order_does_not_make_two_actions_of_one(self, granted):
        """Keyed on the canonical form, not on the dict's insertion order.

        Asserted on `_replay_key` rather than through a second `edit_file`:
        `edit_file` has no post-condition, so it never reaches `VERIFIED` and
        never enters the cache at all - the run-twice result would be correct
        behaviour proving nothing about the key.
        """
        a = granted._replay_key("delete_file",
                                {"path": "/home/me/a", "recursive": True}, "user")
        b = granted._replay_key("delete_file",
                                {"recursive": True, "path": "/home/me/a"}, "user")
        assert a is not None and a == b, (
            f"the same call keyed twice: {a!r} vs {b!r}")

    def test_the_origin_is_part_of_what_makes_a_call_the_same(self, granted):
        """A trigger-driven turn and a typed one are two different decisions.

        This is the gateway's grant being keyed on channel, one level down: two
        origins reaching identical arguments are not one decision, and collapsing
        them would let an unattended turn be answered with a person's receipt.
        """
        args = {"path": "/home/me/a"}
        assert (granted._replay_key("delete_file", args, "user")
                != granted._replay_key("delete_file", args, "unattended"))


class TestWhatIsNeverReplayed:
    def test_a_refusal_is_never_cached(self, shut, tmp_path):
        """The defect the first version of this had.

        A refusal inside a skill is `ran=True, UNVERIFIED` - the same cell as a
        success - so caching that cell means a replay hands back the refusal. A
        later legitimate retry, after the permission is granted, then cannot run
        at all. The file below is the one the first version deleted the entry
        for.
        """
        keep = tmp_path / "keep.txt"
        keep.write_text("keep\n", encoding="utf-8")
        result = shut.execute_tool_outcome("delete_file", {"path": str(keep)})
        assert keep.exists(), "the probe granted permission it meant to withhold"
        assert "Refusing" in result.text
        assert result.ran and result.verdict.value != "verified"
        assert len(shut._REPLAYS) == 0, (
            "a refusal was cached, so a retry after the permission is granted "
            f"would be answered with stale bad news: {list(shut._REPLAYS)}")

    def test_a_failure_is_never_cached(self, granted, tmp_path):
        """Running again is the recovery from a failure, so it must stay possible."""
        from shani_chronoa import verification
        key = granted._replay_key("delete_file", {"path": str(tmp_path / "x")}, "user")
        granted._replay_put(key, granted.DispatchResult(
            "it did not hold", verification.Verdict.FAILED, True))
        assert granted._replay_get(key) is None

    def test_an_ungated_tool_is_never_a_candidate(self, granted):
        assert granted._replay_key("set_volume", {"percent": 10}, "user") is None, (
            "an ungated tool in the cache would answer a later call with a "
            "stale value - a get_datetime from last minute reads as this second")

    def test_a_receipt_does_not_survive_the_permission_that_authorised_it(self,
                                                                         granted, tmp_path,
                                                                         monkeypatch):
        """The defect `test_matrix_skills.py` found in this feature.

        An earlier test in that file ran the same `set_locale` call with the key
        granted and verified it; the refusal test then ran the identical call with
        the key shut and was answered with the stored receipt. So a test asserting
        "this is refused" read "this is done" - and the product defect underneath
        is the same one: **the permission changed between the two calls, so they
        are not the same call.**
        """
        from shani_chronoa import config as config_mod
        target = tmp_path / "doomed.txt"
        target.write_text("x\n", encoding="utf-8")
        args = {"path": str(target)}
        assert granted.execute_tool_outcome("delete_file", dict(args)).verdict.value \
            == "verified"

        granted_key = granted._replay_key("delete_file", args, "user")
        assert granted._replay_get(granted_key) is not None, (
            "nothing was cached, so this test cannot see the receipt cross the "
            "permission boundary")

        class Revoked:
            def get_bool(self, key, default=False):
                return False if key == "file-delete-enabled" else default

        monkeypatch.setattr(config_mod, "ChronoaConfig", lambda *a, **k: Revoked())
        shut_key = granted._replay_key("delete_file", args, "user")
        assert shut_key != granted_key, (
            "the permission is not in the key, so a receipt outlives the grant "
            "that authorised it")
        assert granted._replay_get(shut_key) is None

    def test_an_expired_entry_is_not_a_replay(self, granted, tmp_path, monkeypatch):
        target = tmp_path / "doomed.txt"
        target.write_text("x\n", encoding="utf-8")
        granted.execute_tool_outcome("delete_file", {"path": str(target)})
        monkeypatch.setattr(granted, "REPLAY_TTL_SECONDS", 0.0)
        result = granted.execute_tool_outcome("delete_file", {"path": str(target)})
        assert "[Not run again:" not in result.text, (
            "an entry past its TTL is still being served, so the window nobody "
            "chose is in fact unbounded")

    def test_the_store_is_bounded(self, granted):
        from shani_chronoa import verification
        granted._REPLAY_CAPACITY = 4
        granted.REPLAY_CAPACITY = 4
        for i in range(20):
            granted._replay_put((f"tool{i}", "{}", "user"), granted.DispatchResult(
                "ok", verification.Verdict.VERIFIED, True))
        assert len(granted._REPLAYS) <= granted.REPLAY_CAPACITY


class TestThePersonIsStillAskedEveryTime:
    def test_the_replay_check_sits_after_the_consent_decision(self, granted):
        """Asked again every time; only the second *action* is skipped.

        `permissions.is_bypass_immune` promises that a destructive tool never
        answers from a standing grant, and "allow once" means once. A replay
        placed *above* the consent decision would quietly undo both.

        An ordering claim, asserted as ordering. The runtime version of it needs
        a consent key that is shut *and* grantable *and* readable by the sandboxed
        child, which is three fixtures arranged to test a fact about source order
        - and this repository's recorded lesson is that a source scan is the
        right shape for "which of these runs first". The scan reads the function
        body, so it also has a control: the same scan on the module with the
        check hoisted fails.
        """
        import ast
        source = Path(granted.__file__).read_text(encoding="utf-8")
        def order_in(source_text):
            """Line numbers of `decide` and `_replay_get`, in **source** order.

            Sorted explicitly: `ast.walk` is breadth-first, so its natural order
            is not the order the statements run in, and the first version of
            this reported `replay` before `decide` for a function where the
            opposite is true.
            """
            tree = ast.parse(source_text)
            fn = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef) and n.name == "_dispatch_inner")
            found = []
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                target = node.func
                if isinstance(target, ast.Attribute) and target.attr == "decide":
                    found.append((node.lineno, "decide"))
                elif isinstance(target, ast.Name) and target.id == "_replay_get":
                    found.append((node.lineno, "replay"))
            return [label for _, label in sorted(found)]

        assert order_in(source)[:2] == ["decide", "replay"], (
            "the replay lookup is not after the consent decision: "
            f"{order_in(source)}. Moving it earlier lets a cached receipt answer "
            f"a call the person was never asked about.")

        # The control: the scanner must report each order correctly on inputs
        # whose answer is known. Patching the real source did not work for this -
        # the line it inserted still fell after `decide` - which is the recorded
        # lesson that a control has to actually change what it is testing.
        template = ("def _dispatch_inner():\n"
                    "{first}\n"
                    "    permissions.decide(name, None, 'k')\n"
                    "    already = _replay_get(key)\n"
                    "    return already\n")
        correct = template.format(first="    pass")
        assert order_in(correct)[:2] == ["decide", "replay"]
        wrong = template.format(first="    already = _replay_get(key)")
        assert order_in(wrong)[:2] == ["replay", "decide"], (
            "the scanner cannot see the wrong order, so it proves nothing "
            "about the real one")


class TestTheClaimedCoverage:
    def test_the_tools_named_as_covered_still_have_post_conditions(self):
        """The coverage list in `tools.py` must not rot.

        It is a list of names in a comment, which is the exact shape that goes
        stale - the same defect as `files._PACKAGE_HINTS` and the four-way
        cascade count that was wrong the day a fifth engine landed. Asserted
        against the live registry so adding a post-condition widens the claim
        visibly instead of quietly.
        """
        from shani_chronoa import capabilities, tools, verification
        covered = []
        for name in capabilities.GATED:
            handler = tools._HANDLER_FNS.get(name)
            if handler and verification.post_condition_for(handler.__module__):
                covered.append(name)
        assert sorted(COVERED) == sorted(covered), (
            "the list in tools.py's _REPLAYABLE_VERDICTS comment is now wrong: "
            f"the registry says {sorted(covered)}. Update the comment and this "
            f"test together.")

    def test_replayable_means_verified_only(self):
        """The narrow rule, asserted as a property rather than a comment."""
        from shani_chronoa import verification, tools
        assert tuple(tools._REPLAYABLE_VERDICTS) == (verification.Verdict.VERIFIED,), (
            "a refusal and a success share the UNVERIFIED cell, so caching it "
            "makes a legitimate retry impossible")
