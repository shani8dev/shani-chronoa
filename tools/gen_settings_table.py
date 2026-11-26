"""Generate the shipped settings table from the gschema and the code's own gates.

One table, one source of truth: types, defaults and summaries come from
``org.shani.chronoa.gschema.xml``; labels come from ``capabilities.GATE_NAMES``
where a person-facing name already exists; flags come from the same consent
sets the app enforces. The person's default decisions are applied here, after
the schema is read, so the generated file is the deliverable rather than a
second copy of the decision.

Run: python3 tools/gen_settings_table.py

Standard library only. Importing ``config`` would pull in Gio and libsecret,
so ``API_KEY_SETTINGS`` is read out of its source with ``ast`` instead. A
generator that needed a desktop session to run would be a generator nobody
could re-run.
"""

from __future__ import annotations

import ast
import json
import pathlib
import sys
import xml.etree.ElementTree as ET

REPO = pathlib.Path(__file__).resolve().parent.parent
SCHEMA = REPO / "usr/share/glib-2.0/schemas/org.shani.chronoa.gschema.xml"
CONFIG = REPO / "usr/lib/shani-chronoa/shani_chronoa/config.py"
OUTPUT = REPO / "usr/share/shani-chronoa/settings.json"
sys.path.insert(0, str(REPO / "usr/lib/shani-chronoa"))

from shani_chronoa import capabilities as C  # noqa: E402

# The person's decision, expressed once. Sense names below are expanded to
# their consent keys; everything else is already a key. Every name is checked
# against the schema, so a rename cannot silently stop applying a decision.
DEFAULT_ON_SENSES = """
hwmon printing polkitpolicy privilege dnsresolvers wirelesslink labnetworks
idle accessibility bluetooth modelfit capture monitoring containers listeners
stale git sessions
failure containerrun screenlock powerstate netstate usbplug btconnect schedule
sleepwake audiodevice journalmatch dbusprop calendar phone fswatch expiry
unithealth
""".split()
DEFAULT_ON_KEYS = """
trigger-control-enabled ocr-sense-enabled filesystem-sense-enabled
mcp-client-enabled cleanup-enabled mascot-enabled organ-audible-enabled
sandbox-seccomp-enabled
file-delete-enabled process-kill-enabled power-control-enabled
window-close-enabled service-control-enabled mount-control-enabled
trash-empty-enabled bulk-edit-enabled run-command-enabled
phone-control-enabled phone-messages-read-enabled landline-enabled
watch-nowplaying-enabled watch-weather-enabled email-read-enabled
""".split()
DEFAULT_OFF_KEYS = """
cloud-stt-enabled cloud-tts-enabled web-sense-enabled email-send-enabled
phone-messages-send-enabled git-push-enabled voice-clone-enabled
vision-sense-enabled hearing-sense-enabled wake-word-enabled
heard-sound-sense-enabled sound-sense-enabled rfsense-sense-enabled
location-sense-enabled meeting-enabled camera-sense-enabled
packet-capture-enabled i2c-write-enabled
""".split()
REMOVED_KEYS = """
lan-bridge-enabled smart-sense-enabled cooling-sense-enabled
monitors-sense-enabled link-sense-enabled contention-sense-enabled
thermal-sense-enabled camera-sense-enabled
""".split()

# These are on by the person's decision *and* ask every time. Their labels say
# so, because a switch that reads "on" must not look like a standing licence.
STILL_ASKS = """
file-delete-enabled process-kill-enabled power-control-enabled
window-close-enabled service-control-enabled mount-control-enabled
trash-empty-enabled bulk-edit-enabled run-command-enabled
""".split()

# A retired key's grant is migrated by config._SENSE_CONSENT_ALIASES in the
# parent work; this generator only drops the keys, never rewrites old values.


def _api_key_settings() -> dict[str, str]:
    """Read ``config.API_KEY_SETTINGS`` without importing ``config``."""
    tree = ast.parse(CONFIG.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "API_KEY_SETTINGS" for t in node.targets
        ):
            return dict(ast.literal_eval(node.value))
    raise RuntimeError("config.py has no API_KEY_SETTINGS assignment")


def _default(element: ET.Element | None, kind: str):
    text = " ".join((element.text or "").split()) if element is not None else ""
    if kind == "b":
        return text.lower() == "true"
    if kind == "d":
        return float(text)
    return text[1:-1] if len(text) >= 2 and text[0] == text[-1] == "'" else text


def _label(key: str) -> str:
    label = C.GATE_NAMES.get(key) or key.replace("-", " ").capitalize()
    if key in STILL_ASKS:
        label += " — still asks before each action"
    return label


def build() -> dict:
    api_keys = _api_key_settings()
    secret_keys = set(api_keys.values())
    consent_keys = (
        set(C.GATED.values()) | C.DESTRUCTIVE_CONSENT_KEYS | C.NEVER_DEFAULTED_ON
    )
    on = {f"{name}-sense-enabled" for name in DEFAULT_ON_SENSES} | set(DEFAULT_ON_KEYS)
    off = set(DEFAULT_OFF_KEYS)
    removed = set(REMOVED_KEYS)
    # `camera-sense-enabled` is both off and removed: removal wins, because a
    # retired key cannot carry a default at all. Only on/off can contradict.
    overlap = on & off
    if overlap:
        raise RuntimeError(f"contradictory default decisions: {sorted(overlap)}")

    root = ET.parse(SCHEMA).getroot()
    settings: dict[str, dict] = {}
    for element in root.iter("key"):
        key = element.get("name")
        if not key or key in removed:
            continue
        kind = element.get("type") or "s"
        summary = element.find("summary")
        row = {
            "type": kind,
            "default": _default(element.find("default"), kind),
            "label": _label(key),
            "summary": " ".join((summary.text or "").split())
            if summary is not None
            else "",
            "consent": key in consent_keys,
            "destructive": key in C.DESTRUCTIVE_CONSENT_KEYS,
            # Mirrors config._is_secret's key rule. The keyring environment
            # switch is a runtime concern, not a property of the setting.
            "secret": key in secret_keys or key.endswith("-api-key"),
        }
        if key in on:
            row["default"] = True
        elif key in off:
            row["default"] = False
        settings[key] = row

    declared = {e.get("name") for e in root.iter("key")}
    missing = (on | off | removed) - declared
    if missing:
        raise RuntimeError(f"decision names no schema key: {sorted(missing)}")
    return {"settings": settings, "version": 1}


def main() -> None:
    text = json.dumps(build(), ensure_ascii=False, indent=2) + "\n"
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    if OUTPUT.exists() and OUTPUT.read_text(encoding="utf-8") == text:
        return
    OUTPUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(REPO)}")


if __name__ == "__main__":
    main()
