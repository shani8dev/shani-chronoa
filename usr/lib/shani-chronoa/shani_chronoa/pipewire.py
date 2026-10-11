"""PipeWire device discovery, beyond bare capture and playback.

Chronoa shells out to `pw-record`/`pw-play`, which bind to whatever PipeWire
treats as the default device. That is fine right up until it is not: a user
with a USB headset and built-in speakers has no way to say which one they meant,
and the only symptom is the assistant listening to the wrong microphone.

Both tools accept `--target`, so the choice is expressible at runtime - but it
takes a PipeWire *node name*, and those are not discoverable from `pw-record`
itself. They come from the running graph.

`pw-dump` is used rather than `pw-cli` because it emits JSON. `pw-cli`'s output
is translated, so parsing it would break under a non-English locale.

Every function here degrades to empty/False rather than raising: PipeWire may be
absent, replaced by PulseAudio, or not running at all, and none of that is a
reason to stop an assistant from starting.
"""

from typing import Optional
import json
import logging
import shutil
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_DUMP_TIMEOUT = 10

# Virtual nodes are real but not selectable in a useful sense, and filtering
# them keeps a plain list to the devices a person would actually pick.
_VIRTUAL_MARKERS = ("node.driver", "Virtual", "Dummy")


@dataclass(frozen=True)
class AudioDevice:
    """One selectable device, as `pw-record --target` / `pw-play --target` wants it."""

    name: str
    description: str
    kind: str  # "input" or "output"
    nick: str = ""

    @property
    def label(self) -> str:
        """Short human-facing name for a device picker.

        `node.nick` is what a person would call the device ("HDMI 3"); the full
        `node.description` leads with the controller name and is unreadable in
        a list, so it is only the fallback.
        """
        return self.nick or self.description or self.name


def _graph_nodes() -> list[dict]:
    """Node property dicts from the live graph, or [] if unavailable."""
    if not shutil.which("pw-dump"):
        return []
    try:
        raw = subprocess.run(
            ["pw-dump"],
            capture_output=True,
            text=True,
            timeout=_DUMP_TIMEOUT,
            check=False,
        ).stdout
        objects = json.loads(raw or "[]")
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError) as e:
        logger.debug("pw-dump did not yield a usable graph: %s", e)
        return []
    if not isinstance(objects, list):
        return []
    return [
        obj["info"]["props"]
        for obj in objects
        if isinstance(obj, dict)
        and str(obj.get("type", "")).endswith("Node")
        and isinstance(obj.get("info"), dict)
        and isinstance(obj["info"].get("props"), dict)
    ]


# `wpctl` is the control-side counterpart to the `pw-dump` discovery above, and
# it was being invoked through three separate wrappers - in the volume skill, the
# privacy skill and the audio sense - each with its own timeout. Same binary,
# same machine, three chances to disagree about how long to wait and whether a
# failure raises. One wrapper, one timeout.
WPCTL_TIMEOUT = 10


def run_wpctl(*args: str) -> "subprocess.CompletedProcess[str]":
    """Run `wpctl` and return the completed process.

    Deliberately does not check for the binary's presence: every caller already
    decides for itself what a missing wpctl means for its own answer (a mute
    request and a device listing fail differently), and collapsing that decision
    here would take the wording away from the code that needs to explain it.
    """
    return subprocess.run(
        ["wpctl", *args],
        capture_output=True, text=True, timeout=WPCTL_TIMEOUT, check=False,
    )


def is_available() -> bool:
    """Whether a live PipeWire graph can be queried."""
    return bool(shutil.which("pw-dump")) and bool(_graph_nodes())


def _devices(kind: str) -> list[AudioDevice]:
    wanted = "Audio/Source" if kind == "input" else "Audio/Sink"
    found: list[AudioDevice] = []
    for props in _graph_nodes():
        if props.get("media.class") != wanted:
            continue
        name = props.get("node.name")
        if not name or any(marker in name for marker in _VIRTUAL_MARKERS):
            continue
        found.append(
            AudioDevice(
                name=name,
                description=props.get("node.description") or name,
                kind=kind,
                nick=props.get("node.nick") or "",
            )
        )
    return sorted(found, key=lambda d: d.label.lower())


def list_inputs() -> list[AudioDevice]:
    """Microphones, i.e. nodes `pw-record --target` can be pointed at."""
    return _devices("input")


def list_outputs() -> list[AudioDevice]:
    """Speakers, i.e. nodes `pw-play --target` can be pointed at."""
    return _devices("output")


STREAM_PLAYBACK = "Stream/Output/Audio"   # the app is producing sound
STREAM_CAPTURE = "Stream/Input/Audio"     # the app is consuming sound (its mic)


@dataclass(frozen=True)
class AudioStream:
    """One application carrying audio right now.

    **Not a device.** A device is hardware, or a virtual endpoint a person picks
    once; a stream arrives and leaves with whatever is playing, so it gets its
    own type rather than carrying fields that never apply to it.
    """

    name: str            # `node.name` - what `pw-record --target` takes
    direction: str       # "playback" or "capture"
    media: str = ""      # `media.name`: what the app says it is playing
    nick: str = ""

    @property
    def label(self) -> str:
        """`media.name` first: the only field that says *what*, where a browser's
        `node.name` is its binary. `node.name` is the fallback."""
        return self.media or self.nick or self.name

    @property
    def recordable(self) -> bool:
        """Only a **playback** stream: audio leaving the app, so there is
        something to capture. A capture stream is the app's own microphone."""
        return self.direction == "playback"


def list_streams() -> list[AudioStream]:
    """Applications carrying audio right now, playback first.

    **Restored 2026-10-11.** The original was written and then lost when a
    concurrent session replaced `recording.py` and `pipewire.py` in this tree;
    the tests in `tests/test_audio_devices.py` are what specified it again.

    Empty is the honest answer when nothing is playing - a graph with no
    streams is a quiet machine, not a broken one.
    """
    found: list[AudioStream] = []
    for props in _graph_nodes():
        media_class = str(props.get("media.class") or "")
        if media_class == STREAM_PLAYBACK:
            direction = "playback"
        elif media_class == STREAM_CAPTURE:
            direction = "capture"
        else:
            continue
        name = props.get("node.name")
        if not name:
            continue          # untargetable: `--target` needs a name or a serial
        found.append(AudioStream(
            name=str(name),
            direction=direction,
            media=str(props.get("media.name") or ""),
            nick=str(props.get("node.nick") or ""),
        ))
    return sorted(found, key=lambda s: (not s.recordable, s.label.lower()))


def resolve_target(configured: str, kind: str) -> "tuple[str, Optional[str]]":
    """Decide which device to actually use, and say why.

    Returns `(node_name_or_empty, problem_or_None)`.

    This exists because `pw-record --target` does not validate: given a name
    that is not in the graph it prints nothing, exits 0, and records from the
    default device instead. Measured here - a bogus target produced 187,720
    bytes of real audio against 189,766 bytes with no target at all, silently.
    So a stale setting (a headset that is no longer plugged in) would appear to
    be honoured while quietly using a different microphone, which is worse than
    not having the setting at all.

    An unconfigured device resolves to the default with no problem, because
    that is a legitimate choice rather than a failure.
    """
    configured = (configured or "").strip()
    if not configured:
        return "", None
    known = {d.name for d in _devices(kind)}
    if configured in known:
        return configured, None
    return "", (
        f"configured {kind} device {configured!r} is not in the running PipeWire "
        "graph; falling back to the default device"
    )


def echo_cancel_active() -> bool:
    """Whether an echo-cancelling node is present in the running graph.

    Reported rather than enabled. PipeWire's `module-echo-cancel` is a SPA hook
    attached to a source node by a `filter-chain.conf.d` fragment, so it cannot
    be switched on from here at runtime - only detected. See `audio.py`'s
    `BargeInMonitor` for why that matters: with no AEC, continuous-VAD barge-in
    hears Chronoa's own playback and interrupts itself.
    """
    for props in _graph_nodes():
        haystack = " ".join(
            str(props.get(key, "")) for key in ("node.name", "factory.name", "node.group")
        ).lower()
        if "echo" in haystack and "cancel" in haystack:
            return True
    return False
