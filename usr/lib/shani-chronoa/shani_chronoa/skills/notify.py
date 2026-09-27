"""Skill: send a desktop notification via notify-send.

`notify-send` is the D-Bus-free route to libnotify and is a declared
dependency of this package, so it is expected to be present on any Shanios
desktop. The skill shells out to it directly rather than through a
generic "run a command" path: the binary, the urgency and the timeout are
fixed, and the only user-supplied value is the notification text, which
travels as a single argv element - no shell, no interpolation.

Notifications are opt-in via the existing `notification-enabled` key, the
same gate `timer.py` already honours, so a machine with notifications
turned off gets a refusal rather than a pop-up the user did not ask for.
"""

import shutil
import subprocess

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_NOTIFY_BINARY = "notify-send"
_TIMEOUT_SECONDS = 10


def _run(arguments: dict) -> str:
    summary = arguments.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return "Invalid summary: expected a non-empty string."
    summary = summary.strip()

    body = arguments.get("body")
    if body is None:
        body = ""
    if not isinstance(body, str):
        return "Invalid body: expected a string."

    if not ChronoaConfig().notification_enabled:
        return (
            "Notifications are turned off (enable 'notification-enabled'); "
            "no notification was sent."
        )

    if shutil.which(_NOTIFY_BINARY) is None:
        return (
            f"Could not send notification: {_NOTIFY_BINARY} is not installed. "
            "Install libnotify (notify-send) to enable desktop notifications."
        )

    cmd = [_NOTIFY_BINARY, summary]
    if body:
        cmd.append(body)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS)
    except FileNotFoundError:
        return f"Could not send notification: {_NOTIFY_BINARY} disappeared."
    except Exception as e:  # noqa: BLE001 - surface any failure as a message
        return f"Could not send notification: {e}"

    if result.returncode != 0:
        return f"Could not send notification: {result.stderr.strip() or result.stdout.strip()}"
    return f"Notification sent: {summary}."


_SCHEMA = {
    "type": "function",
    "function": {
        "name": "notify",
        "description": (
            "Send a desktop notification to the user. Honours the "
            "'notification-enabled' setting, so it refuses quietly when "
            "notifications are turned off."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "The notification title (required).",
                },
                "body": {
                    "type": "string",
                    "description": "Optional notification body text.",
                },
            },
            "required": ["summary"],
        },
    },
}

SKILLS = [Skill(name="notify", schema=_SCHEMA, run=_run)]