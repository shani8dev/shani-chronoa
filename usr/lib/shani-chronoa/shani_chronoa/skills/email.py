"""Email against a locally configured IMAP/SMTP account - read, send and draft.

There are no built-in cloud credentials here and no account is guessed from an
address. The account is a 0600 JSON file the person writes themselves at
``~/.config/shani-chronoa/email.json``; a password is never stored in it and is
read from the session keyring under ``email:<user>`` when the server needs one.
With no file, every skill refuses and says where to put it - an absent account
is not an empty inbox.

Reading and sending are separate disclosures with separate consent keys:
``email-read-enabled`` and ``email-send-enabled``, both default off. Drafting
only writes a local file, like ``notes``, and needs no gate; listing drafts
would disclose mail and is intentionally not offered here.

Fetched bodies are bounded because a single huge message would eat the whole
tool result, and the whitelist has no way to ask for "just the first page".
"""

from __future__ import annotations

import email.utils
import imaplib
import json
import re
import smtplib
import time
from email.message import EmailMessage
from email.parser import BytesParser
from email.policy import default as _policy
from pathlib import Path

from shani_chronoa import files, secret_store
from shani_chronoa.capabilities import gate_label
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_READ_KEY = "email-read-enabled"
_SEND_KEY = "email-send-enabled"
_TIMEOUT = 20
_MAX_MESSAGES = 20
_MAX_CHARS = 4000
_ADDRESS = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")


def _config_path() -> Path:
    return files.config_home() / "shani-chronoa" / "email.json"


def _account() -> "tuple[dict | None, str]":
    """The local account, or (None, why). No defaults: no account, no mail."""
    path = _config_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, (
            f"no email account is configured - put one in {path} "
            "(see skills/email.py for the fields)"
        )
    except OSError as exc:
        return None, f"could not read {path}: {files.describe(exc, path, 'read')}"
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return None, f"the email account file {path} is not valid JSON ({exc})"
    if not isinstance(data, dict):
        return None, f"the email account file {path} must hold a JSON object"
    for name in ("password", "app_password", "token"):
        if name in data:
            return None, (
                f"the email account file must not hold a {name}; "
                "store it in the session keyring as email:<user>"
            )
    cfg = {
        k: v
        for k, v in data.items()
        if k
        in {
            "imap_host",
            "imap_port",
            "imap_tls",
            "smtp_host",
            "smtp_port",
            "smtp_tls",
            "user",
            "mailbox",
        }
    }
    if not cfg.get("imap_host") or not cfg.get("smtp_host"):
        return None, (
            "the email account needs both imap_host and smtp_host; "
            "nothing is guessed from an email address"
        )
    # An explicitly configured port must be honoured; the old expression
    # discarded `smtp_port` on every account and always used 587. The defaults
    # differ by transport: IMAP over SSL is 993 (143 without), while the SMTP
    # path here uses SMTP_SSL, whose standard submission port is 465 (587 is
    # the STARTTLS port and needs SMTP.starttls instead).
    for key, tls_port, plain_port in (
        ("imap_port", 993, 143),
        ("smtp_port", 465, 587),
    ):
        tls_key = key.replace("_port", "_tls")  # imap_port -> imap_tls
        raw = cfg.get(key)
        if raw in (None, ""):
            cfg[key] = tls_port if cfg.get(tls_key, True) else plain_port
        else:
            try:
                cfg[key] = int(raw)
            except (TypeError, ValueError):
                return None, f"{key} must be a number"
    cfg["imap_tls"] = bool(cfg.get("imap_tls", True))
    cfg["smtp_tls"] = bool(cfg.get("smtp_tls", True))
    cfg["mailbox"] = str(cfg.get("mailbox") or "INBOX")
    if "\r" in cfg["mailbox"] or "\n" in cfg["mailbox"]:
        return None, "mailbox must be a single line"
    return cfg, ""


def _password(user: str) -> str:
    """The account's password from the keyring, or '' for a local relay."""
    if not user:
        return ""
    return secret_store.get(f"email:{user}") or ""


def _consent(config: ChronoaConfig, key: str, what: str, nothing: str) -> str:
    """A refused read or send names the Settings row, not the raw key."""
    if config.get_bool(key, False):
        return ""
    return (
        f"{what} is turned off (switch on \u201c{gate_label(key)}\u201d in "
        f"Settings). Nothing {nothing}."
    )


def _text(arguments: dict, key: str) -> str:
    value = arguments.get(key)
    return value.strip() if isinstance(value, str) else ""


def _subject(message: bytes) -> str:
    parsed = BytesParser(policy=_policy).parsebytes(message)
    sender = str(parsed.get("From", ""))[:80]
    when = str(parsed.get("Date", ""))[:40]
    subject = str(parsed.get("Subject", ""))[:120]
    return f"{when} | {sender} | {subject}"


def _imap(cfg: dict):
    host = str(cfg["imap_host"])
    if cfg["imap_tls"]:
        client = imaplib.IMAP4_SSL(host, int(cfg["imap_port"]), timeout=_TIMEOUT)
    else:
        client = imaplib.IMAP4(host, int(cfg["imap_port"]), timeout=_TIMEOUT)
    user = str(cfg.get("user") or "")
    if user:
        client.login(user, _password(user))
    return client


def _read(arguments: dict) -> str:
    refusal = _consent(ChronoaConfig(), _READ_KEY, "Reading your email", "was read")
    if refusal:
        return f"Refusing: {refusal}"
    cfg, why = _account()
    if cfg is None:
        return f"Refusing: {why}."
    action = _text(arguments, "action") or "list"
    try:
        client = _imap(cfg)
        try:
            status, _ = client.select(cfg["mailbox"], readonly=True)
            if status != "OK":
                return (
                    f"Could not open mailbox {cfg['mailbox']!r} on {cfg['imap_host']}."
                )
            uid = _text(arguments, "uid")
            if action == "get":
                if not uid.isdigit():
                    return "Give a numeric 'uid' to read one message."
                status, data = client.fetch(uid, "(RFC822)")
                if status != "OK" or not data or not data[0]:
                    return f"No message with uid {uid}."
                raw = data[0][1] if isinstance(data[0], tuple) else b""
                parsed = BytesParser(policy=_policy).parsebytes(raw or b"")
                body = parsed.get_body(preferencelist=("plain",))
                text = (body.get_content() if body else str(parsed.get_payload())) or ""
                return f"Message {uid}:\n{str(text)[:_MAX_CHARS]}"
            if action != "list":
                return "action must be list or get."
            criteria = "UNSEEN" if arguments.get("unseen") else "ALL"
            status, data = client.search(None, criteria)
            if status != "OK" or not data or not data[0]:
                return f"No messages in {cfg['mailbox']!r}."
            ids = data[0].split()[-_MAX_MESSAGES:]
            lines = []
            for message_id in reversed(ids):
                status, chunk = client.fetch(message_id, "(RFC822.HEADER)")
                if status == "OK" and chunk and chunk[0]:
                    raw = chunk[0][1] if isinstance(chunk[0], tuple) else b""
                    lines.append(_subject(raw or b""))
            more = (
                ""
                if len(data[0].split()) <= _MAX_MESSAGES
                else f" (newest {_MAX_MESSAGES} shown)"
            )
            return (
                (
                    f"{len(lines)} message(s) in {cfg['mailbox']!r}{more}:\n"
                    + "\n".join(lines)
                )
                if lines
                else f"No readable messages{more}."
            )
        finally:
            try:
                client.close()
            finally:
                client.logout()
    except (OSError, imaplib.IMAP4.error) as exc:
        return f"Could not reach the configured IMAP server: {exc}"


def _send(arguments: dict) -> str:
    refusal = _consent(ChronoaConfig(), _SEND_KEY, "Sending your email", "was sent")
    if refusal:
        return f"Refusing: {refusal}"
    cfg, why = _account()
    if cfg is None:
        return f"Refusing: {why}."
    to, subject, body = (
        _text(arguments, "to"),
        _text(arguments, "subject"),
        _text(arguments, "body"),
    )
    if not to or not body:
        return "Give 'to' and 'body'; 'subject' is optional."
    if any(c in to + subject for c in "\r\n"):
        return "'to' and 'subject' must be single lines."
    # Check *every* recipient, not just the first with a display name. The old
    # form let "a@example.com, not-an-address" through and then sent to a
    # malformed second address.
    parsed = email.utils.getaddresses([to])
    addresses = [addr for _name, addr in parsed if addr]
    if (
        len(addresses) != len(parsed)
        or not addresses
        or any(not _ADDRESS.match(addr) for addr in addresses)
    ):
        return "Give one or more plain email addresses in 'to'."
    if len(body) > 100_000:
        return "That message is too long to send in one call."
    user = str(cfg.get("user") or "")
    message = EmailMessage()
    message["From"] = user or "localhost"
    message["To"] = to
    message["Subject"] = subject[:200] or "(no subject)"
    message.set_content(body)
    try:
        if cfg["smtp_tls"]:
            with smtplib.SMTP_SSL(
                cfg["smtp_host"], int(cfg["smtp_port"]), timeout=_TIMEOUT
            ) as smtp:
                if user:
                    smtp.login(user, _password(user))
                smtp.send_message(message)
        else:
            with smtplib.SMTP(
                cfg["smtp_host"], int(cfg["smtp_port"]), timeout=_TIMEOUT
            ) as smtp:
                if user:
                    smtp.login(user, _password(user))
                smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        return f"Could not send through the configured SMTP server: {exc}"
    return f"Sent to {to} through {cfg['smtp_host']}."


def _draft(arguments: dict) -> str:
    action = _text(arguments, "action") or "create"
    if action != "create":
        return "action must be create. A draft is saved locally and is never sent."
    to, subject, body = (
        _text(arguments, "to"),
        _text(arguments, "subject"),
        _text(arguments, "body"),
    )
    if not (to or subject or body):
        return "Nothing to draft. Give 'to', 'subject' or 'body'."
    if any(c in to + subject for c in "\r\n"):
        return "'to' and 'subject' must be single lines."
    message = EmailMessage()
    message["To"] = to or "(not set)"
    message["Subject"] = subject[:200]
    message["Date"] = email.utils.formatdate(localtime=True)
    message.set_content(body)
    directory = files.state_home() / "shani-chronoa" / "email" / "drafts"
    files.ensure_private_dir(directory)
    name = f"draft-{time.time_ns()}.eml"
    target = directory / name
    target.write_bytes(message.as_bytes())
    target.chmod(0o600)
    return f"Saved the draft at {target}. Nothing was sent."


_READ = {
    "type": "function",
    "function": {
        "name": "email_read",
        "description": (
            "Read a locally configured IMAP mailbox: 'list' recent messages "
            "(or 'unseen'), or 'get' one by 'uid'. Needs the "
            "'email-read-enabled' consent key. There is no built-in account "
            "or cloud credential - the account must be configured locally."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "get"]},
                "uid": {
                    "type": "string",
                    "description": "Message uid, for action=get.",
                },
                "unseen": {
                    "type": "boolean",
                    "description": "list only unseen messages.",
                },
            },
            "required": ["action"],
        },
    },
}
_SEND = {
    "type": "function",
    "function": {
        "name": "email_send",
        "description": (
            "Send a plain-text email through the locally configured SMTP "
            "server. Needs 'to' and 'body'; 'subject' is optional. Needs the "
            "'email-send-enabled' consent key and asks before sending. "
            "No account or credential is built in."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["to", "body"],
        },
    },
}
_DRAFT = {
    "type": "function",
    "function": {
        "name": "email_draft",
        "description": (
            "Save an email draft as a local .eml file under your state "
            "directory. Needs 'to', 'subject' or 'body'. Nothing is sent and "
            "no consent key is needed - a draft is a note in a private file."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
        },
    },
}

SKILLS = [
    Skill(name="email_read", schema=_READ, run=_read),
    Skill(name="email_send", schema=_SEND, run=_send),
    Skill(name="email_draft", schema=_DRAFT, run=_draft),
]
