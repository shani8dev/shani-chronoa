"""The conversations surface as a built widget.

Drives a real temp conversation store (files, like production) and checks the
search entry actually narrows the visible rows - including a negative control
that breaks the filter on purpose to prove the row-count check can fail.

**The filter is typed, not set.** The entry is a `Gtk.SearchEntry`, whose
`search-changed` fires 150 ms after the last keystroke; `set_text()` emits
neither signal, so a test that filters on a programmatic set and reads the count
immediately is asserting against the *previous* query - it would still pass with
a filter that never ran at all, which is the mistake this file's control exists
to rule out. `_filter_by` types and pumps the main context, the same way
`test_surface_activity.py` drives its own.
"""

import sys
import time

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

# The surfaces package __init__ does `import Gtk`; pygobject never registers
# that short name, so seed it for the package import to find.
sys.modules.setdefault("Gtk", Gtk)

from shani_chronoa import conversation_store  # noqa: E402
from shani_chronoa.gui.surfaces import conversations  # noqa: E402


def _pump(ms=500):
    """Run the main context for `ms` so the debounce can expire."""
    context = GLib.MainContext.default()
    deadline = time.monotonic() + ms / 1000.0
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        time.sleep(0.005)


def _filter_by(surface, text):
    """Type `text` into the filter entry and wait for the filter it causes.

    `set_text("")` clears and is silent by design; the insert below is what a
    keystroke does, and it is what emits `search-changed` once the search delay
    expires. A filter that never fires leaves the previous row count in place,
    which the assertions below would then read as a result.
    """
    entry = surface.search
    entry.set_text("")
    entry.insert_text(text, -1)
    _pump()


class _StubApp:
    assistant = None
    _assistant = None
    window = None

    def __init__(self):
        self.opened = []
        self.deleted = []
        self.reset_calls = 0

    def _open_conversation(self, ref):
        self.opened.append(ref)

    def _reset_conversation(self, _action, _param):
        self.reset_calls += 1

    def _delete_conversation(self, ref):
        self.deleted.append(ref)


@pytest.fixture
def store(tmp_path, monkeypatch):
    # The conversation store resolves from XDG_DATA_HOME (files.data_home);
    # XDG_STATE_HOME alone would NOT redirect it, so set both.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return conversation_store.session_dir()


def _seed(text_pairs):
    conversation_store.new_session(conversation_store.session_dir())
    path = conversation_store.active_path(conversation_store.session_dir())
    for role, text in text_pairs:
        conversation_store.append({"role": role, "content": text}, path)
    return path


def _visible(surface):
    return [t for t in surface.rows() if t[0].get_visible()]


def test_module_contract():
    assert isinstance(conversations.TITLE, str)
    assert isinstance(conversations.ICON, str)
    assert callable(conversations.build)


def test_build_returns_a_widget_with_a_stub_app():
    surface = conversations.build(_StubApp())
    assert isinstance(surface, Gtk.Widget)


def test_missing_store_shows_honest_empty_state(store):
    surface = conversations.build(_StubApp())
    assert isinstance(surface, Gtk.Widget)
    assert surface.rows() == []
    assert surface.empty() is True


def test_stale_index_json_is_not_a_crash(store):
    store.mkdir(parents=True)
    (store / conversation_store.INDEX).write_text("{ not json", encoding="utf-8")
    surface = conversations.build(_StubApp())
    assert surface.rows() == []
    assert surface.empty() is True


def test_new_and_row_buttons_route_to_the_app():
    app = _StubApp()
    surface = conversations.build(app)
    surface.new_button.emit("clicked")
    assert app.reset_calls == 1


def test_rows_are_newest_first_and_search_filters(store):
    _seed([("user", "my router keeps dropping wifi"), ("assistant", "move it somewhere open")])
    _seed([("user", "dinner ideas with lentils"), ("assistant", "try dal")])

    surface = conversations.build(_StubApp())
    titles = []
    for row, open_btn, _d, _h in surface.rows():
        label = open_btn._title.get_label()
        titles.append(label)
    assert "dinner ideas" in titles[0], titles
    assert "router" in titles[1], titles
    assert surface.visible_row_count() == 2

    _filter_by(surface, "router")
    assert surface.visible_row_count() == 1
    assert _visible(surface)[0][1]._title.get_label().startswith("my router")

    _filter_by(surface, "")
    assert surface.visible_row_count() == 2

    # negative control: an always-match filter must break the row-count check
    with pytest.MonkeyPatch.context() as ctx:
        ctx.setattr(conversations, "_matches", lambda haystack, query: True)
        _filter_by(surface, "router")
        assert surface.visible_row_count() == 2
        with pytest.raises(AssertionError):
            assert surface.visible_row_count() == 1

    # restored
    surface._apply_filter(surface.search.get_text())
    assert surface.visible_row_count() == 1

    # row buttons route to the application's own methods
    app = _StubApp()
    surface2 = conversations.build(app)
    for row, open_btn, delete, _h in surface2.rows():
        open_btn.emit("clicked")
        delete.emit("clicked")
    assert len(app.opened) == 2 and len(app.deleted) == 2


def test_no_matches_label_when_filter_excludes_everything(store):
    _seed([("user", "router question"), ("assistant", "answer")])
    surface = conversations.build(_StubApp())
    _filter_by(surface, "zzz-nothing-like-this")
    assert surface.visible_row_count() == 0
    assert surface.no_matches.get_visible() is True


def test_row_dates_are_relative():
    """`Today 14:02` reads at a glance; `(07 Oct 14:02)` had to be compared to a calendar."""
    import time as _t
    now = _t.mktime((2026, 10, 7, 15, 0, 0, 0, 0, -1))
    at = lambda d, h: _t.mktime((2026, 10, d, h, 5, 0, 0, 0, -1))
    assert conversations._when(at(7, 9), now) == "Today 09:05"
    assert conversations._when(at(6, 23), now) == "Yesterday 23:05"
    assert conversations._when(at(3, 8), now).endswith(" 08:05")
    assert not conversations._when(at(3, 8), now).startswith(("Today", "Yesterday"))
    assert conversations._when(_t.mktime((2026, 9, 2, 8, 0, 0, 0, 0, -1)), now) == "02 Sep"
    assert conversations._when(_t.mktime((2025, 9, 2, 8, 0, 0, 0, 0, -1)), now) == "02 Sep 2025"


def test_a_conversation_can_be_renamed_from_its_row(store):
    """`conversation_store.rename` existed with no control anywhere."""
    _seed([("user", "my router keeps dropping wifi"), ("assistant", "move it")])
    surface = conversations.build(_StubApp())
    row = surface.rows()[0][0]
    renames = [n for n in _walk_all(row) if isinstance(n, Gtk.MenuButton)
               and n.get_tooltip_text() == "Rename this conversation"]
    assert renames, "no rename control on the row"
    button = renames[0]
    button._rename_entry.set_text("Wi-Fi troubleshooting")
    button._rename_save.emit("clicked")
    listed = conversation_store.list_sessions(conversation_store.session_dir())
    assert listed[0]["title"] == "Wi-Fi troubleshooting"


def _walk_all(node, out=None):
    out = [] if out is None else out
    out.append(node)
    child = node.get_first_child()
    while child is not None:
        _walk_all(child, out)
        child = child.get_next_sibling()
    return out


def test_branching_keeps_the_original_and_opens_a_copy(store):
    """`conversation_store.fork` had no control; chat was the only way."""
    _seed([("user", "plan a trip to Pune"), ("assistant", "two days is enough")])
    opened = []
    app = _StubApp()
    app._open_conversation = opened.append
    surface = conversations.build(app)
    row = surface.rows()[0][0]
    branch = next(n for n in _walk_all(row) if isinstance(n, Gtk.Button)
                  and (n.get_tooltip_text() or "").startswith("Branch"))
    before = conversation_store.list_sessions(conversation_store.session_dir())
    branch.emit("clicked")
    after = conversation_store.list_sessions(conversation_store.session_dir())
    assert len(after) == len(before) + 1, "no copy was made"
    assert opened and opened[0] != before[0]["id"], "the copy was not opened"
    assert sum(1 for s in after if s["messages"] == 2) == 2, "the original lost its messages"
