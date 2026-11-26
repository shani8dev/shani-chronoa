"""Health: the tool-call audit trail and a bounded probe of each subsystem.

`activity` reads the durable log files and the live tracker through
`tool_tracking` - it never re-parses a log format of its own. `probe` asks each
engine the same question through the module that would make it true, with no
GTK, no bus call and no subprocess. A missing binary is a named fact, and a
probe that raises becomes `could not determine`, never `working`.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime

from shani_chronoa import capabilities
from shani_chronoa import config as config_mod
from shani_chronoa import local_llm, stt, tool_tracking, tts

logger = logging.getLogger(__name__)

WORKING = "working"
NOT_WORKING = "not working"
UNKNOWN = "could not determine"
_STATUSES = (WORKING, NOT_WORKING, UNKNOWN)


def _clip(text, limit: int = 180) -> str:
    flat = " ".join(str(text if text is not None else "").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def _epoch(value) -> float:
    """A timestamp as epoch seconds, so `since` can be a number or an ISO string."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _call_row(record) -> dict:
    """One audit row. Args and results are never returned - they can carry secrets."""
    if isinstance(record, dict):
        data = record
    elif callable(getattr(record, "to_dict", None)):
        data = record.to_dict()
    else:
        fields = (
            "timestamp",
            "tool_name",
            "duration_ms",
            "origin",
            "verdict",
            "evidence",
            "undone",
        )
        data = {key: getattr(record, key, None) for key in fields}
    return {
        "timestamp": str(data.get("timestamp") or ""),
        "tool_name": str(data.get("tool_name") or ""),
        "duration_ms": data.get("duration_ms") or 0,
        "origin": str(data.get("origin") or ""),
        "verdict": data.get("verdict"),
        "evidence": str(data.get("evidence") or ""),
        "undone": bool(data.get("undone")),
    }


def activity(since=0, limit=100) -> dict:
    """Recent tool calls, newest first, with secrets left out."""
    records = []
    for path in tool_tracking.log_files():
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:  # noqa: PERF203 - an unreadable chunk costs one row
            logger.debug("could not read the tool log %s", path, exc_info=True)
            continue
        for line in lines:
            try:
                parsed = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
    try:
        records.extend(tool_tracking.ToolTracker().get_calls())
    except Exception:  # noqa: BLE001 - a tracker that cannot start costs only live rows
        logger.debug("could not read the live tool tracker", exc_info=True)

    threshold = _epoch(since) if since else 0.0
    found = []
    seen = set()
    for record in records:
        row = _call_row(record)
        key = (row["timestamp"], row["tool_name"], row["duration_ms"], row["origin"])
        if key in seen:  # the live copy of a durable call is the same call
            continue
        seen.add(key)
        at = _epoch(row["timestamp"])
        if threshold and at < threshold:
            continue
        found.append((at, row))
    found.sort(key=lambda item: item[0], reverse=True)
    cap = max(0, int(limit))
    return {"calls": [row for _, row in found[:cap]], "truncated": len(found) > cap}


def _binary_probe(*names: str):
    def check():
        installed = [name for name in names if shutil.which(name)]
        if installed:
            return WORKING, f"{installed[0]} is on PATH"
        return NOT_WORKING, f"none of {', '.join(names)} is on PATH"

    return check


def _local_model():
    keys = local_llm.installed()
    if keys:
        return WORKING, f"{len(keys)} local model file(s) are installed"
    return NOT_WORKING, "no local model file is installed, so nothing serves replies"


def _speech_in():
    model = stt.installed_model("base")
    if stt.WhisperSTT(model=model).is_available():
        return WORKING, f"whisper.cpp can transcribe with the {model} model"
    return NOT_WORKING, "neither a whisper.cpp binary nor a model file is usable here"


def _speech_out():
    chosen = tts.PiperTTS().engine()
    if chosen:
        return WORKING, f"a reply would be spoken by {chosen}"
    return NOT_WORKING, "no engine in the speech chain can speak here"


def _settings():
    config = config_mod.ChronoaConfig()
    if config.setting_readable("privacy-mode"):
        return WORKING, "the settings store can be read"
    return NOT_WORKING, "a schema setting could not be read from the settings store"


def _capabilities():
    items, error = capabilities.live_capabilities()
    if error:
        return NOT_WORKING, f"the skill registry could not be read: {_clip(error)}"
    if not items:
        return NOT_WORKING, "no capabilities are available from the skill registry"
    return WORKING, f"{len(items)} capabilities are available"


def _checks():
    """Resolved on every call so a caller can stand in for any one probe."""
    return (
        ("Local model", _local_model),
        ("Speech in", _speech_in),
        ("Speech out", _speech_out),
        ("Audio capture", _binary_probe("pw-record", "arecord")),
        ("Audio playback", _binary_probe("pw-play", "aplay")),
        ("Speech floor (espeak-ng)", _binary_probe("espeak-ng")),
        ("Settings store", _settings),
        ("Capabilities", _capabilities),
    )


def probe() -> dict:
    """One bounded status per subsystem. No GTK, no bus, no subprocess."""
    checks = []
    for title, run in _checks():
        try:
            answered = run()
            status, detail = answered[0], answered[1]
        except Exception as exc:  # noqa: BLE001 - a failed probe is never working
            logger.debug("health probe %s failed", title, exc_info=True)
            status = UNKNOWN
            detail = f"the probe did not finish ({_clip(exc)})"
        if status not in _STATUSES:
            status = UNKNOWN
            detail = f"the probe answered with a status this surface does not use ({status!r})"
        checks.append({"title": title, "status": status, "detail": str(detail)})
    return {"checks": checks}
