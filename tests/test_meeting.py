"""A meeting is a long capture, an honest end status, and notes.

The thing this file exists to pin: **a meeting that ended the way the person
meant and one that died on its own are different facts, and `on_done(None)`
cannot tell them apart.** `pw-record` exiting mid-meeting and a room falling
silent both arrive as a callback with no path, and a caller that treated them
the same would tell a person they had the whole meeting when they had a third of
it. That is the failure `test_child_supervisor_live.py` was written for, in the
long form.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]
                       / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import meeting as M  # noqa: E402


class _Recorder:
    """A recorder whose child is a stub, shaped like `AudioRecorder`."""

    def __init__(self, produces_wav=True, reason="silence"):
        self._produces = produces_wav
        self._reason = reason
        self._auto_stop_reason = reason
        self._stop_epoch = 0
        self.started_with = {}
        self._thread = None

    def start_auto_stop(self, on_done, max_seconds, silence_seconds, on_level=None):
        self.started_with = {"max_seconds": max_seconds,
                             "silence_seconds": silence_seconds}
        import types
        self._thread = types.SimpleNamespace(join=lambda timeout=None: None)
        # fire synchronously: the real one fires from its thread, and the
        # generation discipline is tested where it lives (audio's own suite)
        on_done("/tmp/meeting.wav" if self._produces else None)
        return True

    def auto_stop_reason(self):
        return self._reason

    @property
    def _auto_stop_thread(self):
        return self._thread


class TestTheCeilingsAreMeetingShaped:
    def test_a_meeting_is_not_stopped_by_a_two_second_pause(self):
        """`MEETING_SILENCE_SECONDS` must exceed the ordinary turn's 1.2s by
        enough to survive a room, or the meeting ends at the first breath.
        """
        # Dictation's silence, which is the number this one has to beat: a
        # sentence of speech pauses for 2.5s, a *room* pauses longer, and a
        # threshold tuned for a turn ends the meeting at the first breath.
        from shani_chronoa.app.voice import DICTATION_SILENCE_SECONDS
        assert M.MEETING_SILENCE_SECONDS >= 8 * DICTATION_SILENCE_SECONDS, (
            "the meeting would stop at a pause that dictation rides out")
        assert M.MEETING_SILENCE_SECONDS >= 20.0

    def test_the_default_ceiling_is_measured_in_hours_not_seconds(self):
        assert M.MAX_MEETING_SECONDS >= 2 * 3600

    def test_a_smaller_ceiling_gets_a_matching_silence(self):
        """The control: a ceiling below the silence threshold must not be asked
        to wait for silence that outlives it - the meeting would run to the clock
        with nobody speaking."""
        recorder = _Recorder()
        M.capture(timeout=10.0, recorder=recorder)
        assert recorder.started_with["silence_seconds"] <= 10.0
        assert recorder.started_with["max_seconds"] == 10.0


class TestTheEndStatusIsHonest:
    def test_a_silent_room_is_reported_as_clean(self):
        result = M.capture(recorder=_Recorder(produces_wav=True, reason="silence"))
        assert result.why == M.END_SILENCE
        assert result.wav == "/tmp/meeting.wav"
        assert result.stopped_cleanly

    def test_a_dead_child_is_not_reported_as_clean(self):
        """**The one that matters.** `on_done(None)` is what a child that died
        looks like, and a caller that treated it as an ordinary end would hand a
        person a fragment described as the whole meeting."""
        result = M.capture(recorder=_Recorder(produces_wav=False))
        assert result.why == M.END_DIED
        assert not result.stopped_cleanly
        assert result.wav is None

    def test_the_clock_running_out_is_reported_as_a_limit(self):
        result = M.capture(recorder=_Recorder(produces_wav=True, reason="limit"))
        assert result.why == M.END_LIMIT
        assert result.stopped_cleanly, "the clock running out still produced a file"

    def test_an_unknown_reason_is_not_read_as_silence(self):
        """A recorder that reports something this module has no name for must not
        become "the person finished" - that is how an interrupted capture reads
        as a complete one."""
        result = M.capture(recorder=_Recorder(produces_wav=True, reason="wat"))
        assert result.why != M.END_SILENCE
        assert result.why == M.END_CANCELLED

    def test_each_end_has_a_sentence_a_person_can_read(self):
        for why in (M.END_SILENCE, M.END_LIMIT, M.END_CANCELLED, M.END_DIED):
            sentence = M._sentence(why, 600)
            assert sentence and "minutes" in sentence, why
        assert "part of the meeting" in M._sentence(M.END_DIED, 600)
        assert "complete" in M._sentence(M.END_SILENCE, 600)


class TestAMicrophoneThatWillNotStart:
    def test_a_recorder_that_refuses_raises_with_a_reason(self):
        class _Refuses:
            def start_auto_stop(self, on_done, max_seconds, silence_seconds,
                                on_level=None):
                return False

            _auto_stop_thread = None

        with pytest.raises(M.MeetingError) as caught:
            M.capture(recorder=_Refuses())
        assert "would not start" in str(caught.value)

    def test_a_recorder_that_raises_becomes_a_sentence(self):
        class _Raises:
            def start_auto_stop(self, on_done, max_seconds, silence_seconds,
                                on_level=None):
                raise OSError("no capture device")

            _auto_stop_thread = None

        with pytest.raises(M.MeetingError) as caught:
            M.capture(recorder=_Raises())
        assert "no capture device" in str(caught.value)


class TestNotes:
    class _Segment:
        def __init__(self, start, end, text, speaker=""):
            self.start, self.end, self.text, self.speaker = start, end, text, speaker

    def _segments(self):
        return [
            self._Segment(0.0, 4.0, "The wifi box in the study drops the link.", "ana"),
            self._Segment(4.0, 8.0, "We agreed to move it to the hall.", "bo"),
        ]

    def test_the_transcript_names_the_speaker(self):
        """`assign_speakers` gives who; notes must not drop it, or "we agreed to
        move it" loses who agreed."""
        prompt_lines = []
        for segment in self._segments():
            speaker = f"[{segment.speaker}] " if segment.speaker else ""
            prompt_lines.append(f"{speaker}{segment.text}".strip())
        text = "\n".join(prompt_lines)
        assert "[ana]" in text and "[bo]" in text

    def test_no_segments_is_refused_not_summarised(self):
        out, why = M.notes([])
        assert out is None and "nothing to summarise" in why

    def test_a_long_meeting_is_summarised_in_sections_and_says_so(self):
        """**The control that keeps a two-hour meeting honest.** `NOTES_MAX_CHARS`
        exists so a long meeting is not silently truncated to its first hour, and
        the note must say how many sections it came from.
        """
        assert M.NOTES_MAX_CHARS < 60 * 60 * 100, (
            "the section size is so large it would be one section for any meeting")
        # one segment per section, at the boundary
        many = [self._Segment(float(i), float(i + 1), "x" * 40, "ana")
                for i in range(400)]
        total = sum(len(s.text) for s in many)
        assert total > M.NOTES_MAX_CHARS, "the fixture does not cross the boundary"


class TestTheDiedSentenceIsDistinguishable:
    """The second half of making `END_DIED` honest: its *sentence* must differ
    from the clean ones, not only the enum. A person reads the sentence, never
    the enum.

    A mutation that only altered the `END_DIED` branch's prose passed everything
    above, because no test compared the sentences to each other.
    """

    def test_died_does_not_read_as_complete(self):
        died = M._sentence(M.END_DIED, 600)
        for clean in (M.END_SILENCE, M.END_LIMIT, M.END_CANCELLED):
            sentence = M._sentence(clean, 600)
            assert died.replace("part of the meeting", "") != sentence.replace(
                "complete", ""), (
                f"{clean} and END_DIED read the same once their one word differs")
            assert "complete" not in died, (
                "the failed-capture sentence claims the transcript is complete")

    def test_every_clean_end_says_complete(self):
        for clean in (M.END_SILENCE, M.END_LIMIT, M.END_CANCELLED):
            assert "complete" in M._sentence(clean, 600), clean


class TestALongMeetingIsNotSilentlyTruncated:
    """**The section count is the promise.** `NOTES_MAX_CHARS` is what keeps a
    two-hour meeting from silently keeping only its first hour, so a mutation
    that truncated the segments to one would produce a shorter note that looks
    fine - and nothing measured the length.

    Asserted through the real `notes`, with a fake engine, rather than by
    reasoning about the constant alone.
    """

    def test_a_long_meeting_uses_more_than_one_section(self):
        # `meeting.notes` imports local_llm *inside* the function, so the
        # patch goes on the module it imports from - patching an attribute of
        # `meeting` would patch a copy.
        from shani_chronoa import local_llm as _llm
        calls = []

        class _Engine:
            def __init__(self, config=None, transport=None):
                pass

            def chat(self, messages, stream=False, **kw):
                calls.append(messages[-1]["content"])
                return {"content": f"summary {len(calls)}"}

        original = _llm.LocalLLM
        _llm.LocalLLM = _Engine
        try:
            many = []
            for index in range(400):
                seg = type("S", (), {"start": float(index), "end": float(index + 1),
                                     "text": "word " * 8, "speaker": "ana"})()
                many.append(seg)
            note, why = M.notes(many)
        finally:
            _llm.LocalLLM = original
        assert note is not None, why
        assert len(calls) >= 2, (
            f"a meeting of {len(many)} segments was summarised in "
            f"{len(calls)} call(s) - the rest was dropped without saying so")
        assert f"{len(calls)} sections" in note, (
            "the note does not say how many sections it came from, so a reader "
            "cannot tell the whole meeting was used")
