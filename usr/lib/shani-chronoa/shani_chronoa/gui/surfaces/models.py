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
import threading

from typing import Any, Callable, Dict, List

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from shani_chronoa.gui.surfaces import common  # noqa: E402

logger = logging.getLogger(__name__)

TITLE = "Models"
ICON = "preferences-desktop-font-symbolic"
SECTION = "Thinking"
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
        # The tuple's third field is a legacy RAM hint, kept in step by hand and
        # already out of date against `local_llm.REQUIRES`, which carries the
        # measured requirement **and** what this machine would actually decode
        # it at. This card reads that one; `_ram` was discarded here, so a card
        # could never say why a tier was refused.
        out.append({"kind": "llm", "key": spec.key, "label": label,
                    "size": spec.size_bytes, "installed": local_llm.verify(spec.key),
                    "requires": _llm_requirements(spec.key)})
    for key, spec in stt_provision.MODELS.items():
        out.append({"kind": "stt", "key": key,
                    "label": f"{key.split('-')[0].title()} ({key})",
                    "size": spec.size_bytes,
                    "installed": stt_provision.is_provisioned(key)})
    # Parakeet is a **second** speech backend with its own catalogue, served from
    # a different repository, so it cannot live in `stt_provision.MODELS` (see
    # the note above that table) - and the panel listed only that one. A machine
    # whose Parakeet model is installed therefore showed every speech card as
    # "not downloaded", which is the confident-wrong-answer shape this file
    # already records for the Eyes list. Each backend's own check is named here,
    # and the key is prefixed so a Parakeet card cannot collide with a whisper
    # one of the same name.
    for key, spec in stt_provision.PARAKEET_MODELS.items():
        out.append({"kind": "stt", "key": f"parakeet:{key}",
                    "label": f"Parakeet ({key})",
                    "size": spec.size_bytes,
                    "installed": stt_provision.is_parakeet_provisioned(key)})
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


def _llm_requirements(key: str) -> Dict[str, Any]:
    """What a tier needs from this machine, and what it would get here.

    Everything on the card comes from `local_llm`'s own table rather than from
    a sentence written here, so a tier that is refused names **the number it is
    short by** instead of "not recommended". Four facts, all measured rather
    than guessed:

    * `cpu` / `ram` / `vram` - what the tier declares.
    * `reads` - bytes one generated token actually reads. Equal to the download
      size for a dense model and a fraction of it for a mixture-of-experts, so
      this is the number that explains why a bigger tier can be faster.
    * `tps` - what this machine would decode at, or None when no server is
      answering. **None means "not measured"**, never a guess.
    * `missing` - `requirements_met`'s own reasons, so the card cannot disagree
      with the code that picks the default.
    """
    from shani_chronoa import local_llm

    need = local_llm.REQUIRES.get(key)
    if need is None:
        return {}
    met, missing = local_llm.requirements_met(key)
    return {
        "cpu": need.get("cpu", 0),
        "ram": need.get("ram", 0),
        "vram": need.get("vram", 0),
        "min_tps": need.get("min_tps", local_llm.MIN_USABLE_TPS),
        "reads": local_llm.bytes_per_token(key),
        "tps": local_llm.expected_decode_tps(key),
        "measured": need.get("measured", True),
        "usable": met,
        "missing": [m for m in (missing if isinstance(missing, list) else [missing]) if m],
    }


def _requirement_text(card: Dict[str, Any]) -> str:
    """The one line under an LLM card: what it needs, and what this box gives it.

    Rendered from `_llm_requirements`, so there is no second copy of the table to
    fall behind. Every part is conditional on being known - a tier with no RAM
    requirement contributes no RAM clause rather than "0 GB".
    """
    req = card.get("requires") or {}
    if not req:
        return ""
    parts: List[str] = []
    if req.get("ram"):
        parts.append(f"{req['ram']:g} GB RAM")
    if req.get("vram"):
        parts.append(f"{req['vram']:g} GB VRAM")
    if req.get("cpu"):
        parts.append(f"{req['cpu']:g} CPU cores")
    reads = req.get("reads")
    if reads:
        # What one token reads, which is the download size for a dense model and
        # a fraction of it for an MoE - the reason a 30B can out-run an 8B.
        parts.append(f"reads {_size_text(reads)} per token")
    if req.get("tps"):
        qualifier = "" if req.get("measured", True) else "about "
        parts.append(f"{qualifier}{req['tps']:.1f} tok/s here")
    head = "Needs " + ", ".join(parts) if parts else ""
    if not head:
        return ""
    if req.get("missing"):
        return head + ". Not the default here: " + req["missing"][0]
    return head + ". Fast enough to be the default on this machine."


def _needs_label_text(card: Dict[str, Any]) -> str:
    """The requirement line for this card, or "" when it should not draw one.

    Only LLM cards carry requirements: a speech card does not become more
    demanding because a text model is, and "Needs 2 CPU cores" under a Piper
    voice would be a claim nobody checked.

    Named separately from `_requirement_text` so *whether* to render is as
    testable as *what* to render. See `_card` for why that separation earned its
    keep.
    """
    if card.get("kind") != "llm":
        return ""
    return _requirement_text(card)


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
    # **Named, so the switch can find it without guessing by position.** The card
    # has three labels before its button - title, size-and-kind, and this one -
    # and taking the first meant the switch wrote to the **title**: pressing
    # "Use it" renamed the card to its own result. Measured.
    status.set_name("status")
    box.append(status)
    # What it costs this machine to run, and why it is or is not the default.
    # `_needs_label_text` owns the *decision* to render a line, not just the
    # text: a first version decided here, and the mutation that stopped the
    # label being appended left every test green - the tests all called the text
    # function and none of them called the card.
    needs = _needs_label_text(card)
    if needs:
        needs_label = Gtk.Label(label=needs, xalign=0, wrap=True)
        needs_label.add_css_class("dim-label")
        # Long on the narrow layouts, so the cap the layout contract requires.
        needs_label.set_max_width_chars(48)
        box.append(needs_label)

    def _in_use() -> bool:
        """Whether this is the model answering right now, not merely installed.

        **`installed` and `in use` were the same word on this panel.** The button
        read "Ready" and its tooltip read "In use" for every model that happened to
        be on disk, while the running server held whichever one it had loaded - so
        a panel of five installed models showed five "In use"s, which is five
        claims and at most one true one. Measured here, the answer to "which model
        am I using" was not on the screen anywhere.
        """
        if not card["installed"]:
            return False
        if card["kind"] != "llm":
            # Only the language model has a "running right now" - the other kinds
            # are read back from the setting they were written to, which is what
            # they are answered by next time.
            return bool(card.get("chosen"))
        try:
            from shani_chronoa import local_llm

            if local_llm.active() != card["key"]:
                return False
        except Exception:  # noqa: BLE001 - unreadable is not "not in use"
            return False
        # **Loaded is not answering.** `local_llm.active()` says which file
        # `current.gguf` points at; whether that is the model answering is a
        # different question - the LLM is chosen once at startup and
        # `_maybe_enable_cloud_fallback` never reconsiders, so starting llama-server
        # afterwards adds an available engine without changing the selected one.
        # `model.py`'s `_selected_engine` exists for exactly this, and reading the
        # object rather than re-deriving the condition is its documented lesson.
        return _answers_locally(app)

    def refresh() -> None:
        installed = card["installed"]
        in_use = _in_use()
        status.set_text("On this machine" if installed else "Not downloaded")
        status.remove_css_class("dim-label")
        if not installed:
            status.add_css_class("dim-label")
        # **The label is a claim about now.** "Ready" was a claim about the
        # disk, and it is what a person reads as "this is the one answering".
        loaded = installed and _loaded(card)
        # **"Loaded" and "In use" are two claims**, and conflating them is the
        # trap `model.py` already recorded: the server can hold a model while the
        # app answers through a cloud provider chosen at startup.
        button.set_label(
            "Set up" if not installed else
            ("In use" if in_use else ("Loaded" if loaded else "Use it")))
        button.set_sensitive(installed and not in_use)
        button.set_tooltip_text(
            "Set this one up, then choose it - the setup window opens on its page"
            if not installed else
            ("This is the model answering now" if in_use else
             ("Downloaded, but the assistant is not answering with it" if loaded
              else "Switch to this model now: the server restarts with it")))
        icon.set_from_icon_name(
            "folder-download-symbolic" if not installed else
            ("object-select-symbolic" if in_use else
             ("document-open-recent-symbolic" if loaded
              else "media-playback-start-symbolic")))

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


def _loaded(card: Dict[str, Any]) -> bool:
    """Whether the server holds this model's file, whether or not it is used."""
    if not card["installed"] or card["kind"] != "llm":
        return False
    try:
        from shani_chronoa import local_llm

        return local_llm.active() == card["key"]
    except Exception:  # noqa: BLE001 - unreadable is not "not loaded"
        return False


def _answers_locally(app: Any) -> bool:
    """Whether the app is routed to the local server, or somewhere else.

    **False when it cannot be determined**, which is what a bare stub answers - so
    a card built with no application says "Loaded" and not "In use". That is the
    honest direction for a claim about *now*: the smaller claim.
    """
    llm = getattr(app, "llm", None)
    if llm is None:
        return False
    try:
        from shani_chronoa.cloud_llm import CloudLLMChain

        return not isinstance(llm, CloudLLMChain)
    except Exception:  # noqa: BLE001 - an unreadable engine is not a local one
        return False


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
        _switch(app, card, box, on_changed)
        return
    _open_setup(app, box)


def _engine_box(app: Any) -> Gtk.Widget:
    """Where the assistant answers from: local, or a cloud provider.

    **Two engines and no way to choose between them was the gap.** Settings held
    "Run the model", "Free the model" and the cloud fallback switch, in a
    different window; `model.py` held the free keys; Privacy held the custom
    server. A person comparing local against cloud had four places to look and one
    of them reported availability where the other reported selection.

    `select_llm` (the previous commit) is what makes this possible at all: the
    engine is chosen once at startup and `_maybe_enable_cloud_fallback` never
    reconsiders, so a switch back from the cloud was a restart.

    **A refusal names its gate, verbatim.** `_cloud_gate_reason` is the same
    answer the startup path logs, and re-wording it here is exactly how a panel
    comes to claim a gate is open that the engine says is shut.
    """
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                  margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
    box.add_css_class("card")
    heading = Gtk.Label(label="Where the assistant answers from", xalign=0)
    heading.add_css_class("heading")
    # Ellipsised, not left to be clipped: the layout contract allows a long
    # label only if it wraps or ellipsises, and a card heading over 30 characters
    # that did neither was cut off at the card's own width.
    heading.set_ellipsize(Pango.EllipsizeMode.END)
    box.append(heading)
    summary = Gtk.Label(xalign=0, wrap=True)
    summary.add_css_class("dim-label")
    summary.set_max_width_chars(60)
    box.append(summary)

    def _set_summary(text):
        summary.set_text(text)
        summary.remove_css_class("dim-label")

    rows = {}

    def refresh():
        selected = _app_selected_kind(app)
        for kind, (button, _idle, in_use_label, note) in rows.items():
            is_it = kind == selected
            # **A Button takes `set_label`, not `set_text`** - the GTK3 name for
            # it, and the first version of this called `set_text` on one, which
            # raises `AttributeError` on the panel's own construction and so took
            # the whole Models page down rather than just this row.
            button.set_label(in_use_label if is_it else "Use it")
            button.set_sensitive(not is_it)
            note.set_text("answering now" if is_it else "not answering")

    def choose(kind):
        def run(_button):
            _set_summary("Switching to %s..." % kind)
            threading.Thread(target=lambda: _apply_engine_choice(
                app, kind, _set_summary, refresh), daemon=True).start()
        return run

    for kind, title, active_title in (
            ("local", "Local (llama.cpp)",
             "Local (llama.cpp) - answering now"),
            ("cloud", "Cloud - free gateways and your own server",
             "Cloud - answering now")):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        label = Gtk.Label(label=title, xalign=0)
        label.set_wrap(True)
        label.set_hexpand(True)
        button = Gtk.Button(label="Use it", valign=Gtk.Align.CENTER)
        button.set_tooltip_text(
            "Answer with the model on this machine" if kind == "local"
            else "Answer through the cloud providers - prompts leave this machine")
        button.connect("clicked", choose(kind))
        note = Gtk.Label(xalign=0, wrap=True)
        note.add_css_class("dim-label")
        row.append(label)
        row.append(note)
        row.append(button)
        box.append(row)
        rows[kind] = (button, "Use it", "In use", note)

    # **The release control.** `stop_service()` existed and was reachable from
    # Settings' presence row only, which is a different window from the model
    # list a person is looking at when they decide they want the memory back.
    release = Gtk.Button(label="Free the model now", halign=Gtk.Align.START,
                         css_classes=["pill"])
    # **"It starts again on the next question" is not true of Asleep.** Now that
    # the choice is reachable, this tooltip promised behaviour `brain` refuses
    # on the one state the person chose deliberately.
    release.set_tooltip_text(
        "Stop llama-server so its memory is released. It starts again on the "
        "next question - unless the model is asleep, in which case it stays "
        "stopped until you press Wake up in Settings.")
    release.connect("clicked", lambda _b: threading.Thread(
        target=lambda: _apply_release(app, _set_summary, refresh),
        daemon=True).start())
    box.append(release)

    refresh()
    return box


def _app_selected_kind(app: Any) -> str:
    """Which engine the app holds, read from its own object.

    **Not a setting.** `cloud-fallback-enabled` being true says nothing about what
    is answering now, and `local_llm.active()` says which file is loaded, which is
    a third question. `app.current_llm_kind()` is the app's own answer.
    """
    reader = getattr(app, "current_llm_kind", None)
    if reader is None:
        return "none"
    try:
        return reader() or "none"
    except Exception:  # noqa: BLE001 - an unreadable app is not "local"
        return "none"


def _apply_engine_choice(app: Any, kind: str, set_summary, refresh) -> None:
    from gi.repository import GLib

    selector = getattr(app, "select_llm", None)
    if selector is None:
        GLib.idle_add(set_summary,
                      "This panel has no application to switch - open it from "
                      "the sidebar so the switch can reach the assistant.")
        return
    try:
        why = selector(kind)
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        why = "the switch failed: %s" % (exc,)
    GLib.idle_add(lambda: (_say_engine_result(why, kind, set_summary), refresh())[1])


def _apply_release(app: Any, set_summary, refresh) -> None:
    """Stop the model, and say which of the states that leaves us in.

    **It printed one sentence for all of them.** "Released the model - its
    memory is back, and the next question loads it again" appeared whether the
    server had just been stopped, had already been stopped, or was **asleep on
    purpose** - and in that last case the second half is false, because
    `brain._use_local_server` refuses to start a server while the choice is
    SLEEPING. The panel offered to release a model that was not loaded and
    promised a wake the next question would not perform.

    `stop_service()` returns `''` for a unit that was already inactive, so the
    success path cannot tell the two apart on its own - `is_up()` is asked first.
    """
    from gi.repository import GLib

    from shani_chronoa import local_llm
    from shani_chronoa.presence import Presence

    chosen = getattr(app, "_presence", None)
    if chosen is None:
        chosen = Presence.ACTIVE
    was_resident = local_llm.is_up()
    try:
        why = local_llm.stop_service()
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        why = "the release failed: %s" % (exc,)
    GLib.idle_add(lambda: (_say_release_result(why, chosen, was_resident,
                                               set_summary), refresh())[1])


def _say_engine_result(why: str, kind: str, set_summary) -> None:
    """One sentence, and the free keys are not repeated in it."""
    if not why:
        set_summary("Answering with %s." % kind)
        return
    set_summary("Could not switch to %s: %s" % (kind, why))
    logger.warning("engine switch to %s refused: %s", kind, why)


def _say_release_result(why: str, chosen, was_resident: bool, set_summary) -> None:
    """What actually happened, which is three sentences rather than one.

    A refused release, a release that happened, and a release that had nothing to
    do - plus Asleep, which is the case the single sentence got wrong.
    """
    from shani_chronoa.presence import Presence

    if why:
        set_summary("Could not release the model: %s" % why)
        logger.warning("model release refused: %s", why)
        return
    if chosen is Presence.SLEEPING:
        # The choice, not an accident: stopping again is a no-op and the next
        # question will not undo it. Name the way out, or this is a dead end the
        # person has to find in another window. Two sentences for two facts -
        # whether it was running just now, and what happens next.
        set_summary(("The model is stopped and stays asleep - the next question "
                     "will not load it." if was_resident else
                     "The model is already asleep - nothing was running.")
                    + " Press \"Wake up\" in Settings > Models > Right now.")
        return
    if not was_resident:
        set_summary("Nothing to release - it was already stopped. The next "
                    "question loads it again.")
        return
    set_summary("Released the model - its memory is back, and the next "
                "question loads it again.")


def _switch(app: Any, card: Dict[str, Any], box: Gtk.Widget,
            on_changed: Callable) -> None:
    """Switch to this model now, and say what happened.

    **There was no switch.** `_pick` wrote `config.set("model", key)` and its own
    docstring said "Writes the setting and stops there" - so the button answered a
    person asking "use this one" with a change to what happens *next time*, and
    kept saying nothing about the change that had not happened. The model a
    question loads is chosen by `local_llm.use()`, which points `current.gguf` at
    the file and (re)starts the service; neither was reachable from this panel.

    Measured on this machine during this change: the server answers, the model is
    pinned, and the panel shows "Ready" - three facts, none of them the one being
    asked for.

    Off the main loop, because `use()` runs the quality gate (`llama-perplexity`
    takes seconds) and `start_service()` shells to `systemctl --user`, and a
    button that freezes the window for seconds while it works is a button nobody
    presses twice.

    **A refusal is reported, not swallowed.** The gate exists to stop a worse
    quant replacing a better one, and a panel that ignored its verdict would be
    the assistant claiming to have switched when it had not.
    """
    import threading

    status = _first_button_status(box)
    if getattr(box, "_switching", False):
        return
    box._switching = True
    button = _card_button(box)
    if button is not None:
        button.set_sensitive(False)
    if status is not None:
        status.set_text("Switching...")
        status.remove_css_class("dim-label")

    def work():
        key = card["key"]
        try:
            if card["kind"] == "llm":
                from shani_chronoa import local_llm

                verdict = local_llm.use(key)
                if not verdict.get("promote"):
                    return {"ok": False, "switched": False,
                            "why": verdict.get("why") or "the quality gate refused"}
                why = local_llm.start_service()
                if why:
                    return {"ok": False, "switched": False, "why": why}
                return {"ok": True, "switched": True, "why": ""}
            # **Not a switch, and it must not say it is.** These kinds have no
            # service to restart - `stt_server`, `local_vision` and `local_embed`
            # expose no `start_service`/`stop_service` - so the engine they run
            # is *selected* on its next use, not now. Returning success with the
            # status line left at "On this machine" made pressing "Use it" report
            # success and show no change at all, which is the shape this repo
            # records as a control that cannot be observed to have done anything.
            _pick(getattr(box, "_app", None) or _config_only(), card)
            return {"ok": True, "switched": False,
                    "why": "chosen - %s will use it next time it is needed"
                           % card["kind"]}
        except Exception as exc:  # noqa: BLE001 - reported below, never raised
            return {"ok": False, "switched": False, "why": str(exc)}

    def done():
        box._switching = False
        on_changed()
        return False

    def body():
        result = work()
        # The reply goes back on the main loop because that is where the widget
        # lives: writing a `Gtk.Label` from a thread is the unsafe half.
        GLib.idle_add(lambda: (_apply_switch_result(box, card, result), None)[1])
        GLib.idle_add(done)

    threading.Thread(target=body, daemon=True).start()


def _card_button(box: Gtk.Widget):
    for child in _children(box):
        if isinstance(child, Gtk.Button):
            return child
    return None


def _first_button_status(box: Gtk.Widget):
    """The card's status label, by the name it was given at build time.

    **By position it is wrong, and the failure is invisible.** The card has three
    labels before its button - title, size-and-kind, and this one - so "the first
    label" is the **title**, and a switch that writes to it overwrites the model's
    name with its own result. Measured: pressing "Use it" renamed the card.
    `_card` names this label and this reads the name; a card without one yields
    `None`, which every caller already treats as "no status line to update".
    """
    for child in _children(box):
        if isinstance(child, Gtk.Label) and child.get_name() == "status":
            return child
    return None


def _children(box: Gtk.Widget):
    """The direct children of a GTK4 container, in order.

    **`Gtk.Widget.get_children()` does not exist in GTK4** - it is the GTK3 API,
    and calling it raises `AttributeError`, which a `getattr` guard silently turns
    into "no children". This repo has recorded that trap already, in a different
    helper, and the first version of this function walked into it: the switch
    worked, the button was never found, and nothing failed.

    The way in is `get_first_child()` + `get_next_sibling()`.
    """
    try:
        out, child = [], box.get_first_child()
        while child is not None:
            out.append(child)
            child = child.get_next_sibling()
        return out
    except Exception:  # noqa: BLE001 - an unreadable box contributes nothing
        return []


def _apply_switch_result(box: Gtk.Widget, card: Dict[str, Any],
                         result: Dict[str, Any]) -> None:
    status = _first_button_status(box)
    if result.get("ok"):
        # **Two sentences for two different facts.** "Switched" means the engine
        # answers differently *now*; "chosen" means it will, the next time it is
        # needed. Writing "On this machine" here was the *installed* label, so a
        # switch that happened and one that did not read the same.
        if status is not None:
            status.set_text("Switched - answering with it now."
                            if result.get("switched") else
                            result.get("why") or "Chosen.")
            status.remove_css_class("dim-label")
        if result.get("switched"):
            _pick(getattr(box, "_app", None) or _config_only(), card)
        return
    if status is not None:
        status.set_text("Switch failed: %s" % result.get("why"))
        status.remove_css_class("dim-label")
    logger.warning("could not switch to %s: %s", card.get("key"), result.get("why"))


def _config_only():
    from shani_chronoa.config import ChronoaConfig

    return _StubApp(ChronoaConfig())


class _StubApp:
    """`_pick` reads `app.config`; this carries one without an application."""

    def __init__(self, config):
        self.config = config


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

    # --- which engine answers, above the cards it chooses between -------------
    # **At the top, because it is the first question.** The panel lists models;
    # the question a person opens it with is "what am I using, and how do I stop
    # using that", and neither was answerable here.
    root.append(_engine_box(app))

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
    # **Each list goes in a box that does not expand.** A `Gtk.ScrolledWindow`
    # hands its child the whole viewport, so a `Gtk.FlowBox` placed directly in
    # one is allocated 732px for 268px of cards and passes the slack on to its
    # children - measured in the render as a **195px gap between the two model
    # cards**, each sitting in 363px of height holding about 100px of content.
    #
    # It is *not* `homogeneous`: measured side by side, `homogeneous=True` and
    # `homogeneous=False` both give 363px cards, so the equalising that flag
    # documents is not what is stretching them. Swapping the FlowBox for a plain
    # `Gtk.Box` does not fix it either - the Box is filled just the same
    # (732px). The non-expanding wrapper is what does it: the same probe reads
    # 268px, the natural height, with the slack left at the end of the list.
    # `set_child_align` is not available to say this more directly - it is
    # GTK3, and GTK4's ScrolledWindow has no equivalent.
    def _list_scrolled(flow):
        holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        holder.append(flow)
        return common.scrolled(holder)

    scroll_added = _list_scrolled(flow_added)
    scroll_available = _list_scrolled(flow_available)
    # With icons: `add_titled` gives a page none, and the switcher then draws
    # a placeholder box above each label - the two blank cards in the render.
    stack.add_titled_with_icon(scroll_added, "added", "On this machine",
                               "computer-symbolic")
    stack.add_titled_with_icon(scroll_available, "available", "Available to add",
                               "folder-download-symbolic")

    # **One switcher, not two.** Both an `Adw.ViewSwitcherBar` (revealed, at
    # the top) and a plain `Adw.ViewSwitcher` (at the bottom) were attached to
    # this stack, so every model list showed the same two tabs twice - once as
    # pills under the status row and once as flat buttons at the foot of the
    # cards, each with its own highlight of the same page. Measured on the
    # rendered panel: both strips visible at once, and the card list squeezed
    # between them.
    #
    # The bar is the one kept, because `reveal=True` is what lets it get out of
    # the way when the list is scrolled - which is the entire reason to have a
    # switcher over a list rather than under one.
    switcher_bar = Adw.ViewSwitcherBar(stack=stack, reveal=True)
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, vexpand=True)
    body.append(switcher_bar)
    body.append(stack)
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


