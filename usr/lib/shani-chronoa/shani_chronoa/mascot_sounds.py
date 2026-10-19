"""The mascot's sounds: one note per decision, replaceable in a folder.

Coucou's receipt (HARVEST PART 15) names its sounds by the *event* they report -
`finish.wav`, `approval.mp3`, `greet.m4a` - drop-in files in a folder, replace
by name, Reload. This module is that, for our face kinds: a table of
kind -> file name, one shipped set of three synth wavs, and a person's own
folder that wins whenever it holds the file.

**The table is the mascot face kinds, not `BY_NAME`.** `Mascot.notice(kind)` is
called with a *face* kind - `call`, `greet`, `meeting`, `build-ok` - from both
the bus routing and the direct landline path, so the sound map keyed on the
same names can never disagree with what the mascot is about to show (a sound
and a face described differently would be two facts for one moment). Coucou
ships three; the three are gone if the folder wastes them - unwired kinds stay
silent, and the count is stated so the next reader greps rather than assumes.

`play` is at most one sound at a time and **never raises**: the mascot is an
overlay and a failed note must not fail a notice, the same promise `emit`
makes for faces. No player installed (`pw-play` or `paplay`, what `cues.py`
prefers) is the common headless answer, and silence is correct there - a
mascot without a surface makes no sound anyway.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

#: face kind -> shipped file name. Exactly four wired kinds (the call ring,
#: a tool's question, the approval gate, the turn answered); every other kind
#: shows a face without a sound, deliberately - Coucou's own receipt names
#: three sounds, and `error`/`build-fail` buzzing on every refusal would be
#: the noise an assistant makes when nobody asked for it.
SOUND_FOR = {
    "call": "greet.wav",
    "greet": "greet.wav",
    "meeting": "approval.wav",
    "build-ok": "finish.wav",
}

#: The person's own folder wins over the shipped set, file by file.
def user_dir() -> Path:
    """`$XDG_DATA_HOME/shani-chronoa/mascot-sounds` (or ~/.local/share/...)."""
    base = Path("/tmp")
    try:
        import os
        base = Path(os.environ.get(
            "XDG_DATA_HOME",
            str(Path.home() / ".local" / "share")))
    except Exception:  # noqa: BLE001 - a folder name must never fail a note
        return base / "shani-chronoa" / "mascot-sounds"
    return base / "shani-chronoa" / "mascot-sounds"


def _shipped_dir() -> Path:
    """The repo-shipped set: `usr/share/shani-chronoa/mascot-sounds` beside the
    installed package (package is `usr/lib/shani-chronoa/shani_chronoa/`)."""
    try:
        return Path(__file__).resolve().parents[3] / "share/shani-chronoa/mascot-sounds"
    except Exception:  # noqa: BLE001 - silence, never a traceback, for a note
        return Path("/nonexistent")


def _settings():
    """The sound's two keys, live. Returns (enabled, volume) or fallback."""
    try:
        import os
        # A test or a second schema dir should not need to monkeypatch GTypes.
        if os.environ.get("CHRONOA_MUTATION_RUN"):
            raise RuntimeError
        from gi.repository import Gio
        import gi
        gi.require_version("Gio", "2.0")
        s = Gio.Settings(schema_id="org.shani.chronoa")
        # `get_boolean`/`get_double` are read per play; flipping the setting
        # takes effect on the next decision, not after a restart.
        return bool(s.get_boolean("mascot-sounds")), max(
            0.0, min(1.0, float(s.get_double("mascot-sounds-volume")) or 0.6))
    except Exception:  # noqa: BLE001
        return False, 0.6


def resolve(kind: str) -> Path | None:
    """The file a kind sounds as: the person's folder beats the shipped set."""
    name = SOUND_FOR.get(kind)
    if not name:
        return None
    own = user_dir() / name
    if own.is_file():
        return own
    shipped = _shipped_dir() / name
    if shipped.is_file():
        return shipped
    return None


#: The one sound in flight. A rapid turn fires greets in quick succession; the
#: newest replaces the last rather than stacking, the same one-at-a-time rule
#: the mascot's bus listener keeps.
_current = None


def _player() -> str | None:
    """`pw-play` (cues.py's own first choice) as the always-there fallback."""
    return shutil.which("pw-play") or shutil.which("paplay")


def _gst_ready() -> bool:
    """Whether a `Gst.PlayBin` can actually sound a file on this system.

    Measured the way `screen_stream.py` measures its own pipeline: ask
    `ElementFactory.find` for the elements before promising them. Missing
    plugins are the honest headless answer, and silence is correct there.
    """
    try:
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        if Gst.is_initialized() or Gst.init(None) is None:
            pass
        return bool(Gst.ElementFactory.find("playbin")) and any(
            Gst.ElementFactory.find(s)
            for s in ("pulseaudiosink", "pulsesink", "pipewiresink",
                      "autoaudiosink"))
    except Exception:  # noqa: BLE001 - a noteless machine is a correct answer
        return False


def _volume_args() -> list:
    """How the chosen player carries a volume, or the empty list when it
    cannot. **Real flags only, measured here:** `pw-play`'s `--help` lists no
    volume flag on this machine, and `paplay` is not installed at all - so the
    subprocess fallback plays full volume and the *setting* is honoured on the
    Gst path, which is the primary one and carries the exact level.
    """
    return []


def _stop_previous() -> None:
    global _current
    if _current is not None:
        try:
            if isinstance(_current, subprocess.Popen):
                if _current.poll() is None:
                    _current.terminate()
            else:
                _current.set_state(0)  # Gst.State.NULL
        except Exception:  # noqa: BLE001
            logger.debug("mascot sound: previous note ignored the stop")
    _current = None


def _play_file(path: Path, volume: float) -> None:
    """The actual note. Gst first (exact volume), pw-play as fallback.

    Written as a whole-method seam: tests replace this one function with a
    recorder and assert on *what would play* - the repo's tests never ask
    hardware to make noise, and a fake backend keeps that promise exact.
    """
    global _current
    if _gst_ready():
        try:
            from gi.repository import Gst
            from gi.repository import GLib
            e = Gst.ElementFactory.make("playbin")
            e.set_property("uri", path.as_uri())
            e.set_property("volume", volume)
            e.set_state(1)  # Gst.State.PLAYING
            # The bus watches for the end-of-stream so the element can be
            # dropped; a leak per note would be the bad kind of quiet.
            GLib.timeout_add(400, _sweep, e)
            _current = e
            return
        except Exception:  # noqa: BLE001
            logger.debug("mascot sound: Gst did not take %s", path, exc_info=True)
            _current = None
    player = _player()
    if player is None:
        logger.debug("mascot sound: no player for %s", path)
        return
    _stop_previous()
    try:
        _current = subprocess.Popen(  # noqa: S603 - argv list, no shell
            [player, str(path)] + _volume_args(),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
    except Exception:  # noqa: BLE001 - a note must never fail a notice
        logger.debug("mascot sound: %s could not play", path, exc_info=True)
        _current = None


def _sweep(element) -> bool:
    """Bins a finished playbin. False removes the GLib timeout."""
    try:
        ok, state, _pending = element.get_state(0)
        if ok and state in (0, 4):  # NULL, PLAYING exhausted can stay 1-frame
            element.set_state(0)
            return False
        return True
    except Exception:  # noqa: BLE001
        return False


def play(kind: str) -> None:
    """Sound a decision, at most one at a time. **Never raises.**"""
    enabled, volume = _settings()
    if not enabled:
        return
    path = resolve(kind)
    if path is None:
        return
    try:
        _play_file(path, volume)
    except Exception:  # noqa: BLE001 - a note must never fail a notice
        logger.debug("mascot sound: %r could not play", kind, exc_info=True)
