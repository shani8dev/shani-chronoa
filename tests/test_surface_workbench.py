"""The workbench surface: the command-to-skill generator, reached at last.

`tools/cli_matrix.py --scaffold CMD` has existed since 2026-10-01 with no panel
on it. These tests are about the three claims that panel makes, and each is
written so that it can fail:

- **a generated skill's flags are the man page's flags.** Not "the module has a
  FLAGS dict" - the generator is what fills it, and this asserts every flag it
  chose appears in the page the matrix read, with the same takes-a-value
  answer. The generated file is also written through the real panel (`generate`
  then `save_generated`) into the test's own directory, and the packaged
  skills directory is compared before and after, because the one thing that
  must never happen here is a write over the repository.
- **a command the matrix does not know is refused in those words.** A refusal
  that produced no file, no preview and no save would also satisfy "nothing was
  written", so the assertion is on the wording the panel shows.
- **a command that would change something comes out disabled.** Labelled
  disabled *and* actually disabled: the generated module is imported and its
  `run({})` called, which is what a real skill call would do, and it refuses.
- **removing a drop-in asks first.** The button is clicked, not the handler
  called - a handler called directly cannot tell you the button was ever wired
  to it, which is how this repo's orb button shipped - and the file is asserted
  to still be there after the ask and after a `cancel`. Then the `remove`
  response, and *only* that one file is gone.
- **markup in a skill's own description is escaped.** The assertion is on
  `Gtk.Label.get_text()`, which is what the row renders. Measured: handed
  `Move <b>a</b> window & report` as Pango markup, libadwaita 1.5 fails the
  parse and the label renders as **the empty string** - so an unescaped
  description cannot pass this by looking right.

**One negative control, and it is the filter** (`test_the_filter_control`):
`_matches` is read as a module global at call time, so patching it to
always-match proves the narrowing assertions are about narrowing and not about
a constant, and restoring it proves the narrowing comes back.

Commands are taken from candidates and checked against the real matrix, so a
machine with no man pages for them says SKIP rather than passing quietly.
`common.adw_ready()` is branched on where it changes behaviour: the removal
confirmation is an `Adw.AlertDialog` with libadwaita and a second press of the
same button without it, and both are held to the same "asks, then removes only
that file".
"""

from __future__ import annotations

import ast
import re
import time
import types
from pathlib import Path

import gi
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

Adw.init()

from shani_chronoa.gui import surfaces as registry  # noqa: E402
from shani_chronoa.gui.surfaces import common, workbench  # noqa: E402

REGISTRY_SOURCE = Path(registry.__file__).resolve()

#: Candidates for the two kinds of command the scaffold tests need, most
#: specific first. A machine whose man pages lack all of them skips.
READ_ONLY_WITH_FLAGS = ("uptime", "ls", "df", "free", "id", "date", "lscpu", "stat")
CHANGING = ("rm", "mv", "touch", "mkdir", "chmod", "truncate")

#: Not a command on any PATH and not a page in any man directory.
UNKNOWN = "chronoa-definitely-not-a-command"

#: The description that carries markup, a bare `&` and a quote. All three are
#: Pango-significant; the `&` is the one that renders the row empty.
MARKUP_DESCRIPTION = "Move <b>a</b> window & report its size"

DROP_IN_TEMPLATE = '''"""A user drop-in: {name}."""

from shani_chronoa.skills import Skill

_SCHEMA = {{"type": "function", "function": {{"name": "{name}", "description": {description!r},
         "parameters": {{"type": "object", "properties": {{}}}}}}}}

SKILLS = [Skill(name="{name}", schema=_SCHEMA, run=lambda arguments: "ok")]
'''


def _write_drop_in(directory: Path, name: str, description: str = "A drop-in.") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.py"
    path.write_text(DROP_IN_TEMPLATE.format(name=name, description=description),
                    encoding="utf-8")
    return path


def _pump(ms=500):
    context = GLib.MainContext.default()
    deadline = time.monotonic() + ms / 1000.0
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        time.sleep(0.005)


def _filter_by(page, text):
    """Type `text` into the filter and wait for the render it causes.

    `set_text()` emits neither `changed` nor `search-changed`, so setting it and
    counting rows would be counting a render the surface never performed.
    """
    page.search.set_text("")
    page.search.insert_text(text, -1)
    _pump()


def _labels(widget):
    """Every `Gtk.Label` under `widget`, in tree order."""
    out = []

    def walk(node):
        child = node.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Label):
                out.append(child)
            walk(child)
            child = child.get_next_sibling()

    walk(widget)
    return out


def _button(widget, label: str):
    """The first button in `widget` carrying `label`."""
    found = []

    def walk(node):
        child = node.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Button) and child.get_label() == label:
                found.append(child)
            walk(child)
            child = child.get_next_sibling()

    walk(widget)
    assert found, f"no {label!r} button in this row"
    return found[0]


def _matrix():
    """The generator, or skip: these tests are about what it produces."""
    module, path, reason = workbench.matrix()
    if module is None:
        pytest.skip(f"{reason} ({path})")
    return module


def _row_for(name):
    """`(name, row)` for the first candidate the real matrix knows about."""
    module = _matrix()
    for candidate in name:
        row, refusal, _detail = workbench._matrix_row(module, candidate)
        if row is not None and not refusal:
            return candidate, row
    pytest.skip(f"the matrix knows none of {name} here")


class TestTheContract:
    def test_module_exports(self):
        assert workbench.TITLE
        assert workbench.ICON
        assert callable(workbench.build)

    def test_its_section_is_one_the_sidebar_knows_a_home_for(self):
        assert workbench.SECTION == "What Chronoa did"
        assert workbench.SECTION in registry.SECTION_ORDER

    def test_the_registry_already_names_this_surface(self):
        """`SURFACE_IDS` is what `all_surfaces()` iterates, so a module nobody
        names there is a module nothing builds.

        Read with `ast` rather than by calling `all_surfaces()`, which imports
        every panel to answer - so one broken surface would turn this into a
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
                ids = [el.value for el in node.value.elts if isinstance(el, ast.Constant)]
        assert ids is not None, "gui/surfaces/__init__.py no longer defines SURFACE_IDS"
        assert ids.count("workbench") == 1, (
            f"'workbench' appears {ids.count('workbench')} times in SURFACE_IDS; the "
            f"sidebar offers one panel per id: {ids}")

    def test_the_icon_is_a_real_icon_name_on_this_machine(self):
        """A symbolic name nothing ships renders as a blank gap the size of an
        icon, which no assertion on the string can see."""
        roots = [Path("/usr/share/icons"), Path("/usr/local/share/icons")]
        if not any(root.is_dir() for root in roots):
            pytest.skip("no icon theme installed on this machine")
        found = [svg for root in roots if root.is_dir()
                 for svg in root.rglob(f"{workbench.ICON}.svg")]
        assert found, f"{workbench.ICON} is not an icon this machine has"


class TestTheBuild:
    def test_build_returns_a_gtk_widget_on_a_stub_app(self, temp_user_skills_dir):
        page = workbench.build(types.SimpleNamespace())
        assert isinstance(page, Gtk.Widget)

    def test_build_takes_an_app_with_no_attributes_at_all(self, temp_user_skills_dir):
        """`build(None)` must work: a panel that needs a config to open is a
        panel that does not open when there is none."""
        assert isinstance(workbench.build(None), Gtk.Widget)

    def test_the_built_ins_are_the_live_registry(self, temp_user_skills_dir):
        page = workbench.build(None)
        assert len(page.builtin_rows()) >= 100, (
            "the whitelist here is far smaller than the shipped one; a filter or "
            "a broken read would look like this")
        names = [row.get_title() for row in page.builtin_rows()]
        assert "get_datetime" in names, f"a shipped skill is missing from the page: {names[:8]}"

    def test_it_builds_without_the_generator(self, temp_user_skills_dir, monkeypatch):
        """The generator is not packaged, so an installed system has none.

        `build()` must still produce the panel and must *say* the generator is
        absent rather than failing to appear or claiming there are no commands.
        """
        monkeypatch.setattr(workbench, "_matrix_path", lambda: None)
        page = workbench.build(None)
        assert isinstance(page, Gtk.Widget)
        assert len(page.builtin_rows()) >= 100
        assert workbench.GENERATOR_MISSING in page.preview_status.get_text()
        assert not page.generate_button.get_sensitive(), (
            "the Generate button is live with no generator behind it")


class TestTheWhitelistAndTheDropIns:
    @pytest.mark.parametrize("argv, want", [
        (["true"], "verifies: verified"),
        (["false"], "verifies: failed"),
    ])
    def test_a_drop_in_row_reports_what_verify_returned(self, temp_user_skills_dir,
                                                        argv, want):
        """The verdict *and* the evidence, both directions.

        `verification.verify()` is the same call `tools.py` makes after every
        skill call, so a drop-in with a post-condition that exits 0 and one that
        exits non-zero must land on opposite sides of this row - and the failed
        one must carry `verify`'s own evidence string, not a word of the panel's
        own. A row that always said "unverified" would pass a weaker test and be
        useless.
        """
        temp_user_skills_dir.mkdir(parents=True, exist_ok=True)
        module = (temp_user_skills_dir / "checked.py")
        module.write_text(
            DROP_IN_TEMPLATE.format(name="checked", description="Checked.") +
            f"\nPOST_CONDITION = {argv!r}\n",
            encoding="utf-8")
        page = workbench.build(None)
        row = next(row for row in page.drop_in_rows() if row.get_title() == "checked")
        subtitle = row.get_subtitle()
        assert want in subtitle, f"{argv} did not reach the row: {subtitle!r}"
        if argv == ["false"]:
            assert "exit 1" in subtitle, (
                f"the evidence verify() returned is not on the row: {subtitle!r}")

    def test_a_drop_in_is_listed_separately_from_the_built_ins(self, temp_user_skills_dir):
        """Even when it takes a built-in's name, it stays a drop-in row.

        `discover_skills()` registers the built-ins and then lets a user module
        of the same name take its place, so the registry a drop-in has joined is
        not a list of what shipped. Listing the user's code under a heading
        that says *built-in* is exactly what this panel exists to prevent - so
        the shadowed built-in leaves the built-in group and the drop-in row says
        what it replaced.
        """
        shipped_before = len(workbench.build(None).builtin_rows())
        assert "get_datetime" in [row.get_title()
                                  for row in workbench.build(None).builtin_rows()], (
            "the fixture did not start from the shipped whitelist")

        _write_drop_in(temp_user_skills_dir, "get_datetime", "Mine, not theirs.")
        page = workbench.build(None)

        drop_in_titles = [row.get_title() for row in page.drop_in_rows()]
        builtin_titles = [row.get_title() for row in page.builtin_rows()]
        assert drop_in_titles == ["get_datetime"], (
            f"the drop-in is not in its own group: {drop_in_titles}")
        assert "get_datetime" not in builtin_titles, (
            "the user's own module is listed as a built-in skill")
        assert len(page.builtin_rows()) == shipped_before - 1, (
            f"the shadowed built-in did not leave the built-in group: "
            f"{len(page.builtin_rows())} rows, was {shipped_before}")
        assert "replaces the built-in skill: get_datetime" in \
            page.drop_in_rows()[0].get_subtitle(), (
            f"the row does not say what it replaced: {page.drop_in_rows()[0].get_subtitle()!r}")
        assert "verifies:" in page.drop_in_rows()[0].get_subtitle()

    def test_a_drop_in_that_does_not_import_is_still_a_row(self, temp_user_skills_dir):
        temp_user_skills_dir.mkdir(parents=True, exist_ok=True)
        broken = temp_user_skills_dir / "broken.py"
        broken.write_text("this is not python(", encoding="utf-8")
        page = workbench.build(None)
        rows = [row for row in page.drop_in_rows() if row.get_title() == "broken"]
        assert len(rows) == 1, "a broken drop-in cost this panel its row"
        assert "will not load" in rows[0].get_subtitle()

    def test_markup_in_a_skill_description_is_escaped(self, temp_user_skills_dir):
        """The rendered text is what the author wrote, not nothing at all.

        Asserted on `get_text()` because that is the rendered string. Handed
        unescaped, libadwaita 1.5 fails to parse a bare `&` and the label holds
        the empty string (measured) - so this cannot pass by looking right.
        """
        _write_drop_in(temp_user_skills_dir, "fancy", MARKUP_DESCRIPTION)
        page = workbench.build(None)
        rows = [row for row in page.drop_in_rows() if row.get_title() == "fancy"]
        assert len(rows) == 1
        rendered = [label.get_text() for label in _labels(rows[0])]
        assert any(MARKUP_DESCRIPTION in text for text in rendered), (
            f"the description is not on the row as written: {rendered!r}")
        assert not any("&amp;" in text or "&lt;b&gt;" in text for text in rendered), (
            f"the row is showing markup entities to a person: {rendered!r}")

    def test_the_search_narrows_the_list_and_the_control_proves_it(self, temp_user_skills_dir,
                                                                   monkeypatch):
        """The one negative control in this file: mutate, watch it fail, restore."""
        _write_drop_in(temp_user_skills_dir, "my_own_thing", "Something of my own.")
        page = workbench.build(None)
        everything = len(page.builtin_rows()) + len(page.drop_in_rows())
        assert everything >= 101, "the fixture did not build the page it is filtering"

        _filter_by(page, "my_own_thing")
        assert page.visible_row_count() == 1, (
            f"a filter that matched nothing is not a narrowing one: "
            f"{page.visible_row_count()} rows visible")

        # Forced to always match: every row comes back, which is what makes the
        # `== 1` above an assertion rather than a constant.
        monkeypatch.setattr(workbench, "_matches", lambda haystack, query: True)
        _filter_by(page, "my_own_thing")
        assert page.visible_row_count() == everything, (
            "the always-match control still narrowed, so the filter assertions "
            "above prove nothing")

        monkeypatch.undo()
        _filter_by(page, "my_own_thing")
        assert page.visible_row_count() == 1, "narrowing did not come back after restore"

    def test_a_filter_matching_nothing_says_so_rather_than_looking_empty(self,
                                                                       temp_user_skills_dir):
        page = workbench.build(None)
        _filter_by(page, "zzzznotaskillzzzz")
        assert page.visible_row_count() == 0
        assert "Showing 0 of" in page.summary.get_text()


class TestScaffolding:
    def test_a_generated_skill_is_written_to_the_users_directory_with_the_man_pages_flags(
            self, temp_user_skills_dir):
        command, row = _row_for(READ_ONLY_WITH_FLAGS)
        package_before = sorted(p.name for p in _packaged_skills().glob("*.py"))

        page = workbench.build(None)
        page.command_entry.set_text(command)
        generated = page.generate()

        assert not generated.refused, f"{command} was refused: {generated.detail}"
        assert generated.path is not None
        assert generated.path.parent == temp_user_skills_dir, (
            f"the generated skill would be written outside the user's own "
            f"directory: {generated.path}")
        assert generated.path == temp_user_skills_dir / f"{command}.py", (
            "the panel's filename is not the one --scaffold would use, so a "
            "file written here and a file written by the CLI are two skills")

        assert page.save_generated() is None
        written = temp_user_skills_dir / f"{command}.py"
        assert written.is_file(), "the panel said it wrote the file and did not"
        assert written.read_text(encoding="utf-8") == generated.source

        assert sorted(p.name for p in _packaged_skills().glob("*.py")) == package_before, (
            "a generated skill was written over the packaged skills")

        # Every flag the module will pass to the command is one the man page
        # documents, and asks for a value exactly when the page says it does.
        match = re.search(r"^FLAGS = (\{.*\})$", written.read_text(encoding="utf-8"), re.M)
        assert match, "the generated module has no FLAGS mapping"
        flags = ast.literal_eval(match.group(1))
        assert flags, f"the man page for {command} documented flags and none were generated"
        documented = {}
        for option in row["option_list"]:
            for flag in option["flags"]:
                documented.setdefault(flag, bool(option["arg"]))
        for key, (flag, takes_value) in flags.items():
            assert flag in documented, (
                f"{flag} is generated but the man page for {command} does not "
                f"document it; the page documents {sorted(documented)}")
            assert takes_value == documented[flag], (
                f"{flag} is generated as taking{' no' if not takes_value else ''} "
                f"value and the man page says otherwise")

    def test_the_generated_skill_really_loads_as_a_skill(self, temp_user_skills_dir):
        """Written, then loaded by the loader itself - the registry the model is
        handed - and the new skill is in it."""
        command, _row = _row_for(READ_ONLY_WITH_FLAGS)
        page = workbench.build(None)
        page.command_entry.set_text(command)
        page.generate()
        assert page.save_generated() is None

        from shani_chronoa import skills

        _tools, handlers = skills.discover_skills()
        assert command in handlers, (
            f"the generated module loaded by the loader did not register {command}")

    def test_it_will_not_overwrite_a_drop_in_that_is_already_there(self, temp_user_skills_dir):
        command, _row = _row_for(READ_ONLY_WITH_FLAGS)
        existing = _write_drop_in(temp_user_skills_dir, command, "Mine, by hand.")
        page = workbench.build(None)
        page.command_entry.set_text(command)
        page.generate()
        problem = page.save_generated()
        assert problem, "a second save reported no problem"
        assert workbench.ALREADY_THERE in problem
        assert "Mine, by hand." in existing.read_text(encoding="utf-8"), (
            "the existing drop-in was overwritten")

    def test_a_command_the_matrix_does_not_know_is_refused_in_those_words(
            self, temp_user_skills_dir):
        page = workbench.build(None)
        page.command_entry.set_text(UNKNOWN)
        generated = page.generate()

        assert generated.refused
        assert generated.refusal == workbench.MATRIX_UNKNOWN
        assert generated.source == "", "a refused command still produced a module"
        assert generated.path is None, "a refused command still had a destination"
        assert workbench.MATRIX_UNKNOWN in page.preview_status.get_text(), (
            f"the page does not say the matrix does not know it: "
            f"{page.preview_status.get_text()!r}")
        assert page.save_generated() is not None, (
            "there was nothing generated, and saving said it worked")
        assert not list(temp_user_skills_dir.glob("*.py")), (
            "a refused command still wrote a file")

    def test_something_that_is_not_a_command_name_is_refused_too(self, temp_user_skills_dir):
        for name in ("../../etc/passwd", "/usr/bin/uptime", "", "up time"):
            generated = workbench.generate(name)
            assert generated.refused, f"{name!r} was scaffolded"
            assert generated.refusal == workbench.NOT_A_COMMAND
            assert not list(temp_user_skills_dir.glob("*.py"))

    def test_a_command_that_would_change_something_is_generated_disabled(
            self, temp_user_skills_dir):
        command, row = _row_for(CHANGING)
        generated = workbench.generate(command)
        assert not generated.refused, f"{command} was refused: {generated.detail}"
        assert generated.disabled, (
            f"{command} is classified {row['safety']} and came out enabled")
        assert "ENABLED = False" in generated.source
        assert workbench.GENERATED_AND_DISABLED in generated.status, (
            f"the panel does not say it in those words: {generated.status!r}")

        page = workbench.build(None)
        page.command_entry.set_text(command)
        page.generate()
        assert workbench.GENERATED_AND_DISABLED in page.preview_status.get_text()

    def test_the_generated_changing_skill_refuses_to_run_until_reviewed(self,
                                                                     temp_user_skills_dir):
        """Labelled disabled is a claim; this is the behaviour.

        The generated module is imported exactly as the loader would and its
        `run({})` called, which is what a skill call reaches. It must refuse -
        otherwise the label on the panel is decoration.
        """
        import importlib.util

        command, _row = _row_for(CHANGING)
        generated = workbench.generate(command)
        assert not generated.refused
        assert workbench._write_skill(generated.path, generated.source) is None

        spec = importlib.util.spec_from_file_location("shani_chronoa_test_scaffold", generated.path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.ENABLED is False, "the generated module is armed"
        answer = module.SKILLS[0].run({})
        assert "can change things" in answer, (
            f"the generated skill did not refuse: {answer!r}")


class TestRemovingADropIn:
    def test_removing_a_drop_in_asks_first_and_removes_only_that_file(self, temp_user_skills_dir):
        alpha = _write_drop_in(temp_user_skills_dir, "alpha", "Mine, the first one.")
        beta = _write_drop_in(temp_user_skills_dir, "beta", "Mine, the second one.")
        page = workbench.build(None)
        row = next(row for row in page.drop_in_rows() if row.get_title() == "alpha")
        button = _button(row, "Remove")

        button.emit("clicked")
        assert alpha.is_file(), "clicking Remove deleted the file without asking"

        if common.adw_ready():
            dialog = page.workbench._dialog
            assert dialog is not None, "no confirmation was raised"
            assert "alpha.py" in f"{dialog.get_heading()} {dialog.get_body()}", (
                "the confirmation does not name the file it would delete")
            dialog.emit("response", "cancel")
            assert alpha.is_file(), "cancelling the confirmation deleted the file"
            button.emit("clicked")
            page.workbench._dialog.emit("response", "remove")
        else:
            # No libadwaita to ask with: the same button is the second step.
            assert "alpha.py" in page.note.get_text() or "alpha" in page.note.get_text()
            assert alpha.is_file()
            button.emit("clicked")

        assert not alpha.exists(), "the confirmed removal did not remove the file"
        assert beta.is_file(), "removing one drop-in removed another"
        assert [entry.name for entry in page.drop_in_entries()] == ["beta"]

    def test_the_confirmation_is_destructive_and_cancel_is_the_default(self,
                                                                      temp_user_skills_dir):
        """A confirmation whose default button is the destructive one is a
        confirmation a person dismisses with Return."""
        _write_drop_in(temp_user_skills_dir, "alpha", "Mine.")
        page = workbench.build(None)
        row = next(row for row in page.drop_in_rows() if row.get_title() == "alpha")
        _button(row, "Remove").emit("clicked")
        if not common.adw_ready():
            pytest.skip("no libadwaita here, so there is no dialog to inspect")
        dialog = page.workbench._dialog
        assert dialog.get_default_response() == "cancel"
        assert dialog.get_close_response() == "cancel"

    def test_write_skill_refuses_a_destination_outside_the_skills_directory(self,
                                                                            temp_user_skills_dir,
                                                                            tmp_path):
        outside = tmp_path / "somewhere_else.py"
        outside.write_text("# not a drop-in\n", encoding="utf-8")
        problem = workbench.remove_drop_in(outside)
        assert problem, "a file outside the skills directory was removed"
        assert outside.is_file(), "the file outside the skills directory was deleted"

        # And the write side is the same guard. The destination here does *not*
        # exist: a path that already exists is refused by `O_EXCL` for a
        # different reason, so using one would let this assertion pass on a
        # panel with no containment check at all.
        escape = tmp_path / "elsewhere"
        escape.mkdir()
        sneaky = escape / "sneaky.py"
        problem = workbench._write_skill(sneaky, "# generated\n")
        assert problem, "a generated skill was written outside the skills directory"
        assert not sneaky.exists(), "the file outside the skills directory was written"


def _packaged_skills() -> Path:
    """The shipped skills package directory - the one nothing may write to."""
    from shani_chronoa import skills

    return Path(skills.__file__).resolve().parent