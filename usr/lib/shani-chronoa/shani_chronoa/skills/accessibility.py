"""Skill: turn accessibility features on or off by voice.

The screen reader (Orca), the on-screen keyboard, the magnifier, large text,
high contrast, reduced animation and sticky keys - set where the desktop
itself keeps them (GNOME's own settings, so its Accessibility menu and
Settings show the same state). On another desktop the screen reader still
works: Orca is started or stopped directly. Nothing leaves the machine.
"""

import shutil
import subprocess

from shani_chronoa.skills import Skill

#: feature -> (schema, key, value when on, value when off)
GNOME = {
    "screen_reader": ("org.gnome.desktop.a11y.applications", "screen-reader-enabled", "true", "false"),
    "on_screen_keyboard": ("org.gnome.desktop.a11y.applications", "screen-keyboard-enabled", "true", "false"),
    "magnifier": ("org.gnome.desktop.a11y.applications", "screen-magnifier-enabled", "true", "false"),
    "large_text": ("org.gnome.desktop.interface", "text-scaling-factor", "1.25", "1.0"),
    "high_contrast": ("org.gnome.desktop.a11y.interface", "high-contrast", "true", "false"),
    "reduce_motion": ("org.gnome.desktop.interface", "enable-animations", "false", "true"),
    "sticky_keys": ("org.gnome.desktop.a11y.keyboard", "stickykeys-enable", "true", "false"),
}

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "accessibility",
        "description": "Turn an accessibility feature on or off, or report them all: screen "
                       "reader, on-screen keyboard, magnifier (zoom), large text, high contrast, "
                       "reduce motion (animations), sticky keys.",
        "parameters": {"type": "object", "properties": {
            "feature": {"type": "string", "enum": sorted(GNOME) + ["status"]},
            "enabled": {"type": "boolean", "description": "On (true) or off (false)."},
        }, "required": ["feature"]},
    },
}


def _gsettings(*args):
    return subprocess.run(["gsettings", *args], capture_output=True, text=True, timeout=5)


def _has_schema(schema: str) -> bool:
    return bool(shutil.which("gsettings")) and _gsettings("list-keys", schema).returncode == 0


def _state(feature: str) -> str:
    schema, key, on, _off = GNOME[feature]
    value = _gsettings("get", schema, key).stdout.strip()
    return "on" if value == on else "off"


def _run(arguments: dict) -> str:
    feature = (arguments.get("feature") or "").strip()
    if feature not in GNOME and feature != "status":
        return f"Unknown feature '{feature}'. Choose from: {', '.join(sorted(GNOME))}."
    gnome = _has_schema("org.gnome.desktop.a11y.applications")
    if feature == "status":
        if not gnome:
            return "This desktop does not keep GNOME's accessibility settings; only the screen reader can be switched here."
        return "; ".join(f"{f.replace('_', ' ')}: {_state(f)}" for f in sorted(GNOME)) + "."
    enabled = arguments.get("enabled")
    if enabled is None:
        return f"Turn {feature.replace('_', ' ')} on or off?"
    if gnome:
        schema, key, on, off = GNOME[feature]
        r = _gsettings("set", schema, key, on if enabled else off)
        if r.returncode != 0:
            return f"Could not change {feature.replace('_', ' ')}: {r.stderr.strip()[:150]}"
        return f"{feature.replace('_', ' ').capitalize()} is now {'on' if enabled else 'off'}."
    if feature == "screen_reader" and shutil.which("orca"):
        if enabled:
            subprocess.Popen(["orca", "--replace"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
            return "Screen reader (Orca) started."
        subprocess.run(["pkill", "-x", "orca"], capture_output=True, timeout=5)
        return "Screen reader (Orca) stopped."
    return (f"On this desktop {feature.replace('_', ' ')} is set in its own system settings; "
            "I can only start or stop the screen reader here.")


SKILLS = [Skill(name="accessibility", schema=_SCHEMA, run=_run)]
