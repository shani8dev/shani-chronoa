"""Every path that opens a camera, scanner or microphone lights the organ strip.

`screengrab` lit "looking" and `AudioRecorder` lit "listening", and three paths
that hold a device themselves did neither: `capture_video` drives ffmpeg on
/dev/video directly, `scan_document` runs scanimage, and the wake word keeps
its own microphone stream open for as long as it is on. So the camera could be
recording, or the microphone open all day, with the strip reading idle.

Each test fakes only the device: the real skill or loop runs, and the assertion
is read from the body register *during* the capture, then again after it.
"""

import pathlib
import subprocess

import pytest


@pytest.fixture(autouse=True)
def _clear_body():
    from shani_chronoa import body
    body.body.done_all("eyes")
    body.body.done_all("ears")
    yield
    body.body.done_all("eyes")
    body.body.done_all("ears")


def test_lit_puts_the_light_out_even_when_the_block_raises():
    from shani_chronoa import body
    with pytest.raises(RuntimeError):
        with body.lit("eyes", "looking at something"):
            assert body.body.busy("eyes")
            raise RuntimeError("the capture failed")
    assert not body.body.busy("eyes")


def test_recording_the_camera_lights_looking(gsettings_env, tmp_path, monkeypatch):
    from shani_chronoa import body
    from shani_chronoa.config import ChronoaConfig
    from shani_chronoa.skills import capture_video
    ChronoaConfig().set("vision-sense-enabled", "true")
    if not ChronoaConfig().vision_sense_enabled:
        pytest.skip("vision consent could not be granted in this environment")

    real_path = capture_video.Path

    class _Camera(type(pathlib.Path())):
        def exists(self):
            return True if str(self).startswith("/dev/video") else super().exists()

    monkeypatch.setattr(capture_video, "Path", lambda p: _Camera(p) if str(p).startswith("/dev/") else real_path(p))
    monkeypatch.setattr(capture_video.shutil, "which", lambda name: f"/usr/bin/{name}")
    seen = {}

    def fake_ffmpeg(argv, **_kw):
        seen["eyes"] = body.body.busy("eyes")
        seen["describe"] = body.body.describe("eyes")
        pathlib.Path(argv[-1]).write_bytes(b"\0" * 64)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(capture_video.subprocess, "run", fake_ffmpeg)
    out = capture_video._run({"device": "/dev/video0", "seconds": 1})
    assert "Saved" in out, out
    assert seen["eyes"], "the camera recorded while 'looking' was idle"
    assert "camera" in seen["describe"], seen["describe"]
    assert not body.body.busy("eyes"), "the light stayed on after the recording"


def test_scanning_a_page_lights_looking(tmp_path, monkeypatch):
    from shani_chronoa import body
    from shani_chronoa.skills import scan_document
    monkeypatch.setattr(scan_document.shutil, "which",
                        lambda name: "/usr/bin/scanimage" if name == "scanimage" else None)
    monkeypatch.setattr(scan_document, "scanners", lambda: [("airscan:e0:Printer", "Printer")])
    seen = {}

    def fake_scanimage(argv, **_kw):
        seen["eyes"] = body.body.busy("eyes")
        pathlib.Path(argv[argv.index("-o") + 1]).write_bytes(b"\x89PNG fake")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(scan_document.subprocess, "run", fake_scanimage)
    scan_document._scanner({})
    assert seen.get("eyes"), "the scanner ran while 'looking' was idle"
    assert not body.body.busy("eyes")


def test_the_wake_word_lights_listening_while_its_stream_is_open():
    """A real subprocess standing in for pw-record: two seconds of silence."""
    from shani_chronoa import body, wakeword
    word = wakeword.WakeWordListener.__new__(wakeword.WakeWordListener) \
        if hasattr(wakeword, "WakeWordListener") else None
    if word is None:
        cls = next(c for c in vars(wakeword).values()
                   if isinstance(c, type) and hasattr(c, "_listen_lit"))
        word = cls.__new__(cls)
    seen = {}
    real_loop = type(word)._listen_loop

    def loop(self, proc, on_detected, generation=0):
        seen["ears"] = body.body.busy("ears")
        seen["describe"] = body.body.describe("ears")
        proc.stdout.read()

    word._backend = "pw-record"
    proc = subprocess.Popen(["head", "-c", "64000", "/dev/zero"], stdout=subprocess.PIPE)
    try:
        type(word)._listen_loop = loop
        word._listen_lit(proc, lambda: None, 0)
    finally:
        type(word)._listen_loop = real_loop
        proc.wait(timeout=5)
    assert seen["ears"], "the wake word's microphone was open while 'listening' was idle"
    assert "wake phrase" in seen["describe"], seen["describe"]
    assert not body.body.busy("ears"), "listening stayed lit after the stream closed"
