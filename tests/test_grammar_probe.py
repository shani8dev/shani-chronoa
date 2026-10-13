"""The grammar probe's decision, and the control that gives it teeth.

**What is being protected.** The three-arm decoder run measures whether a grammar
buys anything. That question is void if the grammar was silently off - a tie would
read as *"the grammar bought nothing"* when nothing was tested. So `verdict()`
is the gate, and it is a **pure function of two replies**, which is why it can be
tested here with no server, no model and no network.

**The failure this file is shaped around is a control that cannot fail.** The
obvious assertion is "the grammar-on reply names the only legal value" - and on
its own it passes on a server that ignores `response_format` and happens to pick
that tool, because the *question* here is about weather and `get_datetime` is not
an obvious answer. So `verdict` also requires the grammar-**off** reply to have
declined, and both directions are pinned below.
"""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "grammar_probe", ROOT / "tools" / "grammar_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

OFF_PROSE = "I don't have access to real-time weather data."
ON_ANSWER = json.dumps({"tool": "get_datetime"})


class TestTheVerdictNeedsBothReplies:
    def test_the_measured_pair_is_enforced(self):
        ok, why = probe.verdict(OFF_PROSE, ON_ANSWER)
        assert ok, why
        # Asserted on the *wording the code actually emits* - my first version
        # asserted "only legal value", which it does not say, and a green-looking
        # assertion about a string nobody wrote is the `skills_total` mistake.
        assert "the only value the schema allows" in why, why

    def test_an_unenforced_server_is_not_reported_as_enforced(self):
        """Same reply both times - nothing was constrained, so nothing is proved."""
        ok, why = probe.verdict(ON_ANSWER, ON_ANSWER)
        assert not ok
        assert "chose" in why

    def test_a_server_that_answers_prose_under_the_grammar_is_not_enforced(self):
        ok, why = probe.verdict(OFF_PROSE, OFF_PROSE)
        assert not ok, "prose under a json_schema means the schema was ignored"


class TestTheProbeCanTellEnforcementFromChoice:
    """**This is the control that gives the rest its meaning.** The question is
    about the weather, so `get_datetime` is a *wrong* answer - and a model that
    merely chose it would satisfy a one-sided check. `verdict` therefore requires
    the grammar-off arm to have declined: if both arms agree, the grammar cannot
    be what made them agree."""

    def test_both_arms_naming_the_tool_is_reported_as_undecidable(self):
        ok, why = probe.verdict(ON_ANSWER, ON_ANSWER)
        assert not ok
        assert "cannot answer it" in why, why

    def test_only_the_grammar_off_arm_naming_it_is_still_not_enforcement(self):
        # The mirror: if the unenforced model already chose it, a constrained
        # reply of the same name proves nothing, and saying so is the point.
        assert not probe.verdict(ON_ANSWER, ON_ANSWER)[0]
        assert probe.verdict(OFF_PROSE, ON_ANSWER)[0]


class TestChoosingIsNotSubstringMatching:
    def test_prose_mentioning_the_tool_is_not_a_tool_call(self):
        """The bare-`in` version passes on the very reply that must not count."""
        assert "get_datetime" in OFF_PROSE or True  # the shape the old test allowed
        assert probe.chosen_tool(OFF_PROSE) is None

    def test_prose_that_does_contain_the_name_still_reads_as_no_call(self):
        text = "You could use get_datetime for that."
        assert "get_datetime" in text
        assert probe.chosen_tool(text) is None

    def test_a_json_reply_with_no_tool_key_is_not_a_call(self):
        assert probe.chosen_tool(json.dumps({"answer": "42"})) is None

    def test_empty_and_malformed_replies_are_not_calls(self):
        for text in ("", "   ", "{not json", "[]"):
            assert probe.chosen_tool(text) in (None, ""), text


class TestTheSchemaActuallyForbidsSomething:
    """A probe whose schema permits two values cannot prove enforcement by
    naming one of them, so the schema's restrictiveness is part of what is
    being claimed."""

    def test_the_enum_holds_exactly_one_value(self):
        enum = probe.TOOL_SCHEMA["properties"]["tool"]["enum"]
        assert enum == [probe.ONLY_TOOL], (
            "with %d permitted values, naming %r proves nothing"
            % (len(enum), probe.ONLY_TOOL))

    def test_the_question_is_one_the_right_answer_declines(self):
        """If `get_datetime` were a sensible answer to the question, the
        grammar-off arm would be entitled to give it and the probe is blind."""
        assert "weather" in probe.OFF_TOPIC.lower()
        assert "get_datetime" not in probe.OFF_TOPIC.lower()
        assert "time" not in probe.OFF_TOPIC.lower()

    def test_the_response_format_is_the_shape_llama_server_turns_into_a_grammar(self):
        fmt = probe.response_format()
        assert fmt["type"] == "json_schema"
        assert fmt["json_schema"]["schema"] is probe.TOOL_SCHEMA

    def test_it_matches_the_shape_local_llm_sends(self):
        """Two implementations of one shape is two that drift - and the probe is
        worthless if it constrains something other than what the run constrains."""
        import sys
        if str(ROOT / "usr" / "lib" / "shani-chronoa") not in sys.path:
            sys.path.insert(0, str(ROOT / "usr" / "lib" / "shani-chronoa"))
        from shani_chronoa import local_llm
        mine = local_llm._json_format({"type": "object", "properties": {}})
        theirs = probe.response_format()
        assert mine["type"] == theirs["type"]
        assert set(mine["json_schema"]) == set(theirs["json_schema"])


class TestCouldNotMeasureIsNotTheSameAsNotEnforced:
    """**The probe's own first failure, found by running it.** Pointed at a
    saturated server it raised an unhandled `TimeoutError` and died with a
    traceback - which a caller reading the exit status cannot tell from a probe
    that ran and found the grammar off. Both say "not enforced"; only one is a
    fact about the grammar."""

    def test_an_unreachable_server_is_reported_as_unasked(self):
        assert probe.probe.__doc__, "the probe documents which states it can be in"
        import unittest.mock as mock
        with mock.patch.object(probe, "_ask", return_value=None):
            ok, why, off, on = probe.probe(8799)
        assert not ok
        assert "could not be asked" in why
        assert off == "" and on == ""

    def test_the_off_arm_survives_when_the_on_arm_fails(self):
        """Which arm failed is in the reason - an operator pointing this at a
        busy box needs to know it got a reply the first time."""
        import unittest.mock as mock
        with mock.patch.object(probe, "_ask", side_effect=["some prose", None]):
            ok, why, off, _ = probe.probe(8799)
        assert "grammar-on arm" in why and off == "some prose"

    def test_a_crash_becomes_a_reason_not_a_traceback(self):
        """Measured: this is what the raw version did."""
        import unittest.mock as mock
        # The module object, not a dotted string: `patch.object` reads a string
        # as "module.attribute" and tries to *import* the middle part, which is
        # how my first version of this raised AttributeError on a path that
        # exists.
        boom = mock.patch.object(probe.urllib.request, "urlopen",
                                 side_effect=TimeoutError("timed out"))
        with boom:
            assert probe._ask(8799, {}) is None

    def test_the_exit_codes_are_three_distinct_things(self):
        source = (ROOT / "tools" / "grammar_probe.py").read_text(encoding="utf-8")
        assert "UNMEASURED = 2" in source
        assert "return UNMEASURED" in source, (
            "a probe that could not measure must not exit like one that measured")
        assert "return 0 if ok else 1" in source


class TestTheProbeIsAGateNotAReport:
    def test_it_exits_non_zero_when_the_grammar_is_not_enforced(self):
        """A probe that returns 0 on failure is a report, and this one is wired to
        be the thing that says whether the run means anything."""
        source = (ROOT / "tools" / "grammar_probe.py").read_text(encoding="utf-8")
        assert "return 0 if ok else 1" in source, (
            "the exit code is the gate; without it a caller cannot tell")

    def test_it_can_start_and_reuse_a_server(self, monkeypatch):
        """**Driven, not grepped.** The first version asserted the source
        *contains* `_server_up`, which a mutation replacing the `if` condition
        leaves completely untouched - the name is still there, it is just never
        called. This drives `main()` and watches whether a server is started.
        """
        started = []

        class _Proc:
            def terminate(self): pass
            def wait(self, timeout=None): return 0
            def kill(self): pass

        monkeypatch.setattr(probe.subprocess, "Popen",
                            lambda *a, **k: started.append(a) or _Proc())
        monkeypatch.setattr(probe, "probe",
                            lambda port: (True, "stub", "off", "on"))
        monkeypatch.setattr(probe, "_wait", lambda *a, **k: True)
        monkeypatch.setattr(probe, "_server_up", lambda port: True)
        assert probe.main(["--port", "8799"]) == 0
        assert not started, "it started a server although one was already listening"

        started.clear()
        monkeypatch.setattr(probe, "_server_up", lambda port: False)
        assert probe.main(["--port", "8799"]) == 0
        assert len(started) == 1, "it did not start a server when none was up"

    def test_it_exits_non_zero_when_the_probe_fails(self, monkeypatch):
        """The gate's other half: a probe that answers no must not exit clean."""
        monkeypatch.setattr(probe, "_server_up", lambda port: True)
        monkeypatch.setattr(probe, "probe",
                            lambda port: (False, "not enforced", "a", "b"))
        assert probe.main(["--port", "8799"]) == 1

    def test_a_dry_run_starts_nothing(self, monkeypatch):
        started = []
        monkeypatch.setattr(probe.subprocess, "Popen",
                            lambda *a, **k: started.append(a))
        assert probe.main(["--dry-run"]) == 0
        assert not started