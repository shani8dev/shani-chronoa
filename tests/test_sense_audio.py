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
the same thing, and this machine's `Integrated Camera (V4L2)` appears as a
*source* — a V4L2 device is an audio capture node to the audio server too,
which is worth saying rather than looking like a mistake.
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
 │      55. Tiger Lake-LP Smart Sound Technology Audio Controller Headphones Stereo Microphone  [vol: 1.00]
 │  *   56. Tiger Lake-LP Smart Sound Technology Audio Controller Digital Microphone  [vol: 0.43]
 │      49. Integrated Camera (V4L2)                    [muted: yes]
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
        camera = [s for s in audio._parse_status(WPCTL_STATUS)["sources"]
                  if "Camera" in s["name"]][0]
        assert camera["muted"] is True
        assert camera["volume"] is None, "a muted device with no [vol:] is not volume 0"

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

    def test_a_real_microphone_is_not_mistaken_for_the_camera(self, wired, granted):
        sources = [s["name"] for s in audio._parse_status(WPCTL_STATUS)["sources"]]
        assert sum(1 for n in sources if "Microphone" in n) == 2, sources
        assert sum(1 for name in sources if "Camera" in name) == 1, (
            f"the camera appears {sum(1 for n in sources if 'Camera' in n)} times: {sources}"
        )
        # The camera is an input, so it must not be listed among the outputs.
        sinks = [s["name"] for s in audio._parse_status(WPCTL_STATUS)["sinks"]]
        assert not any("Camera" in name for name in sinks)


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
