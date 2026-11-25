"""The local email account file: the Settings form's headless half.

There is no built-in cloud account and none is guessed from an address. The
account lives in the same 0600 JSON the email skills read, at
``~/.config/shani-chronoa/email.json``. This module owns the *write* side and
the honest status; ``skills/email.py`` stays the single reader, so a bad file
here produces exactly the refusal a person would get from asking Chronoa to
read their mail.

Nothing in this module imports GTK. ``status()`` and ``save()`` are local file
operations, deliberately callable in a headless test. ``check_account()`` is
the one network path: setup never calls it, only an explicit "Check account"
press does. It reads the password through the skill's keyring lookup and never
from the JSON file.

The statuses are a closed vocabulary: ``not configured``, ``configured``,
``reachable`` and ``failed``. A local file that merely parses is only
``configured`` - it is not a claim that any server answered.
"""

from __future__ import annotations

import imaplib
import json
import os
import smtplib
import tempfile
import threading
from pathlib import Path

from shani_chronoa import files
from shani_chronoa.skills import email as email_skill

NOT_CONFIGURED = "not configured"
CONFIGURED = "configured"
REACHABLE = "reachable"
FAILED = "failed"
STATUS_WORDS = (NOT_CONFIGURED, CONFIGURED, REACHABLE, FAILED)

# Legacy names for callers that compare against a specific local-file word.
MISSING = NOT_CONFIGURED
BAD_FILE = NOT_CONFIGURED

#: Both probes are bounded. A stalled server must not hold the Settings window
#: (or a headless caller) past this deadline.
IMAP_TIMEOUT = 10.0
SMTP_TIMEOUT = 10.0

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
        return NOT_CONFIGURED, (
            "No email account is configured yet. Save the fields below and the "
            "email skills will use them; until then they refuse."
        )
    cfg, why = email_skill._account()
    if cfg is None:
        return NOT_CONFIGURED, why
    user = cfg.get("user") or "no user"
    return CONFIGURED, (
        f"Ready for {user}: IMAP {cfg['imap_host']}, SMTP {cfg['smtp_host']}. "
        "This is a local file check only - no mail server was contacted."
    )


def _reason(exc: BaseException, password: str) -> str:
    """A short refusal that never repeats the password a server echoed."""
    text = " ".join(str(exc).split()) or type(exc).__name__
    if password and password in text:
        text = text.replace(password, "[redacted]")
    return text[:200]


def _bounded(
    call, deadline: float, what: str, password: str = ""
) -> "tuple[bool, str]":
    """Run `call` on a worker and return before `deadline` elapses.

    A thread is abandoned rather than killed when it overruns: Python cannot
    stop a blocked socket call from another thread. What matters is that the
    caller gets an answer and says the probe timed out instead of hanging.
    """
    box: dict = {}

    def work() -> None:
        try:
            box["value"] = call()
        except BaseException as exc:  # noqa: BLE001 - every failure is a reason
            box["error"] = exc

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(deadline)
    if thread.is_alive():
        return False, f"{what} timed out after {deadline:g}s"
    if "error" in box:
        return False, f"{what} failed: {_reason(box['error'], password)}"
    return True, str(box.get("value", ""))


def _imap_login(module, cfg: dict, user: str, password: str, timeout: float) -> str:
    cls = module.IMAP4_SSL if cfg["imap_tls"] else module.IMAP4
    try:
        client = cls(cfg["imap_host"], int(cfg["imap_port"]), timeout=timeout)
    except TypeError:  # a stand-in client without the timeout keyword
        client = cls(cfg["imap_host"], int(cfg["imap_port"]))
    try:
        if user:
            client.login(user, password)
    finally:
        logout = getattr(client, "logout", None)
        if callable(logout):
            try:
                logout()
            except Exception:  # noqa: BLE001 - a closed socket is not a failure
                pass
    return "ok"


def _smtp_probe(module, cfg: dict, user: str, password: str, timeout: float) -> str:
    cls = module.SMTP_SSL if cfg["smtp_tls"] else module.SMTP
    try:
        client = cls(cfg["smtp_host"], int(cfg["smtp_port"]), timeout=timeout)
    except TypeError:  # a stand-in client without the timeout keyword
        client = cls(cfg["smtp_host"], int(cfg["smtp_port"]))
    try:
        if user:
            client.login(user, password)
    finally:
        for name in ("quit", "close"):
            close = getattr(client, name, None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001
                    pass
                break
    return "ok"


def check_account(
    imap_module=None,
    smtp_module=None,
    imap_timeout: float = IMAP_TIMEOUT,
    smtp_timeout: float = SMTP_TIMEOUT,
    smtp_probe: bool = True,
) -> "tuple[str, str]":
    """Log in once to prove the stored account works. Never called by setup.

    The account comes from the skill's own reader, the password from its
    keyring lookup - never from the JSON file - and no message is sent. Both
    probes run under a deadline so a silent server cannot hang the caller.
    Returns ``(reachable, ...)`` or one of the honest refusals.
    """
    cfg, why = email_skill._account()
    if cfg is None:
        return NOT_CONFIGURED, why
    user = str(cfg.get("user") or "")
    if not user:
        return (
            FAILED,
            "no account user is configured, so there is no keyring entry to log in with",
        )
    password = email_skill._password(user)
    imap_mod = imap_module or imaplib
    smtp_mod = smtp_module or smtplib

    ok, detail = _bounded(
        lambda: _imap_login(imap_mod, cfg, user, password, imap_timeout),
        imap_timeout,
        "IMAP login",
        password,
    )
    if not ok:
        return FAILED, detail
    reached = f"IMAP {cfg['imap_host']} accepted the login"
    if smtp_probe:
        ok, detail = _bounded(
            lambda: _smtp_probe(smtp_mod, cfg, user, password, smtp_timeout),
            smtp_timeout,
            "SMTP login",
            password,
        )
        if not ok:
            return FAILED, detail
        reached += f" and SMTP {cfg['smtp_host']} accepted the login"
    return REACHABLE, f"{reached}. Nothing was sent."


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
    "FAILED",
    "IMAP_TIMEOUT",
    "MISSING",
    "NOT_CONFIGURED",
    "PASSWORD_NOTE",
    "REACHABLE",
    "SECURITY_MODES",
    "SMTP_TIMEOUT",
    "STATUS_WORDS",
    "account_path",
    "check_account",
    "read_fields",
    "save",
    "status",
]
