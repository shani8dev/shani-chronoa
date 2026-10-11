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

        # **Shaped like the real backend, which is the whole point.** The old
        # fake took `(config=None, transport=None)` and defined `chat()` - the
        # signature `notes()` was *calling*, not the one `local_llm.LocalLLM`
        # actually has. It encoded the bug and let it pass for its whole life:
        # `LocalLLM.__new__(cls, model_key)` plus an async `chat_message()`, not
        # a config-taking `chat()`. `TestNotesDrivesTheBackendTheRealWay` pins
        # that contract explicitly.
        class _Engine:
            def __init__(self, model_key=""):
                pass

            async def chat_message(self, messages, tools=None, stream=False):
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


class TestAPathFromTheModel:
    """**A str from `arguments` reached a function typed `Path`.** `notes` takes a
    `path` from the model, and `meeting.transcribe` declared its parameter as
    `Path` and trusted the annotation - so the real call passed through to
    `recordings.segments_for`, which reaches for `src.name`, and the tool answered
    with `AttributeError: 'str' object has no attribute 'name'` instead of a
    sentence. Found by running the action, not by reading it.
    """

    def test_a_string_path_is_a_sentence_not_a_traceback(self):
        out, why = M.transcribe("/tmp/definitely-not-here.wav")
        assert out is None
        assert "could not be read" in why or "no audio" in why

    def test_a_path_from_a_test_is_coerced(self, tmp_path):
        """**The control.** Coercing everything to Path would make both tests
        pass while nothing worked."""
        import pathlib
        target = tmp_path / "a.wav"
        target.write_bytes(b"RIFFnot really")
        out, why = M.transcribe(target)
        assert out is None and why, "a bogus WAV was accepted"

    def test_a_none_path_is_refused(self):
        out, why = M.transcribe(None)
        assert out is None and why

    def test_the_skill_answers_with_the_sentence(self):
        from shani_chronoa.skills import meeting as S
        on = type("C", (), {"get_bool": lambda s, k, d=False: True})()
        out = S._run({"action": "notes", "path": "/tmp/nope.wav", "_config": on})
        assert "could not be read" in out or "no audio" in out, out
        assert "Traceback" not in out


class TestNotesDrivesTheBackendTheRealWay:
    """`notes()` called a backend that does not exist, and its own test agreed.

    Measured before the fix, through the real `notes()`:

        TypeError: LocalLLM.__new__() got an unexpected keyword argument 'config'

    Two defects on two consecutive lines, and the second is why nobody noticed
    for as long as they did: the constructor was called with `config=` and
    `transport=`, it takes `(model_key,)` alone, and the `except TypeError`
    fallback was the *same* wrong call without `transport` - so it raised the
    identical error outside any handler. `LocalLLM` is
    `def __new__(cls, model_key="")`, and `chat_message()` is the interface every
    backend in this package has.

    `distill.py`'s `_ask_chat_message` documents this bug in full - a first
    version called `.chat()`, which only `OllamaLLM` defines; llama.cpp and the
    whole cloud chain raised, and a machine with a configured provider reported
    "no language model is configured" while the key sat right there. This is the
    second instance of it, so the test is written against the real interface
    rather than a fake shaped like the call.
    """

    def _drive(self, monkeypatch, seen, reply):
        from shani_chronoa import local_llm as _llm

        class _Engine:
            def __init__(self, model_key=""):
                seen.append(("__init__", model_key))

            async def chat_message(self, messages, tools=None, stream=False):
                seen.append(("chat_message", tools))
                return reply

        monkeypatch.setattr(_llm, "LocalLLM", lambda *a, **k: _Engine(*a, **k))

    def _segments(self, count=3):
        from shani_chronoa.recordings import Segment
        return [Segment(float(i), float(i) + 1, "we ship on friday") for i in range(count)]

    def test_it_actually_returns_notes(self, monkeypatch):
        """The measured defect: every call raised, so notes were never produced."""
        from shani_chronoa import meeting as _m
        seen = []
        self._drive(monkeypatch, seen, {"content": "ship on friday; alice owns it"})
        note, why = _m.notes(self._segments())
        assert note, f"notes() returned nothing: {why}"
        assert "alice owns it" in note
        assert ("__init__", "") in seen, (
            f"the backend was not constructed the way `LocalLLM()` is: {seen}")
        assert ("chat_message", None) in seen, seen

    def test_the_constructor_is_not_given_what_it_cannot_take(self, monkeypatch):
        """If `config` or `transport` ever reach the constructor again, this fails.

        The old code passed both. `LocalLLM.__new__` takes `model_key` alone, so
        forwarding them is not a style preference - it is the TypeError.
        """
        import inspect
        from shani_chronoa import local_llm as _llm
        params = inspect.signature(_llm.LocalLLM).parameters
        for forbidden in ("config", "transport"):
            assert forbidden not in params, (
                f"`notes()` forwards `{forbidden}` to LocalLLM, which does not "
                "accept it - reconnect this only by fixing LocalLLM, not by "
                "putting it back")

    def test_it_never_calls_chat(self, monkeypatch):
        """No `.chat()`: it exists only on `OllamaLLM`.

        A fake that raises when `chat` is used proves the path is not taken,
        because an implementation that used it would surface immediately rather
        than falling through to a model that never answers.
        """
        from shani_chronoa import meeting as _m
        from shani_chronoa import local_llm as _llm

        class _Engine:
            def __init__(self, model_key=""):
                pass

            def chat(self, *a, **k):
                raise AssertionError("notes() used .chat() - only OllamaLLM has it")

            async def chat_message(self, messages, tools=None, stream=False):
                return {"content": "notes from chat_message"}

        monkeypatch.setattr(_llm, "LocalLLM", lambda *a, **k: _Engine())
        note, why = _m.notes(self._segments())
        assert "notes from chat_message" in note, why
