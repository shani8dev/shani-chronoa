"""WhisperSTT returns the words, not whisper-cli's console output.

The stand-in whisper-cli below behaves as the real 1.9.4 was measured to:
-otxt writes "<input>.txt" (q.wav -> q.wav.txt) unless -of names the base,
and stdout carries "[00:00:00.000 --> 00:00:02.400]" timestamps unless -nt.
Before the fix the file landed where WhisperSTT never looked, and every
spoken question reached the model with the timestamp in front of it.
"""

import stat

from shani_chronoa.stt import WhisperSTT

FAKE = r'''#!/usr/bin/env python3
import sys
a = sys.argv[1:]
inp = a[a.index("-f") + 1]
base = a[a.index("-of") + 1] if "-of" in a else inp
words = "what is two plus two"
if "-otxt" in a:
    open(base + ".txt", "w").write(" " + words + "\n")
print(words if "-nt" in a else "[00:00:00.000 --> 00:00:02.400]   " + words)
'''


def test_the_transcript_is_the_words_alone(tmp_path):
    cli = tmp_path / "whisper-cli"
    cli.write_text(FAKE)
    cli.chmod(cli.stat().st_mode | stat.S_IEXEC)
    model = tmp_path / "ggml-base-q5_1.bin"
    model.write_bytes(b"x")
    wav = tmp_path / "q.wav"
    wav.write_bytes(b"RIFF")
    stt = WhisperSTT(model="base-q5_1", whisper_path=str(cli))
    stt.model_path = str(model)
    text = stt.transcribe(str(wav))
    assert text == "what is two plus two", text
    assert not list(tmp_path.glob("*.txt")), "the transcript file must not be left behind"
