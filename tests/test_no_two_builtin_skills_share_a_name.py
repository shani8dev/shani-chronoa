"""No two built-in skills may claim the same tool name.

edge0's `registry.register_model` **raises** on a duplicate registration
(`src/edge0/registry.py:29`), because in a dict-of-names a second entry is
invisible: the later one wins and the earlier module's work is gone with no
error anywhere. That is not a hypothetical in this repository - a duplicate key
in a hand-kept table has been found here three times, and `pyflakes` caught all
three because the suite never would:

* `capabilities._GROUPS` listed `browse` twice with two different menu labels,
  so the better label was dead while the table read as correct;
* `tool_select` carried a dead duplicate `captions` entry, later key always
  winning;
* `space_by_type` arrived with three entries in one table.

This is the same failure one level up, and **nothing guarded it**, because
`discover_skills()` has already deduplicated by the time any test can look: it
returns schemas with no record of which module each came from. So the check
reads the modules' `SKILLS` lists directly.

**Static AST, deliberately.** Importing every module was tried first and four
of them - `fm_radio`, `ir_remote`, `list_apps`, `nfc` - could not be loaded
that way, so a fourth of the tree went unchecked. Reading the literal needs no
import, runs in milliseconds, and covers a module that will not even import on
this box.

**A user skill may still override a built-in**, and that is a documented feature
(`tests/test_duplicate_override_keeps_single_schema`), so only built-ins are
compared. A collision between a built-in and a user's file is the user's
decision; a collision between two built-ins is nobody's.
"""

from __future__ import annotations

import ast
import collections
import pathlib

SKILLS_DIR = (pathlib.Path(__file__).resolve().parents[1]
              / "usr" / "lib" / "shani-chronoa" / "shani_chronoa" / "skills")


def declared_names(source: str) -> list:
    """Tool names in one module's module-level ``SKILLS`` list.

    Parsed, not executed and not grepped: a ``# Skill(name="x")`` in a comment
    is prose about a skill rather than a skill, and a raw substring search
    cannot tell the difference - which is how `tests/test_user_prompts.py` and
    `tests/test_tool_tracking.py` each failed on their own explanatory comment
    before they were fixed.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out = []
    for node in tree.body:                      # module level only
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = ([node.target] if isinstance(node, ast.AnnAssign)
                   else node.targets)
        if not any(isinstance(t, ast.Name) and t.id == "SKILLS" for t in targets):
            continue
        value = node.value
        if not isinstance(value, (ast.List, ast.Tuple)):
            continue
        for element in value.elts:
            if not isinstance(element, ast.Call):
                continue
            func = element.func
            name_of_func = getattr(func, "id", getattr(func, "attr", None))
            if name_of_func != "Skill":
                continue
            for kw in element.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant) \
                        and isinstance(kw.value.value, str):
                    out.append(kw.value.value)
    return out


def builtin_names_by_module(directory=None) -> dict:
    """Module stem -> the tool names it declares.

    `directory` is a parameter, not a module constant that tests patch, so the
    planted-collision test below drives this exact function rather than a copy.
    """
    directory = SKILLS_DIR if directory is None else directory
    out = {}
    for path in sorted(directory.glob("*.py")):
        if path.stem == "__init__":
            continue
        names = declared_names(path.read_text(encoding="utf-8", errors="replace"))
        if names:
            out[path.stem] = names
    return out


def collisions(by_module: dict) -> dict:
    """`{name: [modules]}` for every name two or more built-ins declare.

    A named function so the planted-collision test can ask *this* question
    rather than restating it. When the collision rule lived only inside the test
    method, two mutations were invisible: deleting the `len(ms) > 1` condition,
    and reading only the first module. Nothing in the real tree collides, so the
    guard passed in both states - a check that cannot fail because its input is
    empty, which is the whole of this repository's standing complaint about
    negative-space assertions.
    """
    owners = collections.defaultdict(list)
    for module, names in sorted(by_module.items()):
        for name in names:
            owners[name].append(module)
    return {name: owners[name] for name in sorted(owners) if len(owners[name]) > 1}


class TestNoTwoBuiltinsClaimOneName:
    def test_it_checks_a_real_number_of_skills(self):
        """**The control.** A collector that finds nothing passes everything.

        Measured: 240 modules declare 244 names, which is the size of the
        registry AGENTS.md re-measured on 2026-10-11. If that collapses the
        parser has broken, not the tree.
        """
        by_module = builtin_names_by_module()
        assert len(by_module) > 200, (
            f"only {len(by_module)} skill modules were read - the parser is "
            f"broken, not the tree")
        assert sum(len(v) for v in by_module.values()) > 200

    def test_no_name_is_declared_by_two_modules(self):
        found = collisions(builtin_names_by_module())
        assert not found, (
            "two built-in skills declare the same tool name; the later one "
            "wins silently and the other module is dead with no error "
            "anywhere:\n" + "\n".join(
                f"  {n}: {', '.join(ms)}" for n, ms in found.items()))

    def test_a_planted_collision_is_caught(self, tmp_path):
        """**The positive case on the real path**, and the reason the rule is a
        named function rather than a line inside the test above.

        The real tree has no collisions, so the guard passes on an empty input
        and a broken guard looks identical to a healthy one. Two mutations were
        invisible before this existed: deleting the `len(ms) > 1` condition, and
        reading only the first module. Two modules on disk, one name, through
        the same two functions the real test calls.
        """
        (tmp_path / "alpha.py").write_text(
            'SKILLS = [Skill(name="get_datetime", run=_run)]\n')
        (tmp_path / "beta.py").write_text(
            'SKILLS = [Skill(name="get_datetime", run=_run),\n'
            '         Skill(name="get_world_time", run=_run)]\n')
        found = collisions(builtin_names_by_module(tmp_path))
        assert found == {"get_datetime": ["alpha", "beta"]}, (
            f"a planted collision was missed: {found} - the guard cannot see "
            f"the thing it exists to catch")

    def test_a_planted_tree_with_no_collision_is_clean(self, tmp_path):
        """And the other direction, so the planted test above cannot pass by
        `collisions()` reporting everything."""
        (tmp_path / "alpha.py").write_text('SKILLS = [Skill(name="a", run=_r)]\n')
        (tmp_path / "beta.py").write_text('SKILLS = [Skill(name="b", run=_r)]\n')
        assert collisions(builtin_names_by_module(tmp_path)) == {}

    def test_a_skill_named_in_a_comment_is_not_declared(self):
        """Prose about a skill is not a skill. Both existing tests in this repo
        that scan source have been bitten by their own comment."""
        src = ('# SKILLS = [Skill(name="commented_out")]\n'
               'SKILLS = [Skill(name="real_one", run=_run)]')
        assert declared_names(src) == ["real_one"]

    def test_a_module_that_will_not_import_is_still_checked(self):
        """The four modules an import-based collector missed.

        `fm_radio`, `ir_remote`, `list_apps` and `nfc` raise on a bare import,
        so the first version of this check - which used `exec_module` - left a
        quarter of the tree unguarded while still reporting success.
        """
        for stem in ("fm_radio", "ir_remote", "nfc"):
            path = SKILLS_DIR / f"{stem}.py"
            assert path.exists(), f"{stem}.py moved; update this test's list"
            assert declared_names(path.read_text(encoding="utf-8")), (
                f"{stem}.py declares no name the collector can see - either it "
                f"uses a shape this parser does not understand, or it is "
                f"unchecked, and both are the same failure here")
