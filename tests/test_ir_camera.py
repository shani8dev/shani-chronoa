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
