"""The explicit "Check account" action: bounded, keyring-only, never automatic.

Building the Settings page must not open a socket. The only network path lives
behind a button, and the password it uses comes from the skill's keyring lookup
rather than from the JSON the account is stored in. These tests drive the real
page builder with real widgets and the real check function with fake clients.
"""

from __future__ import annotations

import pathlib
import sys
import time
import types

sys.path.insert(
    0,
    str(pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"),
)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

from shani_chronoa import email_setup  # noqa: E402
from shani_chronoa.settings_window import email as page_mod  # noqa: E402
from shani_chronoa.skills import email as email_skill  # noqa: E402

FIELDS = {
    "imap_host": "imap.example.org",
    "imap_port": 993,
    "imap_tls": True,
    "smtp_host": "smtp.example.org",
    "smtp_port": 465,
    "smtp_tls": True,
    "user": "me@example.org",
}


class _Group:
    def __init__(self):
        self.rows = []
        self._needle_extra = []

    def add(self, row):
        self.rows.append(row)


class _Entry:
    def __init__(self, text):
        self._text = text

    def get_text(self):
        return self._text


class _Host:
    def _group(self, page, title, description):
        self.group = _Group()
        return self.group

    def _entry(self, group, title, subtitle, text, changed):
        return _Entry(text)


def _build_page():
    host = _Host()
    page_mod.EmailPage._build_email(host, object())
    return host


def _status(host):
    row = host._email_status_row
    return row.get_subtitle() or ""


def _account(tmp_path, monkeypatch, **extra):
    home = tmp_path / "config"
    (home / "shani-chronoa").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(email_setup.files, "config_home", lambda: home)
    email_setup.save(dict(FIELDS, **extra))
    return home / "shani-chronoa" / "email.json"


class _Client:
    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.calls = []

    def login(self, user, password):
        self.calls.append((user, password))

    def logout(self):
        pass

    def quit(self):
        pass


def _clients(imap_client=None, smtp_client=None):
    imap_client = imap_client or _Client("", 0)
    smtp_client = smtp_client or _Client("", 0)

    def make_imap(*args, **kwargs):
        imap_client.host, imap_client.port = args[0], args[1]
        imap_client.timeout = kwargs.get("timeout")
        return imap_client

    def make_smtp(*args, **kwargs):
        smtp_client.host, smtp_client.port = args[0], args[1]
        smtp_client.timeout = kwargs.get("timeout")
        return smtp_client

    imap = types.SimpleNamespace(IMAP4_SSL=make_imap, IMAP4=make_imap)
    smtp = types.SimpleNamespace(SMTP_SSL=make_smtp, SMTP=make_smtp)
    return imap, smtp, imap_client, smtp_client


def _buttons(group):
    found = []

    def walk(widget):
        child = widget.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Button):
                found.append(child)
            walk(child)
            child = child.get_next_sibling()

    for row in group.rows:
        walk(row)
    return found


def test_building_the_page_never_contacts_a_server(tmp_path, monkeypatch):
    home = tmp_path / "config"
    (home / "shani-chronoa").mkdir(parents=True)
    monkeypatch.setattr(email_setup.files, "config_home", lambda: home)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("a mail server was contacted while building the page")

    monkeypatch.setattr(email_setup.imaplib, "IMAP4_SSL", forbidden)
    monkeypatch.setattr(email_setup.imaplib, "IMAP4", forbidden)
    monkeypatch.setattr(email_setup.smtplib, "SMTP_SSL", forbidden)
    monkeypatch.setattr(email_setup.smtplib, "SMTP", forbidden)
    monkeypatch.setattr(email_setup, "check_account", forbidden)

    host = _build_page()
    assert "not configured" in _status(host)
    buttons = [b.get_label() for b in _buttons(host.group)]
    assert "Save account" in buttons
    assert page_mod.CHECK_LABEL in buttons


def test_check_is_a_button_not_an_automatic_probe(tmp_path, monkeypatch):
    monkeypatch.setattr(email_setup, "check_account", lambda: ("reachable", "proved"))
    host = _build_page()
    assert _status(host).startswith(email_setup.NOT_CONFIGURED)

    # The handler runs its work synchronously here so the assertion does not
    # need a main loop; the product still uses a worker and GLib.idle_add.
    monkeypatch.setattr(
        page_mod.threading,
        "Thread",
        lambda target, daemon: types.SimpleNamespace(start=target),
    )
    monkeypatch.setattr(page_mod.GLib, "idle_add", lambda fn, arg: fn(arg))

    button = next(
        b for b in _buttons(host.group) if b.get_label() == page_mod.CHECK_LABEL
    )
    button.emit("clicked")
    assert _status(host) == "reachable: proved"
    assert button.get_sensitive() is True


def test_login_uses_the_keyring_password_and_the_configured_servers(
    tmp_path, monkeypatch
):
    path = _account(tmp_path, monkeypatch)
    monkeypatch.setattr(
        email_skill.secret_store,
        "get",
        lambda key: "keyring-secret" if key == "email:me@example.org" else None,
    )
    imap, smtp, imap_client, smtp_client = _clients()
    word, message = email_setup.check_account(
        imap_module=imap, smtp_module=smtp, imap_timeout=3.0, smtp_timeout=4.0
    )
    assert word == email_setup.REACHABLE, (word, message)
    assert imap_client.calls == [("me@example.org", "keyring-secret")]
    assert smtp_client.calls == [("me@example.org", "keyring-secret")]
    assert imap_client.host == "imap.example.org" and imap_client.timeout == 3.0
    assert smtp_client.host == "smtp.example.org" and smtp_client.timeout == 4.0
    assert "Nothing was sent" in message
    assert "keyring-secret" not in message
    assert "password" not in path.read_text(encoding="utf-8")


def test_smtp_probe_is_optional(tmp_path, monkeypatch):
    _account(tmp_path, monkeypatch)
    monkeypatch.setattr(email_skill.secret_store, "get", lambda key: "secret")
    imap, smtp, imap_client, smtp_client = _clients()

    def no_smtp(*_args, **_kwargs):
        raise AssertionError("SMTP was probed when the caller opted out")

    smtp.SMTP_SSL = no_smtp
    smtp.SMTP = no_smtp
    word, message = email_setup.check_account(
        imap_module=imap, smtp_module=smtp, smtp_probe=False
    )
    assert word == email_setup.REACHABLE
    assert "SMTP" not in message
    assert imap_client.calls == [("me@example.org", "secret")]


def test_a_refused_login_is_a_failure_with_a_reason(tmp_path, monkeypatch):
    _account(tmp_path, monkeypatch)
    monkeypatch.setattr(email_skill.secret_store, "get", lambda key: "secret")

    class Bad(_Client):
        def login(self, user, password):
            raise RuntimeError(f"no for {user}: secret")

    imap, _smtp, _imap_client, _smtp_client = _clients(imap_client=Bad("", 0))
    word, message = email_setup.check_account(imap_module=imap, smtp_module=_smtp)
    assert word == email_setup.FAILED, (word, message)
    assert "IMAP login failed" in message
    assert "secret" not in message, "the password a server echoed reached the user"


def test_a_stalled_server_is_bounded(tmp_path, monkeypatch):
    _account(tmp_path, monkeypatch)
    monkeypatch.setattr(email_skill.secret_store, "get", lambda key: "secret")

    class Slow(_Client):
        def login(self, user, password):
            time.sleep(1.0)

    imap, smtp, _i, _s = _clients(imap_client=Slow("", 0))
    start = time.monotonic()
    word, message = email_setup.check_account(
        imap_module=imap, smtp_module=smtp, imap_timeout=0.05, smtp_timeout=0.05
    )
    elapsed = time.monotonic() - start
    assert word == email_setup.FAILED, (word, message)
    assert "timed out" in message
    assert elapsed < 0.75, elapsed


def test_missing_account_and_missing_user_never_open_a_socket(tmp_path, monkeypatch):
    home = tmp_path / "config"
    (home / "shani-chronoa").mkdir(parents=True)
    monkeypatch.setattr(email_setup.files, "config_home", lambda: home)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("a server was contacted with no usable account")

    imap = types.SimpleNamespace(IMAP4_SSL=forbidden, IMAP4=forbidden)
    smtp = types.SimpleNamespace(SMTP_SSL=forbidden, SMTP=forbidden)
    word, message = email_setup.check_account(imap_module=imap, smtp_module=smtp)
    assert word == email_setup.NOT_CONFIGURED
    assert "no email account" in message.lower()

    _account(tmp_path, monkeypatch, user="")
    word, message = email_setup.check_account(imap_module=imap, smtp_module=smtp)
    assert word == email_setup.FAILED
    assert "no account user" in message


def test_the_status_vocabulary_is_honest(tmp_path, monkeypatch):
    assert email_setup.STATUS_WORDS == (
        email_setup.NOT_CONFIGURED,
        email_setup.CONFIGURED,
        email_setup.REACHABLE,
        email_setup.FAILED,
    )
    home = tmp_path / "config"
    (home / "shani-chronoa").mkdir(parents=True)
    monkeypatch.setattr(email_setup.files, "config_home", lambda: home)
    word, _message = email_setup.status()
    assert word == email_setup.NOT_CONFIGURED
    path = home / "shani-chronoa" / "email.json"
    path.write_text("{not json", encoding="utf-8")
    word, message = email_setup.status()
    assert word == email_setup.NOT_CONFIGURED and "not valid JSON" in message
    _account(tmp_path, monkeypatch)
    word, message = email_setup.status()
    assert word == email_setup.CONFIGURED
    assert "no mail server was contacted" in message
