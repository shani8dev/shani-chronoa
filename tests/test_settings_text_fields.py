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
    found["__window__"] = window
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
    doubled = [title for title, row in rows.items() if not title.startswith("__")
               if any(type(n) is Gtk.Entry for n in _walk(row)[1:])]
    assert not doubled, f"rows with a second, unconnected field: {doubled}"


def test_secret_rows_are_password_rows(rows):
    for title, row in rows.items():
        if not title.startswith("__") and "key" in title.lower():
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


def test_the_voice_style_choice_is_on_the_voice_page(rows):
    """The "Voice" style row was never built: it read `config.get_string`, which
    ChronoaConfig does not have, and the AttributeError skipped the whole row
    (2026-10-08). Built from the same window as the text rows above."""
    window = rows["__window__"]
    titles = [n.get_title() for n in _walk(window) if isinstance(n, Adw.ComboRow)]
    assert "Voice" in titles, f"no Voice style row; combo rows found: {titles}"


class TestTheModelCanBeStartedFromHere:
    """**The reported gap, in a person's words:** "when shifting to that Kilo
    APIs, needs button to run the model etc".

    The presence row said *Free the model* and *Asleep* and nothing else. Cycles
    are fine when the caller can see the state - and the state here is "the model
    is off because the cloud fallback answered instead", which the row does not
    show. So a person who wanted their local model back had to press a button
    labelled with the opposite of what they wanted, three times.

    **The cycle was not removed.** `Asleep` means "stop it *and* do not start it
    again to answer the next question", which no pair of Run/Stop buttons can
    express. Dropping it to fit two buttons would have removed a capability to fix
    a different one.
    """

    def _row(self, rows):
        for widget in _walk(rows["__window__"]):
            if (isinstance(widget, Adw.ActionRow)
                    and "Right now" in (widget.get_title() or "")):
                return widget
        pytest.fail("the 'Right now' row is not in the Settings window")

    def test_the_row_has_both_a_run_and_a_free(self, rows):
        """Both controls, and the release one names the action it will take.

        **This used to assert the literal `"Free the model"`**, which was true
        only because the label was written once at build time and never read
        again. With the row refreshed from the real state, the release button
        carries `Presence.action()` for wherever we are - "Free the model" when
        the model is loaded, "Go to sleep" once it is released, "Wake up" when
        asleep - so a machine with no model answering correctly reads "Go to
        sleep" and the old assertion failed on it. The contract is what is
        asserted now: the label is *an* action, and it is the right one for the
        state the row is describing.
        """
        from shani_chronoa import local_llm, presence

        buttons = [b.get_label() for b in _walk(self._row(rows))
                   if isinstance(b, Gtk.Button)]
        assert "Run the model" in buttons, (
            f"no way to start the model from the one page about models: {buttons}")
        chosen = getattr(rows["__window__"].app, "_presence", None)
        if chosen is None:
            chosen = presence.detect(local_llm.is_up)
        assert chosen.action() in buttons, (
            f"the control that releases it went missing: {buttons} "
            f"(expected {chosen.action()!r} for {chosen.name})")

    def test_the_run_button_is_not_just_drawn(self, rows):
        """**Presence is not wiring.** A button with no handler is the dead-control
        class this repo records four times - the orb shipped that way - so the
        handler is checked, not the label.
        """
        from shani_chronoa.settings_window import window as module
        assert hasattr(module.SettingsWindow, "_run_model"), (
            "the Run button has no handler in the class, so it draws and does "
            "nothing")

    def test_both_run_off_the_main_loop(self, rows):
        """A `systemctl --user start` on the GTK thread is a frozen window, and
        `start_service` waits for a cold start that takes seconds. Asserted by
        reading the source for the thread, because the freeze is otherwise only
        visible to a person.
        """
        import inspect

        from shani_chronoa.settings_window import window as module
        for name in ("_run_model", "_cycle_presence"):
            source = inspect.getsource(getattr(module.SettingsWindow, name))
            assert "threading.Thread" in source, (
                f"{name} does its systemctl work on the main loop")
