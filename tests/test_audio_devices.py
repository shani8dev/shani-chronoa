"""Audio device selection over PipeWire, and what happens when it goes stale.

The behaviour worth protecting here is not "the target is passed through" -
that is one line - but the stale case. `pw-record --target` does not validate
its argument: measured on a live PipeWire, a target naming a device that does
not exist printed nothing, exited 0, and recorded from the *default* device
(187,720 bytes against 189,766 bytes for no target at all). A saved device
name that has gone stale - a headset unplugged since last run - would
therefore look honoured while quietly recording somewhere else, which is worse
than having no device setting at all.

Tests that need a live graph skip rather than fake it, because the whole point
is what the running daemon reports.
"""

import os
import shutil
import sys

import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")
from shani_chronoa import pipewire  # noqa: E402

needs_graph = pytest.mark.skipif(
    not (shutil.which("pw-dump") and pipewire.is_available()),
    reason="needs a running PipeWire graph",
)


class TestTargetResolution:
    def test_unconfigured_means_the_default_and_is_not_a_problem(self):
        assert pipewire.resolve_target("", "input") == ("", None)
        assert pipewire.resolve_target("   ", "input") == ("", None)
        assert pipewire.resolve_target(None, "input") == ("", None)

    @needs_graph
    def test_a_live_device_is_kept(self):
        device = pipewire.list_inputs()[0].name
        assert pipewire.resolve_target(device, "input") == (device, None)

    @needs_graph
    def test_a_stale_device_is_rejected_and_explained(self):
        target, problem = pipewire.resolve_target("alsa_input.GONE.__source", "input")
        assert target == "", "an unknown device must not be passed to pw-record"
        assert problem and "not in the running" in problem

    @needs_graph
    def test_an_output_device_is_not_accepted_as_an_input(self):
        output = pipewire.list_outputs()[0].name
        assert pipewire.resolve_target(output, "input")[0] == ""


class TestDiscoveryDegradesQuietly:
    def test_no_pipewire_is_not_an_error(self, monkeypatch):
        """Chronoa must still start on a PulseAudio-only or ALSA-only machine."""
        monkeypatch.setattr(pipewire.shutil, "which", lambda name: None)
        assert pipewire.is_available() is False
        assert pipewire.list_inputs() == []
        assert pipewire.list_outputs() == []
        assert pipewire.echo_cancel_active() is False
        assert pipewire.resolve_target("", "input") == ("", None)

    def test_unparseable_graph_output_is_not_an_error(self, monkeypatch):
        class _Result:
            stdout = "not json at all"

        monkeypatch.setattr(pipewire.shutil, "which", lambda name: "/usr/bin/pw-dump")
        monkeypatch.setattr(pipewire.subprocess, "run", lambda *a, **k: _Result())
        assert pipewire.is_available() is False
        assert pipewire.list_inputs() == []

    @needs_graph
    def test_virtual_nodes_are_not_offered(self):
        """Dummy/Freewheel drivers are real nodes but not things to pick."""
        for device in pipewire.list_inputs() + pipewire.list_outputs():
            assert "Dummy" not in device.name
            assert "Freewheel" not in device.name

    @needs_graph
    def test_devices_carry_a_usable_label(self):
        """`node.description` leads with the controller name and is unreadable."""
        for device in pipewire.list_inputs() + pipewire.list_outputs():
            assert device.label
            assert device.label == device.label.strip()


def _pin_pipewire_backends(monkeypatch):
    """Pretend PipeWire is installed, so these tests are about the argv.

    The neighbouring tests in this class call _stream_capture_cmd("pw-record", ...)
    and pass the backend in explicitly, which is why they always passed. These
    three instead construct AudioPlayer/WakeWordListener, whose _detect_backend
    really does shutil.which("pw-play") / ("pw-record"). On a dev machine with
    PipeWire that resolved to pw-play and the --target assertions held; on a
    runner with neither binary it resolved to None, _playback_cmd returned
    [None, path], and the tests failed without ever reaching the behaviour they
    are named for. Pin the detection, not the assertion.
    """
    from shani_chronoa.audio import AudioPlayer
    from shani_chronoa.wakeword import WakeWordListener

    monkeypatch.setattr(AudioPlayer, "_detect_backend", lambda self: "pw-play")
    monkeypatch.setattr(WakeWordListener, "_detect_backend", lambda self: "pw-record")


class TestTargetReachesTheSubprocess:
    def test_capture_uses_target_on_pipewire(self):
        from shani_chronoa.audio import _stream_capture_cmd

        cmd = _stream_capture_cmd("pw-record", "alsa_input.foo.__source")
        assert "--target" in cmd
        assert cmd[cmd.index("--target") + 1] == "alsa_input.foo.__source"
        assert cmd[-1] == "-", "the stdout argument must stay last"

    def test_capture_omits_target_when_unset(self):
        from shani_chronoa.audio import _stream_capture_cmd

        assert "--target" not in _stream_capture_cmd("pw-record")
        assert "--target" not in _stream_capture_cmd("pw-record", "")

    def test_alsa_capture_ignores_a_pipewire_name_rather_than_guessing(self):
        """A PipeWire node name is not an ALSA device; passing it would break."""
        from shani_chronoa.audio import _stream_capture_cmd

        cmd = _stream_capture_cmd("arecord", "alsa_input.foo.__source")
        assert "--target" not in cmd
        assert cmd[0] == "arecord"

    def test_playback_uses_target_on_pipewire(self, monkeypatch):
        _pin_pipewire_backends(monkeypatch)
        from shani_chronoa.audio import AudioPlayer

        player = AudioPlayer(target="alsa_output.bar._sink")
        cmd = player._playback_cmd("/tmp/x.wav")
        assert cmd[:3] == ["pw-play", "--target", "alsa_output.bar._sink"]
        assert cmd[-1] == "/tmp/x.wav"

    def test_playback_target_can_be_cleared(self, monkeypatch):
        _pin_pipewire_backends(monkeypatch)
        from shani_chronoa.audio import AudioPlayer

        player = AudioPlayer(target="alsa_output.bar._sink")
        player.set_target(None)
        assert player._playback_cmd("/tmp/x.wav") == ["pw-play", "/tmp/x.wav"]

    def test_the_wake_word_honours_the_same_device_as_the_recorder(self, monkeypatch):
        """A wake word listening on a different mic looks like flaky recognition."""
        _pin_pipewire_backends(monkeypatch)
        from shani_chronoa.wakeword import WakeWordListener

        listener = WakeWordListener(target="alsa_input.foo.__source")
        cmd = listener._record_cmd()
        assert "--target" in cmd
        assert cmd[cmd.index("--target") + 1] == "alsa_input.foo.__source"


class TestEveryCapturePathGetsTheSameDevice:
    def test_recorder_barge_in_and_wake_word_agree(self, gsettings_env, tmp_path, monkeypatch):
        import shani_chronoa.senses.store as store_mod

        from shani_chronoa.app import ChronoaApplication

        monkeypatch.setattr(store_mod, "DURABLE_FILE", tmp_path / "p" / "memory.jsonl")
        if not (shutil.which("pw-dump") and pipewire.is_available()):
            pytest.skip("needs a running PipeWire graph to name a real device")

        device = pipewire.list_inputs()[0].name
        seed = ChronoaApplication()
        seed.config.set_audio_devices(device, "")

        app = ChronoaApplication()
        assert app.recorder._target == device
        assert app.barge_in_monitor._target == device
        assert app.wakeword._target == device
        assert app._device_warnings == []

    def test_a_stale_device_is_not_threaded_through_and_is_reported(
        self, gsettings_env, tmp_path, monkeypatch
    ):
        import shani_chronoa.senses.store as store_mod

        from shani_chronoa.app import ChronoaApplication

        monkeypatch.setattr(store_mod, "DURABLE_FILE", tmp_path / "p" / "memory.jsonl")
        seed = ChronoaApplication()
        seed.config.set_audio_devices("alsa_input.UNPLUGGED.__source", "")

        app = ChronoaApplication()
        assert app.recorder._target is None
        assert app.wakeword._target is None
        assert app._device_warnings, "a rejected device must be reported, not swallowed"
        assert "not in the running" in app._device_warnings[0]

    def test_default_devices_produce_no_warning(self, gsettings_env, tmp_path, monkeypatch):
        import shani_chronoa.senses.store as store_mod

        from shani_chronoa.app import ChronoaApplication

        monkeypatch.setattr(store_mod, "DURABLE_FILE", tmp_path / "p" / "memory.jsonl")
        ChronoaApplication().config.set_audio_devices("", "")
        app = ChronoaApplication()
        assert app.recorder._target is None
        assert app._device_warnings == []


class TestDevicesCliCommand:
    def test_the_command_the_schema_refers_to_actually_exists(self):
        """The gschema text tells users to run `shani-chronoa-sense devices`."""
        from sense_manifest_support import SENSE_CLI

        assert os.path.exists(SENSE_CLI)
        out = os.popen(
            f'PYTHONPATH=usr/lib/shani-chronoa python3 "{SENSE_CLI}" --help 2>&1'
        ).read()
        assert "devices" in out

    @needs_graph
    def test_it_prints_devices_and_says_which_is_chosen(self, tmp_path):
        import subprocess

        env = dict(os.environ, PYTHONPATH="usr/lib/shani-chronoa")
        result = subprocess.run(
            [sys.executable, "usr/bin/shani-chronoa-sense", "devices"],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert "inputs" in result.stdout
        assert "outputs" in result.stdout


# --- application streams (measured: real, and capturable) ----------------------
#
# `_devices()` matches `Audio/Source` and `Audio/Sink` **exactly**, so an app's
# `Stream/Output/Audio` node was never in any list - the graph could show a
# running application's audio and the driver still had nothing to point at.
# Measured on this box: `pw-record --target=<stream>` took -27.4 dBFS off a live
# `pw-play`, where targeting the *sink* returned 11.9s of silence.

_PLAYBACK = {"media.class": "Stream/Output/Audio", "node.name": "pw-play",
             "media.name": "/tmp/tone.wav", "media.type": "Audio"}
_CAPTURE = {"media.class": "Stream/Input/Audio", "node.name": "chromium",
            "media.name": "Audio input", "media.type": "Audio"}


def _graph(*nodes):
    return lambda: list(nodes)


def test_a_playing_application_is_listed(monkeypatch):
    from shani_chronoa import pipewire

    monkeypatch.setattr(pipewire, "_graph_nodes", _graph(_PLAYBACK))

    streams = pipewire.list_streams()

    assert len(streams) == 1
    assert streams[0].name == "pw-play", (
        "name must be node.name, because that is what `pw-record --target` takes")
    assert streams[0].recordable is True


def test_a_microphone_belonging_to_an_app_is_not_something_to_record(monkeypatch):
    """`Stream/Input` is the app consuming audio - its own microphone - and is
    already what `list_inputs()` is for. Offering it as a capture target would be
    the same dead control the repo keeps refusing."""
    from shani_chronoa import pipewire

    monkeypatch.setattr(pipewire, "_graph_nodes", _graph(_CAPTURE))

    streams = pipewire.list_streams()

    assert len(streams) == 1, "it is still a real stream, and hiding it would be a lie"
    assert streams[0].recordable is False, (
        "a capture stream is audio going INTO the app; there is nothing to take")


def test_the_label_says_what_is_playing_not_which_binary(monkeypatch):
    """A browser's `node.name` is the binary. `media.name` is the only field
    that says what - here, the file being played."""
    from shani_chronoa import pipewire

    monkeypatch.setattr(pipewire, "_graph_nodes", _graph(_PLAYBACK))
    assert pipewire.list_streams()[0].label == "/tmp/tone.wav"

    bare = dict(_PLAYBACK)
    bare.pop("media.name")
    monkeypatch.setattr(pipewire, "_graph_nodes", _graph(bare))
    assert pipewire.list_streams()[0].label == "pw-play", (
        "with nothing else set, the node name is the honest fallback - "
        "`pw-play` had no nick and no description either")


def test_a_stream_with_no_node_name_is_skipped_not_listed_blank(monkeypatch):
    """`--target` takes a name or a serial. A stream with neither cannot be
    pointed at, so listing it would offer a control that cannot work."""
    from shani_chronoa import pipewire

    monkeypatch.setattr(pipewire, "_graph_nodes",
                        _graph({"media.class": "Stream/Output/Audio"}))

    assert pipewire.list_streams() == []


def test_devices_are_not_mistaken_for_streams(monkeypatch):
    """The control: the two lists must stay apart, or `list_streams` is
    `list_outputs` wearing a different name."""
    from shani_chronoa import pipewire

    sink = {"media.class": "Audio/Sink", "node.name": "alsa_output.pci",
            "node.description": "Speaker"}
    source = {"media.class": "Audio/Source", "node.name": "alsa_input.pci",
              "node.description": "Mic"}
    monkeypatch.setattr(pipewire, "_graph_nodes", _graph(sink, source, _PLAYBACK))

    assert [d.name for d in pipewire.list_outputs()] == ["alsa_output.pci"]
    assert [d.name for d in pipewire.list_inputs()] == ["alsa_input.pci"]
    assert [s.name for s in pipewire.list_streams()] == ["pw-play"]


def test_a_quiet_machine_is_empty_and_not_a_failure(monkeypatch):
    """Empty is the honest answer: nothing is playing. A caller that turns this
    into an error would be reporting a broken machine where there is a quiet one."""
    from shani_chronoa import pipewire

    monkeypatch.setattr(pipewire, "_graph_nodes", _graph())
    assert pipewire.list_streams() == []

    monkeypatch.setattr(pipewire, "_graph_nodes", _graph({"nonsense": True}))
    assert pipewire.list_streams() == []


def test_recordable_streams_come_first(monkeypatch):
    """A person reading the list wants the capturable one, not the app's mic."""
    from shani_chronoa import pipewire

    monkeypatch.setattr(pipewire, "_graph_nodes", _graph(_CAPTURE, _PLAYBACK))

    assert [s.recordable for s in pipewire.list_streams()] == [True, False]
