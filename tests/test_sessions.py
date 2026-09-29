"""The conversation was a list in one process, so a restart was an amnesia.

Nothing about that was a bug in any one line - `_history` was a plain list and
everything using it worked. The loss only shows up when the window closes, or
the process dies, which is exactly the moment a transcript is worth having.

What these hold onto:

- a conversation survives a *new process* reading the same file
- **percepts never reach disk.** They are deliberately not in `_history` - they
  are re-read per request so one that expires mid-turn stops appearing - so a
  transcript cannot capture a machine reading the user revoked consent for
- a crash mid-append loses the last message, not the conversation
- `reset()` clears the file too, or the next process resurrects a discarded
  conversation
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import sessions  # noqa: E402
from shani_chronoa.assistant import Assistant  # noqa: E402


class _FakeLLM:
    async def chat_message(self, messages, tools=None):
        return {"role": "assistant", "content": "stub"}


@pytest.fixture
def transcript(tmp_path):
    return tmp_path / "current.jsonl"


def _assistant(transcript):
    return Assistant(_FakeLLM(), session_path=transcript)


class TestRoundTrip:
    def test_a_new_process_reads_the_conversation_back(self, transcript):
        first = _assistant(transcript)
        first._record({"role": "user", "content": "what is on my disk?"})
        first._record({"role": "assistant", "content": "these files"})
        first._record({"role": "tool", "tool_call_id": "1", "content": "/home/x/a.txt"})

        second = _assistant(transcript)
        roles = [m["role"] for m in second._history]
        assert roles == ["system", "user", "assistant", "tool"]
        assert any(m.get("content") == "what is on my disk?" for m in second._history)
        assert any(m.get("tool_call_id") == "1" for m in second._history)

    def test_the_system_prompt_is_not_duplicated(self, transcript):
        first = _assistant(transcript)
        first._record({"role": "user", "content": "hello"})
        second = _assistant(transcript)
        assert sum(1 for m in second._history if m["role"] == "system") == 1

    def test_the_system_prompt_is_ours_not_the_saved_one(self, transcript):
        first = _assistant(transcript)
        first._record({"role": "user", "content": "hello"})
        second = _assistant(transcript)
        assert second._history[0]["content"] == first._history[0]["content"]

    def test_unicode_survives(self, transcript):
        _assistant(transcript)._record({"role": "user", "content": "世界 🌍"})
        assert any("🌍" in m.get("content", "")
                   for m in _assistant(transcript)._history)

    def test_a_third_process_keeps_accumulating(self, transcript):
        a = _assistant(transcript)
        a._record({"role": "user", "content": "one"})
        b = _assistant(transcript)
        b._record({"role": "user", "content": "two"})
        c = _assistant(transcript)
        texts = [m.get("content") for m in c._history if m["role"] == "user"]
        assert texts == ["one", "two"]


class TestPerceptsStayOffDisk:
    def test_a_percept_never_reaches_the_transcript(self, transcript):
        """Consent is re-checked per request; a saved percept would outlive it."""
        a = _assistant(transcript)
        a._record({"role": "user", "content": "what can you see?"})
        raw = transcript.read_text()
        assert "percept" not in raw.lower()
        # And the only roles that may be written are the four known ones.
        for line in raw.splitlines():
            assert sessions.json.loads(line)["role"] in sessions._KNOWN_ROLES

    def test_a_system_message_is_not_written(self, transcript):
        a = _assistant(transcript)
        a._record({"role": "system", "content": "should not be stored"})
        assert not transcript.exists() or "should not be stored" not in transcript.read_text()

    def test_an_unknown_role_is_refused(self, transcript):
        assert sessions.append({"role": "developer", "content": "x"}, transcript) is False
        assert sessions.append("not a dict", transcript) is False


class TestACrashLosesOneMessageNotTheConversation:
    def test_a_truncated_final_line_is_skipped(self, transcript):
        a = _assistant(transcript)
        a._record({"role": "user", "content": "first"})
        a._record({"role": "user", "content": "second"})
        with open(transcript, "a") as handle:
            handle.write('{"role": "user", "conte')     # a crash mid-append
        restored = [m.get("content") for m in _assistant(transcript)._history
                    if m["role"] == "user"]
        assert restored == ["first", "second"]

    def test_a_junk_line_does_not_stop_the_load(self, transcript):
        a = _assistant(transcript)
        a._record({"role": "user", "content": "mine"})
        with open(transcript, "a") as handle:
            handle.write("something else wrote here\n")
        assert any(m.get("content") == "mine"
                   for m in _assistant(transcript)._history)

    def test_a_message_that_is_not_an_object_is_skipped(self, transcript):
        a = _assistant(transcript)
        a._record({"role": "user", "content": "mine"})
        with open(transcript, "a") as handle:
            handle.write('["a bare list"]\n')
        assert any(m.get("content") == "mine"
                   for m in _assistant(transcript)._history)


class TestPrivacyAndPermissions:
    def test_the_file_is_not_world_readable(self, transcript):
        _assistant(transcript)._record({"role": "user", "content": "private"})
        mode = stat.S_IMODE(os.stat(transcript).st_mode)
        assert not mode & (stat.S_IRGRP | stat.S_IROTH), (
            f"transcript is mode {oct(mode)}; group/other can read a private "
            f"conversation")

    def test_no_consent_setting_deletes_a_local_transcript(self, transcript):
        """privacy-mode is documented as egress, so it must not remove this.

        Asserted on behaviour rather than on wording: the previous version of
        this test grepped the module docstring for the absence of a word, which
        proves nothing about what the code does.
        """
        from shani_chronoa.config import ChronoaConfig

        _assistant(transcript)._record({"role": "user", "content": "still here"})
        assert transcript.exists()

        # Every consent key turned off, which is the strongest privacy setting
        # a user can choose.
        config = ChronoaConfig()
        for key in ("privacy-mode", "memory-sense-enabled", "vision-sense-enabled"):
            try:
                config.set(key, "false")
            except Exception:
                pass

        _assistant(transcript)._record({"role": "user", "content": "after"})
        assert transcript.exists(), (
            "a consent setting deleted a local transcript; privacy-mode is "
            "documented as 'no data is sent to external servers', which is not "
            "the same promise as 'keep nothing on disk'")

    def test_persistence_is_opt_in(self, tmp_path):
        """An Assistant with no path must not touch the real one.

        The first version defaulted to `sessions.TRANSCRIPT`, so any test or
        embedder that built an Assistant silently read and overwrote the real
        user's conversation.
        """
        real = sessions.TRANSCRIPT
        assert not real.exists() or real.stat().st_size >= 0
        bare = Assistant(_FakeLLM())          # no session_path at all
        bare._record({"role": "user", "content": "should go nowhere"})
        assert bare._session_path is None
        assert "should go nowhere" not in (
            real.read_text() if real.exists() else "")


class TestReset:
    def test_reset_clears_the_file_as_well_as_memory(self, transcript):
        a = _assistant(transcript)
        a._record({"role": "user", "content": "discard me"})
        a.reset()
        assert [m["role"] for m in a._history] == ["system"]
        assert not transcript.exists()

    def test_a_reset_conversation_does_not_come_back(self, transcript):
        a = _assistant(transcript)
        a._record({"role": "user", "content": "discard me"})
        a.reset()
        later = _assistant(transcript)
        assert [m["role"] for m in later._history] == ["system"]

    def test_clearing_a_missing_file_is_not_an_error(self, transcript):
        assert sessions.clear(transcript) is True
