"""Short sounds for "I'm listening" and "I didn't catch that" - so a person who is not looking can tell.

sayri plays a cue when the microphone opens and while it works
(`core.py:202-213`); hands-free and low-vision users otherwise cannot know the
mic is open without looking at the orb. These are the freedesktop sound
theme's own files, which both images ship (`sound-theme-freedesktop`), played
with pw-play; off unless `sound-cues-enabled` is set. Never blocks.
"""

from __future__ import annotations

import os
import shutil
import subprocess

_THEME = "/usr/share/sounds/freedesktop/stereo"
CUES = {
    "listening": "message-new-instant.oga",
    "not-understood": "dialog-information.oga",
    "done": "complete.oga",
}


def path_for(name: str) -> str:
    file = CUES.get(name, "")
    full = os.path.join(_THEME, file) if file else ""
    return full if full and os.path.isfile(full) else ""


def play(name: str, config=None) -> bool:
    """Play cue `name` in the background; False when cues are off or the sound is not installed."""
    if config is None:
        from shani_chronoa.config import ChronoaConfig
        config = ChronoaConfig()
    if not config.get_bool("sound-cues-enabled", False):
        return False
    sound = path_for(name)
    player = shutil.which("pw-play") or shutil.which("paplay")
    if not sound or not player:
        return False
    try:
        subprocess.Popen([player, sound], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False
    return True
