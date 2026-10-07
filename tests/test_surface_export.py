"""The export surface: the way a person gets things out of Chronoa.

Everything below is asserted against the **content** - the text the exporters
would write - rather than against a click, because a button that renders is not
an export: `markdown_export(root, sid)` is the whole of the Markdown path, so
the expected transcript can be compared with what the store's own
`export_markdown` produced, byte for byte, instead of with a filename that
happened to be written somewhere.

The store is real. The conversation lives in a tmp `XDG_DATA_HOME` through
`conversation_store`'s own API (a `tool`-role message and all), and the percept
store is a real `PerceptStore` on tmp paths, so "what is not in the file" is a
statement about data that genuinely exists and was genuinely left out.

`common.adw_ready()` branches the two places libadwaita genuinely changes what
can be asserted: an `Adw.ActionRow` parses its title and subtitle as Pango
markup (so the panel escapes them, and a title containing `&` or `<` is the
evidence that it did), while the plain-GTK row is a `Gtk.Box` of labels that
takes no markup; and an `Adw.PreferencesGroup` takes rows through `add()` where
the plain one takes `append()`. Every invariant is asserted in both branches,
because both are answers a person can be shown.

The four rows each get their keys asserted by name, because the claim printed
beside the button and the code that writes the file are the same claim, and a
panel that lies about its own fields is worse than one that says nothing.
"""

from __future__ import annotations

import ast
import json
import time
import types
from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from shani_chronoa import conversation_store  # noqa: E402
from shani_chronoa.senses import Percept  # noqa: E402
from shani_chronoa.senses.store import PerceptStore  # noqa: E402

# `shani_chronoa.gui.surfaces.export` imports the `shani_chronoa.gui` package
# `__init__`, which is a real import on this machine (verified), so no stub is
# inserted here: if that import ever starts failing, this file should fail
# rather than paper over it.
from shani_chronoa.gui.surfaces import common  # noqa: E402
from shani_chronoa.gui.surfaces import export as surface  # noqa: E402

REGISTRY_SOURCE = Path(surface.__file__).parent / "__init__.py"

#: A tool message and a percept, both from senses that read the user's world.
#: These are the two things this panel could leak by accident, so they are the
#: two things every export is checked against.
TOOL_TEXT = "window titles: Payroll Q4 - Firefox, Secure Mail - Thunderbird"
FACT_TEXT = "the meeting is at 4pm in the annex"
PERCEPT_TEXT = "the screen shows the building access log for floor 3"
SECRET_TITLE = "what is 5/3, and is 12:30 past noon?"


# -- fixtures and helpers ---------------------------------------------------


@pytest.fixture
def root(tmp_path, monkeypatch):
    """A real conversations directory, in this test's own data home."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return conversation_store.session_dir()


@pytest.fixture
def talking(root):
    """A real conversation with a hostile title, a tool message and a fact.

    The title carries a `/` and a `:` because those are ordinary things to type
    and neither is legal in a path component on half the filesystems people
    export to; the tool message is the perception-bearing one.
    """
    path = conversation_store.active_path(root)
    conversation_store.append({"role": "user", "content": SECRET_TITLE}, path)
    conversation_store.append({"role": "assistant", "content": "1.67, and yes."}, path)
    conversation_store.append({"role": "tool", "content": TOOL_TEXT}, path)
    conversation_store.append({"role": "user", "content": "and my wifi password?"}, path)
    conversation_store.append({"role": "assistant", "content": "It is on the sticker."}, path)
    return path.stem


@pytest.fixture
def hostile(root):
    """A conversation whose title is markup, and a path character, at once."""
    path = conversation_store.active_path(root)
    conversation_store.append({"role": "user", "content": 'A & B < C > "x"'}, path)
    conversation_store.append({"role": "assistant", "content": "yes"}, path)
    return path.stem


@pytest.fixture
def store(tmp_path):
    """A real store holding one fact and one transient percept of the world."""
    percept_store = PerceptStore(
        durable_path=tmp_path / "percepts" / "memory.jsonl",
        live_path=tmp_path / "percepts" / "live.json",
    )
    percept_store.add(Percept(sense="memory", kind="fact", content=FACT_TEXT,
                              created_at=time.time(), ttl_seconds=None,
                              source="memory sense"))
    percept_store.add(Percept(sense="vision", kind="observation", content=PERCEPT_TEXT,
                              created_at=time.time(), ttl_seconds=300, source="vision"))
    return percept_store


def _app(store=None):
    return types.SimpleNamespace(percept_store=store)


def _walk(node):
    """Every widget in the tree, `node` included."""
    yield node
    child = node.get_first_child()
    while child is not None:
        yield from _walk(child)
        child = child.get_next_sibling()


def _marked(widget, css):
    return [w for w in _walk(widget) if css in w.get_css_classes()]


def _labels(widget):
    return [w.get_text() for w in _walk(widget) if isinstance(w, Gtk.Label) and w.get_text()]


def _buttons(row):
    return [w for w in _walk(row) if isinstance(w, Gtk.Button)]


def _statuses(page):
    """Every status label, as (label, text)."""
    return [(w, w.get_text()) for w in _marked(page, surface.STATUS_CSS)]


def _status_text(page):
    return " ".join(text for _label, text in _statuses(page))


def _button(row, label):
    found = [b for b in _buttons(row) if b.get_label() == label]
    assert found, f"no {label} button in {_labels(row)!r}"
    return found[0]


class _FakeFile:
    """What a `Gtk.FileDialog.save_finish()` hands back."""

    def __init__(self, path: Path):
        self._path = Path(path)
        self.written: "bytes | None" = None

    def replace_contents(self, data, etag, make_backup, flags, cancellable):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self.written = bytes(data)
        self._path.write_bytes(self.written)
        return True, None

    def get_path(self):
        return str(self._path)

    def get_uri(self):
        return self._path.as_uri()


class _FakeDialog:
    """A `Gtk.FileDialog` that answers immediately instead of asking a person.

    Substituted for `Gtk.FileDialog` itself - the name the module reads at call
    time - so the real code path runs: `set_initial_name`, `save`,
    `save_finish`, `replace_contents`, and the row's own sentence afterwards.
    """

    made: "list" = []
    cancelled = False
    target_dir: "Path | None" = None

    def __init__(self):
        self.initial_name = None
        self.title = None
        self.parent = "unset"
        type(self).made.append(self)

    def set_initial_name(self, name):
        self.initial_name = name

    def set_title(self, title):
        self.title = title

    def save(self, parent, cancellable, callback, user_data=None):
        # **The callback is called with the arity PyGObject really uses, which
        # is three.**
        #
        # `Gtk.FileDialog.save` takes a `Gio.AsyncReadyCallback`, and this fake
        # used to call it with one argument - so it exercised a shape the real
        # dialog never produces, and the production lambda
        # (`lambda _dlg, result: ...`) matched the fake rather than GTK. Measured
        # on this PyGObject with `Gio.File.load_contents_async`, whose callback is
        # the same `Gio.AsyncReadyCallback`: it is invoked as
        # `(GLocalFile, Gio.Task, None)` - source object, result, and the
        # `user_data` that was passed in.
        #
        # So the real save button raised `TypeError: <lambda>() takes 2
        # positional arguments but 3 were given` on **every** save, and both
        # `TestSaving` tests here failed for the same reason while reading as
        # two separate problems. A fake whose shape the real API never produces
        # is a fake that hides the bug it was written to find.
        self.parent = parent
        callback(self, "fake-result", user_data)

    def save_finish(self, result):
        if type(self).cancelled:
            raise GLib.Error("Operation was cancelled by the user")
        target = Path(type(self).target_dir) / self.initial_name
        return _FakeFile(target)


@pytest.fixture
def chooser(tmp_path, monkeypatch):
    """The fake chooser, in place of `Gtk.FileDialog` for the length of a test."""
    # Its own empty directory: `tmp_path` itself holds the fixtures' homes and
    # stores, and "a save wrote exactly one file" is not checkable in a
    # directory that already has three.
    _FakeDialog.made = []
    _FakeDialog.cancelled = False
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    _FakeDialog.target_dir = chosen
    monkeypatch.setattr(surface.Gtk, "FileDialog", _FakeDialog)
    return _FakeDialog


# -- the module contract ----------------------------------------------------


class TestModuleContract:
    def test_it_exports_what_the_registry_reads(self):
        assert surface.TITLE == "Export"
        assert isinstance(surface.ICON, str) and surface.ICON
        assert surface.SECTION == "Acting"
        assert callable(surface.build)

    def test_the_icon_is_a_real_icon_name_on_this_machine(self):
        """A symbolic icon nothing ships renders as a blank space-shaped gap."""
        roots = [Path("/usr/share/icons"), Path("/usr/local/share/icons")]
        if not any(root.is_dir() for root in roots):
            pytest.skip("no icon theme installed on this machine")
        found = [svg for root in roots if root.is_dir()
                 for svg in root.rglob(f"{surface.ICON}.svg")]
        assert found, f"{surface.ICON} is not an icon this machine has"

    def test_the_registry_names_this_surface(self):
        """`SURFACE_IDS` is what the sidebar offers.

        Read with `ast` rather than through `all_surfaces()`, which imports
        every other panel and would report their failures as this one's.
        """
        tree = ast.parse(REGISTRY_SOURCE.read_text(encoding="utf-8"))
        target = next(node for node in tree.body
                      if isinstance(node, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == "SURFACE_IDS"
                              for t in node.targets))
        assert isinstance(target.value, ast.Tuple), "SURFACE_IDS must stay a tuple"
        names = [elt.value for elt in target.value.elts if isinstance(elt, ast.Constant)]
        assert "export" in names, (
            f"the sidebar never offers this surface: {names}"
        )


# -- build ------------------------------------------------------------------


class TestBuild:
    def test_returns_a_widget_for_a_stub_app(self, root, talking, store):
        widget = surface.build(_app(store))
        assert isinstance(widget, Gtk.Widget)
        assert len(_marked(widget, surface.ROW_CSS)) == 4, _labels(widget)

    def test_every_row_offers_a_save_and_a_copy(self, root, talking, store):
        page = surface.build(_app(store))
        for row in page.rows():
            labels = [_b.get_label() for _b in _buttons(row)]
            assert "Save…" in labels and "Copy" in labels, labels

    def test_a_window_with_no_stores_at_all_still_builds(self):
        """An application object built before `_init_components` has neither.
        That is a failure to read, not an absence of things, and it must not be
        drawn as a panel that quietly exports an empty file."""
        page = surface.build(types.SimpleNamespace())
        shown = " ".join(_labels(page))
        assert surface.NO_STORE_REASON in shown, shown
        facts = page.rows()[-1]
        assert not _button(facts, "Save…").get_sensitive()
        assert "Not available" in _button(facts, "Save…").get_tooltip_text()

    def test_it_does_not_build_a_store_of_its_own(self, monkeypatch):
        """A `PerceptStore()` built here would read the default durable file and
        answer about a store the assistant is not using."""
        built = []
        real = PerceptStore.__init__

        def _spy(self, *args, **kwargs):
            built.append(kwargs.get("durable_path", args[0] if args else None))
            return real(self, *args, **kwargs)

        monkeypatch.setattr(PerceptStore, "__init__", _spy)
        surface.build(types.SimpleNamespace())
        assert built == [], f"the surface built its own store: {built}"


# -- Markdown ---------------------------------------------------------------


class TestMarkdown:
    def test_it_is_the_store_s_own_document_byte_for_byte(self, root, talking):
        """Not "a Markdown export" - *the* export, from
        `conversation_store.export_markdown`, which is what the `conversations`
        skill also calls. A second writer here could disagree with it."""
        document, reason = surface.markdown_export(root, talking)
        assert reason == ""
        _title, expected = conversation_store.export_markdown(root, talking)
        assert document.text == expected
        assert document.kind == "markdown" and document.name.endswith(".md")

    def test_it_says_what_was_said(self, root, talking):
        document, _reason = surface.markdown_export(root, talking)
        assert document.text.startswith("# what is 5/3, and is 12:30 past noon?\n")
        assert "**You:** what is 5/3, and is 12:30 past noon?" in document.text
        assert "**Chronoa:** 1.67, and yes." in document.text
        assert "**Chronoa:** It is on the sticker." in document.text
        assert document.text.endswith("\n")

    def test_the_tool_traffic_is_not_in_it(self, root, talking):
        """A tool result is where a sense's reading of the user's world
        arrives - `list_windows` runs the accessibility sense - so this is the
        line that decides whether an export can leak perception data."""
        document, _reason = surface.markdown_export(root, talking)
        assert TOOL_TEXT not in document.text
        assert "**tool**" not in document.text.lower()

    def test_a_conversation_that_is_not_there_says_so(self, root, talking):
        document, reason = surface.markdown_export(root, "20200101-000000-0000")
        assert document is None
        assert "no longer in the store" in reason, reason


# -- plain text -------------------------------------------------------------


class TestPlainText:
    def test_one_turn_per_line_and_no_markup(self, root, talking):
        document, reason = surface.plain_text_export(root, talking)
        assert reason == ""
        lines = document.text.splitlines()
        assert lines[0].startswith("Chat with Chronoa - what is 5/3")
        assert "You: what is 5/3, and is 12:30 past noon?" in lines
        assert "Chronoa: 1.67, and yes." in lines
        assert document.text.endswith("\n")
        assert not document.text.startswith("#") and "**" not in document.text

    def test_the_tool_traffic_is_not_in_it(self, root, talking):
        document, _reason = surface.plain_text_export(root, talking)
        assert TOOL_TEXT not in document.text
        assert not any(line.startswith("Tool:") for line in document.text.splitlines())


# -- JSON -------------------------------------------------------------------


class TestJson:
    def _payload(self, root, sid):
        document, reason = surface.json_export(root, sid)
        assert reason == ""
        return json.loads(document.text)

    def test_the_key_set_is_exactly_what_the_panel_claims(self, root, talking):
        payload = self._payload(root, talking)
        assert tuple(payload) == surface.JSON_KEYS, sorted(payload)
        assert tuple(payload["conversation"]) == surface.JSON_CONVERSATION_KEYS
        assert all(tuple(m) == surface.JSON_MESSAGE_KEYS for m in payload["messages"])
        assert payload["format"] == surface.CONVERSATION_FORMAT
        assert payload["version"] == surface.EXPORT_VERSION
        assert payload["conversation"]["id"] == talking

    def test_the_claim_on_the_row_is_the_key_set(self, root, talking, store):
        """The sentence printed beside the button names every key the exporter
        writes. A key added later without the sentence would fail here."""
        payload = self._payload(root, talking)
        shown = surface.JSON_FIELDS
        for key in list(payload) + list(payload["conversation"]) + list(payload["messages"][0]):
            assert key in shown, f"{key} is written but not claimed: {shown!r}"
        page = surface.build(_app(store))
        assert surface.JSON_FIELDS in " ".join(_labels(page))

    def test_it_carries_no_perception_data(self, root, talking, store):
        """The assertion this panel exists to be able to make: the tool result
        and the percept are both absent from the file, and no message claims to
        be a tool."""
        text = surface.json_export(root, talking)[0].text
        assert TOOL_TEXT not in text
        assert PERCEPT_TEXT not in text
        assert FACT_TEXT not in text
        payload = json.loads(text)
        assert {m["role"] for m in payload["messages"]} == {"user", "assistant"}

    def test_the_two_exports_say_the_same_thing(self, root, talking):
        payload = self._payload(root, talking)
        said = [(m["role"], m["text"]) for m in payload["messages"]]
        assert said == [("user", SECRET_TITLE), ("assistant", "1.67, and yes."),
                        ("user", "and my wifi password?"),
                        ("assistant", "It is on the sticker.")]


# -- the facts file ---------------------------------------------------------


class TestFacts:
    def _payload(self, store):
        document, reason = surface.facts_export(store)
        assert reason == ""
        return json.loads(document.text)

    def test_the_key_set_is_exactly_what_it_claims(self, store):
        payload = self._payload(store)
        assert tuple(payload) == surface.JSON_FACTS_KEYS, sorted(payload)
        assert all(tuple(f) == surface.JSON_FACT_KEYS for f in payload["facts"])
        assert payload["format"] == surface.FACTS_FORMAT
        assert payload["count"] == len(payload["facts"]) == 1

    def test_it_is_the_facts_and_not_what_a_sense_perceived(self, store):
        """The durable memory fact is in; the transient percept of the screen is
        not, whatever its lifetime - that is the whole claim of this row."""
        text = surface.facts_export(store)[0].text
        assert FACT_TEXT in text
        assert PERCEPT_TEXT not in text

    def test_a_durable_percept_from_another_sense_is_left_out(self, tmp_path):
        """`senses/scheduler.py` refuses to let a sense but `memory` declare a
        durable percept, and discards one that returns it anyway - but the
        store's own API does not enforce that, so the panel does not lean on
        it. Perception data that reached the durable tier is still perception
        data, and exporting it by accident is the failure this panel could
        cause."""
        other = PerceptStore(durable_path=tmp_path / "p" / "memory.jsonl",
                             live_path=tmp_path / "p" / "live.json")
        other.add(Percept(sense="memory", kind="fact", content=FACT_TEXT,
                          created_at=time.time(), ttl_seconds=None, source="memory sense"))
        other.add(Percept(sense="vision", kind="observation", content=PERCEPT_TEXT,
                          created_at=time.time(), ttl_seconds=None, source="vision"))
        payload = self._payload(other)
        assert payload["count"] == 1, payload
        assert PERCEPT_TEXT not in surface.facts_export(other)[0].text

    def test_an_expired_fact_is_absent_rather_than_quoted_as_current(self, tmp_path):
        """`active()` is the read path the context builder uses, so an expired
        fact stops being something Chronoa believes."""
        other = PerceptStore(durable_path=tmp_path / "q" / "memory.jsonl",
                             live_path=tmp_path / "q" / "live.json")
        other.add(Percept(sense="memory", kind="fact", content=FACT_TEXT,
                          created_at=time.time() - 7200, ttl_seconds=None,
                          source="memory sense", valid_until=time.time() - 3600))
        document, reason = surface.facts_export(other)
        assert reason == ""
        assert document.text.count(FACT_TEXT) == 0, document.text

    def test_a_missing_store_is_a_reason_not_a_crash(self):
        document, reason = surface.facts_export(None)
        assert document is None
        assert reason == surface.NO_STORE_REASON
        assert "no percept store" in reason

    def test_a_store_that_raises_says_so(self):
        class _Exploding:
            def active(self, now=None):
                raise OSError("the percept file is on a disk that is gone")

        document, reason = surface.facts_export(_Exploding())
        assert document is None
        assert "the percept file is on a disk that is gone" in reason, reason

    def test_the_row_says_it_is_facts(self, store):
        shown = " ".join(_labels(surface.build(_app(store))))
        assert "fact" in shown
        assert "perceived" in shown, shown


# -- names ------------------------------------------------------------------


class TestSafeNames:
    @pytest.mark.parametrize("raw", ["what is 5/3", "12:30 standup", "a/b:c",
                                     "../../etc/passwd", "  ", ".", ".."])
    def test_a_slash_or_a_colon_never_reaches_a_name(self, raw):
        name = surface.safe_filename(raw)
        assert "/" not in name and ":" not in name, name
        assert name not in {".", "..", ""}
        assert not name.startswith("."), name

    def test_it_keeps_what_a_person_would_recognise(self):
        assert surface.safe_filename("dinner ideas") == "dinner ideas"
        # The trailing "?" is replaced and then trimmed, so the name does not
        # end in a separator-looking character.
        assert surface.safe_filename("what is 5/3?") == "what is 5-3"

    def test_a_very_long_name_is_cut_at_a_word_boundary(self):
        name = surface.safe_filename(" ".join(["word"] * 40))
        assert len(name) <= surface.NAME_LIMIT
        assert not name.endswith(" ")

    def test_the_document_name_is_safe_and_dated(self, root, talking):
        for document, extension in ((surface.markdown_export(root, talking)[0], "md"),
                                    (surface.plain_text_export(root, talking)[0], "txt"),
                                    (surface.json_export(root, talking)[0], "json")):
            assert "/" not in document.name and ":" not in document.name, document.name
            assert document.name.endswith(f".{extension}")
            assert time.strftime("%Y-%m-%d") in document.name

    def test_the_name_the_chooser_is_offered_is_the_safe_one(self, root, talking, store, chooser):
        page = surface.build(_app(store))
        _button(_marked(page, surface.ROW_CSS)[0], "Save…").emit("clicked")
        assert len(chooser.made) == 1
        dialog = chooser.made[0]
        assert dialog.initial_name == surface.markdown_export(root, talking)[0].name
        assert "/" not in dialog.initial_name and ":" not in dialog.initial_name
        assert "5-3" in dialog.initial_name, dialog.initial_name


# -- saving and copying -----------------------------------------------------


class TestSaving:
    def test_a_save_writes_the_bytes_and_the_row_says_where(self, root, talking, store, chooser):
        page = surface.build(_app(store))
        row = _marked(page, surface.ROW_CSS)[0]
        _button(row, "Save…").emit("clicked")

        written = list(chooser.target_dir.glob("*"))
        assert len(written) == 1, written
        expected = surface.markdown_export(root, talking)[0].text
        assert written[0].read_text(encoding="utf-8") == expected
        assert str(written[0]) in _status_text(page), _status_text(page)

    def test_the_chooser_is_modal_to_the_window_it_was_pressed_from(self, root, talking, store, chooser):
        window = Gtk.Window()
        box = surface.build(_app(store))
        window.set_child(box)
        try:
            _button(_marked(box, surface.ROW_CSS)[0], "Save…").emit("clicked")
            assert chooser.made[-1].parent is window, chooser.made[-1].parent
        finally:
            window.destroy()

    def test_a_cancelled_chooser_says_not_saved(self, root, talking, store, chooser):
        chooser.cancelled = True
        page = surface.build(_app(store))
        _button(_marked(page, surface.ROW_CSS)[0], "Save…").emit("clicked")
        assert "Not saved" in _status_text(page), _status_text(page)
        assert "cancelled" in _status_text(page), _status_text(page)
        assert not list(chooser.target_dir.glob("*")), "a cancelled save wrote something"

    def test_copy_puts_the_same_text_on_the_clipboard(self, root, talking, store, monkeypatch):
        """Patched where the module reads it: `Gdk.ContentProvider.new_for_value`
        is the current clipboard API, and the argument it is handed is the whole
        claim, so recording it is stronger than checking that a status label
        changed."""
        copied = []
        real = Gdk.ContentProvider.new_for_value

        def _record(value):
            copied.append(value)
            return real(value)

        monkeypatch.setattr(Gdk.ContentProvider, "new_for_value", staticmethod(_record))
        page = surface.build(_app(store))
        _button(_marked(page, surface.ROW_CSS)[0], "Copy").emit("clicked")
        assert copied == [surface.markdown_export(root, talking)[0].text]
        assert "Copied to the clipboard" in _status_text(page), _status_text(page)

    def test_a_clipboard_that_cannot_be_reached_says_so(self, root, talking, store, monkeypatch):
        """No display is a state this row can name; it is not a window crash."""
        def _no_clipboard(_self):
            raise RuntimeError("no display")

        monkeypatch.setattr(Gtk.Widget, "get_clipboard", _no_clipboard)
        page = surface.build(_app(store))
        _button(_marked(page, surface.ROW_CSS)[0], "Copy").emit("clicked")
        assert "Could not reach the clipboard" in _status_text(page), _status_text(page)
        assert "no display" in _status_text(page), _status_text(page)


class TestCallbackArity:
    """The save callback's signature, checked against the installed PyGObject.

    `Gtk.FileDialog.save` takes a `Gio.AsyncReadyCallback`, and this file's fake
    used to invoke it with one argument - so it exercised a shape the real dialog
    never produces. The production lambda matched the fake, and every real save
    raised `TypeError: <lambda>() takes 2 positional arguments but 3 were given`.

    Nothing caught it, because nothing drove a real dialog, and the fake was
    written to match the code rather than the library. So the arity is asserted
    here against a *real* `Gio.AsyncReadyCallback` - `Gio.File.load_contents_async`
    takes one too - rather than against the fake above. If PyGObject ever changes
    how many arguments it passes, this fails and the fake is corrected with it.
    """

    def test_an_async_ready_callback_is_called_with_three_arguments(self, tmp_path):
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib

        path = tmp_path / "probe.txt"
        path.write_bytes(b"probe")
        seen = []
        loop = GLib.MainLoop()

        def callback(*args):
            seen.append(args)
            loop.quit()

        Gio.File.new_for_path(str(path)).load_contents_async(None, callback, None)
        GLib.timeout_add(5000, loop.quit)
        loop.run()

        assert seen, "the callback never ran, so this proves nothing"
        assert len(seen[0]) == 3, (
            f"PyGObject passed {len(seen[0])} arguments "
            f"({[type(a).__name__ for a in seen[0]]}); the export panel's save "
            "callback and the fake above must both match this, and the fake "
            "is the one that was wrong")


# -- a missing conversation store -------------------------------------------


class TestMissingStore:
    def test_an_empty_store_says_there_is_nothing_open(self, root):
        page = surface.build(types.SimpleNamespace())
        shown = " ".join(_labels(page))
        assert surface.NO_CONVERSATION_REASON in shown, shown
        assert len(_marked(page, surface.BANNER_CSS)) == 1, _labels(page)
        for row in page.rows()[:3]:
            assert not _button(row, "Save…").get_sensitive()
            assert surface.NO_CONVERSATION_REASON in _button(row, "Save…").get_tooltip_text()

    def test_a_store_that_cannot_be_read_is_not_an_empty_store(self, root, monkeypatch):
        def _boom(_root):
            raise OSError("the sessions directory is on a disk that is gone")

        monkeypatch.setattr(conversation_store, "index", _boom)
        page = surface.build(types.SimpleNamespace())
        shown = " ".join(_labels(page))
        assert "the sessions directory is on a disk that is gone" in shown, shown
        assert "Nothing to export from a conversation" in shown, shown
        for row in page.rows()[:3]:
            assert not _button(row, "Save…").get_sensitive()

    def test_nothing_is_written_when_there_is_nothing_to_export(self, root, store, chooser):
        """An empty conversation must produce a sentence, not an empty file: a
        zero-byte export reads as "this conversation had no content"."""
        page = surface.build(_app(store))
        _button(_marked(page, surface.ROW_CSS)[2], "Save…").emit("clicked")
        assert "Nothing to save" in _status_text(page), _status_text(page)
        assert not chooser.made, "a chooser was opened with nothing to write"
        assert not list(chooser.target_dir.glob("*"))


# -- markup -----------------------------------------------------------------


class TestMarkup:
    """A conversation's title is user text on a widget that renders markup.

    The failure mode is measured, not assumed: an `Adw.ActionRow` handed
    `A & B < C > "x"` unescaped fails its markup parse and renders an **empty**
    row, so the invariant below - the exact characters are on screen - can only
    hold because the panel escaped them, and getting it wrong shows up as a
    blank row rather than as a wrong character.
    """

    HOSTILE = 'A & B < C > "x"'

    def test_the_title_reads_back_exactly_in_both_branches(self, root, hostile, store):
        page = surface.build(_app(store))
        shown = _labels(page)
        # The title is on a group description that continues past it, so this is
        # a containment check on the rendered characters rather than equality
        # with a label's whole text.
        assert any(self.HOSTILE in text for text in shown), shown
        # Nothing rendered empty: an unparsed markup title leaves no text at
        # all, which is the failure this class exists to catch.
        assert any(text.strip() for text in shown), shown

    def test_the_group_says_which_conversation_is_open(self, root, hostile, store):
        page = surface.build(_app(store))
        described = " ".join(_labels(page))
        assert self.HOSTILE in described, described
        assert "message(s)" in described, described

    @pytest.mark.skipif(not common.adw_ready(), reason="no libadwaita: no markup is parsed")
    def test_with_adw_the_description_round_trips_to_the_characters(self, root, hostile, store):
        """`Adw.PreferencesGroup` parses its description as markup.

        **The getter does not hand the characters back, and this test used to
        assert that it did.** Measured on the installed libadwaita 1.9.1:

            set_description("A &amp; B &lt; C &gt; \\"x\\"")
              -> get_description() == 'A &amp; B &lt; C &gt; "x"'
            set_description('A & B < C > "x"')
              -> get_description() == 'A & B < C > "x"', and libadwaita
                 warns "Failed to set text ... from markup due to error
                 parsing markup" and renders nothing.

        So the getter returns what was *set*, and the panel is right to set the
        escaped form. What actually has to hold is narrower and checkable: the
        description libadwaita receives **parses as markup**, which is exactly
        what the raw string above fails. Asserting the round trip instead was
        asserting a third-party library's behaviour, it failed against the
        version installed here, and - being a shape assertion - it would have
        been satisfied by a panel that escaped nothing at all.

        The row titles here are the module's own constants and are not what
        this is about.
        """
        gi.require_version("Adw", "1")
        gi.require_version("Pango", "1.0")
        from gi.repository import Adw, Pango

        page = surface.build(_app(store))
        groups = [w for w in _walk(page) if isinstance(w, Adw.PreferencesGroup)]
        descriptions = [g.get_description() for g in groups]
        assert descriptions, "no group descriptions to check"

        described = [d for d in descriptions if "message(s)" in d]
        assert described, f"the open conversation's description is missing: {descriptions}"

        # The control for this assertion. Without it, a test that only checked
        # "the description parses" would also pass on a description containing
        # no markup at all - and this class exists precisely because the
        # characters are markup.
        with pytest.raises(GLib.Error):
            Pango.parse_markup(self.HOSTILE, -1, "\0")

        for description in described:
            # What the panel set must parse, or libadwaita renders nothing at
            # all - which is how this failure presents: a blank row, not a
            # wrong character.
            ok, _attrs, text, _accel = Pango.parse_markup(description, -1, "\0")
            assert ok, description
            # ...and the parse must hand back the *characters*, not the
            # entities: this is the round trip, checked against Pango, which is
            # the parser libadwaita itself uses.
            assert self.HOSTILE in text, text

        assert groups[0].get_title() == "The open conversation", groups[0].get_title()

    @pytest.mark.skipif(common.adw_ready(), reason="libadwaita: rows are not Gtk.Labels")
    def test_without_adw_the_row_is_a_plain_box_of_labels(self, root, hostile, store):
        page = surface.build(_app(store))
        rows = _marked(page, surface.ROW_CSS)
        assert len(rows) == 4
        assert all(isinstance(row, Gtk.Box) for row in rows)
        assert not any(isinstance(row, Gtk.Label) for row in rows)


# -- negative control -------------------------------------------------------


def test_letting_tool_messages_through_must_break_the_guarantee(root, talking, store):
    """The one control: `EXPORTED_ROLES` is patched to admit the `tool` role -
    the mistake this panel could make - and the JSON export must then leak the
    tool result, so the assertion that says it does not is live. The patch is
    released inside the test and the guarantee asserted again afterwards,
    because a control that leaves its mutation behind is a change hidden from
    the next reader.
    """
    clean = surface.json_export(root, talking)[0].text
    assert TOOL_TEXT not in clean

    with pytest.MonkeyPatch.context() as ctx:
        ctx.setattr(surface, "EXPORTED_ROLES", ("user", "assistant", "tool"))
        leaked = surface.json_export(root, talking)[0].text
        assert TOOL_TEXT in leaked, (
            "the mutation did not change anything, so the guarantee's test is "
            "asserting a difference that is not there"
        )
        assert {m["role"] for m in json.loads(leaked)["messages"]} == {"user", "assistant", "tool"}

    restored = surface.json_export(root, talking)[0].text
    assert restored == clean
    assert TOOL_TEXT not in restored