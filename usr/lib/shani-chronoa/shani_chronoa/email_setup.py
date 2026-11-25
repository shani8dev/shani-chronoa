"""The local email account file: the Settings form's headless half.

There is no built-in cloud account and none is guessed from an address. The
account lives in the same 0600 JSON the email skills read, at
``~/.config/shani-chronoa/email.json``. This module owns the *write* side and
the honest status; ``skills/email.py`` stays the single reader, so a bad file
here produces exactly the refusal a person would get from asking Chronoa to
read their mail.

Nothing in this module imports GTK, talks to a mail server, or opens the
keyring. Setup is a local file operation; a password stays in the session
keyring under ``email:<user>`` and is never written to the file. ``status()``
and ``save()`` are deliberately callable in a headless test for that reason.

The three statuses are a closed vocabulary: ``configured``, ``missing`` and
``bad file``. A missing file is not a corrupt one, and neither is a claim that
the account works - only that the local file parses.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import email as email_skill

CONFIGURED = "configured"
MISSING = "missing"
BAD_FILE = "bad file"
STATUS_WORDS = (CONFIGURED, MISSING, BAD_FILE)

#: The one place the whitelist is written down is the reader; importing it here
#: means a field added to the skill is offered by the form automatically, and a
#: field removed from the skill can no longer be saved by the form.
FIELDS = email_skill.ACCOUNT_FIELDS

PASSWORD_NOTE = (
    "Your password is never written to this file. Chronoa reads it from the "
    "session keyring under email:<user> when a server asks for it."
)

#: The security modes the email skill actually implements. It uses IMAP4_SSL
#: and SMTP_SSL when ``*_tls`` is on and the plain classes when it is off. There
#: is deliberately no STARTTLS option here: offering one would promise a mode
#: the reader cannot perform.
SECURITY_MODES = (
    ("tls", "TLS (recommended)", True, True),
    ("plain", "None (plain text)", False, False),
)


def account_path() -> Path:
    """The exact path the skill reads; resolved per call, never at import."""
    return email_skill._config_path()


def status() -> "tuple[str, str]":
    """``(word, message)`` from the local file, without contacting a server.

    A bad file's message is the skill's own refusal sentence, word for word.
    The form cannot say something friendlier than the gate that will refuse the
    first read - and a second, divergent explanation would be worse than none.
    """
    path = account_path()
    if not path.exists():
        return MISSING, (
            "No email account is configured yet. Save the fields below and the "
            "email skills will use them; until then they refuse."
        )
    cfg, why = email_skill._account()
    if cfg is None:
        return BAD_FILE, why
    user = cfg.get("user") or "no user"
    return CONFIGURED, (
        f"Ready for {user}: IMAP {cfg['imap_host']}, SMTP {cfg['smtp_host']}. "
        "This is a local file check only - no mail server was contacted."
    )


def read_fields() -> dict:
    """Raw values for prefilling the form; ``{}`` for anything unreadable."""
    try:
        raw = json.loads(account_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if k in FIELDS}


def save(values: dict) -> Path:
    """Write the account JSON atomically with mode 0600; return its path.

    Refusals raise ``ValueError`` with the skill's own wording where one
    exists, so an account the form writes is one the skill will accept. Nothing
    here opens a connection or a keyring: a password key is refused outright
    rather than quietly dropped, which would leave a person believing it had
    been saved somewhere safe when it had not.
    """
    for name in ("password", "app_password", "token"):
        if name in values:
            raise ValueError(
                f"the email account file must not hold a {name}; "
                "store it in the session keyring as email:<user>"
            )

    clean: dict = {}
    for key in sorted(FIELDS):
        value = values.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if "\r" in value or "\n" in value:
                raise ValueError(f"{key} must be a single line")
        if value == "":
            continue
        clean[key] = value

    for key in ("imap_host", "smtp_host"):
        if not clean.get(key):
            raise ValueError(
                "the email account needs both imap_host and smtp_host; "
                "nothing is guessed from an email address"
            )
    for key in ("imap_port", "smtp_port"):
        if key in clean:
            try:
                clean[key] = int(clean[key])
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be a number") from None
            if not 1 <= clean[key] <= 65535:
                raise ValueError(f"{key} must be a port between 1 and 65535")
    for key in ("imap_tls", "smtp_tls"):
        if key in clean:
            clean[key] = bool(clean[key])

    path = account_path()
    files.ensure_private_dir(path.parent)
    # Temp file in the same directory: os.replace is atomic, so a crash leaves
    # the previous account intact instead of half a JSON document. mkstemp is
    # 0600 already; fchmod states the contract rather than relying on that.
    fd, tmp = tempfile.mkstemp(prefix=".email-", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(clean, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


__all__ = [
    "FIELDS",
    "BAD_FILE",
    "CONFIGURED",
    "MISSING",
    "PASSWORD_NOTE",
    "SECURITY_MODES",
    "STATUS_WORDS",
    "account_path",
    "read_fields",
    "save",
    "status",
]
