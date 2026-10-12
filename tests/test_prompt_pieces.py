"""The prompt override, and the half of it that is deliberately absent.

`harness-study/laya` (a local-first AI notification centre, Python engine) states
its headline feature as: *"Override 20+ pipeline stages individually - change one,
keep the rest as default. Delete the file to revert - defaults always come back."*
Chronoa's `SYSTEM_PROMPT` was one hard-coded string, so a person had no lever that
survived an update. `prompt_pieces.py` is that lever.

Four of Laya's decisions are the whole of the transfer, and each has a test
because each is a way a simpler version lies:

1. **Delete to revert** - the default lives in code and nothing here writes the
   directory. `test_deleting_the_file_reverts`.
2. **An unrecognised file name is a warning naming the recognised ones**, or a
   typo (`rout.md` for `router.md`) tunes a string nobody reads.
3. **An empty file is a warning and not an override** - replacing a prompt with
   nothing is worse than not replacing it.
4. **`override_status()` answers "what am I overriding"** without reading source.

**And the property that matters more than any of them:** the provenance
boundary sentence - the one telling the model what the untrusted-content fence
means - is **not in the registry**. There is no key for it, so no file can name
it. Laya's phrasing for the same construction is that identifier fields are
"absent by construction because capabilities don't list them as content"; here a
security statement is not a preference, and the way to keep it unoverridable is
to leave no door rather than to check the door harder.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "usr" / "lib"))

from shani_chronoa import prompt_pieces  # noqa: E402
from shani_chronoa.assistant import SYSTEM_PROMPT  # noqa: E402
from shani_chronoa.provenance import describe_boundary  # noqa: E402


@pytest.fixture
def prompts(tmp_path, monkeypatch):
    directory = tmp_path / ".config" / "shani-chronoa" / "prompts"
    directory.mkdir(parents=True)
    monkeypatch.setattr(prompt_pieces, "PROMPTS_DIR", directory)
    # A module-level cache would outlive the fixture and make every later test
    # read a previous test's file.
    monkeypatch.setattr(prompt_pieces, "_overrides", {})
    return directory


class TestTheDefaultIsWhatItAlwaysWas:
    def test_no_overrides_means_the_exact_prompt(self):
        """**The no-op path, and it must be byte-identical.** A person who has
        written no file must get exactly what the constant gave - a preference
        mechanism that rewords the prompt when nobody expressed a preference is
        a change nobody asked for."""
        assert prompt_pieces.compose({}) == SYSTEM_PROMPT

    def test_the_boundary_is_part_of_the_prompt_and_not_a_piece(self):
        assert describe_boundary() in prompt_pieces.compose({})
        assert not any(describe_boundary() in piece["default"]
                       for piece in prompt_pieces.PIECES.values()), (
            "the safety boundary is one of the overridable pieces, so a file "
            "could replace it")


class TestDeleteToRevert:
    def test_a_file_replaces_one_piece(self, prompts):
        (prompts / "spoken-reply.md").write_text(
            "Answer in British English, always.\n", encoding="utf-8")
        assert prompt_pieces.compose(prompt_pieces.load_overrides()) != SYSTEM_PROMPT
        assert "British English" in prompt_pieces.compose()

    def test_deleting_the_file_reverts(self, prompts):
        target = prompts / "spoken-reply.md"
        target.write_text("Answer in British English, always.\n",
                          encoding="utf-8")
        assert "British English" in prompt_pieces.compose()
        # **The whole revert mechanism.** No state is written, no directory is
        # created by this module, so deleting is the entire operation.
        target.unlink()
        assert prompt_pieces.compose() == SYSTEM_PROMPT

    def test_nothing_here_creates_the_directory(self, tmp_path, monkeypatch):
        missing = tmp_path / "not" / "there"
        monkeypatch.setattr(prompt_pieces, "PROMPTS_DIR", missing)
        prompt_pieces.load_overrides()
        assert not missing.exists(), (
            "the override loader made the directory, so a run that read an empty "
            "one leaves behind a directory that looks like it has content")


class TestTheThreeWaysASimplerVersionLies:
    def test_a_typo_file_name_is_named_not_ignored(self, prompts, caplog):
        (prompts / "rout.md").write_text("Ignore me.\n", encoding="utf-8")
        with caplog.at_level("WARNING", logger="shani_chronoa.prompt_pieces"):
            prompt_pieces.load_overrides()
        assert "rout.md" in caplog.text, (
            "a file with a typo'd name was dropped in silence, so a person tunes "
            "a string nobody reads")
        # **The recognised names, not just the word.** The first version asserted
        # `"recognised" in caplog.text`, which the word *unrecognised* satisfies -
        # so a warning that named the problem and not the answer still passed.
        for filename in prompt_pieces._FILES.values():
            assert filename in caplog.text, (
                f"the warning did not say {filename} is a recognised file name, "
                f"so it names the problem without offering the answer: "
                f"{caplog.text!r}")

    def test_an_empty_file_is_not_an_override(self, prompts, caplog):
        (prompts / "spoken-reply.md").write_text("   \n\n", encoding="utf-8")
        with caplog.at_level("WARNING", logger="shani_chronoa.prompt_pieces"):
            prompt_pieces.load_overrides()
        assert prompt_pieces.compose() == SYSTEM_PROMPT, (
            "an empty file replaced the prompt with nothing, which is worse than "
            "not replacing it")
        assert "empty" in caplog.text

    def test_a_piece_that_is_not_a_preference_is_refused(self, caplog):
        """Nothing is marked unoverridable today, so this asserts the *mechanism*
        - the flag is honoured when it is set, or the flag is decoration."""
        import types

        monkey = types.SimpleNamespace()
        # The flag is read from the piece itself, so a piece flipped to False
        # must be refused rather than silently accepted.
        original = prompt_pieces.ALL_PIECES["identity"]["overridable"]
        try:
            prompt_pieces.ALL_PIECES["identity"]["overridable"] = False
            directory = Path("/tmp/opencode/pp-refuse")
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "identity.md").write_text("I am a teapot.\n",
                                                   encoding="utf-8")
            monkey = directory
            prompt_pieces._overrides.clear()
            old = prompt_pieces.PROMPTS_DIR
            prompt_pieces.PROMPTS_DIR = monkey
            try:
                prompt_pieces.load_overrides()
            finally:
                prompt_pieces.PROMPTS_DIR = old
            assert "teapot" not in prompt_pieces.compose(), (
                "a piece flagged unoverridable was replaced anyway")
        finally:
            prompt_pieces.ALL_PIECES["identity"]["overridable"] = original


class TestWhatAmIOverriding:
    def test_the_status_function_answers_it(self, prompts):
        assert all(row["overridden"] is False for row in prompt_pieces.override_status())
        (prompts / "identity.md").write_text("I am a teapot.\n", encoding="utf-8")
        status = {row["key"]: row for row in prompt_pieces.override_status()}
        assert status["identity"]["overridden"] is True
        assert status["identity"]["current"] == "I am a teapot."
        assert status["identity"]["default"] != "I am a teapot.", (
            "the status did not keep the default, so there is nothing to revert "
            "to and nothing to compare against")

    def test_every_declared_file_is_in_the_status(self):
        """A piece with no status row is invisible in the diagnostics - the
        "which of my prompts am I overriding" question would have a hole."""
        status = {row["file"] for row in prompt_pieces.override_status()}
        assert status == set(prompt_pieces._FILES.values()), (
            f"status lists {sorted(status)} but the registry declares "
            f"{sorted(prompt_pieces._FILES.values())}")


class TestItReachesTheConversation:
    def _llm(self):
        class _LLM:
            def chat_message(self, *_a, **_k):
                return "ok"

            def is_available(self):
                return True

        return _LLM()

    def test_the_rebuilt_assistant_uses_the_override(self, monkeypatch):
        from shani_chronoa.assistant import Assistant

        prompts_dir = Path("/tmp/opencode/pp-assistant")
        prompts_dir.mkdir(parents=True, exist_ok=True)
        (prompts_dir / "spoken-reply.md").write_text(
            "Answer in British English.\n", encoding="utf-8")
        monkeypatch.setattr(prompt_pieces, "PROMPTS_DIR", prompts_dir)
        try:
            assistant = Assistant(self._llm(), None)
        finally:
            (prompts_dir / "spoken-reply.md").unlink(missing_ok=True)
            prompts_dir.rmdir()
        system = assistant._history[0]["content"]
        assert "British English" in system, (
            "the override did not reach the message the model is sent")
        assert describe_boundary() in system, (
            "the safety boundary was lost on the way to the model")
