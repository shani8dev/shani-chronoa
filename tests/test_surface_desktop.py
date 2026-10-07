"""The Desktop surface, as a built widget.

Pinned here because the properties are the ones a reader cannot see by reading
the module: that a row leads with the evidence word it actually used, that a
subprocess which failed says *could not ask* and never *not running*, that the
global-shortcut row carries its standing admission in the exact words the module
promises, and that a missing `.desktop` file is reported as missing rather than
as an empty MimeType list.

Every subprocess answer here comes from a real stub executable run as a real
subprocess, and `PATH` is **only** that stub directory - so a real `busctl`
cannot answer a question this test meant to leave unanswered, and cannot reach
the developer's own session bus. That is the mistake AGENTS.md records from the
global-shortcut test: Gio caches one session-bus connection per process, and a
test that only set `DBUS_SESSION_BUS_ADDRESS` once reached the real desktop
portal. `busctl` in a fresh subprocess would be a narrower version of the same
problem, and the fix is the same: nothing real on `PATH`.

Rows are found by walking the built tree for `ROW_CSS` and reading the
`VALUE_CSS` label underneath, never off a list the module stashed on itself -
which is the count read back out of the thing being counted, the mistake
AGENTS.md records a deleted visual-regression test for. The walk branches on
`common.adw_ready()` because the two row shapes are genuinely different: an
`Adw.ActionRow` keeps its title and subtitle labels inside boxes of its own
(measured on libadwaita 1.5: row -> header box -> title box -> title label) and
names the subtitle label `subtitle`, while the no-Adw fallback is a `Gtk.Box` of
two plain `Gtk.Label`s with no such class.
"""

from __future__ import annotations

import stat
import textwrap
import types
from pathlib import Path

import gi
import pytest

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa import secret_store  # noqa: E402
from shani_chronoa.gui.surfaces import common, desktop  # noqa: E402

REGISTRY_SOURCE = Path(desktop.__file__).parent / "__init__.py"

#: The one argv this panel is allowed to build. Anything else is a write, or a
#: question about something other than the session bus.
READ_ONLY_BUS_CALL = "--user --list --no-legend"


def _stub(tmp_path, name, body):
    """An executable stub on its own PATH directory, so nothing real is found."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _listing(*names):
    """A `busctl --user --list --no-legend` body printing `names`.

    The shape is the real one - a 47-character NAME column, then the rest - and
    the column width is measured, not assumed: the provider name is 32
    characters and the secret service's is 24, so neither is elided.
    """
    lines = [f'printf "%-47s %s\\n" {name} -' for name in names]
    return "".join(line + "\n" for line in lines) + "exit 0\n"


def _packaged(*parts) -> Path:
    """A path inside this checkout's `usr/`, the way the package installs it.

    `.../usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/desktop.py`, so the
    package root under `lib/` is four parents up and `usr/` is five.
    """
    return Path(desktop.__file__).resolve().parents[5].joinpath(*parts)


@pytest.fixture(autouse=True)
def _only_stub_binaries_on_path(tmp_path, monkeypatch):
    """`PATH` is this test's own stub directory, and nothing else.

    Autouse because every `build()` runs one `busctl`, so a test that only
    stubbed it in the cases that cared would let the others reach the real
    session bus. With no `bin/` directory yet, `busctl` is genuinely absent,
    which is the honest starting state for the rows that must say so.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    return bin_dir


def _busctl(tmp_path, monkeypatch, body, bin_dir=None):
    """Put a `busctl` stub alone on PATH and return its call log."""
    log = tmp_path / "busctl.calls"
    path = _stub(tmp_path, "busctl", f'echo "$@" >> "{log}"\n' + body)
    if bin_dir is not None and path.parent != bin_dir:
        raise AssertionError("the stub landed outside the directory on PATH")
    monkeypatch.setenv("PATH", str(path.parent))
    return log


def _app(config=None):
    return types.SimpleNamespace(config=config)


class _Config:
    """The two calls the panel is allowed to make, with a write recorder."""

    def __init__(self, **gates):
        self.gates = gates
        self.writes = []
        self.reads = []

    def get_bool(self, key, default=False):
        self.reads.append(key)
        if key not in self.gates:
            raise KeyError(key)
        return self.gates[key]

    def set(self, key, value):
        self.writes.append((key, value))


class _RaisingConfig:
    def get_bool(self, key, default=False):
        raise RuntimeError("dconf is not answering")


def _labels(node):
    """Every `Gtk.Label` under `node`, in order."""
    out = []
    child = node.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label):
            out.append(child)
        out.extend(_labels(child))
        child = child.get_next_sibling()
    return out


def _rows(widget):
    """[(heading, value)] for every row in the built tree.

    Branching on `common.adw_ready()` is not a convenience: it is the condition
    `common.row()` itself branches on when it decides which widget to build, and
    the two shapes put the answer in different places.
    """
    found = []

    def walk(node):
        if desktop.ROW_CSS in node.get_css_classes():
            if common.adw_ready():
                values = [label.get_text() for label in _labels(node)
                          if desktop.VALUE_CSS in label.get_css_classes()]
                headings = [label for label in _labels(node)
                            if desktop.VALUE_CSS not in label.get_css_classes()]
                if values:
                    found.append((headings[0].get_text(), "\n".join(values)))
            else:
                # The no-Adw row is a box of [title, subtitle] labels.
                texts = [label.get_text() for label in _labels(node)]
                if len(texts) >= 2:
                    found.append((texts[0], "\n".join(texts[1:])))
        child = node.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(widget)
    return found


def _row_value(widget, heading):
    for title, value in _rows(widget):
        if title == heading:
            return value
    raise AssertionError(
        f"no row titled {heading!r}; the panel has "
        f"{[title for title, _ in _rows(widget)]!r}"
    )


class TestModuleContract:
    def test_it_exports_the_names_the_registry_reads(self):
        assert desktop.TITLE == "Desktop"
        assert desktop.ICON == "applications-system-symbolic"
        assert desktop.SECTION == "Health and trust"
        assert callable(desktop.build)

    def test_the_icon_is_a_real_icon_name_on_this_machine(self):
        """A symbolic icon name that is not installed renders as a blank
        space-shaped gap, which is invisible to a test that only checks the
        string is non-empty."""
        roots = [Path("/usr/share/icons"), Path("/usr/local/share/icons")]
        if not any(root.is_dir() for root in roots):
            pytest.skip("no icon theme installed on this machine")
        found = [svg for root in roots if root.is_dir()
                 for svg in root.rglob(f"{desktop.ICON}.svg")]
        assert found, f"{desktop.ICON} is not an icon this machine has"

    def test_its_section_is_one_the_sidebar_knows(self):
        """`sections()` files a panel by the module's own `SECTION`; a value
        outside `SECTION_ORDER` puts it in an "Everything else" group instead."""
        from shani_chronoa.gui import surfaces

        assert desktop.SECTION in surfaces.SECTION_ORDER

    def test_the_registry_names_this_surface_exactly_once(self):
        """`gui/surfaces/__init__.py` lists surface names in `SURFACE_IDS` and
        reads `TITLE`/`ICON`/`build` off the module it imports for each.

        Read as source rather than through `all_surfaces()`, which imports every
        sibling panel and would report another module's problem as this one's.
        Exactly once as well as at least once: `all_surfaces()` builds a dict, so
        a name listed twice is invisible there and shows up in the sidebar as two
        entries for one panel.
        """
        # Counted in `SURFACE_IDS` - where a duplicate becomes two sidebar rows -
        # rather than across the whole file, which also counted the panel's
        # legitimate `SETTINGS_TARGETS` entry (its Settings gear) as a duplicate.
        import ast
        tree = ast.parse(REGISTRY_SOURCE.read_text(encoding="utf-8"))
        ids = next(ast.literal_eval(node.value) for node in tree.body
                   if isinstance(node, ast.Assign)
                   and any(getattr(t, "id", "") == "SURFACE_IDS" for t in node.targets))
        assert ids.count("desktop") == 1, (
            f"SURFACE_IDS must name 'desktop' exactly once; it has it {ids.count('desktop')} times")

    def test_the_names_it_exports_are_the_ones_the_registry_reads(self):
        for name in ("TITLE", "ICON", "build"):
            assert hasattr(desktop, name), name


class TestBuild:
    def test_build_returns_a_gtk_widget_on_a_bare_app(self):
        """An app that has not loaded its settings is a normal state, not a
        broken install, and an exception here takes the whole window down."""
        assert isinstance(desktop.build(_app()), Gtk.Widget)

    def test_it_builds_with_settings_too(self):
        config = _Config(**{desktop.SHORTCUT_KEY: True, desktop.DOCUMENT_KEY: False})
        assert isinstance(desktop.build(_app(config)), Gtk.Widget)

    def test_it_builds_when_reading_a_setting_raises(self):
        """A dconf that will not answer is a fact, not an exception."""
        widget = desktop.build(_app(_RaisingConfig()))
        assert isinstance(widget, Gtk.Widget)
        assert "could not be read" in _row_value(
            widget, "Global shortcut")

    def test_it_builds_when_app_config_itself_raises(self):
        class _Exploding:
            @property
            def config(self):
                raise RuntimeError("no settings yet")

        assert isinstance(desktop.build(_Exploding()), Gtk.Widget)

    def test_every_row_renders_and_is_read_once(self):
        """One row per declared question, no more and no fewer: a row that is
        built twice reads as two answers to one question."""
        widget = desktop.build(_app())
        assert [title for title, _ in _rows(widget)] == list(desktop.ROW_TITLES)

    def test_every_row_leads_with_one_of_the_evidence_words(self):
        """The closed vocabulary. A row that led with a phrase rather than a word
        is a row whose evidence is whatever the sentence felt like."""
        for title, value in _rows(desktop.build(_app())):
            lead = value.split(" - ", 1)[0].strip()
            assert lead in desktop.EVIDENCE_WORDS, (
                f"row {title!r} leads with {lead!r}, which is not one of "
                f"{list(desktop.EVIDENCE_WORDS)}"
            )

    def test_it_reads_the_session_bus_once_for_the_two_rows_that_share_it(
        self, tmp_path, monkeypatch
    ):
        log = _busctl(tmp_path, monkeypatch, _listing("org.freedesktop.DBus"))
        widget = desktop.build(_app())
        assert log.read_text().splitlines() == [READ_ONLY_BUS_CALL], (
            "two rows share one bus listing, so a second call would be a second "
            "question asked twice with a different answer"
        )
        # Both rows that read the bus got the same answer from that one call.
        assert "not running" in _row_value(widget, "Answering on the session bus")
        assert "not running" in _row_value(widget, "Desktop keyring")

    def test_it_asks_the_bus_only_read_only_questions(self, tmp_path, monkeypatch):
        log = _busctl(tmp_path, monkeypatch, _listing("org.freedesktop.DBus"))
        desktop.build(_app())
        for call in log.read_text().splitlines():
            assert call == READ_ONLY_BUS_CALL, (
                f"the panel asked something other than a read-only bus listing: {call!r}"
            )

    def test_it_writes_no_setting(self, tmp_path, monkeypatch):
        """The two gates it reports are granted in Settings. A panel that
        reported a gate by writing it would have made looking at the panel the
        thing that changed it."""
        config = _Config(**{desktop.SHORTCUT_KEY: True, desktop.DOCUMENT_KEY: True})
        desktop.build(_app(config))
        assert config.writes == [], f"the panel wrote {config.writes!r}"
        assert set(config.reads) <= {desktop.SHORTCUT_KEY, desktop.DOCUMENT_KEY}

    def test_it_also_builds_without_libadwaita(self, monkeypatch):
        """A headless builder may have no libadwaita, and `common` has a plain
        GTK answer for every helper. This exercises that answer end to end: the
        page, the groups and the rows all change shape, and `common.group()`
        returns a `Gtk.Box` (which takes `append`) instead of an
        `Adw.PreferencesGroup` (which takes `add`). Getting that branch wrong
        raises on the fallback path, which is the one a test that only ever has
        Adw never takes - and it would raise inside `build`, taking the window
        with it.

        Patched on `common`, where every helper reads it, rather than on this
        surface: this module calls `common.adw_ready()` itself for its escaping,
        and a copy set anywhere else would be a copy nobody reads.
        """
        monkeypatch.setattr(common, "adw_ready", lambda: False)
        widget = desktop.build(_app(_Config()))
        rows = _rows(widget)
        assert [title for title, _ in rows] == list(desktop.ROW_TITLES), (
            f"the no-Adw walk found {[t for t, _ in rows]!r}"
        )
        for title, value in rows:
            assert value.strip(), f"row {title!r} rendered with no answer at all"
            lead = value.split(" - ", 1)[0].strip()
            assert lead in desktop.EVIDENCE_WORDS, f"{title!r} led with {lead!r}"
        # Nothing here escaped, because the no-Adw row is a `Gtk.Label`, which
        # takes no markup and would print the entities themselves.
        assert "&amp;" not in _row_value(widget, "Global shortcut")


class TestTheSearchProvider:
    def test_a_service_file_that_is_not_there_says_not_installed(
        self, tmp_path, monkeypatch
    ):
        missing = tmp_path / "nowhere" / "dev.shani.chronoa.SearchProvider.service"
        monkeypatch.setattr(desktop, "SERVICE_FILE", str(missing))
        value = _row_value(desktop.build(_app()), "Search provider service file")
        assert value.startswith("not installed"), value
        assert str(missing) in value, (
            "the row must name the path it looked at, or 'not installed' is a "
            "verdict about an unnamed thing"
        )
        assert "could not ask" not in value, value

    def test_a_service_file_with_its_executable_present_says_installed(
        self, tmp_path, monkeypatch
    ):
        binary = _stub(tmp_path, "shani-chronoa-search", "exit 0\n")
        service = tmp_path / "dev.shani.chronoa.SearchProvider.service"
        service.write_text(textwrap.dedent(
            "[D-Bus Service]\n"
            f"Name={desktop.SEARCH_PROVIDER_NAME}\n"
            f"Exec={binary}\n"
        ))
        monkeypatch.setattr(desktop, "SERVICE_FILE", str(service))
        value = _row_value(desktop.build(_app()), "Search provider service file")
        assert value.startswith("installed"), value
        assert str(binary) in value, value

    def test_a_service_file_whose_binary_is_missing_still_says_installed(
        self, tmp_path, monkeypatch
    ):
        """The file is on disk; the program it names is not. Those are two
        different faults and only one of them is about the install - saying
        "not installed" here sends someone to reinstall a file that is present.
        """
        service = tmp_path / "dev.shani.chronoa.SearchProvider.service"
        service.write_text(
            "[D-Bus Service]\n"
            f"Name={desktop.SEARCH_PROVIDER_NAME}\n"
            "Exec=/usr/bin/definitely-not-here\n"
        )
        monkeypatch.setattr(desktop, "SERVICE_FILE", str(service))
        value = _row_value(desktop.build(_app()), "Search provider service file")
        assert value.startswith("installed"), value
        assert "/usr/bin/definitely-not-here" in value, value
        assert "D-Bus activation would fail" in value, value

    def test_a_name_mismatch_is_reported_rather_than_called_installed_bare(
        self, tmp_path, monkeypatch
    ):
        binary = _stub(tmp_path, "shani-chronoa-search", "exit 0\n")
        service = tmp_path / "dev.shani.chronoa.SearchProvider.service"
        service.write_text(f"[D-Bus Service]\nName=org.example.Other\nExec={binary}\n")
        monkeypatch.setattr(desktop, "SERVICE_FILE", str(service))
        value = _row_value(desktop.build(_app()), "Search provider service file")
        assert value.startswith("installed"), value
        assert "org.example.Other" in value, value

    def test_the_reported_service_file_is_the_real_installed_path(self):
        """Spelled from the provider's own bus name, so the two cannot drift."""
        assert desktop.SERVICE_FILE == (
            f"/usr/share/dbus-1/services/{desktop.SEARCH_PROVIDER_NAME}.service"
        )
        packaged = _packaged("share", "dbus-1", "services",
                             f"{desktop.SEARCH_PROVIDER_NAME}.service")
        assert packaged.is_file(), f"the package does not ship {packaged}"

    def test_a_provider_on_the_bus_says_running(self, tmp_path, monkeypatch):
        _busctl(tmp_path, monkeypatch,
                _listing("org.freedesktop.DBus", desktop.SEARCH_PROVIDER_NAME))
        value = _row_value(desktop.build(_app()), "Answering on the session bus")
        assert value.startswith("running"), value
        assert desktop.SEARCH_PROVIDER_NAME in value, value

    def test_a_provider_absent_from_a_real_listing_says_not_running(
        self, tmp_path, monkeypatch
    ):
        _busctl(tmp_path, monkeypatch, _listing("org.freedesktop.DBus"))
        value = _row_value(desktop.build(_app()), "Answering on the session bus")
        assert value.startswith("not running"), value
        assert "started on demand" in value, (
            "a D-Bus-activated provider that exits after two idle minutes is "
            "absent between searches; saying only 'not running' reads as broken"
        )

    def test_a_busctl_that_will_not_run_says_could_not_ask_not_not_running(
        self, tmp_path, monkeypatch
    ):
        """The measured case: with `DBUS_SESSION_BUS_ADDRESS` naming a socket
        that does not exist, real `busctl --user --list` exits 1 with *empty*
        stdout. Reading that as an empty bus is a confident wrong answer - it
        would report the search provider, the keyring and every other desktop
        integration as absent on a machine where the whole question went
        unasked."""
        _busctl(tmp_path, monkeypatch, textwrap.dedent(
            'echo "Failed to connect to bus: No such file or directory" >&2\n'
            "exit 1\n"
        ))
        value = _row_value(desktop.build(_app()), "Answering on the session bus")
        assert value.startswith("could not ask"), value
        assert "Failed to connect to bus" in value, value
        assert "not running" not in value, (
            "busctl failed to answer and the panel claimed nothing is answering"
        )
        assert "stopped" not in value, (
            "a failed question rendered as a definite negative"
        )

    def test_a_silent_busctl_is_could_not_ask_too(self, tmp_path, monkeypatch):
        """Exit 0 with no output at all is not a bus with nothing on it."""
        _busctl(tmp_path, monkeypatch, "exit 0\n")
        value = _row_value(desktop.build(_app()), "Answering on the session bus")
        assert value.startswith("could not ask"), value
        assert "busctl exited 0" in value, value

    def test_output_that_is_not_a_bus_listing_is_could_not_ask(
        self, tmp_path, monkeypatch
    ):
        """A shell stub's echo is not a bus name. dbus itself rejects a dotless
        destination (`dbus-send --dest=bogus` -> `invalid value (bogus) of
        "--dest"`), so a first token that is not a name is a failed question, not
        a bus with nothing on it."""
        _busctl(tmp_path, monkeypatch,
                'echo "Error acquiring bus name: Connection refused"\nexit 0\n')
        value = _row_value(desktop.build(_app()), "Answering on the session bus")
        assert value.startswith("could not ask"), value
        assert "Connection refused" not in value, (
            "an error that reached stdout was accepted as a bus name"
        )

    def test_no_busctl_at_all_is_could_not_ask(self, tmp_path, monkeypatch):
        _busctl(tmp_path, monkeypatch, _listing("org.freedesktop.DBus"))
        (tmp_path / "bin" / "busctl").unlink()
        value = _row_value(desktop.build(_app()), "Answering on the session bus")
        assert value.startswith("could not ask"), value
        assert "busctl is not installed" in value, value

    def test_the_negative_control_makes_that_failure_read_as_not_running(
        self, tmp_path, monkeypatch
    ):
        """The control for the assertion above: the *same* erroring stub, with
        the listing replaced by one that does answer. The unknown state must then
        be replaced by a definite negative, which is exactly why `could not ask`
        in the test above is a real assertion.

        Patched on `gui.surfaces.desktop`, where `_bus_names` is read - patching
        `subprocess` instead would set a copy nobody calls, and the test would
        pass for the wrong reason.
        """
        log = _busctl(tmp_path, monkeypatch, textwrap.dedent(
            'echo "Failed to connect to bus: No such file or directory" >&2\n'
            "exit 1\n"
        ))
        widget = desktop.build(_app())
        assert "could not ask" in _row_value(widget, "Answering on the session bus"), (
            "the control did not start from the state it is meant to break"
        )
        assert log.read_text().strip() == READ_ONLY_BUS_CALL, "the stub was never called"

        # A context, not `monkeypatch.undo()`: undo would also drop the PATH
        # this fixture set, and the real busctl on this machine would answer for
        # a machine where Chronoa is not installed - the state under test,
        # arriving from the wrong cause.
        with monkeypatch.context() as control:
            control.setattr(desktop, "_bus_names",
                             lambda: (["org.freedesktop.DBus"], ""))
            controlled = _row_value(
                desktop.build(_app()), "Answering on the session bus")
            assert "could not ask" not in controlled, (
                "an unanswerable question still rendered as unknown, so the "
                "assertion in the test above cannot fail and proves nothing"
            )
            assert controlled.startswith("not running"), controlled

        restored = _row_value(desktop.build(_app()), "Answering on the session bus")
        assert "could not ask" in restored, "restoring the probe lost the state"
        assert "not running" not in restored, restored


#: The sentence the shortcut row has to carry, spelled out here rather than read
#: off `desktop.UNPROVABLE_NOTE`. Asserting the constant would move both sides of
#: the comparison at once: rewording the module's constant - to "the portal
#: probably accepted it" - would leave the test green while the panel had stopped
#: saying the only thing that keeps it honest. Measured, by running that
#: mutation: with the assertions reading the constant, the whole suite stayed at
#: 51 passed. With them reading this string, it drops to 47.
REQUIRED_ADMISSION = "a portal bind cannot be proven from outside the process"


class TestTheGlobalShortcut:
    def test_it_reports_the_gate_and_the_standing_admission(self):
        for gates in ({desktop.SHORTCUT_KEY: True}, {desktop.SHORTCUT_KEY: False}):
            value = _row_value(desktop.build(_app(_Config(**gates))),
                              "Global shortcut")
            assert value.startswith("could not ask"), value
            assert REQUIRED_ADMISSION in value, (
                "a panel that cannot prove the bind must say so in the words the "
                "module promises, not in a reworded version of them"
            )
            assert desktop.UNPROVABLE_NOTE == REQUIRED_ADMISSION, (
                f"the module's own constant has drifted to "
                f"{desktop.UNPROVABLE_NOTE!r}"
            )
            said = "says on" if gates[desktop.SHORTCUT_KEY] else "says off"
            assert said in value, value
            assert desktop.SHORTCUT_KEY in value, value

    def test_an_unreadable_gate_is_still_could_not_ask_and_never_a_claim(self):
        value = _row_value(desktop.build(_app(_RaisingConfig())), "Global shortcut")
        assert value.startswith("could not ask"), value
        assert "dconf is not answering" in value, value
        assert REQUIRED_ADMISSION in value, value

    def test_no_settings_is_could_not_ask_not_off(self):
        """Reading a missing gate as "off" would tell someone their shortcut is
        switched off when nobody knows."""
        value = _row_value(desktop.build(_app()), "Global shortcut")
        assert value.startswith("could not ask"), value
        assert "no settings" in value, value
        assert "says off" not in value, value
        assert REQUIRED_ADMISSION in value, value

    def test_it_names_where_the_bind_would_be_logged(self):
        """The one thing a person can actually do about this: read the app's own
        log line. A row that only says "cannot be proven" leaves them stuck."""
        value = _row_value(desktop.build(_app()), "Global shortcut")
        assert "app/desktop_integration.py" in value, value
        assert "PortalError" in value, value


class TestDocumentSearch:
    def test_an_indexer_on_this_machine_says_installed_and_names_it(
        self, tmp_path, monkeypatch
    ):
        _stub(tmp_path, "tracker3", "exit 0\n")
        value = _row_value(
            desktop.build(_app(_Config(**{desktop.DOCUMENT_KEY: True}))),
            "Document search")
        assert value.startswith("installed"), value
        assert "tracker3" in value, value

    def test_no_indexer_says_not_installed(self):
        """PATH is the stub directory and holds no indexer."""
        value = _row_value(desktop.build(_app(_Config())), "Document search")
        assert value.startswith("not installed"), value
        assert "tracker3" in value and "baloosearch6" in value, (
            "the row must name both backends, or 'not installed' is about "
            "something the reader cannot see"
        )

    def test_no_indexer_does_not_read_as_finding_nothing(self):
        value = _row_value(desktop.build(_app(_Config())), "Document search")
        assert "not the same as finding nothing" in value, value

    def test_both_indexers_are_named_when_both_are_present(
        self, tmp_path, monkeypatch
    ):
        _stub(tmp_path, "tracker3", "exit 0\n")
        _stub(tmp_path, "baloosearch6", "exit 0\n")
        value = _row_value(desktop.build(_app(_Config())), "Document search")
        assert value.startswith("installed"), value
        assert "tracker3 and baloosearch6" in value, value

    def test_localsearch_alone_is_not_reported_as_no_index(self, tmp_path, monkeypatch):
        """`skills/search_documents.py:backend()` tries `localsearch` first on
        GNOME, so a current GNOME has an index this panel's two named tools
        would miss - and "not installed" about it would be the confident wrong
        answer this repo keeps paying for."""
        _stub(tmp_path, "localsearch", "exit 0\n")
        value = _row_value(desktop.build(_app(_Config())), "Document search")
        assert value.startswith("installed"), value
        assert "localsearch" in value, value

    def test_the_gate_is_reported_next_to_the_index(self):
        on = _row_value(
            desktop.build(_app(_Config(**{desktop.DOCUMENT_KEY: True}))),
            "Document search")
        off = _row_value(
            desktop.build(_app(_Config(**{desktop.DOCUMENT_KEY: False}))),
            "Document search")
        assert f"{desktop.DOCUMENT_KEY} is on" in on, on
        assert "Chronoa may ask the index" in on, on
        assert f"{desktop.DOCUMENT_KEY} is off" in off, off
        assert "Chronoa will refuse to" in off, off

    def test_an_unreadable_gate_is_named_rather_than_assumed(self):
        value = _row_value(desktop.build(_app(_RaisingConfig())), "Document search")
        assert "could not be read" in value, value
        assert f"{desktop.DOCUMENT_KEY} is" not in value, (
            "an unreadable gate was reported as a state"
        )


class TestTheKeyring:
    def test_a_secret_service_on_the_bus_says_running(self, tmp_path, monkeypatch):
        _busctl(tmp_path, monkeypatch, _listing(desktop.SECRET_SERVICE_NAME))
        value = _row_value(desktop.build(_app()), "Desktop keyring")
        assert value.startswith("running"), value
        assert desktop.SECRET_SERVICE_NAME in value, value
        assert "secret_store.py" in value, value

    def test_no_secret_service_on_a_real_bus_says_not_running(
        self, tmp_path, monkeypatch
    ):
        _busctl(tmp_path, monkeypatch, _listing("org.freedesktop.DBus"))
        value = _row_value(desktop.build(_app()), "Desktop keyring")
        assert value.startswith("not running"), value

    def test_an_unanswerable_bus_is_could_not_ask_here_too(
        self, tmp_path, monkeypatch
    ):
        _busctl(tmp_path, monkeypatch, textwrap.dedent(
            'echo "Failed to connect to bus: No such file or directory" >&2\n'
            "exit 1\n"))
        value = _row_value(desktop.build(_app()), "Desktop keyring")
        assert value.startswith("could not ask"), value
        assert "not running" not in value, value

    def test_a_missing_typelib_is_not_installed_whatever_the_bus_says(
        self, tmp_path, monkeypatch
    ):
        """Both halves, because either alone is not a keyring: a machine with a
        Secret Service answering but no libsecret typelib still has nothing
        `secret_store.py` can use."""
        _busctl(tmp_path, monkeypatch, _listing(desktop.SECRET_SERVICE_NAME))
        monkeypatch.setattr(secret_store, "_secret", lambda: (None, None))
        value = _row_value(desktop.build(_app()), "Desktop keyring")
        assert value.startswith("not installed"), value
        assert "typelib" in value, value

    def test_it_reads_the_keyring_through_secret_store(self, monkeypatch):
        """The probe really is `secret_store.py`'s own, not a second guess about
        which library is present - a copy of it here is a second thing to be
        wrong."""
        asked = []

        def probe():
            asked.append(True)
            return object(), None

        monkeypatch.setattr(secret_store, "_secret", probe)
        desktop.build(_app())
        assert asked, "secret_store.py was never asked whether there is a keyring"

    def test_a_raising_keyring_probe_is_could_not_ask(self, monkeypatch):
        def probe():
            raise RuntimeError("libsecret exploded")

        monkeypatch.setattr(secret_store, "_secret", probe)
        value = _row_value(desktop.build(_app()), "Desktop keyring")
        assert value.startswith("could not ask"), value
        assert "libsecret exploded" in value, value


class TestOpenWith:
    def test_a_missing_entry_says_not_installed_and_names_the_path(
        self, tmp_path, monkeypatch
    ):
        """The honest answer about an absent file: what is missing, where it
        would have been, and what that costs. Not an empty MimeType list, which
        is what a reader would have to be told to tell apart."""
        missing = tmp_path / "nowhere" / "shani-chronoa.desktop"
        monkeypatch.setattr(desktop, "DESKTOP_ENTRY", str(missing))
        value = _row_value(desktop.build(_app()), "Open with Chronoa")
        assert value.startswith("not installed"), value
        assert str(missing) in value, value
        assert "MimeType" not in value, (
            "a file that is not there has no MimeType list to report, and "
            "reporting one anyway would be the confident wrong answer"
        )

    def test_an_entry_with_mime_types_counts_them(self, tmp_path, monkeypatch):
        entry = tmp_path / "shani-chronoa.desktop"
        entry.write_text(
            "[Desktop Entry]\nType=Application\nExec=shani-chronoa %U\n"
            "MimeType=application/pdf;text/plain;\n"
        )
        monkeypatch.setattr(desktop, "DESKTOP_ENTRY", str(entry))
        value = _row_value(desktop.build(_app()), "Open with Chronoa")
        assert value.startswith("installed"), value
        assert "2 MIME type(s)" in value, value
        assert "application/pdf" in value, value
        assert "passes the file on" in value, value

    def test_an_entry_with_an_empty_mime_list_is_installed_but_offers_nothing(
        self, tmp_path, monkeypatch
    ):
        """The file being there is not the same as the desktop offering it, and a
        desktop entry with an empty MimeType is a real state that looks fine on
        disk."""
        entry = tmp_path / "shani-chronoa.desktop"
        entry.write_text(
            "[Desktop Entry]\nType=Application\nExec=shani-chronoa %U\n"
            "MimeType=;\n"
        )
        monkeypatch.setattr(desktop, "DESKTOP_ENTRY", str(entry))
        value = _row_value(desktop.build(_app()), "Open with Chronoa")
        assert value.startswith("installed"), value
        assert "0 type(s)" in value, value
        assert "no reason" in value, value

    def test_an_entry_with_no_mime_line_at_all_is_not_reported_as_working(
        self, tmp_path, monkeypatch
    ):
        entry = tmp_path / "shani-chronoa.desktop"
        entry.write_text("[Desktop Entry]\nType=Application\nExec=shani-chronoa\n")
        monkeypatch.setattr(desktop, "DESKTOP_ENTRY", str(entry))
        value = _row_value(desktop.build(_app()), "Open with Chronoa")
        assert value.startswith("installed"), value
        assert "no MimeType line at all" in value, value

    def test_a_desktop_action_is_not_mistaken_for_the_entrys_own_exec(
        self, tmp_path, monkeypatch
    ):
        """The `[Desktop Action ...]` groups carry their own `Exec=` lines.
        Reading one of those as the entry's own would report the entry as taking
        files when its Exec= says `shani-chronoa` with no field code."""
        entry = tmp_path / "shani-chronoa.desktop"
        entry.write_text(textwrap.dedent(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Exec=shani-chronoa\n"
            "MimeType=application/pdf;\n"
            "Actions=talk;\n"
            "\n"
            "[Desktop Action talk]\n"
            "Name=Talk to Chronoa\n"
            "Exec=shani-chronoa --listen %F\n"
        ))
        monkeypatch.setattr(desktop, "DESKTOP_ENTRY", str(entry))
        value = _row_value(desktop.build(_app()), "Open with Chronoa")
        assert value.startswith("installed"), value
        assert "takes no field code" in value, value

    def test_a_continued_mime_list_is_not_truncated_to_its_first_line(
        self, tmp_path, monkeypatch
    ):
        """A trailing backslash continues the value onto the next line, which is
        how a long MimeType list is normally written. Splitting on newlines would
        report one type for a list of forty."""
        entry = tmp_path / "shani-chronoa.desktop"
        entry.write_text(
            "[Desktop Entry]\nType=Application\nExec=shani-chronoa %U\n"
            "MimeType=application/pdf;image/png;video/mp4;\\\n"
            "audio/mpeg;text/plain;\n"
        )
        monkeypatch.setattr(desktop, "DESKTOP_ENTRY", str(entry))
        value = _row_value(desktop.build(_app()), "Open with Chronoa")
        assert "5 MIME type(s)" in value, value

    def test_the_reported_entry_is_the_path_the_package_ships(
        self, tmp_path, monkeypatch
    ):
        """Read against the entry the package actually installs, not a fixture -
        and the count is taken from that file, so a trimmed or added MIME type
        shows up here rather than passing because both sides moved together."""
        packaged = _packaged("share", "applications", "shani-chronoa.desktop")
        assert packaged.is_file(), f"the package does not ship {packaged}"
        wanted = next(line.partition("=")[2] for line
                      in packaged.read_text(encoding="utf-8").splitlines()
                      if line.startswith("MimeType="))
        count = len([part for part in wanted.split(";") if part.strip()])
        assert count, f"{packaged} has an empty MimeType list"

        monkeypatch.setattr(desktop, "DESKTOP_ENTRY", str(packaged))
        value = _row_value(desktop.build(_app()), "Open with Chronoa")
        assert value.startswith("installed"), value
        assert f"{count} MIME type(s)" in value, value
        assert wanted.split(";")[0] in value, value
        assert "passes the file on" in value, (
            "the shipped entry's Exec= carries %U, which is what makes "
            "\"Open with\" hand a file to Chronoa at all"
        )


def test_off_the_bus_is_fine_when_the_service_is_installed(monkeypatch):
    """Not running between searches is normal; only a missing service file is red."""
    from shani_chronoa.gui.surfaces import common, desktop
    monkeypatch.setattr(desktop, "_service_fields", lambda: ({"Name": desktop.SEARCH_PROVIDER_NAME}, ""))
    recorder = common.StatusRecorder()
    desktop._status_row(recorder, ["org.freedesktop.DBus"], "")
    assert recorder.status() == common.STATUS_OK
    monkeypatch.setattr(desktop, "_service_fields", lambda: (None, "there is no service file"))
    recorder = common.StatusRecorder()
    desktop._status_row(recorder, ["org.freedesktop.DBus"], "")
    assert recorder.status() == common.STATUS_ATTENTION
