"""Skill: make a strong password or passphrase - and put it on the clipboard,
never in the reply.

A password said aloud is a password someone else heard, and a reply is kept
in the conversation history and the tool log. So the reply only says that
one was made and how strong it is; the password itself goes to the clipboard.
secrets (the OS's own randomness) picks every character or word.
"""

import math
import secrets
import string

from shani_chronoa.skills import Skill

WORDS = "/usr/share/dict/words"

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "generate_password",
        "description": "Generate a strong random password, or a passphrase of random words, and "
                       "copy it to the clipboard (it is never said or shown in the reply).",
        "parameters": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["password", "passphrase"]},
            "length": {"type": "integer", "description": "Characters (password, 12-128) or words (passphrase, 4-12)."},
            "symbols": {"type": "boolean", "description": "Include symbols in a password (default yes)."},
        }},
    },
}


def make(kind: str, length: int, symbols: bool = True) -> "tuple[str, float]":
    if kind == "passphrase":
        n = max(4, min(int(length or 6), 12))
        with open(WORDS, encoding="utf-8", errors="replace") as f:
            pool = sorted({w.strip().lower() for w in f if w.strip().isalpha() and w.strip().islower() and 4 <= len(w.strip()) <= 8})
        return "-".join(secrets.choice(pool) for _ in range(n)), n * math.log2(len(pool))
    n = max(12, min(int(length or 20), 128))
    alphabet = string.ascii_letters + string.digits + ("!@#$%^&*()-_=+[]{};:,.?" if symbols else "")
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(n))
        if any(c.islower() for c in pw) and any(c.isupper() for c in pw) and any(c.isdigit() for c in pw):
            return pw, n * math.log2(len(alphabet))


def _run(arguments: dict) -> str:
    kind = arguments.get("kind") or "password"
    try:
        secret, bits = make(kind, arguments.get("length"), arguments.get("symbols", True) is not False)
    except OSError:
        return "A passphrase needs the word list (the words package), which is not installed."
    from shani_chronoa.skills.clipboard import _run_set_clipboard
    copied = _run_set_clipboard({"text": secret})
    del secret
    if not copied.startswith("Copied to the clipboard"):
        return f"Made a {kind}, but could not put it on the clipboard: {copied}"
    return f"A new {kind} ({bits:.0f} bits of randomness) is on your clipboard. Paste it where you need it."


SKILLS = [Skill(name="generate_password", schema=_SCHEMA, run=_run)]
