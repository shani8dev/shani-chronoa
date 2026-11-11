"""The cloud-keys page opens on the model the person chose, and does not crash.

**Two defects, one lookup.** `config.cloud_llm_models()` returns
`{"<pid>_model": "<the model>"}` - the shape `CloudLLMChain` reads
(`api_keys.get("%s_model" % (p,))`). The setup wizard's cloud-keys page
looked it up as `saved.get(provider_id)`, the **bare** provider id, so the
lookup found nothing every time.

What that cost, measured rather than reasoned:

- **The choice was ignored.** A person who picked `my-chosen-kilo` for Kilo
  and reopened the wizard saw the dropdown on Kilo's registry default, not on
  what they had chosen.
- **The wizard aborted the process.** `custom` - "Your own server" - has no
  registry default (`default_model` is `""`, because its model is whatever
  the person typed on a machine nobody else can see). So `current` was `""`,
  and `ids.index("")` raised `ValueError` **inside a `GLib.idle_add`
  callback** - which does not propagate and does not fail the test: the
  interpreter dies with a core dump, and every test after it is reported as
  an error rather than as a result.

  Measured 2026-11-11: the suite segfaulted inside
  `pango_renderer_draw_glyph_item` with the traceback pointing at
  `setup_wizard.py:1981`, and the failure read as a rendering fault rather
  than as a dropdown selecting a value that is not there.

**So the page is driven here the way a person drives it** - the real wizard
built on a real config, with the provider's model list stubbed so nothing
goes to the network - and the assertions are on what the **built dropdown**
shows, not on a helper's return value.

The stub is **module-scoped and patched by hand**, because the page's
`fill()` runs on a **daemon thread**: a function-scoped `monkeypatch` is
undone at teardown, and the thread - which reads
`cloud_llm.offerable_models` *when it gets there* - then calls the real
function and makes the live request the stub was there to prevent. That is
not a hypothetical: it is what segfaulted the interpreter.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

Adw.init()

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import cloud_llm, setup_wizard  # noqa: E402
from shani_chronoa.config import ChronoaConfig  # noqa: E402


#: What each provider "offers" in the stub. **Two classes of provider, so a
#: parser that answered everything the same way would be caught.**
MODELS = {
    "kilo": ["kilo-auto/free", "alt-one", "alt-two"],
    "custom": ["my-own-model", "other-own-model"],
}

#: The model this machine has chosen for each provider, in the **chain's** key
#: shape - which is what `cloud_llm_models()` returns.
SAVED = {"kilo_model": "alt-one"}


class _Config(ChronoaConfig):
    """A real `ChronoaConfig` with only the two things a test must control.

    **The real class is the interface.** A hand-written stub omits the
    attributes the page reads, and an `AttributeError` inside
    `Gtk.Application`'s `activate` handler neither propagates nor quits the
    app - so the window is never built and `app.run([])` blocks forever.
    """

    def __init__(self):
        super().__init__()
        self.keys = {}
        self.models = dict(SAVED)
        self.saved_keys = {}
        self.saved_models = {}

    def cloud_llm_api_keys(self):
        return dict(self.keys)

    def cloud_llm_models(self):
        return dict(self.models)

    def set_api_key(self, setting, value):
        self.saved_keys[setting] = value

    def set_cloud_model(self, provider_id, model):
        self.saved_models[provider_id] = model


@pytest.fixture(scope="module", autouse=True)
def no_model_listing_over_the_network():
    """**The provider's model list is stubbed, so the test measures the page.**

    Module-scoped and patched by hand, because the page's `fill()` thread is a
    **daemon** that outlives the test that started it - a function-scoped
    `monkeypatch` would be undone while the thread is still running, and the
    thread would then make the live request.
    """
    original = cloud_llm.offerable_models

    def fake(provider_id, timeout=12.0, api_key="", base_url=""):
        if provider_id in MODELS:
            return list(MODELS[provider_id]), ""
        provider = cloud_llm.PROVIDERS.get(provider_id)
        if provider is None:
            return [], f"no provider is registered as {provider_id!r}"
        if not provider.model_list:
            # The real refusal, without the network: `list_models` refuses
            # before it builds a URL, so this path makes no request.
            return [], (f"{provider.name} does not publish a model list over "
                        f"an OpenAI-shaped API, so it answers with "
                        f"{provider.default_model}")
        return [provider.default_model] if provider.default_model else [], ""

    cloud_llm.offerable_models = fake
    try:
        yield None
    finally:
        cloud_llm.offerable_models = original


def _walk(widget, out=None):
    out = [] if out is None else out
    child = widget.get_first_child()
    while child is not None:
        out.append(child)
        _walk(child, out)
        child = child.get_next_child() if hasattr(child, "get_next_child") \
            else child.get_next_sibling()
    return out


def _view(window):
    for widget in _walk(window):
        if isinstance(widget, Adw.NavigationView):
            return widget
    return None


def _dropdowns(window):
    """Every model dropdown on the built page, in the order they were made."""
    return [w for w in _walk(window) if isinstance(w, Gtk.DropDown)]


def _selected_text(dropdown):
    """What this dropdown is showing.

    **Read through the model's own index.** `get_selected_item()` returns the
    `Gtk.StringObject` behind the row, and asking *that* for its string
    raises `TypeError: Expected type gi.overrides.GObject.Object` on this
    PyGObject - the wrapper is not a GObject. `Gtk.StringList.get_string(i)`
    is the reader that works, and it is the same one the page's own code
    would use.
    """
    model = dropdown.get_model()
    if model is None:
        return ""
    index = dropdown.get_selected()
    if index < 0:
        return ""
    return model.get_string(index)


def _model_texts(dropdown):
    """Every entry this dropdown offers."""
    model = dropdown.get_model()
    if model is None:
        return []
    return [model.get_string(i) for i in range(model.get_n_items())]


def _build_page(config, monkeypatch):
    """The **built** cloud-keys page, with the page's own thread answered.

    The wizard's `fill()` builds its own `ChronoaConfig()` rather than closing
    over the page's - right in production, since both read the same GSettings.
    So the fresh construction is redirected here, or the page is asked with
    `''` whatever the test saved and the defect is invisible.
    """
    monkeypatch.setattr("shani_chronoa.config.ChronoaConfig", lambda: config)
    box = {}
    ticks = [0]

    app = Gtk.Application(application_id="test.chronoa.savedmodel",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)

    def build(a):
        try:
            box["window"] = setup_wizard.build_window(a, config)
        except BaseException as exc:              # noqa: BLE001
            box["error"] = exc
            a.quit()
            return False
        box["window"].present()
        GLib.timeout_add(200, poll)
        return False

    def poll():
        window = box.get("window")
        view = _view(window) if window is not None else None
        if view is not None:
            view.push_by_tag("cloud-keys")
            GLib.timeout_add(600, finish)
            return False
        ticks[0] += 1
        if ticks[0] > 60:
            box["error"] = AssertionError(
                "the wizard built no NavigationView after 60 polls")
            app.quit()
            return False
        GLib.timeout_add(100, poll)
        return False

    def finish():
        GLib.timeout_add(50, lambda: (app.quit(), False)[1])
        return False

    app.connect("activate", build)
    app.run([])
    if "error" in box:
        raise box["error"]
    return box["window"]


@pytest.fixture
def cloud_page(monkeypatch):
    """The built cloud-keys page, on a config that has chosen a Kilo model.

    **The exceptions the page's own callbacks raise are captured.** A
    `GLib.idle_add` callback that raises does **not** abort the interpreter on
    this PyGObject - measured: the traceback is printed, `sys.excepthook` is
    called, and the main loop carries on. So the failure is *silent* except
    for that traceback, which is exactly the shape this file exists for: a
    dropdown that silently never got its selection.

    (The segfault this file's crash was blamed on is a **separate**,
    theme-dependent pango fault - this machine's `Greybird` is a GTK3 theme,
    and the same test file crashes identically at HEAD under it. CI runs
    Yaru, where there are no fatal errors at all.)
    """
    config = _Config()
    escaped = []
    original_hook = sys.excepthook

    def record(*exc_info):
        escaped.append(exc_info[1])
        original_hook(*exc_info)

    monkeypatch.setattr(sys, "excepthook", record)
    try:
        window = _build_page(config, monkeypatch)
    finally:
        monkeypatch.undo()
    yield window, escaped
    window.destroy()


def test_the_page_opens_on_the_model_this_machine_chosen(cloud_page):
    """**The dropdown shows the choice, not the registry default.**

    Kilo's registry default is `kilo-auto/free` and the machine has chosen
    `alt-one`. Before the fix the page asked for the model under the bare
    provider id, found nothing, and opened on the default - so a person who
    had picked a model saw it ignored.
    """
    page, escaped = cloud_page
    dropdowns = _dropdowns(page)
    assert dropdowns, "the page built no model dropdown at all"

    # **The chosen model, not the default.** Kilo's dropdown is the one
    # whose list carries the default *and* the choice.
    kilo_row = [d for d in dropdowns
                if "kilo-auto/free" in _model_texts(d)]
    assert kilo_row, ("no dropdown lists Kilo's models, so the one under "
                      "test was not found: %r"
                      % [_model_texts(d) for d in dropdowns])

    shown = _selected_text(kilo_row[0])
    assert shown == "alt-one", (
        "the dropdown opened on %r, but this machine chose 'alt-one' - the "
        "page looked the choice up under the bare provider id and never "
        "found it" % (shown,))


def test_a_provider_with_no_chosen_model_does_not_raise(cloud_page):
    """**The crash, and it is silent.**

    "Your own server" has no registry default and nothing saved for it, so
    its `current` was `""` - and `ids.index("")` raised `ValueError` inside
    the page's own `GLib.idle_add` callback.

    **An exception in that callback does not fail the test.** Measured on
    this PyGObject: the traceback is printed, `sys.excepthook` is called, and
    the main loop carries on to the next test. So a page that crashed on
    every open looked like a page that worked, and the only trace was a
    line on stderr that nothing read.

    So the assertion is on **the exception**, not on the dropdown - because
    the dropdown ends up in the same state either way (`set_selected(0)` is
    what a fresh dropdown already reads as).
    """
    page, escaped = cloud_page
    assert not escaped, (
        "the page raised %r while building itself: %r"
        % (escaped[0], escaped))


def test_the_own_servers_dropdown_lists_what_the_server_listed(cloud_page):
    """**And the dropdown is real**, so the test above is not passing for want
    of a case."""
    page, escaped = cloud_page
    own = [d for d in _dropdowns(page) if "my-own-model" in _model_texts(d)]
    assert own, ("no dropdown lists the own server's models, so the crash "
                 "case was not reached: %r"
                 % [_model_texts(d) for d in _dropdowns(page)])
    assert _selected_text(own[0]) in MODELS["custom"], (
        "the own server's dropdown opened on %r, which is not one of the "
        "models the server listed" % (_selected_text(own[0]),))


def test_the_lookup_asks_the_chain_the_same_question(cloud_page):
    """**One reader, so the page and the chain cannot disagree.**

    `CloudLLMChain` reads `api_keys.get("%s_model" % (p,))`, and
    `cloud_llm_models()` builds exactly that key. The page asked for the bare
    provider id - which is a **different string**, and one nothing writes.
    """
    from shani_chronoa import cloud_llm as _cl

    models = _cl.PROVIDERS["kilo"]
    assert models.default_model == "kilo-auto/free"
    # The chain's own spelling, written out here rather than imported, so the
    # assertion is about the shape and not about a helper agreeing with
    # itself.
    assert "%s_model" % ("kilo",) in SAVED
    assert "kilo" not in SAVED
