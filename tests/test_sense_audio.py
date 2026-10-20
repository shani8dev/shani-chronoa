"""The `audio` sense, and the two ways the `wpctl` tree defeats a parser.

The real `wpctl status` on this machine, verbatim including box-drawing:

    ├─ Sinks:
    │  *   54. ... Speaker + Headphones  [vol: 0.50]
    ├─ Sources:
    │      55. ... Headphones Stereo Microphone  [vol: 1.00]
    │  *   56. ... Digital Microphone  [vol: 0.43]

Two parsers got this wrong before one got it right, and both reported **no
audio devices** on a machine with seven:

- the heading is `├─ Sinks:`, so stripping only `│` leaves the tree prefix, and
  a "contains no spaces" test on the result never matches — the section is
  skipped entirely;
- the default marker is a `*`, not a `•`, and only the default has one, so
  looking for a bullet finds at most one device however many exist.

**Sinks and sources are never summed.** An output and an input are not two of
the same thing.

**Correction (2026-09-29): the third way this tree defeats a parser.** An
earlier version of this file's fixture listed `Integrated Camera (V4L2)` among the
Audio block's sources and the docstring defended it as correct — a webcam with a
built-in microphone *is* an audio capture node. The reasoning is sound, but the
fixture did not match this machine. Real `wpctl status` puts that camera under the
**Video** block's `Sources:` heading, and the parser tracked only "am I inside
Sources", so a video device reached the microphone list. That is not cosmetic:
`set_mic_mute` picks its target from that list, so muting the microphone could
have muted the camera — an action that reports success and changes nothing
audible.

The fix is by block, never by name, because both cases are real: a camera listed
under Video is excluded, and a webcam microphone listed under Audio is kept. The
second fixture below pins that pair directly. `block` is left `None` when no
header is recognised and `None` is accepted, so a format change that drops the
headers degrades to reading every Sinks/Sources rather than reporting no devices
— the failure this docstring opened by recording.
"""

import pytest

from shani_chronoa.senses import audio

WPCTL_STATUS = """PipeWire 'pipewire-0' [1.0.5, host]
   └─ Clients:
          32. pipewire                            [1.0.5, host, pid:2622]
   └─ Sinks:
 │      51. Tiger Lake-LP Smart Sound Technology Audio Controller HDMI / DisplayPort 3 Output  [vol: 1.00]
 │  *   54. Tiger Lake-LP Smart Sound Technology Audio Controller Speaker + Headphones  [vol: 0.50]
 │
 ├─ Sources:
 │      55. Tiger Lake-LP Smart Sound Technology Audio Controller Headphones Stereo Microphone  [vol: 1.00 MUTED]
 │  *   56. Tiger Lake-LP Smart Sound Technology Audio Controller Digital Microphone  [vol: 0.43]
 │      49. Integrated Camera (V4L2)
"""

WPCTL_EMPTY = "PipeWire 'pipewire-0' [1.0.5, host]\n   └─ Clients:\n"


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setattr(audio.shutil, "which", lambda n: "/usr/bin/wpctl")
    state = {"status": WPCTL_STATUS}

    def fake(argv, **kwargs):
        class R:
            stdout = state["status"]
            stderr = ""
            returncode = 0
        return R()
    monkeypatch.setattr(audio.subprocess, "run", fake)
    return state


@pytest.fixture
def granted(monkeypatch):
    monkeypatch.setattr(audio.ChronoaConfig, "sense_allowed",
                        lambda self, s: True)


class TestTheTree:
    def test_sinks_and_sources_are_both_found(self, wired):
        """The bug: reporting nothing at all on a machine with seven devices."""
        found = audio._parse_status(WPCTL_STATUS)
        assert len(found["sinks"]) == 2
        assert len(found["sources"]) == 3

    def test_the_box_drawing_heading_is_recognised(self, wired):
        """`├─ Sinks:` has a tree prefix; a "no spaces" test never matched."""
        assert audio._parse_status(WPCTL_STATUS)["sinks"], (
            "the Sinks section was skipped entirely"
        )

    def test_the_star_marks_the_default(self, wired):
        found = audio._parse_status(WPCTL_STATUS)
        assert {s["id"]: s["default"] for s in found["sinks"]} == {
            "51": False, "54": True}
        assert {s["id"]: s["default"] for s in found["sources"]}["49"] is False

    def test_a_client_is_not_an_audio_device(self, wired):
        """"Clients:" is a third section and its ids are not devices."""
        ids = {s["id"] for s in audio._parse_status(WPCTL_STATUS)["sinks"]}
        assert "32" not in ids

    def test_the_volume_is_a_number_not_a_string(self, wired):
        found = audio._parse_status(WPCTL_STATUS)
        assert found["sinks"][1]["volume"] == 0.50
        assert isinstance(found["sinks"][1]["volume"], float)

    def test_the_mute_flag_is_parsed_separately_from_volume(self, wired):
        """The two are separate facts, and both are in the same annotation.

        A muted node's line ends `[vol: 1.00 MUTED]` - measured by muting a
        live source and reading the status back - so the volume and the mute
        state come out of one bracket and must not swallow each other.

        This used to look for `[muted: yes]`, a marker wpctl does not write:
        its binary carries no such string, so the parse could only ever have
        matched a device's own description, and the mute flag was None on every
        machine. The sense's own count of muted devices was therefore always
        zero, whatever was actually muted.
        """
        muted = [s for s in audio._parse_status(WPCTL_STATUS)["sources"]
                 if s["id"] == "55"][0]
        assert muted["muted"] is True
        assert muted["volume"] == 1.00, "a muted node still reports its volume"
        unmuted = [s for s in audio._parse_status(WPCTL_STATUS)["sources"]
                   if s["id"] == "56"][0]
        assert unmuted["muted"] is False, (
            "a node with a volume and no MUTED marker is known to be unmuted, "
            "not unknown"
        )

    def test_a_node_with_no_annotation_is_not_known_to_be_unmuted(self, wired):
        """The integrated camera is the real case: wpctl refuses to mute it.

        Measured on this machine - `wpctl set-mute` on the camera answers "Node
        does not support mute" and `wpctl get-volume` answers "does not support
        volume", and its status line carries no annotation at all. A node whose
        state was never reported is unknown, not unmuted.
        """
        camera = [s for s in audio._parse_status(WPCTL_STATUS)["sources"]
                  if "Camera" in s["name"]][0]
        assert camera["muted"] is None
        assert camera["volume"] is None


class TestTheMutedCount:
    """The number the sense reports, which used to be zero on every machine.

    `wpctl status` carries the mute state inside the volume annotation, so the
    count could always have been right - and was not, because two modules
    parsed a `[muted: yes]` marker that wpctl does not write. A count of muted
    devices that is always zero is the confident wrong answer: it says nothing
    is muted, on a machine where something is.
    """

    def test_a_muted_device_is_counted(self, wired, granted):
        percept = audio._run({})
        assert percept.metadata["muted"] == 1, (
            "a muted microphone was not counted - the marker is "
            "[vol: 1.00 MUTED], and this used to look for [muted: yes]"
        )

    def test_the_count_is_not_the_number_of_annotations(self, wired, granted):
        """A device with no annotation is unknown, and unknown is not muted.

        The camera in the fixture carries no volume and no mute state, so it is
        not counted - and it is not counted as *unmuted* either, which is why
        the count is a count of muted devices and not of all devices.
        """
        percept = audio._run({})
        assert percept.metadata["sinks"] == 2
        assert percept.metadata["sources"] == 3
        assert percept.metadata["muted"] == 1

    def test_no_muted_device_reads_as_zero(self, wired, granted, monkeypatch):
        """The control: with nothing muted the count really is zero.

        A test that only ever saw a non-zero count could not tell a count that
        works from a constant.
        """
        monkeypatch.setattr(audio, "_run_wpctl",
                            lambda *a: type("R", (), {
                                "stdout": WPCTL_STATUS.replace(
                                    "[vol: 1.00 MUTED]", "[vol: 1.00]"),
                                "stderr": "", "returncode": 0})())
        percept = audio._run({})
        assert percept.metadata["muted"] == 0

    def test_the_id_is_the_first_number_not_the_vendor(self, wired):
        assert audio._parse_status(WPCTL_STATUS)["sinks"][0]["id"] == "51"


class TestSinksAreNotSources:
    def test_they_are_never_summed_into_one_count(self, wired, granted):
        content = audio._run({}).content
        assert "4 output(s) and 3 input(s)" in content or \
               "2 output(s) and 3 input(s)" in content
        assert "plays" in content and "records" in content

    def test_a_camera_among_the_sources_is_explained(self, wired, granted):
        """It is not a mistake and it is not a microphone."""
        content = audio._run({}).content
        assert "note: a camera appears among the recording sources" in content
        assert "V4L2 device is an audio capture node" in content

    def test_every_annotation_is_stripped_from_the_name(self, wired):
        """Only `[vol: …]` was being removed, so a muted device carried the
        whole bracket into its name — and the name is what a person reads.

        The bracket a muted node carries is `[vol: 1.00 MUTED]`, so the strip
        has to take the whole group rather than the part after `[vol:`.
        """
        for entry in (audio._parse_status(WPCTL_STATUS)["sinks"]
                      + audio._parse_status(WPCTL_STATUS)["sources"]):
            assert "[" not in entry["name"], entry["name"]
            assert "vol:" not in entry["name"], entry["name"]
            assert "MUTED" not in entry["name"], entry["name"]
        muted = [s for s in audio._parse_status(WPCTL_STATUS)["sources"]
                 if s["id"] == "55"][0]
        assert muted["name"] == (
            "Tiger Lake-LP Smart Sound Technology Audio Controller "
            "Headphones Stereo Microphone")
        assert muted["muted"] is True

    def test_a_real_microphone_is_not_mistaken_for_the_camera(self, wired, granted):
        sources = [s["name"] for s in audio._parse_status(WPCTL_STATUS)["sources"]]
        assert sum(1 for n in sources if "Microphone" in n) == 2, sources
        assert sum(1 for name in sources if "Camera" in name) == 1, (
            f"the camera appears {sum(1 for n in sources if 'Camera' in n)} times: {sources}"
        )
        # The camera is an input, so it must not be listed among the outputs.
        sinks = [s["name"] for s in audio._parse_status(WPCTL_STATUS)["sinks"]]
        assert not any("Camera" in name for name in sinks)


class TestTheReadingLeadsWithASummary:
    """**The first line is what a panel that shows one line shows.**

    The Memory panel showed a machine-state percept's content as a row's
    title, and the audio sense's content is a device list - so the panel
    read as a log file. The sense now leads with a sentence and the device
    list follows it.
    """

    def test_the_first_line_is_a_summary(self, wired, granted):
        content = audio._run({}).content
        first = content.splitlines()[0]
        assert "can play" in first and "can record" in first, first

    def test_the_detail_is_still_there(self, wired, granted):
        """**Moved, not dropped.** The device list is what a person asks the
        machine for."""
        content = audio._run({}).content
        assert "plays (" in content and "records (" in content, content
        assert "marked default" in content, content


class TestDegradation:
    def test_a_missing_wpctl_is_its_own_fact(self, monkeypatch, granted):
        monkeypatch.setattr(audio.shutil, "which", lambda n: None)
        percept = audio._run({})
        assert percept.metadata["available"] is False
        assert "wpctl is not installed" in percept.content
        assert "rather than that there is no sound hardware" in percept.content, (
            "a missing tool was reported as a machine with no audio hardware"
        )

    def test_a_running_server_with_no_devices_is_a_different_fact(self, wired, granted):
        wired["status"] = WPCTL_EMPTY
        percept = audio._run({})
        assert percept.metadata["available"] is True
        assert percept.metadata["sinks"] == 0
        assert "not the same as the audio server not running" in percept.content

    def test_an_unreachable_server_is_not_no_devices(self, monkeypatch, granted):
        import subprocess

        def fake(argv, **kwargs):
            raise subprocess.TimeoutExpired("wpctl", 20)
        monkeypatch.setattr(audio.shutil, "which", lambda n: "/usr/bin/wpctl")
        monkeypatch.setattr(audio.subprocess, "run", fake)
        assert audio.read_devices()["available"] is False

    def test_it_refuses_when_consent_is_off(self, monkeypatch):
        monkeypatch.setattr(audio.ChronoaConfig, "sense_allowed",
                            lambda self, s: False)
        assert isinstance(audio._run({}), str)

    def test_it_is_a_well_formed_sense(self):
        sense = audio.SENSES[0]
        assert sense.name == "audio"
        assert sense.schema["function"]["name"] == "audio"
        assert sense.poll_interval and sense.poll_interval >= 60


#: Real `wpctl status` from this machine, with both blocks, verbatim including
#: box-drawing. The Video block's camera is starred as default, which is the
#: nasty case: a default-starred camera is what `set_mic_mute` would have
#: targeted, had the Audio block listed it first.
WPCTL_TWO_BLOCKS = """PipeWire 'pipewire-0' [1.0.5, host]
Audio
   ├─ Devices:
   │      48. Tiger Lake-LP Smart Sound Technology Audio Controller [alsa]
   │
   ├─ Sinks:
   │  *   54. Tiger Lake-LP Smart Sound Technology Audio Controller Speaker + Headphones  [vol: 0.50]
   │
   └─ Sources:
   │      55. Tiger Lake-LP Smart Sound Technology Audio Controller Headphones Stereo Microphone  [vol: 1.00]
   │  *   56. Tiger Lake-LP Smart Sound Technology Audio Controller Digital Microphone  [vol: 0.43]
   │
Video
   ├─ Devices:
   │      46. Integrated Camera  [v4l2]
   │
   └─ Sources:
   │  *   49. Integrated Camera (V4L2)

Settings
"""


#: The same machine, with a webcam that exposes an audio stream, so PipeWire
#: lists its microphone under the **Audio** block. Indistinguishable from the
#: Video-block camera by name alone.
WPCTL_CAMERA_AS_AUDIO_SOURCE = """PipeWire 'pipewire-0' [1.0.5, host]
Audio
   ├─ Sinks:
   │  *   54. Tiger Lake-LP Smart Sound Technology Audio Controller Speaker + Headphones  [vol: 0.50]
   │
   └─ Sources:
   │      55. Tiger Lake-LP Smart Sound Technology Audio Controller Headphones Stereo Microphone  [vol: 1.00]
   │  *   56. Tiger Lake-LP Smart Sound Technology Audio Controller Digital Microphone  [vol: 0.43]
   │      57. Integrated Camera Microphone  [vol: 1.00]
   │
Video
   └─ Sources:
   │      49. Integrated Camera (V4L2)
"""

class TestVideoBlockSourcesAreNotMicrophones:
    """`wpctl status` has a `Sources:` heading under Video as well as under Audio.

    Nothing in the heading distinguishes them, so a parser that tracks only
    "am I inside Sources" appends the integrated camera to the microphone list.
    That is not cosmetic: `set_mic_mute` chooses its target from this list, so
    "mute the microphone" could have muted the camera - an action that reports
    success and changes nothing the user can hear.
    """

    def test_the_camera_is_not_listed_as_a_microphone(self):
        found = audio._parse_status(WPCTL_TWO_BLOCKS)
        assert not [e for e in found["sources"] if "Camera" in e["name"]], \
            f"a video device reached the microphone list: {found['sources']}"

    def test_the_camera_is_not_silently_reclassified_as_a_sink(self):
        found = audio._parse_status(WPCTL_TWO_BLOCKS)
        assert not [e for e in found["sinks"] if "Camera" in e["name"]]

    def test_the_real_microphones_survive(self):
        found = audio._parse_status(WPCTL_TWO_BLOCKS)
        assert [e["id"] for e in found["sources"]] == ["55", "56"]
        assert any(e["default"] for e in found["sources"]), \
            "the default microphone was lost along with the camera"

    def test_the_default_source_is_a_microphone_not_the_camera(self):
        # The ordering is what made this survivable rather than catastrophic: the
        # Audio block is printed first, so the default-starred microphone was
        # found before the default-starred camera. Relying on print order is
        # not a fix, and this is the assertion that would notice it stopping.
        found = audio._parse_status(WPCTL_TWO_BLOCKS)
        defaults = [e for e in found["sources"] if e["default"]]
        assert len(defaults) == 1
        assert "Microphone" in defaults[0]["name"]

    def test_a_camera_that_is_a_real_audio_source_is_kept(self):
        # A webcam with a built-in microphone *is* an audio capture node, and
        # PipeWire lists it under the Audio block. Filtering by name would
        # discard a genuine capture device, so the filter is by block.
        #
        # This is its own literal rather than a `.replace()` into the fixture
        # above: patching a box-drawing line silently matched nothing (the
        # default marker `*` sits in the middle of it) and the assertion then ran
        # against the unpatched fixture. An explicit fixture cannot no-op.
        found = audio._parse_status(WPCTL_CAMERA_AS_AUDIO_SOURCE)
        assert [e["id"] for e in found["sources"]] == ["55", "56", "57"]
        assert "Integrated Camera Microphone" in found["sources"][2]["name"]

    def test_the_two_fixtures_disagree_about_the_camera(self):
        # The control for the test above: the same camera name is excluded from
        # one fixture and kept from the other, so the difference is provably the
        # block it was listed under rather than anything about the name.
        excluded = audio._parse_status(WPCTL_TWO_BLOCKS)
        kept = audio._parse_status(WPCTL_CAMERA_AS_AUDIO_SOURCE)
        assert not [e for e in excluded["sources"] if "Camera" in e["name"]]
        assert [e for e in kept["sources"] if "Camera" in e["name"]]

    def test_output_with_no_block_headers_still_parses(self):
        # The fallback that must not be lost. Being strict about the block name
        # would report *no* devices here - the exact failure this module's own
        # docstring records two past parsers producing.
        found = audio._parse_status(WPCTL_STATUS)
        assert found["sinks"] and found["sources"], \
            "a format without block headers produced no devices at all"

    def test_a_settings_block_does_not_reset_device_discovery(self):
        text = WPCTL_TWO_BLOCKS + "\n   └─ Some Audio Setting:\n      90. irrelevant\n"
        found = audio._parse_status(text)
        assert [e["id"] for e in found["sources"]] == ["55", "56"]
