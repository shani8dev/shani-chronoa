"""Consent and secret rules at the command-line boundary."""

from __future__ import annotations

import argparse
import io

from shani_chronoa.tui import cli


class Client:
    def __init__(self):
        self.calls = []

    def capability(self, method, *args):
        self.calls.append((method, args))
        return {"name": args[0] if args else "", "value": "<set>", "changed": True}


def args_for(argv):
    return cli.build_parser().parse_args(argv)


def test_a_consent_write_without_yes_is_refused(capsys):
    client = Client()
    code = cli.run_capability(
        client,
        "ConversationsDelete",
        args_for(["conversations", "delete", "abc"]),
        jsonl=True,
    )
    assert code == 2
    assert "--yes" in capsys.readouterr().err
    assert client.calls == []


def test_yes_passes_confirm_as_a_bool(capsys):
    client = Client()
    code = cli.run_capability(
        client,
        "ConversationsDelete",
        args_for(["conversations", "delete", "abc", "--yes"]),
        jsonl=True,
    )
    assert code == 0
    assert client.calls == [("ConversationsDelete", ("abc", True))]


def test_an_api_key_in_argv_is_refused(capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    client = Client()
    code = cli.run_capability(
        client,
        "SettingsSet",
        args_for(["settings", "set", "llm7-api-key", "secret-in-argv", "--yes"]),
        jsonl=True,
    )
    assert code == 2
    assert "argv" in capsys.readouterr().err
    assert client.calls == []


def test_an_api_key_comes_from_stdin(capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("a-secret\n"))
    client = Client()
    code = cli.run_capability(
        client,
        "SettingsSet",
        args_for(["settings", "set", "llm7-api-key", "-", "--yes", "--stdin"]),
        jsonl=True,
    )
    assert code == 0
    assert client.calls == [("SettingsSet", ("llm7-api-key", "a-secret", True))]
    captured = capsys.readouterr()
    assert "a-secret" not in captured.out and "a-secret" not in captured.err


def test_secret_value_is_never_even_parsed_as_an_optional_flag():
    # argparse sees the API key only as the positional value of --stdin input,
    # so a value that looks like a flag cannot become an option.
    parsed = cli.build_parser().parse_args(
        ["settings", "set", "llm7-api-key", "-", "--yes", "--stdin"]
    )
    assert parsed.value == "-"
    assert parsed.yes is True and parsed.stdin is True
