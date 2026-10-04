"""The background-mode surface, as a built widget.

Pinned here because the properties are the ones a reader cannot see by reading
the module: the setting is written through the same call the settings window
makes, a `systemctl` that will not answer renders as *could not ask* instead of
claiming the daemon is stopped, the panel asks systemd read-only questions and
nothing else, and a log too long to show says how much was withheld.

The systemctl and journalctl answers come from real stub executables on `PATH`,
run as real subprocesses. A `MagicMock` for `subprocess.run` cannot catch the
mistake this module exists to avoid, which is reading a non-zero exit code as
a failed question - on this machine `is-enabled` on a unit that is not
installed and `is-active` on one that is not running *both* exit 4 and print
the real answer on stdout.

Rows and the log are read by walking the built widget tree for real widgets and
css classes, not by reading a list the module stashed on itself: a count read
back out of the thing being counted is the mistake this repo deleted a test for
(the visual-regression suite in AGENTS.md).
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import textwrap
import types
from pathlib import Path

import gi
import pytest

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa.gui.surfaces import daemon  # noqa: E402

REGISTRY_SOURCE = Path(daemon.__file__).parent / "__init__.py"

#: The two read-only questions this panel is allowed to ask.
READ_ONLY_QUESTIONS = {
    "--user is-enabled shani-chronoa-daemon.service",
    "--user is-active shani-chronoa-daemon.service",
}


def _stub(tmp_path, name, body):
    """An executable stub on its own PATH directory, so nothing real is found."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


def _systemctl_stub(tmp_path, monkeypatch, body):
    """Put a stub `systemctl` alone on PATH and return its call log."""
    log = tmp_path / "systemctl.calls"
    bin_dir = _stub(tmp_path, "systemctl", f'echo "$@" >> "{log}"\n' + body)
    # PATH is *only* the stub directory, so a real systemctl or journalctl
    # cannot answer a question this test meant to leave unanswered.
    monkeypatch.setenv("PATH", str(bin_dir))
    return log


def _journalctl_stub(tmp_path, monkeypatch, body):
    bin_dir = _stub(tmp_path, "journalctl", body)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return bin_dir


def _app(config=None):
    return types.SimpleNamespace(config=config)


def _rows(widget):
    """[(heading, [value texts])] for every row in the built tree.

    A row's value is every descendant label carrying `VALUE_CSS`, not just its
    immediate children: the setting row puts its state line inside a control box
    beside the switch, and a walk that stopped at the first level would report
    that row as having no state at all.

    The heading is found by walking *into* the row rather than across its direct
    children only, because the rows are libadwaita rows now: an `Adw.ActionRow`
    keeps its title and subtitle labels inside boxes of its own (measured on
    libadwaita 1.5.0: row -> header box -> title box -> title label), and an
    `Adw.PreferencesGroup` keeps its title label the same way. A direct-children
    scan finds no heading in either and reports the panel as having no rows at
    all - which is what it did until this walk was added. Nothing else about the
    rows changed: they are still located by `ROW_CSS` in the built tree, the
    values are still the `VALUE_CSS` labels underneath, and every assertion about
    their text is unchanged.
    """
    found = []

    def value_labels(node):
        out = []
        child = node.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Label) and daemon.VALUE_CSS in child.get_css_classes():
                out.append(child.get_text())
            out.extend(value_labels(child))
            child = child.get_next_sibling()
        return out

    def first_label(node):
        child = node.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Label):
                return child
            found_inner = first_label(child)
            if found_inner is not None:
                return found_inner
            child = child.get_next_sibling()
        return None

    def walk(node):
        if daemon.ROW_CSS in node.get_css_classes():
            heading = first_label(node)
            values = value_labels(node)
            if heading is not None and values:
                found.append((heading.get_text(), values))
        child = node.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(widget)
    return found


def _row_value(widget, heading):
    for title, values in _rows(widget):
        if title == heading:
            return "\n".join(values)
    raise AssertionError(f"no row titled {heading!r} in {_rows(widget)!r}")


def _labels(widget):
    out = []

    def walk(node):
        if isinstance(node, Gtk.Label):
            text = node.get_text()
            if text.strip():
                out.append(text)
        child = node.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(widget)
    return out


def _switches(widget):
    found = []

    def walk(node):
        child = node.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Switch):
                found.append(child)
            walk(child)
            child = child.get_next_sibling()

    walk(widget)
    return found


class TestModuleContract:
    def test_it_exports_the_three_names_the_registry_reads(self):
        assert daemon.TITLE == "Background mode"
        assert daemon.ICON == "emblem-system-symbolic"
        assert callable(daemon.build)

    def test_the_icon_is_a_real_icon_name_on_this_machine(self):
        """A symbolic icon name that is not installed renders as a blank
        space-shaped gap, which is invisible to a test that only checks the
        string is non-empty."""
        roots = [Path("/usr/share/icons"), Path("/usr/local/share/icons")]
        found = [
            svg
            for root in roots
            if root.is_dir()
            for svg in root.rglob(f"{daemon.ICON}.svg")
        ]
        if not any(root.is_dir() for root in roots):
            pytest.skip("no icon theme installed on this machine")
        assert found, f"{daemon.ICON} is not an icon this machine has"

    def test_the_registry_already_names_this_surface(self):
        """`gui/surfaces/__init__.py` lists surface names in `SURFACE_IDS` and
        reads `TITLE`/`ICON`/`build` off the module it imports for each. Read
        the registry's source rather than calling `all_surfaces()`: that also
        imports `model`, which does not exist in this tree, so a failure there
        would say nothing about this wiring."""
        source = REGISTRY_SOURCE.read_text(encoding="utf-8")
        assert re.search(r'"daemon"', source), (
            "gui/surfaces/__init__.py never names a 'daemon' surface, so this "
            "module is built by nothing that runs"
        )

    def test_the_names_it_exports_are_the_ones_the_registry_reads(self):
        for name in ("TITLE", "ICON", "build"):
            assert hasattr(daemon, name), name


class TestBuild:
    def test_build_returns_a_gtk_widget(self):
        assert isinstance(daemon.build(_app()), Gtk.Widget)

    def test_an_app_with_no_settings_builds_a_disabled_switch(self):
        """A window that has not loaded its settings is a normal state, not a
        broken install, and an exception here takes the whole window down."""
        widget = daemon.build(types.SimpleNamespace())
        switches = _switches(widget)
        assert len(switches) == 1, f"expected the one setting switch, got {switches}"
        assert not switches[0].get_sensitive(), "a switch with nothing to write to"
        assert "no settings available" in _row_value(widget, "Background mode")

    def test_the_microphone_sentence_is_on_the_panel(self):
        """The reason this setting is safe, stated where someone switching it
        on will read it - not only in daemon.py's docstring."""
        said = " ".join(_labels(daemon.build(_app()))).lower()
        assert "microphone stays off" in said
        assert "window is closed" in said

    def test_the_unit_is_named_on_the_panel(self):
        said = " ".join(_labels(daemon.build(_app())))
        assert daemon.UNIT in said
        assert daemon.CONFIG_KEY in said


class TestTheToggle:
    def test_it_reflects_the_config_key(self, chronoa_config):
        assert chronoa_config.get_bool("background-mode-enabled", False) is False
        off = _switches(daemon.build(_app(chronoa_config)))[0]
        assert off.get_active() is False

        chronoa_config.set("background-mode-enabled", "true")
        on = _switches(daemon.build(_app(chronoa_config)))[0]
        assert on.get_active() is True

    def test_flipping_it_writes_that_key_and_nothing_else(self, chronoa_config):
        widget = daemon.build(_app(chronoa_config))
        switch = _switches(widget)[0]
        other_before = chronoa_config.get_bool("privacy-mode", True)

        switch.set_active(True)
        assert chronoa_config.get_bool("background-mode-enabled", False) is True

        switch.set_active(False)
        assert chronoa_config.get_bool("background-mode-enabled", False) is False
        assert chronoa_config.get_bool("privacy-mode", True) is other_before, (
            "the toggle wrote a second key"
        )

    def test_the_write_lands_in_this_test_s_own_config_directory(
        self, chronoa_config, tmp_path
    ):
        """The keyfile backend this suite runs on puts its store under
        `$XDG_CONFIG_HOME`, which the harness points at this test's own home. So
        finding the key *by name* in that file is what proves the write went to a
        temp directory and not to a real user's dconf."""
        store = Path(os.environ["XDG_CONFIG_HOME"]) / "glib-2.0" / "settings" / "keyfile"
        switch = _switches(daemon.build(_app(chronoa_config)))[0]
        switch.set_active(True)
        assert chronoa_config.get_bool("background-mode-enabled", False) is True
        assert store.is_file(), f"nothing was written at {store}"
        assert "background-mode-enabled" in store.read_text(encoding="utf-8")
        assert str(tmp_path) in str(store)

    def test_a_refused_write_puts_the_switch_back(self, chronoa_config, monkeypatch):
        """A switch showing a setting nobody changed is the most misleading
        thing this panel could do, so it is re-derived from the key."""
        widget = daemon.build(_app(chronoa_config))
        switch = _switches(widget)[0]

        def refuse(*_args, **_kwargs):
            raise OSError("dconf is not answering")

        monkeypatch.setattr(chronoa_config, "set", refuse)
        switch.set_active(True)
        assert switch.get_active() is False, "a refused write left the switch on"


class TestAskingSystemd:
    def test_a_real_stopped_unit_reads_as_stopped(self, tmp_path, monkeypatch):
        """The contrast the unknown state is measured against: these two exit
        codes are both non-zero, and both print the real answer."""
        _systemctl_stub(tmp_path, monkeypatch, textwrap.dedent(
            'case "$2" in\n'
            '  is-enabled) echo disabled; exit 1;;\n'
            '  is-active) echo inactive; exit 3;;\n'
            'esac\n'
        ))
        widget = daemon.build(_app())
        assert "disabled" in _row_value(widget, "Unit file")
        assert "stopped" in _row_value(widget, "Running now")

    def test_an_erroring_systemctl_does_not_claim_the_unit_is_stopped(
        self, tmp_path, monkeypatch
    ):
        log = _systemctl_stub(tmp_path, monkeypatch, textwrap.dedent(
            'echo "Failed to connect to bus: No such file or directory" >&2\n'
            'exit 1\n'
        ))
        widget = daemon.build(_app())
        running = _row_value(widget, "Running now")
        assert "could not ask" in running, running
        assert "stopped" not in running, (
            "systemctl failed to answer and the panel claimed the unit is "
            "stopped, which is the confident wrong answer"
        )
        unit_file = _row_value(widget, "Unit file")
        assert "could not ask" in unit_file, unit_file
        # It asked; it just got no answer.
        assert log.read_text().splitlines() == [
            "--user is-enabled shani-chronoa-daemon.service",
            "--user is-active shani-chronoa-daemon.service",
        ]

    def test_the_negative_control_makes_that_error_read_as_stopped(
        self, tmp_path, monkeypatch
    ):
        """The control for the assertion above: the *same* erroring stub, with
        the subprocess wrapper replaced by one that answers anyway. The unknown
        state must then be replaced by a confident 'stopped', which is exactly
        why `could not ask` in the test above is a real assertion. Patched on
        `gui.surfaces.daemon` - where `_query` reads the name - and not on
        `subprocess`, whose copy nobody calls."""
        _systemctl_stub(tmp_path, monkeypatch, textwrap.dedent(
            'echo "Failed to connect to bus: No such file or directory" >&2\n'
            'exit 1\n'
        ))
        assert "could not ask" in _row_value(daemon.build(_app()), "Running now"), (
            "the control did not start from the state it is meant to break"
        )

        def answers_even_so(which):
            word = "disabled" if which == "is-enabled" else "inactive"
            return subprocess.CompletedProcess(["systemctl", "--user", which], 0, word, "")

        # A context, not `monkeypatch.undo()`: undo would also drop the PATH
        # this fixture set, and the real systemctl on this machine would answer
        # "inactive" - which is the state under test, arriving from the wrong
        # cause.
        with monkeypatch.context() as control:
            control.setattr(daemon, "_systemctl", answers_even_so)
            controlled = _row_value(daemon.build(_app()), "Running now")
            assert "could not ask" not in controlled, (
                "an unanswerable question still rendered as unknown, so the "
                "assertion in the test above cannot fail and proves nothing"
            )
            assert "stopped" in controlled, controlled

        restored = _row_value(daemon.build(_app()), "Running now")
        assert "could not ask" in restored, "restoring the wrapper lost the state"

    def test_no_systemctl_at_all_is_could_not_ask(self, tmp_path, monkeypatch):
        _systemctl_stub(tmp_path, monkeypatch, "exit 0\n")
        (tmp_path / "bin" / "systemctl").unlink()
        widget = daemon.build(_app())
        assert "could not ask" in _row_value(widget, "Running now")
        assert "systemctl is not installed" in _row_value(widget, "Running now")

    def test_the_panel_only_asks_read_only_questions(self, tmp_path, monkeypatch):
        """No writes at all: the unit is enabled or disabled by the app at
        startup (`app/desktop_integration.py:_sync_background_mode`), and a
        panel that reports a unit while changing it is how a machine gets a
        service nobody chose."""
        log = _systemctl_stub(tmp_path, monkeypatch, textwrap.dedent(
            'case "$2" in\n'
            '  is-enabled) echo enabled; exit 0;;\n'
            '  is-active) echo active; exit 0;;\n'
            'esac\n'
        ))
        widget = daemon.build(_app())
        _switches(widget)[0].set_active(True)
        assert log.read_text().splitlines(), "the stub was never called"
        for call in log.read_text().splitlines():
            assert call in READ_ONLY_QUESTIONS, (
                f"the panel asked something other than a read-only question: {call!r}"
            )

    def test_an_enabled_and_running_unit_says_so(self, tmp_path, monkeypatch):
        _systemctl_stub(tmp_path, monkeypatch, textwrap.dedent(
            'case "$2" in\n'
            '  is-enabled) echo enabled; exit 0;;\n'
            '  is-active) echo active; exit 0;;\n'
            'esac\n'
        ))
        widget = daemon.build(_app())
        assert "installed and enabled" in _row_value(widget, "Unit file")
        assert "running" in _row_value(widget, "Running now")

    def test_a_unit_that_is_not_installed_says_not_installed(
        self, tmp_path, monkeypatch
    ):
        """Different from "could not ask", and from "disabled": the file is not
        on this machine at all, which is a fact about the install."""
        _systemctl_stub(tmp_path, monkeypatch, textwrap.dedent(
            'case "$2" in\n'
            '  is-enabled) echo not-found; exit 4;;\n'
            '  is-active) echo inactive; exit 4;;\n'
            'esac\n'
        ))
        widget = daemon.build(_app())
        assert "not installed" in _row_value(widget, "Unit file")
        assert "could not ask" not in _row_value(widget, "Unit file")


class TestTheLogTail:
    def _log_text(self, widget):
        found = []

        def walk(node):
            if daemon.LOG_CSS in node.get_css_classes() and isinstance(node, Gtk.Label):
                found.append(node.get_text())
            child = node.get_first_child()
            while child is not None:
                walk(child)
                child = child.get_next_sibling()

        walk(widget)
        return "\n".join(found)

    def test_a_longer_log_is_cut_and_the_cut_is_admitted(self, tmp_path, monkeypatch):
        """A silently shortened tail reads as the whole log, which is how a
        user concludes the daemon said nothing about the failure they are
        looking at."""
        total = daemon.JOURNAL_LINES + 7
        _systemctl_stub(tmp_path, monkeypatch, "exit 1\n")
        # A shell loop with no external command in it: PATH here is only the
        # stub directory, so `seq` or `printf` would not be found and the stub
        # would silently produce nothing.
        _journalctl_stub(tmp_path, monkeypatch, (
            "n=1\n"
            f'while [ "$n" -le {total} ]; do echo "line $n"; n=$((n+1)); done\n'
        ))
        widget = daemon.build(_app())
        text = self._log_text(widget)
        shown = [line for line in text.splitlines() if line.startswith("line ")]
        assert len(shown) == daemon.JOURNAL_LINES, shown
        assert shown[-1] == f"line {total}", "the newest line is the one that must survive"
        # The note sits under the log as the row's own text, not inside it.
        note = _row_value(widget, "What the daemon logged")
        assert f"{total - daemon.JOURNAL_LINES} earlier lines" in note, note
        assert "not shown" in note, note

    def test_a_short_log_is_shown_whole(self, tmp_path, monkeypatch):
        _systemctl_stub(tmp_path, monkeypatch, "exit 1\n")
        _journalctl_stub(tmp_path, monkeypatch, (
            "echo 'INFO shani_chronoa.daemon: Chronoa background mode running'\n"
            "echo 'INFO shani_chronoa.daemon: Chronoa background mode stopped'\n"
        ))
        widget = daemon.build(_app())
        text = self._log_text(widget)
        assert "background mode running" in text
        assert "background mode stopped" in text
        assert "not shown" not in _row_value(widget, "What the daemon logged"), (
            "a two-line log was cut, so the cut note is wrong"
        )

    def test_an_empty_journal_is_empty_not_silent_success(self, tmp_path, monkeypatch):
        """journalctl exits 0 and prints one placeholder line for a journal with
        nothing in it, which must not be shown as a log line."""
        _systemctl_stub(tmp_path, monkeypatch, "exit 1\n")
        _journalctl_stub(tmp_path, monkeypatch, "echo '-- No entries --'\n")
        text = self._log_text(daemon.build(_app()))
        assert "no entries" in text.lower(), text
        assert "No entries --" not in text.splitlines()[0:1], text

    def test_a_journal_that_cannot_be_read_says_so(self, tmp_path, monkeypatch):
        """A read failure and an absent log are different answers, and only one
        of them means the daemon had nothing to say."""
        _systemctl_stub(tmp_path, monkeypatch, "exit 1\n")
        _journalctl_stub(tmp_path, monkeypatch,
                         "echo 'No journal files were found.' >&2\nexit 0\n")
        text = self._log_text(daemon.build(_app()))
        assert "could not be read" in text, text
        assert "No journal files were found" in text, text

    def test_no_journalctl_says_so_rather_than_showing_nothing(
        self, tmp_path, monkeypatch
    ):
        _systemctl_stub(tmp_path, monkeypatch, "exit 1\n")
        widget = daemon.build(_app())
        text = self._log_text(widget)
        assert "journalctl is not installed" in text, text

    def test_the_log_says_where_it_reads_from(self):
        """The daemon writes to stderr, so the journal is where its output
        lands; the panel has to say so rather than implying a log file."""
        said = " ".join(_labels(daemon.build(_app())))
        assert "journal" in said.lower()
        assert "stderr" in said.lower()

