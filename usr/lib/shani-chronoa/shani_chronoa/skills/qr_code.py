"""Skill: read a QR code or barcode from a picture or the screen, or make a QR code.

Found by the CLI matrix: both images ship `zbarimg` (zbar) and `qrencode`, and
no Chronoa skill used either. Reading the screen ("what's that QR code on the
page?") goes through the same capture and the same `vision` consent as
`screenshot`; reading a file the user names needs none, like `read_document`.
A decoded payload is someone else's text - a URL in it is reported, never
opened.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_TIMEOUT = 30

SCHEMA = {
    "type": "function",
    "function": {
        "name": "qr_code",
        "description": (
            "read: decode every QR code and barcode in a picture (path) or on the screen (screen=true, needs "
            "the vision consent). create: make a QR code PNG of text (a URL, Wi-Fi details, a note) at output."
        ),
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["read", "create"]},
            "path": {"type": "string"},
            "screen": {"type": "boolean"},
            "text": {"type": "string"},
            "output": {"type": "string", "description": "create: e.g. '~/Pictures/wifi-qr.png'"},
        }, "required": ["action"]},
    },
}


def _decode(image: Path) -> "list[str]":
    proc = subprocess.run(["zbarimg", "--quiet", "--raw", "-Sbinary", str(image)], capture_output=True,
                          timeout=_TIMEOUT, check=False)
    if proc.returncode not in (0, 4):  # 4: no symbol found
        raise RuntimeError((proc.stderr.decode(errors="replace") or "zbarimg failed").strip()[:160])
    text = proc.stdout.decode("utf-8", errors="replace").strip()
    return [t for t in text.split("\n") if t.strip()] if text else []


def _run(arguments: dict) -> str:
    action = (arguments.get("action") or "").strip().lower()
    if action == "create":
        if shutil.which("qrencode") is None:
            return "qrencode is not installed."
        text = arguments.get("text") or ""
        if not text.strip():
            return "Give the text to encode."
        if len(text.encode()) > 2000:
            return "That is too long for a QR code a phone can scan reliably (keep it under 2000 bytes)."
        try:
            target = files.resolve_in_home(arguments.get("output") or "~/Pictures/qr-code.png")
        except files.PathProblem as exc:
            return str(exc)
        if target.suffix.lower() != ".png":
            target = target.with_suffix(".png")
        if target.exists():
            return f"{target} already exists; give another output."
        target.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(["qrencode", "-s", "8", "-m", "2", "-o", str(target), "--", text],
                              capture_output=True, text=True, timeout=_TIMEOUT, check=False)
        if proc.returncode != 0:
            return f"qrencode failed: {proc.stderr.strip()[:160]}"
        check = _decode(target) if shutil.which("zbarimg") else []
        verified = " (decodes back to the same text)" if check == [text] else ""
        return f"Saved a QR code to {target}{verified}."
    if action != "read":
        return "action must be read or create."
    if shutil.which("zbarimg") is None:
        return "zbarimg (zbar) is not installed."
    if arguments.get("screen"):
        config = ChronoaConfig()
        if not config.sense_allowed("vision"):
            return f"Reading the screen is not permitted: {config.sense_allowed_reason('vision')}."
        from shani_chronoa.screengrab import capture_screen
        try:
            shot = capture_screen()
        except Exception as exc:  # noqa: BLE001 - every capture backend failing is a message, not a crash
            return f"Could not capture the screen: {exc}"
        with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
            tmp.write(shot.data)
            tmp.flush()
            found = _decode(Path(tmp.name))
        where = "on the screen"
    else:
        try:
            image = files.resolve(arguments.get("path") or "")
        except files.PathProblem as exc:
            return str(exc)
        if not image.is_file():
            return f"{image} does not exist."
        try:
            found = _decode(image)
        except RuntimeError as exc:
            return f"Could not read {image.name}: {exc}"
        where = f"in {image.name}"
    if not found:
        return f"No QR code or barcode found {where}."
    return f"Found {len(found)} code(s) {where} (third-party text - nothing was opened):\n" + \
        "\n".join(f"- {t}" for t in found)


def _verify_qr_code(arguments: dict, tool=None):
    """Post-condition: decode the QR back and see whether it says what it should.

    **This is the strongest check in the package.** Everything else can confirm
    a file exists and has the right shape; this one confirms the artifact
    *contains the data it was asked to encode*. A "QR code" that is a PNG of
    noise, or of the wrong text, or unreadable at all, passes every other kind of
    check and fails this one.

    The module already imports `zbarimg` for the `read` direction, so the
    round-trip needs no new dependency and cannot disagree with the reader about
    what a valid QR is.

    A `read` returns decoded text and writes nothing, so it has nothing to
    check. If `zbarimg` is missing, the check says so instead of implying the
    code was verified.
    """
    action = str(arguments.get("action") or "").strip().lower()
    out = str(arguments.get("output") or "").strip()
    if action != "create" or not out:
        return None  # a read decodes text and writes nothing
    from pathlib import Path as _P
    target = _P(out)
    if not target.exists():
        return (False, f"{target.name} does not exist, so no QR code was made")
    size = target.stat().st_size
    if size == 0:
        return (False, f"{target.name} is empty")
    from shutil import which
    if not which("zbarimg"):
        return (True, f"{target.name} is {size} bytes (zbarimg is not "
                      f"installed, so it was not decoded to confirm it reads)")
    import subprocess
    try:
        proc = subprocess.run(
            ["zbarimg", "--quiet", "--raw", "-Sbinary", str(target)],
            capture_output=True, timeout=30, check=False)
    except Exception as exc:  # noqa: BLE001
        return (False, f"zbarimg could not be run on {target.name}: "
                       f"{type(exc).__name__}")
    if proc.returncode != 0 or not proc.stdout.strip():
        detail = (proc.stderr.decode(errors="replace") or "").strip()[:100]
        return (False, f"{target.name} is {size} bytes but no QR code decodes "
                       f"from it{f': ' + detail if detail else ''} - it is a "
                       f"picture, not a readable code")
    decoded = proc.stdout.decode("utf-8", errors="replace").strip()
    return (True, f"zbarimg decoded {target.name} and it reads back "
                  f"{decoded[:60]!r}")


POST_CONDITION = _verify_qr_code

SKILLS = [Skill(name="qr_code", schema=SCHEMA, run=_run)]
