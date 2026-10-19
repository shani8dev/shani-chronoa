"""Whether a microphone is muted is readable, and this file is why that is
a fact rather than a belief.

## The mistake this file records

Three modules in this tree had three different answers to "is this node muted",
and only one of them was right:

- `senses/audio.py` and `skills/privacy.py` parsed `wpctl status` output for a
  **`[muted: yes]`** marker. wpctl's binary carries no such string, so the
  parse could only ever have matched a device's own description - and it never
  fired. The audio sense's count of muted devices was therefore **always zero**,
  whatever was actually muted, while its own schema description promised "each
  with its volume, whether it is muted, and which is the default".
- `skills/set_mic_mute.py` ran `wpctl get-volume`, and then returned `None` on
  every path - it read the answer and threw it away, because its docstring
  asserted that wpctl cannot report a mute state. So "is the microphone
  muted" was always UNKNOWN.
- `skills/privacy.py`'s post-condition read a GSettings key,
  `privacy-mic-muted`, that **nothing writes** and that is not in the schema.
  The effect was not a GSettings key at all: `_mute_mic` runs `wpctl set-mute`.
  So the check could not tell its two cases apart - muting was reported as
  FAILED every single time even when the microphone really was muted, and
  unmuting was reported as VERIFIED every single time without having checked
  anything.

## What was measured

By muting a live source on this machine and reading both commands back (the
source was unmuted again immediately afterwards, and the restoration was
verified):

    wpctl status        ->  │  61. ... Stereo Microphone [vol: 1.00 MUTED]
    wpctl get-volume 61 ->  Volume: 1.00 [MUTED]

**The two commands use different markers.** `status` puts `MUTED` *inside* the
volume bracket; `get-volume` puts `[MUTED]` *after* the level. A reader that
takes one for the other reads a permanently-unmuted machine as muted, or a
muted machine as unmuted.

And the integrated camera is the case that is neither: `wpctl` refuses to mute
it ("Node 79 does not support mute"), refuses to report a volume for it, and
its status line carries no annotation at all. A node whose state was never
reported is **unknown**, not unmuted.
"""

import pytest

from shani_chronoa import pipewire


class TestTheStatusMarker:
    """`wpctl status` writes `MUTED` inside the volume bracket."""

    def test_a_muted_node_is_muted(self):
        assert pipewire.status_muted(
            "Tiger Lake-LP ... Stereo Microphone [vol: 1.00 MUTED]") is True

    def test_an_unmuted_node_is_not_muted(self):
        assert pipewire.status_muted(
            "Tiger Lake-LP ... Stereo Microphone [vol: 1.00]") is False

    def test_the_two_are_different_answers(self):
        """A control: a reader that answered the same either way would pass
        both of the tests above."""
        muted = pipewire.status_muted("Name [vol: 1.00 MUTED]")
        unmuted = pipewire.status_muted("Name [vol: 1.00]")
        assert muted is True and unmuted is False
        assert muted is not unmuted

    def test_a_node_with_no_annotation_is_unknown_not_unmuted(self):
        """The integrated camera: wpctl will not mute it and will not report a
        volume for it, so its status line carries no annotation at all."""
        assert pipewire.status_muted("Integrated Camera (V4L2)") is None

    def test_a_name_that_merely_mentions_muted_is_not_a_mute(self):
        """The marker is read from the annotation, not from the text.

        A device called "MUTED Buttons" must not read as muted, and a
        description carrying brackets must not be mistaken for a volume
        annotation.
        """
        assert pipewire.status_muted("MUTED Buttons") is None
        assert pipewire.status_muted("Speaker [aux]") is None


class TestTheGetVolumeMarker:
    """`wpctl get-volume` writes `[MUTED]` after the level - a different form."""

    def test_a_muted_node_is_muted(self, fake_wpctl):
        fake_wpctl({"get-volume": "Volume: 1.00 [MUTED]"})
        assert pipewire.mute_state("61").muted is True

    def test_an_unmuted_node_is_not_muted(self, fake_wpctl):
        fake_wpctl({"get-volume": "Volume: 1.00"})
        assert pipewire.mute_state("61").muted is False

    def test_the_text_is_kept_for_the_evidence(self, fake_wpctl):
        """A post-condition's evidence quotes what wpctl actually printed."""
        fake_wpctl({"get-volume": "Volume: 1.00 [MUTED]"})
        assert pipewire.mute_state("61").text == "Volume: 1.00 [MUTED]"

    def test_a_failed_read_is_unknown(self, fake_wpctl):
        fake_wpctl({"get-volume": "", "returncode": 1, "stderr": "No such node"})
        assert pipewire.mute_state("61").muted is None

    def test_a_command_that_printed_nothing_is_unknown(self, fake_wpctl):
        """A command that exited 0 and said nothing has not answered.

        The marker is absent from every unmuted node, so an empty output and an
        unmuted node are the same absence - and only one of them is a fact.
        """
        fake_wpctl({"get-volume": ""})
        assert pipewire.mute_state("61").muted is None

    def test_the_two_commands_do_not_share_a_marker(self, fake_wpctl):
        """The get-volume form is not the status form.

        `status` says `[vol: 1.00 MUTED]`; `get-volume` says
        `Volume: 1.00 [MUTED]`. A reader that took one for the other would
        read a permanently-unmuted machine as muted.
        """
        fake_wpctl({"get-volume": "Volume: 1.00 [MUTED]"})
        assert pipewire.mute_state("61").muted is True
        # The get-volume output is not a status entry, so the status reader
        # declines it rather than answering "muted" from a marker it does not
        # use. Declining is the honest answer: this is not a shape wpctl's
        # `status` produces.
        assert pipewire.status_muted("Volume: 1.00 [MUTED]") is None


@pytest.fixture
def fake_wpctl(monkeypatch):
    """A `wpctl` answering with what the test tells it to."""
    calls = []

    class _Proc:
        def __init__(self, stdout="", returncode=0, stderr=""):
            self.stdout, self.returncode, self.stderr = stdout, returncode, stderr

    def run(*args):
        calls.append(list(args))
        answers = getattr(fake_wpctl, "answers", {})
        key = args[0] if args else ""
        stdout = answers.get(key, "")
        return _Proc(stdout,
                     answers.get("returncode", 0),
                     answers.get("stderr", ""))

    monkeypatch.setattr(pipewire, "run_wpctl", run)
    fake_wpctl.calls = calls

    def fake(answers):
        fake_wpctl.answers = answers

    fake.answers = {}
    return fake
