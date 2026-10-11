"""Sense: who is capturing on this machine, and through which door.

"What can see or hear me right now" has no answer anywhere in this project. The
machine knows - a device node has holders, an audio server has capture streams,
a process has a name - and none of it was read. This reads it and says so, once,
as a fact about the machine the person is sitting at.

**This is a reader and nothing else.** It does not block, mute, occupy, kill, or
redirect anything, and it does not need to: a person who is told *"the camera is
held by process X"* can decide what to do about it, which is their decision and
not a machine's. That is the line between this and a tool whose whole subject is
fooling an observer, and the docstring says it plainly because the two look
similar from the outside and are not similar at all.

Honesty rules, each of which is a way of being confidently wrong:

- **A probe that could not run is UNKNOWN, never "nothing is capturing."** No
  `fuser`, no `pactl`, no readable `/dev/input` - each of those produces an
  empty answer, and an empty answer reads as a clean machine. The sense has to
  be able to say "I could not ask" and the schema has to be able to carry it.
- **A device with no holder is not a finding.** `/dev/video0` existing says
  nothing at all; what matters is who has it open.
- **The compositor holding `/dev/input/*` is normal and is named as normal.**
  A raw-input reader that flags Xorg, Mutter, KWin or Sway on every machine is
  a sensor that cries wolf, and the one real finding is the one nobody reads.
- **A monitoring agent's name is reported, not judged.** Whether one is allowed
  to run here is a question with a different answer on a personal laptop and on
  a machine somebody enrolled in a workforce product, so the sense names the
  process and its outbound connections and stops there.
"""

from __future__ import annotations

import logging
import os
import pathlib
import re
import shutil
import subprocess
from typing import List, Optional

from shani_chronoa.senses import SENSITIVITY_PRIVATE, Sense

logger = logging.getLogger(__name__)

KIND = "machine-state"
#: Private, and off by default: what it reports is about other processes on this
#: machine, which is a fact about the machine's *use* rather than its hardware.
SENSITIVITY = SENSITIVITY_PRIVATE
_TTL_SECONDS = 600.0
_POLL_INTERVAL = 600.0
_TIMEOUT = 15

#: Device nodes and what holding them means.
_VIDEO_GLOB = re.compile(r"^video\d+$")
_INPUT_GLOB = re.compile(r"^event\d+$")

#: Who legitimately reads raw input on an ordinary desktop. Without this list
#: every machine reports its own compositor as a keylogger, and the one real
#: finding is the one nobody reads.
_LEGIT_INPUT_HOLDERS = re.compile(
    r"(^|[/\s])(Xorg|Xwayland|mutter|gnome-shell|weston|sway|kwin_x11|"
    r"kwin_wayland|labwc|hyprland|systemd-logind|libinput)([/\s]|$)", re.IGNORECASE)

#: Workforce-monitoring agents, by the names they actually ship under. Reported
#: by name and connection, never acted on - see the module docstring.
_AGENT_PATTERN = re.compile(
    r"workpuls|insightful|hubstaff|activtrak|teramind|timecamp|veriato|"
    r"remote-people|staffcop|kickidler", re.IGNORECASE)

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "monitoring",
        "description": (
            "Report who is capturing on this machine: which process holds each "
            "camera device, whether an audio stream is being recorded, who reads "
            "raw input devices, and whether a workforce-monitoring agent is "
            "running with outbound connections. Read-only; it reports and "
            "changes nothing."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def _probe(*argv: str) -> Optional[subprocess.CompletedProcess]:
    """Run a read-only command, or None when it cannot be run at all.

    Deliberately not named `_run`: the sense's entry point is `_run`, and a
    helper sharing its name is overwritten by the second definition - which is
    exactly what happened in the first version of this file, and the sense then
    never registered at all.
    """
    if not argv or shutil.which(argv[0]) is None:
        return None
    try:
        return subprocess.run(list(argv), capture_output=True, text=True,
                              timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None


def _holders(node: str) -> List[str]:
    """Every command currently holding `node` open.

    `fuser` prints a bare list of pids on one line; the command line for each is
    read from `/proc`, because a pid alone is not something a person can act on
    and a name alone can be fabricated by any process.
    """
    proc = _probe("fuser", node)
    if proc is None:
        return []
    out = []
    for pid in re.findall(r"\d+", proc.stdout or ""):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as handle:
                cmd = handle.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
        except OSError:
            continue
        if cmd:
            out.append(cmd[:120])
    return out


def _audio_streams() -> Optional[List[str]]:
    """Active capture streams, or None when the audio server cannot be asked.

    `pactl list source-outputs short` is authoritative: a *source* existing says
    nothing, and it returns empty when nothing is capturing. The `wpctl`
    fallback looks for a running source, not a present one - the two differ on
    every machine with an idle microphone.
    """
    proc = _probe("pactl", "list", "source-outputs", "short")
    if proc is None:
        proc = _probe("wpctl", "status")
        if proc is None:
            return None
        lines = proc.stdout.splitlines()
        start = next((i for i, l in enumerate(lines) if re.search(r"Sources:", l, re.I)), None)
        if start is None:
            return None
        window = "\n".join(lines[start:start + 12])
        return ["a microphone source is capturing"] if re.search(r"\brunning\b", window, re.I) else []
    if proc.returncode != 0:
        return None
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _agents() -> List[str]:
    """Monitoring agents running now, by name."""
    proc = _probe("ps", "-eo", "pid,comm,args")
    if proc is None or proc.returncode != 0:
        return []
    mine = {os.getpid(), os.getppid()}
    out = []
    for line in proc.stdout.splitlines()[1:]:
        parts = line.split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit() or int(parts[0]) in mine:
            continue
        # the *program* must carry the name; an argument mentioning one is a
        # script, a search, or this very probe (measured: this sense's own
        # command line contains the agent's name and was reported as the agent)
        if not _AGENT_PATTERN.search(parts[1].lower()):
            continue
        out.append(line.strip()[:120])
    return out


def _public_peers(agents: List[str]) -> List[str]:
    """Public addresses a *monitoring agent* holds connections to.

    Scoped to the agent on purpose: a machine's whole connection list is a much
    broader disclosure than this sense claims to make, and listing every public
    peer drowns the one line that matters. `ss -tunp` names the owning process,
    so the filter is the process, not the destination.
    """
    proc = _probe("ss", "-tunp")
    if proc is None or proc.returncode != 0:
        return []
    running = " ".join(agents)
    peers = []
    for line in proc.stdout.splitlines():
        if not re.search(r"\sESTAB\s", line):
            continue
        owner = re.search(r'users:\(\("([^"]+)"', line)
        if not owner or not _AGENT_PATTERN.search(owner.group(1)):
            continue
        if owner.group(1) not in running and not _AGENT_PATTERN.search(running):
            continue
        parts = line.split()
        if len(parts) < 6:
            continue
        peer = parts[5]
        if _is_public(peer):
            peers.append(f"{owner.group(1)} {parts[4]} -> {peer}")
    return peers[:10]


def _is_public(addr: str) -> bool:
    host = addr.rsplit(":", 1)[0].strip("[]")
    if not host or host in ("*", "::"):
        return False
    if re.match(r"^\d+\.\d+\.\d+\.\d+$", host):
        octets = [int(p) for p in host.split(".")]
        if octets[0] in (10, 127) or (octets[0] == 192 and octets[1] == 168):
            return False
        if octets[0] == 172 and 16 <= octets[1] <= 31:
            return False
        if octets[0] == 169 and octets[1] == 254:
            return False
        return True
    if ":" in host:
        return not re.match(r"^(::1|fe80|fc|fd)", host, re.IGNORECASE)
    return False


#: Who legitimately reads raw input on an ordinary desktop - the same list the
#: anti-surveillance script uses, because without it every machine reports its
#: own compositor as a keylogger and the one real finding is the one nobody reads.
_KNOWN_XINPUT_DEVICES = re.compile(
    r"Virtual core (pointer|keyboard)|XTEST|Touchpad|TrackPoint|Video Bus|"
    r"Power Button|Sleep Button|AT Translated Set 2 keyboard|"
    r"ThinkPad Extra Buttons|Wacom|Logitech|Razer|DualShock|Maliit|"
    r"libinput Pointer", re.IGNORECASE)

#: Clipboard tools that are fine as a *pusher*; only a reader running in a
#: loop is worth naming.
_CLIP_TOOLS = ("xclip", "xsel", "clipman", "parcellite", "copyq", "greenclip",
               "cliphist", "wl-paste")

#: Remote-desktop products. Their presence is a fact, not an accusation - a
#: person may have installed one on purpose.
_REMOTE_CONTROL = ("vino-server", "x11vnc", "vncserver", "x0vnc", "teamviewer",
                   "anydesk", "rustdesk", "todesk", "sunlogin")

#: Capture is decided by the *arguments*, not the program name: `import` and
#: `ffmpeg` are ordinary tools, and matching the name alone makes this sense cry
#: wolf on any machine with ImageMagick installed.
_CAPTURE_TOOLS = ("ffmpeg", "scrot", "import", "grim", "obs", "x11grab")
_CAPTURE_ARGS = re.compile(
    r"x11grab|xfce4-screencast|-grab\b|screenrecord|record|screencast|"
    r"screencapture|capture=", re.IGNORECASE)

#: What a monitoring agent's store looks like, wherever it is. Derived from the
#: running process rather than hardcoded to one product's path: the first version
#: of this probe read `~/.config/workpuls-agent/storage`, which only ever covered
#: one agent and would report "no media" for every other one - the confident
#: wrong answer this sense exists to avoid.
_STORE_SUBDIRS = ("storage", "screenshots", "media", "capture", "records", "data")
#: Where named products keep their store, under the user's config directory.
#: Consulted only when nothing could be derived from the process itself.
_STORE_LAYOUTS = {"workpuls": "workpuls-agent/storage"}
_MEDIA_FILE = re.compile(r"\.(png|jpe?g|webm|mp4)$", re.IGNORECASE)
_MEDIA_SCAN_DEPTH = 4


def _clipboard_readers() -> "tuple[list[str], bool]":
    """Clipboard readers running in a loop. (found, could_ask)."""
    if shutil.which("ps") is None:
        return [], False
    proc = _probe("ps", "-eo", "args")
    if proc is None or proc.returncode != 0:
        return [], False
    hits = []
    for line in proc.stdout.splitlines()[1:]:
        low = line.lower()
        if not any(t in low for t in _CLIP_TOOLS):
            continue
        if re.search(r"-(o|-output|-watch|--loop)", low) or any(
                t in low for t in ("clipman", "parcellite", "copyq", "greenclip", "cliphist")):
            hits.append(line.strip()[:100])
    return hits, True


def _uinput_holder() -> "tuple[list[str], bool]":
    """Who holds /dev/uinput - the device that *creates* virtual input."""
    if not os.path.exists("/dev/uinput"):
        return [], True
    return _holders("/dev/uinput"), True


def _unknown_input_devices() -> "tuple[list[str], bool]":
    """Input devices that are not on the known-good list."""
    if shutil.which("xinput") is None:
        return [], False
    proc = _probe("xinput", "list")
    if proc is None or proc.returncode != 0:
        return [], False
    out = []
    for match in re.finditer(r"\s+([^\n]+?)\s+id=\d+", proc.stdout):
        name = match.group(1).strip()
        if name and not _KNOWN_XINPUT_DEVICES.search(name):
            out.append(name[:80])
    return out, True


def _remote_control() -> "tuple[list[str], bool]":
    proc = _probe("ps", "-eo", "pid,comm,args")
    if proc is None or proc.returncode != 0:
        return [], False
    out = []
    for line in proc.stdout.splitlines()[1:]:
        low = line.lower()
        if any(t in low for t in _REMOTE_CONTROL):
            out.append(line.strip()[:100])
    return out, True


def _audit_rules() -> "tuple[list[str], bool]":
    """auditd rules watching the filesystem."""
    if shutil.which("auditctl") is None:
        return [], False
    proc = _probe("auditctl", "-l")
    if proc is None or proc.returncode != 0:
        return [], False
    return [l.strip() for l in proc.stdout.splitlines() if l.strip()][:6], True


def _fingerprint() -> "tuple[list[str], bool]":
    proc = _probe("ps", "-eo", "pid,comm,args")
    if proc is None or proc.returncode != 0:
        return [], False
    out = []
    for line in proc.stdout.splitlines()[1:]:
        if re.search(r"\bfprintd(-verify|-list)?\b", line):
            out.append(line.strip()[:100])
    return out, True


def _screen_capture() -> "tuple[list[str], bool]":
    """Capture tools that are *actually capturing*, by their arguments."""
    proc = _probe("ps", "-eo", "pid,comm,args")
    if proc is None or proc.returncode != 0:
        return [], False
    out = []
    for line in proc.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        comm, args = parts[1], " ".join(parts[2:])
        if comm not in _CAPTURE_TOOLS and not any(
                re.search(rf"\b{re.escape(t)}\b", line) for t in _CAPTURE_TOOLS):
            continue
        if _CAPTURE_ARGS.search(args) or _AGENT_PATTERN.search(line):
            out.append(line.strip()[:100])
    return out, True


def _agent_stores() -> "list[pathlib.Path]":
    """Where each running monitoring agent keeps what it captures.

    Derived, not listed: the agent's own working directory and its config
    directory under the user's home, each looked at for a store-like subdirectory.
    An agent that keeps its store somewhere else entirely is simply not watched -
    and the sense says the store could not be found rather than reporting zero.
    """
    stores = []
    seen = set()
    for line in _agents():
        pid = line.split()[0] if line.split() else ""
        if not pid.isdigit():
            continue
        roots = []
        try:
            roots.append(pathlib.Path(f"/proc/{pid}/cwd").resolve())
        except OSError:
            pass
        # `~/.config/<app>/...`, where `<app>` is the agent's own app id - taken
        # from its command line rather than guessed from a product name.
        for token in line.split():
            if "." in token and "/" not in token and len(token) > 4:
                candidate = pathlib.Path.home() / ".config" / token
                if candidate not in roots:
                    roots.append(candidate)
        for root in roots:
            for name in _STORE_SUBDIRS:
                candidate = root / name
                if candidate.is_dir() and candidate not in seen:
                    seen.add(candidate)
                    stores.append(candidate)
    # A known layout as the *floor*, not the ceiling: an agent whose store cannot
    # be derived from its process would otherwise go silently unwatched, which is
    # the "no media found" reading as "nothing is captured" failure. The
    # derivation above is what makes this general; this only stops it being
    # narrower than the hardcoded version it replaced.
    agents = " ".join(_agents()).lower()
    for program, relative in _STORE_LAYOUTS.items():
        if program in agents:
            candidate = pathlib.Path.home() / ".config" / relative
            if candidate.is_dir() and candidate not in seen:
                seen.add(candidate)
                stores.append(candidate)
    return stores


def _media_files() -> "tuple[int, bool]":
    """How many media files sit in every monitoring agent's store.

    A count, not a judgment: growth between two readings is the signal, and the
    first reading only sets the baseline. (0, False) means no store could be
    found at all, which is *not* the same as "nothing is being captured".
    """
    stores = _agent_stores()
    if not stores:
        return 0, False

    def walk(folder: pathlib.Path, depth: int) -> int:
        if depth > _MEDIA_SCAN_DEPTH:
            return 0
        total = 0
        try:
            for entry in folder.iterdir():
                if entry.is_dir():
                    total += walk(entry, depth + 1)
                elif _MEDIA_FILE.search(entry.name):
                    total += 1
        except OSError:
            pass
        return total

    return sum(walk(store, 0) for store in stores), True


#: The media count carries between calls: growth is the finding, so the first
#: reading is only a baseline and must not read as "nothing is being captured".
_MEDIA_BASELINE: "list[int | None]" = [None]


def _unanswerable() -> str:
    """The one sentence for every probe that could not run."""
    return ("Who is capturing on this machine is UNKNOWN: the tools to ask are "
            "not all here, and an empty answer would read as a clean machine.")


def _run(arguments: dict):
    if shutil.which("fuser") is None:
        return _unanswerable()

    unknowns = []

    cameras = []
    try:
        import os
        for entry in sorted(os.listdir("/dev")):
            if not _VIDEO_GLOB.match(entry):
                continue
            held = _holders(f"/dev/{entry}")
            if held:
                cameras.append(f"{entry} held by {held[0]}")
    except OSError as exc:
        unknowns.append(f"/dev could not be listed ({exc})")
    if not cameras:
        cameras = ["no camera device is held open"]

    raw_input = []
    try:
        import os
        for entry in sorted(os.listdir("/dev/input")):
            if not _INPUT_GLOB.match(entry):
                continue
            for holder in _holders(f"/dev/input/{entry}"):
                if not _LEGIT_INPUT_HOLDERS.search(holder):
                    raw_input.append(f"{entry} read by {holder}")
    except OSError:
        unknowns.append("/dev/input could not be listed")

    streams = _audio_streams()
    if streams is None:
        unknowns.append("no audio server answered, so a live microphone is unknown")
        audio = ["whether the microphone is capturing could not be determined"]
    else:
        audio = [f"audio stream: {s}" for s in streams] or ["no audio stream is capturing"]

    agents = _agents()
    peers = _public_peers(agents)

    # The eight further channels. Each returns (found, could_ask): a probe whose
    # tool is absent must land in `unknowns`, never in an empty list that reads
    # as a clean machine.
    channels = []
    readers, asked = _clipboard_readers()
    if not asked:
        unknowns.append("clipboard readers could not be listed (no ps)")
    else:
        channels += [f"clipboard reader: {r}" for r in readers]
    uinput, asked = _uinput_holder()
    if not asked:
        unknowns.append("/dev/uinput could not be checked")
    else:
        channels += [f"virtual-input creator held by {u}" for u in uinput]
    unknown_dev, asked = _unknown_input_devices()
    if not asked:
        unknowns.append("input devices could not be listed (no xinput)")
    else:
        channels += [f"unexpected input device: {d}" for d in unknown_dev]
    remote, asked = _remote_control()
    if not asked:
        unknowns.append("remote-control processes could not be listed")
    else:
        channels += [f"remote control: {r}" for r in remote]
    rules, asked = _audit_rules()
    if not asked:
        unknowns.append("auditd rules could not be read (no auditctl, or not root)")
    else:
        channels += [f"audit rule: {r}" for r in rules]
    prints, asked = _fingerprint()
    if not asked:
        unknowns.append("fingerprint processes could not be listed")
    else:
        channels += [f"fingerprint reader in use: {f}" for f in prints]
    capturing, asked = _screen_capture()
    if not asked:
        unknowns.append("screen-capture processes could not be listed")
    else:
        channels += [f"screen capture active: {c}" for c in capturing]
    stores = _agent_stores()
    media, asked = _media_files()
    if not stores:
        unknowns.append("no monitoring agent store could be found, so whether "
                        "it is capturing media is unknown")
    elif not asked:
        unknowns.append("the monitoring agent's store could not be counted")
    else:
        if _MEDIA_BASELINE[0] is not None and media > _MEDIA_BASELINE[0]:
            channels.append(f"new media written by the monitoring store "
                            f"({media} files, was {_MEDIA_BASELINE[0]})")
        _MEDIA_BASELINE[0] = media
    if agents and not peers:
        # `ss -tunp` names the owning process only for this user's own sockets,
        # so an agent running as another user shows its connections with nobody
        # attached. Saying that is better than an empty list that reads as "it
        # uploads nothing".
        unknowns.append("the agent's connections could not be attributed to it "
                        "(ss names a socket's owner only for this user's own), so "
                        "whether it is uploading is unknown")

    # The headline is the strongest thing actually *found*, and only UNKNOWN
    # when nothing could be determined. An agent seen by name is a finding even
    # when its connections cannot be attributed to it - putting UNKNOWN first
    # made the one real answer read as "could not ask".
    if agents:
        conclusions = ["A monitoring agent is running on this machine."]
    elif raw_input:
        conclusions = ["Something is reading raw input - see the list."]
    elif channels:
        conclusions = ["Something is watching or capturing on this machine - see the list."]
    elif cameras[0].startswith("video") or audio[0].startswith("audio stream:"):
        conclusions = ["The camera or the microphone is in use."]
    elif unknowns:
        conclusions = [_unanswerable()]
    else:
        conclusions = ["Nothing is capturing: no camera held, no audio stream, "
                       "no unexpected raw-input reader, and no monitoring agent running."]

    lines = list(conclusions) + cameras + audio + raw_input + channels
    lines += [f"monitoring agent running: {a}" for a in agents]
    lines += [f"public connection: {p}" for p in peers]
    lines += [f"unknown: {u}" for u in unknowns]
    return "\n".join(lines)


_SENSE = Sense(
    name="monitoring",
    kind=KIND,
    sensitivity=SENSITIVITY,
    schema=_SCHEMA,
    run=_run,
    ttl_seconds=_TTL_SECONDS,
    poll_interval=_POLL_INTERVAL,
)

SENSES = [_SENSE]
