"""A meeting app on the microphone is offered notes; Chronoa's own capture never is."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import mic_watch  # noqa: E402


def test_meeting_apps_are_found_and_chronoa_is_not():
    streams = {("Firefox", "firefox"), ("ZOOM VoiceEngine", "zoom"),
               ("shani-chronoa", "python3"), ("pw-record", "pw-record"), ("", "whisper-cli"),
               ("GNOME Sound Recorder", "gnome-sound-recorder")}
    assert mic_watch.meetings_among(streams) == {"Firefox", "ZOOM VoiceEngine"}


def test_it_is_offered_once_per_app_and_only_with_the_switch_on(monkeypatch):
    offered = []
    monkeypatch.setattr(mic_watch, "_offer", offered.append)
    monkeypatch.setattr(mic_watch, "capturing", lambda: {("Firefox", "firefox")})

    class App:
        def __init__(self, on):
            self.config = type("C", (), {"get_bool": lambda s, k, d=False: on})()
    mic_watch._STATE.update(app=App(False), seen=set())
    mic_watch._tick()
    assert offered == [], "offered with meeting-enabled off"
    mic_watch._STATE["app"] = App(True)
    mic_watch._tick()
    mic_watch._tick()
    assert offered == ["Firefox"], offered
