"""Which providers can actually do speech, reachable from Settings.

`cloud_voice.probe_capabilities()` was written and measured, then left with **zero
callers** — so the table lived inside a maintenance function that no person could
reach, while the two switches above it said only "needs an API key" and stopped.

It is a **button and not a row** because it makes real requests to up to five
providers with a 30 s timeout each, and a probe that can take two minutes cannot
run while the window is being drawn.
"""

from shani_chronoa.settings_window import privacy


#: The shape `probe_capabilities()` actually returns, from its own docstring:
#: `{pid: {"base_url", "stt": {...}, "tts": {...}}}` with `verdict` inside each.
MEASURED = {
    "openai": {"stt": {"verdict": "yes"}, "tts": {"verdict": "yes"}},
    "groq": {"stt": {"verdict": "yes"}, "tts": {"verdict": "yes"}},
    "openrouter": {"stt": {"verdict": "yes"}, "tts": {"verdict": "yes"}},
    "kilo": {"stt": {"verdict": "yes"}, "tts": {"verdict": "no"}},
    "llm7": {"stt": {"verdict": "yes"}, "tts": {"verdict": "no"}},
    "blockrun": {"stt": {"verdict": "no"}, "tts": {"verdict": "needs-paid"}},
    "anthropic": {"stt": {"verdict": "no"}, "tts": {"verdict": "no"}},
}


class TestTheFiveVerdictsStayApart:
    """Merging them is what made the table wrong three times before it was right."""

    def test_it_reads_the_real_shape(self):
        """**And not keys of its own.** My first renderer invented `stt_yes`,
        `needs_key` and friends, and printed "none" for everything while looking
        like a measurement. The real verdicts are hyphenated: `needs-key`,
        `needs-paid`."""
        said = privacy._capability_sentence(MEASURED)
        for provider in ("openai", "groq", "openrouter"):
            assert provider in said, said

    def test_speech_in_and_out_are_different_answers(self):
        said = privacy._capability_sentence(MEASURED)
        # Kilo and llm7 transcribe but do not synthesize - measured, not assumed.
        speech_in = said.split("Speech in:")[1].split(".")[0]
        speech_out = said.split("Speech out:")[1].split(".")[0]
        assert "kilo" in speech_in and "kilo" not in speech_out, said
        assert "llm7" in speech_in and "llm7" not in speech_out, said
        assert "openai" in speech_out, said

    def test_a_paid_route_is_named_as_paid_not_as_working(self):
        """0.002 USDC per request behind a switch that does not mention money."""
        said = privacy._capability_sentence(MEASURED)
        paid = said.split("Needs payment:")[1].split(".")[0]
        assert "blockrun" in paid, said
        assert "blockrun" not in said.split("Speech out:")[1].split(".")[0], (
            "a route that answers 402 for money is not one that works")

    def test_the_hyphenated_verdicts_are_recognised(self):
        said = privacy._capability_sentence({
            "a": {"stt": {"verdict": "needs-key"}, "tts": {"verdict": "needs-paid"}},
            "b": {"stt": {"verdict": "unreachable"}, "tts": {"verdict": "unreachable"}},
        })
        assert "Needs a key: a" in said, said
        assert "Needs payment: a" in said, said
        assert "Could not be asked: b" in said, (
            f"an unreachable provider must not be filed as having no route: {said}")

    def test_nothing_measured_says_none_rather_than_guessing(self):
        said = privacy._capability_sentence({})
        assert said.count("none") == 6, said

    def test_a_missing_side_does_not_crash(self):
        said = privacy._capability_sentence({"a": {"stt": {"verdict": "yes"}}})
        assert "a" in said, said


class TestItIsAButtonNotARow:
    def test_the_probe_runs_off_the_main_loop(self):
        """Five providers at up to 30 s each cannot run on the drawing thread."""
        import inspect

        source = inspect.getsource(privacy.PrivacyPage._probe_cloud_speech)
        assert "threading.Thread" in source, source
        assert "daemon=True" in source, source

    def test_the_button_starts_the_probe_and_re_enables_itself(self):
        """A button left disabled after a probe is a dead end."""
        import inspect

        source = inspect.getsource(privacy.PrivacyPage._probe_cloud_speech)
        assert "set_sensitive(False)" in source, source
        assert "set_sensitive(True)" in source, (
            "the button is never re-enabled, so one probe would end the feature "
            "for the rest of the session")

    def test_a_failing_probe_reports_and_still_re_enables(self):
        import inspect

        source = inspect.getsource(privacy._probe_worker)
        assert "set_sensitive(True)" in source, source
