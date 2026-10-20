"""The two speech engines are interchangeable, and neither streams.

**The shared surface is the contract**: `stt.build_stt` hands the caller
one of two engines and every caller asks the same three questions of
it - `is_available`, `transcribe`, `transcribe_stream`. Measured, the two
classes have **identical** public surfaces - no method on one that the
other lacks - so the choice is a selection rather than a branch every
caller has to write.

**And `transcribe_stream` does not stream.** The name is the contract's;
the behaviour is one final answer. The bytes are written to a WAV and
transcribed as a file. The Parakeet engine's docstring said so; the
whisper.cpp one called itself "streaming mode", which is a name promising
something the program does not do.

**whisper.cpp cannot do the partial version.** Measured on the installed
`whisper-cli`: there is no streaming flag - the closest are `--vad`,
which *segments* the audio, and `--split-on-word`, which is a formatting
option - so a partial means chunking the audio and stabilising the
hypothesis between chunks.

**Parakeet could, and the blocker is a licence.** Upstream publishes a
streaming variant (`parakeet-tdt-0.6b-v3-streaming`), but under a
licence nobody has resolved - so no streaming path, flag or documentation
of one exists here, and the reason is not the code.
"""

from __future__ import annotations

import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "usr", "lib", "shani-chronoa"))

from shani_chronoa import stt  # noqa: E402
from shani_chronoa.stt_parakeet import ParakeetSTT  # noqa: E402


def _public(cls) -> set:
    """The class's public callables - the surface a caller may use."""
    return {name for name, value in vars(cls).items()
            if not name.startswith("_") and callable(value)}


class TestTheEnginesAreInterchangeable:
    def test_the_two_engines_have_the_same_surface(self):
        """**`build_stt` hands the caller one of two engines**, so every
        caller asks the same three questions of either - and a method on
        one that the other lacks is a branch every caller has to write."""
        assert _public(stt.WhisperSTT) == _public(ParakeetSTT), (
            f"whisper.cpp has {_public(stt.WhisperSTT)} and Parakeet has "
            f"{_public(ParakeetSTT)}")

    def test_the_surface_is_the_one_the_caller_uses(self):
        """**The three questions a caller actually asks** - and nothing
        else, so the two really are a selection."""
        assert _public(stt.WhisperSTT) == {"is_available", "transcribe",
                                              "transcribe_stream"}

    def test_the_backends_are_a_table_not_a_branch(self):
        """**A third engine is a config change, not a rewrite**: the
        backend name is a tuple, the builder branches on it, and the
        settings row and the model cards read the same table."""
        assert stt.BACKENDS == ("whisper", "parakeet")
        source = (open(stt.__file__, encoding="utf-8").read())
        assert "BACKEND_PARAKEET" in source, (
            "the builder no longer branches on the backend name")


class TestNeitherEngineStreams:
    """**The bytes become a file, and the file becomes one answer.**

    That is the behaviour, and it is measured rather than read: the call
    reaches `transcribe` with a **path**, not with the bytes - so a
    caller passing a growing buffer gets a longer file transcribed each
    time, not a running hypothesis.
    """

    @pytest.mark.parametrize("engine", ["whisper", "parakeet"])
    def test_the_bytes_become_a_file_that_is_transcribed(self, engine, monkeypatch,
                                                          tmp_path):
        """**The engine transcribes a file, not a stream** - so the
        "streaming" name is the shared contract's and not a description of
        the behaviour."""
        if engine == "whisper":
            subject = stt.WhisperSTT(model="", language="en")
        else:
            subject = ParakeetSTT(model="", language="en")
        seen = {}

        def fake_transcribe(path):
            seen["path"] = path
            seen["bytes"] = open(path, "rb").read()
            return "a final answer"

        monkeypatch.setattr(subject, "transcribe", fake_transcribe)
        assert subject.transcribe_stream(b"RIFF....audio") == "a final answer"
        assert seen.get("bytes") == b"RIFF....audio", (
            "the engine did not write the bytes it was given")
        assert str(seen.get("path", "")).endswith(".wav"), (
            f"the engine transcribed {seen.get('path')!r} rather than a WAV")

    def test_the_whisper_engine_does_not_claim_a_streaming_mode(self):
        """**A summary that promises something the program does not do.**

        **The summary line, not the whole docstring** - the body is
        allowed to *name* the old wording to say that it was wrong, and a
        substring test over the whole text would read that quotation as
        the claim."""
        summary = (stt.WhisperSTT.transcribe_stream.__doc__ or "").splitlines()
        first = summary[0] if summary else ""
        assert "stream" not in first.lower(), (
            f"the whisper engine's summary still promises streaming: {first!r}")

    def test_the_parakeet_engine_says_so_too(self):
        """**The control**: the parakeet engine already said so, and the
        two do the same thing - so the honest one proves the check can
        tell them apart."""
        summary = (ParakeetSTT.transcribe_stream.__doc__ or "").splitlines()
        first = summary[0] if summary else ""
        assert "stream" not in first.lower(), (
            f"the parakeet engine's summary promises streaming: {first!r}")
