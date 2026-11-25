"""Focused checks for the email skill: reachability, consent, account, drafts.

Nothing here talks to a mail server. The point is that the skill is advertised
and refused for the right reason, that the locally written account is parsed
correctly (the old code silently discarded `smtp_port`), and that drafting is
local-only. Driven through `execute_tool_outcome` except where the question is
about a private parser.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa")
)

import pytest  # noqa: E402

from shani_chronoa import tools  # noqa: E402
from shani_chronoa.skills import email as email_mod  # noqa: E402


def _run(name: str, **args) -> str:
    out = tools.execute_tool_outcome(name, args)
    assert out.ran, f"{name} did not run: {out}"
    return str(out.text)


def test_all_three_email_tools_are_registered():
    names = {t["function"]["name"] for t in tools.TOOLS}
    assert {"email_read", "email_send", "email_draft"} <= names, names


@pytest.mark.parametrize(
    "tool,key,label",
    [
        ("email_read", "email-read-enabled", "Let Chronoa read your email"),
        ("email_send", "email-send-enabled", "Let Chronoa send email"),
    ],
)
def test_a_shut_gate_refuses_and_names_the_settings_row(
    tmp_path,
    monkeypatch,
    gsettings_env,
    tool,
    key,
    label,
):
    from shani_chronoa.config import ChronoaConfig

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    (tmp_path / "config").mkdir(exist_ok=True)
    ChronoaConfig().set(key, "false")

    args = (
        {"action": "list"}
        if tool == "email_read"
        else {"to": "a@example.com", "body": "hi"}
    )
    refused = _run(tool, **args)
    assert "Refusing" in refused, refused
    assert label in refused, refused
    assert key not in refused, "the refusal names a key no Settings row spells that way"


def test_granting_the_key_changes_the_answer(tmp_path, monkeypatch, gsettings_env):
    """The two gates must be reachable, not just present in a table."""
    from shani_chronoa.config import ChronoaConfig

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    (tmp_path / "config").mkdir(exist_ok=True)
    config = ChronoaConfig()
    config.set("email-read-enabled", "true")
    assert config.get_bool("email-read-enabled", False) is True

    # No account is configured, so the answer is the honest "no account" one,
    # not the gate refusal. That difference is what proves the grant took.
    answer = _run("email_read", action="list")
    assert "is turned off" not in answer, answer
    assert "no email account is configured" in answer, answer


def test_account_uses_an_explicit_port_and_a_transport_default(tmp_path, monkeypatch):
    """The old expression always returned 587 and ignored `smtp_port`."""
    config_home = tmp_path / "config"
    (config_home / "shani-chronoa").mkdir(parents=True)
    monkeypatch.setattr(email_mod.files, "config_home", lambda: config_home)
    account = config_home / "shani-chronoa" / "email.json"
    account.write_text(
        json.dumps(
            {
                "imap_host": "imap.example.org",
                "smtp_host": "smtp.example.org",
                "smtp_port": 2525,
                "user": "me@example.org",
            }
        )
    )

    cfg, why = email_mod._account()
    assert why == ""
    assert cfg["smtp_port"] == 2525, f"explicit port discarded: {cfg}"
    # SMTP_SSL is used when smtp_tls is on, so its default is 465, not 587.
    assert cfg["imap_port"] == 993
    assert cfg["smtp_tls"] is True

    data = json.loads(account.read_text())
    data.pop("smtp_port")
    data["smtp_tls"] = False
    data["imap_tls"] = False
    account.write_text(json.dumps(data))
    cfg, why = email_mod._account()
    assert why == ""
    assert cfg["smtp_port"] == 587 and cfg["imap_port"] == 143, cfg


def test_a_password_in_the_account_file_is_refused(tmp_path, monkeypatch):
    config_home = tmp_path / "config"
    (config_home / "shani-chronoa").mkdir(parents=True)
    monkeypatch.setattr(email_mod.files, "config_home", lambda: config_home)
    (config_home / "shani-chronoa" / "email.json").write_text(
        json.dumps(
            {
                "imap_host": "i",
                "smtp_host": "s",
                "user": "me@example.org",
                "app_password": "secret",
            }
        )
    )
    cfg, why = email_mod._account()
    assert cfg is None and "app_password" in why, why


def test_a_bad_second_recipient_is_refused_before_any_send(tmp_path, monkeypatch):
    """The old check looked only at the first address with a display name."""
    from shani_chronoa.config import ChronoaConfig

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    (tmp_path / "config").mkdir(exist_ok=True)
    ChronoaConfig().set("email-send-enabled", "true")
    config_home = tmp_path / "config"
    (config_home / "shani-chronoa").mkdir(exist_ok=True)
    (config_home / "shani-chronoa" / "email.json").write_text(
        json.dumps(
            {
                "imap_host": "i",
                "smtp_host": "s",
                "user": "me@example.org",
            }
        )
    )
    monkeypatch.setattr(email_mod.files, "config_home", lambda: config_home)

    called = []
    monkeypatch.setattr(email_mod.smtplib, "SMTP_SSL", lambda *a, **k: called.append(a))
    result = email_mod._send(
        {
            "to": "ok@example.org, not-an-address",
            "body": "hello",
        }
    )
    assert "plain email address" in result, result
    assert called == [], "the SMTP client was reached despite a bad recipient"


def test_a_draft_is_a_private_local_file_and_sends_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))

    out = _run("email_draft", to="friend@example.org", subject="Hi", body="Hello")
    assert "Nothing was sent" in out, out
    drafts = tmp_path / "shani-chronoa" / "email" / "drafts"
    written = list(drafts.glob("draft-*.eml"))
    assert len(written) == 1, written
    content = written[0].read_text()
    assert "Hello" in content and "friend@example.org" in content
    assert (written[0].stat().st_mode & 0o777) == 0o600
