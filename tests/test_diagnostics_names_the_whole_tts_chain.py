"""The Diagnostics speech-out row, asked whether it can go stale.

Third place today where a panel describes a cascade it does not own: a count
hardcoded in a sentence, which was wrong on the same day a fifth link was added
to the chain it was counting.
"""


def _no_local_voices(monkeypatch):
    """Take away every local engine, so only the cloud link is left to win."""
    from shani_chronoa import tts
    monkeypatch.setattr(tts.shutil, "which", lambda _n: None)
    # `_engine()` tries Kokoro first and it is a hard block on `kokoro_reason`
    # being empty, so the switch has to be off for any later link to be reached.
    monkeypatch.setattr(tts.PiperTTS, "_kokoro_enabled", lambda self: False)
    monkeypatch.setattr(tts.PiperTTS, "piper_path", "/nonexistent/piper", raising=False)
    monkeypatch.setattr(tts.PiperTTS, "voice_path", "/nonexistent/voice.onnx", raising=False)


def _cloud_on(monkeypatch):
    from shani_chronoa import cloud_voice
    monkeypatch.setattr(cloud_voice, "_read_switch", lambda name, config=None: True)
    monkeypatch.setattr(cloud_voice, "_provider_key",
                        lambda pid, api_keys=None, config=None: "sk-test")
    monkeypatch.setattr(cloud_voice.egress, "privacy_mode_enabled", lambda: False)


class TestTheCountIsReadNotTyped:
    """`resolved from PiperTTS.engine()'s own four-way chain`."""

    def test_the_row_reads_the_chain_rather_than_naming_a_number(self):
        from shani_chronoa.gui.surfaces import diagnostics as surface
        from shani_chronoa.gui.surfaces.voice import CHAIN

        chain = surface._tts_chain()
        assert chain, "the cascade could not be read at all"
        assert chain == tuple(CHAIN), (
            f"the row reads {chain!r} while the cascade is {CHAIN!r} - the two "
            "have drifted apart, which is the bug this exists to prevent")

    def test_the_sentence_counts_the_links_that_exist(self, monkeypatch):
        from shani_chronoa.gui.surfaces import diagnostics as surface
        said = surface._speech_out()[1]
        assert "four-way" not in said, (
            f"the row still calls the cascade four-way: {said!r}")
        # And the number it does say is the real one.
        expected = f"{len(surface._tts_chain())}-way"
        assert expected in said, (f"expected {expected!r} in {said!r}")

    def test_a_new_link_would_change_the_row_without_touching_it(self, monkeypatch):
        """The real point: the number is derived, so it cannot be forgotten."""
        from shani_chronoa.gui.surfaces import diagnostics as surface
        import shani_chronoa.gui.surfaces.voice as voice_surface

        before = f"{len(surface._tts_chain())}-way"
        monkeypatch.setattr(voice_surface, "CHAIN", voice_surface.CHAIN + ("newlink",))
        after = surface._tts_chain()
        assert len(after) == len(voice_surface.CHAIN)
        assert f"{len(after)}-way" != before, (
            "adding a link did not change the count, so it is not being read "
            "from the chain")


class TestTheCloudWinnerIsDisclosed:
    def test_speaking_in_the_cloud_says_the_text_leaves_the_machine(self, monkeypatch):
        from shani_chronoa.gui.surfaces import diagnostics as surface
        _no_local_voices(monkeypatch)
        _cloud_on(monkeypatch)
        status, detail = surface._speech_out()
        assert status == surface.STATUS_WORKING, detail
        assert "cloud" in detail.lower(), detail
        assert "sent" in detail.lower() or "leaves" in detail.lower(), (
            f"the row says a cloud provider would speak but never says the reply "
            f"text is uploaded: {detail!r} - and this is the panel somebody "
            "checks to find out what is leaving the machine")

    def test_the_nothing_works_message_names_every_link_that_was_tried(self, monkeypatch):
        from shani_chronoa.gui.surfaces import diagnostics as surface
        _no_local_voices(monkeypatch)
        _cloud_on(monkeypatch)
        from shani_chronoa import cloud_voice
        monkeypatch.setattr(cloud_voice.CloudTTS, "is_available", lambda self: False)
        status, detail = surface._speech_out()
        assert status != surface.STATUS_WORKING, detail
        for named in ("Kokoro", "Piper", "RHVoice", "espeak-ng", "cloud"):
            assert named in detail, (
                f"{named!r} is missing from the list of what was tried: {detail!r} - "
                "so on a machine relying on the cloud, the reason it is not "
                "working is invisible")
