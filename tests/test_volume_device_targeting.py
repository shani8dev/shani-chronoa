"""Targeting a specific audio output, on a machine that has more than one.

Every volume call was hardcoded to `@DEFAULT_AUDIO_SINK@`. On a laptop with
four sinks - three HDMI/DisplayPort outputs and the built-in speaker - that means
"turn it down" can only ever mean one device, and there is no way to ask about
the others at all.

Two things were wrong in the first version, and running it against a real sink
found both.

**Whitespace was rejected.** The guard refused any name containing a space. Every
real sink on this machine is named "Tiger Lake-LP Smart Sound Technology Audio
Controller HDMI / DisplayPort 3 Output", so the rule refused every device the
feature existed to serve.

**Names were passed to `wpctl`.** wpctl wants a numeric node id, and answers

    Error: '...DisplayPort 3 Output' is not a valid number

so even a correctly-guarded name failed. The id is the contract; names are
resolved to ids as a convenience, because a model reading a device list will
reach for the name it was shown.

Restricting to digits also makes injection structurally impossible: there is no
string that is both a number and a command.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import volume as V  # noqa: E402


class _Proc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


STATUS = """\
PipeWire 'pipewire-0' [1.0.5]
 └─ Sinks:
   *   54. Built-in Speaker [vol: 0.50]
      51. Tiger Lake HDMI / DisplayPort 3 Output [vol: 1.00]
      52. Tiger Lake HDMI / DisplayPort 2 Output [vol: 1.00]

 └─ Sources:
      73. Built-in Microphone [vol: 1.00]
"""


@pytest.fixture
def wpctl(monkeypatch):
    """Record what reached wpctl, and answer `status` from a fixed tree."""
    calls = []

    def run(*args):
        calls.append(list(args))
        if args[:1] == ("status",):
            return _Proc(STATUS)
        return _Proc("Volume: 1.00\n")

    monkeypatch.setattr(V, "_run_wpctl", run)
    return calls


class TestTheDefaultIsUnchanged:
    def test_no_device_still_means_the_default_sink(self, wpctl):
        V._run_get_volume({})
        assert wpctl[0][1] == "@DEFAULT_AUDIO_SINK@"

    def test_setting_without_a_device_still_targets_the_default(self, wpctl):
        V._run_set_volume({"percent": 30})
        assert wpctl[0][1] == "@DEFAULT_AUDIO_SINK@"

    def test_muting_without_a_device_still_targets_the_default(self, wpctl):
        V._run_set_mute({"mute": True})
        assert wpctl[0][1] == "@DEFAULT_AUDIO_SINK@"


class TestTargetingWorks:
    def test_a_numeric_id_reaches_wpctl(self, wpctl):
        V._run_get_volume({"device": "51"})
        assert wpctl[-1] == ["get-volume", "51"], f"calls were {wpctl}"

    def test_an_integer_id_is_accepted_too(self, wpctl):
        V._run_get_volume({"device": 51})
        assert wpctl[-1] == ["get-volume", "51"], f"calls were {wpctl}"

    def test_a_name_is_resolved_to_its_id(self, wpctl):
        """Because a model reading a device list reaches for the name."""
        V._run_get_volume({"device": "Tiger Lake HDMI / DisplayPort 3 Output"})
        # Name resolution issues a `status` call first, then the real one.
        assert wpctl[-1] == ["get-volume", "51"], (
            f"the name did not resolve to node 51; calls were {wpctl}"
        )

    def test_the_asterisked_default_name_resolves(self, wpctl):
        V._run_get_volume({"device": "Built-in Speaker"})
        assert wpctl[-1] == ["get-volume", "54"], f"calls were {wpctl}"

    def test_setting_a_specific_output(self, wpctl):
        out = V._run_set_volume({"percent": 30, "device": "51"})
        assert wpctl[-1][1] == "51", f"calls were {wpctl}"
        assert "30%" in wpctl[-1][2]
        assert "51" in out, (
            "naming a specific output and then not saying which one changed "
            "leaves the user unable to tell what happened"
        )

    def test_the_default_path_does_not_say_default_sink(self, wpctl):
        """`Volume set to 50%.` is the answer; naming the sentinel is noise.

        The existing coercion tests pin this message exactly, and they were
        right to - "@DEFAULT_AUDIO_SINK@" means nothing to a person.
        """
        out = V._run_set_volume({"percent": 50})
        assert out == "Volume set to 50%."
        assert "DEFAULT" not in out.upper()

    def test_muting_a_specific_output(self, wpctl):
        V._run_set_mute({"mute": True, "device": "52"})
        assert wpctl[-1] == ["set-mute", "52", "1"], f"calls were {wpctl}"


class TestAnUnknownDeviceNeverSilentlyMeansTheDefault:
    """The failure this feature could most easily introduce.

    Falling back to the default would change a *different* device than the user
    asked about, and report success. Quieting the built-in speaker when someone
    asked about an HDMI monitor is worse than refusing.
    """

    def test_an_unknown_name_is_refused(self, wpctl):
        out = V._run_get_volume({"device": "Nonexistent Device"})
        assert "Unknown audio device" in out
        assert "@DEFAULT_AUDIO_SINK@" not in [c[1] for c in wpctl if len(c) > 1], (
            f"an unknown device fell through to the default sink: {wpctl}"
        )

    def test_the_refusal_says_what_to_pass_instead(self, wpctl):
        out = V._run_get_volume({"device": "Nonexistent Device"})
        assert "numeric id" in out

    def test_an_unknown_device_is_refused_for_set_too(self, wpctl):
        out = V._run_set_volume({"percent": 30, "device": "Nonexistent"})
        assert "Unknown audio device" in out
        assert "@DEFAULT_AUDIO_SINK@" not in [c[1] for c in wpctl if len(c) > 1]

    def test_and_for_mute(self, wpctl):
        out = V._run_set_mute({"mute": True, "device": "Nonexistent"})
        assert "Unknown audio device" in out
        assert "@DEFAULT_AUDIO_SINK@" not in [c[1] for c in wpctl if len(c) > 1]


class TestInjectionIsStructurallyImpossible:
    """A node id is digits, so nothing else can be one."""

    @pytest.mark.parametrize("hostile", [
        "foo; rm -rf /", "x'y", "a`id`", "51; id", "abc",
        "a|b", "a>b", "a\nb", "$(id)", "${HOME}", "", "   ", "51 52",
    ])
    def test_nothing_but_an_id_reaches_wpctl(self, wpctl, hostile):
        """Every wpctl argument must be an id, a command name or a number.

        Name resolution legitimately issues a `status` call first - that is the
        point of resolving a name - so the assertion is on the *device* argument
        rather than on call index 0.
        """
        V._run_get_volume({"device": hostile})
        device_args = [c[1] for c in wpctl if len(c) > 1 and c[1] != "status"]
        for arg in device_args:
            assert arg.isdigit(), (
                f"{hostile!r} reached wpctl as a device argument: {arg!r}"
            )

    def test_a_non_string_non_int_is_refused(self, wpctl):
        out = V._run_get_volume({"device": ["51"]})
        assert "Invalid device" in out
        assert not wpctl

    def test_a_boolean_is_not_an_id(self, wpctl):
        """`True` is an int in Python, which would otherwise become '1'."""
        out = V._run_get_volume({"device": True})
        assert "Invalid device" in out
        assert not wpctl


class TestAnUnreadableDeviceList:
    def test_a_failed_status_does_not_crash_name_resolution(self, monkeypatch):
        monkeypatch.setattr(V, "_run_wpctl", lambda *a: _Proc("", 1))
        out = V._run_get_volume({"device": "Any Name"})
        assert "Unknown audio device" in out

    def test_a_raising_wpctl_does_not_crash_it(self, monkeypatch):
        def boom(*a):
            raise RuntimeError("wpctl is not installed")
        monkeypatch.setattr(V, "_run_wpctl", boom)
        out = V._run_get_volume({"device": "Any Name"})
        assert "Unknown audio device" in out


class TestTheSchemaSaysWhatWpctlActuallyAccepts:
    def test_the_device_argument_is_a_numeric_node_id(self):
        source = (_REPO / "usr" / "lib" / "shani-chronoa" / "shani_chronoa"
                  / "skills" / "volume.py").read_text()
        assert "Numeric node id" in source, (
            "the schema tells the model to pass a name, which wpctl rejects "
            "with \"is not a valid number\""
        )
