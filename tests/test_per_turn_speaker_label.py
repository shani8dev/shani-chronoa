"""Optional per-turn speaker labels: opt-in, offline, silent in every fallback.

A spoken turn may carry a neutral diarization label and a fact written from
that turn may carry the same label. Neither is an identity claim, and neither
changes what the consent gates allow. Four properties are pinned here: the
label is computed while the audio still exists and handed to the turn; a fact
captured from that turn carries it into the stored percept's metadata; opting
out or lacking enrollment is a silent no-op; no audio means no label at all.
"""

from __future__ import annotations

import asyncio
import os
import sys
import types

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "usr", "lib", "shani-chronoa"))

from shani_chronoa import speaker_turn, speakers  # noqa: E402
from shani_chronoa.senses import memory  # noqa: E402
from shani_chronoa.skills import speaker_id  # noqa: E402
from shani_chronoa.app.voice import VoiceMixin  # noqa: E402


class _Window:
    def answer_pending_question(self, text):
        return False

    def set_state(self, state):
        pass

    def set_orb_state(self, state):
        pass

    def set_status(self, text):
        pass

    def take_attachments(self):
        return [], ""

    def add_user_turn(self, text):
        pass


class _Voice(VoiceMixin):
    """A voice mixin with just the state `_transcribe`/`_on_transcribed` read."""

    def __init__(self, text="hello"):
        self.stt = types.SimpleNamespace(transcribe=lambda path: text)
        self.config = types.SimpleNamespace(get_bool=lambda key, default=False: True)
        self.window = _Window()
        self.recorder = types.SimpleNamespace(auto_stop_reason="")
        self.submitted = []
        self._speech_gate = None
        self._turn_future = None

    def _submit(self, text, channel="", speaker=""):
        self.submitted.append((text, channel, speaker))


class TestTheHelperIsOptInAndSilent:
    def test_opt_out_never_runs_the_diarizer(self, monkeypatch):
        called, asked = [], []

        def diarize(*args, **kwargs):
            called.append(args)
            return []

        def consent(config=None):
            asked.append(config)
            return False, "voice is off"

        monkeypatch.setattr(speaker_id, "_consent", consent)
        monkeypatch.setattr(speaker_id, "_read_registry", lambda: {"Joseph": {}})
        monkeypatch.setattr(speakers, "installed", lambda: True)
        monkeypatch.setattr(speakers, "diarize", diarize)
        assert speaker_turn.label_for("turn.wav") == ""
        assert len(asked) == 1  # the gate was really reached, not just broken
        assert called == []

    def test_no_enrollment_is_the_same_silent_no_op(self, monkeypatch):
        called, read = [], []

        def registry():
            read.append(True)
            return {}

        monkeypatch.setattr(speaker_id, "_consent", lambda config=None: (True, ""))
        monkeypatch.setattr(speaker_id, "_read_registry", registry)
        monkeypatch.setattr(speakers, "installed", lambda: True)
        monkeypatch.setattr(speakers, "diarize", lambda *a, **k: called.append(a) or [])
        assert speaker_turn.label_for("turn.wav") == ""
        assert read == [True]
        assert called == []

    def test_the_label_is_a_cluster_not_an_enrolled_name(self, monkeypatch):
        monkeypatch.setattr(speaker_id, "_consent", lambda config=None: (True, ""))
        monkeypatch.setattr(speaker_id, "_read_registry", lambda: {"Jo": {}})
        monkeypatch.setattr(speakers, "installed", lambda: True)
        monkeypatch.setattr(
            speakers,
            "diarize",
            lambda wav, count=None: [(0.0, 1.0, "Speaker 2"), (1.0, 4.0, "Speaker 1")],
        )
        assert speaker_turn.label_for("turn.wav") == "Speaker 1"

    def test_a_failed_diarizer_says_nothing(self, monkeypatch):
        monkeypatch.setattr(speaker_id, "_consent", lambda config=None: (True, ""))
        monkeypatch.setattr(speaker_id, "_read_registry", lambda: {"Jo": {}})
        monkeypatch.setattr(speakers, "installed", lambda: True)
        monkeypatch.setattr(
            speakers,
            "diarize",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        assert speaker_turn.label_for("turn.wav") == ""


class TestTheTurnCarriesTheLabel:
    def test_label_is_computed_before_the_audio_is_removed(self, tmp_path, monkeypatch):
        path = tmp_path / "turn.wav"
        path.write_bytes(b"not a real wav - the label helper is stubbed")
        monkeypatch.setattr(
            speaker_turn, "label_for", lambda wav, config=None: "Speaker 2"
        )
        voice = _Voice()
        assert asyncio.run(voice._transcribe(str(path))) == "hello"
        assert not path.exists()  # the normal cleanup still happened
        assert voice._pending_speaker == "Speaker 2"

    def test_the_label_is_handed_to_the_turn_once(self, monkeypatch):
        monkeypatch.setattr(
            speaker_turn, "label_for", lambda wav, config=None: "Speaker 3"
        )
        voice = _Voice()
        voice._pending_speaker = "Speaker 3"
        assert VoiceMixin._on_transcribed(voice, "hello") is not None
        assert voice.submitted == [("hello", "", "Speaker 3")]
        assert voice._pending_speaker == ""

    def test_no_audio_means_no_label_and_no_turn(self):
        voice = _Voice()
        voice._pending_speaker = "Speaker 1"
        VoiceMixin._on_recording_done_main(voice, None)
        assert voice.submitted == []
        assert voice._pending_speaker == ""


class TestTheFactCarriesTheLabel:
    def test_attribution_reaches_the_stored_percept(self, monkeypatch):
        captured = []

        def fake_store_fact(fact, store=None):
            captured.append(fact)
            return fact

        class _Store:
            def durable(self):
                return []

        monkeypatch.setattr(memory, "store_fact", fake_store_fact)
        monkeypatch.setattr(memory, "_last_auto_capture", 0.0)
        text = "User: my name is Joseph\nAssistant: Nice to meet you, Joseph."
        fact, reason = memory.remember_from_turn(
            text, store=_Store(), force=True, channel="phone", speaker="Speaker 1"
        )
        assert reason == ""
        assert fact is not None
        assert captured[0].speaker == "Speaker 1"
        assert captured[0].channel == "phone"
        # The label rides in metadata, the same place channel and private live.
        assert memory._memory_percept(captured[0]).metadata["speaker"] == "Speaker 1"

    def test_an_unlabelled_turn_writes_no_speaker_metadata(self, monkeypatch):
        captured = []
        monkeypatch.setattr(
            memory, "store_fact", lambda fact, store=None: captured.append(fact) or fact
        )
        monkeypatch.setattr(memory, "_last_auto_capture", 0.0)

        class _Store:
            def durable(self):
                return []

        memory.remember_from_turn(
            "User: my name is Joseph\nAssistant: Hi Joseph.", store=_Store(), force=True
        )
        assert captured[0].speaker == ""
        assert "speaker" not in memory._memory_percept(captured[0]).metadata
