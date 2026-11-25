"""The GUI's gettext catalog: extraction, real translations, RTL-safe strings.

The rule in `AI-DESKTOP.md` is that the UI works before the model does and never
silently falls back to English. These tests make that checkable without a
display: a catalog that covers every extracted string, a Hindi catalog that is
really translated, a locale with no translations that is not advertised, and
surfaces whose strings are whole messages rather than fragments.
"""

from __future__ import annotations

import ast
import re
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
GUI = REPO / "usr/lib/shani-chronoa/shani_chronoa/gui"
SURFACES = GUI / "surfaces"

import sys  # noqa: E402

sys.path.insert(0, str(REPO / "usr/lib/shani-chronoa"))

from shani_chronoa.gui import locale  # noqa: E402


def _marked_strings(root: Path) -> "dict[str, list[str]]":
    """file -> the literal strings passed to `_()` in a Python tree."""
    out: "dict[str, list[str]]" = {}
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found = [
            n.args[0].value
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_"
            and n.args
            and isinstance(n.args[0], ast.Constant)
            and isinstance(n.args[0].value, str)
        ]
        if found:
            out[path.name] = found
    return out


def _surface_titles() -> "dict[str, str]":
    """Every surface's own title, read from its source rather than importing GTK."""
    out = {}
    for path in sorted(SURFACES.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "TITLE"
            ):
                value = node.value
                if (
                    isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Name)
                    and value.func.id == "_"
                    and value.args
                    and isinstance(value.args[0], ast.Constant)
                ):
                    out[path.stem] = value.args[0].value
    return out


# --- extraction -------------------------------------------------------------


def test_pot_covers_every_marked_string():
    """Nothing marked `_()` in the GUI is absent from the translation template."""
    ids = set(locale.catalog_ids())
    assert ids, "the catalog template is empty"
    for name, strings in _marked_strings(GUI).items():
        missing = set(strings) - ids
        assert not missing, f"{name} marks strings absent from messages.pot: {missing}"


def test_all_surface_titles_and_subtitles_are_marked():
    """All 34 registered panels extracted, not a hand-picked few.

    `__init__.py` holds `SURFACE_IDS`; a title left as a bare literal would not
    be seen by xgettext and would silently stay English in every locale.
    """
    registry = (SURFACES / "__init__.py").read_text(encoding="utf-8")
    ids = re.findall(r'^\s+"([a-z_]+)",', registry, re.M)
    assert len(ids) >= 30
    titles = _surface_titles()
    missing = set(ids) - set(titles)
    assert not missing, f"surfaces without a translated TITLE: {missing}"

    for path in sorted(SURFACES.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in ("TITLE", "SUBTITLE")
            ):
                continue
            value = node.value
            assert (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "_"
            ), f"{path.name}: {node.targets[0].id} is not extracted with _()"


# --- catalogs ---------------------------------------------------------------


def test_every_locale_catalog_matches_the_template():
    ids = list(locale.catalog_ids())
    for path in locale.LOCALE_ROOT.glob("*/LC_MESSAGES/*.po"):
        entries = locale._entries(path)
        assert list(entries) == ids, f"{path}: msgids differ from messages.pot"
    assert locale._entries(locale.TEMPLATE) == dict.fromkeys(ids, "")


def test_hindi_translates_the_core_strings():
    """Every panel title and the shared status words are real Hindi, not blanks."""
    entries = locale._entries(locale._catalog_path("hi"))
    titles = set(_surface_titles().values())
    core = titles | {
        "Ready",
        "Needs attention",
        "Could not determine",
        "Turned off",
        "Details",
        "Reload",
        "Turn on",
        "Search",
        "Download",
    }
    assert not (core - set(entries)), "core strings missing from the Hindi catalog"
    missing = {msgid for msgid in core if not entries[msgid]}
    assert not missing, f"core strings left untranslated in Hindi: {missing}"
    assert entries["Privacy"] == "गोपनीयता"
    assert entries["Details"] == "विवरण"


def test_an_empty_catalog_is_not_a_translated_locale(tmp_path, monkeypatch):
    """A `.po` skeleton must never be advertised as a finished translation."""
    root = tmp_path / "locale"
    stub = root / "ta" / "LC_MESSAGES"
    stub.mkdir(parents=True)
    skeleton = "\n".join(
        f'msgid "{msgid.replace(chr(34), chr(92) + chr(34))}"\nmsgstr ""'
        for msgid in locale.catalog_ids()
    )
    (stub / "shani-chronoa.po").write_text(skeleton, encoding="utf-8")
    shutil.copy2(locale._catalog_path("hi", ".mo"), stub / "shani-chronoa.mo")
    monkeypatch.setattr(locale, "LOCALE_ROOT", root)

    assert not locale.is_translated("ta"), "empty msgstrs counted as a translation"
    assert "ta" not in locale.translated_locales()
    assert locale.untranslated("ta") == list(locale.catalog_ids())
    assert not locale.is_translated("en"), "English is the source, not a translation"


def test_only_real_catalogs_are_listed(tmp_path, monkeypatch):
    root = tmp_path / "locale"
    for code in ("hi", "xx"):
        target = root / code / "LC_MESSAGES"
        target.mkdir(parents=True)
        shutil.copy2(locale._catalog_path("hi", ".po"), target / "shani-chronoa.po")
        if code == "hi":
            shutil.copy2(locale._catalog_path("hi", ".mo"), target / "shani-chronoa.mo")
    monkeypatch.setattr(locale, "LOCALE_ROOT", root)
    assert locale.translated_locales() == ["hi"]


# --- behavior ---------------------------------------------------------------


def test_system_locale_comes_from_gettext_environment():
    assert locale.system_locale({"LANGUAGE": "hi:en", "LANG": "en_US.UTF-8"}) == "hi"
    assert locale.system_locale({"LC_ALL": "hi_IN.UTF-8"}) == "hi"
    assert locale.system_locale({"LANG": "hi-IN"}) == "hi"
    assert locale.system_locale({"LANG": "C", "LANGUAGE": ""}) == "en"


def test_hindi_is_used_from_the_system_locale(monkeypatch):
    for name in ("LANGUAGE", "LC_ALL", "LC_MESSAGES"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANG", "hi_IN.UTF-8")
    monkeypatch.setenv(locale.DEV_ENV, "0")
    locale.configure()
    assert locale.translate("Privacy") == "गोपनीयता"
    assert locale.translate("Diagnostics") == "निदान"


def test_dev_mode_surfaces_gaps_but_normal_mode_does_not(monkeypatch):
    missing = locale.untranslated("hi")
    assert missing, "Hindi catalog is complete; this control needs a gap"
    monkeypatch.delenv("LANGUAGE", raising=False)
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.setenv("LANG", "hi_IN.UTF-8")

    monkeypatch.setenv(locale.DEV_ENV, "1")
    locale.configure()
    assert locale.translate(missing[0]).startswith("[untranslated:hi] ")

    monkeypatch.setenv(locale.DEV_ENV, "0")
    locale.configure()
    assert locale.translate(missing[0]) == missing[0]


def test_missing_catalog_warns_and_does_not_claim_a_locale(monkeypatch):
    monkeypatch.setenv("LANG", "ta_IN.UTF-8")
    monkeypatch.setenv(locale.DEV_ENV, "0")
    locale.configure("ta")
    assert locale.translate("Privacy") == "Privacy"
    assert "ta" not in locale.translated_locales()


# --- layout safety ----------------------------------------------------------


CONCATENATED = re.compile(
    r'_\(\s*(?:"[^"]*"|\'[^\']*\')\s*\)\s*\+|\+\s*_\(\s*(?:"[^"]*"|\'[^\']*\')\s*\)'
)
DIRECTIONAL = re.compile(
    r"margin_(?:left|right)\b|padding-(?:left|right)\b|"
    r'["\'](?:left|right)["\']\s*[:=]'
)


@pytest.mark.parametrize(
    "name", ["privacy", "senses", "models", "calendar", "editor", "diagnostics"]
)
def test_sampled_surfaces_are_rtl_safe(name):
    """No directional padding, no messages built by joining translated pieces.

    GTK's `margin-start`/`margin-end`, `halign=START/END` and its own shaping
    flip with the text direction; a translated fragment joined with `+` cannot
    be reordered by a translator and breaks Hindi and RTL sentences alike.
    """
    source = (SURFACES / f"{name}.py").read_text(encoding="utf-8")
    assert "_(" in source, f"{name} has no extracted strings"
    assert not CONCATENATED.search(source), f"{name} concatenates a translated fragment"
    assert not DIRECTIONAL.search(source), f"{name} hard-codes a left/right edge"


def test_set_locale_skill_still_changes_the_system_locale():
    """The UI locale must not repurpose the `set_locale` skill or `language`."""
    source = (
        REPO / "usr/lib/shani-chronoa/shani_chronoa/skills/set_locale.py"
    ).read_text(encoding="utf-8")
    assert "set-locale" in source
    assert "gui.locale" not in source
