"""The Diagnostics surface, as a built widget.

One assertion here is load-bearing and the rest are bookkeeping: **a probe that
raises must never render as a working subsystem.** Everything else in this file
exists so that assertion is not the only thing standing between this panel and
a confident wrong answer - "it did nothing" has to have a named cause
somewhere visible, and a cause that reads "working" because a probe blew up is
worse than no cause at all.

So the tests that matter here are the ones that make the honesty claim
falsifiable rather than decorative:

- a probe that raises renders as `could not determine`, checked both through
  `_measure()` directly **and** through the built tree, because the two are
  different code paths and the row is what a person reads;
- `TestNegativeControl` mutates `_fallback_status` - the one function allowed to
  name a status for a probe that did not finish - to return `working`, and
  asserts the row then really does say `working`. That is what makes the test
  above an assertion rather than a tautology: a control that cannot fail is
  not a control. Then it restores and shows the honest answer comes back;
- a probe that *returns* a word outside the closed vocabulary is demoted to
  `could not determine` rather than rendered, so a future row cannot add a
  fourth state by accident;
- a missing binary is reported as missing - `not working`, naming the program -
  and never as `could not determine`, because "the panel could not ask" and
  "the package is not installed" are different sentences about different worlds.

**Every subprocess answer comes from a real stub executable run as a real
subprocess, and `PATH` is only that stub directory.** So a real `busctl` cannot
answer a question this test meant to leave unanswered, and cannot reach the
developer's own session bus - the mistake `AGENTS.md` records from the
global-shortcut test, where Gio's one-cached-connection-per-process reached the
real desktop portal. It also means no real capture is taken: with no `xwd` on
`PATH`, `screengrab.capture_screen()` raises its own `ScreenCaptureError`, which
is the honest answer for the screen row rather than a picture of the test
runner's desktop.

Rows are found by walking the built tree for `ROW_CSS` and reading the
`VALUE_CSS` label underneath, never off a list the module stashed on itself -
which is the count read back out of the thing being counted, the mistake
`AGENTS.md` records a deleted visual-regression test for. The walk branches on
`common.adw_ready()` because the two row shapes are genuinely different: an
`Adw.ActionRow` keeps its title and subtitle labels inside boxes of its own and
names the subtitle label `subtitle`, while the no-Adw fallback is a `Gtk.Box` of
two plain `Gtk.Label`s with no such class.
"""

from __future__ import annotations

import stat
import types
from pathlib import Path

import gi
import pytest

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa import model_service  # noqa: E402
from shani_chronoa.gui.surfaces import common, diagnostics  # noqa: E402

REGISTRY_SOURCE = Path(diagnostics.__file__).parent / "__init__.py"


def _stub(tmp_path, name, body="#!/bin/sh\nexit 0\n"):
    """An executable stub on its own PATH directory, so nothing real is found."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    path = bin_dir / name
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.fixture(autouse=True)
def _only_stub_binaries_on_path(tmp_path, monkeypatch):
    """`PATH` is this test's own stub directory, and nothing else.

    Autouse because every `build()` looks for real programs - `llama-server`,
    `ffmpeg`, `pw-dump`, `busctl` - and a test that only stubbed them in the
    cases that cared would let the others reach the developer's session bus and
    the developer's home directory. With no `bin/` directory yet every one of
    them is genuinely absent, which is the honest starting state for the rows
    that must say so.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    return bin_dir


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
    """[(title, value)] for every row in the built tree.

    Branching on `common.adw_ready()` is not a convenience: it is the condition
    `common.row()` itself branches on when it decides which widget to build, and
    the two shapes put the answer in different places.
    """
    found = []

    def walk(node):
        if diagnostics.ROW_CSS in node.get_css_classes():
            labels = _labels(node)
            if common.adw_ready():
                values = [label.get_text() for label in labels
                          if diagnostics.VALUE_CSS in label.get_css_classes()]
                headings = [label.get_text() for label in labels
                            if diagnostics.VALUE_CSS not in label.get_css_classes()]
                if values:
                    found.append((headings[0], "\n".join(values)))
            else:
                # The no-Adw row is a box of [title, subtitle] labels.
                texts = [label.get_text() for label in labels]
                if len(texts) >= 2:
                    found.append((texts[0], "\n".join(texts[1:])))
        child = node.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(widget)
    return found


def _row(widget, title):
    for name, value in _rows(widget):
        if name == title:
            return value
    raise AssertionError(
        f"no row titled {title!r}; the panel has {[n for n, _ in _rows(widget)]!r}"
    )


def _status_of(value):
    """The status word a rendered row leads with, or `''` if it leads with none."""
    for word in diagnostics.STATUS_WORDS:
        if value.startswith(f"{word} - "):
            return word
    return ""


def _sections(*rows):
    """A replacement `_SECTIONS` carrying `rows` - `(title, probe)` pairs.

    Used to drive one named probe through the real `build()` when the point is
    what the *panel* renders rather than what `_measure()` returns. It patches
    the name `build()` reads at call time, not a copy it captured.
    """
    return (("Under test", "A replacement section list.", tuple(rows)),)


def _raising_probe():
    """A probe that fails the way a real one does - by raising, not by returning."""
    raise RuntimeError("pw-dump did not yield a usable graph")


# -- the contract ------------------------------------------------------------

class TestModuleContract:
    def test_it_exports_the_names_the_registry_reads(self):
        assert diagnostics.TITLE == "Diagnostics"
        assert diagnostics.ICON == "dialog-information-symbolic"
        assert diagnostics.SECTION == "Desktop and system"
        assert callable(diagnostics.build)

    def test_the_icon_is_a_real_icon_name_on_this_machine(self):
        """A symbolic icon name that is not installed renders as a blank
        space-shaped gap, which is invisible to a test that only checks the
        string is non-empty."""
        roots = [Path("/usr/share/icons"), Path("/usr/local/share/icons")]
        if not any(root.is_dir() for root in roots):
            pytest.skip("no icon theme installed on this machine")
        found = [svg for root in roots if root.is_dir()
                 for svg in root.rglob(f"{diagnostics.ICON}.svg")]
        assert found, f"{diagnostics.ICON} is not an icon this machine has"

    def test_its_section_is_one_the_sidebar_knows(self):
        """`sections()` files a panel by the module's own `SECTION`; a value
        outside `SECTION_ORDER` puts it in "Everything else" instead."""
        from shani_chronoa.gui import surfaces

        assert diagnostics.SECTION in surfaces.SECTION_ORDER

    def test_the_registry_names_this_surface_exactly_once(self):
        """`gui/surfaces/__init__.py` lists surface names in `SURFACE_IDS`.

        Read as source rather than through `all_surfaces()`, which imports every
        sibling panel and would report another module's problem as this one's.
        Exactly once as well as at least once: `all_surfaces()` builds a dict, so
        a name listed twice is invisible there and shows up in the sidebar as two
        entries for one panel.
        """
        source = REGISTRY_SOURCE.read_text(encoding="utf-8")
        assert source.count('"diagnostics"') == 1, (
            "gui/surfaces/__init__.py must name the 'diagnostics' surface exactly "
            f"once; it has it {source.count(chr(34) + 'diagnostics' + chr(34))} times"
        )

    def test_the_status_vocabulary_is_closed_and_three_words_long(self):
        """A fourth word is how "broken" and "not installed" and "unreachable"
        drift into looking like the same state, which is the thing this panel
        exists to prevent."""
        assert diagnostics.STATUS_WORDS == ("working", "not working", "could not determine")

    @pytest.mark.parametrize("instance,port", sorted(model_service.PORTS.items()))
    def test_each_model_server_row_names_the_port_the_module_uses(self, instance, port):
        """The three ports are `model_service.PORTS`' to choose, not this panel's.

        A literal copied into a row title is a drift bug that reports the wrong
        port for the whole life of the panel, and nothing else would notice.
        """
        title = f"{instance} server (port {port})"
        assert title in diagnostics.ROW_TITLES

    def test_every_required_subsystem_has_a_row(self):
        """The questions the panel was asked to answer, one row each.

        Written as substrings rather than an exact list: this is a floor, not a
        ceiling, and a panel that grows a row nobody asked for is a different
        bug than one missing a subsystem.
        """
        wanted = (
            "Local model server", "Speech in", "Speech out",
            "Microphone", "Audio path",
            "Screen capture", "Screenshot portal", "Global shortcut portal",
            "Search provider", "SymPy", "ffmpeg", "ImageMagick", "poppler",
            "at-spi", "vision server", "embed server", "imagine server",
        )
        for fragment in wanted:
            assert any(fragment in title for title in diagnostics.ROW_TITLES), fragment


# -- building ----------------------------------------------------------------

class TestBuild:
    def test_build_returns_a_gtk_widget_on_a_stub_app(self):
        """An app that has not loaded its settings is a normal state, not a
        broken install, and an exception here takes the whole window down."""
        widget = diagnostics.build(types.SimpleNamespace())
        assert isinstance(widget, Gtk.Widget)

    def test_build_takes_no_app_at_all(self):
        """The registry calls `build(app)` for every surface; this panel reads
        nothing from one, so it must not insist on having it."""
        assert isinstance(diagnostics.build(), Gtk.Widget)

    def test_every_row_renders_exactly_once(self):
        widget = diagnostics.build(types.SimpleNamespace())
        titles = [title for title, _ in _rows(widget)]
        assert sorted(titles) == sorted(diagnostics.ROW_TITLES)
        assert len(titles) == len(set(titles)), (
            f"a subsystem rendered more than one row: {titles!r}"
        )

    def test_every_row_leads_with_one_of_the_three_status_words(self):
        """Not a cosmetic check: the word is what a person reads first, and a
        row whose value opens with anything else has smuggled in a fourth
        state."""
        widget = diagnostics.build(types.SimpleNamespace())
        for title, value in _rows(widget):
            assert _status_of(value), (
                f"row {title!r} does not lead with a status word: {value!r}"
            )

    def test_a_missing_program_is_never_reported_as_a_broken_probe(self):
        """With this file's stub-only `PATH`, nothing is installed - so every
        binary-gated row must say `not working` and name the program. If any of
        them said `could not determine`, the panel would be telling the user
        *it* could not ask about something it knows perfectly well."""
        widget = diagnostics.build(types.SimpleNamespace())
        named = {
            "ImageMagick (edit_image, upscale)": "magick",
            "ffmpeg (video, audio, subtitles)": "ffmpeg",
            "poppler (read_document, page scans)": "pdftotext",
            "Local model server (llama.cpp)": "llama-server",
            "Microphone": "pw-dump",
        }
        values = dict(_rows(widget))
        for title, binary in named.items():
            value = values[title]
            assert _status_of(value) == diagnostics.STATUS_NOT_WORKING, (title, value)
            assert binary in value, f"{title} does not name {binary}: {value}"
            assert "not on PATH" in value, (
                f"{title} says a binary is unusable without saying it is missing: {value}"
            )


class TestMarkup:
    def test_markup_in_a_path_is_escaped(self, monkeypatch):
        """A home directory is a place a user can have put a `&` or `<` in, and
        every row on this panel carries a path.

        The assertion is both halves, because either alone passes for the wrong
        reason: `get_label()` is the *raw markup* libadwaita was handed (measured
        on libadwaita 1.5, where `use-markup` defaults to true on an
        `ActionRow`), so it must carry the entities; and `get_text()` is what
        got parsed, so the `<b>` must have survived as literal characters. A
        panel that did not escape would render bold instead - the parsed text
        would have lost the tags entirely and the raw markup would show them
        unescaped.
        """
        from shani_chronoa import local_llm

        nasty = "/home/a & b/<b>injected</b>/llm/current.gguf"
        monkeypatch.setattr(local_llm, "server_binary", lambda: nasty)
        monkeypatch.setattr(local_llm, "is_up", lambda *a, **k: True)
        widget = diagnostics.build(types.SimpleNamespace())
        value = _row(widget, "Local model server (llama.cpp)")
        assert nasty in value, f"the path was not shown at all: {value!r}"

        raw = _raw_markup(widget, "Local model server (llama.cpp)")
        assert "&amp;" in raw and "&lt;b&gt;" in raw, (
            f"the raw markup was not escaped: {raw!r}"
        )
        assert "<b>injected</b>" not in raw, f"a tag reached the label: {raw!r}"


def _raw_markup(widget, title):
    """The raw markup of the row titled `title`'s value label.

    Only meaningful with Adw: the no-Adw row is a `Gtk.Label` built with
    `set_text`, which takes no markup at all, so there is nothing to escape
    and nothing to read back. The test skips rather than pretending.
    """
    if not common.adw_ready():
        pytest.skip("the no-Adw row carries plain text, not markup")
    found = []

    def walk(node):
        if diagnostics.ROW_CSS in node.get_css_classes():
            for label in _labels(node):
                if diagnostics.VALUE_CSS in label.get_css_classes():
                    found.append(label.get_label())
        child = node.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(widget)
    assert found, f"no value label on the row titled {title!r}"
    return found[0]


class TestMissingBinary:
    def test_a_program_that_is_not_there_is_named_as_missing(self):
        """`_binary_extra` is the panel's shape for "is this program installed",
        and the distinction it must not blur is missing vs. unknown: `which`
        returning nothing is a fact about the install."""
        probe = diagnostics._binary_extra("ffmpeg", "video keyframes")
        finding = diagnostics._measure("ffmpeg", probe)
        assert finding.status == diagnostics.STATUS_NOT_WORKING
        assert "ffmpeg is not on PATH" in finding.detail
        assert finding.status != diagnostics.STATUS_UNKNOWN

    def test_a_program_that_is_there_is_named_by_its_path(self, tmp_path, monkeypatch):
        stub = _stub(tmp_path, "ffmpeg")
        monkeypatch.setenv("PATH", str(stub.parent))
        finding = diagnostics._measure(
            "ffmpeg", diagnostics._binary_extra("ffmpeg", "video keyframes"))
        assert finding.status == diagnostics.STATUS_WORKING
        assert str(stub) in finding.detail

    def test_a_program_that_is_there_but_will_not_run_is_still_not_unknown(self, tmp_path,
                                                                         monkeypatch):
        """A stub that exits non-zero is a different failure from an absent
        program, and this panel must not report either as "the probe blew up"."""
        _stub(tmp_path, "ffmpeg", body="#!/bin/sh\nexit 3\n")
        monkeypatch.setenv("PATH", str(tmp_path / "bin"))
        finding = diagnostics._measure(
            "ffmpeg", diagnostics._binary_extra("ffmpeg", "video keyframes"))
        # `which` finds the stub, so the answer is about the install, not the run.
        assert finding.status == diagnostics.STATUS_WORKING
        assert str(tmp_path / "bin" / "ffmpeg") in finding.detail


# -- the load-bearing assertion ---------------------------------------------

class TestProbeFailure:
    def test_a_raising_probe_is_never_a_working_subsystem(self):
        """The whole panel in one assertion: a probe that did not finish has not
        established that anything works."""
        finding = diagnostics._measure("Microphone", _raising_probe)
        assert finding.status == diagnostics.STATUS_UNKNOWN
        assert finding.status != diagnostics.STATUS_WORKING
        assert "RuntimeError" in finding.detail
        assert "pw-dump did not yield" in finding.detail

    def test_a_raising_probe_renders_as_could_not_determine_in_the_built_tree(
        self, monkeypatch
    ):
        """Through `build()`, not just `_measure()` - the row is what a person
        reads, and it is a different code path from the one above."""
        monkeypatch.setattr(diagnostics, "_SECTIONS",
                            _sections(("Microphone", _raising_probe)))
        widget = diagnostics.build(types.SimpleNamespace())
        value = _row(widget, "Microphone")
        assert value.startswith(f"{diagnostics.STATUS_UNKNOWN} - "), value
        assert "working - " not in value, f"a failed probe rendered as working: {value!r}"

    def test_a_probe_returning_a_word_outside_the_vocabulary_is_demoted(self):
        """A future row cannot add a fourth state by accident. This also covers
        the more likely mistake: a probe returning `None`, which is what an
        early `return` with no value looks like."""
        for bad in ("mostly fine", "", None, True, "Working"):
            finding = diagnostics._measure("Microphone", lambda bad=bad: (bad, "because"))
            assert finding.status == diagnostics.STATUS_UNKNOWN, (bad, finding)
            assert finding.status != diagnostics.STATUS_WORKING

    def test_a_raising_probe_in_any_section_of_the_real_panel_is_unknown(self, monkeypatch):
        """The real row list, with its first probe replaced by one that raises.

        The point is that the honesty guarantee comes from `_measure`, not from
        each row happening to catch its own exceptions: if a later edit replaced
        `_measure` with something narrower, this fails while the seventeen real
        probes still pass.
        """
        monkeypatch.setattr(diagnostics, "_brain", _raising_probe)
        monkeypatch.setattr(diagnostics, "_SECTIONS", (
            ("Real panel, first probe broken", "", (("Local model server (llama.cpp)",
                                                     diagnostics._brain),)),
        ))
        widget = diagnostics.build(types.SimpleNamespace())
        value = _row(widget, "Local model server (llama.cpp)")
        assert value.startswith(f"{diagnostics.STATUS_UNKNOWN} - "), value
        assert "working" not in value.split(" - ")[0], value


class TestNegativeControl:
    def test_a_raising_probe_reporting_working_is_what_would_break_it(
        self, monkeypatch
    ):
        """Make the honesty rule report `working` and the assertion must fail.

        `_fallback_status` is a separate function read by `_measure` at call
        time, so patching it here patches the name that is actually used. If the
        mutation could not produce a `working` row, every test in
        `TestProbeFailure` would be asserting nothing.

        Then it is restored and the honest answer comes back - otherwise this
        file would leave the panel lying for whoever runs it next.
        """
        honest = diagnostics._measure("Microphone", _raising_probe)
        assert honest.status == diagnostics.STATUS_UNKNOWN, (
            "fixture does not start from the honest answer, so the mutation below "
            "proves nothing"
        )

        monkeypatch.setattr(diagnostics, "_fallback_status",
                            lambda exc: diagnostics.STATUS_WORKING)
        monkeypatch.setattr(diagnostics, "_SECTIONS",
                            _sections(("Microphone", _raising_probe)))
        widget = diagnostics.build(types.SimpleNamespace())
        value = _row(widget, "Microphone")
        assert value.startswith(f"{diagnostics.STATUS_WORKING} - "), (
            "the mutation did not make a failed probe report working, so the "
            "assertion it is meant to justify cannot fail"
        )

        monkeypatch.undo()
        restored = diagnostics._measure("Microphone", _raising_probe)
        assert restored.status == diagnostics.STATUS_UNKNOWN, (
            "the honest answer did not come back after the restore"
        )