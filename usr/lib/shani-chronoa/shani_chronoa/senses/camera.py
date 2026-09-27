"""Camera sense: which cameras exist, and whether anything has one open.

Enumerates `/dev/video*` and reads each node's `name`, so the machine's
cameras are identifiable rather than just numbered.

**This captures no frame and reads no image, and that boundary is the entire
design.** Knowing a camera exists is a different question from looking through
it, with a different blast radius: the first is a line of sysfs, the second is
a live feed of the room and everyone in it, and a user who consents to the
first has not consented to the second. So this sense deliberately stops at
enumeration, and `camera-sense-enabled` is not a permission to photograph
anything.

For the same reason there is no "is the camera light on" heuristic here.
Inferring an active capture from a node changing state is exactly the kind of
guess that produces a confident wrong answer, and `fuser` on the device node
gives a real answer instead: which processes hold the device open.

`senses/contention.py` covers that same question across every capture device
and is worth preferring; this exists because cameras specifically are the
device people most want an inventory of.
"""

import logging
import re
from pathlib import Path
from typing import Optional, Union

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PERSONAL, Sense
from shani_chronoa.senses.context import Percept

logger = logging.getLogger(__name__)

KIND = "machine-state"
SENSITIVITY = SENSITIVITY_PERSONAL
_TTL_SECONDS = 300.0
_POLL_INTERVAL = 300.0

_VIDEO_DIR = Path("/dev")
_VIDEO_NODE = re.compile(r"^video\d+$")

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "camera",
        "description": (
            "List the cameras this machine has - their device nodes and names "
            "- and report whether any process currently holds each one open. "
            "Enumeration only: no frame is captured and no image is read, so "
            "this cannot see anything, and enabling it is not permission to "
            "look through a camera."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _read_name(node: Path) -> Optional[str]:
    try:
        return (node / "name").read_text().strip()
    except OSError:
        return None


def read_cameras() -> list:
    try:
        entries = sorted(_VIDEO_DIR.iterdir())
    except OSError:
        return []
    out = []
    for entry in entries:
        if not _VIDEO_NODE.match(entry.name):
            continue
        out.append({
            "device": str(entry),
            "name": _read_name(entry),
            "node": not entry.is_dir(),
        })
    return out


def _run(arguments: dict) -> Union[str, Percept]:
    config = ChronoaConfig()
    if not config.sense_allowed("camera"):
        return f"Not enumerating cameras: {config.sense_allowed_reason('camera')}."

    cameras = read_cameras()
    if not cameras:
        return "This machine exposes no camera device nodes."

    from shani_chronoa.senses.contention import describe

    lines = []
    for cam in cameras:
        holder = describe(Path(cam["device"]))
        who = ", ".join(str(h["pid"]) for h in holder["holders"]) or "free"
        label = cam["name"] or "unnamed"
        lines.append(f"{cam['device']} ({label}): held by {who}")
    return _SENSE.to_percept(
        "\n".join(lines),
        source="dev-video",
        metadata={"cameras": len(cameras)},
    )


_SENSE = Sense(
    name="camera",
    kind=KIND,
    ttl_seconds=_TTL_SECONDS,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
