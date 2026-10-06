"""The Diagnostics panel, asked whether speech input is broken.

A third instance of one shape, and the one with the loudest alarm - see
the class docstring below.
"""



class TestSpeechInAsksTheEngineThatIsActuallyTranscribing:
    """**A third instance of one shape**, and the one with the loudest alarm.

    The panel's row is titled "Speech in (whisper.cpp)" and `_speech_in()`
    constructed a `WhisperSTT` directly, so it could not see a
    `cloud_voice.CloudSTT` even when that was the engine transcribing every
    utterance. On a machine with cloud recognition on, a key configured and no
    local model it reported

        neither the whisper.cpp binary nor a model file is on this machine, so
        speech input is off whatever the settings say

    - a **warning**, on a machine transcribing perfectly well. The row title was
    honest about what it probed; the alarm was still about nothing.

    The row is now "what can transcribe here", and the local half is reported
    alongside rather than suppressed: a person who wants whisper.cpp locally is
    still told exactly what is missing.
    """

    def test_the_probe_still_reports_the_local_truth_when_cloud_is_off(self, monkeypatch):
        from shani_chronoa import cloud_voice
        from shani_chronoa.gui.surfaces import diagnostics as surface
        monkeypatch.setattr(cloud_voice, "_read_switch",
                            lambda name, config=None: False)
        status, detail = surface._speech_in()
        assert status != surface.STATUS_WORKING, detail
        assert "hissper" in detail or "model" in detail, detail
        assert "cloud" not in detail.lower(), (
            f"cloud is switched off, so naming it is noise: {detail!r}")

    def test_a_working_cloud_engine_makes_it_working_not_broken(self, monkeypatch):
        from shani_chronoa import cloud_voice
        from shani_chronoa.gui.surfaces import diagnostics as surface
        monkeypatch.setattr(cloud_voice, "_read_switch",
                            lambda name, config=None: True)
        monkeypatch.setattr(cloud_voice, "_provider_key",
                            lambda pid, api_keys=None, config=None: "sk-test")
        monkeypatch.setattr(cloud_voice.egress, "privacy_mode_enabled", lambda: False)
        status, detail = surface._speech_in()
        assert status == surface.STATUS_WORKING, (
            f"cloud recognition is on and the panel still calls speech input "
            f"broken: {detail!r}")
        assert "cloud" in detail.lower(), detail
        # And the local half is still there, because someone may want it.
        assert "Locally:" in detail, (
            f"the local truth was dropped along with the alarm: {detail!r}")

    def test_the_cloud_half_can_never_invent_a_working_engine(self, monkeypatch):
        """A diagnostic that raises must under-report, never over-report."""
        from shani_chronoa import cloud_voice
        from shani_chronoa.gui.surfaces import diagnostics as surface

        def explode(*_a, **_k):
            raise RuntimeError("the probe is broken")

        monkeypatch.setattr(cloud_voice, "CloudSTT", explode)
        assert surface._cloud_speech_in() == "", (
            "a raising cloud probe returned a working claim")
