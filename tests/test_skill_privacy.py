"""The `set_privacy` skill, against the two ways a privacy control can lie.

A privacy actuator that reports success and did nothing is the worst outcome
this codebase can produce: the user believes their microphone or camera is off
and is not. Both tests below are built on real output from this machine.

**`wpctl status` prints a tree, not a flat list.** The actual source section:

    ├─ Sources:
    │      55. ... Headphones Stereo Microphone [vol: 1.00]
    │  *   56. ... Digital Microphone [vol: 0.43]

Two parsers got this wrong before it was right, and both reported "no audio
source" on a machine that has two:

- stripping only `│` left the `├─ ` on the heading, which then failed a
  "contains no spaces" test, so the section was never recognised;
- looking for a `•` bullet finds nothing — the bullet is a `*`, and only on
  the *default* source.

Verified on the real machine: mute takes (`wpctl get-volume` reports
`[MUTED]`), and unmute restores it.

**A camera with no `disable` control cannot be turned off.** This machine's
integrated camera exposes none, so `disable_camera` must say it did *not*
disable anything. A driver without the control is not a permission problem and
not a silent success.
"""

import pytest

from shani_chronoa.skills import privacy

# The real `wpctl status` source section, box-drawing characters and all.
WPCTL_STATUS = """PipeWire 'pipewire-0' [1.0.5, host]
   └─ Clients:
          32. pipewire                            [1.0.5, host, pid:2622]
   └─ Sinks:
   └─ Sources:
 │      55. Tiger Lake-LP Smart Sound Technology Audio Controller Headphones Stereo Microphone [vol: 1.00]
 │  *   56. Tiger Lake-LP Smart Sound Technology Audio Controller Digital Microphone [vol: 0.43]
 │
 ├─ Source endpoints:
 │
 └─ Streams:
"""

WPCTL_TOKENS = """PipeWire 'pipewire-0' [1.0.5, host]
├─ Sources:
│  *   @DEFAULT_AUDIO_SOURCE@.alsa_input.pci-0000_00_1f.3.analog-stereo  [vol: 1.00]
"""

CAMERA_NO_CONTROL = """\x00"""


def _text(result):
    """The reply string from a helper, which also returns a machine-readable
    outcome dict. `_run` unwraps it for the Skill contract."""
    return result["text"] if isinstance(result, dict) else result


def _v4l(tmp_path, nodes):
    """A fake /sys/class/video4linux. `nodes` maps name -> disable control?"""
    root = tmp_path / "video4linux"
    root.mkdir()
    for name, (label, control) in nodes.items():
        entry = root / name
        entry.mkdir()
        (entry / "name").write_text(label + "\n")
        if control is not None:
            (entry / "disable").write_text(control + "\n")
    return root


@pytest.fixture
def wired(monkeypatch):
    """Both `wpctl` and a video4linux tree, faked."""
    def fake(*arguments, **kwargs):
        class R:
            stdout = WPCTL_STATUS
            stderr = ""
            returncode = 0
        return R()
    monkeypatch.setattr(privacy.shutil, "which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(privacy.subprocess, "run", fake)
    return fake


class TestTheWpctlTree:
    def test_numeric_sources_are_found(self, wired):
        """Two sources here. Reporting none was the bug."""
        sources = privacy._audio_targets()
        assert len(sources) == 2
        assert {s["id"] for s in sources} == {"55", "56"}

    def test_the_box_drawing_heading_is_recognised(self, wired):
        """`├─ Sources:` has a space and a tree prefix; a "no spaces" test on the
        stripped line never matched, so the section was skipped entirely."""
        assert any(s["name"].endswith("Digital Microphone")
                   for s in privacy._audio_targets())

    def test_the_star_marks_the_default_not_a_bullet(self, wired):
        """The default marker is a `*`, and only the default has one."""
        sources = {s["id"]: s for s in privacy._audio_targets()}
        assert sources["56"]["default"] is True
        assert sources["55"]["default"] is False

    def test_the_volume_annotation_is_stripped_from_the_name(self, wired):
        for source in privacy._audio_targets():
            assert "[vol:" not in source["name"]

    def test_a_sink_is_not_a_source(self, wired):
        """`Sinks:` and `Sources:` are different sections and a microphone is
        not a sink."""
        assert all("Sink" not in s["name"] for s in privacy._audio_targets())

    def test_the_token_form_is_also_accepted(self, monkeypatch):
        """`wpctl status` prints ids, but a token form exists and must not be
        silently invisible."""
        def fake(*a, **k):
            class R:
                stdout = WPCTL_TOKENS
                stderr = ""
                returncode = 0
            return R()
        monkeypatch.setattr(privacy.shutil, "which", lambda n: "/usr/bin/wpctl")
        monkeypatch.setattr(privacy.subprocess, "run", fake)
        sources = privacy._audio_targets()
        assert sources and sources[0]["id"].startswith("@"), sources
        assert sources[0]["default"] is True

    def test_a_missing_wpctl_is_an_explanation_not_a_silent_zero(self, monkeypatch):
        monkeypatch.setattr(privacy.shutil, "which", lambda n: None)
        assert privacy._audio_targets() == []
        assert "wpctl is not installed" in _text(privacy._mute_mic(True))

    def test_no_sources_is_not_the_same_as_a_muted_microphone(self, monkeypatch):
        def fake(*a, **k):
            class R:
                stdout = "no audio here\n"
                stderr = ""
                returncode = 0
            return R()
        monkeypatch.setattr(privacy.shutil, "which", lambda n: "/usr/bin/wpctl")
        monkeypatch.setattr(privacy.subprocess, "run", fake)
        result = _text(privacy._mute_mic(True))
        assert "different from a microphone that is already muted" in result


class TestMuteIsAHardwareClaim:
    def test_the_reply_says_it_is_a_stream_mute(self, wired):
        """Muting a stream is an intention; a new stream starts unmuted. Saying
        "muted" without that is a claim about the hardware this cannot make."""
        result = _text(privacy._mute_mic(True))
        assert "stream mute" in result
        assert "starts unmuted" in result

    def test_it_names_the_sources_it_changed(self, wired):
        assert "Digital Microphone" in _text(privacy._mute_mic(True))

    def test_it_says_how_to_undo(self, wired):
        assert "To undo" in _text(privacy._mute_mic(False))

    def test_a_source_that_refuses_is_named_as_a_failure(self, monkeypatch):
        def fake(argv, **k):
            class R:
                stdout = ""
                stderr = "No such node"
                returncode = 1
            return R()
        monkeypatch.setattr(privacy.shutil, "which", lambda n: "/usr/bin/wpctl")
        monkeypatch.setattr(privacy.subprocess, "run", fake)
        monkeypatch.setattr(privacy, "_audio_targets",
                            lambda: [{"id": "1", "name": "Mic", "default": True,
                                      "muted": None}])
        result = _text(privacy._mute_mic(True))
        assert "Could not change" in result
        assert "Mic" in result


class TestEnablingIsNotTheSameAsDisabling:
    """The asymmetry, which is the whole consent design of this skill.

    Disabling a camera is protective and stays ungated — a user who asks for it
    is the consent, and gating it would make the one action nobody objects to the
    one an assistant cannot take. Enabling one re-arms a sensor that may have
    been disabled deliberately, so it is refused without the camera consent.
    """

    def test_enabling_is_refused_without_camera_consent(self, tmp_path, monkeypatch):
        root = _v4l(tmp_path, {"video0": ("Cam", "1")})
        monkeypatch.setattr(privacy, "_VIDEO4LINUX", root)
        monkeypatch.setattr(privacy.ChronoaConfig, "sense_allowed",
                            lambda self, s: False)
        result = privacy._set_camera(True)
        assert result["changed"] == []
        assert (root / "video0" / "disable").read_text().strip() == "1", (
            "the camera was switched on with the camera sense refused"
        )
        assert "Refusing to enable a camera" in result["text"]

    def test_the_refusal_says_disabling_needs_no_permission(self, tmp_path, monkeypatch):
        """Otherwise a user who cannot enable one may think the whole skill is
        unavailable."""
        root = _v4l(tmp_path, {"video0": ("Cam", "1")})
        monkeypatch.setattr(privacy, "_VIDEO4LINUX", root)
        monkeypatch.setattr(privacy.ChronoaConfig, "sense_allowed",
                            lambda self, s: False)
        assert "always available" in privacy._set_camera(True)["text"]

    def test_disabling_is_never_gated(self, tmp_path, monkeypatch):
        """The protective direction must not be gated, or the asymmetry is
        pointless."""
        root = _v4l(tmp_path, {"video0": ("Cam", "0")})
        monkeypatch.setattr(privacy, "_VIDEO4LINUX", root)
        monkeypatch.setattr(privacy.ChronoaConfig, "sense_allowed",
                            lambda self, s: False)
        result = privacy._set_camera(False)
        assert len(result["changed"]) == 1
        assert (root / "video0" / "disable").read_text().strip() == "1"

    def test_enabling_proceeds_once_consent_is_granted(self, tmp_path, monkeypatch):
        root = _v4l(tmp_path, {"video0": ("Cam", "1")})
        monkeypatch.setattr(privacy, "_VIDEO4LINUX", root)
        monkeypatch.setattr(privacy.ChronoaConfig, "sense_allowed",
                            lambda self, s: True)
        result = privacy._set_camera(True)
        assert len(result["changed"]) == 1
        assert (root / "video0" / "disable").read_text().strip() == "0"

    def test_muting_the_microphone_is_also_ungated(self):
        """Muting is protective too, so it is not behind the camera consent
        even though both live in one skill."""
        import inspect
        source = inspect.getsource(privacy._mute_mic)
        assert "sense_allowed" not in source


class TestCamerasCannotLie:
    def test_a_camera_with_no_control_is_reported_as_not_disabled(self, tmp_path, monkeypatch):
        """This machine's real state: no `disable` node exists at all."""
        root = _v4l(tmp_path, {
            "video0": ("Integrated Camera", None),
            "video1": ("Integrated Camera", None),
        })
        monkeypatch.setattr(privacy, "_VIDEO4LINUX", root)
        result = privacy._set_camera(False)
        assert "expose no disable control" in result["text"]
        assert "were NOT disabled" in result["text"]
        assert "claiming otherwise would be the worst possible answer" in result["text"]

    def test_nothing_is_claimed_changed_when_nothing_can_be(self, tmp_path, monkeypatch):
        root = _v4l(tmp_path, {"video0": ("Integrated Camera", None)})
        monkeypatch.setattr(privacy, "_VIDEO4LINUX", root)
        result = privacy._set_camera(False)
        assert result["changed"] == []
        assert result["unsupported"]

    def test_a_root_owned_control_reports_the_permission_failure(self, tmp_path, monkeypatch):
        """A permission error must say so, never read as success."""
        root = _v4l(tmp_path, {"video0": ("Cam", "0")})
        monkeypatch.setattr(privacy, "_VIDEO4LINUX", root)
        node = root / "video0" / "disable"
        node.chmod(0o400)
        try:
            result = privacy._set_camera(False)
            assert result["changed"] == [], "an unwritable node was reported as changed"
            assert "needs root" in result["text"]
            assert "nothing was changed" in result["text"]
        finally:
            node.chmod(0o644)

    def test_a_writable_control_is_actually_written(self, tmp_path, monkeypatch):
        root = _v4l(tmp_path, {"video0": ("Cam", "0")})
        monkeypatch.setattr(privacy, "_VIDEO4LINUX", root)
        result = privacy._set_camera(False)
        assert len(result["changed"]) == 1
        assert (root / "video0" / "disable").read_text().strip() == "1"
        assert "To undo" in result["text"]

    def test_enable_writes_the_other_value(self, tmp_path, monkeypatch):
        root = _v4l(tmp_path, {"video0": ("Cam", "1")})
        monkeypatch.setattr(privacy, "_VIDEO4LINUX", root)
        monkeypatch.setattr(privacy.ChronoaConfig, "sense_allowed",
                            lambda self, s: True)
        privacy._set_camera(True)
        assert (root / "video0" / "disable").read_text().strip() == "0"

    def test_no_camera_at_all_is_not_the_same_as_an_unusable_one(self, tmp_path, monkeypatch):
        monkeypatch.setattr(privacy, "_VIDEO4LINUX", tmp_path / "gone")
        result = privacy._set_camera(False)["text"]
        assert "No camera was found" in result
        assert "different from a camera that exists but could not be reached" in result


class TestBlankingIsNotPortable:
    def test_a_wayland_session_is_explained_not_worked_around(self, monkeypatch):
        """Routinely blanking a screen under Wayland requires a portal prompt.
        Bypassing that would defeat a security boundary, so the reply says no
        and offers the idle timeout instead."""
        monkeypatch.setattr(privacy.shutil, "which", lambda n: None)
        monkeypatch.setattr(privacy, "_session_type", lambda: "wayland")
        text = privacy._blank_screen()["text"]
        assert "permission dialog" in text
        assert "security boundary" in text
        assert "power settings" in text, "the portable alternative is not offered"

    def test_x11_uses_xset(self, monkeypatch):
        calls = []

        def fake(argv, **k):
            calls.append(argv)
            class R:
                stdout = ""
                stderr = ""
                returncode = 0
            return R()
        monkeypatch.setattr(privacy.shutil, "which", lambda n: "/usr/bin/xset")
        monkeypatch.setattr(privacy.subprocess, "run", fake)
        text = privacy._blank_screen()["text"]
        assert ["xset", "dpms", "force", "off"] in calls
        assert "Any key press" in text

    def test_no_xset_and_not_wayland_says_so(self, monkeypatch):
        monkeypatch.setattr(privacy.shutil, "which", lambda n: None)
        monkeypatch.setattr(privacy, "_session_type", lambda: "x11")
        text = privacy._blank_screen()["text"]
        assert "xset is not installed" in text
        assert "idle timeout" in text


class TestDispatch:
    def test_status_is_the_default_action(self, monkeypatch, tmp_path):
        monkeypatch.setattr(privacy, "_VIDEO4LINUX", _v4l(tmp_path, {
            "video0": ("Cam", "0")}))
        monkeypatch.setattr(privacy.shutil, "which", lambda n: None)
        monkeypatch.setattr(privacy, "_audio_targets", lambda: [])
        result = privacy._run({})
        assert "microphone" in result
        assert "no camera found" not in result

    def test_an_unknown_action_lists_the_valid_ones(self):
        result = privacy._run({"action": "self_destruct"})
        assert "Unknown action" in result
        for name in ("mute_mic", "disable_camera", "blank_screen"):
            assert name in result

    def test_every_advertised_action_is_handled(self, monkeypatch, tmp_path):
        monkeypatch.setattr(privacy, "_VIDEO4LINUX", _v4l(tmp_path, {
            "video0": ("Cam", "0")}))
        monkeypatch.setattr(privacy.ChronoaConfig, "sense_allowed",
                            lambda self, s: True)
        monkeypatch.setattr(privacy.shutil, "which", lambda n: None)
        monkeypatch.setattr(privacy, "_session_type", lambda: "wayland")
        for action in ("mute_mic", "unmute_mic", "disable_camera",
                       "enable_camera", "blank_screen"):
            result = privacy._run({"action": action})
            assert "Unknown action" not in result, action
            assert result, action

    def test_it_is_a_well_formed_skill(self):
        assert len(privacy.SKILLS) == 1
        skill = privacy.SKILLS[0]
        assert skill.name == "set_privacy"
        assert skill.schema["type"] == "function"
        assert skill.schema["function"]["name"] == "set_privacy"
        assert callable(skill.run)


class TestTheDeviceArgumentSelectsOneCamera:
    """`device` has been in `set_privacy`'s schema since the skill was written,
    and no handler ever read it.

    `_set_camera` acted on **every** camera whatever was passed, so the answer
    to "turn off my external webcam and leave the built-in one alone" was both
    cameras disabled and a reply saying the right thing had happened. A dead
    control on a privacy action is the wrong direction to fail in: it does the
    opposite of protecting.

    Found by `test_no_dead_schema_parameters`, which is what the class is here
    for - the audit and the fix belong together, or the next dead parameter has
    no reason to be believed.
    """

    def _cams(self, tmp_path, monkeypatch):
        # Both start at "1" = already disabled, so a write of the same value
        # would be invisible to a `read_text() == "1"` assertion. That is why
        # the untouched camera is asserted against the fixture's own starting
        # value and the selected one is asserted by what `changed` reports.
        root = _v4l(tmp_path, {
            "video0": ("Integrated Camera: Integrated C", "1"),
            "video1": ("HD Pro Webcam C920", "1"),
        })
        monkeypatch.setattr(privacy, "_VIDEO4LINUX", root)
        monkeypatch.setattr(privacy.ChronoaConfig, "sense_allowed",
                            lambda self, s: True)
        return root

    def test_a_named_camera_leaves_the_others_alone(self, tmp_path,
                                                    monkeypatch):
        root = self._cams(tmp_path, monkeypatch)
        result = privacy._set_camera(False, "HD Pro Webcam C920")
        assert [c["path"] for c in result["changed"]] == ["/dev/video1"]
        assert (root / "video1" / "disable").read_text().strip() == "1"
        assert (root / "video0" / "disable").read_text().strip() == "1"
        assert "HD Pro Webcam C920" in result["text"]

    def test_the_node_name_selects(self, tmp_path, monkeypatch):
        self._cams(tmp_path, monkeypatch)
        result = privacy._set_camera(False, "video0")
        assert [c["path"] for c in result["changed"]] == ["/dev/video0"]

    def test_the_device_path_selects(self, tmp_path, monkeypatch):
        self._cams(tmp_path, monkeypatch)
        result = privacy._set_camera(False, "/dev/video1")
        assert [c["path"] for c in result["changed"]] == ["/dev/video1"]

    def test_a_substring_of_the_driver_name_selects(self, tmp_path,
                                                    monkeypatch):
        """`/sys/class/video4linux/videoN/name` reads `UVC Camera (046d:0825)`
        and a person says "logitech", so a substring is what has to match.
        """
        root = self._cams(tmp_path, monkeypatch)
        (root / "video1" / "name").write_text("UVC Camera (046d:0825)\n")
        result = privacy._set_camera(False, "uvc")
        assert [c["path"] for c in result["changed"]] == ["/dev/video1"]

    def test_a_name_that_matches_nothing_refuses_and_changes_nothing(
            self, tmp_path, monkeypatch):
        """The empty action is the dangerous one: nothing disabled, reported as
        if it had been.

        **Asserted on `changed` and on the files**, not on the sentence, because
        a plausible refusal with a camera switched off underneath it is exactly
        the failure this class documents.
        """
        root = self._cams(tmp_path, monkeypatch)
        result = privacy._set_camera(False, "NoSuchCam")
        assert result["changed"] == []
        assert (root / "video0" / "disable").read_text().strip() == "1"
        assert (root / "video1" / "disable").read_text().strip() == "1"
        assert "No camera matches" in result["text"]
        assert "Nothing was changed" in result["text"]

    def test_the_refusal_names_what_this_machine_has(self, tmp_path,
                                                     monkeypatch):
        self._cams(tmp_path, monkeypatch)
        result = privacy._set_camera(False, "NoSuchCam")
        assert "Integrated Camera: Integrated C (/dev/video0)" in result["text"]
        assert "HD Pro Webcam C920 (/dev/video1)" in result["text"]

    def test_omitting_the_device_still_means_every_camera(self, tmp_path,
                                                          monkeypatch):
        """The omission must keep its meaning once the promise is honoured.

        Otherwise the fix has swapped one dead control for a different wrong
        answer - a `device=""` that disables nothing at all.
        """
        self._cams(tmp_path, monkeypatch)
        result = privacy._set_camera(False, None)
        assert sorted(c["path"] for c in result["changed"]) == [
            "/dev/video0", "/dev/video1"]

    def test_an_empty_string_is_the_omission_not_a_miss(self, tmp_path,
                                                        monkeypatch):
        """A model sends `device=""` for "none", which must not become "match
        nothing" and refuse.

        **And it must not work by accident.** The first version matched with
        `"" in name`, which is True for every camera - so the omission held by
        an accident of substring semantics, one edit away from selecting
        nothing at all. Stripping first and testing the stripped value is what
        makes it deliberate.
        """
        self._cams(tmp_path, monkeypatch)
        assert len(privacy._set_camera(False, "")["changed"]) == 2

    def test_a_whitespace_value_is_the_omission(self, tmp_path, monkeypatch):
        """The same accident, from the value nobody thinks to send.

        `"  "` selected every camera through `"" in name`, which is the point
        at which a reader discovers the omission was never a decision.
        """
        self._cams(tmp_path, monkeypatch)
        assert len(privacy._set_camera(False, "   ")["changed"]) == 2

    def test_a_match_selected_for_enabling_is_still_consent_gated(
            self, tmp_path, monkeypatch):
        root = self._cams(tmp_path, monkeypatch)
        monkeypatch.setattr(privacy.ChronoaConfig, "sense_allowed",
                            lambda self, s: False)
        result = privacy._set_camera(True, "HD Pro Webcam C920")
        assert result["changed"] == []
        assert (root / "video1" / "disable").read_text().strip() == "1"
        assert "Refusing to enable a camera" in result["text"]

    def test_it_reaches_the_handler_from_the_dispatch_path(self, tmp_path,
                                                           monkeypatch):
        """Through `_run`, not through `_set_camera`.

        The defect was a *call site* that dropped the argument, so a test of the
        helper alone is the "a helper can be correct and still be unreachable"
        shape - and it stayed green for as long as the parameter was unread.

        **Asserted on which cameras the reply names as changed.** The first
        version of this assertion checked that the selected camera's name
        appears and that both are still listed, both of which the *defect*
        satisfies - a write to every camera names every camera in its report.
        """
        root = self._cams(tmp_path, monkeypatch)
        out = privacy._run({"action": "disable_camera",
                            "device": "HD Pro Webcam C920"})
        assert "disabled: HD Pro Webcam C920." in out
        assert "Integrated Camera" not in out.split("disabled:")[1].split(
            "To undo")[0], (
            "the reply names a camera that was not selected, so the argument "
            "is still being dropped at the call site")
        assert len(privacy._cameras()) == 2
        assert (root / "video1" / "disable").read_text().strip() == "1"

    def test_a_wrong_device_from_the_dispatch_path_refuses(self, tmp_path,
                                                           monkeypatch):
        root = self._cams(tmp_path, monkeypatch)
        out = privacy._run({"action": "disable_camera", "device": "nope"})
        assert "No camera matches" in out
        assert (root / "video0" / "disable").read_text().strip() == "1"
        assert (root / "video1" / "disable").read_text().strip() == "1"
