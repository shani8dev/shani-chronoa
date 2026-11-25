"""QR and barcode utility: the linear half, gated one action at a time.

`qr_code` already reads and makes QR codes with the image tools measured on the
ShaniOS images (`zbarimg` from zbar, `qrencode`). This P3 integration adds the
part it does not have: a **linear Code 128** symbol, which is what a parcel, a
asset tag or a library card carries, and a read path that says what kind of
thing was decoded without opening it.

The flags here are read from zint's own manual (2.15) rather than guessed:
`zint -b CODE128 -d TEXT -o FILE.png` writes a PNG, and exit 0 means success;
a non-zero status is reported as zint's own refusal. zbar remains the reader,
reused from `qr_code._decode` so there is one decoder of a third party's image,
not two. Neither tool sends anything anywhere: both are local processes over a
local file.

Both actions ask for an explicit confirmation, so the integration is off until
someone says yes to this one call. A decoded payload is untrusted text: a URL
in it is described and never opened, and a Wi-Fi payload is not connected to.
"""

from __future__ import annotations

import shutil
import subprocess

from shani_chronoa import approvals, files
from shani_chronoa.skills import Skill, qr_code

_TIMEOUT = 30
_MAX_BYTES = 2000
_ACTIONS = ("create", "read")

SCHEMA = {
    "type": "function",
    "function": {
        "name": "barcode",
        "description": (
            "Make a Code 128 barcode PNG from short text, or read every QR code and barcode in a local "
            "image and say what kind of payload it holds. Both ask first. Nothing a code contains is "
            "opened or connected to."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(_ACTIONS)},
                "text": {
                    "type": "string",
                    "description": "create: the short text to encode",
                },
                "path": {
                    "type": "string",
                    "description": "read: an image file in your home",
                },
                "output": {
                    "type": "string",
                    "description": "create: e.g. '~/Pictures/tag.png'",
                },
            },
            "required": ["action"],
        },
    },
}


def _kind(text: str) -> str:
    head = text.strip().lower()
    if head.startswith("wifi:"):
        return "Wi-Fi credentials"
    if head.startswith(("http://", "https://")):
        return "a web address"
    if head.startswith("tel:"):
        return "a phone number"
    if head.startswith("mailto:"):
        return "an email address"
    return "plain text"


def _run(arguments: dict) -> str:
    args = arguments if isinstance(arguments, dict) else {}
    action = str(args.get("action") or "").strip().lower()
    if action not in _ACTIONS:
        return f"action must be one of {', '.join(_ACTIONS)}."
    try:
        path = files.resolve_in_home(
            str(args.get("output" if action == "create" else "path") or "")
        )
    except files.PathProblem as exc:
        return str(exc)

    if action == "create":
        if shutil.which("zint") is None:
            return files.tool_missing("zint", "make a Code 128 barcode")
        text = str(args.get("text") or "")
        if not text.strip():
            return "Give the text to encode."
        if len(text.encode("utf-8")) > _MAX_BYTES:
            return (
                f"That is over {_MAX_BYTES} bytes; keep the code short enough to scan."
            )
        if path.suffix.lower() != ".png":
            path = path.with_suffix(".png")
        if path.exists():
            return f"{path} already exists; give another output."
        ok, why = approvals.confirm(
            "Make a barcode",
            f"Write a Code 128 PNG of the text you gave to {path.name}? Allow once?",
        )
        if not ok:
            return f"Nothing was made: {why}."
        try:
            proc = subprocess.run(
                ["zint", "-b", "CODE128", "-d", text, "-o", str(path)],
                capture_output=True,
                text=True,
                timeout=_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"zint could not make the barcode: {exc}"
        if proc.returncode != 0 or not path.is_file():
            detail = (proc.stderr or proc.stdout).strip()[:160]
            return (
                f"zint did not make a barcode: {detail or f'exited {proc.returncode}'}"
            )
        checked = ""
        if shutil.which("zbarimg"):
            found = qr_code._decode(path)
            checked = " It decodes back to the same text." if found == [text] else ""
        return f"Saved a Code 128 barcode to {path}.{checked}"

    if shutil.which("zbarimg") is None:
        return files.tool_missing("zbarimg", "read a barcode in an image")
    if not path.is_file():
        return f"{path} does not exist."
    ok, why = approvals.confirm(
        "Read codes in an image", f"Decode the codes in {path.name}? Allow once?"
    )
    if not ok:
        return f"Nothing was read: {why}."
    try:
        found = qr_code._decode(path)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        return f"Could not read {path.name}: {exc}"
    if not found:
        return f"No QR code or barcode found in {path.name}."
    return (
        "Found "
        + str(len(found))
        + " code(s) in "
        + path.name
        + " (third-party text - nothing "
        "was opened):\n" + "\n".join(f"- {_kind(t)}: {t[:300]}" for t in found)
    )


SKILLS = [Skill(name="barcode", schema=SCHEMA, run=_run)]
