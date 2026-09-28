"""Tests for `list_apps`, the discovery half of `open_application`.

The list is only useful if it cannot lie in two specific ways, and both are
covered here:

- an empty result means "the desktop-entry database is missing" far more often
  than it means "this machine has no applications", and those need different
  answers;
- apps marked `NoDisplay` are not offered, because `open_application` skips
  them too - and their count is reported, so a user who knows an app is
  installed is told why it is absent rather than left to conclude it is not.
"""

import pytest

from shani_chronoa import capabilities
from shani_chronoa.skills import discover_skills, list_apps
from shani_chronoa.skills.list_apps import AppEntry


def _entry(display, app_id, keywords=()):
    return AppEntry(display=display, app_id=app_id, keywords=tuple(keywords))


@pytest.fixture
def apps(monkeypatch):
    """A fixed catalogue, so the tests do not depend on what this box has."""
    catalogue = [
        _entry("Firefox", "firefox", ("web", "browser", "internet")),
        _entry("Files", "org.gnome.Nautilus", ("file", "manager")),
        _entry("Konsole", "org.kde.konsole", ("terminal", "shell", "console")),
        _entry("Text Editor", "org.gnome.TextEditor"),
    ]
    monkeypatch.setattr(list_apps, "_entries", lambda: (catalogue, 0))
    return catalogue


class TestListing:
    def test_it_lists_every_app_with_its_id(self, apps):
        out = list_apps._run({})
        for display, app_id in (
            ("Firefox", "firefox"), ("Files", "org.gnome.Nautilus"),
            ("Konsole", "org.kde.konsole"), ("Text Editor", "org.gnome.TextEditor"),
        ):
            assert display in out, f"{display} missing from the listing"
            assert app_id in out, f"{app_id} missing - the id is what opens it"

    def test_the_order_is_stable_and_case_insensitive(self, apps):
        first = list_apps._run({})
        second = list_apps._run({})
        assert first == second
        order = [line for line in first.splitlines() if "(" in line]
        assert order == sorted(order, key=str.lower)


class TestSearch:
    @pytest.mark.parametrize("term,expected", [
        ("fire", "Firefox"),
        ("konsole", "Konsole"),
        ("nautilus", "Files"),
        ("browser", "Firefox"),
        ("terminal", "Konsole"),
        ("edit", "Text Editor"),
    ])
    def test_it_searches_names_ids_and_keywords(self, apps, term, expected):
        out = list_apps._run({"search": term})
        assert expected in out

    def test_search_is_case_insensitive(self, apps):
        assert "Firefox" in list_apps._run({"search": "FIREFOX"})

    def test_no_match_does_not_claim_nothing_is_installed(self, apps):
        """The dangerous failure: a search miss reported as "you have no such
        application", when four are installed under other names."""
        out = list_apps._run({"search": "photoshop"})
        assert "No installed application matches" in out
        assert "4 are installed" in out, "did not say how many actually are"
        assert "broader" in out, "did not suggest a way forward"

    def test_an_empty_search_string_is_treated_as_no_filter(self, apps):
        assert list_apps._run({"search": "   "}) == list_apps._run({})


class TestHonesty:
    def test_an_empty_database_is_not_reported_as_an_empty_machine(self, monkeypatch):
        """Gio returning nothing nearly always means a missing desktop-entry
        database, which is indistinguishable from an empty machine from here."""
        monkeypatch.setattr(list_apps, "_entries", lambda: ([], 0))
        out = list_apps._run({})
        assert "cannot tell the difference" in out
        assert "No applications are installed" not in out

    def test_hidden_entries_are_counted_not_silently_dropped(self, monkeypatch):
        monkeypatch.setattr(list_apps, "_entries", lambda: ([_entry("Files", "files")], 7))
        out = list_apps._run({})
        assert "7 further entries" in out
        assert "not offered" in out, (
            "a hidden app is absent from the list with no explanation, so the "
            "user concludes it is not installed"
        )

    def test_a_long_list_says_it_was_cut_short(self, monkeypatch):
        many = [_entry(f"App {i:03d}", f"app{i:03d}") for i in range(45)]
        monkeypatch.setattr(list_apps, "_entries", lambda: (many, 0))
        out = list_apps._run({})
        assert "and 5 more" in out, "45 apps were shown as if they were all of them"


class TestCollection:
    """`_entries` against a fake Gio, since the real one depends on this box."""

    class _FakeInfo:
        def __init__(self, display, app_id, show=True, keywords=("a",), bare=False):
            self._d, self._i, self._s, self._k, self._bare = (
                display, app_id, show, keywords, bare)

        def should_show(self):
            return self._s

        def get_display_name(self):
            return self._d

        def get_id(self):
            return self._i

        def get_keywords(self):
            if self._bare:
                raise AttributeError("get_keywords")
            return self._k

    def _collect(self, monkeypatch, infos):
        monkeypatch.setattr(
            list_apps.Gio.AppInfo, "get_all", staticmethod(lambda: infos))

    def test_a_hidden_entry_is_excluded_and_counted(self, monkeypatch):
        self._collect(monkeypatch, [
            self._FakeInfo("Files", "files.desktop", show=True),
            self._FakeInfo("Secret", "secret.desktop", show=False),
        ])
        entries, hidden = list_apps._entries()
        assert [e.display for e in entries] == ["Files"]
        assert hidden == 1

    def test_the_desktop_suffix_is_stripped_from_the_id(self, monkeypatch):
        """`open_application` strips it, so keeping it here would offer an id
        that then fails to open."""
        self._collect(monkeypatch, [self._FakeInfo("Files", "org.gnome.Nautilus.desktop")])
        assert list_apps._entries()[0][0].app_id == "org.gnome.Nautilus"

    def test_an_entry_with_no_name_falls_back_to_its_id(self, monkeypatch):
        self._collect(monkeypatch, [self._FakeInfo("", "orphan.desktop")])
        entries, _ = list_apps._entries()
        assert [e.display for e in entries] == ["orphan.desktop"]

    def test_an_entry_with_neither_name_nor_id_is_skipped(self, monkeypatch):
        self._collect(monkeypatch, [self._FakeInfo("", "")])
        assert list_apps._entries() == ([], 0)

    def test_keywords_are_optional(self, monkeypatch):
        self._collect(monkeypatch, [self._FakeInfo("X", "x", bare=True)])
        entries, _ = list_apps._entries()
        assert entries[0].keywords == ()


class TestRegistry:
    def test_it_is_discovered_and_grouped(self):
        tools, _ = discover_skills()
        # discover_skills returns Ollama-shaped dicts, not objects.
        names = [t["function"]["name"] for t in tools]
        assert "list_apps" in names, "the skill loads but is not in the registry"
        found = capabilities.find_capabilities(tools)
        grouped = {c.tool: c.group for c in found}
        assert grouped.get("list_apps"), "list_apps is ungrouped, so the help window hides it"
        assert grouped["list_apps"] in capabilities.GROUP_ORDER

    def test_its_schema_is_valid(self):
        schema = list_apps._SCHEMA
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "list_apps"
        assert "search" in schema["function"]["parameters"]["properties"]
        assert not schema["function"]["parameters"].get("required")
