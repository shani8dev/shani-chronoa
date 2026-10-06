"""A model manager: what is on this machine, what could be, and a search over both.

From `Alpaca`'s model manager, which gets two things right that a list of
free-text entries does not:

- **Added and available are different questions.** Alpaca's manager is an
  `Adw.NavigationPage` with a view stack: one view for the models you have, one
  for everything else, with a view switcher between them. Chronoa's Models page
  had three `Adw.EntryRow` boxes and a read-only "In effect right now" group, so
  "which of these do I have" was answered by reading subtitles, and the
  consequence of typing a name you do not have was discovered later - as
  "llama.cpp is not answering" in a log and "No model yet" on the orb.
- **Cards that reflow.** A `Gtk.FlowBox` whose `min-children-per-line` and
  `max-children-per-line` are bound to the window's `Adw.Breakpoint`, so a narrow
  window gets one card per line and a wide one gets several. Alpaca binds them
  with `win_bp.add_setter(...)`, which is the part worth copying: the layout
  follows the window instead of a width guessed once.

And the search bar, which is not a nicety: there are nine kinds of model here
across four engines, and the answer to "do I have a voice?" should not require
scrolling.

**A card states a fact and offers one action.** Installed cards say so and offer
"Use this one"; available cards give the exact size and offer "Download". A card
that cannot be downloaded says why instead of showing a button that fails.

**Nothing here loads a model by itself.** Choosing an installed model writes the
pin and stops; the model is brought back by a question, through
`local_llm.start_service()`, so that a pin can never leave the assistant claiming
to be ready while a server it never started stays down.
"""

from __future__ import annotations

import logging

from typing import Any, Callable, Dict, List

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from shani_chronoa.gui.surfaces import common  # noqa: E402

logger = logging.getLogger(__name__)

TITLE = "Models"
ICON = "application-x-executable"
SECTION = "This machine"
SUBTITLE = ("Everything Chronoa can run, what each one costs, and which are "
            "already here")

#: One card per model. `(kind, key, label, size_bytes, installed, note)`.
#: Read from the engines themselves rather than a list kept in step by hand, so
#: a new model appears here when its engine gains it - which is the failure mode
#: of a list of three entries that quietly stops mentioning half the models.
CARD_KINDS = ("llm", "stt", "voice", "vision")

#: The breakpoints are held here rather than as locals. A `Gtk.Breakpoint` with no
#: Python reference can be finalised, and a finalised breakpoint silently stops
#: applying - which looks exactly like a layout that never reflows.
_BREAKPOINTS: List[Any] = []


def _cards(config=None) -> List[Dict[str, Any]]:
    """Every model the package knows how to fetch, newest first within a kind."""
    from shani_chronoa import local_llm, stt_provision, voices

    out: List[Dict[str, Any]] = []
    for spec, label, _ram in local_llm.TIERS:
        out.append({"kind": "llm", "key": spec.key, "label": label,
                    "size": spec.size_bytes, "installed": local_llm.verify(spec.key)})
    for key, spec in stt_provision.MODELS.items():
        out.append({"kind": "stt", "key": key,
                    "label": f"{key.split('-')[0].title()} ({key})",
                    "size": spec.size_bytes,
                    "installed": stt_provision.is_provisioned(key)})
    for key, spec in voices.VOICES.items():
        out.append({"kind": "voice", "key": key,
                    "label": f"{spec.label.split(' - ')[0]} (Piper)",
                    "size": spec.onnx_size, "installed": False})
    for key, voice in voices.KOKORO_VOICES.items():
        # Kokoro's engine and its model are one download shared by every voice,
        # so the card says that rather than implying six separate purchases. And
        # `KOKORO_VOICES` holds `KokoroVoice` objects, not strings - reading
        # `.split` off one is how a card list built from a dict of strings fails
        # on the first render.
        label = voice.label if hasattr(voice, "label") else str(voice)
        out.append({"kind": "voice", "key": f"kokoro:{key}",
                    "label": f"{label.split(' - ')[0]} (Kokoro)",
                    "size": voices._KOKORO_MODEL.size_bytes,
                    "installed": voices.kokoro_installed(key),
                    "shared": "one download covers every Kokoro voice"})
    # `verify`, not `is_provisioned`: the first version called a function this
    # engine does not have, the `except` swallowed it, and the panel quietly
    # shipped **with no Eyes cards at all** - the very models the "what do I
    # have" view exists to show. A bare `except: pass` around a probe is how a
    # panel ends up silently incomplete, so the engine's own check is named here
    # and the whole engine is only skipped when it cannot be imported at all.
    try:
        from shani_chronoa import local_vision
    except Exception as exc:               # noqa: BLE001 - no engine, no cards
        logger.debug("no vision engine for the model list: %s", exc)
    else:
        for key, spec in local_vision.MODELS.items():
            out.append({"kind": "vision", "key": key, "label": key,
                        "size": spec.size_bytes,
                        "installed": local_vision.verify(key)})
    return out


def _size_text(size_bytes: float) -> str:
    mb = float(size_bytes) / 1e6
    return f"{mb:.0f} MB" if mb < 1000 else f"{mb / 1000:.1f} GB"


def _matches(card: Dict[str, Any], needle: str) -> bool:
    if not needle:
        return True
    n = needle.lower()
    return n in card["key"].lower() or n in card["label"].lower() \
        or n in card["kind"].lower()


def _card(app: Any, card: Dict[str, Any], on_changed: Callable) -> Gtk.Widget:
    """One model, as a box a FlowBox can lay out in columns."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=8,
                  margin_bottom=8, margin_start=8, margin_end=8)
    title = Gtk.Label(label=card["label"], xalign=0, wrap=True)
    title.add_css_class("heading")
    box.append(title)
    # Size and kind are two different facts, so they join with one separator
    # rather than two spaces around a middot. The `and` used to be inside the
    # f-string, which for a card with no size interpolated the *falsy value*
    # itself - `None  ·  llm` - so a size-less card announced `None` as though
    # that were its size. An empty size now contributes no part at all.
    parts = [text for text in (_size_text(card["size"]) if card["size"] else "",
                               card["kind"]) if text]
    box.append(Gtk.Label(label=" · ".join(parts), xalign=0))
    status = Gtk.Label(xalign=0, wrap=True)
    box.append(status)

    def refresh() -> None:
        installed = card["installed"]
        status.set_text("On this machine" if installed else "Not downloaded")
        status.remove_css_class("dim-label")
        if not installed:
            status.add_css_class("dim-label")
        button.set_label("Ready" if installed else "Set up")
        button.set_tooltip_text(
            "In use" if installed else
            "Set this one up, then choose it - the setup window opens on its page")
        icon.set_from_icon_name(
            "object-select-symbolic" if installed else "folder-download-symbolic")

    button = Gtk.Button(halign=Gtk.Align.START, css_classes=["pill"])
    icon = Gtk.Image()
    button.set_child(icon)
    button.connect("clicked", lambda _b: _activate(app, card, box, on_changed))
    box.append(button)

    if card.get("shared"):
        box.append(Gtk.Label(label=card["shared"], xalign=0, wrap=True))

    refresh()
    box._chronoa_card = card
    return box


def _activate(app: Any, card: Dict[str, Any], box: Gtk.Widget,
             on_changed: Callable) -> None:
    """Do what the button says, which depends on whether the model is here.

    **This button was insensitive for every model that was not installed**, from
    `button.set_sensitive(installed)`. Measured on a machine with no models at
    all: all 27 "Set up" buttons on the Models panel came back
    `is_sensitive() == False`, and the only live control on the whole panel was
    "Go to Available Models", which switches tab and lands you on the same dead
    buttons. So the panel's stated answer to "Chronoa needs at least one model"
    was a control that could not be pressed - and the tooltip underneath it
    promised "the setup window opens on its page", so the disabled state was
    also a broken promise rather than an honest "not available".

    The inversion was almost certainly meant to be "you can only *choose* a model
    that is installed", which is true and is what `_pick` does. But choosing and
    obtaining are two different jobs, and only one of them had a live button:

    - installed -> pin it (`_pick`), which is what choosing means.
    - not installed -> open the setup wizard on this model's page. That is the
      wizard's own job - it downloads, and it is the only thing in the app that
      does - so the button delegates rather than downloading anything itself.

    Setup is reached through `app.activate_action("setup", None)`, the same route
    `privacy.py` uses for `toggle-privacy`, so the panel needs no reference to
    the application object that owns `_open_setup`. If neither that nor a
    `Gio.Application` is reachable - a bare stub, as the tests build - the
    button says so in its own tooltip instead of silently doing nothing.
    """
    if card["installed"]:
        _pick(app, card)
        on_changed()
        return
    _open_setup(app, box)


def _open_setup(app: Any, widget: Gtk.Widget) -> None:
    """Open the setup wizard, the one thing here that can install a model.

    **Delegates to `common.open_setup`,** which is the same function `model.py`
    and `voice.py` use for their "not installed" buttons. This one used to be the
    only copy, in one of the three panels that need it - so the other two had
    either to import it across module boundaries or to write their own, and three
    panels with three routes to the wizard is the arrangement this repo keeps
    paying for. Kept as a named wrapper so this module's own callers and tests
    keep the name they had.
    """
    common.open_setup(app, widget)


def _pick(app: Any, card: Dict[str, Any]) -> None:
    """Pin a model. Writes the setting and stops there."""
    config = getattr(app, "config", None)
    if config is None:
        from shani_chronoa.config import ChronoaConfig
        config = ChronoaConfig()
    key = card["key"]
    if card["kind"] == "llm":
        config.set("model", key)
    elif card["kind"] == "stt":
        config.set("whisper-model", key)
    elif card["kind"] == "vision":
        config.set("vision-model", key)
    elif card["kind"] == "voice":
        config.set("piper-voice", key.split(":", 1)[-1])


def build(app: Any) -> Gtk.Widget:
    """The model manager for `app` - anything with a `config` will do."""
    page, set_content = common.surface(TITLE, SUBTITLE)
    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

    # --- the search bar, above the stack, so it filters whichever view is up --
    search_bar = Gtk.SearchBar()
    search_entry = Gtk.SearchEntry(hexpand=True, placeholder_text="Search models")
    # `Gtk.Accessible.update_property`, not `set_accessible_name`: GTK4 removed
    # the setter, and a placeholder is not an accessible name - it disappears the
    # moment somebody types in the box, leaving the entry unnamed.
    search_entry.update_property([Gtk.AccessibleProperty.LABEL],
                                 ["Search models"])
    search_bar.set_child(search_entry)
    search_bar.connect_entry(search_entry)
    root.append(search_bar)

    # The panel's own health, above the two lists:
    # how many models are installed, how many are
    # available, how many could not be read. One
    # row, one dot, one word - the question the
    # panel is opened for, before the cards that
    # hold the models.
    cards = _cards(getattr(app, "config", None))
    installed = sum(1 for c in cards if c["installed"])
    available = len(cards) - installed
    #: One tally of one card list, for the row here and the sidebar's dot. The
    #: two used to be built from separate reads of the registry, and a model
    #: finishing a download in between would put "2 installed" in the row and a
    #: green dot for a panel claiming one.
    recorder = common.StatusRecorder()
    if installed:
        root.append(recorder.row(
            common.STATUS_OK,
            f"{installed} installed, {available} available",
            f"{installed} of {len(cards)} models are on this machine, "
            f"{available} are available to add"))
    elif available:
        root.append(recorder.row(
            common.STATUS_ATTENTION,
            f"None installed, {available} available",
            f"none of the {len(cards)} models are on this machine, "
            f"{available} are available to add"))
    else:
        root.append(recorder.row(
            common.STATUS_UNKNOWN,
            "No models",
            "the registry returned nothing"))

    # --- the two lists, as a view stack with a switcher under them ---------
    stack = Adw.ViewStack(vexpand=True)
    flow_added = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                             min_children_per_line=1, max_children_per_line=1,
                             homogeneous=True, row_spacing=6, column_spacing=6)
    flow_available = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                                 min_children_per_line=1, max_children_per_line=1,
                                 homogeneous=True, row_spacing=6, column_spacing=6)
    scroll_added = common.scrolled(flow_added)
    scroll_available = common.scrolled(flow_available)
    stack.add_titled(scroll_added, "added", "On this machine")
    stack.add_titled(scroll_available, "available", "Available to add")

    switcher = Adw.ViewSwitcher(stack=stack, halign=Gtk.Align.CENTER)
    switcher_bar = Adw.ViewSwitcherBar(stack=stack, reveal=True)
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, vexpand=True)
    body.append(switcher_bar)
    body.append(stack)
    body.append(switcher)
    root.append(body)

    # --- the cards, and the search over them --------------------------------
    cards = _cards(getattr(app, "config", None))
    state: Dict[str, Any] = {"needle": ""}

    def _switch_tab(tab_name: str) -> None:
        stack.set_visible_child_name(tab_name)

    # The button is built first and handed to `empty_state(child=...)`.
    # `Adw.StatusPage` has `set_child()`, not `append()` - measured on the
    # installed libadwaita 1.5, `hasattr(Adw.StatusPage, "append")` is False - so
    # calling `append` on it raised `AttributeError` at build time and the empty
    # state, and therefore the only route out of the empty tab, never appeared.
    # On the plain-GTK fallback path `empty_state()` returns a `Gtk.Box`, where
    # `append` is right, which is why this only ever broke with Adw present.
    switch_btn = Gtk.Button(label="Go to Available Models")
    switch_btn.connect("clicked", lambda _: _switch_tab("available"))
    switch_btn.update_property(
        [Gtk.AccessibleProperty.LABEL], ["Go to Available Models"])

    # The wizard itself, as a second button beside the tab switch.
    #
    # "Go to Available Models" only moves to the other tab - which lists the same
    # models, behind the buttons that were all insensitive. So the empty state,
    # which is the *only* thing a machine with no models ever shows, offered no
    # route to actually obtaining one: two clicks from the panel's own stated
    # problem and you were back where you started. This button opens the setup
    # wizard, which is the only thing in the app that downloads a model.
    wizard_btn = Gtk.Button(label="Set Chronoa up")
    wizard_btn.add_css_class("suggested-action")
    wizard_btn.connect("clicked", lambda _b: _open_setup(app, page))
    wizard_btn.update_property(
        [Gtk.AccessibleProperty.LABEL], ["Set Chronoa up: download a model"])
    empty_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                            halign=Gtk.Align.CENTER)
    empty_buttons.append(wizard_btn)
    empty_buttons.append(switch_btn)

    empty_added = common.empty_state(
        "application-x-executable-symbolic",
        "No models installed",
        "Chronoa needs at least one model to answer questions. Set it up, or "
        "choose one from the 'Available to add' tab.",
        child=empty_buttons,
    )

    empty_available = common.empty_state(
        "system-search-symbolic",
        "No matching models",
        "Try a different search term or clear the filter."
    )

    def repopulate() -> None:
        for flow, wanted, empty in ((flow_added, True, empty_added),
                                   (flow_available, False, empty_available)):
            child = flow.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                flow.remove(child)
                child = nxt

            count = 0
            for card in cards:
                if card["installed"] is not wanted or not _matches(card, state["needle"]):
                    continue
                flow.append(_card(app, card, repopulate))
                count += 1

            if count == 0:
                flow.append(empty)
                empty.set_visible(True)
            else:
                empty.set_visible(False)

    def on_search() -> None:
        state["needle"] = search_entry.get_text().strip()
        search_bar.set_search_mode(bool(state["needle"]))
        repopulate()

    search_entry.connect("search-changed", lambda *_: on_search())
    repopulate()

    # --- Alpaca's part worth copying: the layout follows the window ----------
    # `Adw.Breakpoint.new()` takes a *condition*, not a width, and the condition
    # is built by `Adw.BreakpointCondition.parse()` - neither of which is what
    # the first attempt here used. `gui/sidebar.py` already documents both by
    # calling the installed library and recording what raised, which is why this
    # is copied rather than rediscovered.
    #
    # One card per line by default, two from 560px, three from 1000px: a narrow
    # window gets a card it can read, and a wide one gets a grid rather than a
    # single lonely column. Guessing a width once and building for it is what
    # produced the clipped settings window.
    def columns(target: int) -> None:
        for flow in (flow_added, flow_available):
            flow.set_max_children_per_line(target)
            flow.set_min_children_per_line(min(target, 2))

    # `add_setter(object, property-name, value)` - the object, the property, and
    # the value to set while the condition holds. `max-children-per-line` and
    # `min-children-per-line` are real `Gtk.FlowBox` properties, so they can be
    # bound directly; there is no intermediate object and none is needed.
    for target, width in ((2, 560), (3, 1000)):
        condition = Adw.BreakpointCondition.parse(f"(min-width: {width}px)")
        bp = Adw.Breakpoint.new(condition)
        for flow in (flow_added, flow_available):
            bp.add_setter(flow, "min-children-per-line", min(target, 2))
            bp.add_setter(flow, "max-children-per-line", target)
        _BREAKPOINTS.append(bp)

    set_content(root)
    # What this panel says about itself, for the sidebar's health dot.
    page.status = recorder.status
    return page


