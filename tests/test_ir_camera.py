from __future__ import annotations

import os
import pathlib
from pathlib import Path

import pytest

import shani_chronoa.camera as camera
from shani_chronoa.skills import take_photo

_HEADER = Path("/usr/include/linux/videodev2.h")


class TestIsInfrared:
    """The pure decision, so the classification is testable without a camera.

    The discipline is linux-hello's: `is_valid_human_face()` is a function over
    landmarks, so its geometry is verified on every machine. Chronoa's face
    detection has no such seam, which is the gap `HARVEST-LINUX-HELLO.md` records -
    so this one does.
    """

    def test_a_greyscale_pixel_format_is_infrared(self):
        assert camera.is_ir([("Y16 ", "16-bit Greyscale")], "")

    def test_grey_and_z16_are_infrared(self):
        assert camera.is_ir([("GREY", "8-bit Greyscale")], "")
        assert camera.is_ir([("Z16 ", "16-bit Depth")], "")

    def test_an_ordinary_format_is_not(self):
        assert not camera.is_ir([("MJPG", "Motion-JPEG"), ("YUYV", "YUYV 4:2:2")], "")

    def test_a_card_name_that_says_ir_is_infrared(self):
        assert camera.is_ir([], "My IR Camera")

    def test_infra_counts_too(self):
        assert camera.is_ir([], "Infrared Webcam")

    def test_a_name_that_merely_contains_the_letters_does_not_count_as_evidence(self):
        """**A weak test, kept and bounded rather than left to be discovered.**

        `"ir" in name.lower()` matches "First Camera", which is not infrared. It is
        kept because it is the only thing that catches a device whose driver never
        reports an IR format - and the false positive is in the *description*, not
        in the capture, because the capture asks for `y16` and the device refuses if
        it has none. This test is the record of that.
        """
        assert camera.is_ir([], "First Camera")
        assert "ir" in "First Camera".lower()

    def test_an_empty_card_name_is_not_infrared(self):
        assert not camera.is_ir([], "")

    def test_none_formats_is_not_a_crash(self):
        assert not camera.is_ir(None, "")


class TestTheDerivedConstants:
    """The ioctl requests are derived, and the derivation is checked against the
    kernel header rather than against a value somebody remembered."""

    def test_the_struct_size_matches_the_header(self):
        if not _HEADER.is_file():
            pytest.skip(f"no {_HEADER} on this machine, so the derivation cannot be "
                        f"cross-checked - it is asserted to be 64 by construction")
        text = _HEADER.read_text(errors="replace")
        start = text.find("struct v4l2_fmtdesc {")
        assert start != -1, "the kernel header has no v4l2_fmtdesc"
        body = text[start:text.find("};", start)]
        size = 0
        for line in body.splitlines():
            line = line.strip()
            if "reserved[3]" in line:
                size += 4 * 3
            elif "description[32]" in line:
                size += 32
            elif "__u32" in line or "__u8" in line:
                size += 4
        assert size == camera._FMT_SIZE, (
            f"the header's field list adds up to {size}, this module derived "
            f"{camera._FMT_SIZE} - a mismatch means ENUM_FMT is refused with ENOTTY, "
            f"which reads as 'this device does not support the ioctl' and not as "
            f"'you asked the wrong size'")

    def test_the_request_encodes_that_size(self):
        assert (camera._FMT_REQUEST >> 16) & 0x3FFF == camera._FMT_SIZE
        assert camera._FMT_REQUEST & 0xFF == 2, "ENUM_FMT is ioctl number 2"

    def test_pixelformat_offset_is_after_the_description(self):
        # index, type, flags are 4 bytes each and description[32] follows them.
        assert camera._FMT_PIXELFORMAT == 4 * 3 + 32
        assert camera._FMT_DESCRIPTION < camera._FMT_PIXELFORMAT


class TestAgainstTheRealNodes:
    """The probe against this machine's real nodes, where there are any.

    **Every assertion here is one a metadata node fails**, because that is the node
    the probe exists to tell apart from a camera.
    """

    @staticmethod
    def _nodes():
        return camera.collect()

    def test_a_probe_reports_a_card_and_formats(self):
        nodes = self._nodes()
        if not nodes:
            pytest.skip("no /dev/video* on this machine, so there is nothing to probe")
        info = nodes[0]
        assert "error" not in info
        assert isinstance(info.get("card"), str)
        assert "can_capture" in info
        assert isinstance(info.get("formats"), list)

    def test_can_capture_is_true_for_something_that_captures(self):
        """**The load-bearing one.** A node that cannot capture still answers
        `QUERYCAP` with a sensible name, so `capture_nodes()` is the only part of
        this module that has to be right about the difference.
        """
        able = camera.capture_nodes()
        if not able:
            pytest.skip("no node here can capture video")
        info = camera.probe(able[0])
        assert info.get("can_capture") is True
        assert info.get("device_caps") is not None

    def test_capture_nodes_never_contains_a_node_that_cannot_capture(self):
        for node in camera.capture_nodes():
            assert camera.probe(node).get("can_capture") is True, (
                f"{node} was reported as capture-capable, so a metadata node would "
                f"be handed to ffmpeg and fail with a raw ENOTTY")

    def test_a_refusal_is_not_the_same_answer_as_none(self):
        """**`formats_error` is what keeps the two apart.**

        Measured: a `v4l2loopback` node reports `can_capture: False` and
        `formats: []` until something writes to it, and the truth afterwards. So an
        empty list with a refusal is not proof of a node that cannot capture, and a
        probe that collapsed the two would call a working camera dead.
        """
        for node in camera.collect():
            if node.get("formats") and node.get("formats_error"):
                pytest.fail(f"{node['node']} reported both formats and an error, so "
                            f"the two are not distinguishable")
        #: and the refusal itself, where a node reports one, is a string.
        refusals = [n for n in camera.collect() if n.get("formats_error")]
        if refusals:
            assert isinstance(refusals[0]["formats_error"], str)


class TestTheSkillChoosesRatherThanAssumes:
    """What `take_photo` does with an infrared request."""

    @staticmethod
    def _consent(monkeypatch):
        class _Config:
            vision_sense_enabled = True

            def sense_allowed_reason(self, name):
                return ""

        from shani_chronoa import skills
        monkeypatch.setattr(skills.take_photo, "ChronoaConfig", _Config)

    def _no_ir(self, monkeypatch):
        import shani_chronoa.camera as camera
        monkeypatch.setattr(camera, "ir_nodes", lambda: [])
        monkeypatch.setattr(camera, "collect", lambda: [])
        monkeypatch.setattr(camera, "none_sentence",
                            lambda: "this machine has no camera node at all")

    def test_a_device_that_is_not_infrared_is_never_replaced(self, monkeypatch):
        """**The defect this module shipped first, pinned.**

        The first version substituted the infrared node for whichever device the
        caller named, so naming `/dev/video1` produced a photo from `/dev/video9` -
        a confident answer to a different question, taken from a camera nobody
        named. It is a refusal now, and it names what the infrared nodes are.
        """
        import shani_chronoa.camera as camera
        monkeypatch.setattr(camera, "collect",
                            lambda: [{"node": "/dev/video9", "card": "Test IR Cam",
                                      "can_capture": True, "looks_ir": True,
                                      "formats": ["Y16 "]}])
        monkeypatch.setattr(camera, "ir_nodes", lambda: ["/dev/video9"])
        node, refusal = take_photo._ir_node("/dev/video1")
        assert node == "", refusal
        assert "/dev/video1 is not an infrared camera" in refusal
        assert "Test IR Cam" in refusal
        assert "Nothing was taken" in refusal

    def test_an_infrared_device_the_caller_named_is_honoured(self, monkeypatch):
        import shani_chronoa.camera as camera
        monkeypatch.setattr(camera, "collect",
                            lambda: [{"node": "/dev/video9", "card": "Test IR Cam",
                                      "can_capture": True, "looks_ir": True,
                                      "formats": ["Y16 "]}])
        monkeypatch.setattr(camera, "ir_nodes", lambda: ["/dev/video9"])
        node, refusal = take_photo._ir_node("/dev/video9")
        assert node == "/dev/video9", refusal
        assert refusal == ""

    def test_a_real_infrared_device_is_preferred_over_one_that_cannot_capture(
            self, monkeypatch):
        """A card name is weak, so a node that cannot capture is not a target
        however infrared it looks."""
        import shani_chronoa.camera as camera
        monkeypatch.setattr(camera, "collect", lambda: [
            {"node": "/dev/video8", "card": "Fake IR Cam", "can_capture": False,
             "looks_ir": True, "formats": []},
            {"node": "/dev/video9", "card": "Test IR Cam", "can_capture": True,
             "looks_ir": True, "formats": ["Y16 "]},
        ])
        monkeypatch.setattr(camera, "ir_nodes", lambda: ["/dev/video9"])
        node, _refusal = take_photo._ir_node("")
        assert node == "/dev/video9"

    def test_no_infrared_camera_is_a_refusal_that_names_the_reason(self, monkeypatch):
        self._no_ir(monkeypatch)
        node, refusal = take_photo._ir_node("")
        assert node == ""
        assert "no camera node at all" in refusal
        assert "Nothing was taken" in refusal

    def test_infrared_defaults_to_false(self, monkeypatch):
        """**And the ordinary path is untouched**, because a capture that changes
        band without being asked is the same defect in the other direction."""
        self._consent(monkeypatch)
        monkeypatch.setattr(take_photo, "_camera_present", lambda d: True)
        chosen = {}

        class _Run:
            returncode = 0
            stderr = ""

        def _fake_run(argv, **kw):
            chosen["argv"] = argv
            target = pathlib.Path(argv[-1])
            target.write_bytes(b"x")
            return _Run()

        monkeypatch.setattr(take_photo.subprocess, "run", _fake_run)
        home = Path(os.path.expanduser("~")) / "Pictures"
        home.mkdir(parents=True, exist_ok=True)
        target = home / "ordinary-default.png"
        target.unlink(missing_ok=True)
        out = take_photo._run({"path": str(target)})
        assert "Saved a photo" in out
        assert "-input_format" not in chosen["argv"], (
            "an ordinary capture asked ffmpeg for y16, so the band changed without "
            "being asked for")
        assert "infrared frame" not in out
        target.unlink(missing_ok=True)


class TestInfraredVideo:
    """`capture_video` takes the same `infrared` argument, for the same reasons.

    **Every claim here was measured against the real camera before it was written**
    - a `v4l2loopback` node advertising `Y16`, fed synthetic 16-bit frames: 58,791
    bytes of h264 holding 90 frames, whose decoded content carried **206 distinct
    levels from a 127-level source**. So the smoothing compounds through yuv420p,
    and the reply has to say so.
    """

    @staticmethod
    def _skill():
        from shani_chronoa.skills import capture_video
        return capture_video

    @staticmethod
    def _fake_ir_cameras(monkeypatch, tmp_path):
        """**A real file standing in for the IR node, not an asserted one.**

        `capture_video` resolves the node from this fake and then asks
        `Path(device).exists()` (capture_video.py:69). `/dev/video9` is not on this
        machine, so every call came back *"No camera at /dev/video9. Reading a
        camera needs the device to be present."* - the test failed one line before
        the `-input_format y16` assertion it is named for, and the message pointed
        at the product.

        Mocking `Path.exists` instead is precisely what `_no_op_run`'s docstring
        warns against: it turned `capture_video`'s filename-collision loop into an
        eight-minute spin. A real empty file in `tmp_path` satisfies the existence
        check without changing anything the test is about.
        """
        import shani_chronoa.camera as camera
        node = tmp_path / "video9"
        node.write_bytes(b"")
        node = str(node)
        monkeypatch.setattr(camera, "collect",
                            lambda: [{"node": node, "card": "Test IR Cam",
                                      "can_capture": True, "looks_ir": True,
                                      "formats": ["Y16 "]}])
        monkeypatch.setattr(camera, "ir_nodes", lambda: [node])

    @staticmethod
    def _no_op_run(monkeypatch, skill, tmp_path):
        """A capture that writes a file and records the argv it was given.

        **`Path.exists` is deliberately NOT mocked.** An earlier version stubbed it
        to always-True, which broke `capture_video`'s own filename-collision loop
        (`while target.exists(): ... n += 1`) into an infinite spin - it burned
        eight minutes of CPU before it was killed. The device really exists, so the
        real answer is the right one, and a fixture that changes the thing being
        tested is the failure this repository records under "a mock that was good
        enough".
        """
        class _Config:
            vision_sense_enabled = True

            def sense_allowed_reason(self, name):
                return ""

        monkeypatch.setattr(skill, "ChronoaConfig", _Config)
        monkeypatch.setattr(skill.shutil, "which", lambda n: "/x/ffmpeg")
        monkeypatch.setattr(skill.files, "data_home",
                            lambda: tmp_path / "data")
        chosen = {}

        class _Run:
            returncode, stderr = 0, ""

        def _fake(argv, **kw):
            chosen["argv"] = argv
            pathlib.Path(argv[-1]).write_bytes(b"x" * 64)
            return _Run()

        monkeypatch.setattr(skill.subprocess, "run", _fake)
        return chosen

    def test_an_infrared_node_is_chosen_not_assumed(self, monkeypatch, tmp_path):
        skill = self._skill()
        chosen = self._no_op_run(monkeypatch, skill, tmp_path)
        self._fake_ir_cameras(monkeypatch, tmp_path)
        out = skill._run({"infrared": True, "seconds": 1})
        assert "Saved" in out
        assert chosen["argv"].count("-input_format") == 1, (
            "the infrared capture did not ask ffmpeg for y16, so the demuxer was "
            "left free to hand back an ordinary format")
        assert chosen["argv"][chosen["argv"].index("-input_format") + 1] == "y16"
        assert "infrared" in out

    def test_a_named_non_infrared_device_is_refused(self, monkeypatch, tmp_path):
        skill = self._skill()
        self._no_op_run(monkeypatch, skill, tmp_path)
        self._fake_ir_cameras(monkeypatch, tmp_path)
        out = skill._run({"infrared": True, "device": "/dev/video1", "seconds": 1})
        assert "not an infrared camera" in out
        assert "Test IR Cam" in out
        assert "Nothing was taken" in out

    def test_an_ordinary_recording_is_not_converted(self, monkeypatch, tmp_path):
        """**The control.** A band that changes without being asked for is the same
        defect as substituting a camera without being asked."""
        skill = self._skill()
        chosen = self._no_op_run(monkeypatch, skill, tmp_path)
        out = skill._run({"seconds": 1})
        assert "Saved" in out
        assert "-input_format" not in chosen["argv"], (
            "an ordinary recording asked ffmpeg for y16, so the band changed without "
            "being asked for")
        assert "infrared" not in out

    def test_a_non_bool_infrared_is_not_a_request(self, monkeypatch):
        """A truthy string is not a request. The schema says boolean, and the parse
        has to agree with it - otherwise `"yes"` reaches the probe."""
        import shani_chronoa.camera as camera
        monkeypatch.setattr(camera, "collect",
                            lambda: (_ for _ in ()).throw(
                                AssertionError("the probe ran")))
        from shani_chronoa.skills import take_photo
        out = take_photo._run({"infrared": "yes", "path": "/nonexistent/x.png"})
        assert "probe ran" not in str(out), (
            "a non-bool `infrared` reached the camera probe, so 'yes' would record "
            "video through a camera nobody chose")


class TestTheCaptureSenseDescribesWhatANodeCanDo:
    """`senses/capture.describe()` gained three fields, and they had no test.

    **The broad `except Exception` around the probe is the reason these tests
    exist.** A probe that fails silently is the shape this repository records for
    `gui/surfaces/models.py` - "`except: pass` around a probe is how a panel ends up
    quietly incomplete" - and it is the same code here: a `describe()` that cannot
    probe would still return device, holders and in_use, so a reader could not tell
    "the camera is free" from "this node could not be asked at all".
    """

    @staticmethod
    def _node():
        from shani_chronoa.senses import capture
        return capture

    def test_the_probe_fields_are_added(self, monkeypatch):
        capture = self._node()
        monkeypatch.setattr(capture, "_holders", lambda n, timeout=2.0: [42])
        monkeypatch.setattr(capture.subproc, "cmdline", lambda pid: "/bin/ffmpeg")
        monkeypatch.setattr("shani_chronoa.camera.probe", lambda node: {
            "node": "/dev/video9", "card": "Test IR Cam", "can_capture": True,
            "device_caps": 0x04200001, "formats": ["Y16 "], "formats_error": None,
            "looks_ir": True})
        out = capture.describe(pathlib.Path("/dev/video9"))
        assert out["device"] == "/dev/video9"
        assert out["can_capture"] is True
        assert out["formats"] == ["Y16 "]
        assert out["infrared"] is True
        assert out["card"] == "Test IR Cam"
        assert out["in_use"] is True, "the holders half is unaffected by the probe"

    def test_a_node_that_cannot_capture_is_not_described_as_a_camera(self, monkeypatch):
        """**The load-bearing one.** A metadata node answers `QUERYCAP` with a
        sensible name, so `card` alone would describe it as a working camera."""
        capture = self._node()
        monkeypatch.setattr(capture, "_holders", lambda n, timeout=2.0: None)
        monkeypatch.setattr("shani_chronoa.camera.probe", lambda node: {
            "node": "/dev/video1", "card": "Integrated Camera", "can_capture": False,
            "device_caps": 0x04a00000, "formats": [], "formats_error": "ENUM_FMT refused",
            "looks_ir": False})
        out = capture.describe(pathlib.Path("/dev/video1"))
        assert out["can_capture"] is False
        assert out["infrared"] is False

    def test_a_probe_that_raises_never_takes_the_percept_down(self, monkeypatch):
        """**And the broad `except` is bounded rather than trusted.** The node's
        existence and its holders are still facts when the probe fails, so the
        answer is thinner rather than absent - and thinner is the honest shape."""
        capture = self._node()
        monkeypatch.setattr(capture, "_holders", lambda n, timeout=2.0: [42])
        monkeypatch.setattr(capture.subproc, "cmdline", lambda pid: "/bin/ffmpeg")

        def _boom(node):
            raise OSError("the probe is not available")

        monkeypatch.setattr("shani_chronoa.camera.probe", _boom)
        out = capture.describe(pathlib.Path("/dev/video9"))
        assert out["device"] == "/dev/video9"
        assert out["in_use"] is True
        assert "can_capture" not in out, (
            "the probe raised and describe() still reported a capability, so a node "
            "that could not be asked reads as one that can")
        assert "formats" not in out
        assert "infrared" not in out

    def test_a_probe_that_cannot_open_the_node_says_so(self, monkeypatch):
        """**`error` is not silently dropped either**, because "not a V4L2 device"
        and "an infrared camera" are different facts."""
        capture = self._node()
        monkeypatch.setattr(capture, "_holders", lambda n, timeout=2.0: [])
        monkeypatch.setattr("shani_chronoa.camera.probe", lambda node: {
            "node": "/dev/video9", "error": "QUERYCAP refused - not a V4L2 device"})
        out = capture.describe(pathlib.Path("/dev/video9"))
        assert out["in_use"] is False
        assert "can_capture" not in out, (
            "a node whose QUERYCAP was refused reported a capture capability")

    def test_a_card_name_that_merely_contains_the_letters_is_not_infrared(
            self, monkeypatch):
        """**The weak test, bounded at the sense layer too.**

        `camera.is_ir()` accepts a card name containing "ir" because it is the only
        thing that catches a device whose driver never reports an IR format - and
        "First Camera" contains "ir". That is documented in `is_ir()` as a false
        positive in the *description*, and this is the assertion that keeps it a
        false positive in the description *only*: a card named that is reported as
        not infrared, so nobody acts on the band and asks a camera for `y16`.

        It is also the fixture that makes the over-fix visible. Replacing
        `looks_ir` with `"ir" in card.lower()` at the call site passes every other
        test here - because the other fixtures' card names happen not to contain
        "ir" - and fails this one. A control that agrees with the code on its own
        fixture is not a control, which is the same lesson as the `[:1]` slice.
        """
        capture = self._node()
        monkeypatch.setattr(capture, "_holders", lambda n, timeout=2.0: None)
        monkeypatch.setattr("shani_chronoa.camera.probe", lambda node: {
            "node": "/dev/video1", "card": "First Camera", "can_capture": False,
            "device_caps": 0x04a00000, "formats": [], "formats_error": None,
            "looks_ir": False})
        out = capture.describe(pathlib.Path("/dev/video1"))
        assert out["infrared"] is False, (
            "a card named 'First Camera' was reported as infrared, so the card-name "
            "substring test is being read as evidence rather than as the weak "
            "fallback it is documented to be")
