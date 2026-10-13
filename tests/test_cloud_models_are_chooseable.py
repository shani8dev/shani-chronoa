"""Cloud models can be listed, read and switched - in the picker and the palette.

Both surfaces existed before this and neither could do it. The picker's only
child was a bare `Gtk.ListBox` of **local** tiers, so `kilo` and every other
provider were invisible; and the Ctrl+K palette - which `AGENTS.md` records as
"built, fed 50 entries, and **never added to any container**" - could not show
anything at all, because a `Gtk.Box` cannot be a toplevel.

**The two surfaces read one cache.** `cloud_llm._MODEL_CACHE` is filled by a
worker and read by both, so a list of models cannot exist in one place and not
the other - the second-source-of-truth failure `ModelPicker`'s own docstring is
written against.

Nothing here touches the network. `fetch_models` is stubbed by `_no_network`,
and **that stub is the control**: if a test here ever reached the real one it
would hang or answer from a live provider, so the fixture asserts it is in
place and every list below is built from what the stub was told to return.

**What is measured rather than asserted elsewhere in this file.** The chosen
model is shown *even when the provider's published list does not carry it* -
`offerable_models` caps at `cloud_llm._MENU_LIMIT` (12) while OpenRouter
publishes 458, so the row that says "answering now" would otherwise vanish on
any machine whose choice sits past the cap. That is the one assertion here
about a list's *shape*; everything else is about a state being reachable.
"""

import sys
from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

Adw.init()
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import cloud_llm  # noqa: E402
from shani_chronoa.gui import model_picker, palette  # noqa: E402
from shani_chronoa.gui.window import ChronoaWindow  # noqa: E402


# ── fixtures ──────────────────────────────────────────────────────────────


class _Config:
    """The three things both surfaces ask a config for, and nothing else."""

    def __init__(self, models=None, keys=None, base=""):
        self._models = models or {}
        self._keys = keys or {}
        self._base = base

    def cloud_llm_models(self):
        return dict(self._models)

    def cloud_llm_api_keys(self):
        return dict(self._keys)

    def get(self, key, default=""):
        return self._base if key == "custom-llm-base-url" else default


class _App:
    def __init__(self, **kw):
        self.config = _Config(**kw)
        self.activated = []

    def activate_action(self, name, parameter=None):
        self.activated.append((name, parameter))


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """No provider is asked for real, and the cache starts empty.

    **The cache is module-level state**, so a test that leaves a fetched
    provider in it changes what the next test sees - and the next test would
    report a different machine rather than a different code. Both halves are
    asserted below: the stub is in place, and the cache is empty on entry.
    """
    cloud_llm.forget_models()
    assert sum(cloud_llm.models_asked(p) for p in cloud_llm.PROVIDERS) == 0

    asked = []
    real = cloud_llm.fetch_models

    def _stub(provider_id, api_key="", base_url="", timeout=12.0):
        asked.append((provider_id, api_key, base_url))
        return real(provider_id, api_key=api_key, base_url=base_url,
                    timeout=timeout)

    monkeypatch.setattr(cloud_llm, "fetch_models", _stub)
    yield asked
    cloud_llm.forget_models()
    monkeypatch.setattr(cloud_llm, "fetch_models", real)


def _pump(seconds=1.5):
    """Let the worker thread and its `GLib.idle_add` land."""
    import time
    ctx = GLib.MainContext.default()
    deadline = time.time() + seconds
    while time.time() < deadline:
        ctx.iteration(False)
        time.sleep(0.01)


def _palette_of(count):
    """A real palette carrying `count` entries.

    Built the way the window builds it - `CommandPalette()` then
    `set_entries(collect(...))` - rather than by assembling a `ListBox`
    directly, because the property under test is about the scroller the
    palette owns and a hand-built list has none.
    """
    rows = [palette.Entry("Entry %d" % i, "a subtitle", "go-next-symbolic",
                          (lambda: None), group="P") for i in range(count)]
    pal = palette.CommandPalette()
    pal.set_entries(palette.collect(rows))
    return pal


def _labels(widget):
    out = []
    for node in _walk(widget):
        if isinstance(node, Gtk.Label):
            out.append(node.get_label() or "")
    return out


def _walk(widget):
    out, stack = [], [widget]
    while stack:
        node = stack.pop()
        if node is None:
            continue
        out.append(node)
        if hasattr(node, "get_first_child"):
            child = node.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
    return out


# ── the defect: the palette was never in the window ───────────────────────


def _walk_in_order(widget):
    """**Document order**, breadth-first - which `_walk` above is not.

    `_walk` pops from the end of a stack, so siblings come out reversed. That
    is fine for "is this anywhere in the tree" and wrong for "which list box is
    the second one", which is how the first version of the press test below
    ended up emitting `activated` on a **local** tier row and then reporting
    that nobody had been asked.
    """
    from collections import deque
    out, queue = [], deque([widget])
    while queue:
        node = queue.popleft()
        if node is None:
            continue
        out.append(node)
        if hasattr(node, "get_first_child"):
            child = node.get_first_child()
            while child is not None:
                queue.append(child)
                child = child.get_next_sibling()
    return out


class TestThePaletteIsOnScreen:
    """Ctrl+K built a widget and showed it on nothing.

    Measured before the fix, on the built window: before the press
    `visible=False parent=None`; after the real key handler returned `True`,
    `visible=True parent=None`; 400 ms later `mapped=False` and **not in the
    window's widget subtree at all**.
    """

    def test_the_palette_is_parented_into_the_window(self):
        import inspect
        src = inspect.getsource(ChronoaWindow._setup_ui)
        assert "main_box.append(self._palette)" in src, (
            "the palette is built, filled and keyed but added to no container, "
            "so there is no surface for it to appear on")

    def test_the_palette_is_in_the_windows_widget_subtree(self):
        """Asked of a **real presented window**, because that is the only thing
        that answers it.

        `ChronoaWindow.__new__` plus a hand-driven `_setup_ui` is not enough -
        it raised here and returned `None`, which would have **skipped the one
        assertion that matters** and left the file green against a palette
        nobody can see.

        **And it deliberately does not go through `app.activate()`.** Measured:
        5 of 5 passes alone, and inside the eight-file batch it failed 1 of 7
        with *"the window was never built, after 60 waits"* - thirty seconds of
        retrying a window that was never going to appear. The cause is the bus,
        not the clock: `ChronoaApplication` hardcodes `dev.shani.chronoa`, and
        `tests/test_slash_menu_opens.py` in the same batch registers it, so
        activation is **forwarded to that owner** and never runs here.
        `AGENTS.md` records this mechanism twice already, once as a 125-second
        five-test timeout, and its remedy is a harness that does not need the
        name.

        So the window is built the way `do_activate` builds it - one call, the
        same constructor - and presented directly. No bus, no owner, no wait.
        """
        from gi.repository import Gdk

        from shani_chronoa.app import ChronoaApplication
        from shani_chronoa.gui.window import ChronoaWindow

        app = ChronoaApplication()
        app.hold()
        out = {}

        def step():
            window = ChronoaWindow(app)
            app.window = window
            window.present()
            pal = window._palette
            out["visible_before"] = pal.get_visible()
            out["handled"] = window._on_palette_key(
                None, Gdk.KEY_k, 0, Gdk.ModifierType.CONTROL_MASK)
            out["visible_after"] = pal.get_visible()
            out["parent_after"] = pal.get_parent() is not None
            out["parent_is_chat_column"] = (
                pal.get_parent() is window._chat_column)
            out["in_subtree"] = any(n is pal for n in _walk(window))
            out["mapped"] = pal.get_mapped()
            app.quit()
            return False

        GLib.timeout_add(200, step)
        GLib.timeout_add(30000, lambda: (app.quit(), False)[1])
        try:
            app.run(["cloud-models-palette-probe"])
        finally:
            app.release()

        assert out, "the window was never built, so nothing was measured"
        assert out["handled"] is True, "Ctrl+K was not claimed by the handler"
        assert out["visible_before"] is False
        assert out["visible_after"] is True, (
            "the handler returned True and the palette is still not visible")
        assert out["parent_after"] is True, (
            "the palette is visible with no parent - a Gtk.Box cannot be a "
            "toplevel, so there is no surface for it to appear on")
        assert out["in_subtree"] is True, (
            "the palette has a parent but is not a descendant of the window")
        # **The chat column specifically, not merely "somewhere in the window".**
        # A palette nested inside any hidden box is a descendant and is mapped
        # never, because a hidden ancestor takes its children with it - which is
        # why this assertion exists beside `mapped` rather than instead of it.
        assert out["parent_is_chat_column"] is True, (
            "the palette is in the window but not in the chat column, so it is "
            "laid out somewhere the conversation is not")
        assert out["mapped"] is True, (
            "the palette is in the window but was never mapped, so it drew "
            "nothing")

    def test_the_palette_list_is_inside_a_scroller_with_a_ceiling(self):
        """A bare `Gtk.ListBox` cannot shrink, so the tail is unreachable.

        Measured on this widget's own rows: **252px at 6 entries, 840px at 20,
        2100px at 50**, `min == natural` at every one, and
        `issubclass(Gtk.ListBox, Gtk.Scrollable)` is **False**.
        """
        rows = [palette.Entry("Entry %d" % i, "sub", "go-next-symbolic",
                              lambda: None, group="Panels")
                for i in range(50)]
        pal = palette.CommandPalette()
        pal.set_entries(palette.collect(rows))
        scrollers = [n for n in _walk(pal) if isinstance(n, Gtk.ScrolledWindow)]
        assert scrollers, "the palette's list is a bare ListBox and cannot scroll"
        scroller = scrollers[0]
        # **Through the `Gtk.Viewport`, not around it.** A `Gtk.ScrolledWindow`
        # wraps a child that is not a `Gtk.Scrollable` - and `Gtk.ListBox` is
        # not one, which is the whole reason the scroller is needed - so
        # `get_child()` is a Viewport holding the list. AGENTS.md records the
        # same trap in the settings window; asserting against the direct child
        # is how a test ends up looking for a widget two hops away.
        child = scroller.get_child()
        inner = child.get_child() if isinstance(child, Gtk.Viewport) else child
        assert inner is pal._list, (
            "the scroller does not wrap the list the keyboard navigation uses")
        cap = scroller.get_max_content_height()
        natural = pal._list.measure(Gtk.Orientation.VERTICAL, -1)[1]
        # **`0 < cap < natural`, not `assert cap`.** GTK's own default for
        # `max-content-height` is **-1** - "no limit" - which is truthy, so the
        # first version of this assertion passed against a scroller with the
        # ceiling line deleted. That is the "a check that cannot fail" trap in a
        # ceiling: a mutation that removes the bound entirely was a SURVIVOR.
        assert 0 < cap, (
            f"the scroller's ceiling is {cap} - GTK's default is -1, meaning no "
            f"limit at all, so the palette grows with its entries")
        assert natural > cap, (
            f"the list asks for only {natural}px against a {cap}px ceiling, so "
            f"this fixture is not actually overflowing and proves nothing")


# ── the picker: three states, kept apart ───────────────────────────────────


class TestThePickerListsCloudModels:

    def test_an_unasked_provider_offers_to_read_its_list(self, _no_network):
        """Not "nothing here" - which would read as "there are no models"."""
        app = _App()
        rows = model_picker.cloud_rows_for(app)
        assert len(rows) == len(cloud_llm.PROVIDERS), (
            "every registered provider should appear while none is asked")
        pending = [r for r in rows if r[4]]
        assert len(pending) == len(rows), "a fresh machine has nothing fetched"
        for _pid, model, _sub, _chosen, _p in rows:
            assert model == "", (
                "a provider nobody asked must not invent a model name")

    def test_a_refusal_carries_the_providers_own_reason(self, _no_network):
        cloud_llm._MODEL_CACHE["openrouter"] = ((), "HTTP 401: no API key")
        rows = model_picker.cloud_rows_for(_App())
        row = [r for r in rows if r[0] == "openrouter"]
        assert len(row) == 1
        pid, model, subtitle, chosen, pending = row[0]
        assert model == "" and not pending
        assert "401" in subtitle and "no API key" in subtitle, (
            f"the refusal says {subtitle!r} - only the provider knows whether "
            f"the answer was a 401, a 404 or a timeout")

    def test_the_fetch_carries_the_key_and_the_custom_address(self, _no_network):
        app = _App(keys={"kilo": "sk-vendor-key"}, base="http://127.0.0.1:9/v1")
        model_picker._read_models(app, model_picker.ModelPicker(app))
        _pump(2.0)
        by_id = {pid: (key, base) for pid, key, base in _no_network}
        assert "kilo" in by_id, "the keyed provider was never asked"
        assert by_id["kilo"][0] == "sk-vendor-key", (
            f"kilo was asked with {by_id['kilo'][0]!r} rather than its key")
        assert by_id["custom"][1] == "http://127.0.0.1:9/v1", (
            "your own server was asked with the default address rather than "
            "the one configured")

    def test_the_chosen_model_is_shown_even_when_the_provider_omits_it(
            self, _no_network):
        """The row whose whole job is "what is answering" cannot be dropped.

        `offerable_models` caps at `cloud_llm._MENU_LIMIT`, so a choice past
        the cap is absent from every fetched list. Without this the picker
        marked **nothing** as answering on a machine where a cloud model
        answered every turn.
        """
        assert cloud_llm._MENU_LIMIT < 458, (
            "the cap no longer makes a published model unreachable, so the "
            "fixture below no longer reproduces the case it exists for")
        cloud_llm._MODEL_CACHE["openrouter"] = (
            tuple("vendor/m%d" % i for i in range(cloud_llm._MENU_LIMIT)), "")
        app = _App(models={"openrouter_model": "vendor/experimental-1"})
        rows = model_picker.cloud_rows_for(app)
        chosen = [r for r in rows if r[3]]
        assert [r[1] for r in chosen] == ["vendor/experimental-1"], (
            f"rows marked answering: {[r[1] for r in chosen]} - the chosen "
            f"model was dropped because the provider's list omits it")

    def test_the_chosen_model_says_so_when_the_provider_omits_it(
            self, _no_network):
        cloud_llm._MODEL_CACHE["openrouter"] = (("vendor/m0",), "")
        app = _App(models={"openrouter_model": "vendor/experimental-1"})
        rows = [r for r in model_picker.cloud_rows_for(app) if r[3]]
        assert "not in this provider's published list" in rows[0][2], (
            f"the row says {rows[0][2]!r} - a model the provider does not "
            f"list should not pass for one it does")

    def test_an_unasked_provider_can_be_pressed_to_read_its_list(
            self, _no_network):
        """A row that says "reading the list..." and does nothing is dead.

        And if the popover's own `show` handler never fires, or a provider's
        fetch was refused, the row would be that way for good - so the press
        reads **that one provider**, through the same door the popover uses for
        all of them.
        """
        picker = model_picker.ModelPicker(_App())
        # **In document order**, so index 1 really is the cloud section. With
        # the stack-order walk this picked the *local* list, pressed a tier row,
        # and reported that no provider had been asked - which was true, and
        # said nothing about the press.
        lists = [n for n in _walk_in_order(picker)
                 if isinstance(n, Gtk.ListBox)]
        assert len(lists) == 2, (
            "the popover should carry a local list and a cloud list; it "
            f"carries {len(lists)}")
        pressed = None
        child = lists[1].get_first_child()
        while child is not None:
            if hasattr(child, "get_activatable") and child.get_activatable():
                pressed = child
                break
            child = child.get_next_sibling()
        assert pressed is not None, (
            "no cloud row in the popover can be pressed, so a provider that "
            "was never fetched can never be fetched by hand")
        title = pressed.get_title() or ""
        assert "reading the list" in title, (
            f"the row pressed is {title!r}, which is not an unasked provider")
        pressed.emit("activated")
        _pump(2.0)
        assert _no_network, (
            "pressing an unasked provider asked nobody - the press is the "
            "only route to a list when the popover's own fetch never ran")

    def test_the_picker_has_a_scroller_with_a_ceiling(self):
        """The popover cannot shrink, so a cap is the only thing bounding it.

        Measured on the real tier rows: **324px** for the six of them, `min ==
        natural`; and the catalogues behind them are 458 models (OpenRouter) and
        390 (Kilo), so the cloud section is an order of magnitude worse.
        """
        picker = model_picker.ModelPicker(_App())
        scrollers = [n for n in _walk(picker)
                     if isinstance(n, Gtk.ScrolledWindow)]
        assert scrollers, (
            "the picker has no scroller, and Gtk.ListBox is not a "
            "Gtk.Scrollable, so its list cannot shrink")
        cap = scrollers[0].get_max_content_height()
        # **`0 < cap`, not `assert cap`** - GTK's default for
        # `max-content-height` is -1 ("no limit"), which is truthy. The first
        # version of this assertion passed with the ceiling line deleted.
        assert 0 < cap, (
            f"the picker's scroller ceiling is {cap} - the GTK default of -1 "
            f"means no limit, so the popover grows with the provider's list")
        rows = cap / 54.0
        assert rows < 12, (
            f"the ceiling is {cap}px, about {rows:.0f} rows at 54px each - far "
            f"more than a screenful, so the scroller would not be what keeps "
            f"the popover on it")

    def test_both_scrollers_are_asked_to_grow_into_their_ceiling(self):
        """**The cap is half a setting, and the other half was missing.**

        `set_max_content_height` does not make a `Gtk.ScrolledWindow` grow
        toward the ceiling - it only refuses to be told it may be taller, and a
        `ScrolledWindow` asks for very little by default. So the cap alone left
        both surfaces **58px tall, about one row**, and left the palette
        drawing a scrollbar over three entries that fit.

        Measured, presented in a 320x900 window given only the scroller's own
        request, identical widgets with only this flag differing:

        |                    | flag off | flag on |
        |--------------------|----------|---------|
        | picker, 6 tiers    | 58px     | **420px** |
        | picker, +848 models| 58px     | **420px** |
        | palette, 50 entries| 58px     | **420px** |
        | palette, 3 entries | 58px, scrollbar on a list that fits | **126px, no scrollbar** |

        So this is not "the ceiling is set again". The ceiling test above passes
        with this flag deleted - measured, it did - and the two surfaces would
        be one row tall and look scrolled when they were not.
        """
        cases = [
            ("picker", model_picker.ModelPicker(_App())),
            ("palette", _palette_of(50)),
            ("palette", _palette_of(3)),
        ]
        for name, widget in cases:
            scrollers = [n for n in _walk(widget)
                         if isinstance(n, Gtk.ScrolledWindow)]
            assert scrollers, f"the {name} has no scroller to grow"
            for scroller in scrollers:
                assert scroller.get_propagate_natural_height() is True, (
                    f"the {name}'s scroller is capped at "
                    f"{scroller.get_max_content_height()}px but is not asked to "
                    f"grow into it, so it comes back at its 58px minimum - "
                    f"about one row - and a short list gets a scrollbar over "
                    f"content that fits")

    def test_the_picker_scroller_wraps_the_rows_it_contains(self):
        """A scroller around a box that is not the rows bounds nothing.

        This reads as fixed - there *is* a scroller, it has a cap, and the
        popover still grows - which is why it is a separate mutation from
        removing the scroller altogether.
        """
        picker = model_picker.ModelPicker(_App())
        scroller = [n for n in _walk(picker)
                    if isinstance(n, Gtk.ScrolledWindow)][0]
        child = scroller.get_child()
        inner = child.get_child() if isinstance(child, Gtk.Viewport) else child
        titled = [n for n in _walk(inner) if n in _walk(picker)
                  and hasattr(n, "get_title") and (n.get_title() or "")]
        assert titled, (
            "the scroller holds no titled rows - it wraps something else, so "
            "its ceiling applies to nothing")


# ── the action ─────────────────────────────────────────────────────────────


class TestTheCloudModelAction:

    @staticmethod
    def _app_and_handler():
        """Drive `_create_actions` with a stubbed `add_action`.

        **Not** `app.register(None)`: `AGENTS.md` records that exporting on a
        shared bus leaves later runs forwarded to a stale owner, which is how a
        0.7s file becomes 125s. Stubbing `add_action` records exactly what was
        registered and touches no bus.
        """
        from shani_chronoa.app import ChronoaApplication

        recorded = []
        original = ChronoaApplication.add_action
        try:
            ChronoaApplication.add_action = (
                lambda self, action: recorded.append(
                    (action.get_name(),
                     action.get_parameter_type().dup_string()
                     if action.get_parameter_type() else "")))
            app = ChronoaApplication()
            app._create_actions()
        finally:
            ChronoaApplication.add_action = original
        return dict(recorded), app

    def test_it_is_registered_with_a_provider_and_a_model(self):
        registered, _app = self._app_and_handler()
        assert registered.get("answer-cloud-model") == "(ss)", (
            "two values travel together, so a stateful 's' action would have "
            "to pack them into one string and every reader would have to "
            "split it correctly")

    @staticmethod
    def _handler_app(**cfg):
        _registered, app = TestTheCloudModelAction._app_and_handler()
        app.config = type("C", (), {
            "written": [],
            "set_cloud_model": lambda self, p, m: app.config.written.append((p, m)),
        })()
        app.config.written = []
        for key, value in cfg.items():
            setattr(app.config, key, value)
        app.llm = None
        app.rebuilt = []
        app.select_llm = lambda what: app.rebuilt.append(what) or ""
        return app

    def test_a_choice_is_written(self):
        app = self._handler_app()
        app._on_answer_cloud_model(None, GLib.Variant("(ss)",
                                                      ("kilo", "kilo-pro")))
        assert app.config.written == [("kilo", "kilo-pro")]

    def test_a_machine_answered_locally_is_not_moved_to_the_cloud(self):
        """Picking a cloud model is not asking to answer in the cloud."""
        app = self._handler_app()
        app._on_answer_cloud_model(None, GLib.Variant("(ss)",
                                                      ("kilo", "kilo-pro")))
        assert app.config.written == [("kilo", "kilo-pro")], (
            "the choice must still be saved for when the cloud answers")
        assert app.rebuilt == [], (
            f"select_llm was called {app.rebuilt} - that moves a person off "
            f"this machine without being asked, which is the opposite of what "
            f"picking a model asks for")

    def test_a_cloud_engine_is_rebuilt_so_the_choice_takes_effect_now(self):
        app = self._handler_app()
        app.llm = cloud_llm.CloudLLMChain.__new__(cloud_llm.CloudLLMChain)
        app._on_answer_cloud_model(None, GLib.Variant("(ss)",
                                                      ("groq", "llama-3.3")))
        assert app.rebuilt == ["cloud"], (
            "CloudLLMChain reads its models when it is built, so writing the "
            "setting alone leaves the old model answering")

    def test_a_refused_rebuild_still_saves_the_choice(self):
        app = self._handler_app()
        app.llm = cloud_llm.CloudLLMChain.__new__(cloud_llm.CloudLLMChain)
        app.select_llm = lambda what: "privacy mode is on"
        app._on_answer_cloud_model(None, GLib.Variant("(ss)",
                                                      ("groq", "llama-3.3")))
        assert app.config.written == [("groq", "llama-3.3")], (
            "a chain that would not rebuild must not discard the choice")

    def test_an_unknown_provider_is_refused_by_name(self, caplog):
        app = self._handler_app()
        with caplog.at_level("WARNING"):
            app._on_answer_cloud_model(
                None, GLib.Variant("(ss)", ("nosuchprovider", "m")))
        assert app.config.written == []
        assert "nosuchprovider" in caplog.text

    def test_a_blank_model_is_refused(self):
        app = self._handler_app()
        app._on_answer_cloud_model(None, GLib.Variant("(ss)", ("kilo", "   ")))
        assert app.config.written == [], "an empty model name was written"

    def test_a_provider_with_no_model_setting_is_refused_by_name(self, caplog):
        """`set_cloud_model` returns early on an id it does not name.

        So this would look like it worked and be discarded - the same shape as
        a row that reports nothing when pressed.
        """
        from shani_chronoa.config import MODEL_SETTINGS

        saved = MODEL_SETTINGS.pop("blockrun")
        try:
            app = self._handler_app()
            with caplog.at_level("WARNING"):
                app._on_answer_cloud_model(
                    None, GLib.Variant("(ss)", ("blockrun", "some-model")))
        finally:
            MODEL_SETTINGS["blockrun"] = saved
        assert app.config.written == []
        assert "blockrun" in caplog.text

    def test_a_provider_the_registry_does_not_know_is_still_refused(self, caplog):
        """**The one input that separates the two guards.**

        `test_an_unknown_provider_is_refused_by_name` alone does not: a name in
        neither `cloud_llm.PROVIDERS` nor `MODEL_SETTINGS` is caught by *both*
        refusals, so removing either one leaves the suite green. That is the
        mutation run's survivor here - not missing coverage of the check, but a
        witness that cannot tell the two apart.

        So this adds the name to `MODEL_SETTINGS` only. The registry guard is
        then the only thing left that can refuse it, which is what makes it the
        one that matters: it is the guard that would still hold if the settings
        table ever gained an entry for a provider nobody has an adapter for.
        """
        from shani_chronoa.config import MODEL_SETTINGS

        ghost = "ghost-provider"
        assert ghost not in cloud_llm.PROVIDERS
        saved = MODEL_SETTINGS.get(ghost)
        MODEL_SETTINGS[ghost] = {"model": "ghost-model"}
        try:
            app = self._handler_app()
            with caplog.at_level("WARNING"):
                app._on_answer_cloud_model(
                    None, GLib.Variant("(ss)", (ghost, "ghost-model")))
        finally:
            if saved is None:
                MODEL_SETTINGS.pop(ghost, None)
            else:
                MODEL_SETTINGS[ghost] = saved
        assert app.config.written == [], (
            "a provider the registry does not know was written, so the choice "
            "could not be used by any chain")
        assert ghost in caplog.text

    def test_a_padded_model_name_is_saved_trimmed(self):
        """The name travels through a `GAction`, so padding is easy to keep.

        `test_a_choice_is_written` passes an already-trimmed name, so it cannot
        tell a strip from no strip - which is how the "saved with its
        surrounding spaces" mutation came back a survivor against a file that
        otherwise holds it.
        """
        app = self._handler_app()
        app._on_answer_cloud_model(
            None, GLib.Variant("(ss)", ("kilo", "  kilo-pro  ")))
        assert app.config.written == [("kilo", "kilo-pro")], (
            f"saved {app.config.written} - a name with spaces round it is not "
            f"a model any provider recognises")

    def test_every_registered_provider_can_have_its_choice_saved(self):
        """The picker and the palette offer exactly `PROVIDERS`.

        A provider in one list and not the other is the dead control the
        palette's own docstring is written against, and the registry is the
        one place that knows the whole set.
        """
        from shani_chronoa.config import MODEL_SETTINGS

        missing = [pid for pid in cloud_llm.PROVIDERS
                   if MODEL_SETTINGS.get(pid) is None]
        assert not missing, (
            f"these providers can be chosen but their choice cannot be saved: "
            f"{missing}")


# ── the palette's cloud section ────────────────────────────────────────────


class TestThePaletteListsCloudModels:

    def test_an_unasked_provider_is_offered_rather_than_absent(self, _no_network):
        entries = ChronoaWindow._palette_cloud_entries(object())
        cloud = [e for e in entries if e.group == "Cloud models"]
        assert len(cloud) == len(cloud_llm.PROVIDERS)
        for entry in cloud:
            assert entry.run is not None, (
                f"{entry.title!r} has nothing to do - `collect()` drops an "
                f"entry with no `run`, so a dead one silently disappears")

    def test_a_refusal_is_kept_and_says_why(self, _no_network):
        cloud_llm._MODEL_CACHE["openrouter"] = ((), "HTTP 429: rate limited")
        entries = ChronoaWindow._palette_cloud_entries(object())
        refused = [e for e in entries if "429" in (e.subtitle or "")]
        assert len(refused) == 1, "the provider's own reason is not on screen"
        assert refused[0].run is not None, (
            "a refusal with nothing to do is dropped by collect(), and then a "
            "provider that answered 429 reads the same as one never asked")

    def test_a_model_is_listed_and_pressable(self, _no_network):
        cloud_llm._MODEL_CACHE["kilo"] = (("kilo-auto/free", "kilo-pro"), "")
        entries = ChronoaWindow._palette_cloud_entries(object())
        models = [e for e in entries if e.group == "Cloud models"
                  and e.title in ("kilo-auto/free", "kilo-pro")]
        assert len(models) == 2
        assert all(e.run is not None for e in models)

    def test_the_cloud_rows_survive_collect(self, _no_network):
        """`collect()` is what the palette actually shows.

        Asserting on the list `_palette_cloud_entries` returns is a test of
        that function; asserting on what `collect` keeps is a test of the
        palette - and the two differ exactly where a row has no `run`.
        """
        cloud_llm._MODEL_CACHE["openrouter"] = ((), "HTTP 401: no API key")
        cloud_llm._MODEL_CACHE["kilo"] = (("kilo-pro",), "")
        entries = ChronoaWindow._palette_cloud_entries(object())
        kept = palette.collect(entries)
        cloud = [e for e in kept if e.group == "Cloud models"]
        assert len(cloud) == len(entries), (
            f"{len(entries) - len(cloud)} cloud rows were dropped by collect()")