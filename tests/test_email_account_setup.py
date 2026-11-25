"""The in-app email account form: same file, same 0600, same honest refusals.

Nothing here talks to a mail server. The headless half is tested directly;
the surface is built as a real ``SettingsWindow`` in a subprocess, because a
form that exists in source but cannot be reached from Settings is the defect
this file is about.

The status vocabulary is closed: configured / missing / bad file. A bad file's
message must be byte-for-byte the skill's own refusal, so a person cannot be
told a friendlier story on the form than the gate will give them on a read.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import textwrap

sys.path.insert(
    0,
    str(pathlib.Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"),
)

import pytest  # noqa: E402

from shani_chronoa import email_setup  # noqa: E402
from shani_chronoa.skills import email as email_mod  # noqa: E402


FIELDS = {
    "imap_host": "imap.example.org",
    "imap_port": 993,
    "imap_tls": True,
    "smtp_host": "smtp.example.org",
    "smtp_port": 465,
    "smtp_tls": True,
    "user": "me@example.org",
}


def _config_home(tmp_path):
    home = tmp_path / "config"
    (home / "shani-chronoa").mkdir(parents=True, exist_ok=True)
    return home


def test_the_setup_path_is_the_path_the_skill_reads(tmp_path, monkeypatch):
    home = _config_home(tmp_path)
    monkeypatch.setattr(email_setup.files, "config_home", lambda: home)
    assert email_setup.account_path() == email_mod._config_path()
    assert email_setup.account_path() == home / "shani-chronoa" / "email.json"


def test_save_then_the_skill_reads_the_same_account(tmp_path, monkeypatch):
    home = _config_home(tmp_path)
    monkeypatch.setattr(email_setup.files, "config_home", lambda: home)
    path = email_setup.save(dict(FIELDS))
    assert path.exists()
    cfg, why = email_mod._account()
    assert why == "", why
    assert cfg["imap_host"] == "imap.example.org"
    assert cfg["smtp_port"] == 465
    assert email_setup.read_fields()["user"] == "me@example.org"


def test_explicit_ports_and_plain_mode_survive_the_round_trip(tmp_path, monkeypatch):
    home = _config_home(tmp_path)
    monkeypatch.setattr(email_setup.files, "config_home", lambda: home)
    values = dict(
        FIELDS, imap_port="2525", smtp_port="2526", imap_tls=False, smtp_tls=False
    )
    email_setup.save(values)
    cfg, why = email_mod._account()
    assert why == ""
    assert (cfg["imap_port"], cfg["smtp_port"]) == (2525, 2526)
    assert cfg["imap_tls"] is False and cfg["smtp_tls"] is False
    # The reader's own defaults, not the form's, decide a blank port.
    email_setup.save(
        dict(FIELDS, imap_port="", smtp_port="", imap_tls=False, smtp_tls=False)
    )
    cfg, why = email_mod._account()
    assert (cfg["imap_port"], cfg["smtp_port"]) == (143, 587), cfg


def test_the_file_is_0600_in_a_0700_dir_and_holds_no_password(tmp_path, monkeypatch):
    home = _config_home(tmp_path)
    monkeypatch.setattr(email_setup.files, "config_home", lambda: home)
    path = email_setup.save(dict(FIELDS))
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    text = path.read_text(encoding="utf-8")
    for name in ("password", "app_password", "token"):
        assert name not in json.loads(text), name
    assert "me@example.org" in text


def test_status_is_missing_configured_or_bad_file(tmp_path, monkeypatch):
    home = _config_home(tmp_path)
    monkeypatch.setattr(email_setup.files, "config_home", lambda: home)
    word, message = email_setup.status()
    assert word == email_setup.MISSING, (word, message)
    assert "no email account" in message.lower()

    email_setup.save(dict(FIELDS))
    word, message = email_setup.status()
    assert word == email_setup.CONFIGURED, (word, message)
    assert "imap.example.org" in message and "password" not in message

    path = email_setup.account_path()
    path.write_text("{not json", encoding="utf-8")
    word, message = email_setup.status()
    assert word == email_setup.BAD_FILE, (word, message)
    _cfg, skill_why = email_mod._account()
    assert message == skill_why, (message, skill_why)


def test_a_bad_file_names_the_exact_skill_refusal(tmp_path, monkeypatch):
    home = _config_home(tmp_path)
    monkeypatch.setattr(email_setup.files, "config_home", lambda: home)
    path = email_setup.account_path()
    for payload in (
        "[]",
        json.dumps(dict(FIELDS, app_password="secret")),
        json.dumps({"imap_host": "i"}),
    ):
        path.write_text(payload, encoding="utf-8")
        word, message = email_setup.status()
        _cfg, skill_why = email_mod._account()
        assert word == email_setup.BAD_FILE, (word, message)
        assert message == skill_why, (message, skill_why)
        assert message, "an empty refusal is not an explanation"


def test_save_refuses_a_password_and_an_incomplete_account(tmp_path, monkeypatch):
    home = _config_home(tmp_path)
    monkeypatch.setattr(email_setup.files, "config_home", lambda: home)
    with pytest.raises(ValueError) as exc:
        email_setup.save(dict(FIELDS, password="hunter2"))
    assert "session keyring" in str(exc.value)
    assert not email_setup.account_path().exists(), "a refused save wrote a file"

    with pytest.raises(ValueError) as exc:
        email_setup.save({"imap_host": "i"})
    assert "needs both imap_host and smtp_host" in str(exc.value)

    with pytest.raises(ValueError) as exc:
        email_setup.save(dict(FIELDS, imap_port="not a number"))
    assert "imap_port must be a number" in str(exc.value)


def test_setup_never_contacts_a_mail_server(tmp_path, monkeypatch):
    home = _config_home(tmp_path)
    monkeypatch.setattr(email_setup.files, "config_home", lambda: home)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("setup contacted a mail server")

    monkeypatch.setattr(email_mod.imaplib, "IMAP4_SSL", forbidden)
    monkeypatch.setattr(email_mod.imaplib, "IMAP4", forbidden)
    monkeypatch.setattr(email_mod.smtplib, "SMTP_SSL", forbidden)
    monkeypatch.setattr(email_mod.smtplib, "SMTP", forbidden)
    email_setup.status()
    email_setup.save(dict(FIELDS))
    email_setup.status()
    email_setup.read_fields()


# The surface: build the real SettingsWindow and drive the real Save button.

_REPO = pathlib.Path(__file__).resolve().parents[1]
_PKG = str(_REPO / "usr" / "lib" / "shani-chronoa")

_SURFACE = textwrap.dedent(
    """
    import json, sys
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Gtk, Adw, GLib
    Adw.init()
    sys.path.insert(0, PKG)
    from shani_chronoa.config import ChronoaConfig
    from shani_chronoa import email_setup

    out = {"groups": 0, "titles": [], "status": "", "status_before": "",
           "note": "", "file": None, "error": "", "modes": [], "selected": ""}

    def walk(node, acc):
        child = node.get_first_child()
        while child:
            walk(child, acc)
            acc.append(child)
            child = child.get_next_sibling()
        return acc

    def find_button(node):
        child = node.get_first_child()
        while child:
            if isinstance(child, Gtk.Button) and child.get_label() == "Save account":
                return child
            found = find_button(child)
            if found:
                return found
            child = child.get_next_sibling()
        return None

    class App(Gtk.Application):
        def __init__(s):
            super().__init__(application_id="test.chronoa.email.setup")
            s.config = ChronoaConfig()
            s.window = None
            s._wake_word_active = False
        def activate_action(s, name, arg):
            pass

    def collect(app):
        try:
            import os
            preload = os.environ.get("EMAIL_PRELOAD")
            if preload:
                email_setup.save(json.loads(preload))
            from shani_chronoa.settings_window import SettingsWindow
            window = SettingsWindow(app)
            nodes = walk(window.get_child(), [])
            out["groups"] = len([n for n in nodes
                                 if isinstance(n, Adw.PreferencesGroup)
                                 and n.get_title() == "Email account"])
            rows = [n for n in nodes if isinstance(n, Adw.PreferencesRow)]
            out["titles"] = sorted(r.get_title() or "" for r in rows)
            before = [r for r in rows if r.get_title() == "Account status"]
            out["status_before"] = (before[0].get_subtitle() or "") if before else ""
            notes = [r for r in rows if r.get_title() == "Password"]
            out["note"] = (notes[0].get_subtitle() or "") if notes else ""
            for row in rows:
                if isinstance(row, Adw.ComboRow) and \
                        row.get_title() == "Connection security":
                    model = row.get_model()
                    out["modes"] = [model.get_item(i).get_string()
                                    for i in range(model.get_n_items())]
                    out["selected"] = model.get_item(row.get_selected()).get_string()

            values = {"IMAP host": "imap.test", "IMAP port": "993",
                      "SMTP host": "smtp.test", "SMTP port": "465",
                      "Account user": "me@test.org"}
            for row in rows:
                if isinstance(row, Adw.EntryRow) and row.get_title() in values:
                    row.set_text(values.pop(row.get_title()))

            button = find_button(window)
            if button is None:
                out["error"] = "no Save account button in the built window"
            else:
                button.emit("clicked")
                after = [r for r in rows if r.get_title() == "Account status"]
                out["status"] = (after[0].get_subtitle() or "") if after else ""
                path = email_setup.account_path()
                data = json.loads(path.read_text())
                out["file"] = {
                    "data": data,
                    "mode": path.stat().st_mode & 0o777,
                    "dir_mode": path.parent.stat().st_mode & 0o777,
                }
        except Exception:
            import traceback
            out["error"] = traceback.format_exc()[-900:]
        finally:
            app.quit()

    app = App()
    app.connect("activate", collect)
    GLib.timeout_add_seconds(120, lambda: (app.quit(), False)[1])
    app.run([])
    print("RESULT" + json.dumps(out))
    """
).replace("PKG", repr(_PKG))


@pytest.fixture(scope="module")
def surface_factory(tmp_path_factory, compiled_schema_dir):
    """Build the real SettingsWindow; `preload` seeds the account file first."""

    def build(preload=None):
        work = tmp_path_factory.mktemp("email-setup-surface")
        for name in ("config", "data", "state"):
            (work / name).mkdir()
        env = dict(os.environ)
        env.update(
            {
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": _PKG,
                "XDG_CONFIG_HOME": str(work / "config"),
                "XDG_DATA_HOME": str(work / "data"),
                "XDG_STATE_HOME": str(work / "state"),
                "GSETTINGS_SCHEMA_DIR": str(compiled_schema_dir),
                "GSETTINGS_BACKEND": "keyfile",
                "SHANI_CHRONOA_KEYRING": "0",
            }
        )
        if preload is not None:
            env["EMAIL_PRELOAD"] = json.dumps(preload)
        proc = subprocess.run(
            [sys.executable, "-c", _SURFACE],
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        lines = [line for line in proc.stdout.splitlines() if line.startswith("RESULT")]
        assert lines, (
            "the SettingsWindow harness produced no RESULT line:\n"
            f"{proc.stdout}\n{proc.stderr[-2000:]}"
        )
        return json.loads(lines[0][len("RESULT") :])

    return build


@pytest.fixture(scope="module")
def surface(surface_factory):
    return surface_factory()


def test_the_form_is_reachable_from_settings(surface):
    assert not surface["error"], surface["error"]
    assert surface["groups"] == 1, (
        "no Email account group in the built SettingsWindow - a form nobody can reach"
    )
    for title in (
        "IMAP host",
        "IMAP port",
        "SMTP host",
        "SMTP port",
        "Account user",
        "Connection security",
        "Account status",
        "Password",
    ):
        assert title in surface["titles"], (title, surface["titles"])
    assert surface["modes"] == ["TLS (recommended)", "None (plain text)"], surface[
        "modes"
    ]
    assert surface["selected"] == "TLS (recommended)", surface["selected"]


def test_the_form_reports_not_configured_then_configured_without_a_password(surface):
    assert "not configured" in surface["status_before"].lower(), surface[
        "status_before"
    ]
    assert "keyring" in surface["note"].lower(), surface["note"]
    assert "configured" in surface["status"].lower(), surface["status"]
    payload = surface["file"]
    assert payload is not None, "the Save button did not write the account file"
    assert payload["mode"] == 0o600, payload
    assert payload["dir_mode"] == 0o700, payload
    for name in ("password", "app_password", "token"):
        assert name not in payload["data"], name
    assert payload["data"]["user"] == "me@test.org"


def test_a_hand_written_mixed_security_mode_is_not_silently_rewritten(
    surface_factory,
):
    surface = surface_factory(
        {
            "imap_host": "imap.test",
            "smtp_host": "smtp.test",
            "user": "me@test.org",
            "imap_tls": True,
            "smtp_tls": False,
        }
    )
    assert not surface["error"], surface["error"]
    assert "Mixed (as in the file)" in surface["modes"], surface["modes"]
    assert surface["selected"] == "Mixed (as in the file)", surface["selected"]
    data = surface["file"]["data"]
    assert data["imap_tls"] is True and data["smtp_tls"] is False, data
