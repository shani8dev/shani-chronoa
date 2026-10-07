"""Every sidebar icon exists in Adwaita, the theme ShaniOS ships.

The icon checks elsewhere ask `Gtk.IconTheme.has_icon`, which answers for the
theme of whatever machine runs the suite. On a Yaru machine that passed
`chat-symbolic` and `office-calendar-symbolic`, neither of which Adwaita has -
so on the target GNOME desktop the Conversation and Calendar rows drew blank
boxes. This reads Adwaita's own files instead, and skips where it is absent.
"""

import pathlib

import pytest

ADWAITA = pathlib.Path("/usr/share/icons/Adwaita")


def _in_adwaita(name: str) -> bool:
    return any(ADWAITA.rglob(f"{name}.svg")) or any(ADWAITA.rglob(f"{name}.png"))


@pytest.mark.skipif(not ADWAITA.is_dir(), reason="Adwaita icon theme not installed")
def test_sidebar_icons_exist_in_adwaita():
    from shani_chronoa.gui import sidebar
    from shani_chronoa.gui.surfaces import all_surfaces
    names = {"chat": sidebar.CHAT_ICON}
    names.update({key: icon for key, (_t, icon, _b) in all_surfaces().items()})
    missing = {key: icon for key, icon in names.items() if not _in_adwaita(icon)}
    assert not missing, f"icons Adwaita does not ship, drawn blank on GNOME: {missing}"


@pytest.mark.skipif(not ADWAITA.is_dir(), reason="Adwaita icon theme not installed")
def test_the_check_can_see_a_missing_icon():
    """Control: the name this test was written for must read as missing."""
    assert not _in_adwaita("chat-symbolic")
    assert _in_adwaita("chat-message-new-symbolic")


def test_no_two_sidebar_rows_share_an_icon():
    from shani_chronoa.gui.surfaces import all_surfaces
    seen = {}
    for key, (_t, icon, _b) in all_surfaces().items():
        seen.setdefault(icon, []).append(key)
    shared = {icon: keys for icon, keys in seen.items() if len(keys) > 1}
    assert not shared, f"rows sharing one glyph: {shared}"


@pytest.mark.skipif(not ADWAITA.is_dir(), reason="Adwaita icon theme not installed")
def test_every_icon_the_gui_names_exists_in_adwaita():
    """The same gap beyond the sidebar: the "remembering" light, the tool
    card's tick and the muted-mic glyph were Yaru-only names, blank on GNOME."""
    import re
    root = pathlib.Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa/shani_chronoa"
    files = list((root / "gui").rglob("*.py")) + list((root / "settings_window").rglob("*.py"))
    files.append(root / "setup_wizard.py")
    names = set()
    for path in files:
        names.update(re.findall(r'"([a-z0-9-]+-symbolic)"', path.read_text()))
    missing = sorted(n for n in names if not _in_adwaita(n))
    assert not missing, f"icon names Adwaita does not ship: {missing}"
