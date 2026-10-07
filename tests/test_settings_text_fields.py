"""Every text setting is one field, and typing in it saves.

`_entry` packed a `Gtk.Entry` inside an `Adw.EntryRow` - itself an editable
field - so each row showed two fields and only the inner one was connected;
the secret variant's reveal button hid the field on its second press. And the
custom model server (three schema keys the brain already reads) had no field at
all, so it could only be set with `gsettings`.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk  # noqa: E402

Adw.init()


class _App(Gtk.Application):
    def __init__(self):
        from shani_chronoa.config import ChronoaConfig
        super().__init__(application_id="test.chronoa.textfields",
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.config = ChronoaConfig()
        self.window = None
        self._wake_word_active = False

    def activate_action(self, name, arg=None):
        pass


def _walk(node, out=None):
    out = [] if out is None else out
    out.append(node)
    child = node.get_first_child()
    while child is not None:
        _walk(child, out)
        child = child.get_next_sibling()
    return out


@pytest.fixture(scope="module")
def rows(gsettings_env_module):
    from shani_chronoa.settings_window import SettingsWindow
    app = _App()
    app.register(None)
    window = SettingsWindow(app)
    found = {r.get_title(): r for r in _walk(window) if isinstance(r, Adw.EntryRow)}
    # The window writes through this config; reading back through it keeps the
    # check inside the same settings store as the write, whichever HOME a later
    # test's autouse fixture points at.
    found["__config__"] = app.config
    return found


@pytest.fixture(scope="module")
def gsettings_env_module(tmp_path_factory, compiled_schema_dir):
    mp = pytest.MonkeyPatch()
    mp.setenv("GSETTINGS_BACKEND", "keyfile")
    mp.setenv("GSETTINGS_SCHEMA_DIR", str(compiled_schema_dir))
    yield
    mp.undo()


def test_no_text_row_carries_a_second_field(rows):
    assert rows, "the settings window built no text rows"
    doubled = [title for title, row in rows.items() if title != "__config__"
               if any(type(n) is Gtk.Entry for n in _walk(row)[1:])]
    assert not doubled, f"rows with a second, unconnected field: {doubled}"


def test_secret_rows_are_password_rows(rows):
    for title, row in rows.items():
        if title != "__config__" and "key" in title.lower():
            assert isinstance(row, Adw.PasswordEntryRow), title


def test_the_custom_model_server_can_be_set_from_settings(rows):
    """The fields exist and what they hold is an endpoint the brain will use.

    Not a control for the double-field bug: run against the old helper this
    still passes (`EntryRow.set_text` reached the saved value there), so the
    two structural tests above are what catch that regression - measured.
    """
    from shani_chronoa.cloud_llm import custom_provider
    for title in ("Server address", "Model name", "API key"):
        assert title in rows, f"no '{title}' field for the custom model server"
    rows["Server address"].set_text("http://192.168.1.20:8080/v1")
    rows["Model name"].set_text("qwen3-8b")
    config = rows["__config__"]
    url, model = config.get("custom-llm-base-url", ""), config.get("custom-llm-model", "")
    assert (url, model) == ("http://192.168.1.20:8080/v1", "qwen3-8b")
    assert custom_provider(url, model) is not None, "the brain would not use what was typed"
