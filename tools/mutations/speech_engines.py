"""Mutation set for the two speech engines being interchangeable, and for
neither of them streaming.

**The shared surface is the contract**: `stt.build_stt` hands the caller
one of two engines and every caller asks the same three questions of it.
Measured, the two classes have identical public surfaces, so the choice is
a selection rather than a branch every caller has to write.

**And `transcribe_stream` does not stream.** The bytes become a WAV and
the WAV becomes one final answer. The Parakeet engine's docstring said
so; the whisper.cpp one called itself "streaming mode", which is a name
promising something the program does not do.

The set is the three ways that can go wrong: the surfaces diverge, the
bytes stop being a file, and the summary promises streaming again.
"""

TEST_FILE = "tests/test_the_two_speech_engines_are_interchangeable.py"

_STT = "usr/lib/shani-chronoa/shani_chronoa/stt.py"

MUTATIONS = [
    {
        "file": _STT,
        "old": '        """Transcribe audio bytes to text.\n\n'
               '        **Not streaming recognition.** The name is the shared interface\'s -\n',
        "new": '        """Transcribe audio bytes to text (streaming mode).\n\n'
               '        **Not streaming recognition.** The name is the shared interface\'s -\n',
        # **The shipped wording.** A summary that promises something the
        # program does not do - the bytes are written to a WAV and
        # transcribed as a file, which is one final answer.
        "label": "the whisper engine's summary promises streaming again",
        "expect": ["test_the_whisper_engine_does_not_claim_a_streaming_mode"],
    },
    {
        "file": _STT,
        "old": "        try:\n            text = self.transcribe(tmp_path)\n",
        "new": "        try:\n            text = self.transcribe(audio_data)\n",
        # The bytes go straight in instead of becoming a file, so the engine
        # claims to take a stream and is handed one.
        "label": "the engine is handed the bytes instead of a file",
        "expect": ["test_the_bytes_become_a_file_that_is_transcribed"],
    },
    {
        "file": _STT,
        "old": "    def transcribe_stream(self, audio_data: bytes) -> str:\n",
        "new": "    def transcribe_bytes(self, audio_data: bytes) -> str:\n",
        # **One engine gains a method the other lacks**, and every caller
        # that asks both has to write a branch - which is the whole thing
        # the shared surface exists to prevent.
        "label": "one engine's surface diverges from the other's",
        "expect": ["test_the_two_engines_have_the_same_surface"],
    },
]
