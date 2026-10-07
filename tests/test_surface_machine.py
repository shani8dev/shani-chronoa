"""The Machine surface, as a built widget.

This panel makes one promise that is easy to state and hard to keep: a sense
that could not answer is never shown as one that did. Every test here is about
that promise, and the negative control is what makes it testable rather than
merely asserted - it swaps in a reader that turns every failure into a
clean-looking reading and checks that the assertions around it stop holding.

Rows are read by walking the built widget tree and reading each row's own title,
subtitle and timestamp, rather than off a list the module stashed on itself. A
count read back out of the thing being counted is the mistake that produced the
visual-regression suite AGENTS.md records as having been deleted: fifteen
assertions that passed against a window which rendered nothing.

The stub senses are real `Sense` objects rather than mocks, because two of the
properties under test only exist on the real type: `run` returning a bare `str`
(which the surface must wrap exactly the way the CLI does) and `to_percept`
stamping a percept with no source and no metadata - which is why such a reading
is reported as unattributed rather than as fact.
"""

from __future__ import annotations

import ast
import re
import signal
import threading
import time
import types
from pathlib import Path
from typing import Any, Dict, List

import gi
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from shani_chronoa.gui.surfaces import common, machine  # noqa: E402
from shani_chronoa.senses import SENSITIVITY_PUBLIC, Percept, Sense  # noqa: E402

REGISTRY_SOURCE = Path(machine.__file__).parent / "__init__.py"

#: The reading a panel that trusted its own fallback would invent instead of
#: admitting it had none. Named once so the test that must not see it and the
#: control that must produce it cannot drift apart.
CLEAN = "hwmon0: 41 C, fan 1400 rpm - all nominal"


# --- stubs -----------------------------------------------------------------


class _Open:
    """A config in which every sense is permitted."""

    def sense_allowed(self, name: str) -> bool:
        return True

    def sense_allowed_reason(self, name: str) -> str:
        return ""


class _Closed:
    """A config in which nothing is permitted, and says why."""

    def sense_allowed(self, name: str) -> bool:
        return False

    def sense_allowed_reason(self, name: str) -> str:
        return f"the {name} sense is turned off (enable '{name}-sense-enabled')"


class _Exploding:
    """A config whose permission check cannot answer at all."""

    def sense_allowed(self, name: str) -> bool:
        raise RuntimeError("dconf is not answering")


def _app(config: Any = None) -> Any:
    return types.SimpleNamespace(config=config)


def _sense(name: str, run: Any) -> Sense:
    """A real `Sense` carrying `run`, so wrapping and provenance are real."""
    return Sense(
        name=name,
        kind="machine-state",
        ttl_seconds=300.0,
        sensitivity=SENSITIVITY_PUBLIC,
        schema={
            "type": "function",
            "function": {
                "name": name,
                "description": "A stub sense.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        run=run,
        poll_interval=300.0,
    )


def _registry(monkeypatch: pytest.MonkeyPatch, senses: Dict[str, Sense]) -> None:
    """Stand in for the real registry.

    Patched on `machine`, which is where `build()` reads the name from. The
    package's own rule applies here: patching a name where it is re-exported
    sets a copy nobody calls, and the test then passes for the wrong reason.
    """
    monkeypatch.setattr(machine, "discover_senses", lambda: dict(senses))


# --- reading the built widget ---------------------------------------------


def _labels(widget: Gtk.Widget) -> List[str]:
    found: List[str] = []
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label):
            found.append(child.get_text())
        found.extend(_labels(child))
        child = child.get_next_sibling()
    return found


def _tree(widget: Gtk.Widget) -> List[Gtk.Widget]:
    """Every widget in the built tree, in order."""
    found: List[Gtk.Widget] = [widget]
    child = widget.get_first_child()
    while child is not None:
        found.extend(_tree(child))
        child = child.get_next_sibling()
    return found


def _rows(widget: Gtk.Widget) -> List[Gtk.Widget]:
    """Every row this panel built, in order, found by its own css class."""
    found: List[Gtk.Widget] = []
    if machine.ROW_CSS in widget.get_css_classes():
        found.append(widget)
    child = widget.get_first_child()
    while child is not None:
        found.extend(_rows(child))
        child = child.get_next_sibling()
    return found


def _adw_row(row: Gtk.Widget) -> bool:
    return common.adw_ready() and hasattr(row, "get_subtitle")


def _the_row(node: Gtk.Widget) -> "Gtk.Widget | None":
    """The `Adw.ActionRow` inside a sense's block, if the node is a block.

    A sense is now rendered as a row *plus* its reading laid out as a table
    beside it, because a wrapped paragraph is what made this the densest screen
    in the app. The `machine-reading` marker is on the block, so a test that
    finds a sense by that class finds the block - and the accessors below want
    the row inside it. Unwrapping here means every assertion in this file keeps
    meaning what it meant when a sense was one row.
    """
    if _adw_row(node):
        return node
    for child in _tree(node):
        if child is not node and _adw_row(child):
            return child
    return None


def _row_title(row: Gtk.Widget) -> str:
    """The sense name a row is about.

    Branches on `adw_ready()` because `common.row()` returns an `Adw.ActionRow`
    where libadwaita is there and a plain `Gtk.Box` of labels where it is not,
    and a surface's test should not have to know which one it got.
    """
    row = _the_row(row) or row
    if _adw_row(row):
        return row.get_title()
    for label in _labels(row):
        if label.strip():
            return label
    raise AssertionError(f"row has no title text at all: {_labels(row)!r}")


def _row_text(row: Gtk.Widget) -> str:
    """Everything the row says, one line per label, title first.

    Reads the *block* rather than only the row inside it, so a reading laid out
    as a table is still text to a test - the assertions below are about what the
    panel says, and a table of the same words says the same thing.

    **The table's two labels are rejoined into one `key: value` line.** A sense
    reading is now rendered through `common.key_values()`, which puts the key
    and the value in two separate `Gtk.Label`s of one `key-value-row`. Joining
    every label with a newline - which is what this did first - turns the single
    string `'hwmon0: 41 C, fan 1400 rpm - all nominal'` into four lines
    (`hwmon` / `reading` / `not read` / `hwmon0` / `41 C, ...`), so an assertion
    about what the panel says no longer matches it. That silently broke the
    negative control in `TestAFailingSenseIsNotACleanReading`, which is worse
    than a red test: a control that can no longer fail proves nothing, which is
    exactly what this repo's own tooling rules warn about.
    """
    inner = _the_row(row) or row
    if _adw_row(inner):
        head = [inner.get_title() or "", inner.get_subtitle() or ""]
    else:
        head = []
    lines = list(head)
    for node in _tree(row):
        if node is row:
            continue
        if not _is_key_value_row(node):
            continue
        found = _labels(node)
        if len(found) >= 2:
            # Key and value, back in the shape the sense actually returned.
            lines.append(f"{found[0].strip()}: {found[1].strip()}")
        else:
            # A line `key_values()` could not classify: it stays whole, which is
            # what the renderer does with it too.
            lines.extend(found)
    if not any(_is_key_value_row(node) for node in _tree(row)):
        # No table in this block at all: the flat label list is the whole of it.
        return "\n".join(_labels(row)) if not head else "\n".join(lines)
    return "\n".join(lines)


def _is_key_value_row(node: Gtk.Widget) -> bool:
    """Whether this widget is one `key-value-row` of the reading table.

    Matched on the CSS class the renderer actually adds, so a row that stopped
    being a key-value row would stop being rejoined here too - the helper reads
    what was built rather than what it assumes was built.
    """
    return any(
        style is not None and "key-value-row" in (style or "")
        for style in (node.get_css_classes() or [])
    )


def _row_state(row: Gtk.Widget) -> str:
    """The row's second line: what kind of answer this is, and why.

    Read off the `Adw.ActionRow`'s own subtitle, not off "the second line of
    everything the block says" - the table below it would otherwise become the
    state, which is the one thing this helper must never return.
    """
    inner = _the_row(row) or row
    if _adw_row(inner):
        subtitle = inner.get_subtitle() or ""
        return subtitle.splitlines()[0] if subtitle.strip() else ""
    lines = _row_text(row).splitlines()
    return lines[1] if len(lines) > 1 else ""


def _row_when(row: Gtk.Widget) -> List[str]:
    """The row's own timestamp labels."""
    found: List[str] = []

    def walk(node: Gtk.Widget) -> None:
        if machine.WHEN_CSS in node.get_css_classes():
            found.append(node.get_text())
        child = node.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(row)
    return found


def _row(widget: Gtk.Widget, name: str) -> Gtk.Widget:
    for candidate in _rows(widget):
        if _row_title(candidate) == name:
            return candidate
    raise AssertionError(
        f"no row for {name!r}; the panel has "
        f"{[_row_title(r) for r in _rows(widget)]!r}"
    )


def _all_text(widget: Gtk.Widget) -> str:
    return "\n".join(_labels(widget))


def _percept(content: str, source: str = "", metadata: Any = None) -> Percept:
    return Percept(
        sense="stub",
        kind="machine-state",
        content=content,
        created_at=time.time(),
        ttl_seconds=300.0,
        source=source,
        sensitivity=SENSITIVITY_PUBLIC,
        metadata=metadata,
    )


# --- the module's own contract --------------------------------------------


class TestModuleContract:
    def test_it_exports_the_three_names_the_registry_reads(self):
        assert machine.TITLE == "Machine"
        assert machine.ICON == "computer-symbolic"
        assert callable(machine.build)

    def test_the_icon_is_a_real_icon_name_on_this_machine(self):
        """A symbolic name nothing ships renders as a blank gap the size of an
        icon, which no assertion on the string can see."""
        roots = [Path("/usr/share/icons"), Path("/usr/local/share/icons")]
        if not any(root.is_dir() for root in roots):
            pytest.skip("no icon theme installed on this machine")
        found = [
            svg
            for root in roots
            if root.is_dir()
            for svg in root.rglob(f"{machine.ICON}.svg")
        ]
        assert found, f"{machine.ICON} is not an icon this machine has"

    def test_the_registry_already_names_this_surface(self):
        """`SURFACE_IDS` is what `all_surfaces()` iterates, so a module nobody
        names there is a module nothing builds.

        Read with `ast` rather than by calling `all_surfaces()`, which imports
        every surface to answer - so one broken panel would turn this into a
        statement about the whole sidebar instead of about this module.
        """
        tree = ast.parse(REGISTRY_SOURCE.read_text(encoding="utf-8"))
        ids = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "SURFACE_IDS"
                for target in node.targets
            ):
                assert isinstance(node.value, ast.Tuple), "SURFACE_IDS must stay a tuple"
                ids = [
                    element.value
                    for element in node.value.elts
                    if isinstance(element, ast.Constant)
                ]
        assert ids is not None, "gui/surfaces/__init__.py no longer defines SURFACE_IDS"
        assert all(isinstance(name, str) for name in ids)
        assert "machine" in ids, (
            "gui/surfaces/__init__.py never names a 'machine' surface, so this "
            "module is built by nothing that runs"
        )

    def test_the_names_it_exports_are_the_ones_the_registry_reads(self):
        for name in ("TITLE", "ICON", "build"):
            assert hasattr(machine, name), name


# --- the rows --------------------------------------------------------------


class TestTheRows:
    def test_build_returns_a_gtk_widget(self):
        assert isinstance(machine.build(_app()), Gtk.Widget)

    def test_a_stub_app_renders_every_row_without_raising(self):
        """A window whose settings have not loaded is a normal state, and an
        exception here takes the window down rather than one row."""
        widget = machine.build(types.SimpleNamespace())
        assert [_row_title(row) for row in _rows(widget)] == list(machine.SENSE_NAMES)

    def test_a_stub_app_reads_nothing_and_says_so(self):
        """No settings means the permission cannot be checked, and a permission
        that cannot be checked is not a granted one: sixteen rows saying so is
        the honest answer, sixteen blank rows would be the misleading one."""
        widget = machine.build(types.SimpleNamespace())
        for row in _rows(widget):
            assert "not read" in _row_state(row), (row.get_title(), _row_state(row))
        assert "0 of 16 senses answered" in _all_text(widget)

    def test_the_real_registry_runs_for_real_and_answers_in_a_known_state(
        self,
    ):
        """The real senses against this machine's real hardware - the one test
        here that cannot be stubbed without proving nothing. Each row has to land
        in one of the panel's own states: a row in no state at all would mean the
        panel rendered something it cannot account for."""
        widget = machine.build(_app(_Open()))
        assert [_row_title(row) for row in _rows(widget)] == list(machine.SENSE_NAMES)
        known = {machine.STATE_LINE[state] for state in machine.STATES}
        for row in _rows(widget):
            state = _row_state(row)
            assert any(state.startswith(word) for word in known), (
                _row_title(row),
                state,
            )
            if state.startswith("reading"):
                assert re.match(r"^read at \d\d:\d\d:\d\d$", _row_when(row)[0]), (
                    _row_title(row),
                    _row_when(row),
                )

    def test_each_sense_is_asked_exactly_once(self, monkeypatch):
        calls = []

        def counting(arguments):
            calls.append(dict(arguments))
            return f"asked {len(calls)} time(s)"

        _registry(monkeypatch, {"hwmon": _sense("hwmon", counting)})
        machine.build(_app(_Open()))
        assert len(calls) == 1, calls

    def test_a_sense_with_no_module_is_shown_as_absent_not_as_nothing_found(self):
        """`battery` is on this panel and no sense module carries that name. A
        name with nothing behind it reads as a broken sense unless the panel says
        so, and "this machine has no battery" is an answer only a sense could
        give."""
        row = _row(machine.build(_app(_Open())), "battery")
        assert "no such sense" in _row_state(row), _row_state(row)
        assert "power" in _row_text(row), "the row should say where the battery is"

    def test_a_reading_that_is_not_about_the_hardware_says_what_it_is(
        self, monkeypatch
    ):
        """`memory` is the assistant's durable memory, not free RAM, and a panel
        of machine readings is exactly where a reader would guess otherwise."""
        def remembered(_arguments):
            return _percept(
                "Memory changes, newest first:\n- 2026-10-01 remembered: 'tea'",
                source="memory",
                metadata={"events": 1},
            )

        _registry(monkeypatch, {"memory": _sense("memory", remembered)})
        text = _row_text(_row(machine.build(_app(_Open())), "memory"))
        assert "not free RAM" in text, text

    def test_an_empty_registry_is_an_empty_state_not_an_empty_page(self, monkeypatch):
        _registry(monkeypatch, {})
        said = _all_text(machine.build(_app(_Open())))
        assert "No senses are registered" in said, said

    def test_a_registry_that_will_not_load_says_so(self, monkeypatch):
        def refuse():
            raise ImportError("no module named shani_chronoa.senses")

        monkeypatch.setattr(machine, "discover_senses", refuse)
        said = _all_text(machine.build(_app(_Open())))
        assert "could not be loaded" in said, said
        assert "not a machine with nothing to say" in said, said


# --- one reading, once, with its timestamp ---------------------------------


class TestOneReadingOnce:
    def test_a_read_row_says_when_it_was_read(self, monkeypatch):
        _registry(monkeypatch, {"hwmon": _sense("hwmon", lambda _a: "41 C")})
        stamp = _row_when(_row(machine.build(_app(_Open())), "hwmon"))
        assert len(stamp) == 1, stamp
        assert re.match(r"^read at \d\d:\d\d:\d\d$", stamp[0]), stamp

    def test_a_row_that_was_not_read_has_no_timestamp(self, monkeypatch):
        _registry(monkeypatch, {"hwmon": _sense("hwmon", lambda _a: "41 C")})
        assert _row_when(_row(machine.build(_app(_Closed())), "hwmon")) == ["not read"]

    def test_a_refused_sense_is_never_invoked_at_all(self, monkeypatch):
        """Not "invoked and then hidden": a sense whose key is off must never
        run, which is the whole point of a per-sense permission."""
        calls = []
        _registry(
            monkeypatch,
            {"hwmon": _sense("hwmon", lambda a: calls.append(a) or "41 C")},
        )
        row = _row(machine.build(_app(_Closed())), "hwmon")
        assert calls == [], "a sense whose permission is off was invoked anyway"
        assert "permission is off" in _row_state(row), _row_state(row)
        assert "hwmon-sense-enabled" in _row_text(row)

    def test_a_permission_check_that_raises_is_not_a_grant(self, monkeypatch):
        calls = []
        _registry(
            monkeypatch,
            {"hwmon": _sense("hwmon", lambda a: calls.append(a) or "41 C")},
        )
        row = _row(machine.build(_app(_Exploding())), "hwmon")
        assert calls == [], "an unreadable permission was treated as a granted one"
        assert "could not be checked" in _row_state(row), _row_state(row)

    def test_the_budget_leaves_no_timer_armed_behind_it(self, monkeypatch):
        """Nothing outlives the call. A watchdog thread would have to be joined
        or leaked; an interval timer has to be cancelled, and this is the check
        that says it was."""
        timer_before = signal.getitimer(signal.ITIMER_REAL)
        handler_before = signal.getsignal(signal.SIGALRM)
        _registry(monkeypatch, {"hwmon": _sense("hwmon", lambda _a: "41 C")})
        machine.build(_app(_Open()))
        assert signal.getitimer(signal.ITIMER_REAL) == timer_before
        assert signal.getsignal(signal.SIGALRM) is handler_before

    def test_it_starts_no_thread(self, monkeypatch):
        threads = threading.active_count()
        _registry(monkeypatch, {"hwmon": _sense("hwmon", lambda _a: "41 C")})
        machine.build(_app(_Open()))
        assert threading.active_count() == threads

    def test_it_has_no_poller_no_store_and_no_shell(self):
        """Read from the source rather than inferred from behaviour: a panel
        that grew a scheduler would still build fine in a test."""
        source = Path(machine.__file__).read_text(encoding="utf-8")
        for forbidden in ("PerceptStore", "AmbientScheduler", "Thread(", "subprocess"):
            assert forbidden not in source, (
                f"{forbidden} is in this module; the panel is not allowed to poll, "
                "deposit or shell out"
            )


# --- the honest states -----------------------------------------------------


class TestAFailingSenseIsNotACleanReading:
    def test_a_sense_that_raises_is_not_shown_as_a_clean_reading(self, monkeypatch):
        def broken(_arguments):
            raise OSError("/sys/class/hwmon is not readable")

        _registry(monkeypatch, {"hwmon": _sense("hwmon", broken)})
        row = _row(machine.build(_app(_Open())), "hwmon")
        state, text = _row_state(row), _row_text(row)
        assert "failed" in state, state
        assert "OSError" in text and "/sys/class/hwmon is not readable" in text, text
        assert CLEAN not in text, (
            "a sense that raised was shown as though it had answered"
        )

    def test_the_negative_control_makes_that_failure_render_as_clean(
        self, monkeypatch
    ):
        """The control for the assertion above, and the reason it can fail.

        The reader is swapped on `machine._read_sense` - where `build()` reads
        the name, not where it is defined - for one that turns anything which is
        not already a reading into a clean-looking one. If the panel then shows
        the clean value, the test above is measuring something real; if it still
        said "failed", that test could not fail and would prove nothing.
        """
        def broken(_arguments):
            raise OSError("/sys/class/hwmon is not readable")

        _registry(monkeypatch, {"hwmon": _sense("hwmon", broken)})
        assert "failed" in _row_state(_row(machine.build(_app(_Open())), "hwmon")), (
            "the control did not start from the state it is meant to break"
        )

        honest = machine._read_sense

        def clean_instead_of_honest(name, sense, config, timeout=None):
            reading = honest(name, sense, config, timeout)
            if reading.is_reading:
                return reading
            return machine.Reading(name, machine.STATE_READING, text=CLEAN, detail="")

        with monkeypatch.context() as control:
            control.setattr(machine, "_read_sense", clean_instead_of_honest)
            controlled = _row(machine.build(_app(_Open())), "hwmon")
            assert "failed" not in _row_state(controlled), (
                "an unanswerable sense still rendered as unknown, so the assertion "
                "in the test above cannot fail"
            )
            assert CLEAN in _row_text(controlled), (
                "the control changed the state but not the reading, so it is not "
                "the failure this panel exists to avoid"
            )

        restored = _row(machine.build(_app(_Open())), "hwmon")
        assert "failed" in _row_state(restored), "restoring the reader lost the state"
        assert CLEAN not in _row_text(restored)

    def test_a_sense_that_outruns_its_budget_is_not_shown_as_a_reading(
        self, monkeypatch
    ):
        monkeypatch.setattr(machine, "SENSE_TIMEOUT_SECONDS", 0.3)

        def wedged(_arguments):
            time.sleep(30)
            return "41 C"

        _registry(monkeypatch, {"hwmon": _sense("hwmon", wedged)})
        began = time.monotonic()
        row = _row(machine.build(_app(_Open())), "hwmon")
        elapsed = time.monotonic() - began
        assert "timed out" in _row_state(row), _row_state(row)
        assert CLEAN not in _row_text(row)
        assert elapsed < 10, f"the budget was not enforced; the build took {elapsed:.1f}s"

    def test_a_sense_that_reports_unknown_is_not_shown_as_a_reading(self, monkeypatch):
        """The real shape of this, verbatim from `snapshots` on a machine with no
        btrfs tool: a sense that answers honestly, and whose answer is that it
        could not determine the thing."""
        def honest(_arguments):
            return (
                "Snapshots are UNKNOWN: the btrfs tool is not installed, so "
                "nothing could be listed. That is not the same as having no "
                "snapshots."
            )

        _registry(monkeypatch, {"snapshots": _sense("snapshots", honest)})
        widget = machine.build(_app(_Open()))
        row = _row(widget, "snapshots")
        assert _row_state(row).startswith("UNKNOWN"), _row_state(row)
        assert "btrfs tool is not installed" in _row_text(row), (
            "the sense's own words must still be shown - the state says how much "
            "to trust them, it does not replace them"
        )
        assert "0 of 16 senses answered" in _all_text(widget)

    def test_a_sense_that_declines_is_not_shown_as_a_reading(self, monkeypatch):
        """`hwmon`'s own wording when it refuses, reached through a config that
        allows it. Checked separately from the permission path because this is a
        different failure: the sense ran, and it declined."""

        def declined(_arguments):
            return _percept(
                "Not reading hardware sensors: the hwmon sense is turned off "
                "(enable 'hwmon-sense-enabled')",
                source="sysfs-hwmon",
                metadata={"sensors": 4},
            )

        _registry(monkeypatch, {"hwmon": _sense("hwmon", declined)})
        row = _row(machine.build(_app(_Open())), "hwmon")
        assert _row_state(row).startswith("UNKNOWN"), _row_state(row)

    def test_a_reading_with_nothing_to_check_it_against_is_not_a_reading(
        self, monkeypatch
    ):
        """A sense that returns a bare string produces a percept with no source
        and no metadata, so there is nothing to attribute the number to. This
        panel's own standard is that a surface says what it is showing it from."""
        _registry(monkeypatch, {"hwmon": _sense("hwmon", lambda _a: "42")})
        row = _row(machine.build(_app(_Open())), "hwmon")
        assert _row_state(row).startswith("UNKNOWN"), _row_state(row)
        assert "no source and no metadata" in _row_text(row)

    def test_a_sense_that_needs_a_missing_thing_names_it(self, monkeypatch):
        """`pacman is not installed` is only actionable next to the fact that the
        sense wanted pacman - and this panel starts no service either way."""
        def honest(_arguments):
            return (
                "Pending updates are UNKNOWN: pacman is not installed, so this "
                "is not an Arch-family system this sense can answer for."
            )

        _registry(monkeypatch, {"updates": _sense("updates", honest)})
        text = _row_text(_row(machine.build(_app(_Open())), "updates"))
        assert "pacman is not installed" in text
        assert "this panel never runs pacman -Sy" in text, text

    def test_a_long_reading_is_cut_and_the_cut_is_admitted(self, monkeypatch):
        long_reading = "\n".join(
            f"0000:00:{index:02x}.0  device {index}" for index in range(60)
        )
        _registry(monkeypatch, {"devices": _sense("devices", lambda _a: long_reading)})
        text = _row_text(_row(machine.build(_app(_Open())), "devices"))
        assert f"{60 - machine.MAX_READING_LINES} more line(s) not shown" in text, text
        assert "shani-chronoa-sense run devices" in text, text


# --- what the panel says about itself --------------------------------------


class TestWhatThePanelSays:
    def test_the_footer_says_this_panel_grants_nothing(self):
        said = _all_text(machine.build(_app(_Open())))
        assert "no permission is changed" in said, said
        assert "Senses panel is where" in said, said

    def test_it_points_at_the_headless_way_to_read_the_same_things(self):
        assert "shani-chronoa-sense" in _all_text(machine.build(_app(_Open())))

    def test_the_banner_counts_exactly_the_rows_that_are_not_readings(
        self, monkeypatch
    ):
        def broken(_arguments):
            raise OSError("no such device")

        _registry(
            monkeypatch,
            {
                "hwmon": _sense("hwmon", broken),
                # A real reading, with the provenance a real one carries: a bare
                # string comes back with no source and no metadata, which this
                # panel reports as unattributed rather than as fact.
                "cpu": _sense(
                    "cpu",
                    lambda _a: _percept(
                        "8 of 8 CPU(s) online", source="proc+s cpu", metadata={"cpus": 8}
                    ),
                ),
            },
        )
        widget = machine.build(_app(_Open()))
        unread = sum(
            1 for row in _rows(widget) if not _row_state(row).startswith("reading")
        )
        assert unread == 15, unread  # 14 names with no module here, plus hwmon
        said = _all_text(widget)
        # Only hwmon *failed*; the 14 with no module are absent by design and
        # are not counted as failures (nor bannered as one).
        assert "1 of 16 machine-state senses could not answer" in said, said
        assert "1 of 16 senses answered" in said, said
        assert "1 could not answer and 14 were not read on purpose" in said, said

    def test_the_banner_is_revealed_not_merely_in_the_tree(self, monkeypatch):
        """`Adw.Banner` starts hidden, so a panel that appends one and stops puts
        nothing on screen while every assertion about its text still passes.

        Found by rendering the panel and looking at it, not by a test: the count
        of senses that could not answer was in the widget tree and absent from
        the pixels. An assertion on the text alone cannot see this.
        """
        def broken(_arguments):
            raise OSError("no such device")

        _registry(monkeypatch, {"hwmon": _sense("hwmon", broken)})
        banners = [
            node
            for node in _tree(machine.build(_app(_Open())))
            if isinstance(node, Adw.Banner)
        ]
        assert banners, "no banner was built for a panel with an unanswerable sense"
        for banner in banners:
            assert banner.get_revealed() is True, (
                "a banner that is in the tree but not revealed renders nothing"
            )