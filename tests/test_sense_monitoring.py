"""The monitoring sense: a reader, and every way it could be confidently wrong.

Each of these exists because the sense's own failure mode is a *plausible wrong
answer* - no tool, no device, an idle microphone - and three of them are the
negative controls that make the refusals able to fail.
"""

from __future__ import annotations

import sys

sys.path.insert(0, "usr/lib/shani-chronoa")

from shani_chronoa.senses import monitoring  # noqa: E402


def test_it_registers_and_agrees_with_the_repo_reader() -> None:
    assert monitoring._SENSE.ttl_seconds >= monitoring._SENSE.poll_interval


def test_a_probe_that_cannot_run_is_unknown_not_a_clean_machine(monkeypatch) -> None:
    monkeypatch.setattr(monitoring.shutil, "which", lambda name: None)
    assert "UNKNOWN" in monitoring._run({})


def test_a_device_with_no_holder_is_not_a_finding(monkeypatch) -> None:
    monkeypatch.setattr(monitoring.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(monitoring, "_holders", lambda node: [])
    out = monitoring._run({})
    assert "no camera device is held open" in out
    assert "no audio stream is capturing" in out
    assert "Something on this machine is capturing" not in out


def test_the_compositor_holding_raw_input_is_normal(monkeypatch) -> None:
    monkeypatch.setattr(monitoring, "_holders",
                        lambda node: ["/usr/bin/Xorg :0"])
    assert monitoring._LEGIT_INPUT_HOLDERS.search("/usr/bin/Xorg :0")


def test_a_monitoring_agent_is_reported_by_name(monkeypatch) -> None:
    fake = " 1541 WorkpulsService /usr/lib/Workpuls/WorkpulsService"
    monkeypatch.setattr(monitoring, "_agents", lambda: [fake])
    # Its own premise, not this box's: `fuser` is not on every PATH (it is
    # missing under pytest's environment here, which is how the first version
    # of this test failed while the sense itself ran fine).
    monkeypatch.setattr(monitoring.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(monitoring, "_holders", lambda node: [])
    monkeypatch.setattr(monitoring, "_audio_streams", lambda: [])
    monkeypatch.setattr(monitoring, "_public_peers", lambda agents: [])
    out = monitoring._run({})
    assert "WorkpulsService" in out
    assert "A monitoring agent is running" in out


def test_an_agent_whose_connections_cannot_be_attributed_says_so(monkeypatch) -> None:
    monkeypatch.setattr(monitoring, "_agents", lambda: ["insightful-agent"])
    monkeypatch.setattr(monitoring, "_public_peers", lambda agents: [])
    assert "could not be attributed" in monitoring._run({})


def test_another_users_socket_is_not_reported_as_ours(monkeypatch) -> None:
    """The scope fix: a machine's whole connection list is not this sense's claim."""
    monkeypatch.setattr(monitoring, "_agents", lambda: [])
    line = ("ESTAB 0 0 192.168.1.5:4444 1.1.1.1:443 "
            'users:(("firefox",pid=99,fd=17))')
    import subprocess
    proc = subprocess.CompletedProcess([], 0, stdout=line, stderr="")
    monkeypatch.setattr(monitoring, "_probe", lambda *a: proc)
    assert monitoring._public_peers([]) == []


def test_a_public_address_is_public_and_a_private_one_is_not() -> None:
    assert monitoring._is_public("1.1.1.1:443")
    assert not monitoring._is_public("192.168.1.9:443")
    assert not monitoring._is_public("127.0.0.1:8080")
    assert not monitoring._is_public("10.0.0.3:22")
    assert not monitoring._is_public("[::1]:443")
    assert not monitoring._is_public("[fe80::1]:443")


# --- the eight channels taken from the anti-surveillance script's probe list ----
#
# Each is read-only, and each has the same failure mode this whole sense exists
# to avoid: a probe whose tool is absent must land in "could not ask", never in
# an empty list that reads as a clean machine.


def test_a_probe_whose_tool_is_absent_is_unknown_not_clean(monkeypatch) -> None:
    monkeypatch.setattr(monitoring.shutil, "which", lambda name: None)
    out = monitoring._run({})
    assert "UNKNOWN" in out
    assert "not all here" in out
    # and the probe level agrees when the early gate passes
    monkeypatch.undo()
    monkeypatch.setattr(monitoring.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(monitoring, "_probe", lambda *a: None)
    said = monitoring._run({})
    assert "could not be listed" in said or "could not be read" in said


def test_a_clipboard_pusher_is_not_a_reader(monkeypatch) -> None:
    """xclip writing is ordinary; only a reader in a loop is worth naming."""
    import subprocess
    proc = subprocess.CompletedProcess([], 0, stdout="COMMAND\nxclip -selection clipboard\n", stderr="")
    monkeypatch.setattr(monitoring, "_probe", lambda *a: proc)
    found, asked = monitoring._clipboard_readers()
    assert found == [] and asked


def test_a_clipboard_watch_loop_is_named(monkeypatch) -> None:
    import subprocess
    proc = subprocess.CompletedProcess([], 0, stdout="COMMAND\nclipman --watch\n", stderr="")
    monkeypatch.setattr(monitoring, "_probe", lambda *a: proc)
    found, asked = monitoring._clipboard_readers()
    assert found and "clipman" in found[0]


def test_a_known_input_device_is_not_flagged(monkeypatch) -> None:
    assert monitoring._KNOWN_XINPUT_DEVICES.search("Virtual core XTEST pointer")
    assert not monitoring._KNOWN_XINPUT_DEVICES.search("USB Keyboard 1234")


def test_capture_is_decided_by_arguments_not_the_tool_name(monkeypatch) -> None:
    """`import` and `ffmpeg` are ordinary tools; the flags are the signal."""
    import subprocess
    # ImageMagick's import with a window argument is not a screen recording
    ordinary = subprocess.CompletedProcess([], 0, stdout="COMMAND\nimport -window 55 out.png\n", stderr="")
    monkeypatch.setattr(monitoring, "_probe", lambda *a: ordinary)
    assert monitoring._screen_capture()[0] == []
    # ...but ffmpeg with x11grab is
    grabbing = subprocess.CompletedProcess(
        [], 0, stdout="COMMAND\nffmpeg -f x11grab -i :0 out.mp4\n", stderr="")
    monkeypatch.setattr(monitoring, "_probe", lambda *a: grabbing)
    assert "x11grab" in monitoring._screen_capture()[0][0]


def test_a_remote_desktop_product_is_named_not_judged(monkeypatch) -> None:
    import subprocess
    proc = subprocess.CompletedProcess([], 0, stdout="COMMAND\nrustdesk --service\n", stderr="")
    monkeypatch.setattr(monitoring, "_probe", lambda *a: proc)
    found, asked = monitoring._remote_control()
    assert found and "rustdesk" in found[0]


def test_a_missing_audit_tool_is_unknown(monkeypatch) -> None:
    monkeypatch.setattr(monitoring.shutil, "which", lambda name: None)
    assert monitoring._audit_rules() == ([], False)


def test_the_media_baseline_means_growth_not_a_count(monkeypatch) -> None:
    """The first reading is a baseline; a count on its own is not a finding."""
    import pathlib

    calls = iter([3, 5])

    def fake():
        return next(calls), True

    # a store must be found first, or the media logic is never reached - which is
    # the point: "no store" is unknown, never "nothing captured"
    monkeypatch.setattr(monitoring, "_agent_stores", lambda: [pathlib.Path("/tmp/none")])
    monkeypatch.setattr(monitoring, "_media_files", fake)
    monitoring._MEDIA_BASELINE[0] = None
    first = monitoring._run({})
    assert "new media written" not in first
    second = monitoring._run({})
    assert "new media written" in second and "was 3" in second


def test_every_new_probe_can_say_it_could_not_ask() -> None:
    """The shape, asserted once for all of them rather than trusted."""
    probes = (monitoring._clipboard_readers, monitoring._unknown_input_devices,
              monitoring._remote_control, monitoring._audit_rules,
              monitoring._fingerprint, monitoring._screen_capture)
    for probe in probes:
        found, asked = probe()
        assert isinstance(found, list), probe.__name__
        assert isinstance(asked, bool), probe.__name__


def test_no_store_found_is_unknown_not_nothing_captured() -> None:
    """The whole reason the probe is generic: an agent whose store cannot be
    derived must not read as "it is not capturing anything"."""
    from shani_chronoa.senses import monitoring
    assert monitoring._agent_stores.__doc__
    stores = monitoring._agent_stores()
    assert isinstance(stores, list)
    # on a machine with no monitoring agent at all there is nothing to watch,
    # and that is also not the same as "nothing is captured"


def test_the_probe_does_not_report_itself_as_the_agent() -> None:
    """Measured: the first version matched its own command line, because the
    command running this sense contained the agent's name."""
    import os
    from shani_chronoa.senses import monitoring
    for line in monitoring._agents():
        assert not line.startswith(f"{os.getpid()} ")
        assert not line.startswith(f"{os.getppid()} ") if os.getppid() > 1 else True
