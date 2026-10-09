"""take_photo against a fake ffmpeg and a fake notify-send - never the real camera.

PATH holds only the fake bin directory, so the real ffmpeg (which would open
/dev/video0) cannot be reached; the camera's presence is faked at the one
function that asks. The consent gate is the real one: the repo's gschema,
the keyfile backend, `vision-sense-enabled` written for the test.
"""

import shutil
import stat
from pathlib import Path

import pytest

from shani_chronoa.skills import take_photo as tp


@pytest.fixture
def cam(gsettings_env, tmp_path, monkeypatch):
    from shani_chronoa.config import ChronoaConfig
    ChronoaConfig().set("vision-sense-enabled", "true")
    if not ChronoaConfig().vision_sense_enabled:
        pytest.skip("vision consent could not be granted in this environment")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    monkeypatch.setenv("PATH", str(bindir))
    log = tmp_path / "argv.log"

    def make(name, body):
        script = bindir / name
        script.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\n{body}\n')
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        assert shutil.which(name) == str(script)

    # the fake ffmpeg writes a small "photo" to its last argument, like the real one
    make("ffmpeg", 'for last; do :; done; printf "JPEGDATA" > "$last"')
    make("notify-send", "exit 0")
    slept = []
    monkeypatch.setattr(tp, "_wait", slept.append)
    monkeypatch.setattr(tp, "_camera_present", lambda device: device == "/dev/video0")
    lines = lambda: log.read_text().splitlines() if log.exists() else []  # noqa: E731
    return {"make": make, "lines": lines, "slept": slept, "home": Path.home()}


def _pictures(home):
    return home / "Pictures" / "Chronoa"


def test_takes_one_frame_into_pictures_with_a_timestamped_name(cam):
    out = tp._run({})
    assert out.startswith("Saved a photo to ") and out.endswith("(8 B).")
    saved = list(_pictures(cam["home"]).glob("photo-*.jpg"))
    assert len(saved) == 1 and saved[0].read_bytes() == b"JPEGDATA"
    assert str(saved[0]) in out
    [ffmpeg] = [line for line in cam["lines"]() if line.startswith("ffmpeg")]
    assert "-f v4l2 -i /dev/video0" in ffmpeg and "-frames:v 1" in ffmpeg and " -n " in ffmpeg
    assert cam["slept"] == [] and not any(line.startswith("notify-send") for line in cam["lines"]())


def test_countdown_is_announced_then_waited(cam):
    out = tp._run({"countdown": 5})
    assert out.startswith("Saved a photo to ")
    assert cam["slept"] == [5]
    notes = [line for line in cam["lines"]() if line.startswith("notify-send")]
    assert len(notes) == 1 and "Taking a photo in 5 seconds" in notes[0]
    # announced before the shutter, not after
    assert cam["lines"]()[0].startswith("notify-send") and cam["lines"]()[1].startswith("ffmpeg")


def test_countdown_with_notifications_off_says_so(cam):
    from shani_chronoa.config import ChronoaConfig
    ChronoaConfig().set("notification-enabled", "false")
    out = tp._run({"countdown": "3"})
    assert out.startswith("Saved a photo") and "notifications are turned off" in out
    assert cam["slept"] == [3]
    assert not any(line.startswith("notify-send") for line in cam["lines"]())


def test_second_photo_in_the_same_second_does_not_overwrite(cam):
    tp._run({})
    tp._run({})
    assert len(list(_pictures(cam["home"]).glob("photo-*.jpg"))) == 2


def test_explicit_path_and_post_condition(cam):
    target = cam["home"] / "selfie.png"
    assert tp._post_condition({"path": "~/selfie.png"}) == (False, f"{target} does not exist or is empty")
    out = tp._run({"path": "~/selfie.png"})
    assert out.startswith(f"Saved a photo to {target}")
    ok, evidence = tp._post_condition({"path": "~/selfie.png"})
    assert ok is True and str(target) in evidence
    assert "already exists; not overwriting" in tp._run({"path": "~/selfie.png"})
    assert target.read_bytes() == b"JPEGDATA"
    assert tp._post_condition({}) is None, "a timestamped name cannot be checked from the arguments"


def test_folder_path(cam):
    (cam["home"] / "shots").mkdir()
    assert tp._run({"path": "~/shots"}).startswith(f"Saved a photo to {cam['home'] / 'shots'}/photo-")


def test_consent_off_refuses_before_anything(cam, monkeypatch):
    """Control: the same call with the camera consent off runs nothing."""
    from shani_chronoa.config import ChronoaConfig
    ChronoaConfig().set("vision-sense-enabled", "false")
    out = tp._run({"countdown": 5})
    assert out.startswith("Taking a photo is not permitted") and "vision-sense-enabled" in out
    assert cam["lines"]() == [] and cam["slept"] == []
    assert not _pictures(cam["home"]).exists()


def test_failures_are_reported_before_the_countdown(cam, monkeypatch):
    assert "No camera at /dev/video3" in tp._run({"device": "/dev/video3", "countdown": 10})
    assert "outside your home directory" in tp._run({"path": "/etc/x.jpg", "countdown": 10})
    assert ".jpg or .png" in tp._run({"path": "~/x.txt", "countdown": 10})
    assert cam["slept"] == [] and cam["lines"]() == []


def test_a_failing_ffmpeg_is_not_a_photo(cam):
    cam["make"]("ffmpeg", 'echo "/dev/video0: Device or resource busy" >&2; exit 1')
    out = tp._run({})
    assert "could not take a photo" in out and "Device or resource busy" in out
    assert not list(_pictures(cam["home"]).glob("*.jpg"))


def test_an_ffmpeg_that_writes_nothing_is_not_a_photo(cam):
    """Control: exit 0 with no file must not be reported as saved."""
    cam["make"]("ffmpeg", "exit 0")
    out = tp._run({})
    assert not out.startswith("Saved") and "could not take a photo" in out


def test_missing_ffmpeg(cam):
    (cam["home"].parent / "bin" / "ffmpeg").unlink()
    out = tp._run({"countdown": 4})
    assert "ffmpeg" in out and not out.startswith("Saved") and cam["slept"] == []


@pytest.mark.parametrize("args,needle", [
    ({"countdown": 31}, "between 0 and 30"),
    ({"countdown": -1}, "between 0 and 30"),
    ({"countdown": "soon"}, "Could not use 'soon'"),
    ({"countdown": True}, "Could not use True"),
    ({"device": "/etc/passwd"}, "is not a camera"),
    ({"device": 7}, "is not a camera"),
    ({"path": 5}, "path must be"),
])
def test_malformed_arguments(cam, args, needle):
    assert needle in tp._run(args)
    assert cam["lines"]() == []


def test_not_a_dict():
    assert tp._run(None).startswith("take_photo takes")  # type: ignore[arg-type]
    assert [s.name for s in tp.SKILLS] == ["take_photo"]
